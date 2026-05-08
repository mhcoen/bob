"""Tests for Slice A event types, envelope, and JSON Schema validation.

Coverage targets:

  - UUIDv7 generator produces well-formed IDs that survive
    ``is_well_formed_event_id`` and exhibit time-ordered prefixes.
  - Every active event type round-trips through ``Event.to_json``
    -> ``Event.from_json`` losslessly.
  - Each payload builder produces a payload that validates against
    the per-type JSON Schema; the combined event schema accepts a
    correct envelope+payload pair.
  - Negative cases: missing required envelope fields, wrong type,
    bad payload shape, unknown event type, additional payload
    fields, malformed event_id.
  - Reserved event types (threshold_crossed, plan_reauthored)
    validate too: Slice B writers can emit them now and have them
    survive the Slice A check.
  - design_reasoning_recorded REQUIRES linked_event_id (the Slice A
    tightening).
  - commit_landed accepts the change_class enum and the optional
    touched_paths field.
"""

from __future__ import annotations

import time
from itertools import pairwise
from typing import Any

import pytest

from bob.ledger import (
    ACTIVE_EVENT_TYPES,
    RESERVED_EVENT_TYPES,
    SCHEMA_VERSION,
    AssumptionConfidence,
    CommitChangeClass,
    Event,
    EventSchemaError,
    EventType,
    GitSnapshot,
    RejectedApproach,
    is_well_formed_event_id,
    iter_validation_errors,
    validate_event,
    validate_event_id_format,
)
from bob.ledger._uuid7 import uuid7
from bob.ledger.events import (
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
# Helpers
# ---------------------------------------------------------------------


def _envelope(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    seq: int = 0,
    writer_id: str = "writer-A",
    run_id: str = "run-X",
    git: GitSnapshot | None = None,
) -> dict[str, Any]:
    return {
        "event_id": uuid7(),
        "seq": seq,
        "ts": "2026-05-08T06:00:00.000000Z",
        "writer_id": writer_id,
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "type": event_type.value,
        "git": (git or GitSnapshot.empty()).to_json(),
        "payload": payload,
    }


# ---------------------------------------------------------------------
# UUIDv7
# ---------------------------------------------------------------------


class TestUuid7:
    def test_format_is_canonical_36_char_with_hyphens(self) -> None:
        u = uuid7()
        assert len(u) == 36
        assert u[8] == "-" and u[13] == "-" and u[18] == "-" and u[23] == "-"

    def test_version_nibble_is_7(self) -> None:
        u = uuid7()
        # 13th hex digit (after the first hyphen) is the version nibble
        # in the canonical 8-4-4-4-12 layout.
        assert u[14] == "7"

    def test_variant_high_bits_are_10(self) -> None:
        u = uuid7()
        # 17th hex digit (after the second hyphen) carries the
        # variant: top two bits must be 10, so the nibble is 8/9/a/b.
        assert u[19] in {"8", "9", "a", "b"}

    def test_is_well_formed_event_id_accepts(self) -> None:
        for _ in range(50):
            assert is_well_formed_event_id(uuid7())

    def test_is_well_formed_rejects_v4(self) -> None:
        # A v4-style UUID; version nibble is 4.
        assert not is_well_formed_event_id(
            "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        )

    def test_time_prefix_is_monotonic_within_burst(self) -> None:
        ids = [uuid7() for _ in range(64)]
        # Strict monotonic for IDs from one process.
        for a, b in pairwise(ids):
            assert a < b, (a, b)

    def test_time_prefix_advances_across_milliseconds(self) -> None:
        first = uuid7()
        time.sleep(0.005)
        second = uuid7()
        # Earlier ID sorts before later one even though randomness
        # differs.
        assert first < second

    def test_validate_event_id_format_raises_on_v4(self) -> None:
        with pytest.raises(EventSchemaError, match="UUIDv7"):
            validate_event_id_format("f47ac10b-58cc-4372-a567-0e02b2c3d479")


# ---------------------------------------------------------------------
# Event dataclass round-trip
# ---------------------------------------------------------------------


class TestEventRoundTrip:
    def test_round_trip_phase_started(self) -> None:
        ev = Event(
            event_id=uuid7(),
            seq=0,
            ts="2026-05-08T06:00:00.000000Z",
            writer_id="w-1",
            run_id="r-1",
            type=EventType.PHASE_STARTED,
            git=GitSnapshot.empty(),
            payload=make_phase_started_payload(
                phase_id="p1", title="Bring up scaffold"
            ),
        )
        line = ev.to_jsonl()
        round = Event.from_jsonl(line)
        assert round == ev

    def test_round_trip_through_to_json(self) -> None:
        ev = Event(
            event_id=uuid7(),
            seq=3,
            ts="2026-05-08T06:00:00.000000Z",
            writer_id="w-2",
            run_id="r-2",
            type=EventType.COMMIT_LANDED,
            git=GitSnapshot(
                commit="abc1234567",
                branch="main",
                dirty=False,
                worktree="/repo",
            ),
            payload=make_commit_landed_payload(
                commit="abc1234567",
                parent_commits=["def4567890"],
                branch="main",
                author="michael",
                subject="bring up scaffold",
                attributed_phase_id="p1",
                files_changed=4,
                lines_added=120,
                lines_removed=10,
                change_class=CommitChangeClass.CODE,
                touched_paths=["src/a.py", "src/b.py"],
            ),
        )
        round = Event.from_json(ev.to_json())
        assert round == ev


# ---------------------------------------------------------------------
# Schema validation: positive cases for every event type
# ---------------------------------------------------------------------


class TestPayloadValidation:
    def test_active_and_reserved_types_cover_18(self) -> None:
        # Pin the count so future additions force a conscious change
        # to this contract.
        assert len(ACTIVE_EVENT_TYPES) == 16
        assert len(RESERVED_EVENT_TYPES) == 2
        assert ACTIVE_EVENT_TYPES.isdisjoint(RESERVED_EVENT_TYPES)

    def test_phase_started_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="p1", title="x"),
            )
        )

    def test_phase_completed_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_COMPLETED,
                make_phase_completed_payload(phase_id="p1"),
            )
        )

    def test_phase_abandoned_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_ABANDONED,
                make_phase_abandoned_payload(phase_id="p1", reason="r"),
            )
        )

    def test_phase_blocked_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_BLOCKED,
                make_phase_blocked_payload(phase_id="p1", reason="r"),
            )
        )

    def test_phase_superseded_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_SUPERSEDED,
                make_phase_superseded_payload(
                    phase_id="p1",
                    superseded_by_phase_id="p2",
                    reason="r",
                ),
            )
        )

    def test_phase_split_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_SPLIT,
                make_phase_split_payload(
                    phase_id="p1", into_phase_ids=["p1a", "p1b"], reason="r"
                ),
            )
        )

    def test_phase_split_rejects_single_target(self) -> None:
        with pytest.raises(EventSchemaError):
            validate_event(
                _envelope(
                    EventType.PHASE_SPLIT,
                    make_phase_split_payload(
                        phase_id="p1", into_phase_ids=["only"], reason="r"
                    ),
                )
            )

    def test_phase_merged_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_MERGED,
                make_phase_merged_payload(
                    merged_phase_ids=["a", "b"],
                    into_phase_id="ab",
                    reason="r",
                ),
            )
        )

    def test_commit_landed_validates_with_change_class_and_touched_paths(
        self,
    ) -> None:
        validate_event(
            _envelope(
                EventType.COMMIT_LANDED,
                make_commit_landed_payload(
                    commit="abc12345",
                    parent_commits=[],
                    branch=None,
                    author="m",
                    subject="initial",
                    attributed_phase_id=None,
                    files_changed=0,
                    lines_added=0,
                    lines_removed=0,
                    change_class=CommitChangeClass.MIXED,
                    touched_paths=["a", "b"],
                ),
            )
        )

    def test_commit_landed_rejects_unknown_change_class(self) -> None:
        env = _envelope(
            EventType.COMMIT_LANDED,
            make_commit_landed_payload(
                commit="abc12345",
                parent_commits=[],
                branch=None,
                author="m",
                subject="initial",
                attributed_phase_id=None,
                files_changed=0,
                lines_added=0,
                lines_removed=0,
                change_class=CommitChangeClass.CODE,
            ),
        )
        env["payload"]["change_class"] = "wat"
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_commit_landed_change_class_is_required(self) -> None:
        env = _envelope(
            EventType.COMMIT_LANDED,
            make_commit_landed_payload(
                commit="abc12345",
                parent_commits=[],
                branch=None,
                author="m",
                subject="initial",
                attributed_phase_id=None,
                files_changed=0,
                lines_added=0,
                lines_removed=0,
                change_class=CommitChangeClass.CODE,
            ),
        )
        del env["payload"]["change_class"]
        with pytest.raises(EventSchemaError, match="change_class"):
            validate_event(env)

    def test_test_failed_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.TEST_FAILED,
                make_test_failed_payload(
                    test_id="t1",
                    phase_id="p1",
                    failure_kind="assertion",
                    summary="expected 2 got 3",
                ),
            )
        )

    def test_finding_observed_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.FINDING_OBSERVED,
                make_finding_observed_payload(
                    summary="something off", tags=["unattributed"]
                ),
            )
        )

    def test_invariant_declared_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.INVARIANT_DECLARED,
                make_invariant_declared_payload(
                    invariant_id="inv-1",
                    statement="x must be y",
                    source="human:michael",
                ),
            )
        )

    def test_assumption_declared_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.ASSUMPTION_DECLARED,
                make_assumption_declared_payload(
                    assumption_id="a-1",
                    statement="x is fine",
                    confidence=AssumptionConfidence.MEDIUM,
                ),
            )
        )

    def test_assumption_falsified_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.ASSUMPTION_FALSIFIED,
                make_assumption_falsified_payload(
                    assumption_id="a-1",
                    evidence_event_id=uuid7(),
                    summary="actually not",
                ),
            )
        )

    def test_work_observed_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.WORK_OBSERVED,
                make_work_observed_payload(summary="ad hoc fix"),
            )
        )

    def test_human_decision_recorded_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.HUMAN_DECISION_RECORDED,
                make_human_decision_recorded_payload(
                    decision_id="d-1",
                    summary="go with option B",
                    rationale="cheaper",
                    decided_by="michael",
                    applies_to_phase_ids=["p1"],
                ),
            )
        )


