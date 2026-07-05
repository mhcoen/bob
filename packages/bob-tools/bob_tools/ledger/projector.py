"""Plan Ledger projector.

Pure function from a sequence of events to a ``PlanState``. Replays
events in deterministic order keyed on
``(event_id, writer_id, seq)`` -- ``event_id`` (UUIDv7) is the primary
key, ``(writer_id, seq)`` is a deterministic tiebreaker that only
matters in the vanishingly unlikely case of equal event_ids. ``ts`` is
not consulted: the projector is independent of human-clock ordering so
that two writers with skewed clocks still project to the same state.

The projector does not validate events. Callers should run them
through ``bob_tools.ledger.schema.validate_event`` before projection
if they have not already been validated at write time. The contract
is: any event reaching ``project()`` is treated as "successfully
applied" for the purposes of advancing
``last_event_seq_per_writer``, including the two reserved Slice A
event types whose semantics will be filled in by Slice B.

Design-reasoning resolution. ``design_reasoning_recorded`` events
carry a required ``linked_event_id``. The projector resolves them in
a second pass after the per-event index is fully built, so a
design_reasoning event whose linked event happens later in the sorted
order still resolves cleanly. If the link cannot be resolved to a
phase the event_id lands in ``PlanState.orphaned_design_reasoning``
(option (b) per Codex's tightening: surface, do not silently drop,
do not hard-fail). ``PlanState.orphaned_design_reasoning_count``
mirrors the list length for cheap visibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bob_tools.ledger.events import (
    RESERVED_EVENT_TYPES,
    SCHEMA_VERSION,
    AssumptionConfidence,
    Event,
    EventType,
)

# ---------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------


class PhaseStatus(StrEnum):
    """Lifecycle status of a phase.

    ``provisional`` is reserved for Slice C/D async re-author mode and
    is never reached by Slice A events; it is included in the enum so
    consumers do not have to migrate the type when Slice C lands.
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    SPLIT = "split"
    MERGED = "merged"
    BLOCKED = "blocked"
    PROVISIONAL = "provisional"


@dataclass
class PhaseSupersession:
    superseded_by_id: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"superseded_by_id": self.superseded_by_id, "reason": self.reason}


@dataclass
class PhaseLineage:
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    supersession: PhaseSupersession | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "predecessors": list(self.predecessors),
            "successors": list(self.successors),
            "supersession": (
                self.supersession.to_json() if self.supersession is not None else None
            ),
        }


@dataclass
class PhaseRecord:
    id: str
    title: str
    goal: str | None
    status: PhaseStatus
    created_event_id: str
    lineage: PhaseLineage = field(default_factory=PhaseLineage)
    evidence_refs: list[str] = field(default_factory=list)
    modification_history: list[str] = field(default_factory=list)
    design_reasoning_refs: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "status": str(self.status),
            "created_event_id": self.created_event_id,
            "lineage": self.lineage.to_json(),
            "evidence_refs": list(self.evidence_refs),
            "modification_history": list(self.modification_history),
            "design_reasoning_refs": list(self.design_reasoning_refs),
        }


@dataclass
class InvariantRecord:
    invariant_id: str
    statement: str
    source: str
    phase_id: str | None
    declared_event_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "statement": self.statement,
            "source": self.source,
            "phase_id": self.phase_id,
            "declared_event_id": self.declared_event_id,
        }


@dataclass
class AssumptionRecord:
    assumption_id: str
    statement: str
    phase_id: str | None
    confidence: AssumptionConfidence
    declared_event_id: str
    falsified: bool = False
    falsified_event_id: str | None = None
    falsified_summary: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "assumption_id": self.assumption_id,
            "statement": self.statement,
            "phase_id": self.phase_id,
            "confidence": str(self.confidence),
            "declared_event_id": self.declared_event_id,
            "falsified": self.falsified,
            "falsified_event_id": self.falsified_event_id,
            "falsified_summary": self.falsified_summary,
        }


@dataclass
class HumanDecisionRecord:
    decision_id: str
    summary: str
    rationale: str
    decided_by: str
    applies_to_phase_ids: list[str]
    decided_event_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "summary": self.summary,
            "rationale": self.rationale,
            "decided_by": self.decided_by,
            "applies_to_phase_ids": list(self.applies_to_phase_ids),
            "decided_event_id": self.decided_event_id,
        }


