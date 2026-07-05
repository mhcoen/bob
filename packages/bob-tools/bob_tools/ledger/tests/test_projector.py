"""Tests for the Plan Ledger projector.

Coverage:

  - Empty event log -> empty PlanState.
  - Each phase lifecycle event drives the right state transition.
  - Lazy `active` activation via the first attributed
    ``commit_landed`` or ``work_observed``.
  - Lifecycle events for unknown phase ids are no-ops (do not error).
  - Evidence routing: phase-attributed evidence attaches; unattributed
    findings land in ``findings_unattributed``.
  - Invariants, assumptions (including falsification), and human
    decisions populate their top-level lists / records.
  - design_reasoning_recorded events resolve to phases via
    ``linked_event_id``; orphans land in ``orphaned_design_reasoning``
    with the count field reflecting the list length.
  - Reserved event types validate (covered in test_events.py) and
    advance high-water marks but otherwise do not change projected
    state.
  - The four mandatory determinism invariants:
      project(shuffle(events)) == project(events)
      ts changes do not change projected state
      writers with close ts but distinct seq replay deterministically
      reserved events change only last_event_seq_per_writer
  - PlanState.to_json -> from_json round-trips.
"""

from __future__ import annotations

import copy
import json
import random
from typing import Any

from bob_tools.ledger import (
    AssumptionConfidence,
    CommitChangeClass,
    Event,
    EventType,
    GitSnapshot,
    PhaseStatus,
    PlanState,
    project,
)
from bob_tools.ledger._uuid7 import uuid7
from bob_tools.ledger.events import (
    RejectedApproach,
    make_assumption_declared_payload,
    make_assumption_falsified_payload,
    make_commit_landed_payload,
    make_design_reasoning_recorded_payload,
    make_finding_observed_payload,
    make_human_decision_recorded_payload,
    make_invariant_declared_payload,
    make_phase_abandoned_payload,
    make_phase_blocked_payload,
    make_phase_completed_payload,
    make_phase_merged_payload,
    make_phase_split_payload,
    make_phase_started_payload,
    make_phase_superseded_payload,
    make_plan_reauthored_payload,
    make_test_failed_payload,
    make_threshold_crossed_payload,
    make_work_observed_payload,
)

# ---------------------------------------------------------------------
# Event factory helpers
# ---------------------------------------------------------------------


_TS = "2026-05-08T06:00:00.000000Z"


def _make(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    seq: int = 0,
    writer_id: str = "writer-A",
    run_id: str = "run-X",
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
        author="test",
        subject="test commit",
        attributed_phase_id=attributed_phase_id,
        files_changed=1,
        lines_added=1,
        lines_removed=0,
        change_class=change_class,
    )


# ---------------------------------------------------------------------
# Empty + basic shape
# ---------------------------------------------------------------------


class TestEmpty:
    def test_empty_log_yields_empty_state(self) -> None:
        s = project([])
        assert s.last_event_id is None
        assert s.last_event_seq_per_writer == {}
        assert s.writer_ids_seen == []
        assert s.phases == []
        assert s.invariants == []
        assert s.assumptions == []
        assert s.human_decisions == []
        assert s.findings_unattributed == []
        assert s.orphaned_design_reasoning == []
        assert s.orphaned_design_reasoning_count == 0


# ---------------------------------------------------------------------
# Phase lifecycle
# ---------------------------------------------------------------------


