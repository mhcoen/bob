"""Bounded corrective gate over the assembled PLAN.md.

After full-plan assembly the pipeline runs :func:`check_plan_sanity`
over the whole PLAN.md (see :mod:`duplo.plan_sanity`). This module turns
that report into action under a strict, bounded policy:

  - repair the deterministically-repairable defect classes
    (duplicate / non-sequential ``phase_id`` comments; verify-without-build
    orphan tasks), logging exactly what changed;
  - re-validate the repaired plan EXACTLY ONCE;
  - proceed if that re-validation is clean;
  - on a persistent failure (still dirty after the single repair pass) or
    any unknown / unrepairable defect class (a ``## Scope`` include item
    built by no phase cannot be synthesized mechanically), HARD STOP with
    an actionable report and do NOT retry.

The gate never loops: at most one repair pass, at most one re-validation.
That bound is the point. A retry loop over an LLM-authored plan masks the
structural problem the user must see; the gate either fixes a known,
mechanical defect once or stops loudly.

:func:`run_plan_sanity_gate` is the pure core (text in, decision out) and
is what the tests exercise. :func:`enforce_plan_sanity` is the thin
pipeline-facing wrapper that reads PLAN.md, writes back a repair (and
commits it), or hard-stops the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from duplo.plan_sanity import (
    KIND_PHASE_IDS,
    KIND_VERIFY_WITHOUT_BUILD,
    PlanSanityReport,
    check_plan_sanity,
    orphan_verification_lines,
)
from duplo.reauthor_phase_ids import parse_plan_phases, stamp_sequential_phase_ids

# The defect classes the gate knows how to repair deterministically.
# Any violation kind outside this set is unrepairable and forces an
# immediate hard stop -- the gate never guesses.
REPAIRABLE_KINDS = frozenset({KIND_PHASE_IDS, KIND_VERIFY_WITHOUT_BUILD})

GateStatus = Literal["clean", "repaired", "hard_stop"]


class PlanSanityHardStop(RuntimeError):
    """Raised by :func:`enforce_plan_sanity` when the gate hard-stops.

    Carries the actionable report so a caller (or test) can inspect it.
    The pipeline lets this propagate after printing the report; it is a
    deliberate, non-retryable stop, not a transient error.
    """

    def __init__(self, report_text: str, outcome: GateOutcome) -> None:
        super().__init__(report_text)
        self.report_text = report_text
        self.outcome = outcome


@dataclass
class GateOutcome:
    """Result of :func:`run_plan_sanity_gate`.

    ``status`` is one of ``clean`` (passed on the first check),
    ``repaired`` (failed only on repairable classes and a single repair
    pass produced a clean plan), or ``hard_stop`` (an unrepairable class
    was present, or the plan was still dirty after the one repair pass).

    ``plan_text`` is the text the caller should persist: the original on
    ``clean``/``hard_stop``-without-repair, the repaired body on
    ``repaired``, and the best-effort repaired body on a persistent
    ``hard_stop`` (the caller does not persist it in that case).
    """

    status: GateStatus
    plan_text: str
    changes: list[str] = field(default_factory=list)
    report_before: PlanSanityReport | None = None
    report_after: PlanSanityReport | None = None
    stop_report: str | None = None


def _repair_phase_ids(plan_text: str) -> tuple[str, list[str]]:
    """Renumber phase_id comments to sequential phase_001..phase_NNN.

    Returns the rewritten text and a one-line change log describing the
    before/after id sequence. When stamping is a no-op (already
    sequential) no change is logged.
    """
    before = [h.id for h in parse_plan_phases(plan_text)]
    repaired = stamp_sequential_phase_ids(plan_text)
    after = [h.id for h in parse_plan_phases(repaired)]
    if repaired == plan_text or before == after:
        return plan_text, []
    return repaired, [f"phase_ids: renumbered {before} -> {after}"]


def _repair_verify_without_build(plan_text: str) -> tuple[str, list[str]]:
    """Drop verification tasks that map to no feature any phase builds.

    Uses :func:`orphan_verification_lines` so the lines removed are
    exactly the ones the checker flags as
    :data:`KIND_VERIFY_WITHOUT_BUILD`. Each removed line is logged
    verbatim. A verify task can never pass if nothing builds the feature
    it checks, so removing it is the only mechanical repair; the missing
    build, if real, surfaces as a scope-coverage problem the user owns.
    """
    drop = set(orphan_verification_lines(plan_text))
    if not drop:
        return plan_text, []
    lines = plan_text.splitlines(keepends=True)
    kept: list[str] = []
    changes: list[str] = []
    for index, line in enumerate(lines):
        if index in drop:
            changes.append(f"removed verify-without-build task: {line.strip()}")
        else:
            kept.append(line)
    return "".join(kept), changes


def _repair(plan_text: str, kinds: set[str]) -> tuple[str, list[str]]:
    """Apply the deterministic repairs for the present repairable kinds.

    Verify-task removal runs before phase-id stamping: dropping lines
    never touches phase headers, so the stamper sees a stable structure.
    """
    text = plan_text
    changes: list[str] = []
    if KIND_VERIFY_WITHOUT_BUILD in kinds:
        text, msgs = _repair_verify_without_build(text)
        changes.extend(msgs)
    if KIND_PHASE_IDS in kinds:
        text, msgs = _repair_phase_ids(text)
        changes.extend(msgs)
    return text, changes


def _format_stop_report(
    *,
    report: PlanSanityReport,
    persistent: bool,
    changes: list[str],
) -> str:
    """Render an actionable, multi-line hard-stop report.

    Names every outstanding violation and, when a repair was attempted,
    exactly what the gate changed before the plan stayed dirty -- so the
    user can act without re-running anything.
    """
    lines: list[str] = ["PLAN.md failed the post-assembly sanity gate."]
    if persistent:
        lines.append(
            "A single deterministic repair pass did not produce a clean plan; "
            "the gate does not retry."
        )
    else:
        lines.append(
            "It has defect(s) that cannot be repaired mechanically; the gate "
            "does not guess and does not retry."
        )
    if changes:
        lines.append("Repairs already applied this pass:")
        lines.extend(f"  - {c}" for c in changes)
    lines.append("Outstanding violations:")
    for v in report.violations:
        lines.append(f"  - [{v.kind}] {v.message}")
    lines.append(
        "Fix the SPEC.md / roadmap so every scope item is built and every "
        "verification maps to a built feature, then re-run duplo."
    )
    return "\n".join(lines)


def run_plan_sanity_gate(
    plan_text: str,
    *,
    scope_include: Any | None = None,
    spec: Any | None = None,
) -> GateOutcome:
    """Apply the bounded loud-repair / hard-stop policy to an assembled plan.

    Pure: it neither reads nor writes the filesystem. The caller decides
    what to do with the returned :class:`GateOutcome` (persist the repair,
    or surface the hard-stop report).

    Args:
        plan_text: The full, assembled PLAN.md markdown.
        scope_include / spec: Forwarded to :func:`check_plan_sanity`.
    """
    report = check_plan_sanity(plan_text, scope_include=scope_include, spec=spec)
    if report.ok:
        return GateOutcome(status="clean", plan_text=plan_text, report_before=report)

    kinds = report.kinds()
    unrepairable = kinds - REPAIRABLE_KINDS
    if unrepairable:
        # An unknown / unrepairable class is present: hard stop without
        # touching the plan. Repairing the others would be busywork that
        # still leaves a plan the user must fix by hand.
        stop = _format_stop_report(report=report, persistent=False, changes=[])
        return GateOutcome(
            status="hard_stop",
            plan_text=plan_text,
            report_before=report,
            stop_report=stop,
        )

    # Every present class is repairable: repair once, re-validate once.
    repaired, changes = _repair(plan_text, set(kinds))
    report_after = check_plan_sanity(repaired, scope_include=scope_include, spec=spec)
    if report_after.ok:
        return GateOutcome(
            status="repaired",
            plan_text=repaired,
            changes=changes,
            report_before=report,
            report_after=report_after,
        )

    # Still dirty after the single repair pass: hard stop, no retry.
    stop = _format_stop_report(report=report_after, persistent=True, changes=changes)
    return GateOutcome(
        status="hard_stop",
        plan_text=repaired,
        changes=changes,
        report_before=report,
        report_after=report_after,
        stop_report=stop,
    )


def enforce_plan_sanity(
    spec: Any | None = None,
    *,
    target_dir: Path | str = ".",
) -> GateOutcome:
    """Run the gate against the on-disk PLAN.md and act on the outcome.

    On ``clean`` returns immediately. On ``repaired`` writes the repaired
    body back to PLAN.md, commits it, and prints the loud change log. On
    ``hard_stop`` records a diagnostics failure, prints the actionable
    report, and raises :class:`PlanSanityHardStop` so the run stops
    without retrying.

    Returns the :class:`GateOutcome` for ``clean`` / ``repaired`` so a
    caller can inspect what happened; ``hard_stop`` raises instead.
    """
    base = Path(target_dir)
    plan_path = base / "PLAN.md"
    if not plan_path.exists():
        # Nothing assembled to check; treat as clean rather than inventing
        # a failure. Callers only invoke this once the plan is complete.
        return GateOutcome(status="clean", plan_text="")

    plan_text = plan_path.read_text(encoding="utf-8")
    scope_include = getattr(spec, "scope_include", None) if spec is not None else None
    outcome = run_plan_sanity_gate(plan_text, scope_include=scope_include, spec=spec)

    if outcome.status == "clean":
        return outcome

    if outcome.status == "repaired":
        plan_path.write_text(outcome.plan_text, encoding="utf-8")
        print("Plan sanity gate: repaired known defect(s) in PLAN.md:")
        for change in outcome.changes:
            print(f"  - {change}")
        _commit_repair(plan_path)
        return outcome

    # hard_stop
    from duplo.diagnostics import record_failure

    final_report = outcome.report_after or outcome.report_before
    violations = final_report.violations if final_report is not None else []
    record_failure(
        "pipeline:plan_sanity_gate",
        "io",
        "Assembled PLAN.md failed the post-assembly sanity gate; hard stop.",
        context={
            "persistent": outcome.report_after is not None,
            "violations": [{"kind": v.kind, "message": v.message} for v in violations],
            "repairs_applied": outcome.changes,
        },
        errors_path=base / ".duplo" / "errors.jsonl",
    )
    assert outcome.stop_report is not None
    print(outcome.stop_report)
    raise PlanSanityHardStop(outcome.stop_report, outcome)


def _commit_repair(plan_path: Path) -> None:
    """Commit a gate repair, mirroring the pipeline's artifact commits."""
    try:
        from duplo.git_ops import commit_artifact

        commit_artifact(plan_path, "plan_sanity_repair")
    except Exception:
        # Committing is best-effort; a missing/locked repo must not mask
        # the repaired plan that is already on disk.
        pass


__all__ = [
    "REPAIRABLE_KINDS",
    "GateOutcome",
    "PlanSanityHardStop",
    "enforce_plan_sanity",
    "run_plan_sanity_gate",
]