@dataclass
class PlanState:
    """Projected state of a Plan Ledger.

    Built by ``project()`` from a sequence of events. ``to_json`` and
    ``from_json`` round-trip a JSON-compatible dict for persistence
    as ``PLAN.state.json``. Equality on ``PlanState`` is field-wise,
    so two projections from the same event log compare equal.
    """

    schema_version: str = SCHEMA_VERSION
    last_event_id: str | None = None
    last_event_seq_per_writer: dict[str, int] = field(default_factory=dict)
    writer_ids_seen: list[str] = field(default_factory=list)
    phases: list[PhaseRecord] = field(default_factory=list)
    invariants: list[InvariantRecord] = field(default_factory=list)
    assumptions: list[AssumptionRecord] = field(default_factory=list)
    human_decisions: list[HumanDecisionRecord] = field(default_factory=list)
    findings_unattributed: list[str] = field(default_factory=list)
    orphaned_design_reasoning: list[str] = field(default_factory=list)
    orphaned_design_reasoning_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "last_event_id": self.last_event_id,
            "last_event_seq_per_writer": dict(self.last_event_seq_per_writer),
            "writer_ids_seen": list(self.writer_ids_seen),
            "phases": [p.to_json() for p in self.phases],
            "invariants": [i.to_json() for i in self.invariants],
            "assumptions": [a.to_json() for a in self.assumptions],
            "human_decisions": [d.to_json() for d in self.human_decisions],
            "findings_unattributed": list(self.findings_unattributed),
            "orphaned_design_reasoning": list(self.orphaned_design_reasoning),
            "orphaned_design_reasoning_count": self.orphaned_design_reasoning_count,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> PlanState:
        return cls(
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
            last_event_id=raw.get("last_event_id"),
            last_event_seq_per_writer=dict(raw.get("last_event_seq_per_writer") or {}),
            writer_ids_seen=list(raw.get("writer_ids_seen") or []),
            phases=[_phase_from_json(p) for p in raw.get("phases") or []],
            invariants=[
                InvariantRecord(**dict(i)) for i in raw.get("invariants") or []
            ],
            assumptions=[
                _assumption_from_json(a) for a in raw.get("assumptions") or []
            ],
            human_decisions=[
                HumanDecisionRecord(
                    decision_id=d["decision_id"],
                    summary=d["summary"],
                    rationale=d["rationale"],
                    decided_by=d["decided_by"],
                    applies_to_phase_ids=list(d.get("applies_to_phase_ids") or []),
                    decided_event_id=d["decided_event_id"],
                )
                for d in raw.get("human_decisions") or []
            ],
            findings_unattributed=list(raw.get("findings_unattributed") or []),
            orphaned_design_reasoning=list(raw.get("orphaned_design_reasoning") or []),
            orphaned_design_reasoning_count=int(
                raw.get("orphaned_design_reasoning_count") or 0
            ),
        )


def _phase_from_json(raw: Mapping[str, Any]) -> PhaseRecord:
    lineage_raw = raw.get("lineage") or {}
    sup_raw = lineage_raw.get("supersession")
    lineage = PhaseLineage(
        predecessors=list(lineage_raw.get("predecessors") or []),
        successors=list(lineage_raw.get("successors") or []),
        supersession=(
            PhaseSupersession(
                superseded_by_id=sup_raw["superseded_by_id"],
                reason=sup_raw["reason"],
            )
            if sup_raw is not None
            else None
        ),
    )
    return PhaseRecord(
        id=raw["id"],
        title=raw["title"],
        goal=raw.get("goal"),
        status=PhaseStatus(raw["status"]),
        created_event_id=raw["created_event_id"],
        lineage=lineage,
        evidence_refs=list(raw.get("evidence_refs") or []),
        modification_history=list(raw.get("modification_history") or []),
        design_reasoning_refs=list(raw.get("design_reasoning_refs") or []),
    )


def _assumption_from_json(raw: Mapping[str, Any]) -> AssumptionRecord:
    return AssumptionRecord(
        assumption_id=raw["assumption_id"],
        statement=raw["statement"],
        phase_id=raw.get("phase_id"),
        confidence=AssumptionConfidence(raw["confidence"]),
        declared_event_id=raw["declared_event_id"],
        falsified=bool(raw.get("falsified", False)),
        falsified_event_id=raw.get("falsified_event_id"),
        falsified_summary=raw.get("falsified_summary"),
    )


# ---------------------------------------------------------------------
# Projector
# ---------------------------------------------------------------------


def _replay_key(event: Event) -> tuple[str, str, int]:
    return (event.event_id, event.writer_id, event.seq)


