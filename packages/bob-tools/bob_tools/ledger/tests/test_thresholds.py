"""Tests for the Plan Ledger threshold evaluator.

Coverage:

  - Per-rule (positive, negative, idempotent) for each of the seven
    Slice B rules.
  - `since` semantics for event-based rules (cursor strictly before
    triggering event fires; cursor at or after does not).
  - `since` semantics for the count-based rule (all three Codex
    edge cases).
  - Rule 7 plan_artifact and attributed-commit exclusions.
  - Multi-rule deterministic ordering.
  - Empty-state and disabled-rule edge cases.
  - Four mandatory determinism invariants.
  - Slice B part 2: record_crossings idempotence, determinism, and
    round-trip-as-no-op.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from bob_tools.ledger import (
    ALL_RULES,
    AssumptionConfidence,
    CommitChangeClass,
    Event,
    EventType,
    GitSnapshot,
    Storage,
    ThresholdCrossing,
    ThresholdParams,
    ThresholdRecommendedAction,
    ThresholdRuleId,
    ThresholdSeverity,
    evaluate_thresholds,
    project,
    record_crossings,
)
from bob_tools.ledger._uuid7 import uuid7
from bob_tools.ledger.events import (
    make_assumption_declared_payload,
    make_assumption_falsified_payload,
    make_commit_landed_payload,
    make_finding_observed_payload,
    make_invariant_declared_payload,
    make_phase_abandoned_payload,
    make_phase_merged_payload,
    make_phase_split_payload,
    make_phase_started_payload,
    make_phase_superseded_payload,
)

# ---------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------


_TS = "2026-05-08T16:00:00.000000Z"


def _make(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    seq: int = 0,
    writer_id: str = "w-A",
    run_id: str = "r-1",
    ts: str | None = None,
    git: GitSnapshot | None = None,
    event_id: str | None = None,
) -> Event:
    return Event(
        event_id=event_id or uuid7(),
        seq=seq,
        ts=ts or _TS,
        writer_id=writer_id,
        run_id=run_id,
        type=event_type,
        git=git or GitSnapshot.empty(),
        payload=payload,
    )


def _commit_payload(
    *,
    attributed_phase_id: str | None,
    change_class: CommitChangeClass = CommitChangeClass.CODE,
    commit: str = "abc12345",
) -> dict[str, Any]:
    return make_commit_landed_payload(
        commit=commit,
        parent_commits=[],
        branch=None,
        author="m",
        subject="s",
        attributed_phase_id=attributed_phase_id,
        files_changed=1,
        lines_added=1,
        lines_removed=0,
        change_class=change_class,
    )


def _eval(
    events: list[Event],
    *,
    params: ThresholdParams | None = None,
    since: str | None = None,
) -> list[ThresholdCrossing]:
    state = project(events)
    return evaluate_thresholds(
        state,
        events,
        params or ThresholdParams(),
        since=since,
    )


# ---------------------------------------------------------------------
# Empty
# ---------------------------------------------------------------------


class TestEmpty:
    def test_empty_events_no_crossings(self) -> None:
        assert _eval([]) == []

    def test_default_params_enables_all_rules(self) -> None:
        assert ThresholdParams().enabled_rules == ALL_RULES


# ---------------------------------------------------------------------
# Rule 1: unattributable_commit
# ---------------------------------------------------------------------


class TestUnattributableCommit:
    def test_positive_unattributed_commit_fires(self) -> None:
        ev = _make(
            EventType.COMMIT_LANDED, _commit_payload(attributed_phase_id=None)
        )
        crossings = _eval([ev])
        assert len(crossings) == 1
        c = crossings[0]
        assert c.rule_id is ThresholdRuleId.UNATTRIBUTABLE_COMMIT
        assert c.severity is ThresholdSeverity.TRIGGER_REAUTHOR
        assert c.recommended_action is ThresholdRecommendedAction.REAUTHOR_PLAN
        assert c.evidence_event_ids == (ev.event_id,)
        assert c.detected_at_event_id == ev.event_id

    def test_negative_attributed_commit_does_not_fire(self) -> None:
        start = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        commit = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id="p1"),
            seq=1,
        )
        crossings = _eval([start, commit])
        assert all(
            c.rule_id is not ThresholdRuleId.UNATTRIBUTABLE_COMMIT
            for c in crossings
        )

    def test_idempotent(self) -> None:
        ev = _make(
            EventType.COMMIT_LANDED, _commit_payload(attributed_phase_id=None)
        )
        a = _eval([ev])
        b = _eval([ev])
        assert a == b


# ---------------------------------------------------------------------
# Rule 2: phase_abandoned
# ---------------------------------------------------------------------


class TestPhaseAbandoned:
    def test_positive_fires(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        crossings = _eval([e1, e2])
        cs = [c for c in crossings if c.rule_id is ThresholdRuleId.PHASE_ABANDONED]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (e2.event_id,)
        assert (
            cs[0].recommended_action is ThresholdRecommendedAction.REAUTHOR_PHASE
        )

    def test_negative_no_abandon_no_fire(self) -> None:
        ev = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
        )
        crossings = _eval([ev])
        assert all(
            c.rule_id is not ThresholdRuleId.PHASE_ABANDONED for c in crossings
        )

    def test_idempotent(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        assert _eval([e1, e2]) == _eval([e1, e2])


# ---------------------------------------------------------------------
# Rule 3: phase_superseded
# ---------------------------------------------------------------------


class TestPhaseSuperseded:
    def test_positive_fires(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p2", title="y"),
            seq=1,
        )
        e3 = _make(
            EventType.PHASE_SUPERSEDED,
            make_phase_superseded_payload(
                phase_id="p1", superseded_by_phase_id="p2", reason="r"
            ),
            seq=2,
        )
        crossings = _eval([e1, e2, e3])
        cs = [c for c in crossings if c.rule_id is ThresholdRuleId.PHASE_SUPERSEDED]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (e3.event_id,)

    def test_negative(self) -> None:
        crossings = _eval(
            [
                _make(
                    EventType.PHASE_STARTED,
                    make_phase_started_payload(phase_id="p1", title="x"),
                )
            ]
        )
        assert all(
            c.rule_id is not ThresholdRuleId.PHASE_SUPERSEDED for c in crossings
        )

    def test_idempotent(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p2", title="y"),
            seq=1,
        )
        e3 = _make(
            EventType.PHASE_SUPERSEDED,
            make_phase_superseded_payload(
                phase_id="p1", superseded_by_phase_id="p2", reason="r"
            ),
            seq=2,
        )
        assert _eval([e1, e2, e3]) == _eval([e1, e2, e3])


# ---------------------------------------------------------------------
# Rule 4: phase_topology_changed
# ---------------------------------------------------------------------


class TestPhaseTopologyChanged:
    def test_split_fires(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_SPLIT,
            make_phase_split_payload(
                phase_id="p1", into_phase_ids=["p1a", "p1b"], reason="r"
            ),
            seq=1,
        )
        crossings = _eval([e1, e2])
        cs = [
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.PHASE_TOPOLOGY_CHANGED
        ]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (e2.event_id,)

    def test_merge_fires(self) -> None:
        evs = [
            _make(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="p1", title="x"),
                seq=0,
            ),
            _make(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="p2", title="y"),
                seq=1,
            ),
            _make(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="merged", title="m"),
                seq=2,
            ),
            _make(
                EventType.PHASE_MERGED,
                make_phase_merged_payload(
                    merged_phase_ids=["p1", "p2"],
                    into_phase_id="merged",
                    reason="ok",
                ),
                seq=3,
            ),
        ]
        crossings = _eval(evs)
        cs = [
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.PHASE_TOPOLOGY_CHANGED
        ]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (evs[3].event_id,)

    def test_negative(self) -> None:
        ev = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
        )
        crossings = _eval([ev])
        assert all(
            c.rule_id is not ThresholdRuleId.PHASE_TOPOLOGY_CHANGED
            for c in crossings
        )

    def test_idempotent(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_SPLIT,
            make_phase_split_payload(
                phase_id="p1", into_phase_ids=["a", "b"], reason="r"
            ),
            seq=1,
        )
        assert _eval([e1, e2]) == _eval([e1, e2])


# ---------------------------------------------------------------------
# Rule 5: invariant_declared
# ---------------------------------------------------------------------


class TestInvariantDeclared:
    def test_positive_fires(self) -> None:
        ev = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="inv-1",
                statement="x must y",
                source="m",
            ),
        )
        crossings = _eval([ev])
        cs = [
            c for c in crossings if c.rule_id is ThresholdRuleId.INVARIANT_DECLARED
        ]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (ev.event_id,)
        assert (
            cs[0].recommended_action is ThresholdRecommendedAction.REAUTHOR_PLAN
        )

    def test_negative(self) -> None:
        crossings = _eval([])
        assert crossings == []

    def test_idempotent(self) -> None:
        ev = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="inv-1",
                statement="x",
                source="m",
            ),
        )
        assert _eval([ev]) == _eval([ev])


# ---------------------------------------------------------------------
# Rule 6: assumption_falsified
# ---------------------------------------------------------------------


class TestAssumptionFalsified:
    def test_positive_fires(self) -> None:
        e1 = _make(
            EventType.ASSUMPTION_DECLARED,
            make_assumption_declared_payload(
                assumption_id="a-1",
                statement="x",
                confidence=AssumptionConfidence.MEDIUM,
            ),
            seq=0,
        )
        e2 = _make(
            EventType.ASSUMPTION_FALSIFIED,
            make_assumption_falsified_payload(
                assumption_id="a-1",
                evidence_event_id=uuid7(),
                summary="no",
            ),
            seq=1,
        )
        crossings = _eval([e1, e2])
        cs = [
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.ASSUMPTION_FALSIFIED
        ]
        assert len(cs) == 1
        assert cs[0].evidence_event_ids == (e2.event_id,)
        assert (
            cs[0].recommended_action is ThresholdRecommendedAction.REAUTHOR_PHASE
        )

    def test_negative_declared_only(self) -> None:
        ev = _make(
            EventType.ASSUMPTION_DECLARED,
            make_assumption_declared_payload(
                assumption_id="a-1",
                statement="x",
                confidence=AssumptionConfidence.HIGH,
            ),
        )
        crossings = _eval([ev])
        assert all(
            c.rule_id is not ThresholdRuleId.ASSUMPTION_FALSIFIED
            for c in crossings
        )

    def test_idempotent(self) -> None:
        e1 = _make(
            EventType.ASSUMPTION_DECLARED,
            make_assumption_declared_payload(
                assumption_id="a-1",
                statement="x",
                confidence=AssumptionConfidence.HIGH,
            ),
            seq=0,
        )
        e2 = _make(
            EventType.ASSUMPTION_FALSIFIED,
            make_assumption_falsified_payload(
                assumption_id="a-1",
                evidence_event_id=uuid7(),
                summary="no",
            ),
            seq=1,
        )
        assert _eval([e1, e2]) == _eval([e1, e2])


# ---------------------------------------------------------------------
# Rule 7: exploratory_count_exceeded
# ---------------------------------------------------------------------


def _exploratory_log(n: int) -> list[Event]:
    return [
        _make(
            EventType.COMMIT_LANDED,
            _commit_payload(
                attributed_phase_id=None,
                change_class=CommitChangeClass.CODE,
                commit=f"e0000{i:03d}",
            ),
            seq=i,
        )
        for i in range(n)
    ]


class TestExploratoryCountExceeded:
    def test_positive_fires_at_limit(self) -> None:
        evs = _exploratory_log(5)
        params = ThresholdParams(exploratory_commit_limit=5)
        # Disable the unattributable_commit rule so we only see the
        # count rule's crossings.
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        crossings = _eval(evs, params=params)
        assert len(crossings) == 1
        c = crossings[0]
        assert c.rule_id is ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED
        # Evidence is the limit-th (1-indexed) exploratory event_id.
        sorted_evs = sorted(evs, key=lambda e: e.event_id)
        assert c.evidence_event_ids == (sorted_evs[4].event_id,)
        assert c.detected_at_event_id == sorted_evs[4].event_id

    def test_negative_below_limit_does_not_fire(self) -> None:
        evs = _exploratory_log(4)
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        assert _eval(evs, params=params) == []

    def test_plan_artifact_does_not_count(self) -> None:
        evs = [
            _make(
                EventType.COMMIT_LANDED,
                _commit_payload(
                    attributed_phase_id=None,
                    change_class=CommitChangeClass.PLAN_ARTIFACT,
                ),
                seq=i,
            )
            for i in range(10)
        ]
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        assert _eval(evs, params=params) == []

    def test_attributed_commit_does_not_count(self) -> None:
        start = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        attributed = [
            _make(
                EventType.COMMIT_LANDED,
                _commit_payload(attributed_phase_id="p1"),
                seq=1 + i,
            )
            for i in range(10)
        ]
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        assert _eval([start, *attributed], params=params) == []

    def test_idempotent(self) -> None:
        evs = _exploratory_log(6)
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        assert _eval(evs, params=params) == _eval(evs, params=params)


# ---------------------------------------------------------------------
# `since` semantics — event-based rules
# ---------------------------------------------------------------------


class TestSinceEventBased:
    def test_event_after_since_fires(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        # since prior to e2 — abandon fires.
        crossings = _eval([e1, e2], since=e1.event_id)
        cs = [c for c in crossings if c.rule_id is ThresholdRuleId.PHASE_ABANDONED]
        assert len(cs) == 1

    def test_event_at_since_does_not_fire(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        # since == e2.event_id; rule must not fire (strict > only).
        crossings = _eval([e1, e2], since=e2.event_id)
        cs = [c for c in crossings if c.rule_id is ThresholdRuleId.PHASE_ABANDONED]
        assert cs == []

    def test_event_before_since_does_not_fire(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        e3 = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="i1", statement="x", source="m"
            ),
            seq=2,
        )
        # since AFTER abandonment — abandon does not fire; later
        # invariant still does.
        crossings = _eval([e1, e2, e3], since=e2.event_id)
        rule_ids = {c.rule_id for c in crossings}
        assert ThresholdRuleId.PHASE_ABANDONED not in rule_ids
        assert ThresholdRuleId.INVARIANT_DECLARED in rule_ids


# ---------------------------------------------------------------------
# `since` semantics — count-based rule (Codex's three edge cases)
# ---------------------------------------------------------------------


class TestSinceCountBased:
    @staticmethod
    def _params() -> ThresholdParams:
        return ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )

    def test_count_at_since_3_now_6_fires(self) -> None:
        evs = _exploratory_log(6)
        sorted_evs = sorted(evs, key=lambda e: e.event_id)
        since = sorted_evs[2].event_id  # count_at_since = 3
        crossings = _eval(evs, params=self._params(), since=since)
        # count_at_since = 3 < 5; count_now = 6 >= 5; fires.
        assert len(crossings) == 1
        # detected_at = the limit-th (5th) exploratory commit.
        assert crossings[0].detected_at_event_id == sorted_evs[4].event_id

    def test_count_at_since_8_now_8_does_not_fire(self) -> None:
        evs = _exploratory_log(8)
        sorted_evs = sorted(evs, key=lambda e: e.event_id)
        since = sorted_evs[7].event_id  # count_at_since = 8
        # count_at_since = 8 >= 5; threshold already crossed before
        # since. Should NOT re-fire.
        crossings = _eval(evs, params=self._params(), since=since)
        assert crossings == []

    def test_count_at_since_4_now_5_just_crossing_fires(self) -> None:
        evs = _exploratory_log(5)
        sorted_evs = sorted(evs, key=lambda e: e.event_id)
        since = sorted_evs[3].event_id  # count_at_since = 4
        # count_at_since = 4 < 5; count_now = 5 >= 5; fires on the 5th.
        crossings = _eval(evs, params=self._params(), since=since)
        assert len(crossings) == 1
        assert crossings[0].detected_at_event_id == sorted_evs[4].event_id

    def test_count_at_since_5_now_5_does_not_fire(self) -> None:
        evs = _exploratory_log(5)
        sorted_evs = sorted(evs, key=lambda e: e.event_id)
        since = sorted_evs[4].event_id  # count_at_since = 5
        # count_at_since = 5 >= limit; already crossed at since.
        crossings = _eval(evs, params=self._params(), since=since)
        assert crossings == []


# ---------------------------------------------------------------------
# Multi-rule + ordering
# ---------------------------------------------------------------------


class TestMultiRule:
    def test_multiple_rules_fire_simultaneously(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        e3 = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="inv-1", statement="x", source="m"
            ),
            seq=2,
        )
        crossings = _eval([e1, e2, e3])
        rule_ids = {c.rule_id for c in crossings}
        assert ThresholdRuleId.PHASE_ABANDONED in rule_ids
        assert ThresholdRuleId.INVARIANT_DECLARED in rule_ids

    def test_returned_list_sorted_by_detected_at_then_rule(self) -> None:
        # Two events at identical detected_at_event_id is impossible
        # (UUIDv7 prevents collisions). Construct three different
        # events with distinct event_ids; the list must come back
        # sorted by event_id ascending.
        e1 = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="i1", statement="x", source="m"
            ),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=1,
        )
        e3 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=2,
        )
        crossings = _eval([e1, e2, e3])
        detected = [c.detected_at_event_id for c in crossings]
        assert detected == sorted(detected)


# ---------------------------------------------------------------------
# Disabled rules
# ---------------------------------------------------------------------


class TestDisabledRule:
    def test_disabled_rule_does_not_fire(self) -> None:
        ev = _make(
            EventType.COMMIT_LANDED, _commit_payload(attributed_phase_id=None)
        )
        params = ThresholdParams(
            enabled_rules=ALL_RULES - {ThresholdRuleId.UNATTRIBUTABLE_COMMIT}
        )
        crossings = _eval([ev], params=params)
        assert all(
            c.rule_id is not ThresholdRuleId.UNATTRIBUTABLE_COMMIT
            for c in crossings
        )

    def test_other_rules_unaffected_by_disable(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_ABANDONED,
            make_phase_abandoned_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        ev = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id=None),
            seq=2,
        )
        params = ThresholdParams(
            enabled_rules=ALL_RULES - {ThresholdRuleId.UNATTRIBUTABLE_COMMIT}
        )
        crossings = _eval([e1, e2, ev], params=params)
        rule_ids = {c.rule_id for c in crossings}
        assert ThresholdRuleId.PHASE_ABANDONED in rule_ids

    def test_empty_enabled_rules_returns_no_crossings(self) -> None:
        ev = _make(
            EventType.COMMIT_LANDED, _commit_payload(attributed_phase_id=None)
        )
        params = ThresholdParams(enabled_rules=frozenset())
        assert _eval([ev], params=params) == []


# ---------------------------------------------------------------------
# Determinism contract — four mandatory invariants
# ---------------------------------------------------------------------


def _build_realistic_log() -> list[Event]:
    """Workhorse fixture spanning every active rule plus
    one reserved event."""
    e1 = _make(
        EventType.PHASE_STARTED,
        make_phase_started_payload(phase_id="p1", title="x"),
        seq=0,
        writer_id="w-A",
    )
    e2 = _make(
        EventType.PHASE_STARTED,
        make_phase_started_payload(phase_id="p2", title="y"),
        seq=1,
        writer_id="w-A",
    )
    e3 = _make(
        EventType.PHASE_SUPERSEDED,
        make_phase_superseded_payload(
            phase_id="p1", superseded_by_phase_id="p2", reason="r"
        ),
        seq=2,
        writer_id="w-A",
    )
    e4 = _make(
        EventType.INVARIANT_DECLARED,
        make_invariant_declared_payload(
            invariant_id="inv-1", statement="x", source="m"
        ),
        seq=0,
        writer_id="w-B",
    )
    e5 = _make(
        EventType.ASSUMPTION_DECLARED,
        make_assumption_declared_payload(
            assumption_id="a-1",
            statement="x",
            confidence=AssumptionConfidence.LOW,
        ),
        seq=1,
        writer_id="w-B",
    )
    e6 = _make(
        EventType.ASSUMPTION_FALSIFIED,
        make_assumption_falsified_payload(
            assumption_id="a-1",
            evidence_event_id=uuid7(),
            summary="no",
        ),
        seq=2,
        writer_id="w-B",
    )
    e7 = _make(
        EventType.COMMIT_LANDED,
        _commit_payload(attributed_phase_id=None),
        seq=3,
        writer_id="w-A",
    )
    e8 = _make(
        EventType.PHASE_SPLIT,
        make_phase_split_payload(
            phase_id="p2", into_phase_ids=["p2a", "p2b"], reason="r"
        ),
        seq=4,
        writer_id="w-A",
    )
    return [e1, e2, e3, e4, e5, e6, e7, e8]


class TestDeterminism:
    def test_shuffle_invariant(self) -> None:
        events = _build_realistic_log()
        baseline = _eval(events)
        rng = random.Random(11)
        for _ in range(20):
            shuffled = list(events)
            rng.shuffle(shuffled)
            assert _eval(shuffled) == baseline

    def test_ts_independence(self) -> None:
        events = _build_realistic_log()
        baseline = _eval(events)
        # Replace ts on every event with one that DECREASES as
        # event_id increases. evaluator should not care.
        sorted_events = sorted(events, key=lambda e: e.event_id)
        rewritten: list[Event] = []
        ts_map = {
            ev.event_id: f"2026-05-08T16:00:{59 - i:02d}.000000Z"
            for i, ev in enumerate(sorted_events)
        }
        for ev in events:
            rewritten.append(
                Event(
                    event_id=ev.event_id,
                    seq=ev.seq,
                    ts=ts_map[ev.event_id],
                    writer_id=ev.writer_id,
                    run_id=ev.run_id,
                    type=ev.type,
                    git=ev.git,
                    payload=ev.payload,
                    schema_version=ev.schema_version,
                )
            )
        assert _eval(rewritten) == baseline

    def test_project_shuffle_composition(self) -> None:
        events = _build_realistic_log()
        sorted_events = sorted(events, key=lambda e: e.event_id)
        baseline = evaluate_thresholds(
            project(sorted_events),
            sorted_events,
            ThresholdParams(),
        )
        rng = random.Random(13)
        for _ in range(10):
            shuffled = list(events)
            rng.shuffle(shuffled)
            actual = evaluate_thresholds(
                project(shuffled),
                shuffled,
                ThresholdParams(),
            )
            assert actual == baseline

    def test_returned_list_ordering_is_stable(self) -> None:
        events = _build_realistic_log()
        result = _eval(events)
        # Check the documented sort order:
        #   primary: detected_at_event_id ascending
        #   tiebreak: rule_id (string) ascending
        for i in range(1, len(result)):
            prev = (
                result[i - 1].detected_at_event_id,
                result[i - 1].rule_id.value,
            )
            curr = (result[i].detected_at_event_id, result[i].rule_id.value)
            assert prev <= curr


# ---------------------------------------------------------------------
# Cross-cutting: only finding_observed without phase does not trigger
# rule 1 (rule 1 is commit-specific, not finding-specific)
# ---------------------------------------------------------------------


class TestCrossCutting:
    def test_unattributed_finding_does_not_trigger_rule_1(self) -> None:
        ev = _make(
            EventType.FINDING_OBSERVED,
            make_finding_observed_payload(summary="dangling finding"),
        )
        crossings = _eval([ev])
        assert all(
            c.rule_id is not ThresholdRuleId.UNATTRIBUTABLE_COMMIT
            for c in crossings
        )


# ---------------------------------------------------------------------
# record_crossings (Slice B part 2)
# ---------------------------------------------------------------------


def _seed_unattributed_commits(
    storage: Storage, *, n: int = 1
) -> list[Event]:
    """Append n unattributed commit_landed events. Returns the
    captured Events so tests can read their event_ids."""
    out: list[Event] = []
    for i in range(n):
        out.append(
            storage.append(
                event_type=EventType.COMMIT_LANDED,
                payload=_commit_payload(
                    attributed_phase_id=None,
                    commit=f"r000{i:04d}",
                ),
                run_id="rec",
            )
        )
    return out


class TestRecordCrossings:
    def test_emits_one_threshold_crossed_per_crossing(
        self, tmp_path: Path
    ) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        # Two unattributed commits → two rule-1 crossings.
        commits = _seed_unattributed_commits(storage, n=2)
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        assert len(crossings) == 2

        emitted_ids = record_crossings(storage, crossings, run_id="rec")
        assert len(emitted_ids) == 2

        all_events = storage.read_all()
        threshold_events = [
            e for e in all_events if e.type is EventType.THRESHOLD_CROSSED
        ]
        assert len(threshold_events) == 2

        # Each emitted event references one of the original commits.
        recorded_triggering = {
            tuple(e.payload["triggering_event_ids"]) for e in threshold_events
        }
        assert recorded_triggering == {(c.event_id,) for c in commits}

    def test_emit_order_matches_crossing_order(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        _seed_unattributed_commits(storage, n=3)
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        emitted_ids = record_crossings(storage, crossings, run_id="rec")

        # The emitted ids appear on disk in the same order as the
        # crossings list, which is itself sorted by
        # (detected_at_event_id, rule_id) per the evaluator contract.
        all_events = storage.read_all()
        threshold_events_in_order = [
            e for e in all_events if e.type is EventType.THRESHOLD_CROSSED
        ]
        on_disk_order = [e.event_id for e in threshold_events_in_order]
        assert on_disk_order == emitted_ids
        # And: the n-th emitted event references the n-th crossing's
        # evidence.
        for emitted, crossing in zip(
            threshold_events_in_order, crossings, strict=True
        ):
            assert (
                tuple(emitted.payload["triggering_event_ids"])
                == crossing.evidence_event_ids
            )

    def test_idempotent_no_double_emit(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        _seed_unattributed_commits(storage, n=2)
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )

        first = record_crossings(storage, crossings, run_id="rec")
        assert len(first) == 2

        # Re-evaluate after recording: the same two crossings still
        # surface (threshold_crossed events are reserved no-ops on
        # the projector and don't suppress the active rules).
        events_after = storage.read_all()
        crossings_after = evaluate_thresholds(
            project(events_after), events_after, ThresholdParams()
        )
        assert len(crossings_after) == 2

        # Recording again must NOT double-emit.
        second = record_crossings(storage, crossings_after, run_id="rec")
        assert second == []

        all_events = storage.read_all()
        threshold_events = [
            e for e in all_events if e.type is EventType.THRESHOLD_CROSSED
        ]
        assert len(threshold_events) == 2

    def test_partial_idempotent_only_new_crossings_emit(
        self, tmp_path: Path
    ) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        _seed_unattributed_commits(storage, n=2)
        events = storage.read_all()
        crossings_initial = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        record_crossings(storage, crossings_initial, run_id="rec")

        # Add one more unattributed commit, re-evaluate.
        _seed_unattributed_commits(storage, n=1)
        events_again = storage.read_all()
        crossings_again = evaluate_thresholds(
            project(events_again), events_again, ThresholdParams()
        )
        assert len(crossings_again) == 3

        # Only the new crossing should emit a new event.
        emitted = record_crossings(storage, crossings_again, run_id="rec")
        assert len(emitted) == 1

        # Total threshold_crossed on disk now == 3.
        all_events = storage.read_all()
        threshold_events = [
            e for e in all_events if e.type is EventType.THRESHOLD_CROSSED
        ]
        assert len(threshold_events) == 3

    def test_recorded_events_project_as_no_ops(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        _seed_unattributed_commits(storage, n=2)
        # Add one phase + invariant for non-trivial state.
        storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="rec",
        )
        storage.append(
            event_type=EventType.INVARIANT_DECLARED,
            payload=make_invariant_declared_payload(
                invariant_id="inv-1", statement="x", source="m"
            ),
            run_id="rec",
        )
        events_pre = storage.read_all()
        state_pre = project(events_pre)

        crossings = evaluate_thresholds(
            state_pre, events_pre, ThresholdParams()
        )
        record_crossings(storage, crossings, run_id="rec")

        events_post = storage.read_all()
        state_post = project(events_post)

        # All non-high-water-mark fields must be identical.
        assert state_post.phases == state_pre.phases
        assert state_post.invariants == state_pre.invariants
        assert state_post.assumptions == state_pre.assumptions
        assert state_post.human_decisions == state_pre.human_decisions
        assert (
            state_post.findings_unattributed == state_pre.findings_unattributed
        )
        assert (
            state_post.orphaned_design_reasoning
            == state_pre.orphaned_design_reasoning
        )
        assert (
            state_post.orphaned_design_reasoning_count
            == state_pre.orphaned_design_reasoning_count
        )
        # High-water marks DID advance because reserved events count
        # as successfully applied per the projector's contract.
        assert (
            state_post.last_event_seq_per_writer["w-1"]
            > state_pre.last_event_seq_per_writer["w-1"]
        )
        assert state_post.last_event_id != state_pre.last_event_id

    def test_payload_shape_matches_crossing(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        commits = _seed_unattributed_commits(storage, n=1)
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        record_crossings(storage, crossings, run_id="rec")

        all_events = storage.read_all()
        threshold_events = [
            e for e in all_events if e.type is EventType.THRESHOLD_CROSSED
        ]
        assert len(threshold_events) == 1
        emitted = threshold_events[0]
        assert emitted.payload["rule_id"] == "unattributable_commit"
        assert emitted.payload["triggering_event_ids"] == [commits[0].event_id]
        assert emitted.payload["summary"] == crossings[0].summary

    def test_empty_crossings_list_emits_nothing(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        # Seed with one event so the file exists.
        storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="rec",
        )
        emitted = record_crossings(storage, [], run_id="rec")
        assert emitted == []
        # No threshold_crossed events on disk.
        all_events = storage.read_all()
        assert not any(
            e.type is EventType.THRESHOLD_CROSSED for e in all_events
        )

    def test_empty_run_id_raises(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path, writer_id="w-1")
        commit = ThresholdCrossing(
            rule_id=ThresholdRuleId.UNATTRIBUTABLE_COMMIT,
            severity=ThresholdSeverity.TRIGGER_REAUTHOR,
            evidence_event_ids=(uuid7(),),
            recommended_action=ThresholdRecommendedAction.REAUTHOR_PLAN,
            summary="x",
            detected_at_event_id=uuid7(),
        )
        import pytest

        with pytest.raises(ValueError, match="run_id"):
            record_crossings(storage, [commit], run_id="")
