"""Command-line entry point for ``bob-plan``: validate, fmt, next, done, fail.

Five subcommands wired through ``argparse``:

* ``validate PATH`` — parse and run :func:`validate_plan` against
  ``PATH``. Reports success or every error on stderr. Exit ``0`` on
  success, ``1`` on parse or validation failure.
* ``next PATH`` — print the next actionable task as a one-line
  ``T-NNNNNN: <text>``. Validates first, exits ``1`` on validation
  failure.
* ``fmt PATH`` — load, :func:`migrate`, and save back in place. The
  composition is ``save(path, migrate(parse_plan(read(path))))`` per
  design doc section 3.2.
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

EXIT_OK = 0
EXIT_INVALID_PLAN = 1
EXIT_TASK_NOT_FOUND = 2
EXIT_OTHER = 3


def _settlements_to_json(settlements: Sequence[Settlement]) -> str:
    return json.dumps([dataclasses.asdict(s) for s in settlements], indent=2)


def _print_parse_error(exc: PlanSyntaxError, stream: IO[str]) -> None:
    print(str(exc), file=stream)


def _print_validation_errors(exc: PlanValidationError, stream: IO[str]) -> None:
    for message in exc.messages:
        print(message, file=stream)


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    except OSError as exc:
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
        _print_parse_error(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    except OSError as exc:
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
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    except OSError as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
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
    try:
        save(path, migrated)
    except OSError as exc:
        print(f"error writing {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    return EXIT_OK


def cmd_done(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    except OSError as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        validate_plan(plan)
    except PlanValidationError as exc:
        _print_validation_errors(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    try:
        new_plan, settlements = complete_task(plan, args.task_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TASK_NOT_FOUND
    try:
        save(path, new_plan)
    except OSError as exc:
        print(f"error writing {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    print(_settlements_to_json(settlements))
    return EXIT_OK


def cmd_fail(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        plan = load(path)
    except PlanSyntaxError as exc:
        _print_parse_error(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    except OSError as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        return EXIT_OTHER
    try:
        validate_plan(plan)
    except PlanValidationError as exc:
        _print_validation_errors(exc, sys.stderr)
        return EXIT_INVALID_PLAN
    try:
        new_plan, settlements = fail_task(plan, args.task_id, args.reason)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TASK_NOT_FOUND
    try:
        save(path, new_plan)
    except OSError as exc:
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
