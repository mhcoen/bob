"""Plan Ledger Slice D: threshold evaluation and auto-reauthor.

After each task settles, McLoop evaluates thresholds against the
ledger and either continues (no crossing or non-actionable
crossing) or pauses to invoke Duplo's re-author. This module owns
both halves: the threshold-evaluation pass that records crossings,
and the auto-reauthor invocation with the hard-stop failure-mode
contract from the Slice D design.

Public surface:

  - :class:`HardStop` -- typed exception McLoop's outer driver
    catches and translates into the distinct exit code.
  - :class:`PauseDecision` -- the structured outcome of an
    evaluation pass that recommends a pause.
  - :func:`evaluate_and_maybe_pause` -- run the threshold pass,
    record any crossings, and return a PauseDecision when the
    crossings recommend re-author. Side-effect: every crossing the
    pass produced is appended to the ledger regardless of pause
    decision.
  - :func:`auto_reauthor` -- invoke duplo.reauthor.reauthor_plan
    against the triggering crossing. Hard-stop on any failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HardStop(RuntimeError):
    """Raised when Slice D must halt the McLoop run.

    Distinct from any other mcloop runtime error so the outer driver
    can map this single exception type to the documented exit code
    for "Plan Ledger pause failed; manual intervention required".
    The ``reason`` field carries a short machine-readable token
    (``"reauthor_failed"``, ``"lineage_invalid"``,
    ``"reauthor_unavailable"``, ``"manual_pause"``) and ``detail``
    carries a human-readable message.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class PauseDecision:
    """Threshold-evaluator output that warrants a McLoop pause.

    ``crossing_event_id`` is the event_id of the
    ``threshold_crossed`` ledger event the auto-reauthor path
    references when invoking Duplo. ``rule_id`` and
    ``recommended_action`` are denormalized from the crossing
    payload so callers can route on action without re-fetching the
    event.

    ``target_phase_id`` carries the phase_id from the crossing's
    triggering event when one can be extracted (phase_superseded,
    phase_split, phase_merged, phase_abandoned). For plan-scoped
    rules and rules whose triggering event has no phase_id (e.g.,
    assumption_falsified, whose ledger payload carries an
    assumption_id rather than a phase_id), the field is None and
    ``auto_reauthor`` decides what to do based on the action
    severity. Used by ``auto_reauthor`` to honor
    ``recommended_action == reauthor_phase`` without escalating to
    plan-wide synthesis.
    """

    crossing_event_id: str
    rule_id: str
    recommended_action: str
    summary: str
    target_phase_id: str | None = None


# ---------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------


_REAUTHOR_ACTIONS: frozenset[str] = frozenset({"reauthor_phase", "reauthor_plan"})


def evaluate_and_maybe_pause(
    *,
    storage: Any,
    run_id: str,
) -> PauseDecision | None:
    """Evaluate Slice B's thresholds against the ledger.

    Records every crossing the pass produces (Slice B's
    record_crossings handles the writes). Returns the FIRST
    crossing whose recommended_action is in
    {reauthor_phase, reauthor_plan}; later crossings get recorded
    but do not become a pause decision (the first one to fire
    pauses the run, the later ones are still in the ledger and
    will be re-evaluated on the next pass).

    Returns None when no crossings warrant pausing.
    """
    from bob_tools.ledger.projector import project
    from bob_tools.ledger.thresholds import (
        ThresholdParams,
        ThresholdRecommendedAction,
        ThresholdRuleId,
        ThresholdSeverity,
        evaluate_thresholds,
        record_crossings,
    )

    events = storage.read_all()
    state = project(events)
    crossings = evaluate_thresholds(state, events, ThresholdParams())
    if not crossings:
        return None

    emitted_ids = record_crossings(storage, crossings, run_id=run_id)
    if not emitted_ids:
        return None

    # Re-read crossings in event-id order so we pause on the earliest
    # actionable one. record_crossings returns event_ids in emission
    # order; that order matches the crossings input order, but reading
    # back ensures we always inspect the persisted payload (the source
    # of truth for the auto-reauthor invocation).
    fresh_events = storage.read_all()
    by_id = {ev.event_id: ev for ev in fresh_events}

    for ev_id in emitted_ids:
        ev = by_id.get(ev_id)
        if ev is None:
            continue
        payload = ev.payload
        rule_id_str = payload.get("rule_id", "")
        severity, recommended_action = _derive_severity_and_action(
            rule_id_str,
            ThresholdRuleId,
            ThresholdSeverity,
            ThresholdRecommendedAction,
        )
        if recommended_action not in _REAUTHOR_ACTIONS:
            continue
        target_phase_id = (
            _extract_target_phase_id(payload, by_id)
            if recommended_action == "reauthor_phase"
            else None
        )
        return PauseDecision(
            crossing_event_id=ev.event_id,
            rule_id=rule_id_str,
            recommended_action=recommended_action,
            summary=payload.get("summary", ""),
            target_phase_id=target_phase_id,
        )
    return None


