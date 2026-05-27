"""Task scheduling: ``next_tasks`` and the readiness/walk helpers it depends on."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

from bob_tools.planfile.model import Phase, Plan, Task, TaskStatus
from bob_tools.planfile.iteration import _iter_plan_tasks, _iter_tasks

def _build_done_ids(plan: Plan) -> set[str]:
    """Return the set of ``task_id`` values for every DONE task in ``plan``.

    Used by :func:`next_tasks` to evaluate ``@deps`` satisfaction in a
    single pass per call rather than re-walking the plan per task. Tasks
    without a ``task_id`` cannot be referenced by ``@deps`` (the
    canonical form uses ``T-NNNNNN``), so they are excluded.
    """
    return {
        task.task_id
        for task in _iter_plan_tasks(plan)
        if task.status == TaskStatus.DONE and task.task_id is not None
    }

def _deps_satisfied(task: Task, done_ids: set[str]) -> bool:
    """Return ``True`` when every ``@deps`` reference resolves to a DONE task.

    Per design doc section 6: dependencies that do not resolve to a known
    task are validation errors (raised by :func:`validate_plan`), not
    actionability blockers. ``next_tasks`` does not raise on unknown
    refs — it simply treats the dep as unsatisfied, so the task stays
    blocked. Callers run :func:`validate_plan` before scheduling to
    surface unknown refs as errors instead.
    """
    return all(dep in done_ids for dep in task.deps)

def _all_tasks_done(tasks: tuple[Task, ...]) -> bool:
    """Return ``True`` when every task in ``tasks`` (and descendants) is DONE."""
    for task in tasks:
        if task.status != TaskStatus.DONE:
            return False
        if not _all_tasks_done(task.children):
            return False
    return True

def _phase_complete(phase: Phase) -> bool:
    """Return ``True`` when every task in ``phase`` (root + subsections) is DONE.

    A FAILED task does not count as complete (the phase is stuck, not
    done); a TODO task does not count either. Mirrors mcloop's
    ``_stage_complete`` semantics.
    """
    if not _all_tasks_done(phase.tasks):
        return False
    return all(_all_tasks_done(sub.tasks) for sub in phase.subsections)

def _get_batch_children(parent: Task) -> tuple[Task, ...]:
    """Collect consecutive batchable children under a ``[BATCH]`` parent.

    Mirrors mcloop's ``get_batch_children`` (``checklist.py`` line 654):
    start from the first unchecked child and collect until hitting a
    ``[USER]`` or ``[AUTO:...]`` child, or running out of children.
    Already-DONE children are skipped (and bump ``seen_non_failed``).
    A FAILED child stops collection once at least one non-failed child
    has been seen — before that point, leading FAILED children are
    skipped, matching the mcloop behavior of treating early-failed
    children as a prelude rather than a dependency barrier.
    """
    batch: list[Task] = []
    seen_non_failed = False
    for child in parent.children:
        if child.status == TaskStatus.DONE:
            seen_non_failed = True
            continue
        if child.status == TaskStatus.FAILED:
            if batch or seen_non_failed:
                break
            continue
        if "USER" in child.flag_tags or child.action_tag is not None:
            break
        batch.append(child)
    return tuple(batch)

def _walk_actionable(
    tasks: tuple[Task, ...],
    *,
    is_subtask: bool,
    done_ids: set[str],
) -> Iterator[Task]:
    """Yield actionable tasks from ``tasks`` in depth-first document order.

    Mirrors mcloop's ``_search_tasks`` (``checklist.py`` line 356) with
    three additions: ``@deps`` satisfaction (design doc section 6.2),
    ``[BATCH]`` parent surfacing (design doc section 6 "A `[BATCH]`
    parent surfaces as one unit"), and the change from "return one
    task" to "yield each in order" so callers can satisfy ``limit > 1``.

    Per design doc section 6, a task is actionable iff:

    1. Status is TODO.
    2. Every ``@deps`` reference is DONE (per ``done_ids``).
    3. No FAILED ancestor — enforced by the walk skipping FAILED tasks
       and never descending into them; a FAILED root-level task is
       skipped (``continue``) and a FAILED subtask returns (``return``),
       blocking later siblings under the same parent (item 3 of the
       priority/scoping list, "Failed subtasks block later siblings").
    4. Leaf-before-parent: a task with children recurses first; the
       parent itself is yielded only when no descendant is actionable.

    ``is_subtask`` distinguishes root-level lists (phase tasks, bug
    tasks, subsection root tasks) — where FAILED is skipped — from
    nested child lists, where FAILED stops the walk and blocks later
    siblings.
    """
    for task in tasks:
        if task.status == TaskStatus.FAILED:
            if is_subtask:
                return
            continue
        if task.status == TaskStatus.DONE:
            continue
        if not _deps_satisfied(task, done_ids):
            continue

        if not task.children:
            yield task
            continue

        sub_iter = _walk_actionable(task.children, is_subtask=True, done_ids=done_ids)
        first_child = next(sub_iter, None)
        if first_child is not None:
            if "BATCH" in task.flag_tags:
                batch_children = _get_batch_children(task)
                if batch_children:
                    yield dataclasses.replace(task, children=batch_children)
                    # The BATCH parent is the unit; drain the remaining
                    # actionable children so the iterator state is clean,
                    # but do not yield them individually (the surfaced
                    # parent carries the batch).
                    for _ in sub_iter:
                        pass
                    continue
                yield first_child
                yield from sub_iter
            else:
                yield first_child
                yield from sub_iter
            continue

        # No actionable descendant. If any child is FAILED, the parent
        # cannot complete (parent state is derived from children, design
        # doc section 6 priority list item 4) and a FAILED child at the
        # subtask level blocks later siblings the same way a direct
        # FAILED sibling would.
        if any(c.status == TaskStatus.FAILED for c in task.children):
            if is_subtask:
                return
            continue
        yield task

def next_tasks(plan: Plan, *, limit: int = 1) -> list[Task]:
    """Return the next actionable tasks in ``plan``.

    Per design doc section 6. Priority and scoping:

    1. Tasks under ``## Bugs`` have absolute priority over phase tasks
       (design doc section 6 priority list item 1, and mcloop's
       ``find_next`` which probes ``_search_in_stage(tasks, "Bugs")``
       before falling through). When any bug task is actionable, the
       phase walk is not performed — even if ``limit`` is not yet
       reached. This keeps bugs from being mixed into the same result
       list as phase work; callers that finish bugs and then want
       phase work call ``next_tasks`` again.
    2. Within phase tasks, only tasks in the **first incomplete phase**
       (document order) are searchable. Later phases are invisible
       until the current phase is fully DONE (priority list item 2).
       A phase is "complete" when every task (root tasks plus every
       subsection task, recursively) has ``status == DONE``; FAILED
       tasks do not count as complete (mcloop ``_stage_complete``
       semantics: a stage with failed tasks is stuck, not done).

    Actionability and BATCH surfacing live in :func:`_walk_actionable`.

    ``limit`` caps the output length. ``limit <= 0`` returns ``[]``
    without searching the plan. The default of ``1`` matches the
    typical caller, which acts on the first actionable task and then
    re-reads PLAN.md between iterations.
    """
    if limit < 1:
        return []

    results: list[Task] = []
    done_ids = _build_done_ids(plan)

    if plan.bugs is not None:
        for task in _walk_actionable(
            plan.bugs.tasks, is_subtask=False, done_ids=done_ids
        ):
            results.append(task)
            if len(results) >= limit:
                return results
        if results:
            return results

    for phase in plan.phases:
        if _phase_complete(phase):
            continue
        for root_list in (phase.tasks, *(sub.tasks for sub in phase.subsections)):
            for task in _walk_actionable(
                root_list, is_subtask=False, done_ids=done_ids
            ):
                results.append(task)
                if len(results) >= limit:
                    return results
        return results
    return results
