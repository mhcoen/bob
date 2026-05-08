"""Slice A -> Slice B integration tests.

The per-module test files in this directory exercise events,
projector, storage, and thresholds in isolation with synthetic
fixtures. This file exercises the round-trip path consumers actually
take:

  ``Storage.append`` -> events.jsonl on disk
  -> ``Storage.read_all`` -> events from disk
  -> ``project`` -> PlanState
  -> ``evaluate_thresholds`` -> crossings

Six scenarios per the Slice C readiness gate:

  1. Single-writer happy path covering all seven rule triggers.
  2. Multi-writer concurrent appends; determinism holds.
  3. ``since`` cursor straddling a writer boundary.
  4. JSONL round-trip preserves ``attributed_phase_id == None``.
  5. Reserved events validate, persist, and project as no-ops.
  6. Count-based rule fires once across two writers' interleaved
     exploratory commits.

Each test uses a fresh ``tmp_path`` so storage state is isolated.
"""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path
from typing import Any

from bob_tools.ledger import (
    AssumptionConfidence,
    CommitChangeClass,
    EventType,
    Storage,
    ThresholdParams,
    ThresholdRecommendedAction,
    ThresholdRuleId,
    ThresholdSeverity,
    evaluate_thresholds,
    project,
)
from bob_tools.ledger._uuid7 import uuid7
from bob_tools.ledger.events import (
    make_assumption_declared_payload,
    make_assumption_falsified_payload,
    make_commit_landed_payload,
    make_invariant_declared_payload,
    make_phase_abandoned_payload,
    make_phase_split_payload,
    make_phase_started_payload,
    make_phase_superseded_payload,
    make_plan_reauthored_payload,
    make_threshold_crossed_payload,
)

# ---------------------------------------------------------------------
# Helpers (mirrors the per-module factories but writes through Storage)
# ---------------------------------------------------------------------


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


def _round_trip(ledger_dir: Path) -> tuple[Any, list[Any]]:
    """Read events back from disk, project, return (state, events)."""
    reader = Storage(ledger_dir, writer_id="reader")
    events = reader.read_all()
    state = project(events)
    return state, events


# ---------------------------------------------------------------------
# Scenario 1: single-writer happy path
# ---------------------------------------------------------------------


