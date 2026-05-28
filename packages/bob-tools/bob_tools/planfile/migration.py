"""Plan migration and phase replacement (``migrate``, ``replace_phase``, etc.)."""

from __future__ import annotations

import dataclasses

from bob_tools.planfile._shared import _PHASE_ID_RE, _TASK_ID_NUMERIC_RE
from bob_tools.planfile.iteration import (
    _iter_phase_task_tree_with_label,
    _iter_plan_tasks,
)
from bob_tools.planfile.model import (
    Phase,
    Plan,
    PlanValidationError,
    Task,
)
from bob_tools.planfile.validation import validate_plan


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


def replace_phase_validated(
    plan: Plan,
    phase_id: str,
    new_phase: Phase,
    *,
    assign_missing_ids: bool = True,
    preserve_position: bool = True,
) -> Plan:
    """Substitute ``phase_id`` with ``new_phase`` and enforce constructed validity.

    Per v4 Contract 3. Replaces :func:`replace_phase` for callers that
    need the substituted plan to satisfy the construction-API invariants
    end-to-end; structural validity only, lineage policy stays in duplo.

    Behavior:

    * **Exactly one match.** ``phase_id`` must resolve to exactly one
      phase in ``plan.phases``. Zero matches (unknown phase) or two-plus
      matches (duplicate-id input) raise :class:`PlanValidationError`
      before any substitution is attempted; the function never silently
      picks one of several duplicates.
    * **Missing phase id.** When ``new_phase.phase_id is None`` and
      ``assign_missing_ids`` is ``True`` the substitute is given a fresh
      ``phase_NNN`` id one above the maximum existing suffix in ``plan``
      (mirrors :func:`migrate`'s convention) and
      ``phase_id_source="explicit_comment"`` so the renderer emits a
      ``<!-- phase_id: ... -->`` comment.
    * **Missing task ids.** When ``assign_missing_ids`` is ``True``,
      tasks in ``new_phase`` (root and subsection, recursing through
      ``children``) whose ``task_id`` is ``None`` receive sequential
      global-unique ``T-NNNNNN`` ids starting at one above the maximum
      existing suffix in ``plan`` (:func:`_assign_task_ids` with a
      shared counter); existing ids are preserved.
    * **assign_missing_ids=False.** When the flag is ``False`` and any
      phase or task id on ``new_phase`` is missing, raise
      :class:`PlanValidationError` listing every offender. The caller
      is asserting "I provided all the ids myself; reject if any are
      missing."
    * **Ordinal normalization.** When ``preserve_position`` is ``True``
      (default) ``new_phase.ordinal`` is overwritten with the replaced
      phase's ordinal so the substitution keeps document order. When
      ``False`` the supplied ordinal is honored, and
      :func:`validate_plan` rejects the result if the ordinals are no
      longer contiguous ``1..N``.
    * **Final validation.** The substituted plan is passed through
      :func:`validate_plan` with ``constructed=True``, which enforces
      the v4 Contract 4 invariants (no duplicate phase or task ids,
      ids well-formed, scalars/deps/tags valid, ordinals contiguous,
      and full per-task / non-task scalar field-stability). Any
      violation propagates as :class:`PlanValidationError`.

    Other phases, the Bugs section, and the plan preamble are returned
    unchanged. :class:`PlanValidationError` is the only exception this
    function raises; callers do not need to catch :class:`ValueError`.
    """
    matched = [
        (index, phase)
        for index, phase in enumerate(plan.phases)
        if phase.phase_id == phase_id
    ]
    if not matched:
        raise PlanValidationError([f"phase {phase_id!r} not found in plan"])
    if len(matched) > 1:
        positions = [index for index, _ in matched]
        raise PlanValidationError(
            [f"phase {phase_id!r} matches multiple phases at positions {positions}"]
        )
    match_index, matched_phase = matched[0]

    if not assign_missing_ids:
        missing: list[str] = []
        if new_phase.phase_id is None:
            missing.append("new_phase.phase_id is missing")
        for label, node in _iter_phase_task_tree_with_label(new_phase):
            if node.task_id is None:
                missing.append(f"new_phase.{label}.task_id is missing")
        if missing:
            raise PlanValidationError(missing)

    candidate = new_phase

    if candidate.phase_id is None:
        next_phase_num = _max_phase_id_number(plan) + 1
        candidate = dataclasses.replace(
            candidate,
            phase_id=f"phase_{next_phase_num:03d}",
            phase_id_source="explicit_comment",
        )

    if assign_missing_ids:
        # ``_next_task_id_number`` reads only the supplied plan; the
        # counter must also clear ids already present inside
        # ``new_phase`` so an auto-assigned id never collides with a
        # caller-supplied id (e.g. plan max is 7 but ``new_phase``
        # already carries T-000020 — assignment must continue from 21,
        # not 8). Compose a pre-substitute plan with the candidate in
        # the target slot so the helper sees every retained id.
        phases_for_max = list(plan.phases)
        phases_for_max[match_index] = candidate
        plan_for_max = dataclasses.replace(plan, phases=tuple(phases_for_max))
        counter = [_next_task_id_number(plan_for_max)]
        namespace = plan.task_namespace
        new_root_tasks = _assign_task_ids(candidate.tasks, counter, namespace)
        new_subsections = tuple(
            dataclasses.replace(
                sub, tasks=_assign_task_ids(sub.tasks, counter, namespace)
            )
            for sub in candidate.subsections
        )
        candidate = dataclasses.replace(
            candidate, tasks=new_root_tasks, subsections=new_subsections
        )

    if preserve_position:
        candidate = dataclasses.replace(candidate, ordinal=matched_phase.ordinal)

    new_phases = list(plan.phases)
    new_phases[match_index] = candidate
    new_plan = dataclasses.replace(plan, phases=tuple(new_phases))

    validate_plan(new_plan, constructed=True)

    return new_plan