def _extract_target_phase_id(
    crossing_payload: dict[str, Any],
    events_by_id: dict[str, Any],
) -> str | None:
    """Walk the crossing's triggering events and return the DERIVATIVE
    phase_id of the first phase-scoped event that has one.

    Each lifecycle event type's payload has a different shape, and
    the "target" for a phase-scoped reauthor MUST be a phase that
    still exists in the current plan. For events that consume a
    phase (supersede / split / merge), the source ``phase_id`` field
    refers to the now-gone phase; the DERIVATIVE field
    (``superseded_by_phase_id`` / ``into_phase_ids[0]`` /
    ``into_phase_id``) is what the reauthor should scope to.

    The earlier implementation pulled ``payload.get("phase_id")``
    unconditionally and returned the consumed source for split /
    merge / supersede, which the structural validator then rejected
    against the post-change prior_plan_ids — a self-perpetuating
    stuck loop. Routing by event type returns the new phase id
    that EXISTS in the current plan.

      - phase_superseded: derivative = ``superseded_by_phase_id``.
      - phase_split:      derivative = ``into_phase_ids[0]`` (first
                          branch is sufficient for scoping; the
                          structural validator enforces the rest).
      - phase_merged:     derivative = ``into_phase_id`` (the single
                          new merged phase).
      - phase_abandoned:  NO derivative — the phase is gone, nothing
                          replaces it. Return None and let
                          auto_reauthor's scope_unavailable path fire.
      - assumption_falsified: payload carries assumption_id not
                          phase_id; return None (existing behavior).

    Returns the first derivative found across triggering events in
    payload order. Multi-phase crossings are rare; the first id is
    sufficient for scoping, and the structural validator on Duplo's
    side enforces that the assembled plan's ids stay within the
    current prior id set.
    """
    triggering_ids = crossing_payload.get("triggering_event_ids") or []
    for trig_id in triggering_ids:
        trig = events_by_id.get(trig_id)
        if trig is None:
            continue
        trig_type = getattr(trig, "type", None)
        trig_payload = getattr(trig, "payload", None) or {}
        # trig.type may be a bob_tools EventType enum or a plain str
        # depending on caller / fixture. Normalize to str via .value
        # when available, else the str() form.
        trig_type_value = getattr(trig_type, "value", None)
        trig_type_str = str(trig_type_value if trig_type_value is not None else trig_type)
        target = _derivative_target_for_event(trig_type_str, trig_payload)
        if target:
            return target
    return None


def _derivative_target_for_event(event_type: str, payload: dict[str, Any]) -> str | None:
    """Map a phase-scoped lifecycle event to its derivative phase id.

    Payload field names come from
    :mod:`bob_tools.ledger.events`:

      - make_phase_superseded_payload: phase_id + superseded_by_phase_id
      - make_phase_split_payload:      phase_id + into_phase_ids (list)
      - make_phase_merged_payload:     merged_phase_ids (list) + into_phase_id
      - make_phase_abandoned_payload:  phase_id (only)

    Returns the derivative id, or None when the event has no
    derivative (phase_abandoned, assumption_falsified, or any event
    type not phase-scoped). The caller (auto_reauthor) treats None
    as scope_unavailable: better to pause for manual review than to
    target a phase that no longer exists.
    """
    if event_type == "phase_superseded":
        value = payload.get("superseded_by_phase_id")
        return value if isinstance(value, str) and value else None
    if event_type == "phase_split":
        into = payload.get("into_phase_ids") or []
        if isinstance(into, list) and into:
            first = into[0]
            return first if isinstance(first, str) and first else None
        return None
    if event_type == "phase_merged":
        value = payload.get("into_phase_id")
        return value if isinstance(value, str) and value else None
    # phase_abandoned has no derivative; assumption_falsified carries
    # no phase_id; any other type is non-phase-scoped.
    return None