class TestPhaseLifecycle:
    def test_phase_started_creates_pending(self) -> None:
        ev = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="Boot"),
        )
        s = project([ev])
        assert len(s.phases) == 1
        ph = s.phases[0]
        assert ph.id == "p1" and ph.title == "Boot"
        assert ph.status == PhaseStatus.PENDING
        assert ph.created_event_id == ev.event_id
        assert ph.modification_history == [ev.event_id]

    def test_phase_completed(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_COMPLETED,
            make_phase_completed_payload(phase_id="p1"),
            seq=1,
        )
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.COMPLETED
        assert s.phases[0].modification_history == [e1.event_id, e2.event_id]

    def test_phase_abandoned(self) -> None:
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
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.ABANDONED

    def test_phase_blocked(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.PHASE_BLOCKED,
            make_phase_blocked_payload(phase_id="p1", reason="r"),
            seq=1,
        )
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.BLOCKED

    def test_phase_superseded_lineage(self) -> None:
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
                phase_id="p1", superseded_by_phase_id="p2", reason="reframe"
            ),
            seq=2,
        )
        s = project([e1, e2, e3])
        by_id = {p.id: p for p in s.phases}
        assert by_id["p1"].status == PhaseStatus.SUPERSEDED
        assert by_id["p1"].lineage.supersession is not None
        assert by_id["p1"].lineage.supersession.superseded_by_id == "p2"
        assert "p2" in by_id["p1"].lineage.successors
        assert "p1" in by_id["p2"].lineage.predecessors

    def test_phase_split_lineage(self) -> None:
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
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.SPLIT
        assert set(s.phases[0].lineage.successors) == {"p1a", "p1b"}

    def test_phase_merged_lineage(self) -> None:
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
        s = project(evs)
        by_id = {p.id: p for p in s.phases}
        assert by_id["p1"].status == PhaseStatus.MERGED
        assert by_id["p2"].status == PhaseStatus.MERGED
        assert "merged" in by_id["p1"].lineage.successors
        assert set(by_id["merged"].lineage.predecessors) == {"p1", "p2"}

    def test_lifecycle_event_for_unknown_phase_is_noop(self) -> None:
        ev = _make(
            EventType.PHASE_COMPLETED,
            make_phase_completed_payload(phase_id="never-started"),
        )
        s = project([ev])
        assert s.phases == []
        # High-water marks still advanced.
        assert s.last_event_seq_per_writer == {"writer-A": 0}


# ---------------------------------------------------------------------
# Lazy active activation
# ---------------------------------------------------------------------


class TestLazyActivation:
    def test_first_attributed_commit_activates_phase(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id="p1"),
            seq=1,
        )
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.ACTIVE
        assert e2.event_id in s.phases[0].evidence_refs

    def test_first_attributed_work_activates_phase(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.WORK_OBSERVED,
            make_work_observed_payload(summary="ad hoc", phase_id="p1"),
            seq=1,
        )
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.ACTIVE

    def test_unattributed_commit_does_not_activate(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id=None),
            seq=1,
        )
        s = project([e1, e2])
        assert s.phases[0].status == PhaseStatus.PENDING


# ---------------------------------------------------------------------
# Evidence routing
# ---------------------------------------------------------------------


class TestEvidence:
    def test_test_failed_attaches(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.TEST_FAILED,
            make_test_failed_payload(
                test_id="t1",
                phase_id="p1",
                failure_kind="assertion",
                summary="bad",
            ),
            seq=1,
        )
        s = project([e1, e2])
        assert e2.event_id in s.phases[0].evidence_refs

    def test_finding_with_phase_attaches_to_phase(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.FINDING_OBSERVED,
            make_finding_observed_payload(summary="found", phase_id="p1"),
            seq=1,
        )
        s = project([e1, e2])
        assert e2.event_id in s.phases[0].evidence_refs
        assert s.findings_unattributed == []

    def test_finding_without_phase_goes_to_top(self) -> None:
        ev = _make(
            EventType.FINDING_OBSERVED,
            make_finding_observed_payload(summary="orphan finding"),
        )
        s = project([ev])
        assert s.findings_unattributed == [ev.event_id]

    def test_finding_with_unknown_phase_goes_to_top(self) -> None:
        ev = _make(
            EventType.FINDING_OBSERVED,
            make_finding_observed_payload(summary="x", phase_id="ghost"),
        )
        s = project([ev])
        assert s.findings_unattributed == [ev.event_id]


# ---------------------------------------------------------------------
# Invariants, assumptions, human decisions
# ---------------------------------------------------------------------