def _index_event_to_phase(event: Event, index: dict[str, str]) -> None:
    """Populate event_id -> phase_id mapping for events that resolve
    to a single unambiguous phase. Used by design_reasoning resolution.

    Events that do not have a single attributable phase (e.g.,
    ``commit_landed`` with ``attributed_phase_id == None``,
    ``human_decision_recorded`` whose ``applies_to_phase_ids`` length
    != 1, the Slice B reserved events, ``assumption_falsified``) are
    not indexed; design reasoning that links to one of them lands in
    the orphan list.
    """
    p = event.payload
    if event.type in {
        EventType.PHASE_STARTED,
        EventType.PHASE_COMPLETED,
        EventType.PHASE_ABANDONED,
        EventType.PHASE_BLOCKED,
        EventType.PHASE_SUPERSEDED,
        EventType.PHASE_SPLIT,
    }:
        phase_id = p.get("phase_id")
        if isinstance(phase_id, str) and phase_id:
            index[event.event_id] = phase_id
    elif event.type == EventType.PHASE_MERGED:
        target = p.get("into_phase_id")
        if isinstance(target, str) and target:
            index[event.event_id] = target
    elif event.type == EventType.COMMIT_LANDED:
        attributed = p.get("attributed_phase_id")
        if isinstance(attributed, str) and attributed:
            index[event.event_id] = attributed
    elif event.type in {
        EventType.TEST_FAILED,
        EventType.FINDING_OBSERVED,
        EventType.WORK_OBSERVED,
        EventType.INVARIANT_DECLARED,
        EventType.ASSUMPTION_DECLARED,
    }:
        phase_id = p.get("phase_id")
        if isinstance(phase_id, str) and phase_id:
            index[event.event_id] = phase_id
    elif event.type == EventType.HUMAN_DECISION_RECORDED:
        applies = p.get("applies_to_phase_ids") or []
        if len(applies) == 1 and isinstance(applies[0], str):
            index[event.event_id] = applies[0]