def _derive_severity_and_action(
    rule_id_str: str,
    rule_id_enum: Any,
    severity_enum: Any,
    action_enum: Any,
) -> tuple[str, str]:
    """Map a persisted rule_id string to its severity + action.

    Mirrors the helper in duplo.reauthor. Persistence stores the
    rule_id only; severity and recommended_action are derived from
    the rule each time they are needed.
    """
    plan_rules = {
        rule_id_enum.UNATTRIBUTABLE_COMMIT.value,
        rule_id_enum.INVARIANT_DECLARED.value,
        rule_id_enum.EXPLORATORY_COUNT_EXCEEDED.value,
    }
    phase_rules = {
        rule_id_enum.PHASE_ABANDONED.value,
        rule_id_enum.PHASE_SUPERSEDED.value,
        rule_id_enum.PHASE_TOPOLOGY_CHANGED.value,
        rule_id_enum.ASSUMPTION_FALSIFIED.value,
    }
    severity = severity_enum.TRIGGER_REAUTHOR.value
    if rule_id_str in plan_rules:
        action = action_enum.REAUTHOR_PLAN.value
    elif rule_id_str in phase_rules:
        action = action_enum.REAUTHOR_PHASE.value
    else:
        action = action_enum.LOG_ONLY.value
    return severity, action


# ---------------------------------------------------------------------
# Auto-reauthor invocation
# ---------------------------------------------------------------------