# ---------------------------------------------------------------------
# Slice A tightening: design_reasoning_recorded MUST carry linked_event_id
# ---------------------------------------------------------------------


class TestDesignReasoningTightening:
    def test_validates_with_linked_event_id(self) -> None:
        validate_event(
            _envelope(
                EventType.DESIGN_REASONING_RECORDED,
                make_design_reasoning_recorded_payload(
                    decision_id="d-1",
                    linked_event_id=uuid7(),
                    rationale="we picked X because Y",
                    constraints=["fast", "cheap"],
                    approaches_rejected=[
                        RejectedApproach(approach="kitchen sink", reason="slow"),
                    ],
                ),
            )
        )

    def test_rejects_missing_linked_event_id(self) -> None:
        env = _envelope(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id="placeholder-event-id",
                rationale="r",
            ),
        )
        del env["payload"]["linked_event_id"]
        with pytest.raises(EventSchemaError, match="linked_event_id"):
            validate_event(env)

    def test_rejects_null_linked_event_id(self) -> None:
        env = _envelope(
            EventType.DESIGN_REASONING_RECORDED,
            make_design_reasoning_recorded_payload(
                decision_id="d-1",
                linked_event_id="placeholder-event-id",
                rationale="r",
            ),
        )
        env["payload"]["linked_event_id"] = None
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_rejected_approach_requires_both_fields(self) -> None:
        env = _envelope(
            EventType.DESIGN_REASONING_RECORDED,
            {
                "decision_id": "d-1",
                "linked_event_id": uuid7(),
                "rationale": "r",
                "constraints": [],
                "approaches_rejected": [{"approach": "x"}],
            },
        )
        with pytest.raises(EventSchemaError):
            validate_event(env)


