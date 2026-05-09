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
    """

    crossing_event_id: str
    rule_id: str
    recommended_action: str
    summary: str


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
    from bob_tools.ledger import (
        evaluate_thresholds,
        project,
        record_crossings,
    )
    from bob_tools.ledger.thresholds import (
        ThresholdParams,
        ThresholdRecommendedAction,
        ThresholdRuleId,
        ThresholdSeverity,
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
        return PauseDecision(
            crossing_event_id=ev.event_id,
            rule_id=rule_id_str,
            recommended_action=recommended_action,
            summary=payload.get("summary", ""),
        )
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
        from duplo.reauthor import LineageValidationError, ReauthorError, reauthor_plan
    except ImportError as exc:
        raise HardStop(
            reason="reauthor_unavailable",
            detail=(
                f"duplo.reauthor is not importable in this environment: {exc}. "
                "Install duplo (or set --no-auto-reauthor / "
                "MCLOOP_NO_AUTO_REAUTHOR=1) to continue without auto-reauthor."
            ),
        ) from exc

    try:
        return reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=decision.crossing_event_id,
            project_dir=project_dir,
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
