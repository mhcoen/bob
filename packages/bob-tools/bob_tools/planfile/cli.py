"""Command-line entry point for ``bob-plan``: validate, fmt, next, done, fail.

Five subcommands wired through ``argparse``:

* ``validate PATH`` — parse and run :func:`validate_plan` against
  ``PATH``. Reports success or every error on stderr. Exit ``0`` on
  success, ``1`` on parse or validation failure.
* ``next PATH`` — print the next actionable task as a one-line
  ``T-NNNNNN: <text>``. Validates first, exits ``1`` on validation
  failure.
* ``fmt PATH`` — parse leniently, :func:`migrate`, and save back in
  place: ``save(path, migrate(parse_plan(read(path))))`` per design
  doc section 3.2. The parse does not let the magic line force strict
  mode (``force_strict_from_magic=False``) so a magic-lined file with
  id-less tasks — the exact input canonicalization exists to repair —
  can be formatted. ``fmt`` refuses (exit ``1``, file untouched) when
  the parse would silently drop incomplete checkboxes that sit outside
  any ``## Stage`` / ``## Phase`` heading.
* ``done PATH TASK_ID`` — validate, :func:`complete_task`, save, and
  print the resulting Settlements as JSON on stdout.
* ``fail PATH TASK_ID --reason TEXT`` — validate, :func:`fail_task`,
  save, and print the Settlement as JSON on stdout.

Exit codes (uniform across subcommands):

* ``0`` — success.
* ``1`` — the plan failed parse or validation.
* ``2`` — a referenced task id was not found in the plan.
* ``3`` — any other error (I/O failure, internal bug).

Errors are written to ``stderr``; the JSON payloads from ``done`` and
``fail`` go to ``stdout`` so callers can pipe them into the ledger.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from bob_tools.planfile._shared import count_unfenced_incomplete_checkboxes
from bob_tools.planfile.canonical import _count_todo_tasks
from bob_tools.planfile.fileio import load, save
from bob_tools.planfile.model import (
    PlanSyntaxError,
    PlanValidationError,
    Settlement,
)
from bob_tools.planfile.operations import (
    complete_task,
    fail_task,
    migrate,
    next_tasks,
    validate_plan,
)
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.preflight import (
    PlanPreflightError,
    preflight_runtime_plan,
)

EXIT_OK = 0
EXIT_INVALID_PLAN = 1
EXIT_TASK_NOT_FOUND = 2
EXIT_OTHER = 3


def _settlements_to_json(settlements: Sequence[Settlement]) -> str:
    return json.dumps([dataclasses.asdict(s) for s in settlements], indent=2)


def _print_parse_error(exc: PlanSyntaxError, stream: IO[str], path: Path) -> None:
    # A PlanSyntaxError raised from a nested re-parse may not carry the
    # source path (``exc.path is None``), in which case ``__str__`` falls
    # back to the hardcoded "PLAN.md" and misnames whatever file the user
    # actually passed — e.g. ``bob-plan fmt BUGS.md`` reporting "PLAN.md
    # invalid at line N". The CLI knows the file being processed, so bind
    # it before formatting so the diagnostic always names the real target.
    if exc.path is None:
        exc.path = path
    print(str(exc), file=stream)


def _print_validation_errors(exc: PlanValidationError, stream: IO[str]) -> None:
    for message in exc.messages:
        print(message, file=stream)


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr, path)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        validate_plan(plan)
    except PlanValidationError as exc:
        _print_validation_errors(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    print(f"OK {path}")
    return EXIT_OK


def cmd_next(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr, path)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        validate_plan(plan)
    except PlanValidationError as exc:
        _print_validation_errors(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    tasks = next_tasks(plan, limit=1)
    if not tasks:
        print("no actionable task", file=sys.stderr)
        return EXIT_OK
    task = tasks[0]
    task_id = task.task_id if task.task_id is not None else "T-?????"
    print(f"{task_id}: {task.text}")
    return EXIT_OK


def cmd_fmt(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        # Pinned UTF-8 to match every other planfile read/write path; an
        # unpinned read here would misdecode non-ASCII content on a
        # non-UTF-8 locale and re-encode the mojibake to disk.
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    # ``fmt`` is the canonicalization entry point, so it must accept the
    # inputs canonicalization exists to repair. A magic-lined file whose
    # tasks are still id-less would be rejected by the strict parse that
    # ``load`` performs (the magic line force-enables strict mode),
    # leaving no tool path to canonicalize it. Parse leniently instead;
    # the save below still round-trips through the canonical gate.
    try:
        plan = parse_plan(text, source_path=path, force_strict_from_magic=False)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr, path)
        return EXIT_INVALID_PLAN
    # The parser only surfaces checkboxes that sit under a ``## Stage``
    # / ``## Phase`` heading; formatting a file with strays would
    # silently drop them from the rewritten file. Refuse instead.
    # Checkbox lines inside ``` fences are example content the parser
    # (correctly) does not surface as tasks, so exclude them from the
    # source count or every fenced example would trip this refusal.
    src_incomplete = count_unfenced_incomplete_checkboxes(text)
    plan_incomplete = _count_todo_tasks(plan)
    if src_incomplete > plan_incomplete:
        dropped = src_incomplete - plan_incomplete
        print(
            f"{path}: refusing to format: {dropped} incomplete checkbox "
            f"line(s) sit outside any `## Stage` / `## Phase` heading and "
            f"would be dropped; move them under a phase heading and re-run",
            file=sys.stderr,
        )
        return EXIT_INVALID_PLAN
    migrated = migrate(plan)
    # ``migrate`` per design doc section 3.2 only assigns T-NNNNNN ids
    # and phase_id comments; it deliberately does not touch
    # magic_version. ``fmt``, however, is the user-facing
    # canonicalization command, and a canonical save (the post-v4-
    # Decision-4 default) requires ``magic_version == 1``. Promote a
    # missing magic version here so a compat-form input round-trips
    # through ``fmt`` into a fully canonical PLAN.md rather than
    # being rejected at save time.
    if migrated.magic_version is None:
        migrated = dataclasses.replace(migrated, magic_version=1)
    # Task trailing lines (fenced output blocks, inter-section prose)
    # pass through the canonical gate and round-trip byte-for-byte; the
    # old "no trailing_lines" invariant was removed from constructed
    # validation because it only ever fired on parsed-from-disk content.
    try:
        save(path, migrated)
    except PlanValidationError as exc:
        # The canonical gate rejected the migrated plan (duplicate ids,
        # multi-paragraph preamble, ...). These need a hand fix; report
        # each message as a diagnostic instead of a traceback.
        for message in exc.messages if exc.messages else [str(exc)]:
            print(f"{path}: {message}", file=sys.stderr)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error writing {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    return EXIT_OK


def cmd_done(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = preflight_runtime_plan(path, notice=lambda m: print(m, file=sys.stderr))
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr, path)
        return EXIT_INVALID_PLAN
    except PlanPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        new_plan, settlements = complete_task(plan, args.task_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TASK_NOT_FOUND
    try:
        save(path, new_plan)
    except PlanValidationError as exc:
        for message in exc.messages if exc.messages else [str(exc)]:
            print(f"{path}: {message}", file=sys.stderr)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error writing {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    print(_settlements_to_json(settlements))
    return EXIT_OK


def cmd_fail(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = preflight_runtime_plan(path, notice=lambda m: print(m, file=sys.stderr))
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr, path)
        return EXIT_INVALID_PLAN
    except PlanPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        new_plan, settlements = fail_task(plan, args.task_id, args.reason)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TASK_NOT_FOUND
    try:
        save(path, new_plan)
    except PlanValidationError as exc:
        for message in exc.messages if exc.messages else [str(exc)]:
            print(f"{path}: {message}", file=sys.stderr)
        return EXIT_INVALID_PLAN
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error writing {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    print(_settlements_to_json(settlements))
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bob-plan",
        description="Deterministic PLAN.md reader, formatter, and operator.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Parse and validate a PLAN.md")
    p_validate.add_argument("path")
    p_validate.set_defaults(func=cmd_validate)

    p_next = sub.add_parser("next", help="Print the next actionable task")
    p_next.add_argument("path")
    p_next.set_defaults(func=cmd_next)

    p_fmt = sub.add_parser(
        "fmt", help="Reformat a PLAN.md in canonical form (assigns task IDs)"
    )
    p_fmt.add_argument("path")
    p_fmt.set_defaults(func=cmd_fmt)

    p_done = sub.add_parser("done", help="Mark a task DONE and save")
    p_done.add_argument("path")
    p_done.add_argument("task_id")
    p_done.set_defaults(func=cmd_done)

    p_fail = sub.add_parser("fail", help="Mark a task FAILED and save")
    p_fail.add_argument("path")
    p_fail.add_argument("task_id")
    p_fail.add_argument("--reason", required=True)
    p_fail.set_defaults(func=cmd_fail)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    func = args.func
    result = func(args)
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