class TestSingleWriterHappyPath:
    def test_all_seven_rules_fire(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")

        # Three phases -- p1, p2, p2_redux -- so we have lifecycle
        # events to fire rules 2, 3, 4.
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="t1"),
            run_id="run-1",
        )
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p2", title="t2"),
            run_id="run-1",
        )
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(
                phase_id="p2_redux", title="t2 redux"
            ),
            run_id="run-1",
        )

        # Rule 7 wants 5 unattributed non-plan-artifact commits.
        # These also each fire rule 1 (unattributable_commit).
        unattributed_ids: list[str] = []
        for i in range(5):
            ev = s.append(
                event_type=EventType.COMMIT_LANDED,
                payload=_commit_payload(
                    attributed_phase_id=None, commit=f"e000{i:04d}"
                ),
                run_id="run-1",
            )
            unattributed_ids.append(ev.event_id)

        abandon_ev = s.append(
            event_type=EventType.PHASE_ABANDONED,
            payload=make_phase_abandoned_payload(phase_id="p1", reason="r"),
            run_id="run-1",
        )
        split_ev = s.append(
            event_type=EventType.PHASE_SPLIT,
            payload=make_phase_split_payload(
                phase_id="p2", into_phase_ids=["p2a", "p2b"], reason="r"
            ),
            run_id="run-1",
        )
        # The split made p2 status=split. Use p2_redux as the
        # supersession target for p1 -- but p1 is already abandoned,
        # which lets phase_superseded fire on a different phase. Use a
        # fresh source: start p3, supersede it by p2_redux.
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p3", title="t3"),
            run_id="run-1",
        )
        super_ev = s.append(
            event_type=EventType.PHASE_SUPERSEDED,
            payload=make_phase_superseded_payload(
                phase_id="p3",
                superseded_by_phase_id="p2_redux",
                reason="reframe",
            ),
            run_id="run-1",
        )

        invariant_ev = s.append(
            event_type=EventType.INVARIANT_DECLARED,
            payload=make_invariant_declared_payload(
                invariant_id="inv-1", statement="x must y", source="m"
            ),
            run_id="run-1",
        )
        s.append(
            event_type=EventType.ASSUMPTION_DECLARED,
            payload=make_assumption_declared_payload(
                assumption_id="a-1",
                statement="z is fine",
                confidence=AssumptionConfidence.MEDIUM,
            ),
            run_id="run-1",
        )
        falsify_ev = s.append(
            event_type=EventType.ASSUMPTION_FALSIFIED,
            payload=make_assumption_falsified_payload(
                assumption_id="a-1",
                evidence_event_id=uuid7(),
                summary="actually no",
            ),
            run_id="run-1",
        )

        state, events = _round_trip(tmp_path)
        crossings = evaluate_thresholds(state, events, ThresholdParams())

        rule_counts: Counter[ThresholdRuleId] = Counter(
            c.rule_id for c in crossings
        )
        assert rule_counts[ThresholdRuleId.UNATTRIBUTABLE_COMMIT] == 5
        assert rule_counts[ThresholdRuleId.PHASE_ABANDONED] == 1
        assert rule_counts[ThresholdRuleId.PHASE_SUPERSEDED] == 1
        assert rule_counts[ThresholdRuleId.PHASE_TOPOLOGY_CHANGED] == 1
        assert rule_counts[ThresholdRuleId.INVARIANT_DECLARED] == 1
        assert rule_counts[ThresholdRuleId.ASSUMPTION_FALSIFIED] == 1
        assert rule_counts[ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED] == 1

        # Spot-check evidence_event_ids against the captured ids.
        abandon = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.PHASE_ABANDONED
        )
        assert abandon.evidence_event_ids == (abandon_ev.event_id,)
        topo = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.PHASE_TOPOLOGY_CHANGED
        )
        assert topo.evidence_event_ids == (split_ev.event_id,)
        super_crossing = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.PHASE_SUPERSEDED
        )
        assert super_crossing.evidence_event_ids == (super_ev.event_id,)
        invariant_crossing = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.INVARIANT_DECLARED
        )
        assert invariant_crossing.evidence_event_ids == (invariant_ev.event_id,)
        falsify_crossing = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.ASSUMPTION_FALSIFIED
        )
        assert falsify_crossing.evidence_event_ids == (falsify_ev.event_id,)
        # Rule 7 evidence is the limit-th unattributed commit
        # (1-indexed in the sorted unattributed subsequence). Since
        # all five were emitted by the same writer in order,
        # event_ids are monotonic and the 5th is the trigger.
        count_crossing = next(
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED
        )
        assert count_crossing.evidence_event_ids == (
            sorted(unattributed_ids)[4],
        )


# ---------------------------------------------------------------------
# Scenario 2: multi-writer concurrent appends, determinism holds
# ---------------------------------------------------------------------