def _max_phase_id_number(plan: Plan) -> int:
    """Return the maximum numeric suffix used in any ``phase_NNN`` id.

    Scans every phase's ``phase_id`` and returns the largest digit run
    after the ``phase_`` prefix; returns ``0`` when no phase has a
    conforming id. Used by :func:`migrate` to assign ids to phases
    whose ``phase_id_source`` is ``"none"`` without colliding with ids
    already in use.
    """
    max_num = 0
    for phase in plan.phases:
        if phase.phase_id is None:
            continue
        match = _PHASE_ID_RE.match(phase.phase_id)
        if match is None:
            continue
        num = int(match.group(1))
        if num > max_num:
            max_num = num
    return max_num


def _assign_task_ids(
    tasks: tuple[Task, ...],
    counter: list[int],
    namespace: str | None = None,
) -> tuple[Task, ...]:
    """Return a copy of ``tasks`` with missing ``task_id`` fields assigned.

    ``counter`` is a single-element list holding the next id number to
    use; it is mutated in place so sibling subtrees share one running
    counter. ``namespace`` is the per-file ``task_namespace`` (or
    ``None``) so assigned ids match the plan's canonical form. Tasks
    that already have a ``task_id`` are returned with only their
    ``children`` re-walked (so a partially-migrated tree fills only
    the gaps); existing ids are not re-namespaced.
    """
    new_tasks: list[Task] = []
    for task in tasks:
        new_children = _assign_task_ids(task.children, counter, namespace)
        if task.task_id is None:
            if namespace is not None:
                new_id = f"T-{namespace}-{counter[0]:06d}"
            else:
                new_id = f"T-{counter[0]:06d}"
            counter[0] += 1
            new_tasks.append(
                dataclasses.replace(task, task_id=new_id, children=new_children)
            )
        else:
            new_tasks.append(dataclasses.replace(task, children=new_children))
    return tuple(new_tasks)


def migrate(plan: Plan) -> Plan:
    """Return ``plan`` with stable identifiers assigned to every task and phase.

    Per design doc section 3.2: identity migration is a one-shot
    transformation that fills in the structural information PLAN.md
    needs for deterministic addressing. ``canonicalize`` is the
    lossless formatter; ``migrate`` is the identity-mutating step.

    Rules:

    * Every existing ``T-NNNNNN`` is preserved unchanged. Tasks with no
      id receive sequential ids starting at ``max(existing) + 1`` (or
      ``T-000001`` when no task in the plan has a conforming id).
    * Every phase whose ``phase_id_source`` is ``"none"`` receives a
      synthesized ``phase_NNN`` id (sequential, starting at one greater
      than the maximum existing ``phase_NNN`` suffix) and has its
      ``phase_id_source`` set to ``"explicit_comment"`` so the
      renderer emits a ``<!-- phase_id: ... -->`` line.
    * Phases that already have an explicit id (any source other than
      ``"none"``) are left untouched.

    The operation is idempotent: ``migrate(migrate(plan))`` equals
    ``migrate(plan)``.
    """
    counter = [_next_task_id_number(plan)]
    namespace = plan.task_namespace
    new_phases: list[Phase] = []
    next_phase_num = _max_phase_id_number(plan) + 1
    for phase in plan.phases:
        new_root_tasks = _assign_task_ids(phase.tasks, counter, namespace)
        new_subsections = tuple(
            dataclasses.replace(
                sub, tasks=_assign_task_ids(sub.tasks, counter, namespace)
            )
            for sub in phase.subsections
        )
        if phase.phase_id_source == "none":
            new_phase_id = f"phase_{next_phase_num:03d}"
            next_phase_num += 1
            new_phases.append(
                dataclasses.replace(
                    phase,
                    phase_id=new_phase_id,
                    phase_id_source="explicit_comment",
                    tasks=new_root_tasks,
                    subsections=new_subsections,
                )
            )
        else:
            new_phases.append(
                dataclasses.replace(
                    phase, tasks=new_root_tasks, subsections=new_subsections
                )
            )

    new_bugs = plan.bugs
    if new_bugs is not None:
        new_bugs = dataclasses.replace(
            new_bugs, tasks=_assign_task_ids(new_bugs.tasks, counter, namespace)
        )

    return dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs)


def _next_task_id_number(plan: Plan) -> int:
    """Return the integer suffix to use for the next assigned task id.

    Equivalent to :func:`_next_task_id` but returns the integer rather
    than the formatted ``T-NNNNNN`` / ``T-XX-NNNNNN`` string; used by
    :func:`migrate` and :func:`_assign_task_ids`, which mutate a
    counter as they walk the tree and need the raw number. The regex
    accepts both legacy and namespaced ids so the counter advances
    past any existing canonical form.
    """
    max_num = 0
    for task in _iter_plan_tasks(plan):
        if task.task_id is None:
            continue
        match = _TASK_ID_NUMERIC_RE.match(task.task_id)
        if match is None:
            continue
        num = int(match.group(1))
        if num > max_num:
            max_num = num
    return max_num + 1
