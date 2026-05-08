"""Plan Ledger event types and envelope.

The ledger writes one JSON object per line into ``PLAN.events.jsonl``.
Each line is an ``Event``: a stable envelope of identification and
provenance fields, plus a type-specific ``payload``. The envelope is
the part the projector and downstream consumers can rely on across
event types; the payload shape is governed by JSON Schema in
``schema.py``.

Replay ordering is by ``event_id`` (UUIDv7 time-prefix) with
``(writer_id, seq)`` as a deterministic tiebreaker. ``ts`` is for
human audit only, not for replay.

Slice A introduces sixteen active event types plus two reserved types
whose schemas are validated but whose semantics are deferred. The
projector applies the active types and ignores the reserved ones,
recording their occurrence in ``last_event_id`` only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from bob.ledger._uuid7 import is_uuid7

SCHEMA_VERSION = "1.0"


class EventType(StrEnum):
    """All Slice A event type names.

    The first sixteen are active: the projector applies them. The last
    two are reserved: their schemas validate, the projector records
    that they occurred but does not project state from them. They are
    Slice B's responsibility.
    """

    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    PHASE_ABANDONED = "phase_abandoned"
    PHASE_BLOCKED = "phase_blocked"
    PHASE_SUPERSEDED = "phase_superseded"
    PHASE_SPLIT = "phase_split"
    PHASE_MERGED = "phase_merged"
    COMMIT_LANDED = "commit_landed"
    TEST_FAILED = "test_failed"
    FINDING_OBSERVED = "finding_observed"
    INVARIANT_DECLARED = "invariant_declared"
    ASSUMPTION_DECLARED = "assumption_declared"
    ASSUMPTION_FALSIFIED = "assumption_falsified"
    WORK_OBSERVED = "work_observed"
    HUMAN_DECISION_RECORDED = "human_decision_recorded"
    DESIGN_REASONING_RECORDED = "design_reasoning_recorded"
    THRESHOLD_CROSSED = "threshold_crossed"
    PLAN_REAUTHORED = "plan_reauthored"


ACTIVE_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.PHASE_STARTED,
        EventType.PHASE_COMPLETED,
        EventType.PHASE_ABANDONED,
        EventType.PHASE_BLOCKED,
        EventType.PHASE_SUPERSEDED,
        EventType.PHASE_SPLIT,
        EventType.PHASE_MERGED,
        EventType.COMMIT_LANDED,
        EventType.TEST_FAILED,
        EventType.FINDING_OBSERVED,
        EventType.INVARIANT_DECLARED,
        EventType.ASSUMPTION_DECLARED,
        EventType.ASSUMPTION_FALSIFIED,
        EventType.WORK_OBSERVED,
        EventType.HUMAN_DECISION_RECORDED,
        EventType.DESIGN_REASONING_RECORDED,
    }
)


RESERVED_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.THRESHOLD_CROSSED,
        EventType.PLAN_REAUTHORED,
    }
)


class CommitChangeClass(StrEnum):
    """Classification for ``commit_landed`` payloads.

    Splits ``commit_landed`` events on what kind of artifact the
    commit primarily changed without proliferating top-level event
    types. ``mixed`` is for commits whose changes span more than one
    class; ``unknown`` is for commits whose class could not be
    classified at emit time.
    """

    CODE = "code"
    PLAN_ARTIFACT = "plan_artifact"
    TEST = "test"
    DOCS = "docs"
    INFRA = "infra"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AssumptionConfidence(StrEnum):
    """Confidence level on an ``assumption_declared`` event."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """Snapshot of git state captured at event-emit time.

    All fields are nullable: the consuming process may have no git
    context at all (e.g., events emitted from a one-off script in a
    non-checkout). The block itself is always present in the envelope
    so consumers do not have to special-case its absence.
    """

    commit: str | None
    branch: str | None
    dirty: bool | None
    worktree: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "branch": self.branch,
            "dirty": self.dirty,
            "worktree": self.worktree,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> GitSnapshot:
        return cls(
            commit=raw.get("commit"),
            branch=raw.get("branch"),
            dirty=raw.get("dirty"),
            worktree=raw.get("worktree"),
        )

    @classmethod
    def empty(cls) -> GitSnapshot:
        return cls(commit=None, branch=None, dirty=None, worktree=None)


@dataclass(frozen=True, slots=True)
class Event:
    """One event record on PLAN.events.jsonl.

    ``payload`` is type-specific and validated by JSON Schema; this
    class does not enforce payload shape on construction so a writer
    can build an event from whatever shape it has and let
    ``schema.validate_event`` decide whether the result conforms.
    """

    event_id: str
    seq: int
    ts: str
    writer_id: str
    run_id: str
    type: EventType
    git: GitSnapshot
    payload: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "ts": self.ts,
            "writer_id": self.writer_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "type": str(self.type),
            "git": self.git.to_json(),
            "payload": dict(self.payload),
        }

    def to_jsonl(self) -> str:
        """Return one JSON-encoded line, no trailing newline."""
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Event:
        return cls(
            event_id=str(raw["event_id"]),
            seq=int(raw["seq"]),
            ts=str(raw["ts"]),
            writer_id=str(raw["writer_id"]),
            run_id=str(raw["run_id"]),
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
            type=EventType(str(raw["type"])),
            git=GitSnapshot.from_json(raw.get("git", {})),
            payload=dict(raw.get("payload") or {}),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> Event:
        """Parse one JSONL line. Whitespace is tolerated."""
        return cls.from_json(json.loads(line))


def is_well_formed_event_id(event_id: str) -> bool:
    """Public predicate for envelope event_id validation.

    Wraps ``is_uuid7`` so consumers do not import the private module
    directly. Schema validation calls this through a custom format
    checker.
    """
    return is_uuid7(event_id)


# ---------------------------------------------------------------------
# Payload helpers (no shape enforcement; validation is done in schema)
# ---------------------------------------------------------------------


def make_phase_started_payload(
    *,
    phase_id: str,
    title: str,
    goal: str | None = None,
    predecessor_phase_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "title": title,
        "goal": goal,
        "predecessor_phase_ids": list(predecessor_phase_ids),
    }


def make_phase_completed_payload(
    *,
    phase_id: str,
    commit_event_ids: Sequence[str] = (),
    artifact_paths: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "commit_event_ids": list(commit_event_ids),
        "artifact_paths": list(artifact_paths),
    }


def make_phase_abandoned_payload(*, phase_id: str, reason: str) -> dict[str, Any]:
    return {"phase_id": phase_id, "reason": reason}


def make_phase_blocked_payload(
    *,
    phase_id: str,
    reason: str,
    blocker_event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "reason": reason,
        "blocker_event_id": blocker_event_id,
    }


def make_phase_superseded_payload(
    *,
    phase_id: str,
    superseded_by_phase_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "superseded_by_phase_id": superseded_by_phase_id,
        "reason": reason,
    }


def make_phase_split_payload(
    *,
    phase_id: str,
    into_phase_ids: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "into_phase_ids": list(into_phase_ids),
        "reason": reason,
    }


def make_phase_merged_payload(
    *,
    merged_phase_ids: Sequence[str],
    into_phase_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "merged_phase_ids": list(merged_phase_ids),
        "into_phase_id": into_phase_id,
        "reason": reason,
    }


def make_commit_landed_payload(
    *,
    commit: str,
    parent_commits: Sequence[str],
    branch: str | None,
    author: str,
    subject: str,
    attributed_phase_id: str | None,
    files_changed: int,
    lines_added: int,
    lines_removed: int,
    change_class: CommitChangeClass,
    touched_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "commit": commit,
        "parent_commits": list(parent_commits),
        "branch": branch,
        "author": author,
        "subject": subject,
        "attributed_phase_id": attributed_phase_id,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "change_class": str(change_class),
    }
    if touched_paths is not None:
        payload["touched_paths"] = list(touched_paths)
    return payload


def make_test_failed_payload(
    *,
    test_id: str,
    phase_id: str | None,
    failure_kind: str,
    summary: str,
    transcript_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "phase_id": phase_id,
        "failure_kind": failure_kind,
        "summary": summary,
        "transcript_ref": transcript_ref,
    }


def make_finding_observed_payload(
    *,
    summary: str,
    phase_id: str | None = None,
    evidence_ref: str | None = None,
    tags: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "summary": summary,
        "phase_id": phase_id,
        "evidence_ref": evidence_ref,
        "tags": list(tags),
    }


def make_invariant_declared_payload(
    *,
    invariant_id: str,
    statement: str,
    source: str,
    phase_id: str | None = None,
) -> dict[str, Any]:
    return {
        "invariant_id": invariant_id,
        "statement": statement,
        "source": source,
        "phase_id": phase_id,
    }


def make_assumption_declared_payload(
    *,
    assumption_id: str,
    statement: str,
    confidence: AssumptionConfidence,
    phase_id: str | None = None,
) -> dict[str, Any]:
    return {
        "assumption_id": assumption_id,
        "statement": statement,
        "phase_id": phase_id,
        "confidence": str(confidence),
    }


def make_assumption_falsified_payload(
    *,
    assumption_id: str,
    evidence_event_id: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "assumption_id": assumption_id,
        "evidence_event_id": evidence_event_id,
        "summary": summary,
    }


def make_work_observed_payload(
    *,
    summary: str,
    phase_id: str | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "summary": summary,
        "phase_id": phase_id,
        "evidence_ref": evidence_ref,
    }


def make_human_decision_recorded_payload(
    *,
    decision_id: str,
    summary: str,
    rationale: str,
    decided_by: str,
    applies_to_phase_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "summary": summary,
        "rationale": rationale,
        "decided_by": decided_by,
        "applies_to_phase_ids": list(applies_to_phase_ids),
    }


@dataclass(frozen=True, slots=True)
class RejectedApproach:
    """One ``approaches_rejected`` entry.

    The schema serialization is a JSON object ``{"approach", "reason"}``;
    this dataclass is a typed convenience for callers.
    """

    approach: str
    reason: str


def make_design_reasoning_recorded_payload(
    *,
    decision_id: str,
    linked_event_id: str,
    rationale: str,
    constraints: Sequence[str] = (),
    approaches_rejected: Sequence[RejectedApproach] = (),
) -> dict[str, Any]:
    """Build a ``design_reasoning_recorded`` payload.

    ``linked_event_id`` is required (not nullable) per the Slice A
    tightening: design reasoning must be attributable to a concrete
    earlier event so the projector can resolve it to a phase. Pass the
    ``event_id`` of the ``phase_started``, ``phase_superseded``, etc.
    that this reasoning explains.
    """
    return {
        "decision_id": decision_id,
        "linked_event_id": linked_event_id,
        "rationale": rationale,
        "constraints": list(constraints),
        "approaches_rejected": [asdict(r) for r in approaches_rejected],
    }


def make_threshold_crossed_payload(
    *,
    rule_id: str,
    triggering_event_ids: Sequence[str],
    summary: str,
) -> dict[str, Any]:
    """Reserved for Slice B. Schema validates; projector ignores at A."""
    return {
        "rule_id": rule_id,
        "triggering_event_ids": list(triggering_event_ids),
        "summary": summary,
    }


def make_plan_reauthored_payload(
    *,
    from_plan_commit: str,
    to_plan_commit: str,
    ledger_slice_event_ids: Sequence[str],
    trigger_event_id: str,
    council_run_id: str | None = None,
) -> dict[str, Any]:
    """Reserved for Slice B. Schema validates; projector ignores at A."""
    return {
        "from_plan_commit": from_plan_commit,
        "to_plan_commit": to_plan_commit,
        "ledger_slice_event_ids": list(ledger_slice_event_ids),
        "trigger_event_id": trigger_event_id,
        "council_run_id": council_run_id,
    }


# ---------------------------------------------------------------------
# Payload builder lookup
# ---------------------------------------------------------------------


PAYLOAD_BUILDERS: dict[EventType, str] = {
    EventType.PHASE_STARTED: "make_phase_started_payload",
    EventType.PHASE_COMPLETED: "make_phase_completed_payload",
    EventType.PHASE_ABANDONED: "make_phase_abandoned_payload",
    EventType.PHASE_BLOCKED: "make_phase_blocked_payload",
    EventType.PHASE_SUPERSEDED: "make_phase_superseded_payload",
    EventType.PHASE_SPLIT: "make_phase_split_payload",
    EventType.PHASE_MERGED: "make_phase_merged_payload",
    EventType.COMMIT_LANDED: "make_commit_landed_payload",
    EventType.TEST_FAILED: "make_test_failed_payload",
    EventType.FINDING_OBSERVED: "make_finding_observed_payload",
    EventType.INVARIANT_DECLARED: "make_invariant_declared_payload",
    EventType.ASSUMPTION_DECLARED: "make_assumption_declared_payload",
    EventType.ASSUMPTION_FALSIFIED: "make_assumption_falsified_payload",
    EventType.WORK_OBSERVED: "make_work_observed_payload",
    EventType.HUMAN_DECISION_RECORDED: "make_human_decision_recorded_payload",
    EventType.DESIGN_REASONING_RECORDED: "make_design_reasoning_recorded_payload",
    EventType.THRESHOLD_CROSSED: "make_threshold_crossed_payload",
    EventType.PLAN_REAUTHORED: "make_plan_reauthored_payload",
}


__all__ = [
    "ACTIVE_EVENT_TYPES",
    "PAYLOAD_BUILDERS",
    "RESERVED_EVENT_TYPES",
    "SCHEMA_VERSION",
    "AssumptionConfidence",
    "CommitChangeClass",
    "Event",
    "EventType",
    "GitSnapshot",
    "RejectedApproach",
    "is_well_formed_event_id",
    "make_assumption_declared_payload",
    "make_assumption_falsified_payload",
    "make_commit_landed_payload",
    "make_design_reasoning_recorded_payload",
    "make_finding_observed_payload",
    "make_human_decision_recorded_payload",
    "make_invariant_declared_payload",
    "make_phase_abandoned_payload",
    "make_phase_blocked_payload",
    "make_phase_completed_payload",
    "make_phase_merged_payload",
    "make_phase_split_payload",
    "make_phase_started_payload",
    "make_phase_superseded_payload",
    "make_plan_reauthored_payload",
    "make_test_failed_payload",
    "make_threshold_crossed_payload",
    "make_work_observed_payload",
]