# ---------------------------------------------------------------------
# Reserved event types validate
# ---------------------------------------------------------------------


class TestReservedEvents:
    def test_threshold_crossed_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.THRESHOLD_CROSSED,
                make_threshold_crossed_payload(
                    rule_id="rule-1",
                    triggering_event_ids=[uuid7()],
                    summary="some rule fired",
                ),
            )
        )

    def test_plan_reauthored_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PLAN_REAUTHORED,
                make_plan_reauthored_payload(
                    from_plan_commit="abcd1234",
                    to_plan_commit="efgh5678",
                    ledger_slice_event_ids=[uuid7(), uuid7()],
                    trigger_event_id=uuid7(),
                ),
            )
        )

    def test_plan_reauthored_council_run_id_nullable(self) -> None:
        env = _envelope(
            EventType.PLAN_REAUTHORED,
            make_plan_reauthored_payload(
                from_plan_commit="abcd1234",
                to_plan_commit="efgh5678",
                ledger_slice_event_ids=[uuid7()],
                trigger_event_id=uuid7(),
            ),
        )
        validate_event(env)
        env["payload"]["council_run_id"] = None
        validate_event(env)


# ---------------------------------------------------------------------
# Negative cases: envelope shape
# ---------------------------------------------------------------------


class TestEnvelopeNegatives:
    def test_unknown_event_type_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        env["type"] = "phase_quantum_entangled"
        with pytest.raises(EventSchemaError, match="type"):
            validate_event(env)

    def test_missing_required_envelope_field_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        del env["writer_id"]
        with pytest.raises(EventSchemaError, match="writer_id"):
            validate_event(env)

    def test_negative_seq_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
            seq=-1,
        )
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_extra_envelope_field_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        env["extra"] = "no"
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_extra_payload_field_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        env["payload"]["bogus"] = True
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_bad_event_id_rejected(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        env["event_id"] = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        with pytest.raises(EventSchemaError):
            validate_event(env)

    def test_schema_version_must_match(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        env["schema_version"] = "0.9"
        with pytest.raises(EventSchemaError):
            validate_event(env)


class TestGitBlock:
    def test_empty_git_block_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="p1", title="t"),
                git=GitSnapshot.empty(),
            )
        )

    def test_populated_git_block_validates(self) -> None:
        validate_event(
            _envelope(
                EventType.PHASE_STARTED,
                make_phase_started_payload(phase_id="p1", title="t"),
                git=GitSnapshot(
                    commit="abcd1234567890",
                    branch="main",
                    dirty=True,
                    worktree="/tmp/wt",
                ),
            )
        )

    def test_git_block_is_required(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        del env["git"]
        with pytest.raises(EventSchemaError, match="git"):
            validate_event(env)

    def test_iter_validation_errors_returns_list(self) -> None:
        env = _envelope(
            EventType.PHASE_STARTED,
            make_phase_started_payload(phase_id="p1", title="t"),
        )
        del env["git"]
        del env["writer_id"]
        errors = iter_validation_errors(env)
        assert errors
        # Every entry is a string with a path-like prefix.
        for e in errors:
            assert ":" in e
