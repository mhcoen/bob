"""Pure operations on typed Plan objects (validate, migrate, mutate, schedule)."""

from __future__ import annotations

from collections.abc import Iterator

from bob_tools.planfile.model import (
    Phase,
    Plan,
    PlanValidationError,
    Task,
    TaskContext,
)


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


def validate_plan(plan: Plan) -> None:
    """Validate referential integrity of ``@deps`` references in ``plan``.

    Every task ID listed in any task's ``deps`` must resolve to the
    ``task_id`` of some task in the plan. Otherwise raise
    :class:`PlanValidationError` carrying one message per missing
    reference (design doc section 8 phase A: "validation requires
    referenced IDs to exist in the plan").

    Parse-time concerns (syntax, structure) are not re-checked here; the
    parser raises :class:`PlanSyntaxError` for those. Validation only
    cross-checks references between already-parsed objects.
    """
    known_ids: set[str] = {
        task.task_id for task in _iter_plan_tasks(plan) if task.task_id is not None
    }

    errors: list[str] = []
    for task in _iter_plan_tasks(plan):
        for dep in task.deps:
            if dep not in known_ids:
                ref = (
                    task.task_id
                    if task.task_id is not None
                    else (f"line {task.line_number}")
                )
                errors.append(f"task {ref} references unknown dep {dep}")

    if errors:
        raise PlanValidationError(errors)


def _task_matches_label(task: Task, ref: str) -> bool:
    """Return ``True`` when ``ref`` resolves to ``task``.

    Match order (each step decisive; no fall-through ambiguity):

    1. Exact match against ``task.task_id``. This is the canonical
       form and is the only path that survives once :func:`migrate`
       has assigned ids to every task. Per design doc section 7.2
       caveat, the comparison is whole-string equality so the
       ``T-000001`` / ``T-0000010`` prefix overlap cannot conflate
       two distinct tasks.
    2. Exact match against ``task.text``.
    3. Prefix match where ``task.text`` begins with ``ref`` followed
       by a structural separator (``:``, ``)``, or whitespace). Pre-id
       PLAN.md files put a duplo-style label at the start of the task
       text (``task-001: Bring up scaffold``); matching with a required
       trailing separator keeps ``task-001`` from matching
       ``task-0010: ...``.

    Substring matches without a trailing separator are deliberately
    rejected: that was the bug the design doc calls out as the
    motivation for moving resolution off raw-text scanning and onto
    parsed task entries.
    """
    if task.task_id is not None and task.task_id == ref:
        return True
    if task.text == ref:
        return True
    return any(task.text.startswith(ref + sep) for sep in (":", ")", " ", "\t"))


def _iter_phase_tasks_with_phase(plan: Plan) -> Iterator[tuple[Task, Phase]]:
    """Yield every ``(task, phase)`` pair for tasks inside a phase.

    Bug tasks are not yielded here; resolve_task_context handles them
    in a separate pass so the ``phase`` parameter is never ``None``
    inside this iterator. Subsection tasks share their parent phase's
    identity for resolution purposes (design doc section 11 question 5:
    subsections are humans-only grouping; they have no phase_id of
    their own).
    """
    for phase in plan.phases:
        for task in _iter_tasks(phase.tasks):
            yield task, phase
        for subsection in phase.subsections:
            for task in _iter_tasks(subsection.tasks):
                yield task, phase


def resolve_task_context(plan: Plan, task_label_or_id: str) -> TaskContext:
    """Resolve a task reference to its containing phase context.

    Replaces ``ledger_emit.find_explicit_phase_id_for_task``. Per design
    doc section 7.1, this is the single resolver for the task → phase
    mapping; ``ledger_emit.resolve_phase_id`` becomes a thin shim that
    promotes the returned :class:`TaskContext` to the ledger's
    ``PhaseIdResolution`` shape and supplies the ordinal-degraded
    fallback when the explicit lookup misses.

    Resolution walks parsed :class:`Task` entries (not raw text) using
    :func:`_task_matches_label`. Phase tasks are searched first, in
    document order, including subsection tasks. Bug-section tasks are
    searched last and return ``phase_id=None`` / ``phase_id_source=
    "none"`` since bugs have no containing phase. Unresolved references
    fall through to the same none-shaped :class:`TaskContext` so
    callers branch on ``phase_id is None`` (or on ``task_id is None``
    when they need to distinguish "matched a bug" from "matched
    nothing"), not on exceptions.

    ``plan_phase_count`` is always populated from ``len(plan.phases)``
    so the ordinal-fallback shim has the count it needs without a
    second pass over the plan.
    """
    plan_phase_count = len(plan.phases)

    for task, phase in _iter_phase_tasks_with_phase(plan):
        if _task_matches_label(task, task_label_or_id):
            return TaskContext(
                task_id=task.task_id,
                phase_id=phase.phase_id,
                phase_id_source=phase.phase_id_source,
                label=task_label_or_id,
                plan_phase_count=plan_phase_count,
            )

    if plan.bugs is not None:
        for task in _iter_tasks(plan.bugs.tasks):
            if _task_matches_label(task, task_label_or_id):
                return TaskContext(
                    task_id=task.task_id,
                    phase_id=None,
                    phase_id_source="none",
                    label=task_label_or_id,
                    plan_phase_count=plan_phase_count,
                )

    return TaskContext(
        task_id=None,
        phase_id=None,
        phase_id_source="none",
        label=task_label_or_id,
        plan_phase_count=plan_phase_count,
    )
