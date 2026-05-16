"""Plan Ledger threshold evaluator.

Pure on-demand classifier. Given a ``PlanState`` and the events that
produced it, returns the list of thresholds that have been crossed
since an opaque cursor (``since``). Slice B does not write back to
the ledger and does not auto-trigger re-authoring; that is Slice B
part 2 / Slice C.

Seven rules ship in Slice B at ``severity=trigger_reauthor``:

  unattributable_commit       commit_landed with no attributed phase
  phase_abandoned             a phase_abandoned event
  phase_superseded            a phase_superseded event
  phase_topology_changed      phase_split or phase_merged
  invariant_declared          an invariant_declared event
  assumption_falsified        an assumption_falsified event
  exploratory_count_exceeded  count of unattributed non-plan-artifact
                              commits crosses a configurable limit

Determinism contract (encoded in tests/test_thresholds.py):

  - Same crossings regardless of input event order. Sort key:
    ``event_id``.
  - Independent of ``ts``. The evaluator never reads ``ts``.
  - Shuffle-invariant on the projector composition:
    ``evaluate_thresholds(project(shuffle), shuffle)`` ==
    ``evaluate_thresholds(project(sorted), sorted)``.
  - Returned list ordered by ``(detected_at_event_id, rule_id)``.

``since`` semantics (load-bearing):

  Only crossings whose evidence has ``event_id > since`` are
  emitted. For count-based rules, the crossing fires only when the
  threshold is crossed AFTER ``since``: count_at_since < limit AND
  count_now >= limit. A log whose count was already above the limit
  before ``since`` does NOT re-fire on subsequent evaluations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from bob_tools.ledger.events import (
    CommitChangeClass,
    Event,
    EventType,
    GitSnapshot,
    make_threshold_crossed_payload,
)
from bob_tools.ledger.projector import PlanState
from bob_tools.ledger.storage import Storage

# ---------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------


class ThresholdRuleId(StrEnum):
    UNATTRIBUTABLE_COMMIT = "unattributable_commit"
    PHASE_ABANDONED = "phase_abandoned"
    PHASE_SUPERSEDED = "phase_superseded"
    PHASE_TOPOLOGY_CHANGED = "phase_topology_changed"
    INVARIANT_DECLARED = "invariant_declared"
    ASSUMPTION_FALSIFIED = "assumption_falsified"
    EXPLORATORY_COUNT_EXCEEDED = "exploratory_count_exceeded"


ALL_RULES: frozenset[ThresholdRuleId] = frozenset(ThresholdRuleId)


class ThresholdSeverity(StrEnum):
    """Severity of a crossing.

    ``annotate`` is reserved for future rules that should be logged
    but not re-author the plan. Slice B emits only ``trigger_reauthor``.
    """

    ANNOTATE = "annotate"
    TRIGGER_REAUTHOR = "trigger_reauthor"


class ThresholdRecommendedAction(StrEnum):
    """Recommended downstream action for a crossing.

    ``log_only`` mirrors ``severity=annotate`` and is unused at Slice B.
    ``reauthor_phase`` proposes reframing the affected phase only.
    ``reauthor_plan`` proposes reframing the plan as a whole.
    """

    LOG_ONLY = "log_only"
    REAUTHOR_PHASE = "reauthor_phase"
    REAUTHOR_PLAN = "reauthor_plan"


@dataclass(frozen=True)
class ThresholdParams:
    """Configurable parameters for the evaluator.

    ``exploratory_commit_limit`` gates rule 7. Default 5 follows the
    conservative design in plan-ledger-slice-b.md.

    ``enabled_rules`` selects which rules participate in this
    evaluation. The default enables every rule. Consumers can disable
    rules per-environment without changing code.
    """

    exploratory_commit_limit: int = 5
    enabled_rules: frozenset[ThresholdRuleId] = field(
        default_factory=lambda: ALL_RULES
    )


@dataclass(frozen=True)
class ThresholdCrossing:
    """One detected threshold crossing.

    Equality is structural; two crossings produced by independent but
    equivalent evaluations compare equal. This makes idempotence and
    determinism contracts checkable with plain ``==``.
    """

    rule_id: ThresholdRuleId
    severity: ThresholdSeverity
    evidence_event_ids: tuple[str, ...]
    recommended_action: ThresholdRecommendedAction
    summary: str
    detected_at_event_id: str


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------


def _is_after_since(event_id: str, since: str | None) -> bool:
    """Return True if ``event_id`` is strictly after ``since``.

    With ``since=None``, every event is considered "after" the empty
    cursor. ``event_id`` ordering is the canonical UUIDv7 string
    comparison the projector uses; the evaluator inherits that.
    """
    return since is None or event_id > since


def _is_unaccounted_execution_commit(event: Event) -> bool:
    """Shared predicate for rule 1 (unattributable_commit) and rule 7
    (exploratory_count_exceeded).

    An unaccounted execution commit is a ``commit_landed`` event with
    no attributed phase whose ``change_class`` is not ``plan_artifact``.
    The two exclusions are deliberate:

      - attributed_phase_id is None: the commit didn't land under any
        phase the plan defined, so the plan didn't anticipate it.
      - change_class != plan_artifact: a plan-artifact commit (Duplo
        writing PLAN.md, a manual plan edit, etc.) IS the plan being
        refreshed, not execution work that escaped it. Treating it as
        unattributable would loop a reauthor into reauthoring the
        plan that just landed.

    Rule 1 and rule 7 historically diverged on the second exclusion:
    rule 1 fired on every unattributed commit, rule 7 already excluded
    plan_artifact. The asymmetry meant a manual PLAN.md edit committed
    without phase attribution fired rule 1 and recommended
    reauthor_plan, which is a false positive.
    """
    if event.type is not EventType.COMMIT_LANDED:
        return False
    payload = event.payload
    if payload.get("attributed_phase_id") is not None:
        return False
    if payload.get("change_class") == CommitChangeClass.PLAN_ARTIFACT.value:
        return False
    return True


def _is_exploratory_commit(event: Event) -> bool:
    """Predicate for rule 7's count.

    Aliases :func:`_is_unaccounted_execution_commit`. Kept under its
    rule-7-specific name for readability at the call site; the
    underlying definition is shared with rule 1.
    """
    return _is_unaccounted_execution_commit(event)


def _short_sha(commit: str | None) -> str:
    if not isinstance(commit, str) or not commit:
        return "?"
    return commit[:8]


# ---------------------------------------------------------------------
# Per-rule evaluators
# ---------------------------------------------------------------------


def _evaluate_unattributable_commit(
    sorted_events: Sequence[Event], since: str | None
) -> list[ThresholdCrossing]:
    out: list[ThresholdCrossing] = []
    for ev in sorted_events:
        if not _is_after_since(ev.event_id, since):
            continue
        # Shared predicate with rule 7: ignore commits that lack
        # attribution but ARE the plan being refreshed
        # (change_class=plan_artifact). A manual PLAN.md edit
        # committed without phase attribution must not cascade
        # into a plan-wide reauthor of the plan that just landed.
        if not _is_unaccounted_execution_commit(ev):
            continue
        commit = _short_sha(ev.payload.get("commit"))
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.UNATTRIBUTABLE_COMMIT,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(ev.event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PLAN,
                summary=(
                    f"commit {commit} landed with no attributed phase"
                ),
                detected_at_event_id=ev.event_id,
            )
        )
    return out


def _evaluate_phase_abandoned(
    sorted_events: Sequence[Event], since: str | None
) -> list[ThresholdCrossing]:
    out: list[ThresholdCrossing] = []
    for ev in sorted_events:
        if ev.type is not EventType.PHASE_ABANDONED:
            continue
        if not _is_after_since(ev.event_id, since):
            continue
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.PHASE_ABANDONED,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(ev.event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PHASE,
                summary=(
                    f"phase {ev.payload.get('phase_id', '?')} abandoned: "
                    f"{ev.payload.get('reason', '')}"
                ),
                detected_at_event_id=ev.event_id,
            )
        )
    return out


def _evaluate_phase_superseded(
    sorted_events: Sequence[Event], since: str | None
) -> list[ThresholdCrossing]:
    out: list[ThresholdCrossing] = []
    for ev in sorted_events:
        if ev.type is not EventType.PHASE_SUPERSEDED:
            continue
        if not _is_after_since(ev.event_id, since):
            continue
        p = ev.payload
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.PHASE_SUPERSEDED,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(ev.event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PHASE,
                summary=(
                    f"phase {p.get('phase_id', '?')} superseded by "
                    f"{p.get('superseded_by_phase_id', '?')}: "
                    f"{p.get('reason', '')}"
                ),
                detected_at_event_id=ev.event_id,
            )
        )
    return out


def _evaluate_phase_topology_changed(
    sorted_events: Sequence[Event], since: str | None
) -> list[ThresholdCrossing]:
    out: list[ThresholdCrossing] = []
    topology_types = {EventType.PHASE_SPLIT, EventType.PHASE_MERGED}
    for ev in sorted_events:
        if ev.type not in topology_types:
            continue
        if not _is_after_since(ev.event_id, since):
            continue
        p = ev.payload
        if ev.type is EventType.PHASE_SPLIT:
            into = p.get("into_phase_ids") or []
            summary = (
                f"phase {p.get('phase_id', '?')} split into "
                f"{len(into)} phases: {p.get('reason', '')}"
            )
        else:
            merged = p.get("merged_phase_ids") or []
            summary = (
                f"phases {', '.join(merged)} merged into "
                f"{p.get('into_phase_id', '?')}: {p.get('reason', '')}"
            )
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.PHASE_TOPOLOGY_CHANGED,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(ev.event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PHASE,
                summary=summary,
                detected_at_event_id=ev.event_id,
            )
        )
    return out


def _evaluate_invariant_declared(
    state: PlanState, since: str | None
) -> list[ThresholdCrossing]:
    out: list[ThresholdCrossing] = []
    for inv in state.invariants:
        if not _is_after_since(inv.declared_event_id, since):
            continue
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.INVARIANT_DECLARED,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(inv.declared_event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PLAN,
                summary=(
                    f"new invariant {inv.invariant_id}: {inv.statement}"
                ),
                detected_at_event_id=inv.declared_event_id,
            )
        )
    return out


def _evaluate_assumption_falsified(
    sorted_events: Sequence[Event], since: str | None
) -> list[ThresholdCrossing]:
    """Scan events for ``assumption_falsified`` directly.

    AssumptionRecord on PlanState stores ``falsified_event_id``, but
    that is the projector-recorded pointer to the *evidence* event
    (the test_failed/finding_observed that disproved the assumption),
    not the ``assumption_falsified`` event itself. The crossing's
    evidence is the falsification event; scan events to recover it,
    same shape as rules 1-4.
    """
    out: list[ThresholdCrossing] = []
    for ev in sorted_events:
        if ev.type is not EventType.ASSUMPTION_FALSIFIED:
            continue
        if not _is_after_since(ev.event_id, since):
            continue
        p = ev.payload
        out.append(
            ThresholdCrossing(
                rule_id=ThresholdRuleId.ASSUMPTION_FALSIFIED,
                severity=ThresholdSeverity.TRIGGER_REAUTHOR,
                evidence_event_ids=(ev.event_id,),
                recommended_action=ThresholdRecommendedAction.REAUTHOR_PHASE,
                summary=(
                    f"assumption {p.get('assumption_id', '?')} falsified: "
                    f"{p.get('summary', '')}"
                ),
                detected_at_event_id=ev.event_id,
            )
        )
    return out


def _evaluate_exploratory_count_exceeded(
    sorted_events: Sequence[Event],
    since: str | None,
    limit: int,
) -> list[ThresholdCrossing]:
    if limit <= 0:
        return []
    exploratory = [ev for ev in sorted_events if _is_exploratory_commit(ev)]
    count_now = len(exploratory)
    if count_now < limit:
        return []
    if since is None:
        count_at_since = 0
    else:
        count_at_since = sum(1 for ev in exploratory if ev.event_id <= since)
    if count_at_since >= limit:
        # Threshold was already crossed before or at `since`; nothing
        # new fires now.
        return []
    # The limit-th exploratory commit (1-indexed) is the transition
    # event, regardless of where `since` lies inside the prefix.
    transition_ev = exploratory[limit - 1]
    return [
        ThresholdCrossing(
            rule_id=ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED,
            severity=ThresholdSeverity.TRIGGER_REAUTHOR,
            evidence_event_ids=(transition_ev.event_id,),
            recommended_action=ThresholdRecommendedAction.REAUTHOR_PLAN,
            summary=(
                f"exploratory commit count reached {limit}"
                f" (limit={limit})"
            ),
            detected_at_event_id=transition_ev.event_id,
        )
    ]


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


def evaluate_thresholds(
    state: PlanState,
    events: Sequence[Event],
    params: ThresholdParams,
    *,
    since: str | None = None,
) -> list[ThresholdCrossing]:
    """Evaluate the seven Slice B threshold rules.

    Parameters
    ----------
    state
        ``PlanState`` produced by ``project(events)``. Rules 5
        (invariant_declared) and 6 (assumption_falsified) read it.
    events
        Same event collection that produced ``state``. Rules 1-4 and 7
        read event-type information that ``PlanState`` does not
        retain.
    params
        Configurable rule parameters and the enabled-rule set.
    since
        Opaque event-id cursor owned by the caller. Only crossings
        whose evidence has ``event_id > since`` are emitted. For
        count-based rules, the crossing fires only when the threshold
        is crossed AFTER ``since``, never on a state where the count
        was already above the limit at the cursor.

    Returns
    -------
    list[ThresholdCrossing]
        Sorted by ``(detected_at_event_id, rule_id)`` so the result
        is invariant under input event ordering. Empty if no rules
        fired (or if all rules are disabled).
    """
    sorted_events = sorted(events, key=lambda e: e.event_id)
    enabled = params.enabled_rules
    crossings: list[ThresholdCrossing] = []

    if ThresholdRuleId.UNATTRIBUTABLE_COMMIT in enabled:
        crossings.extend(
            _evaluate_unattributable_commit(sorted_events, since)
        )
    if ThresholdRuleId.PHASE_ABANDONED in enabled:
        crossings.extend(_evaluate_phase_abandoned(sorted_events, since))
    if ThresholdRuleId.PHASE_SUPERSEDED in enabled:
        crossings.extend(_evaluate_phase_superseded(sorted_events, since))
    if ThresholdRuleId.PHASE_TOPOLOGY_CHANGED in enabled:
        crossings.extend(
            _evaluate_phase_topology_changed(sorted_events, since)
        )
    if ThresholdRuleId.INVARIANT_DECLARED in enabled:
        crossings.extend(_evaluate_invariant_declared(state, since))
    if ThresholdRuleId.ASSUMPTION_FALSIFIED in enabled:
        crossings.extend(
            _evaluate_assumption_falsified(sorted_events, since)
        )
    if ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED in enabled:
        crossings.extend(
            _evaluate_exploratory_count_exceeded(
                sorted_events, since, params.exploratory_commit_limit
            )
        )

    crossings.sort(key=lambda c: (c.detected_at_event_id, c.rule_id.value))
    return crossings


# ---------------------------------------------------------------------
# record_crossings — Slice B part 2
# ---------------------------------------------------------------------


def _existing_crossing_keys(
    storage: Storage,
) -> set[tuple[str, frozenset[str]]]:
    """Build the dedupe set from threshold_crossed events on disk.

    A crossing's identity for idempotence is ``(rule_id,
    frozenset(triggering_event_ids))``. ``summary`` is human-readable
    and must not affect identity. ``detected_at_event_id`` is
    redundant with the evidence for event-based rules and a derived
    pick for the count-based rule, so it is also excluded from the
    key.
    """
    keys: set[tuple[str, frozenset[str]]] = set()
    for ev in storage.iter_events():
        if ev.type is not EventType.THRESHOLD_CROSSED:
            continue
        rule_id = ev.payload.get("rule_id")
        triggering = ev.payload.get("triggering_event_ids") or []
        if isinstance(rule_id, str) and isinstance(triggering, list):
            keys.add((rule_id, frozenset(triggering)))
    return keys


def record_crossings(
    storage: Storage,
    crossings: Sequence[ThresholdCrossing],
    *,
    run_id: str,
    git: GitSnapshot | None = None,
) -> list[str]:
    """Append one ``threshold_crossed`` event per new crossing.

    Idempotent. Reads existing ``threshold_crossed`` events from
    ``storage`` first; any incoming crossing whose ``(rule_id,
    frozenset(evidence_event_ids))`` key matches a pre-existing
    record is skipped. The skip is silent: callers see only the
    list of newly emitted ``event_id``s, in the order the
    corresponding crossings appeared in ``crossings``.

    Determinism: emit order matches input order. Callers usually
    pass a list returned by ``evaluate_thresholds``, which is
    already sorted by ``(detected_at_event_id, rule_id)``, so the
    on-disk order of recorded crossings is deterministic.

    Round-trip: emitted events are reserved-type ``threshold_crossed``,
    which Slice A's projector treats as no-ops. Recording crossings
    therefore advances ``last_event_id`` and the per-writer
    ``last_event_seq_per_writer`` high-water mark but does not
    change any other field of ``PlanState``.

    Returns the event_ids of newly emitted events. Returns an
    empty list if every crossing was already recorded or if
    ``crossings`` is empty.
    """
    if not run_id:
        raise ValueError("run_id must be non-empty")

    if not crossings:
        return []

    seen_keys = _existing_crossing_keys(storage)
    emitted: list[str] = []
    for crossing in crossings:
        key = (crossing.rule_id.value, frozenset(crossing.evidence_event_ids))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ev = storage.append(
            event_type=EventType.THRESHOLD_CROSSED,
            payload=make_threshold_crossed_payload(
                rule_id=crossing.rule_id.value,
                triggering_event_ids=list(crossing.evidence_event_ids),
                summary=crossing.summary,
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(ev.event_id)
    return emitted


__all__ = [
    "ALL_RULES",
    "ThresholdCrossing",
    "ThresholdParams",
    "ThresholdRecommendedAction",
    "ThresholdRuleId",
    "ThresholdSeverity",
    "evaluate_thresholds",
    "record_crossings",
]
