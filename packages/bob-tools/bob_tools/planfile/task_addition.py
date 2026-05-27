"""Task addition: ``add_task``, ``add_phase_task``, ``add_bug_task`` and helpers."""

from __future__ import annotations

import dataclasses
import warnings

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanValidationError,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile._shared import _TASK_ID_NUMERIC_RE, _now_iso_utc
from bob_tools.planfile.iteration import _iter_plan_tasks, _iter_tasks
from bob_tools.planfile.construction import (
    _assert_task_field_stability,
    _explicit_task_ids,
    make_task,
)
from bob_tools.planfile.validation import validate_plan

def _next_task_id(plan: Plan) -> str:
    """Return the next sequential canonical task id not yet used in ``plan``.

    Scans every task in the plan, takes the maximum numeric suffix on
    ids that match ``T-(XX-)?`` + digits, and formats the result with
    the plan's declared ``task_namespace`` prefix when set or as bare
    ``T-NNNNNN`` otherwise. Tasks without an id, or with non-conforming
    ids, are ignored. The scan covers phase tasks (with subsection
    descent) and bug tasks via :func:`_iter_plan_tasks`, so the
    returned id is globally unique within ``plan`` per design doc
    section 11 question 1 (global default).
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
    if plan.task_namespace is not None:
        return f"T-{plan.task_namespace}-{max_num + 1:06d}"
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
    id (:func:`_next_task_id`) and a ``created_at`` timestamp set to
    the current UTC instant in ISO 8601 form (:func:`_now_iso_utc`).
    When ``parent_id`` is supplied the task is nested under that
    task's children, searching first the phase's root tasks and then
    each subsection's tasks. When ``parent_id`` is ``None`` the task
    is appended to the phase's root task list.

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
        created_at=_now_iso_utc(),
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

def add_phase_task(
    plan: Plan,
    phase_id: str,
    task: Task,
    *,
    parent_id: str | None = None,
    subsection_title: str | None = None,
) -> tuple[Plan, str]:
    """Append a Stage-10 field-stable task into ``phase_id``.

    Per v4 Contract 6. Returns ``(new_plan, assigned_id)`` where
    ``assigned_id`` is the ``T-NNNNNN`` carried by the appended task
    (the caller-supplied id when ``task.task_id`` is non-None, otherwise
    the next globally-unique sequential id assigned by
    :func:`_next_task_id`). A caller-supplied ``task.created_at`` is
    honored verbatim; when the supplied task has ``created_at=None``
    the appended task is stamped with the current UTC instant in
    ISO 8601 form via :func:`_now_iso_utc`.

    Placement is selected by the keyword arguments:

    * Both ``parent_id`` and ``subsection_title`` ``None`` (default) —
      the task is appended to ``phase.tasks`` at the root of the named
      phase.
    * ``parent_id`` set — the task is nested under the task whose
      ``task_id`` equals ``parent_id``. Searches the phase's root tasks
      first, then each subsection's tasks (recursing through children),
      matching the convention :func:`add_task` already uses; the first
      match wins. The look-up is scoped to the named phase so a stray
      id elsewhere in the plan does not silently capture the insert.
    * ``subsection_title`` set — the task is appended at the root of
      the subsection whose ``title`` equals ``subsection_title``
      within the named phase.

    ``parent_id`` and ``subsection_title`` are mutually exclusive
    (Contract 6 enumerates the three placements as alternatives, not
    combinations); passing both raises :class:`PlanValidationError`.

    Validation is layered to surface the same diagnostics callers see
    elsewhere in the library. First the supplied ``task`` is run
    through the Stage 10 field-stability harness
    (:func:`_assert_task_field_stability`) so hand-constructed tasks
    cannot bypass the grammar contract that :func:`make_task` enforces.
    Then the placement keywords are resolved, the new task is spliced
    in with its id (assigned or honored), and the resulting plan is
    passed through :func:`validate_plan` with ``constructed=True``,
    which catches duplicate task ids and unknown ``@deps`` references.
    :class:`PlanValidationError` is the only exception this function
    raises; callers do not need to catch :class:`ValueError`.
    """
    _assert_task_field_stability(task)

    if parent_id is not None and subsection_title is not None:
        raise PlanValidationError(
            ["parent_id and subsection_title are mutually exclusive"]
        )

    phase_index = -1
    for index, phase in enumerate(plan.phases):
        if phase.phase_id == phase_id:
            phase_index = index
            break
    if phase_index == -1:
        raise PlanValidationError([f"phase {phase_id!r} not found in plan"])

    target_phase = plan.phases[phase_index]

    assigned_id = task.task_id if task.task_id is not None else _next_task_id(plan)
    assigned_created_at = (
        task.created_at if task.created_at is not None else _now_iso_utc()
    )
    appended = dataclasses.replace(
        task, task_id=assigned_id, created_at=assigned_created_at
    )

    if parent_id is not None:
        updated_root, root_added = _try_add_under_parent(
            target_phase.tasks, parent_id, appended
        )
        if root_added:
            new_phase = dataclasses.replace(target_phase, tasks=updated_root)
        else:
            new_subs: list[Subsection] = []
            sub_added = False
            for sub in target_phase.subsections:
                if sub_added:
                    new_subs.append(sub)
                    continue
                sub_tasks, this_added = _try_add_under_parent(
                    sub.tasks, parent_id, appended
                )
                if this_added:
                    sub_added = True
                    new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
                else:
                    new_subs.append(sub)
            if not sub_added:
                raise PlanValidationError(
                    [f"parent task {parent_id!r} not found in phase {phase_id!r}"]
                )
            new_phase = dataclasses.replace(target_phase, subsections=tuple(new_subs))
    elif subsection_title is not None:
        new_subs = []
        sub_found = False
        for sub in target_phase.subsections:
            if not sub_found and sub.title == subsection_title:
                sub_found = True
                new_subs.append(dataclasses.replace(sub, tasks=(*sub.tasks, appended)))
            else:
                new_subs.append(sub)
        if not sub_found:
            raise PlanValidationError(
                [f"subsection {subsection_title!r} not found in phase {phase_id!r}"]
            )
        new_phase = dataclasses.replace(target_phase, subsections=tuple(new_subs))
    else:
        new_phase = dataclasses.replace(
            target_phase, tasks=(*target_phase.tasks, appended)
        )

    new_phases = list(plan.phases)
    new_phases[phase_index] = new_phase
    new_plan = dataclasses.replace(plan, phases=tuple(new_phases))

    validate_plan(new_plan, constructed=True)

    return new_plan, assigned_id

def _normalize_bug_text(text: str) -> str:
    """Return ``text`` normalized for bug-dedup comparison.

    Strips leading/trailing whitespace and collapses every run of
    interior whitespace into a single space. The normalization is
    deliberately conservative: it absorbs incidental whitespace
    differences (a model re-emit with an extra space; copy-paste that
    introduced a leading newline) without merging tasks whose text
    differs in any non-whitespace character. ``"a b"``, ``"a  b"``,
    and ``"\\ta b\\n"`` all normalize to the same key; ``"a B"`` does
    not.
    """
    return " ".join(text.split())

def _bug_dedup_keys(task: Task, *, explicit: tuple[str, ...] = ()) -> set[str]:
    """Return the dedup-key set for ``task`` per v4 Contract 2.

    The set is the union of three sources, in the order the spec
    lists them: caller-supplied ``explicit`` keys, the value of every
    ``fix`` annotation on the task, then the task's text after
    :func:`_normalize_bug_text`. The ``fix`` annotation is the one
    Duplo's investigator emits so a regenerated bug task whose text
    drifted slightly still collapses against the same underlying
    issue (saver.py:1291 in duplo).
    """
    keys: set[str] = set(explicit)
    for key, value in task.annotations:
        if key == "fix":
            keys.add(value)
    keys.add(_normalize_bug_text(task.text))
    return keys

def add_bug_task(
    plan: Plan,
    task: Task,
    *,
    dedup_keys: tuple[str, ...] = (),
) -> tuple[Plan, str]:
    """Append ``task`` to the Bugs section with reopen-in-place semantics.

    Per v4 Contract 2. Returns ``(new_plan, outcome)`` where ``outcome``
    is one of ``"appended"``, ``"reopened"``, or ``"unchanged"``.

    ``task`` must already satisfy Stage 10 task field-stability
    (typically constructed via :func:`make_task`); the harness runs
    here too so a hand-built :class:`Task` cannot bypass the grammar
    contract. Failure raises :class:`PlanValidationError` naming the
    field that did not round-trip. Per the contract,
    :class:`PlanValidationError` is the only exception this function
    raises.

    Behavior:

    * **Bugs section creation.** When ``plan.bugs is None`` and the
      task is appended (no match found), a fresh :class:`BugsSection`
      with this one task is attached to the plan. An existing empty
      :class:`BugsSection` (parsed but with no tasks) is preserved
      and the task is appended to it.
    * **Root bug tasks only.** The supplied task is inserted at the
      Bugs root level. Any ``children`` on the task are preserved
      unchanged, but the task itself is never nested under an existing
      bug.
    * **Dedup match.** The incoming task's dedup-key set
      (:func:`_bug_dedup_keys` with the caller's ``dedup_keys``) is
      compared against each existing root bug task's dedup-key set
      (no explicit keys; just ``fix`` annotations and normalized
      text). The first existing task whose key set intersects the
      incoming key set is the match; subsequent matches are ignored
      (the "earliest match" rule).
    * **Outcome by match status:**

      - Match is TODO → return ``"unchanged"``. The plan is returned
        as-is; no field on any task changes. The incoming task is
        discarded — the bug is already open.
      - Match is DONE or FAILED → reopen the earliest match in place:
        flip ``status`` to TODO. ``task_id``, ``children``,
        ``annotations``, ``deps``, ``ruled_out``, and the task's
        position in ``plan.bugs.tasks`` are preserved. Return
        ``"reopened"``.
      - No match → append. ``status`` is forced to TODO regardless of
        the incoming task's status (an incoming DONE/FAILED was
        meaningless for a newly-tracked bug). When ``task.task_id is
        None`` the next globally-unique ``T-NNNNNN`` id is assigned
        via :func:`_next_task_id`; a caller-supplied id is honored
        verbatim (collision checking belongs to
        :func:`validate_plan`). Return ``"appended"``.
    """
    _assert_task_field_stability(task)

    incoming_keys = _bug_dedup_keys(task, explicit=dedup_keys)
    existing_bugs: tuple[Task, ...] = plan.bugs.tasks if plan.bugs is not None else ()

    for index, existing in enumerate(existing_bugs):
        if not (incoming_keys & _bug_dedup_keys(existing)):
            continue
        if existing.status == TaskStatus.TODO:
            return plan, "unchanged"
        reopened = dataclasses.replace(existing, status=TaskStatus.TODO)
        new_tasks = (
            *existing_bugs[:index],
            reopened,
            *existing_bugs[index + 1 :],
        )
        # ``existing_bugs`` is non-empty here, so ``plan.bugs`` is set;
        # narrow for the type checker.
        assert plan.bugs is not None
        new_bugs = dataclasses.replace(plan.bugs, tasks=new_tasks)
        return dataclasses.replace(plan, bugs=new_bugs), "reopened"

    assigned_id = task.task_id if task.task_id is not None else _next_task_id(plan)
    appended = dataclasses.replace(task, task_id=assigned_id, status=TaskStatus.TODO)
    if plan.bugs is None:
        new_bugs = BugsSection(tasks=(appended,), line_number=0)
    else:
        new_bugs = dataclasses.replace(plan.bugs, tasks=(*plan.bugs.tasks, appended))
    return dataclasses.replace(plan, bugs=new_bugs), "appended"
