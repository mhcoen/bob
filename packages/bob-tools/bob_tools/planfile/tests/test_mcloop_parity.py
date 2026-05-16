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

Beyond the structural backbone (task positions, indents, statuses),
this module asserts agreement on every coarse-grained structural
fact the two parsers should see identically on a compat-mode plan:
phase/stage ordinals, presence of a Bugs section with content,
per-phase task counts, and per-task ``[RULEDOUT]`` attachment counts.

For the operational tags (USER, BATCH, AUTO) full equality is not
assertable because the two parsers use different recognizers:

* bob_tools matches each tag only at the leading position of the
  task text (after any task ID is stripped), per design doc section
  4.3.
* mcloop's ``is_user_task``, ``is_batch_task``, and ``is_auto_task``
  are plain substring checks against ``task.text`` — ``_USER_TAG in
  task.text``, ``_BATCH_TAG in task.text``, and ``_AUTO_TAG_RE.search
  (task.text)`` respectively. BATCH gets the exact same substring
  treatment as USER in ``is_batch_task`` (the design doc's
  prose-mention divergence note flagged USER explicitly, but the
  same shape applies to BATCH and AUTO, which is the detail this
  task pins down).

The asymmetry is therefore one-sided: any task whose body merely
*mentions* the bracket form (``"marked with [USER]"``, ```"use a
[BATCH] parent"```, etc.) is tagged by mcloop and not by bob_tools.
The reverse — bob_tools flagging a task that mcloop's substring
matcher misses — is impossible by construction: a leading-anchored
match is necessarily also a substring match. The parity assertion
encodes this directly:

1. ``bob ⊆ mcloop`` (subset relation) on each of USER / BATCH /
   AUTO. A violation in this direction would be a bob_tools regression.
2. For every mcloop-only divergence (mcloop flags it, bob_tools
   does not), the corresponding bracket literal must be present
   somewhere in ``task.text``. This is tautologically true given
   mcloop's definition, so the assertion's job is anchoring the
   *test's* claim: the only allowed divergences are substring-match
   prose-mention cases, never some other unrelated disagreement.

Together those two checks express "the parity test allows this
specific divergence and asserts nothing else differs."
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

from bob_tools.planfile import Plan, Task, TaskStatus, parse_plan

SOURCE_PATHS: tuple[Path, ...] = (
    Path("/Users/mhcoen/proj/duplo/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md"),
)

_MCLOOP_ROOT = Path("/Users/mhcoen/proj/mcloop")