def project(events: Iterable[Event]) -> PlanState:
    """Replay ``events`` in deterministic order and return ``PlanState``.

    Events are sorted by ``(event_id, writer_id, seq)``; ``ts`` is not
    consulted. This makes projection independent of clock skew across
    writers. ``project(events)`` is a pure function: identical inputs
    always produce equal outputs.
    """
    sorted_events: list[Event] = sorted(events, key=_replay_key)

    state = PlanState()
    phase_by_id: dict[str, PhaseRecord] = {}
    assumption_by_id: dict[str, AssumptionRecord] = {}
    event_to_phase: dict[str, str] = {}
    deferred_design_reasoning: list[Event] = []
    seen_writers: set[str] = set()
    seen_event_ids: set[str] = set()

    for ev in sorted_events:
        # Idempotence: a duplicated event line (same event_id appended
        # twice, e.g. a retried writer or a torn-then-rewritten append)
        # must apply exactly once. Phase and assumption handlers dedupe
        # by their domain id, but invariant/human_decision/finding and
        # every evidence_refs/design_reasoning_refs append are pure
        # list appends that would otherwise double-count and re-fire
        # threshold crossings. Skip the whole event -- high-water marks
        # are idempotent for a repeated (writer_id, seq) anyway.
        if ev.event_id in seen_event_ids:
            continue
        seen_event_ids.add(ev.event_id)

        # High-water marks. T1 fold: every event reaching project() has
        # been validated by the caller and so counts as successfully
        # applied. Reserved events count too -- their schema validates,
        # the projector just chooses to treat them as no-ops at Slice A.
        state.last_event_id = ev.event_id
        prior = state.last_event_seq_per_writer.get(ev.writer_id, -1)
        if ev.seq > prior:
            state.last_event_seq_per_writer[ev.writer_id] = ev.seq
        if ev.writer_id not in seen_writers:
            seen_writers.add(ev.writer_id)

        _index_event_to_phase(ev, event_to_phase)

        if ev.type == EventType.PHASE_STARTED:
            _apply_phase_started(ev, phase_by_id)
        elif ev.type == EventType.PHASE_COMPLETED:
            _apply_phase_lifecycle(ev, phase_by_id, PhaseStatus.COMPLETED)
        elif ev.type == EventType.PHASE_ABANDONED:
            _apply_phase_lifecycle(ev, phase_by_id, PhaseStatus.ABANDONED)
        elif ev.type == EventType.PHASE_BLOCKED:
            _apply_phase_lifecycle(ev, phase_by_id, PhaseStatus.BLOCKED)
        elif ev.type == EventType.PHASE_SUPERSEDED:
            _apply_phase_superseded(ev, phase_by_id)
        elif ev.type == EventType.PHASE_SPLIT:
            _apply_phase_split(ev, phase_by_id)
        elif ev.type == EventType.PHASE_MERGED:
            _apply_phase_merged(ev, phase_by_id)
        elif ev.type == EventType.COMMIT_LANDED:
            _apply_commit_landed(ev, phase_by_id)
        elif ev.type == EventType.TEST_FAILED:
            _apply_phase_evidence(ev, phase_by_id, ev.payload.get("phase_id"))
        elif ev.type == EventType.FINDING_OBSERVED:
            _apply_finding_observed(ev, phase_by_id, state)
        elif ev.type == EventType.WORK_OBSERVED:
            _apply_work_observed(ev, phase_by_id)
        elif ev.type == EventType.INVARIANT_DECLARED:
            state.invariants.append(
                InvariantRecord(
                    invariant_id=ev.payload["invariant_id"],
                    statement=ev.payload["statement"],
                    source=ev.payload["source"],
                    phase_id=ev.payload.get("phase_id"),
                    declared_event_id=ev.event_id,
                )
            )
        elif ev.type == EventType.ASSUMPTION_DECLARED:
            assumption_id = ev.payload["assumption_id"]
            if assumption_id not in assumption_by_id:
                assumption_by_id[assumption_id] = AssumptionRecord(
                    assumption_id=assumption_id,
                    statement=ev.payload["statement"],
                    phase_id=ev.payload.get("phase_id"),
                    confidence=AssumptionConfidence(ev.payload["confidence"]),
                    declared_event_id=ev.event_id,
                )
        elif ev.type == EventType.ASSUMPTION_FALSIFIED:
            target = assumption_by_id.get(ev.payload["assumption_id"])
            if target is not None:
                target.falsified = True
                target.falsified_event_id = ev.payload["evidence_event_id"]
                target.falsified_summary = ev.payload["summary"]
        elif ev.type == EventType.HUMAN_DECISION_RECORDED:
            state.human_decisions.append(
                HumanDecisionRecord(
                    decision_id=ev.payload["decision_id"],
                    summary=ev.payload["summary"],
                    rationale=ev.payload["rationale"],
                    decided_by=ev.payload["decided_by"],
                    applies_to_phase_ids=list(
                        ev.payload.get("applies_to_phase_ids") or []
                    ),
                    decided_event_id=ev.event_id,
                )
            )
        elif ev.type == EventType.DESIGN_REASONING_RECORDED:
            deferred_design_reasoning.append(ev)
        elif ev.type in RESERVED_EVENT_TYPES:
            # Slice A: schema validates, projector ignores. High-water
            # marks already advanced above.
            pass

    # Resolve design_reasoning_recorded after the index is fully built.
    # An event whose linked_event_id resolves to a known phase attaches
    # to that phase; otherwise it lands in the orphan list.
    for ev in deferred_design_reasoning:
        linked = ev.payload["linked_event_id"]
        target_phase_id = event_to_phase.get(linked)
        if target_phase_id is not None and target_phase_id in phase_by_id:
            phase_by_id[target_phase_id].design_reasoning_refs.append(ev.event_id)
        else:
            state.orphaned_design_reasoning.append(ev.event_id)

    # Finalize. Deterministic ordering across all output collections so
    # ``project(shuffle(events)) == project(events)``.
    state.phases = sorted(phase_by_id.values(), key=lambda ph: ph.created_event_id)
    state.assumptions = sorted(
        assumption_by_id.values(), key=lambda ar: ar.declared_event_id
    )
    state.writer_ids_seen = sorted(seen_writers)
    state.orphaned_design_reasoning_count = len(state.orphaned_design_reasoning)

    return state


# ---------------------------------------------------------------------
# Per-event handlers (split out so the main loop reads cleanly)
# ---------------------------------------------------------------------


