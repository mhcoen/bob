"""Pure operations on typed Plan objects (validate, migrate, mutate, schedule)."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator

from bob_tools.planfile.model import (
    Outcome,
    Phase,
    Plan,
    PlanValidationError,
    Settlement,
    Subsection,
    Task,
    TaskContext,
    TaskStatus,
)

_POSITIONAL_LABEL_RE = re.compile(r"^\d+(?:\.\d+)+$")


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


def _resolve_positional_label(plan: Plan, label: str) -> tuple[Phase, int, Task] | None:
    """Resolve a positional label like ``1.3.2`` to ``(phase, doc_index, task)``.

    Mirrors mcloop's ``task_label`` output (``checklist.py``): the first
    dot-separated token is the phase ordinal as printed in the
    ``Stage N`` / ``Phase N`` heading (i.e. ``Phase.ordinal``); each
    subsequent token is a 1-based positional index into the task tree.
    The second token picks a root task from ``phase.tasks``, the third
    picks a child of that task, and so on. Subsection tasks are
    intentionally not addressable by positional label — they sit under
    a sub-heading whose stage number is empty in mcloop, so mcloop never
    produces ``N.M`` labels for them. Mirroring that here keeps the
    resolver consistent with the strings mcloop actually emits.

    ``doc_index`` is the 1-based position of the matched phase within
    ``plan.phases``, surfaced so :func:`resolve_task_context` can
    synthesize an ordinal-fallback phase_id when the matched phase has
    ``phase_id_source == "none"`` (design doc section 7.1). The
    distinction matters: ``Phase.ordinal`` comes from the heading text
    (``Stage 5`` → ``ordinal == 5``), but a plan whose first phase is
    ``Stage 5`` still has that phase at ``doc_index == 1``.

    Returns ``None`` when ``label`` is not in ``N.M[.K...]`` form (the
    pattern is anchored — at least two dot-separated all-numeric
    tokens), when no phase has the requested ordinal, or when any
    positional index along the path is out of range. Bare ``N`` (no
    dots) is intentionally not a positional label: it would be too
    easy to collide with an unrelated single-digit literal in
    ``task.text``, and mcloop's ``task_label`` always emits at least
    a stage-plus-position pair for stage-headed sections.

    Tokenization is explicit: ``label.split(".")`` and integer
    conversion under the anchored regex above. The resolver MUST NOT
    fall back to ``ref in task.text``-style substring scanning — that
    is the bug the design doc section 7.2 caveat calls out and the
    reason this helper exists.
    """
    if _POSITIONAL_LABEL_RE.match(label) is None:
        return None
    parts = [int(token) for token in label.split(".")]
    phase_ordinal, *task_indexes = parts

    matched_phase: Phase | None = None
    matched_doc_index: int = 0
    for doc_index, phase in enumerate(plan.phases, start=1):
        if phase.ordinal == phase_ordinal:
            matched_phase = phase
            matched_doc_index = doc_index
            break
    if matched_phase is None:
        return None

    current_tasks: tuple[Task, ...] = matched_phase.tasks
    matched_task: Task | None = None
    for index in task_indexes:
        if index < 1 or index > len(current_tasks):
            return None
        matched_task = current_tasks[index - 1]
        current_tasks = matched_task.children

    if matched_task is None:
        return None
    return matched_phase, matched_doc_index, matched_task


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


def _phase_id_for_task_context(phase: Phase, doc_index: int) -> tuple[str | None, str]:
    """Return the ``(phase_id, phase_id_source)`` pair for a TaskContext.

    Pass-through when the phase already has an explicit identifier
    (``"explicit_comment"`` or ``"explicit_header"``). When the
    containing phase has ``phase_id_source == "none"`` the function
    synthesizes the ordinal-derived id ``phase_{doc_index:03d}`` and
    reports ``phase_id_source == "ordinal"``, per design doc section
    7.1 ("Ordinal fallback. The n-th phase heading in document order")
    and section 2.4 (the explicit-required / ordinal-degraded
    contract). The synthesis lets callers proceed with a usable
    phase_id rather than having to thread a separate
    ``ordinal_index`` argument through the resolver, which is exactly
    the simplification that section 7.1's shim sketch is aiming at.
    """
    if phase.phase_id_source == "none":
        return f"phase_{doc_index:03d}", "ordinal"
    return phase.phase_id, phase.phase_id_source


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

    When the matched task's containing phase has ``phase_id_source ==
    "none"`` (no ``<!-- phase_id: ... -->`` comment and no legacy
    ``## Phase phase_NNN: ...`` header), the resolver synthesizes an
    ordinal-derived ``phase_NNN`` id from the phase's 1-based position
    in ``plan.phases`` and reports ``phase_id_source == "ordinal"``.
    This implements the ordinal-fallback half of the explicit-required
    / ordinal-degraded contract inside the library, per design doc
    sections 2.4 and 7.1, so the ledger_emit shim does not need a
    separate ``ordinal_index`` pass over the plan to recover a usable
    phase_id. Bug-section tasks and unresolved references stay
    ``phase_id_source == "none"`` — they are not contained in a phase,
    so there is no ordinal to attribute to.

    ``plan_phase_count`` is always populated from ``len(plan.phases)``
    so the ordinal-fallback shim has the count it needs without a
    second pass over the plan.

    Positional labels (``N.M[.K...]``, as emitted by mcloop's
    ``task_label``) are tried first and bypass the task-walk
    entirely: tokenized index lookup is unambiguous, so a
    well-formed positional reference cannot be confused with a
    ``task_id`` or with task text — see :func:`_resolve_positional_label`
    for the contract. When the format does not match, when no phase
    has the requested ordinal, or when an index along the path is out
    of range, positional resolution returns ``None`` and the walk
    falls through to ``task_id`` / text matching.
    """
    plan_phase_count = len(plan.phases)

    positional = _resolve_positional_label(plan, task_label_or_id)
    if positional is not None:
        phase, doc_index, task = positional
        phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
        return TaskContext(
            task_id=task.task_id,
            phase_id=phase_id,
            phase_id_source=phase_id_source,
            label=task_label_or_id,
            plan_phase_count=plan_phase_count,
        )

    for task, phase, doc_index in _iter_phase_tasks_with_phase(plan):
        if _task_matches_label(task, task_label_or_id):
            phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
            return TaskContext(
                task_id=task.task_id,
                phase_id=phase_id,
                phase_id_source=phase_id_source,
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


def _surface_batch_parent(parent: Task) -> Task:
    """Return a copy of ``parent`` whose ``children`` is the batch unit.

    Per design doc section 6: a ``[BATCH]`` parent surfaces as one unit
    with its actionable children joined. The surfaced Task carries the
    same id, text, status, tags, deps, and source position as the
    parent — only ``children`` is replaced with the result of
    :func:`_get_batch_children` so callers receive the batchable
    children directly without recomputing them.
    """
    return dataclasses.replace(parent, children=_get_batch_children(parent))


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
                yield _surface_batch_parent(task)
                # The BATCH parent is the unit; drain the remaining
                # actionable children so the iterator state is clean,
                # but do not yield them individually (the surfaced
                # parent carries the batch).
                for _ in sub_iter:
                    pass
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


def _flip_in_tree(
    tasks: tuple[Task, ...],
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[tuple[Task, ...], list[Task], bool]:
    """Apply ``new_status`` to ``task_id`` inside ``tasks``; return new tree.

    Returns ``(new_tasks, newly_completed_ancestors, found)``.

    When ``cascade`` is True and the change flips the target to DONE, any
    ancestor whose children become all-DONE is also flipped to DONE and
    appended to ``newly_completed_ancestors`` in innermost-first order
    (the leaf's parent precedes the grandparent). When ``cascade`` is
    False, ancestor statuses are left untouched (used by fail_task and
    reset_task: failing or un-failing a child must not auto-complete
    ancestors, per design doc section 5).

    An ancestor whose ``status`` is already DONE is not re-appended even
    if its children are all DONE after the flip. The cascade fires on a
    transition from not-DONE to DONE; idempotently completing a task
    whose parents were already DONE produces no additional derived
    Settlements.
    """
    new_list: list[Task] = []
    found = False
    ancestors: list[Task] = []

    for task in tasks:
        if found:
            new_list.append(task)
            continue
        if task.task_id is not None and task.task_id == task_id:
            new_list.append(dataclasses.replace(task, status=new_status))
            found = True
            continue
        if task.children:
            new_children, child_ancestors, child_found = _flip_in_tree(
                task.children, task_id, new_status, cascade=cascade
            )
            if child_found:
                found = True
                ancestors.extend(child_ancestors)
                if (
                    cascade
                    and new_status == TaskStatus.DONE
                    and task.status != TaskStatus.DONE
                    and _all_tasks_done(new_children)
                ):
                    new_parent = dataclasses.replace(
                        task, children=new_children, status=TaskStatus.DONE
                    )
                    new_list.append(new_parent)
                    ancestors.append(new_parent)
                else:
                    new_list.append(dataclasses.replace(task, children=new_children))
                continue
        new_list.append(task)

    return tuple(new_list), ancestors, found


def _apply_to_phase(
    phase: Phase,
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[Phase, list[Task], bool]:
    """Walk ``phase``'s root tasks then subsection tasks. Returns rebuilt phase."""
    new_tasks, ancestors, found = _flip_in_tree(
        phase.tasks, task_id, new_status, cascade=cascade
    )
    if found:
        return dataclasses.replace(phase, tasks=new_tasks), ancestors, True

    new_subs: list[Subsection] = []
    for sub in phase.subsections:
        if found:
            new_subs.append(sub)
            continue
        sub_tasks, sub_ancestors, sub_found = _flip_in_tree(
            sub.tasks, task_id, new_status, cascade=cascade
        )
        if sub_found:
            found = True
            ancestors.extend(sub_ancestors)
            new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
        else:
            new_subs.append(sub)

    if found:
        return (
            dataclasses.replace(phase, subsections=tuple(new_subs)),
            ancestors,
            True,
        )
    return phase, [], False


def _apply_status_to_plan(
    plan: Plan,
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[Plan, list[Task]]:
    """Return ``(new_plan, newly_completed_ancestors)`` after the flip.

    Walks phases (each phase's root tasks then its subsections) before
    falling through to the Bugs section. The first match wins; subsequent
    sections are returned untouched. Raises :class:`ValueError` if no
    task in the plan has ``task_id``.
    """
    new_phases: list[Phase] = []
    found = False
    ancestors: list[Task] = []

    for phase in plan.phases:
        if found:
            new_phases.append(phase)
            continue
        new_phase, phase_ancestors, phase_found = _apply_to_phase(
            phase, task_id, new_status, cascade=cascade
        )
        if phase_found:
            found = True
            ancestors.extend(phase_ancestors)
            new_phases.append(new_phase)
        else:
            new_phases.append(phase)

    new_bugs = plan.bugs
    if not found and plan.bugs is not None:
        bug_tasks, bug_ancestors, bug_found = _flip_in_tree(
            plan.bugs.tasks, task_id, new_status, cascade=cascade
        )
        if bug_found:
            found = True
            ancestors.extend(bug_ancestors)
            new_bugs = dataclasses.replace(plan.bugs, tasks=bug_tasks)

    if not found:
        raise ValueError(f"task {task_id!r} not found in plan")

    return (
        dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs),
        ancestors,
    )


def _direct_completion_kind(task: Task) -> str:
    """Pick the Settlement kind for a directly-completed task.

    Per design doc section 5 kind policy:

    - AUTO action tasks (``task.action_tag is not None``) and USER tasks
      (``"USER" in task.flag_tags``) settle as ``work_observed``: the
      task completed but did not produce a commit, so callers record the
      work observation without expecting a new commit on disk.
    - Every other task (the common case) settles as ``commit_landed``:
      the task produced a commit and the ledger emits the commit event.
    """
    if task.action_tag is not None or "USER" in task.flag_tags:
        return "work_observed"
    return "commit_landed"


def _settlement_phase_id(plan: Plan, task_id: str | None) -> str | None:
    """Return the resolved phase_id for ``task_id`` in ``plan``, or None.

    Thin wrapper around :func:`resolve_task_context` so Settlement
    construction does not repeat the ``task_id is None`` guard at every
    call site. Bug tasks resolve with ``phase_id=None``; unresolved
    references also return ``None``.
    """
    if task_id is None:
        return None
    return resolve_task_context(plan, task_id).phase_id


def complete_task(
    plan: Plan, task_id: str, outcome: Outcome | None = None
) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` to DONE and return the new plan and its settlements.

    The first Settlement in the returned tuple is the **direct** result
    for ``task_id`` (kind picked by :func:`_direct_completion_kind`,
    always ``ledger_event_required=True``). When the flip causes one or
    more ancestor tasks to become complete because all of their
    children are now DONE, each newly-completed ancestor is appended as
    a **derived** Settlement with ``kind="none"`` and
    ``ledger_event_required=False``. Per design doc section 5 the
    derived sequence is innermost-outward: the leaf's immediate parent
    first, the grandparent next, and so on.

    Idempotency: completing a task whose ancestors were already DONE
    produces no derived Settlements (the cascade fires only on a
    transition into DONE).

    ``outcome`` is accepted for API symmetry with :func:`fail_task` and
    is currently unused by ``complete_task``. The signature is pinned
    by the design doc; future consumers can attach commit metadata
    without an API break.
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")
    _ = outcome  # Accepted for API symmetry; not currently consumed.

    new_plan, ancestors = _apply_status_to_plan(
        plan, task_id, TaskStatus.DONE, cascade=True
    )

    direct = Settlement(
        kind=_direct_completion_kind(original),  # type: ignore[arg-type]
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=original.text,
        failure_kind=None,
        ledger_event_required=True,
    )
    derived = tuple(
        Settlement(
            kind="none",
            task_id=ancestor.task_id,
            phase_id=_settlement_phase_id(new_plan, ancestor.task_id),
            summary=ancestor.text,
            failure_kind=None,
            ledger_event_required=False,
        )
        for ancestor in ancestors
    )
    return new_plan, (direct, *derived)


def fail_task(
    plan: Plan,
    task_id: str,
    reason: str,
    outcome: Outcome | None = None,
) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` to FAILED and return a single ``test_failed`` Settlement.

    ``reason`` becomes the Settlement's ``summary`` (a human-readable
    description of what failed). ``failure_kind`` comes from
    ``outcome.failure_kind`` when an :class:`Outcome` is supplied with a
    non-empty value; otherwise defaults to ``"max_retries_exceeded"``
    per the task contract.

    Failing a task does not auto-complete ancestors — a failure inside
    a parent task leaves the parent unchecked (and stuck), per design
    doc section 5 and mcloop's behavior. The returned tuple therefore
    contains exactly one Settlement.
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")

    new_plan, _ = _apply_status_to_plan(plan, task_id, TaskStatus.FAILED, cascade=False)

    failure_kind = outcome.failure_kind if outcome is not None else None
    if not failure_kind:
        failure_kind = "max_retries_exceeded"

    settlement = Settlement(
        kind="test_failed",
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=reason,
        failure_kind=failure_kind,
        ledger_event_required=True,
    )
    return new_plan, (settlement,)


def reset_task(plan: Plan, task_id: str) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` back to TODO and return a ``none``-kind Settlement.

    Mirrors mcloop's ``clear_failed_markers``: a FAILED checkbox is
    rewritten as TODO so the task is re-tried. Per design doc section
    5, reset is an operator decision about retry, not new evidence
    about implementation — the Settlement therefore has ``kind="none"``
    and ``ledger_event_required=False``. The operation is idempotent on
    non-FAILED tasks (TODO stays TODO, DONE is rewritten to TODO if the
    caller asks, with no error) so callers can apply it blindly to
    "clear any failed marker on this task".
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")

    new_plan, _ = _apply_status_to_plan(plan, task_id, TaskStatus.TODO, cascade=False)

    settlement = Settlement(
        kind="none",
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=original.text,
        failure_kind=None,
        ledger_event_required=False,
    )
    return new_plan, (settlement,)


def _next_task_id(plan: Plan) -> str:
    """Return the next sequential ``T-NNNNNN`` id not yet used in ``plan``.

    Scans every task in the plan, takes the maximum numeric suffix on
    ids that match ``T-`` + digits, and returns ``T-`` formatted to six
    digits at ``max + 1``. Tasks without an id, or with non-conforming
    ids, are ignored. The scan covers phase tasks (with subsection
    descent) and bug tasks via :func:`_iter_plan_tasks`, so the
    returned id is globally unique within ``plan`` per design doc
    section 11 question 1 (global default).
    """
    max_num = 0
    for task in _iter_plan_tasks(plan):
        if task.task_id is None or not task.task_id.startswith("T-"):
            continue
        suffix = task.task_id[2:]
        if not suffix.isdigit():
            continue
        num = int(suffix)
        if num > max_num:
            max_num = num
    return f"T-{max_num + 1:06d}"


def _try_add_under_parent(
    tasks: tuple[Task, ...], parent_id: str, new_task: Task
) -> tuple[tuple[Task, ...], bool]:
    """Append ``new_task`` to the children of the task whose id == parent_id.

    Returns ``(new_tasks, found)`` so callers can fall through to a
    sibling list (phase root tasks → subsection tasks) when the parent
    is not in this list. Walks recursively so a nested parent matches.
    """
    new_list: list[Task] = []
    found = False
    for task in tasks:
        if found:
            new_list.append(task)
            continue
        if task.task_id is not None and task.task_id == parent_id:
            new_list.append(
                dataclasses.replace(task, children=(*task.children, new_task))
            )
            found = True
            continue
        if task.children:
            new_children, child_found = _try_add_under_parent(
                task.children, parent_id, new_task
            )
            if child_found:
                new_list.append(dataclasses.replace(task, children=new_children))
                found = True
                continue
        new_list.append(task)
    return tuple(new_list), found


def add_task(
    plan: Plan,
    phase_id: str,
    text: str,
    *,
    deps: tuple[str, ...] = (),
    parent_id: str | None = None,
) -> Plan:
    """Append a new TODO task to ``phase_id`` and return the new plan.

    The new task gets the next sequential globally-unique ``T-NNNNNN``
    id (:func:`_next_task_id`). When ``parent_id`` is supplied the task
    is nested under that task's children, searching first the phase's
    root tasks and then each subsection's tasks. When ``parent_id`` is
    ``None`` the task is appended to the phase's root task list.

    Raises :class:`ValueError` when ``phase_id`` does not match any
    phase, or when ``parent_id`` is supplied but does not match any
    task inside the named phase.
    """
    new_id = _next_task_id(plan)
    new_task = Task(
        task_id=new_id,
        text=text,
        status=TaskStatus.TODO,
        flag_tags=(),
        action_tag=None,
        annotations=(),
        deps=tuple(deps),
        children=(),
        ruled_out=(),
        indent_level=0,
        line_number=0,
    )

    new_phases: list[Phase] = []
    phase_found = False
    for phase in plan.phases:
        if phase_found or phase.phase_id != phase_id:
            new_phases.append(phase)
            continue
        phase_found = True
        if parent_id is None:
            new_phases.append(
                dataclasses.replace(phase, tasks=(*phase.tasks, new_task))
            )
            continue
        updated_root, added = _try_add_under_parent(phase.tasks, parent_id, new_task)
        if added:
            new_phases.append(dataclasses.replace(phase, tasks=updated_root))
            continue
        new_subs: list[Subsection] = []
        sub_added = False
        for sub in phase.subsections:
            if sub_added:
                new_subs.append(sub)
                continue
            sub_tasks, this_added = _try_add_under_parent(
                sub.tasks, parent_id, new_task
            )
            if this_added:
                sub_added = True
                new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
            else:
                new_subs.append(sub)
        if not sub_added:
            raise ValueError(
                f"parent task {parent_id!r} not found in phase {phase_id!r}"
            )
        new_phases.append(dataclasses.replace(phase, subsections=tuple(new_subs)))

    if not phase_found:
        raise ValueError(f"phase {phase_id!r} not found in plan")

    return dataclasses.replace(plan, phases=tuple(new_phases))


def replace_phase(plan: Plan, phase_id: str, new_phase: Phase) -> Plan:
    """Substitute the phase whose ``phase_id`` == ``phase_id`` with ``new_phase``.

    Wholesale replacement: ``new_phase`` is inserted at the matched
    phase's position in ``plan.phases`` with no field-level merge.
    Used by Duplo on phase reauthor where the entire phase content has
    been regenerated; the caller is responsible for the new phase's
    contents (including its ``phase_id``, which may differ from the
    lookup key when the reauthor renamed the phase).

    Raises :class:`ValueError` when no phase matches ``phase_id``.
    Other phases are returned unchanged, including the Bugs section
    and the plan's preamble.
    """
    new_phases: list[Phase] = []
    found = False
    for phase in plan.phases:
        if not found and phase.phase_id == phase_id:
            new_phases.append(new_phase)
            found = True
        else:
            new_phases.append(phase)
    if not found:
        raise ValueError(f"phase {phase_id!r} not found in plan")
    return dataclasses.replace(plan, phases=tuple(new_phases))
