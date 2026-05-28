"""Task status transitions (complete/fail/reset/clear/purge) and ledger consistency."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from bob_tools.planfile.canonical import (
    resolve_task_context,
)
from bob_tools.planfile.iteration import _find_task_by_id, _iter_plan_tasks
from bob_tools.planfile.model import (
    Outcome,
    Phase,
    Plan,
    PlanInconsistencyError,
    Settlement,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile.scheduling import _all_tasks_done

# Event-type → checkbox state implied when this is the most recent
# task-attributed lifecycle event for a task. Per design doc section 5
# kind policy: test_failed implies the task is currently FAILED;
# commit_landed and work_observed both imply the task is currently DONE
# (commit_landed is the commit-producing path, work_observed the
# AUTO/USER verification path). Other event types (phase lifecycle,
# findings, threshold crossings, etc.) do not carry a task-level
# state claim and are excluded.
_EVENT_TYPE_TO_EXPECTED_STATUS: dict[str, TaskStatus] = {
    "test_failed": TaskStatus.FAILED,
    "commit_landed": TaskStatus.DONE,
    "work_observed": TaskStatus.DONE,
}


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


def _clear_failed_in_tasks(tasks: tuple[Task, ...]) -> tuple[tuple[Task, ...], bool]:
    """Return ``tasks`` with every FAILED task reset to TODO."""
    new_tasks: list[Task] = []
    changed = False

    for task in tasks:
        new_children = task.children
        if task.children:
            new_children, children_changed = _clear_failed_in_tasks(task.children)
            changed = changed or children_changed

        new_status = (
            TaskStatus.TODO if task.status == TaskStatus.FAILED else task.status
        )
        if new_status != task.status or new_children != task.children:
            new_tasks.append(
                dataclasses.replace(task, status=new_status, children=new_children)
            )
            changed = True
        else:
            new_tasks.append(task)

    return tuple(new_tasks), changed


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


def clear_failed(plan: Plan) -> Plan:
    """Reset every FAILED task in ``plan`` to TODO and return a new plan.

    Mirrors mcloop's bulk ``clear_failed_markers`` retry behavior:
    every failed checkbox becomes unchecked, DONE and TODO tasks are
    untouched, no parent cascade is applied, and no ledger Settlement is
    produced. The operation does not require task ids, so it works on
    compat-mode plans as well as migrated plans.
    """
    new_phases: list[Phase] = []
    for phase in plan.phases:
        phase_tasks, phase_tasks_changed = _clear_failed_in_tasks(phase.tasks)
        new_subsections: list[Subsection] = []
        subsections_changed = False
        for subsection in phase.subsections:
            sub_tasks, sub_changed = _clear_failed_in_tasks(subsection.tasks)
            subsections_changed = subsections_changed or sub_changed
            if sub_changed:
                new_subsections.append(dataclasses.replace(subsection, tasks=sub_tasks))
            else:
                new_subsections.append(subsection)

        if phase_tasks_changed or subsections_changed:
            new_phases.append(
                dataclasses.replace(
                    phase,
                    tasks=phase_tasks,
                    subsections=tuple(new_subsections),
                )
            )
        else:
            new_phases.append(phase)

    new_bugs = plan.bugs
    if plan.bugs is not None:
        bug_tasks, bugs_changed = _clear_failed_in_tasks(plan.bugs.tasks)
        if bugs_changed:
            new_bugs = dataclasses.replace(plan.bugs, tasks=bug_tasks)

    return dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs)


def _purge_done_tasks(tasks: tuple[Task, ...]) -> tuple[tuple[Task, ...], bool]:
    """Return ``tasks`` with DONE tasks removed."""
    new_tasks: list[Task] = []
    changed = False

    for task in tasks:
        if task.status == TaskStatus.DONE:
            # A DONE parent with non-DONE children is unreachable in canonical
            # planfiles; if encountered, drop the subtree with the parent.
            changed = True
            continue

        new_children = task.children
        if task.children:
            new_children, children_changed = _purge_done_tasks(task.children)
            changed = changed or children_changed

        if new_children != task.children:
            new_tasks.append(dataclasses.replace(task, children=new_children))
        else:
            new_tasks.append(task)

    return tuple(new_tasks), changed


def purge_done_bug_tasks(plan: Plan) -> Plan:
    """Remove DONE tasks from ``plan.bugs`` and return a new plan.

    Mirrors mcloop's legacy ``purge_completed_bugs`` delete behavior for
    BUGS.md: checked bug entries are removed, phase tasks are untouched,
    and no ledger Settlement is produced.
    """
    new_bugs = plan.bugs
    if plan.bugs is not None:
        bug_tasks, bugs_changed = _purge_done_tasks(plan.bugs.tasks)
        if bugs_changed:
            new_bugs = dataclasses.replace(plan.bugs, tasks=bug_tasks)

    return dataclasses.replace(plan, bugs=new_bugs)


class _LedgerEvent(Protocol):
    """Structural type for events accepted by :func:`check_consistency`.

    Mirrors the shape of :class:`bob_tools.ledger.events.Event` (a frozen
    dataclass with ``event_id``, ``type``, and ``payload`` attributes)
    without importing it: per design doc section 3.1 the two libraries
    are peers and planfile must not depend on ledger. Any object whose
    attributes match this protocol — including the ledger's ``Event``
    dataclass and a thin test fixture — satisfies the contract.

    Attributes are declared as ``@property`` (read-only) so the
    matching is covariant: ``payload`` typed as ``dict[str, Any]`` on a
    concrete implementation satisfies ``Mapping[str, Any]`` here.
    ``type`` is returned as ``Any`` so both the ledger's ``EventType``
    StrEnum and a plain ``str`` (``"test_failed"``, ``"commit_landed"``)
    are accepted; the consistency check compares with ``==`` which works
    transparently for both because ``EventType`` is a ``StrEnum``.
    """

    @property
    def event_id(self) -> str: ...

    @property
    def type(self) -> Any: ...

    @property
    def payload(self) -> Mapping[str, Any]: ...


def _event_task_id(event_type: str, payload: Mapping[str, Any]) -> str | None:
    """Return the task_id referenced by ``event``, or ``None`` when absent.

    Field lookup per event type:

    - ``test_failed`` → ``payload["test_id"]``. Per design doc section
      7.2: "Task identity flows through ``test_failed.test_id`` (which
      is the task label, per ``emit_task_lifecycle_events``)." After
      McLoop's settle hook moves to :func:`resolve_task_context`, the
      same field carries a stable ``T-NNNNNN`` id.
    - ``commit_landed`` / ``work_observed`` → ``payload
      ["attributed_task_id"]``. Today's ledger schema (SCHEMA.md §
      ``commit_landed``) attributes commits only to a phase
      (``attributed_phase_id``); the design doc section 7.2 / section
      10 flags ``attributed_task_id`` as a future schema bump. Reading
      the field here is forward-compatible: payloads that omit it
      (the current case) return ``None`` and the event contributes no
      task-level claim.

    Returns ``None`` for any other event type — phase lifecycle events,
    findings, invariants, assumptions, decisions, and the reserved
    ``threshold_crossed`` / ``plan_reauthored`` types do not carry a
    per-task state claim.
    """
    if event_type == "test_failed":
        value = payload.get("test_id")
    elif event_type in ("commit_landed", "work_observed"):
        value = payload.get("attributed_task_id")
    else:
        return None
    return str(value) if value is not None else None


def check_consistency(plan: Plan, events: Iterable[_LedgerEvent]) -> None:
    """Compare checkbox state against the most recent lifecycle event per task.

    Per design doc section 5: PLAN.md is the writable surface, the
    ledger is the append-only witness, and the two are coupled at the
    settle call site rather than enforced as a bidirectional invariant.
    ``check_consistency`` is the after-the-fact reconciliation hook —
    it raises :class:`PlanInconsistencyError` when a task's current
    checkbox state contradicts the most recent task-attributed
    lifecycle event for that task, and stays silent on every other
    situation.

    Per-task attribution. Events without a resolvable task_id
    (:func:`_event_task_id` returns ``None``) are ignored: today
    ``commit_landed`` and ``work_observed`` attribute only to a phase
    (SCHEMA.md), so the function can only check tasks for which the
    ledger carries a task-level claim. Once ``attributed_task_id`` lands
    on those events (design doc section 10, future schema bump), the
    same check applies to commit/work events without further change.

    "Most recent" is decided by ``event.event_id``. Ledger event_ids are
    UUIDv7 (SCHEMA.md envelope) whose 48-bit time prefix gives
    lexicographic order that matches emission order across writers;
    breaking ties by the same field is acceptable because the projector
    uses the same key for replay ordering. ``ts`` is explicitly not
    used — SCHEMA.md forbids relying on ``ts`` for ordering.

    What gets flagged (contradictions):

    - Checkbox is ``DONE`` but the most recent event is
      ``test_failed`` — the ledger says the task failed and no later
      success event landed.
    - Checkbox is ``FAILED`` but the most recent event is
      ``commit_landed`` / ``work_observed`` — the ledger says the task
      succeeded and no later failure event landed.
    - Checkbox is ``TODO`` but the most recent event is
      ``commit_landed`` / ``work_observed`` — the ledger says the task
      succeeded but PLAN.md no longer reflects it (a checkoff that
      regressed back to unchecked is a contradiction, not a reset).

    What does NOT get flagged (intentional gaps, per design doc § 5):

    - Checkbox is ``TODO`` and the most recent event is
      ``test_failed``: per design doc section 5, "Resetting ``[!]`` to
      ``[ ]`` via retry → no ledger event; it is an operator decision
      to retry existing work, not evidence about the implementation."
      :func:`reset_task` emits a ``"none"``-kind Settlement with
      ``ledger_event_required=False`` for exactly this reason, so the
      checker must accept TODO after a test_failed as the expected
      shape of a reset.
    - Derived parent completion: a parent that auto-checks because all
      of its children are DONE does not produce a ledger event
      (:func:`complete_task` returns a ``"none"``-kind Settlement with
      ``ledger_event_required=False``). The parent therefore has no
      task-attributed event of its own; the checker sees no event for
      the parent and stays silent.
    - Tasks with no task-attributed events at all: the absence of
      evidence is not a contradiction. Compat-mode tasks (no stable
      id) fall here too — they cannot be matched by ``test_id`` /
      ``attributed_task_id`` so the checker has nothing to compare
      against.

    Every contradiction is collected into one
    :class:`PlanInconsistencyError`; the function does not short-
    circuit on the first failure so a single run surfaces every fix.
    Messages are sorted by ``task_id`` for deterministic output.
    """
    latest_by_task: dict[str, tuple[str, str]] = {}
    for event in events:
        event_type = str(event.type)
        task_id = _event_task_id(event_type, event.payload)
        if task_id is None:
            continue
        if event_type not in _EVENT_TYPE_TO_EXPECTED_STATUS:
            continue
        prior = latest_by_task.get(task_id)
        if prior is None or event.event_id > prior[0]:
            latest_by_task[task_id] = (event.event_id, event_type)

    messages: list[str] = []
    for task in _iter_plan_tasks(plan):
        if task.task_id is None:
            continue
        latest = latest_by_task.get(task.task_id)
        if latest is None:
            continue
        _event_id, event_type = latest
        expected = _EVENT_TYPE_TO_EXPECTED_STATUS[event_type]
        if task.status == expected:
            continue
        if event_type == "test_failed" and task.status == TaskStatus.TODO:
            continue
        messages.append(
            f"task {task.task_id} checkbox is {task.status.value} but "
            f"most recent lifecycle event is {event_type}"
        )

    if messages:
        messages.sort()
        raise PlanInconsistencyError(messages)