class TestInvariantsAssumptionsDecisions:
    def test_invariant_recorded(self) -> None:
        ev = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="inv-1",
                statement="x must y",
                source="human:michael",
            ),
        )
        s = project([ev])
        assert len(s.invariants) == 1
        assert s.invariants[0].invariant_id == "inv-1"
        assert s.invariants[0].declared_event_id == ev.event_id

    def test_assumption_recorded_falsified_false(self) -> None:
        ev = _make(
            EventType.ASSUMPTION_DECLARED,
            make_assumption_declared_payload(
                assumption_id="a-1",
                statement="x is fine",
                confidence=AssumptionConfidence.MEDIUM,
            ),
        )
        s = project([ev])
        assert len(s.assumptions) == 1
        assert s.assumptions[0].falsified is False
        assert s.assumptions[0].falsified_event_id is None

    def test_assumption_falsified_updates_record(self) -> None:
        e1 = _make(
            EventType.ASSUMPTION_DECLARED,
            make_assumption_declared_payload(
                assumption_id="a-1",
                statement="x is fine",
                confidence=AssumptionConfidence.HIGH,
            ),
            seq=0,
        )
        evidence_id = uuid7()
        e2 = _make(
            EventType.ASSUMPTION_FALSIFIED,
            make_assumption_falsified_payload(
                assumption_id="a-1",
                evidence_event_id=evidence_id,
                summary="actually no",
            ),
            seq=1,
        )
        s = project([e1, e2])
        assert s.assumptions[0].falsified is True
        assert s.assumptions[0].falsified_event_id == evidence_id
        assert s.assumptions[0].falsified_summary == "actually no"

    def test_assumption_falsified_unknown_assumption_is_noop(self) -> None:
        ev = _make(
            EventType.ASSUMPTION_FALSIFIED,
            make_assumption_falsified_payload(
                assumption_id="ghost",
                evidence_event_id=uuid7(),
                summary="x",
            ),
        )
        s = project([ev])
        assert s.assumptions == []

    def test_human_decision_recorded(self) -> None:
        ev = _make(
            EventType.HUMAN_DECISION_RECORDED,
            make_human_decision_recorded_payload(
                decision_id="d-1",
                summary="go with B",
                rationale="cheaper",
                decided_by="michael",
                applies_to_phase_ids=["p1"],
            ),
        )
        s = project([ev])
        assert len(s.human_decisions) == 1
        assert s.human_decisions[0].decision_id == "d-1"


# ---------------------------------------------------------------------
# Design reasoning resolution
# ---------------------------------------------------------------------


class TestDesignReasoning:
    def test_resolves_to_linked_phase_started(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id=e1.event_id,
                rationale="why X",
                approaches_rejected=[
                    RejectedApproach(approach="big bang", reason="risk"),
                ],
            ),
            seq=1,
        )
        s = project([e1, e2])
        assert s.orphaned_design_reasoning == []
        assert e2.event_id in s.phases[0].design_reasoning_refs

    def test_resolves_to_attributed_commit(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="x"),
            seq=0,
        )
        e2 = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id="p1"),
            seq=1,
        )
        e3 = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id=e2.event_id,
                rationale="this commit's choice",
            ),
            seq=2,
        )
        s = project([e1, e2, e3])
        assert s.orphaned_design_reasoning == []
        assert e3.event_id in s.phases[0].design_reasoning_refs

    def test_orphans_when_link_is_missing(self) -> None:
        ev = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id=uuid7(),
                rationale="dangling",
            ),
        )
        s = project([ev])
        assert s.orphaned_design_reasoning == [ev.event_id]
        assert s.orphaned_design_reasoning_count == 1

    def test_orphans_when_link_is_unattributed_commit(self) -> None:
        e1 = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id=None),
            seq=0,
        )
        e2 = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id=e1.event_id,
                rationale="linked to unattributed commit",
            ),
            seq=1,
        )
        s = project([e1, e2])
        assert s.orphaned_design_reasoning == [e2.event_id]

    def test_orphans_when_human_decision_has_multiple_phases(self) -> None:
        e1 = _make(
            EventType.HUMAN_DECISION_RECORDED,
            make_human_decision_recorded_payload(
                decision_id="d-1",
                summary="x",
                rationale="r",
                decided_by="m",
                applies_to_phase_ids=["p1", "p2"],
            ),
            seq=0,
        )
        e2 = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id=e1.event_id,
                rationale="ambiguous attribution",
            ),
            seq=1,
        )
        s = project([e1, e2])
        assert s.orphaned_design_reasoning == [e2.event_id]

    def test_orphan_count_matches_list_length(self) -> None:
        evs = [
            _make(
                EventType.DESIGN_REASONING_RECORDED,
                make_design_reasoning_recorded_payload(
                    decision_id=f"d-{i}",
                    linked_event_id=uuid7(),
                    rationale="dangling",
                ),
                seq=i,
            )
            for i in range(3)
        ]
        s = project(evs)
        assert len(s.orphaned_design_reasoning) == 3
        assert s.orphaned_design_reasoning_count == 3


# ---------------------------------------------------------------------
# Reserved events: only advance high-water marks
# ---------------------------------------------------------------------


