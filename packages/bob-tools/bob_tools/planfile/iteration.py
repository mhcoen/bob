"""Plan and task traversal helpers (pure iteration, no mutation/validation)."""

from __future__ import annotations

from collections.abc import Iterator

from bob_tools.planfile.model import Phase, Plan, Task


def _iter_task_tree_with_paths(
    task: Task, path: tuple[int, ...] = ()
) -> Iterator[tuple[tuple[int, ...], Task]]:
    yield path, task
    for index, child in enumerate(task.children):
        yield from _iter_task_tree_with_paths(child, (*path, index))


def bug_count(plan: Plan) -> int:
    """Return the number of bug tasks in ``plan``.

    Returns ``0`` when ``plan.bugs is None`` (no Bugs section was parsed)
    and the count of top-level bug tasks otherwise. Verification scripts
    use this to surface a concrete count rather than the ambiguous
    ``bugs={p.bugs is not None}`` boolean (where ``bugs=False`` can be
    misread as "the Bugs section exists but is empty" instead of "no
    Bugs section was found"). The count covers root tasks only; nested
    subtask counts are left for callers that need them.
    """
    if plan.bugs is None:
        return 0
    return len(plan.bugs.tasks)


def _iter_tasks(tasks: tuple[Task, ...]) -> Iterator[Task]:
    """Yield every task in ``tasks``, descending into ``children`` first."""
    for task in tasks:
        yield task
        yield from _iter_tasks(task.children)


def _iter_plan_tasks(plan: Plan) -> Iterator[Task]:
    """Yield every Task in the plan: phase tasks, subsection tasks, bugs."""
    for phase in plan.phases:
        yield from _iter_tasks(phase.tasks)
        for subsection in phase.subsections:
            yield from _iter_tasks(subsection.tasks)
    if plan.bugs is not None:
        yield from _iter_tasks(plan.bugs.tasks)


def _find_task_by_id(plan: Plan, task_id: str) -> Task | None:
    """Return the task whose ``task_id`` equals ``task_id``, or ``None``.

    Walks the parsed tree via :func:`_iter_plan_tasks` and compares
    ``task.task_id == task_id``. The library MUST NOT resolve task
    references with substring matching (e.g. ``task_id in line``)
    because ``T-NNNNNN`` IDs prefix-overlap: ``T-000001`` is a substring
    of ``T-0000010``, so a substring search would conflate the two. Per
    design doc section 7.2 caveat. Returns the first match in iteration
    order; a well-formed plan has unique IDs, so the order only matters
    when callers want to surface a duplicate-id diagnostic separately.
    """
    for task in _iter_plan_tasks(plan):
        if task.task_id == task_id:
            return task
    return None


def _plan_phase_path(phase_index: int) -> str:
    return f"phases[{phase_index}]"


def _plan_subsection_path(phase_index: int, sub_index: int) -> str:
    return f"phases[{phase_index}].subsections[{sub_index}]"


def _iter_plan_top_level_tasks_with_label(
    plan: Plan,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for each top-level task in the plan.

    ``label`` locates the task within the plan structure
    (``phases[i].tasks[j]``, ``phases[i].subsections[j].tasks[k]``, or
    ``bugs.tasks[i]``) so validator messages can identify which task's
    round-trip failed without descending into children. Child tasks are
    not yielded: field-stability is checked per-tree, since the Stage 10
    harness recurses through children when invoked on the root task.
    """
    for phase_index, phase in enumerate(plan.phases):
        for task_index, task in enumerate(phase.tasks):
            yield f"{_plan_phase_path(phase_index)}.tasks[{task_index}]", task
        for sub_index, sub in enumerate(phase.subsections):
            for task_index, task in enumerate(sub.tasks):
                yield (
                    f"{_plan_subsection_path(phase_index, sub_index)}"
                    f".tasks[{task_index}]",
                    task,
                )
    if plan.bugs is not None:
        for task_index, task in enumerate(plan.bugs.tasks):
            yield f"bugs.tasks[{task_index}]", task


def _iter_plan_tasks_with_label(
    plan: Plan,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for every task in the plan, including children.

    Labels follow :func:`_iter_plan_top_level_tasks_with_label` with the
    child path appended (``...children[i]...``). Used by the
    constructed-mode checks that operate per-node (``task_id`` presence
    and ``trailing_lines`` shape) rather than per-tree.
    """
    for label, root_task in _iter_plan_top_level_tasks_with_label(plan):
        for path, node in _iter_task_tree_with_paths(root_task):
            yield label + _child_path_suffix(path), node


def _child_path_suffix(path: tuple[int, ...]) -> str:
    return "".join(f".children[{index}]" for index in path)


def _iter_phase_tasks_with_phase(
    plan: Plan,
) -> Iterator[tuple[Task, Phase, int]]:
    """Yield ``(task, phase, doc_index)`` for every task inside a phase.

    ``doc_index`` is the 1-based position of ``phase`` within
    ``plan.phases``; :func:`resolve_task_context` uses it to synthesize
    a ``phase_NNN`` ordinal fallback when the containing phase has no
    explicit id (design doc section 7.1, "Ordinal fallback. The n-th
    phase heading in document order"). The plan's positional addressing
    uses 1-based indexes throughout, so the synthesized id starts at
    ``phase_001``.

    Bug tasks are not yielded here; resolve_task_context handles them
    in a separate pass so the ``phase`` parameter is never ``None``
    inside this iterator. Subsection tasks share their parent phase's
    identity for resolution purposes (design doc section 11 question 5:
    subsections are humans-only grouping; they have no phase_id of
    their own).
    """
    for doc_index, phase in enumerate(plan.phases, start=1):
        for task in _iter_tasks(phase.tasks):
            yield task, phase, doc_index
        for subsection in phase.subsections:
            for task in _iter_tasks(subsection.tasks):
                yield task, phase, doc_index


def _iter_phase_task_tree_with_label(
    phase: Phase,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for every task in ``phase``, including children.

    Labels mirror :func:`_iter_plan_tasks_with_label` but are rooted at
    the phase (``tasks[i]...``, ``subsections[j].tasks[k]...``) so
    diagnostics from :func:`replace_phase_validated` locate the offending
    task within the supplied ``new_phase`` rather than within the plan
    (which has not yet been substituted at the point of the check).
    """
    for task_index, task in enumerate(phase.tasks):
        for path, node in _iter_task_tree_with_paths(task):
            yield f"tasks[{task_index}]{_child_path_suffix(path)}", node
    for sub_index, sub in enumerate(phase.subsections):
        for task_index, task in enumerate(sub.tasks):
            for path, node in _iter_task_tree_with_paths(task):
                yield (
                    f"subsections[{sub_index}].tasks[{task_index}]"
                    f"{_child_path_suffix(path)}",
                    node,
                )