def auto_reauthor(
    *,
    decision: PauseDecision,
    plan_path: Path,
    ledger_dir: Path,
    project_dir: Path,
) -> Any:
    """Invoke duplo.reauthor.reauthor_plan against the crossing.

    Slice D's failure-mode contract (Q1 resolution) is fail-closed:

    - If duplo cannot be imported, raise HardStop("reauthor_unavailable",
      ...). McLoop's outer driver maps this to the distinct exit code.
    - If duplo's reauthor raises LineageValidationError, raise
      HardStop("lineage_invalid", ...). The atomicity guarantees in
      duplo's reauthor mean the ledger and PLAN.md are unchanged in
      this case; the only ledger evidence of the attempt is the
      original threshold_crossed event recorded above.
    - If duplo's reauthor raises any other exception, raise
      HardStop("reauthor_failed", ...) and surface the underlying
      error in detail.
    - On success, return the ReauthorResult unchanged. The outer
      driver refreshes its plan/task mapping from the new PLAN.md
      and continues.
    """
    try:
        from duplo.reauthor import (
            LineageValidationError,
            ReauthorError,
            reauthor_plan,
        )
    except ImportError as exc:
        raise HardStop(
            reason="reauthor_unavailable",
            detail=(
                f"duplo.reauthor is not importable in this environment: {exc}. "
                "Install duplo (or set --no-auto-reauthor / "
                "MCLOOP_NO_AUTO_REAUTHOR=1) to continue without auto-reauthor."
            ),
        ) from exc

    # PlanArtifactError is a duplo-side subclass of ReauthorError.
    # It might not exist on older duplo installations; guard the
    # import so this module still imports cleanly there. When the
    # symbol is absent, fall back to a sentinel that the except
    # clauses below cannot catch (so the generic ReauthorError
    # handler picks up the failure as before).
    try:
        from duplo.reauthor import PlanArtifactError as _PlanArtifactError
    except ImportError:

        class _PlanArtifactError(Exception):  # type: ignore[no-redef]
            """Sentinel for older duplo installs that lack PlanArtifactError."""

    # CommitAttributionError is the same shape: duplo-side subclass
    # of ReauthorError, guarded import for older duplo installs that
    # don't expose it. mcloop surfaces it as a distinct pause reason
    # so the operator knows the model named an unknown commit or an
    # unknown phase in the attribution, not a generic reauthor crash.
    try:
        from duplo.reauthor import (
            CommitAttributionError as _CommitAttributionError,
        )
    except ImportError:

        class _CommitAttributionError(Exception):  # type: ignore[no-redef]
            """Sentinel for older duplo installs that lack CommitAttributionError."""

    # SchemaValidationError is the same shape: duplo-side subclass
    # of ReauthorError, surfaced as schema_validation_invalid when
    # the synthesizer's verdict was rejected by orchestra's schema
    # validator (additional_properties, missing_required,
    # enum_mismatch, malformed_array, or json_parse). The error
    # carries a classification.primary kind we include in the detail
    # surface so the operator sees the named failure mode without
    # reading the audit log.
    try:
        from duplo.reauthor import (
            SchemaValidationError as _SchemaValidationError,
        )
    except ImportError:

        class _SchemaValidationError(Exception):  # type: ignore[no-redef]
            """Sentinel for older duplo installs that lack SchemaValidationError."""

    # Honor decision.recommended_action. Phase-scoped recommendations
    # MUST NOT be silently widened to plan-wide synthesis; that
    # amplifies a single phase change into a council pass over every
    # prior id and is exactly the failure mode lineage_invalid pauses
    # surface. When the crossing's triggering event identifies a
    # specific phase, scope the reauthor to that phase. Otherwise
    # fail closed: a phase-scoped recommendation without an
    # extractable target phase_id is better paused for human review
    # than escalated.
    target_phase_id: str | None = None
    if decision.recommended_action == "reauthor_phase":
        if decision.target_phase_id is None:
            raise HardStop(
                reason="scope_unavailable",
                detail=(
                    f"recommended_action={decision.recommended_action!r} on "
                    f"rule {decision.rule_id!r} but the crossing's "
                    "triggering event has no extractable phase_id "
                    "(assumption_falsified payloads carry assumption_id, "
                    "not phase_id). Refusing to widen the scope to a "
                    "plan-wide reauthor; pausing for manual review. "
                    f"Crossing event: {decision.crossing_event_id}."
                ),
            )
        target_phase_id = decision.target_phase_id

    try:
        return reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=decision.crossing_event_id,
            project_dir=project_dir,
            target_phase_id=target_phase_id,
        )
    except LineageValidationError as exc:
        raise HardStop(
            reason="lineage_invalid",
            detail=(
                f"duplo's lineage validator rejected the synthesized plan: "
                f"{exc}. PLAN.md is unchanged; the threshold crossing "
                f"({decision.crossing_event_id}) is on the ledger."
            ),
        ) from exc
    except _PlanArtifactError as exc:
        # MUST be caught before the generic ReauthorError handler
        # below, since PlanArtifactError is a subclass of
        # ReauthorError. The plan-artifact contract is a model
        # output error (not a reauthor implementation failure); it
        # gets a distinct pause reason so the operator knows
        # whether to retry the model or fix the runtime.
        raise HardStop(
            reason="plan_artifact_invalid",
            detail=(
                f"duplo's reauthor rejected the plan artifact: "
                f"{exc}. PLAN.md is unchanged; the threshold "
                f"crossing ({decision.crossing_event_id}) is on the "
                "ledger. The synthesizer's plan output did not "
                "satisfy the trailing-fenced-verdict contract."
            ),
        ) from exc
    except _CommitAttributionError as exc:
        # MUST be caught before the generic ReauthorError handler
        # below, since CommitAttributionError is a subclass of
        # ReauthorError. The commit-attribution contract is a model
        # output error too: the synthesizer named a commit or phase
        # the runtime doesn't know about. Distinct pause reason so
        # the operator can read the validator's accumulated
        # violations directly.
        raise HardStop(
            reason="commit_attribution_invalid",
            detail=(
                f"duplo's reauthor rejected the verdict's "
                f"commit_attributions: {exc}. PLAN.md is unchanged; "
                f"the threshold crossing ({decision.crossing_event_id}) "
                "is on the ledger."
            ),
        ) from exc
    except _SchemaValidationError as exc:
        # MUST be caught before the generic ReauthorError handler
        # below, since SchemaValidationError is a subclass of
        # ReauthorError. Orchestra rejected the synthesizer's verdict
        # at schema validation and duplo's bounded retry was unable
        # to rescue the run (the retry budget is shared with lineage
        # validation; one schema failure and one lineage failure
        # exhausts the run's allotment). Distinct pause reason names
        # the primary failure kind so the operator can read the
        # audit log targeted at the right phase.
        primary_kind = getattr(getattr(exc, "classification", None), "primary", None)
        kind_value = getattr(primary_kind, "value", None) or "unknown"
        raise HardStop(
            reason="schema_validation_invalid",
            detail=(
                f"orchestra rejected the synthesizer's verdict at "
                f"schema validation (primary_kind={kind_value!r}): "
                f"{exc}. PLAN.md is unchanged; the threshold crossing "
                f"({decision.crossing_event_id}) is on the ledger."
            ),
        ) from exc
    except ReauthorError as exc:
        raise HardStop(
            reason="reauthor_failed",
            detail=(
                f"duplo.reauthor.reauthor_plan failed: {exc}. The threshold "
                f"crossing ({decision.crossing_event_id}) is on the ledger; "
                "PLAN.md was not modified."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 -- explicit fail-closed surface
        raise HardStop(
            reason="reauthor_failed",
            detail=(
                f"unexpected exception during auto-reauthor: "
                f"{type(exc).__name__}: {exc}. The threshold crossing "
                f"({decision.crossing_event_id}) is on the ledger; PLAN.md "
                "was not modified."
            ),
        ) from exc


__all__ = [
    "HardStop",
    "PauseDecision",
    "auto_reauthor",
    "evaluate_and_maybe_pause",
]
