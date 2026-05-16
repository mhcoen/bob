"""Stage 8 verification: ``bob-plan fmt`` is additive-only on a duplo plan.

Run as
``python -m bob_tools.planfile.tests.manual.check_duplo_generated_fmt``.

Picks the first ``/Users/mhcoen/proj/*/.duplo`` whose parent also has a
``PLAN.md``, copies that ``PLAN.md`` to ``/tmp``, runs ``bob-plan fmt``
on the copy, then compares the source (untouched) against the formatted
copy by parsing both and walking the parse trees in lockstep.

The fmt mutations allowed by the design doc and Stage 7 are: a leading
``<!-- bob-plan-format: N -->`` magic line, a ``<!-- phase_id: ... -->``
comment after each phase heading, a ``T-NNNNNN: `` prefix on each task
body, and two-space indentation normalization. Anything beyond that —
reordered tasks, dropped or added tasks, mutated tag sets, status
changes, lost RULEDOUT siblings, lost deps — is a semantic divergence
that must surface as a Stage 8 regression.

On divergence the script appends a precise, dated entry to
``/Users/mhcoen/proj/bob-tools/BUGS.md`` (parent path, formatted-copy
path, and a list of the specific structural mismatches found) and exits
non-zero. On agreement it exits zero.

All paths are hardcoded; the script takes no arguments. Every
subprocess call has an explicit short timeout so the verification run
can never wedge. Progress lines carry a ``HH:MM:SS`` prefix and are
flushed immediately so an operator sees activity on each step.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from bob_tools.planfile import (
    BugsSection,
    Phase,
    Plan,
    PlanSyntaxError,
    Task,
    parse_plan,
)

BOB_PLAN = Path("/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan")
DUPLO_GLOB_ROOT = Path("/Users/mhcoen/proj")
DUPLO_GLOB_PATTERN = "*/.duplo"
SCRATCH = Path("/tmp/bob-plan-stage8-duplo-check.PLAN.md")
BUGS_MD = Path("/Users/mhcoen/proj/bob-tools/BUGS.md")

# fmt on a real PLAN.md (~few hundred lines) finishes well under a
# second on a warm machine. 15 s absorbs cold-start latency and a slow
# disk; well short of wedging the verification run.
SUBPROCESS_TIMEOUT_S = 15.0


def _step(message: str) -> None:
    """Print one timestamped progress line, flushed immediately."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _pick_duplo_plan() -> Path | None:
    """Return the first ``<parent>/PLAN.md`` whose sibling ``.duplo`` exists.

    Globs deterministically (sorted) so the choice is stable run-to-run
    and so a future second-duplo project doesn't silently change which
    plan is verified.
    """
    candidates = sorted(DUPLO_GLOB_ROOT.glob(DUPLO_GLOB_PATTERN))
    for duplo_dir in candidates:
        plan = duplo_dir.parent / "PLAN.md"
        if plan.is_file():
            return plan
    return None


def _walk_tasks(tasks: tuple[Task, ...]) -> Iterator[Task]:
    """Yield ``tasks`` in pre-order, including all nested children."""
    for task in tasks:
        yield task
        yield from _walk_tasks(task.children)


def _phase_task_signature(phase: Phase) -> tuple[Task, ...]:
    """Return every task in a phase, including those inside subsections."""
    collected: list[Task] = []
    collected.extend(_walk_tasks(phase.tasks))
    for sub in phase.subsections:
        collected.extend(_walk_tasks(sub.tasks))
    return tuple(collected)