class TestReservedEvents:
    def test_threshold_crossed_no_state_change(self) -> None:
        ev = _make(
            EventType.THRESHOLD_CROSSED,
            make_threshold_crossed_payload(
                rule_id="r1",
                triggering_event_ids=[uuid7()],
                summary="rule fired",
            ),
        )
        s = project([ev])
        assert s.phases == []
        assert s.invariants == []
        assert s.assumptions == []
        assert s.human_decisions == []
        assert s.findings_unattributed == []
        assert s.orphaned_design_reasoning == []
        # High-water marks DO advance.
        assert s.last_event_id == ev.event_id
        assert s.last_event_seq_per_writer == {"writer-A": 0}

    def test_plan_reauthored_no_state_change(self) -> None:
        ev = _make(
            EventType.PLAN_REAUTHORED,
            make_plan_reauthored_payload(
                from_plan_commit="abcd1234",
                to_plan_commit="efgh5678",
                ledger_slice_event_ids=[uuid7()],
                trigger_event_id=uuid7(),
            ),
        )
        s = project([ev])
        assert s.phases == [] and s.invariants == []
        assert s.last_event_seq_per_writer == {"writer-A": 0}


# ---------------------------------------------------------------------
# Determinism contracts (mandatory per Codex)
# ---------------------------------------------------------------------


def _build_realistic_log() -> list[Event]:
    """An interleaved log spanning every active event type and both
    reserved types, across two writers. Used as a workhorse fixture
    for the determinism invariants below.
    """
    p_started = _make(
        EventType.PHASE_STARTED,
        make_phase_started_payload(phase_id="p1", title="boot"),
        seq=0,
        writer_id="writer-A",
    )
    p2_started = _make(
        EventType.PHASE_STARTED,
        make_phase_started_payload(phase_id="p2", title="follow"),
        seq=1,
        writer_id="writer-A",
    )
    commit = _make(
        EventType.COMMIT_LANDED,
        _commit_payload(attributed_phase_id="p1"),
        seq=0,
        writer_id="writer-B",
    )
    test = _make(
        EventType.TEST_FAILED,
        make_test_failed_payload(
            test_id="t1", phase_id="p1", failure_kind="x", summary="y"
        ),
        seq=2,
        writer_id="writer-A",
    )
    finding_attached = _make(
        EventType.FINDING_OBSERVED,
        make_finding_observed_payload(summary="found", phase_id="p2"),
        seq=1,
        writer_id="writer-B",
    )
    finding_unatt = _make(
        EventType.FINDING_OBSERVED,
        make_finding_observed_payload(summary="orphan"),
        seq=2,
        writer_id="writer-B",
    )
    invariant = _make(
        EventType.INVARIANT_DECLARED,
        make_invariant_declared_payload(
            invariant_id="inv-1",
            statement="x",
            source="m",
        ),
        seq=3,
        writer_id="writer-A",
    )
    a_decl = _make(
        EventType.ASSUMPTION_DECLARED,
        make_assumption_declared_payload(
            assumption_id="a-1",
            statement="x",
            confidence=AssumptionConfidence.LOW,
        ),
        seq=4,
        writer_id="writer-A",
    )
    a_fals = _make(
        EventType.ASSUMPTION_FALSIFIED,
        make_assumption_falsified_payload(
            assumption_id="a-1",
            evidence_event_id=uuid7(),
            summary="no",
        ),
        seq=3,
        writer_id="writer-B",
    )
    work = _make(
        EventType.WORK_OBSERVED,
        make_work_observed_payload(summary="ad hoc", phase_id="p2"),
        seq=4,
        writer_id="writer-B",
    )
    decision = _make(
        EventType.HUMAN_DECISION_RECORDED,
        make_human_decision_recorded_payload(
            decision_id="d-1",
            summary="go B",
            rationale="cheaper",
            decided_by="m",
            applies_to_phase_ids=["p1"],
        ),
        seq=5,
        writer_id="writer-A",
    )
    reasoning = _make(
        EventType.DESIGN_REASONING_RECORDED,
        make_design_reasoning_recorded_payload(
            decision_id="d-1",
            linked_event_id=decision.event_id,
            rationale="single-phase decision",
        ),
        seq=5,
        writer_id="writer-B",
    )
    threshold = _make(
        EventType.THRESHOLD_CROSSED,
        make_threshold_crossed_payload(
            rule_id="r-1",
            triggering_event_ids=[finding_unatt.event_id],
            summary="fired",
        ),
        seq=6,
        writer_id="writer-A",
    )
    superseded = _make(
        EventType.PHASE_SUPERSEDED,
        make_phase_superseded_payload(
            phase_id="p1", superseded_by_phase_id="p2", reason="reframe"
        ),
        seq=7,
        writer_id="writer-A",
    )
    completed = _make(
        EventType.PHASE_COMPLETED,
        make_phase_completed_payload(phase_id="p2"),
        seq=8,
        writer_id="writer-A",
    )
    return [
        p_started,
        p2_started,
        commit,
        test,
        finding_attached,
        finding_unatt,
        invariant,
        a_decl,
        a_fals,
        work,
        decision,
        reasoning,
        threshold,
        superseded,
        completed,
    ]