def _apply_phase_started(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    p = ev.payload
    phase_id = p["phase_id"]
    if phase_id in phase_by_id:
        # Duplicate phase_started for the same id is a writer error;
        # leave the existing record untouched, just record the
        # repeated event in modification_history for audit.
        phase_by_id[phase_id].modification_history.append(ev.event_id)
        return
    phase = PhaseRecord(
        id=phase_id,
        title=p["title"],
        goal=p.get("goal"),
        status=PhaseStatus.PENDING,
        created_event_id=ev.event_id,
    )
    phase.lineage.predecessors = list(p.get("predecessor_phase_ids") or [])
    phase.modification_history.append(ev.event_id)
    for pred_id in phase.lineage.predecessors:
        pred = phase_by_id.get(pred_id)
        if pred is not None and phase_id not in pred.lineage.successors:
            pred.lineage.successors.append(phase_id)
    phase_by_id[phase_id] = phase


def _apply_phase_lifecycle(
    ev: Event, phase_by_id: dict[str, PhaseRecord], new_status: PhaseStatus
) -> None:
    ph = phase_by_id.get(ev.payload["phase_id"])
    if ph is None:
        return
    ph.status = new_status
    ph.modification_history.append(ev.event_id)


def _apply_phase_superseded(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    p = ev.payload
    old = phase_by_id.get(p["phase_id"])
    if old is not None:
        old.status = PhaseStatus.SUPERSEDED
        old.lineage.supersession = PhaseSupersession(
            superseded_by_id=p["superseded_by_phase_id"], reason=p["reason"]
        )
        old.modification_history.append(ev.event_id)
        successor_id = p["superseded_by_phase_id"]
        if successor_id not in old.lineage.successors:
            old.lineage.successors.append(successor_id)
    new_phase = phase_by_id.get(p["superseded_by_phase_id"])
    if new_phase is not None:
        new_phase.modification_history.append(ev.event_id)
        if p["phase_id"] not in new_phase.lineage.predecessors:
            new_phase.lineage.predecessors.append(p["phase_id"])


def _apply_phase_split(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    p = ev.payload
    old = phase_by_id.get(p["phase_id"])
    if old is None:
        return
    old.status = PhaseStatus.SPLIT
    for nid in p["into_phase_ids"]:
        if nid not in old.lineage.successors:
            old.lineage.successors.append(nid)
    old.modification_history.append(ev.event_id)


def _apply_phase_merged(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    p = ev.payload
    target = p["into_phase_id"]
    for old_id in p["merged_phase_ids"]:
        old = phase_by_id.get(old_id)
        if old is None:
            continue
        old.status = PhaseStatus.MERGED
        if target not in old.lineage.successors:
            old.lineage.successors.append(target)
        old.modification_history.append(ev.event_id)
    merged_target = phase_by_id.get(target)
    if merged_target is not None:
        for old_id in p["merged_phase_ids"]:
            if old_id not in merged_target.lineage.predecessors:
                merged_target.lineage.predecessors.append(old_id)
        merged_target.modification_history.append(ev.event_id)


def _apply_commit_landed(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    attributed = ev.payload.get("attributed_phase_id")
    if not isinstance(attributed, str) or not attributed:
        return
    ph = phase_by_id.get(attributed)
    if ph is None:
        return
    ph.evidence_refs.append(ev.event_id)
    if ph.status == PhaseStatus.PENDING:
        ph.status = PhaseStatus.ACTIVE


def _apply_phase_evidence(
    ev: Event, phase_by_id: dict[str, PhaseRecord], phase_id: object
) -> None:
    if not isinstance(phase_id, str) or not phase_id:
        return
    ph = phase_by_id.get(phase_id)
    if ph is None:
        return
    ph.evidence_refs.append(ev.event_id)


def _apply_finding_observed(
    ev: Event,
    phase_by_id: dict[str, PhaseRecord],
    state: PlanState,
) -> None:
    phase_id = ev.payload.get("phase_id")
    if isinstance(phase_id, str) and phase_id and phase_id in phase_by_id:
        phase_by_id[phase_id].evidence_refs.append(ev.event_id)
        return
    state.findings_unattributed.append(ev.event_id)


def _apply_work_observed(ev: Event, phase_by_id: dict[str, PhaseRecord]) -> None:
    phase_id = ev.payload.get("phase_id")
    if not isinstance(phase_id, str) or not phase_id:
        return
    ph = phase_by_id.get(phase_id)
    if ph is None:
        return
    ph.evidence_refs.append(ev.event_id)
    if ph.status == PhaseStatus.PENDING:
        ph.status = PhaseStatus.ACTIVE


__all__ = [
    "AssumptionRecord",
    "HumanDecisionRecord",
    "InvariantRecord",
    "PhaseLineage",
    "PhaseRecord",
    "PhaseStatus",
    "PhaseSupersession",
    "PlanState",
    "project",
]