def _compare_task(
    before: Task, after: Task, location: str, divergences: list[str]
) -> None:
    """Record any semantic mismatch between two positionally-paired tasks.

    Only fields whose preservation is part of fmt's contract are
    compared. ``task_id``, ``line_number``, and ``indent_level`` are
    expected to change (added or renumbered) and are deliberately
    ignored.
    """
    if before.text != after.text:
        divergences.append(
            f"{location}: task text changed\n"
            f"  before: {before.text!r}\n"
            f"  after:  {after.text!r}"
        )
    if before.status != after.status:
        divergences.append(
            f"{location}: status changed "
            f"({before.status.value} -> {after.status.value})"
        )
    if before.flag_tags != after.flag_tags:
        divergences.append(
            f"{location}: flag_tags changed "
            f"({list(before.flag_tags)} -> {list(after.flag_tags)})"
        )
    if before.action_tag != after.action_tag:
        divergences.append(
            f"{location}: action_tag changed "
            f"({before.action_tag!r} -> {after.action_tag!r})"
        )
    if before.annotations != after.annotations:
        divergences.append(
            f"{location}: annotations changed "
            f"({list(before.annotations)} -> {list(after.annotations)})"
        )
    if before.deps != after.deps:
        divergences.append(
            f"{location}: deps changed ({list(before.deps)} -> {list(after.deps)})"
        )
    if len(before.ruled_out) != len(after.ruled_out):
        divergences.append(
            f"{location}: ruled_out count changed "
            f"({len(before.ruled_out)} -> {len(after.ruled_out)})"
        )
    else:
        for idx, (rb, ra) in enumerate(
            zip(before.ruled_out, after.ruled_out, strict=True)
        ):
            if rb.text != ra.text:
                divergences.append(
                    f"{location}: ruled_out[{idx}] text changed\n"
                    f"  before: {rb.text!r}\n"
                    f"  after:  {ra.text!r}"
                )
    if len(before.children) != len(after.children):
        divergences.append(
            f"{location}: child count changed "
            f"({len(before.children)} -> {len(after.children)})"
        )


def _compare_bugs(
    before: BugsSection | None,
    after: BugsSection | None,
    divergences: list[str],
) -> None:
    if (before is None) != (after is None):
        divergences.append(
            "bugs section presence changed "
            f"(before={before is not None}, after={after is not None})"
        )
        return
    if before is None or after is None:
        return
    b_tasks = tuple(_walk_tasks(before.tasks))
    a_tasks = tuple(_walk_tasks(after.tasks))
    if len(b_tasks) != len(a_tasks):
        divergences.append(
            f"bugs section task count changed ({len(b_tasks)} -> {len(a_tasks)})"
        )
        return
    for idx, (bt, at) in enumerate(zip(b_tasks, a_tasks, strict=True)):
        _compare_task(bt, at, f"bugs[{idx}]", divergences)


def _compare_plans(before: Plan, after: Plan) -> list[str]:
    """Walk two parse trees in lockstep and return every divergence."""
    divergences: list[str] = []
    if before.project_title != after.project_title:
        divergences.append(
            f"project title changed "
            f"({before.project_title!r} -> {after.project_title!r})"
        )
    if len(before.phases) != len(after.phases):
        divergences.append(
            f"phase count changed ({len(before.phases)} -> {len(after.phases)})"
        )
        return divergences
    for p_idx, (bp, ap) in enumerate(zip(before.phases, after.phases, strict=True)):
        if bp.ordinal != ap.ordinal:
            divergences.append(
                f"phase[{p_idx}] ordinal changed ({bp.ordinal} -> {ap.ordinal})"
            )
        if bp.keyword != ap.keyword:
            divergences.append(
                f"phase[{p_idx}] keyword changed ({bp.keyword!r} -> {ap.keyword!r})"
            )
        if bp.title != ap.title:
            divergences.append(
                f"phase[{p_idx}] title changed ({bp.title!r} -> {ap.title!r})"
            )
        b_tasks = _phase_task_signature(bp)
        a_tasks = _phase_task_signature(ap)
        if len(b_tasks) != len(a_tasks):
            divergences.append(
                f"phase[{p_idx}] task count changed ({len(b_tasks)} -> {len(a_tasks)})"
            )
            continue
        for t_idx, (bt, at) in enumerate(zip(b_tasks, a_tasks, strict=True)):
            _compare_task(bt, at, f"phase[{p_idx}].task[{t_idx}]", divergences)
    _compare_bugs(before.bugs, after.bugs, divergences)
    return divergences


