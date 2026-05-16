"""Parity check: bob_tools compat-mode parsing agrees with mcloop on real files.

Stage 8 acceptance: ``parse_plan(text)`` in compat mode must recognize
the same set of tasks ``mcloop.checklist.parse`` recognizes when both
read one of the real PLAN.md files on this machine. This is the
empirical check that the new parser is a drop-in replacement for the
checklist parser mcloop has shipped against for the lifetime of the
project: same task positions, same indents, same checkbox status, same
bugs-section detection.

The two parsers return different shapes — mcloop yields a flat
``list[Task]`` with each task tagged by its stage string, and bob_tools
yields a typed :class:`~bob_tools.planfile.model.Plan` with phases and
subsections. To compare them this module flattens both into a sorted
list of ``(line_number_zero_indexed, indent_level, status_word)``
triples per task, then asserts the two lists are equal. Line numbers
are normalized to zero-indexed because mcloop stores raw ``enumerate``
indices and bob_tools stores ``idx + 1``; the offset is a presentation
choice rather than a semantic disagreement.

mcloop is not a bob_tools dependency, so the module is not installed
in the bob_tools venv. The sibling project's source tree is added to
``sys.path`` at runtime to import ``mcloop.checklist`` directly; the
test skips with a clear message when the sibling project is not
present (CI, fresh clones, anywhere outside the dev environment).

Subsequent tasks in Stage 8 extend this file to assert on flag-tag and
action-tag presence, RULEDOUT attachments, and the one documented
divergence (mcloop's substring matcher classifying prose-mention tasks
as USER/BATCH/AUTO). This module currently asserts the structural
backbone — task positions and statuses — which is the foundation those
later assertions ride on.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from bob_tools.planfile import Plan, TaskStatus, parse_plan

SOURCE_PATHS: tuple[Path, ...] = (
    Path("/Users/mhcoen/proj/duplo/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md"),
)

_MCLOOP_ROOT = Path("/Users/mhcoen/proj/mcloop")


def _load_mcloop_checklist() -> Any | None:
    """Import ``mcloop.checklist`` from the sibling project, or return None.

    bob_tools intentionally does not depend on mcloop, so the package
    is not on the venv's import path. The sibling project's source
    tree is prepended to :data:`sys.path` at runtime so the parity
    test can import the live module on a dev machine where both repos
    are checked out. Returns ``None`` when the source file is missing
    or the import fails for any reason, which makes the test skip
    cleanly outside the dev environment instead of erroring.
    """
    if not (_MCLOOP_ROOT / "mcloop" / "checklist.py").is_file():
        return None
    if str(_MCLOOP_ROOT) not in sys.path:
        sys.path.insert(0, str(_MCLOOP_ROOT))
    try:
        from mcloop import checklist  # type: ignore[import-not-found]
    except Exception:
        return None
    return checklist


_STATUS_NAME: dict[TaskStatus, str] = {
    TaskStatus.TODO: "TODO",
    TaskStatus.DONE: "DONE",
    TaskStatus.FAILED: "FAILED",
}


def _flatten_mcloop(tasks: list[Any]) -> list[tuple[int, int, str]]:
    """Sorted ``(line_number, indent_level, status)`` triples from mcloop."""
    out: list[tuple[int, int, str]] = []

    def walk(ts: list[Any]) -> None:
        for t in ts:
            if t.checked:
                status = "DONE"
            elif t.failed:
                status = "FAILED"
            else:
                status = "TODO"
            out.append((t.line_number, t.indent_level, status))
            walk(t.children)

    walk(tasks)
    return sorted(out)


def _flatten_bobtools(plan: Plan) -> list[tuple[int, int, str]]:
    """Sorted ``(line_number, indent_level, status)`` triples from bob_tools.

    Bob_tools stores 1-indexed line numbers; the value is reduced by
    one so it lines up with mcloop's 0-indexed ``enumerate``-derived
    line numbers. Tasks live in three places — phase root tasks, phase
    subsection tasks, and bugs-section tasks — and the walker visits
    all three so the flattening matches what mcloop sees from a single
    linear scan.
    """
    out: list[tuple[int, int, str]] = []

    def walk(task: Any) -> None:
        out.append((task.line_number - 1, task.indent_level, _STATUS_NAME[task.status]))
        for c in task.children:
            walk(c)

    for phase in plan.phases:
        for t in phase.tasks:
            walk(t)
        for sub in phase.subsections:
            for t in sub.tasks:
                walk(t)
    if plan.bugs is not None:
        for t in plan.bugs.tasks:
            walk(t)
    return sorted(out)


def _count_mcloop_bugs(tasks: list[Any]) -> int:
    """Total task count under the ``Bugs`` stage in an mcloop parse.

    mcloop tags every task with a ``stage`` string; bug tasks carry
    the literal ``"Bugs"``. Walking the tree and counting matches
    gives the same denominator bob_tools reports via
    ``len(plan.bugs.tasks)`` (recursing into children).
    """
    n = 0

    def walk(ts: list[Any]) -> None:
        nonlocal n
        for t in ts:
            if t.stage == "Bugs":
                n += 1
            walk(t.children)

    walk(tasks)
    return n


def _count_bobtools_bugs(plan: Plan) -> int:
    if plan.bugs is None:
        return 0
    n = 0

    def walk(task: Any) -> None:
        nonlocal n
        n += 1
        for c in task.children:
            walk(c)

    for t in plan.bugs.tasks:
        walk(t)
    return n


@pytest.mark.parametrize(
    "source_path",
    SOURCE_PATHS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_compat_parse_matches_mcloop(source_path: Path) -> None:
    """Compat-mode bob_tools parse must see the same tasks as mcloop.

    The shared expectation is the set of recognized task positions and
    their checkbox status. Both parsers run their structural-sanity
    check first, so a corrupted file would cause both to raise before
    this assertion runs; on the real fixtures both checks pass. Line
    numbers are normalized to 0-indexed; indent and status are taken
    verbatim because both parsers compute them from the same checkbox
    regex (which is intentional — bob_tools mirrors mcloop's
    ``CHECKBOX_RE`` byte-for-byte).
    """
    if not source_path.is_file():
        pytest.skip(
            f"source PLAN.md not present at {source_path}; "
            "this parity check only runs in the dev environment "
            "where the sibling projects are checked out"
        )
    checklist = _load_mcloop_checklist()
    if checklist is None:
        pytest.skip(
            f"mcloop.checklist could not be imported from {_MCLOOP_ROOT}; "
            "this parity check only runs in the dev environment "
            "where the mcloop project is checked out alongside bob_tools"
        )

    text = source_path.read_text()
    bob_plan = parse_plan(text)
    mc_tasks = checklist.parse(source_path)

    bob_flat = _flatten_bobtools(bob_plan)
    mc_flat = _flatten_mcloop(mc_tasks)

    assert bob_flat == mc_flat, (
        f"bob_tools.planfile.parse_plan and mcloop.checklist.parse "
        f"disagree on the task set of {source_path}. "
        f"Triples are (line_number_0indexed, indent_level, status).\n"
        f"bob_tools only: {sorted(set(bob_flat) - set(mc_flat))}\n"
        f"mcloop only:    {sorted(set(mc_flat) - set(bob_flat))}"
    )

    bob_bug_count = _count_bobtools_bugs(bob_plan)
    mc_bug_count = _count_mcloop_bugs(mc_tasks)
    assert bob_bug_count == mc_bug_count, (
        f"bugs-section task count disagrees on {source_path}: "
        f"bob_tools={bob_bug_count} mcloop={mc_bug_count}"
    )
