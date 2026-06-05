"""Co-locate a covering test task inside any module-creating batch.

mcloop's coverage gate accepts created code together with its exercising
test only when both land in the *same* batch (the same ``[BATCH]`` parent
task). A batch whose subtasks create new executable ``.py`` modules but
whose covering test is deferred to a later phase fails that gate within
the batch: the created code is rejected because nothing in the batch
exercises it.

:func:`ensure_batch_test_coverage` walks an assembled, typed
:class:`bob_tools.planfile.Plan` and, for every ``[BATCH]`` task that
creates new non-test ``.py`` modules without a sibling test task that
already exercises them, appends a covering test task as a sibling child
in the SAME batch. The batch is then self-contained: created modules and
their exercising test are accepted together.

The transform is pure and idempotent. It only ever adds a sibling test
task; it never moves, removes, or rewrites existing tasks, and re-running
it on its own output is a no-op (the test task it adds already covers the
modules, so no further task is emitted).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator

from bob_tools.planfile import Phase, Plan, Task, make_task

# Any ``.py`` path token appearing in a task description. ``\w`` covers
# the underscore and alphanumerics that make up module/file stems; the
# leading character class also admits directory separators, dots, and
# hyphens so ``tests/test_scanner.py`` and ``duplo/frame_filter.py`` are
# captured whole.
_PY_PATH_RE = re.compile(r"[\w./-]+\.py\b")

# A task that names a test file is treated as a test task. The synthesizer
# (and the task this module emits) names the covering test file
# explicitly, so a referenced test-file path is the unambiguous signal of
# "this task is the batch's covering test" -- it cannot be confused with a
# module-creation task that merely mentions the word "test" in prose.
_BATCH_FLAG = "BATCH"


def _py_paths(text: str) -> list[str]:
    """Return every ``*.py`` path token mentioned in *text*."""
    return _PY_PATH_RE.findall(text)


def _basename_stem(path: str) -> str:
    """Return the lowercased module stem of a ``.py`` *path*.

    ``duplo/scanner.py`` -> ``scanner``; ``tests/test_scanner.py`` ->
    ``test_scanner``. Directory components and the ``.py`` suffix are
    dropped; the result is lowercased so matching is case-insensitive
    (Python module names are conventionally lowercase).
    """
    base = path.rsplit("/", 1)[-1]
    if base.endswith(".py"):
        base = base[: -len(".py")]
    return base.lower()


def _is_test_path(path: str) -> bool:
    """True when *path* names a test file (``test_*``/``*_test`` or under tests/)."""
    components = path.split("/")
    if "tests" in components[:-1]:
        return True
    stem = _basename_stem(path)
    return stem.startswith("test_") or stem.endswith("_test")


def _created_module_stems(text: str) -> set[str]:
    """Stems of the non-test ``.py`` modules a task description references."""
    return {_basename_stem(p) for p in _py_paths(text) if not _is_test_path(p)}


def _is_test_task(text: str) -> bool:
    """True when a task references a test file (and so is a covering test)."""
    return any(_is_test_path(p) for p in _py_paths(text))


def _covered_stems(text: str, candidate_stems: set[str]) -> set[str]:
    """Stems among *candidate_stems* that this test-task description targets.

    A test task targets a module stem when it names the module's test file
    (``test_<stem>.py``), names the module file itself (``<stem>.py``), or
    mentions the stem as a whole word. Only stems in *candidate_stems* (the
    modules actually created in the batch) are returned, so an incidental
    mention of an unrelated file never manufactures phantom coverage.
    """
    targeted: set[str] = set()
    for path in _py_paths(text):
        stem = _basename_stem(path)
        if stem.startswith("test_"):
            targeted.add(stem[len("test_") :])
        elif stem.endswith("_test"):
            targeted.add(stem[: -len("_test")])
        else:
            targeted.add(stem)
    lowered = text.lower()
    for stem in candidate_stems:
        if re.search(rf"\b{re.escape(stem)}\b", lowered):
            targeted.add(stem)
    return targeted & candidate_stems


def _iter_descendants(task: Task) -> Iterator[Task]:
    """Yield every descendant of *task* (its children, recursively)."""
    for child in task.children:
        yield child
        yield from _iter_descendants(child)


def _covering_test_text(uncovered: set[str]) -> str:
    """ASCII covering-test task text exercising the *uncovered* module stems.

    The task names a concrete test file (``tests/test_<stem>.py``) and
    lists the module files it exercises, so a re-scan recognises it as the
    batch's covering test. ASCII only, no backticks: PLAN.md task lines are
    parsed literally by mcloop.
    """
    ordered = sorted(uncovered)
    test_file = f"tests/test_{ordered[0]}.py"
    modules = ", ".join(f"{stem}.py" for stem in ordered)
    return f"Add tests in {test_file} exercising {modules} created in this batch"


def _process_task(task: Task) -> Task:
    """Return *task* with a covering test child added if its batch needs one.

    Children are processed first so a nested batch is repaired before its
    parent is evaluated. A non-batch task is returned unchanged (after the
    recursion). A batch task gains exactly one sibling test task when it
    creates module(s) that no existing sibling test exercises.
    """
    new_children = tuple(_process_task(child) for child in task.children)
    if new_children != task.children:
        task = dataclasses.replace(task, children=new_children)

    if _BATCH_FLAG not in task.flag_tags:
        return task

    descendants = list(_iter_descendants(task))
    created: set[str] = set()
    for node in descendants:
        created |= _created_module_stems(node.text)
    if not created:
        return task

    covered: set[str] = set()
    for node in descendants:
        if _is_test_task(node.text):
            covered |= _covered_stems(node.text, created)

    uncovered = created - covered
    if not uncovered:
        return task

    test_task = make_task(_covering_test_text(uncovered))
    return dataclasses.replace(task, children=task.children + (test_task,))


def _process_phase(phase: Phase) -> Phase:
    """Return *phase* with batch coverage ensured in tasks and subsections."""
    new_tasks = tuple(_process_task(t) for t in phase.tasks)
    new_subsections = tuple(
        dataclasses.replace(sub, tasks=tuple(_process_task(t) for t in sub.tasks))
        for sub in phase.subsections
    )
    if new_tasks == phase.tasks and new_subsections == phase.subsections:
        return phase
    return dataclasses.replace(phase, tasks=new_tasks, subsections=new_subsections)


def ensure_batch_test_coverage(plan: Plan) -> Plan:
    """Co-locate a covering test task inside every module-creating batch.

    For each ``[BATCH]`` task whose subtasks create new non-test ``.py``
    modules without a sibling test task that already exercises them, a
    covering test task is appended as a sibling child of that batch, so the
    batch is self-contained for mcloop's coverage gate.

    The plan is returned unchanged (same object) when no batch needs a
    covering test. Added tasks carry no ``task_id`` so a downstream
    :func:`bob_tools.planfile.migrate` assigns ids alongside the rest.
    """
    new_phases = tuple(_process_phase(phase) for phase in plan.phases)
    if new_phases == plan.phases:
        return plan
    return dataclasses.replace(plan, phases=new_phases)


__all__ = ["ensure_batch_test_coverage"]