class TestMultiWriterDeterminism:
    def test_concurrent_writers_produce_same_crossings_each_run(
        self, tmp_path: Path
    ) -> None:
        # First run: writer-A and writer-B append concurrently from
        # threads. Capture the resulting crossings.
        run1_dir = tmp_path / "run1"
        crossings1 = self._concurrent_run(run1_dir)

        # Second run: same logical events, same writer_ids, but a
        # fresh ledger dir. The crossings list must be equal as a
        # multiset (since each run's UUIDv7s are different, we can
        # only compare counts and rule ids, not event_ids). The point
        # of this test is that both runs satisfy the determinism
        # contract -- shuffling the disk-write interleave does not
        # change which RULES fire or how many times each fires.
        run2_dir = tmp_path / "run2"
        crossings2 = self._concurrent_run(run2_dir)

        rules1 = Counter(c.rule_id for c in crossings1)
        rules2 = Counter(c.rule_id for c in crossings2)
        assert rules1 == rules2

    @staticmethod
    def _concurrent_run(ledger_dir: Path) -> list[Any]:
        ledger_dir.mkdir(parents=True, exist_ok=True)

        # Each writer fires its own subset of rule triggers so the
        # final crossings reflect both writers' contributions.
        def writer_a() -> None:
            sa = Storage(ledger_dir, writer_id="w-A")
            sa.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(
                    phase_id="pA", title="A"
                ),
                run_id="r",
            )
            sa.append(
                event_type=EventType.PHASE_ABANDONED,
                payload=make_phase_abandoned_payload(
                    phase_id="pA", reason="r"
                ),
                run_id="r",
            )
            sa.append(
                event_type=EventType.INVARIANT_DECLARED,
                payload=make_invariant_declared_payload(
                    invariant_id="inv-A", statement="x", source="m"
                ),
                run_id="r",
            )

        def writer_b() -> None:
            sb = Storage(ledger_dir, writer_id="w-B")
            sb.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(
                    phase_id="pB", title="B"
                ),
                run_id="r",
            )
            sb.append(
                event_type=EventType.COMMIT_LANDED,
                payload=_commit_payload(attributed_phase_id=None),
                run_id="r",
            )
            sb.append(
                event_type=EventType.ASSUMPTION_DECLARED,
                payload=make_assumption_declared_payload(
                    assumption_id="a-B",
                    statement="x",
                    confidence=AssumptionConfidence.LOW,
                ),
                run_id="r",
            )
            sb.append(
                event_type=EventType.ASSUMPTION_FALSIFIED,
                payload=make_assumption_falsified_payload(
                    assumption_id="a-B",
                    evidence_event_id=uuid7(),
                    summary="no",
                ),
                run_id="r",
            )

        ta = threading.Thread(target=writer_a)
        tb = threading.Thread(target=writer_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        state, events = _round_trip(ledger_dir)
        return evaluate_thresholds(state, events, ThresholdParams())

    def test_concurrent_run_fires_expected_rules(self, tmp_path: Path) -> None:
        crossings = self._concurrent_run(tmp_path)
        rule_ids = {c.rule_id for c in crossings}
        # Writer A fires phase_abandoned + invariant_declared.
        # Writer B fires unattributable_commit + assumption_falsified.
        assert ThresholdRuleId.PHASE_ABANDONED in rule_ids
        assert ThresholdRuleId.INVARIANT_DECLARED in rule_ids
        assert ThresholdRuleId.UNATTRIBUTABLE_COMMIT in rule_ids
        assert ThresholdRuleId.ASSUMPTION_FALSIFIED in rule_ids


# ---------------------------------------------------------------------
# Scenario 3: since cursor across multi-writer events
# ---------------------------------------------------------------------


class TestSinceAcrossWriters:
    def test_since_writer_a_only_writer_b_events_fire(
        self, tmp_path: Path
    ) -> None:
        # Writer A first, then writer B. Because UUIDv7 is time-
        # ordered and writer B emits strictly after writer A within a
        # single process, writer B's event_ids > writer A's.
        sa = Storage(tmp_path, writer_id="w-A")
        a_events = [
            sa.append(
                event_type=EventType.COMMIT_LANDED,
                payload=_commit_payload(
                    attributed_phase_id=None, commit=f"a000{i:04d}"
                ),
                run_id="r",
            )
            for i in range(2)
        ]
        sb = Storage(tmp_path, writer_id="w-B")
        b_events = [
            sb.append(
                event_type=EventType.COMMIT_LANDED,
                payload=_commit_payload(
                    attributed_phase_id=None, commit=f"b000{i:04d}"
                ),
                run_id="r",
            )
            for i in range(2)
        ]

        state, events = _round_trip(tmp_path)
        # Use the LAST writer-A event_id as `since`. Crossings for
        # writer-A events must be suppressed; writer-B's must remain.
        since = a_events[-1].event_id
        crossings = evaluate_thresholds(
            state, events, ThresholdParams(), since=since
        )
        unattributable = [
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.UNATTRIBUTABLE_COMMIT
        ]
        evidence_ids = {c.evidence_event_ids[0] for c in unattributable}
        for ev in a_events:
            assert ev.event_id not in evidence_ids
        for ev in b_events:
            assert ev.event_id in evidence_ids


# ---------------------------------------------------------------------
# Scenario 4: round-trip preserves attributed_phase_id == None
# ---------------------------------------------------------------------


class TestStorageRoundTripAttribution:
    def test_unattributed_commit_survives_jsonl_round_trip(
        self, tmp_path: Path
    ) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        ev = s.append(
            event_type=EventType.COMMIT_LANDED,
            payload=_commit_payload(attributed_phase_id=None),
            run_id="r",
        )
        # Read back from disk via a fresh Storage instance; the
        # persisted JSON must round-trip the explicit None.
        reader = Storage(tmp_path, writer_id="reader")
        reread = reader.read_all()
        assert len(reread) == 1
        assert reread[0].payload["attributed_phase_id"] is None

        state = project(reread)
        crossings = evaluate_thresholds(state, reread, ThresholdParams())
        unattributable = [
            c
            for c in crossings
            if c.rule_id is ThresholdRuleId.UNATTRIBUTABLE_COMMIT
        ]
        assert len(unattributable) == 1
        assert unattributable[0].evidence_event_ids == (ev.event_id,)
        assert (
            unattributable[0].recommended_action
            is ThresholdRecommendedAction.REAUTHOR_PLAN
        )
        assert (
            unattributable[0].severity is ThresholdSeverity.TRIGGER_REAUTHOR
        )


# ---------------------------------------------------------------------
# Scenario 5: reserved events validate, persist, project as no-ops
# ---------------------------------------------------------------------


class TestReservedEventsThroughStorage:
    def test_reserved_events_persist_and_do_not_trigger_crossings(
        self, tmp_path: Path
    ) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        s.append(
            event_type=EventType.THRESHOLD_CROSSED,
            payload=make_threshold_crossed_payload(
                rule_id="rule-1",
                triggering_event_ids=[uuid7()],
                summary="rule fired",
            ),
            run_id="r",
        )
        s.append(
            event_type=EventType.PLAN_REAUTHORED,
            payload=make_plan_reauthored_payload(
                from_plan_commit="abcd1234",
                to_plan_commit="efgh5678",
                ledger_slice_event_ids=[uuid7()],
                trigger_event_id=uuid7(),
            ),
            run_id="r",
        )
        # An active-rule event mixed in so the test is not
        # accidentally trivially empty.
        invariant_ev = s.append(
            event_type=EventType.INVARIANT_DECLARED,
            payload=make_invariant_declared_payload(
                invariant_id="inv-1", statement="x", source="m"
            ),
            run_id="r",
        )

        state, events = _round_trip(tmp_path)
        assert len(events) == 3

        crossings = evaluate_thresholds(state, events, ThresholdParams())
        # Exactly one crossing -- from the invariant. The two
        # reserved events validate, persist on disk, project as
        # no-ops, and produce no crossings.
        assert len(crossings) == 1
        assert crossings[0].rule_id is ThresholdRuleId.INVARIANT_DECLARED
        assert crossings[0].evidence_event_ids == (invariant_ev.event_id,)


# ---------------------------------------------------------------------
# Scenario 6: count-based rule across writer boundaries
# ---------------------------------------------------------------------


class TestCountAcrossWriters:
    def test_rule_7_fires_once_at_limit_th_commit_across_writers(
        self, tmp_path: Path
    ) -> None:
        sa = Storage(tmp_path, writer_id="w-A")
        sb = Storage(tmp_path, writer_id="w-B")

        # Three exploratory commits from A, then two from B; the
        # 5th overall (B's second) should trigger rule 7.
        emitted: list[Any] = []
        for i in range(3):
            emitted.append(
                sa.append(
                    event_type=EventType.COMMIT_LANDED,
                    payload=_commit_payload(
                        attributed_phase_id=None, commit=f"a000{i:04d}"
                    ),
                    run_id="r",
                )
            )
        for i in range(2):
            emitted.append(
                sb.append(
                    event_type=EventType.COMMIT_LANDED,
                    payload=_commit_payload(
                        attributed_phase_id=None, commit=f"b000{i:04d}"
                    ),
                    run_id="r",
                )
            )

        state, events = _round_trip(tmp_path)
        params = ThresholdParams(
            exploratory_commit_limit=5,
            enabled_rules=frozenset(
                {ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED}
            ),
        )
        crossings = evaluate_thresholds(state, events, params)
        assert len(crossings) == 1
        c = crossings[0]
        assert c.rule_id is ThresholdRuleId.EXPLORATORY_COUNT_EXCEEDED
        # The 5th exploratory commit overall (sorted by event_id) is
        # the trigger. With UUIDv7's time-ordering plus single-process
        # sequencing, this is the last commit emitted (B's second).
        ordered = sorted(emitted, key=lambda e: e.event_id)
        assert c.evidence_event_ids == (ordered[4].event_id,)
        assert c.detected_at_event_id == ordered[4].event_id