def _format_bugs_entry(source_plan: Path, divergences: list[str]) -> str:
    """Render the BUGS.md entry appended on semantic divergence.

    Mirrors the existing entry style in BUGS.md: a leading ``- [ ]``
    bullet under the ``## Bugs`` heading, with the divergence list in a
    fenced ``text`` block so multi-line detail stays intact.
    """
    today = _dt.date.today().isoformat()
    lines = [
        "",
        f"- [ ] Stage 8 divergence detected {today} by "
        "`check_duplo_generated_fmt.py`: `bob-plan fmt` produced a "
        "semantically non-equivalent copy of "
        f"`{source_plan}`. The source was copied to `{SCRATCH}` and "
        "formatted in place; comparing the parsed source against the "
        "parsed formatted copy yielded the following structural "
        "mismatches (only task IDs, phase-id comments, indentation "
        "normalization, and the format magic line are permitted to "
        "differ):",
        "",
        "```text",
        *divergences,
        "```",
        "",
    ]
    return "\n".join(lines)


def _append_bugs_entry(source_plan: Path, divergences: list[str]) -> None:
    entry = _format_bugs_entry(source_plan, divergences)
    with BUGS_MD.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def main() -> int:
    _step("Stage 8 verification: bob-plan fmt additive-only on a duplo plan")
    if not BOB_PLAN.exists():
        print(
            f"FAIL: {BOB_PLAN} not found. Install bob-tools into the venv "
            "(pip install -e .) before running this check.",
            file=sys.stderr,
        )
        return 1
    if not BUGS_MD.exists():
        print(f"FAIL: {BUGS_MD} not found", file=sys.stderr)
        return 1

    _step(f"globbing {DUPLO_GLOB_ROOT}/{DUPLO_GLOB_PATTERN}")
    source_plan = _pick_duplo_plan()
    if source_plan is None:
        print(
            f"FAIL: no parent of any {DUPLO_GLOB_ROOT}/{DUPLO_GLOB_PATTERN} "
            "also contains a PLAN.md",
            file=sys.stderr,
        )
        return 1
    _step(f"chose source plan: {source_plan}")

    _step(f"copying {source_plan} -> {SCRATCH}")
    try:
        shutil.copyfile(source_plan, SCRATCH)
    except OSError as exc:
        print(f"FAIL: could not copy source plan: {exc}", file=sys.stderr)
        return 1

    _step(f"running: {BOB_PLAN.name} fmt {SCRATCH}")
    try:
        result = subprocess.run(
            [str(BOB_PLAN), "fmt", str(SCRATCH)],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"FAIL: bob-plan fmt timed out after {exc.timeout}s",
            file=sys.stderr,
        )
        return 1
    if result.returncode != 0:
        print(
            "FAIL: bob-plan fmt exited "
            f"{result.returncode}\n--- stdout ---\n{result.stdout}"
            f"--- stderr ---\n{result.stderr}",
            file=sys.stderr,
        )
        return 1
    _step("bob-plan fmt exited 0")

    _step("parsing source plan (compat mode)")
    try:
        before = parse_plan(source_plan.read_text(), source_path=source_plan)
    except PlanSyntaxError as exc:
        print(f"FAIL: source plan failed to parse: {exc}", file=sys.stderr)
        return 1

    _step("parsing formatted copy (strict mode)")
    try:
        after = parse_plan(SCRATCH.read_text(), strict=True, source_path=SCRATCH)
    except PlanSyntaxError as exc:
        print(
            f"FAIL: formatted copy failed to parse in strict mode: {exc}",
            file=sys.stderr,
        )
        return 1

    _step("comparing parse trees in lockstep")
    divergences = _compare_plans(before, after)
    if divergences:
        _step(f"FAIL: {len(divergences)} structural divergence(s) found")
        for d in divergences:
            print(f"  - {d}", flush=True)
        _step(f"appending divergence entry to {BUGS_MD}")
        try:
            _append_bugs_entry(source_plan, divergences)
        except OSError as exc:
            print(f"FAIL: could not append to BUGS.md: {exc}", file=sys.stderr)
            return 1
        return 1

    _step(
        "PASS: parse trees agree (only task IDs, phase-id comments, "
        "indentation, and magic line were added)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