# Mirror of mcloop.checklist._STAGE_NUM_RE. Defined locally rather than
# imported from the (underscore-prefixed) private constant so this test
# file does not lock in the private name; mcloop's documented header
# grammar ("Stage N" / "Phase N") is the actual contract being verified.
_STAGE_NUM_RE = re.compile(r"\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE)

# Mirrors of mcloop's tag literals (``_USER_TAG``, ``_BATCH_TAG``,
# ``_AUTO_TAG_RE``). Used by the parity test to verify that every
# mcloop-only flag/action divergence is anchored to a bracket literal
# actually present in the task text — the substring-match
# prose-mention signature. Defined locally rather than imported from
# mcloop's underscore-prefixed constants so this test does not lock
# in mcloop's private names; mcloop's tag grammar is the contract.
_LITERAL_USER_RE = re.compile(r"\[USER\]")
_LITERAL_BATCH_RE = re.compile(r"\[BATCH\]")
_LITERAL_AUTO_RE = re.compile(r"\[AUTO:\w+\]")


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


def _bob_phase_ordinals(plan: Plan) -> tuple[int, ...]:
    """Phase/stage ordinals in document order from a bob_tools parse."""
    return tuple(p.ordinal for p in plan.phases)


def _mcloop_phase_ordinals(tasks: list[Any]) -> tuple[int, ...]:
    """Phase/stage ordinals in encounter order from an mcloop parse.

    mcloop stores each task's stage as the post-strip header text
    (e.g. ``"Stage 3: Foo"``); the ordinal is the digit after the
    keyword. Bug tasks (``stage="Bugs"``) and stage-less orphan tasks
    (``stage=""``) carry no ordinal and are skipped. Order is first-
    encounter via a depth-first walk, which matches the document order
    because mcloop builds the tree linearly from the file.
    """
    seen: set[int] = set()
    out: list[int] = []

    def walk(ts: list[Any]) -> None:
        for t in ts:
            if t.stage and t.stage != "Bugs":
                m = _STAGE_NUM_RE.search(t.stage)
                if m is not None:
                    num = int(m.group(1))
                    if num not in seen:
                        seen.add(num)
                        out.append(num)
            walk(t.children)

    walk(tasks)
    return tuple(out)


def _bob_phase_task_counts(plan: Plan) -> dict[int, int]:
    """Total task count per phase ordinal from a bob_tools parse.

    Counts every task under a phase — root tasks, subsection tasks,
    and all nested children — so the denominator matches what mcloop
    reports by summing all tasks whose ``stage`` resolves to the same
    ordinal.
    """

    def count_subtree(t: Task) -> int:
        n = 1
        for c in t.children:
            n += count_subtree(c)
        return n

    counts: dict[int, int] = {}
    for phase in plan.phases:
        n = 0
        for t in phase.tasks:
            n += count_subtree(t)
        for sub in phase.subsections:
            for t in sub.tasks:
                n += count_subtree(t)
        counts[phase.ordinal] = n
    return counts


def _mcloop_phase_task_counts(tasks: list[Any]) -> dict[int, int]:
    """Total task count per stage ordinal from an mcloop parse.

    Every task (root or child) with a ``stage`` that resolves to a
    numbered Stage/Phase header contributes one. Bug tasks
    (``stage="Bugs"``) and orphans (``stage=""``) are skipped because
    they do not belong to a phase.
    """
    counts: dict[int, int] = {}

    def walk(ts: list[Any]) -> None:
        for t in ts:
            if t.stage and t.stage != "Bugs":
                m = _STAGE_NUM_RE.search(t.stage)
                if m is not None:
                    num = int(m.group(1))
                    counts[num] = counts.get(num, 0) + 1
            walk(t.children)

    walk(tasks)
    return counts


def _bob_tasks_by_line(plan: Plan) -> dict[int, Task]:
    """Index every bob_tools task by 0-indexed line number.

    Lines are normalized to 0-indexed so the dict shares its key
    space with :func:`_mcloop_tasks_by_line`. Walks phase root tasks,
    phase subsection tasks, and bugs-section tasks — every place a
    Task lives — so the resulting index is exhaustive.
    """
    out: dict[int, Task] = {}

    def walk(t: Task) -> None:
        out[t.line_number - 1] = t
        for c in t.children:
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
    return out


def _mcloop_tasks_by_line(tasks: list[Any]) -> dict[int, Any]:
    """Index every mcloop task by its 0-indexed line number."""
    out: dict[int, Any] = {}

    def walk(ts: list[Any]) -> None:
        for t in ts:
            out[t.line_number] = t
            walk(t.children)

    walk(tasks)
    return out


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

    bob_ords = _bob_phase_ordinals(bob_plan)
    mc_ords = _mcloop_phase_ordinals(mc_tasks)
    assert bob_ords == mc_ords, (
        f"phase/stage ordinal sequence disagrees on {source_path}: "
        f"bob_tools={bob_ords} mcloop={mc_ords}"
    )

    # A Bugs section "is present" for cross-parser purposes only when
    # at least one task sits under it: mcloop's parser tags tasks via
    # the active stage string and never emits a standalone "Bugs
    # header" event, so an empty Bugs section is invisible to it.
    # Comparing nonzero-bug-count against ``plan.bugs is not None and
    # tasks`` keeps the assertion well-defined on the shape both
    # parsers can actually see.
    bob_bugs_present = bob_plan.bugs is not None and len(bob_plan.bugs.tasks) > 0
    mc_bugs_present = mc_bug_count > 0
    assert bob_bugs_present == mc_bugs_present, (
        f"bugs-section presence disagrees on {source_path}: "
        f"bob_tools={bob_bugs_present} mcloop={mc_bugs_present}"
    )

    bob_counts = _bob_phase_task_counts(bob_plan)
    mc_counts = _mcloop_phase_task_counts(mc_tasks)
    assert bob_counts == mc_counts, (
        f"per-phase task counts disagree on {source_path}: "
        f"bob_tools={bob_counts} mcloop={mc_counts}"
    )

    bob_by_line = _bob_tasks_by_line(bob_plan)
    mc_by_line = _mcloop_tasks_by_line(mc_tasks)
    # The flat-triple check above already proves these key sets match;
    # asserting again here makes the per-task crossing's preconditions
    # locally visible to a reader and surfaces a misalignment as a
    # targeted error rather than a downstream KeyError.
    assert bob_by_line.keys() == mc_by_line.keys(), (
        f"task line-number sets disagree on {source_path}: "
        f"bob_only={sorted(bob_by_line.keys() - mc_by_line.keys())} "
        f"mcloop_only={sorted(mc_by_line.keys() - bob_by_line.keys())}"
    )

    ruledout_mismatches: list[tuple[int, int, int]] = []
    status_mismatches: list[tuple[int, str, str]] = []
    flag_subset_violations: list[tuple[int, str]] = []
    unexplained_mc_only: list[tuple[int, str, str]] = []

    for ln in sorted(bob_by_line):
        b = bob_by_line[ln]
        m = mc_by_line[ln]
        # Status was already cross-checked in flat form; the per-line
        # restatement catches a future regression where the flat sort
        # masked a (line, indent, status) tuple-equivalence that did
        # not actually align the same task on each side.
        bob_status = _STATUS_NAME[b.status]
        mc_status = "DONE" if m.checked else "FAILED" if m.failed else "TODO"
        if bob_status != mc_status:
            status_mismatches.append((ln, bob_status, mc_status))
        if len(b.ruled_out) != len(m.eliminated):
            ruledout_mismatches.append((ln, len(b.ruled_out), len(m.eliminated)))
        # USER / BATCH / AUTO use the bob ⊆ mcloop subset relation
        # (see module docstring). A bob-side detection that mcloop's
        # substring matcher misses would imply a bob_tools parser bug,
        # because mcloop's substring is necessarily a superset of any
        # leading-anchored match. The reverse direction (mcloop=True,
        # bob=False) is the documented prose-mention divergence: the
        # mcloop literal (``[USER]`` / ``[BATCH]`` / ``[AUTO:...]``)
        # appears somewhere in the task text but not at the leading
        # position, so mcloop's plain-substring matcher fires while
        # bob_tools' leading-anchored matcher does not.
        flag_checks = (
            (
                "USER",
                "USER" in b.flag_tags,
                checklist.is_user_task(m),
                _LITERAL_USER_RE,
            ),
            (
                "BATCH",
                "BATCH" in b.flag_tags,
                checklist.is_batch_task(m),
                _LITERAL_BATCH_RE,
            ),
            (
                "AUTO",
                b.action_tag is not None,
                checklist.is_auto_task(m),
                _LITERAL_AUTO_RE,
            ),
        )
        for tag, bob_has, mc_has, literal_re in flag_checks:
            if bob_has and not mc_has:
                flag_subset_violations.append((ln, tag))
            elif mc_has and not bob_has:
                # The mcloop-only direction is allowed only as a
                # substring-match prose mention: the bracket literal
                # must be present somewhere in the raw task text.
                # mcloop's classifier defines this tautologically, so
                # a failure here means we are no longer reading the
                # same task on each side (line-number drift in one
                # parser, an mcloop API change, or a bob_tools
                # text-stripping bug that erased the bracket entirely).
                if not literal_re.search(m.text):
                    unexplained_mc_only.append((ln, tag, m.text))

    assert not status_mismatches, (
        f"per-task checkbox status disagrees on {source_path} "
        f"(line, bob_status, mcloop_status): {status_mismatches}"
    )
    assert not ruledout_mismatches, (
        f"per-task RULEDOUT attachment counts disagree on {source_path} "
        f"(line, bob_count, mcloop_count): {ruledout_mismatches}"
    )
    assert not flag_subset_violations, (
        f"bob_tools sees a leading tag that mcloop's substring matcher "
        f"misses on {source_path} (line, tag): {flag_subset_violations}. "
        f"This would mean bob_tools recognized a tag mcloop could not "
        f"have seen, which contradicts the bob⊆mcloop subset relation."
    )
    assert not unexplained_mc_only, (
        f"mcloop classifies tasks on {source_path} that bob_tools does "
        f"not, without the corresponding bracket literal appearing in "
        f"the raw task text. The only allowed mcloop-only divergence is "
        f"the substring-match prose-mention case; an mcloop-only flag "
        f"with no bracket literal in the text means the two parsers are "
        f"disagreeing on something other than substring-vs-leading "
        f"anchoring. (line, tag, text): {unexplained_mc_only}"
    )
