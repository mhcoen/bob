"""Pure operations on typed Plan objects (validate, migrate, mutate, schedule)."""

from __future__ import annotations

from collections.abc import Iterator

from bob_tools.planfile.model import Plan, PlanValidationError, Task


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