class TestDeterminism:
    def test_shuffle_invariant(self) -> None:
        events = _build_realistic_log()
        baseline = project(events)
        rng = random.Random(7)
        for _ in range(20):
            shuffled = list(events)
            rng.shuffle(shuffled)
            assert project(shuffled) == baseline

    def test_ts_does_not_affect_state(self) -> None:
        events = _build_realistic_log()
        baseline = project(events)
        # Reverse the apparent ts ordering: replace each ts with one
        # that decreases as event_id increases. State must stay equal.
        events_with_inverted_ts: list[Event] = []
        for i, ev in enumerate(events):
            new_ts = f"2026-05-08T06:00:{59 - i:02d}.000000Z"
            events_with_inverted_ts.append(
                Event(
                    event_id=ev.event_id,
                    seq=ev.seq,
                    ts=new_ts,
                    writer_id=ev.writer_id,
                    run_id=ev.run_id,
                    type=ev.type,
                    git=ev.git,
                    payload=ev.payload,
                    schema_version=ev.schema_version,
                )
            )
        assert project(events_with_inverted_ts) == baseline

    def test_close_ts_different_writers_deterministic(self) -> None:
        events = _build_realistic_log()
        baseline = project(events)
        # Force every event to share the same ts to within microsecond
        # resolution. Replay key is (event_id, writer_id, seq); state
        # must be identical.
        identical_ts_events = [
            Event(
                event_id=ev.event_id,
                seq=ev.seq,
                ts="2026-05-08T06:00:00.000001Z",
                writer_id=ev.writer_id,
                run_id=ev.run_id,
                type=ev.type,
                git=ev.git,
                payload=ev.payload,
                schema_version=ev.schema_version,
            )
            for ev in events
        ]
        assert project(identical_ts_events) == baseline

    def test_reserved_events_only_advance_high_water_marks(self) -> None:
        # Workhorse fixture's reserved event sits in the middle of the
        # writer's seq range, so it's a content-only invariant there:
        # adding a reserved event that DOES advance a writer's high
        # water mark is the discriminating case. Construct one
        # explicitly: operational events on writer-A up to seq=4, plus
        # a reserved event on writer-C with seq=10 that pushes a new
        # high-water entry no operational event would create.
        operational = _build_realistic_log()
        late_reserved = _make(
            EventType.THRESHOLD_CROSSED,
            make_threshold_crossed_payload(
                rule_id="late",
                triggering_event_ids=[uuid7()],
                summary="late fire",
            ),
            seq=10,
            writer_id="writer-C",
        )
        with_reserved = project([*operational, late_reserved])
        without_reserved = project(operational)

        # Operational projection content is preserved.
        assert with_reserved.phases == without_reserved.phases
        assert with_reserved.invariants == without_reserved.invariants
        assert with_reserved.assumptions == without_reserved.assumptions
        assert with_reserved.human_decisions == without_reserved.human_decisions
        assert (
            with_reserved.findings_unattributed
            == without_reserved.findings_unattributed
        )
        assert (
            with_reserved.orphaned_design_reasoning
            == without_reserved.orphaned_design_reasoning
        )
        assert (
            with_reserved.orphaned_design_reasoning_count
            == without_reserved.orphaned_design_reasoning_count
        )
        # High-water marks DO differ: writer-C now appears with seq=10
        # solely because of the reserved event.
        assert "writer-C" in with_reserved.last_event_seq_per_writer
        assert "writer-C" not in without_reserved.last_event_seq_per_writer
        assert with_reserved.last_event_seq_per_writer["writer-C"] == 10
        assert "writer-C" in with_reserved.writer_ids_seen
        assert "writer-C" not in without_reserved.writer_ids_seen


# ---------------------------------------------------------------------
# T2 surfacing invariant: orphaned list and count must agree
# ---------------------------------------------------------------------


class TestOrphanSurfacing:
    def test_count_matches_list_after_arbitrary_replay(self) -> None:
        events = _build_realistic_log() + [
            _make(
                EventType.DESIGN_REASONING_RECORDED,
                make_design_reasoning_recorded_payload(
                    decision_id=f"dangling-{i}",
                    linked_event_id=uuid7(),
                    rationale="no link",
                ),
                seq=100 + i,
                writer_id="writer-C",
            )
            for i in range(4)
        ]
        s = project(events)
        assert len(s.orphaned_design_reasoning) == s.orphaned_design_reasoning_count
        assert s.orphaned_design_reasoning_count >= 4


# ---------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------


class TestStateSerialization:
    def test_to_json_from_json_roundtrip(self) -> None:
        events = _build_realistic_log()
        s = project(events)
        encoded = json.dumps(s.to_json(), sort_keys=True)
        decoded = PlanState.from_json(json.loads(encoded))
        assert decoded == s

    def test_state_equality_is_value_based(self) -> None:
        events = _build_realistic_log()
        a = project(events)
        b = project(copy.deepcopy(events))
        assert a == b


# ---------------------------------------------------------------------
# Idempotence: duplicated event lines apply exactly once
# ---------------------------------------------------------------------


class TestDuplicateEventDedup:
    """A duplicated event line (same event_id appended twice) must be
    applied exactly once across every record type, not just phases and
    assumptions. Without event_id dedup, list-append handlers
    (invariants, human_decisions, findings, evidence_refs,
    design_reasoning_refs) double-count and re-fire threshold crossings.
    """

    def test_duplicate_invariant_declared_counts_once(self) -> None:
        ev = _make(
            EventType.INVARIANT_DECLARED,
            make_invariant_declared_payload(
                invariant_id="inv-1",
                statement="x must hold",
                source="human",
            ),
        )
        s = project([ev, copy.deepcopy(ev)])
        assert len(s.invariants) == 1
        assert s.invariants[0].invariant_id == "inv-1"

    def test_duplicate_human_decision_counts_once(self) -> None:
        ev = _make(
            EventType.HUMAN_DECISION_RECORDED,
            make_human_decision_recorded_payload(
                decision_id="d-1",
                summary="ship it",
                rationale="because",
                decided_by="mike",
            ),
        )
        s = project([ev, copy.deepcopy(ev)])
        assert len(s.human_decisions) == 1
        assert s.human_decisions[0].decision_id == "d-1"

    def test_duplicate_unattributed_finding_counts_once(self) -> None:
        ev = _make(
            EventType.FINDING_OBSERVED,
            make_finding_observed_payload(summary="odd log line"),
        )
        s = project([ev, copy.deepcopy(ev)])
        assert s.findings_unattributed == [ev.event_id]

    def test_duplicate_commit_evidence_ref_counts_once(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="Boot"),
        )
        commit = _make(
            EventType.COMMIT_LANDED,
            _commit_payload(attributed_phase_id="p1"),
        )
        s = project([e1, commit, copy.deepcopy(commit)])
        assert len(s.phases) == 1
        assert s.phases[0].evidence_refs == [commit.event_id]

    def test_duplicate_design_reasoning_ref_counts_once(self) -> None:
        e1 = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="Boot"),
        )
        dr = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="dr-1",
                linked_event_id=e1.event_id,
                rationale="why",
            ),
        )
        s = project([e1, dr, copy.deepcopy(dr)])
        assert s.phases[0].design_reasoning_refs == [dr.event_id]
        assert s.orphaned_design_reasoning == []

    def test_duplicate_orphan_design_reasoning_counts_once(self) -> None:
        dr = _make(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="dr-1",
                linked_event_id=uuid7(),  # unresolvable link -> orphan
                rationale="why",
            ),
        )
        s = project([dr, copy.deepcopy(dr)])
        assert s.orphaned_design_reasoning == [dr.event_id]
        assert s.orphaned_design_reasoning_count == 1

    def test_duplicate_does_not_advance_extra_state(self) -> None:
        # A repeated line must not perturb the high-water marks beyond
        # what the single event already established.
        ev = _make(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="Boot"),
            seq=3,
            writer_id="writer-Z",
        )
        once = project([ev])
        twice = project([ev, copy.deepcopy(ev)])
        assert once == twice
