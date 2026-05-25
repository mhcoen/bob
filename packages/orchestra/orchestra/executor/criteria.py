"""F2.5a runtime decision-consistency invariant.

The judge's verdict carries a ``criteria_compliance`` array (one entry
per configured criterion). This module enforces three invariants the
JSON Schema cannot express cleanly:

  - ID uniqueness within ``criteria_compliance``.
  - ID coverage: the set of ``criterion_id`` values exactly matches
    the configured ``CriterionDecl`` ids; no missing, no extras.
  - Decision consistency: a route-specific check tying ``decision``
    to the per-criterion compliance values.

Two consistency modes are supported, each tied to a workflow's
decision-derivation policy:

  - ``DecisionConsistencyMode.ACCEPT_ONLY``: only forbids
    ``decision="accept"`` when any required criterion is non-compliant.
    Non-accept decisions pass through unchecked. This is the
    universal accept-boundary invariant, suitable for PRJI where
    non-accept routes (``implement``, ``rereview``, ``reframe``,
    ``stuck``) carry route-specific policy beyond F2.5a's scope. See
    design/criteria-compliance.md for the F2.5b deferral.

  - ``DecisionConsistencyMode.STRICT_BIDIRECTIONAL``: in addition to
    the accept-boundary invariant, requires that any non-accept
    decision corresponds to at least one non-compliant required
    criterion. This rules out the F2 self-contradiction case where
    ``stuck`` fires on a fully-compliant artifact (no material
    issue to persist). Used by iterate, where the implicit invariant
    in the F2 prompt is now structurally enforced.

The check is a pure function. The executor calls it post-schema-
validation; on failure it logs an ``accept_consistency`` event and
returns an ``ErrorRecord`` so the state exits via the error outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from orchestra.config import CriterionDecl


class DecisionConsistencyMode(StrEnum):
    """Per-workflow enforcement strength for decision-consistency."""

    ACCEPT_ONLY = "accept_only"
    STRICT_BIDIRECTIONAL = "strict_bidirectional"


@dataclass(frozen=True)
class DecisionConsistencyResult:
    """Outcome of a single decision-consistency check.

    Attributes:
        ok: True iff every invariant the chosen mode enforces holds.
        reason: short slug identifying the violation kind on failure;
            empty when ok is True. One of: "missing_ids", "extra_ids",
            "duplicate_ids", "accept_with_noncompliant",
            "non_accept_with_full_compliance".
        missing_ids: configured criterion ids absent from
            criteria_compliance.
        extra_ids: criterion_ids present in criteria_compliance but
            not configured.
        duplicate_ids: criterion_ids that appear more than once in
            criteria_compliance.
        noncompliant_required_ids: configured criterion ids with
            ``required=True`` whose compliance entry has
            ``compliant=False``.
    """

    ok: bool
    reason: str
    missing_ids: tuple[str, ...]
    extra_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    noncompliant_required_ids: tuple[str, ...]


_REQUIRED_ENTRY_KEYS = ("criterion_id", "observed_value", "compliant")


def _coverage(
    criteria_compliance: list[dict[str, Any]],
    configured: tuple[CriterionDecl, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Compute (missing, extra, duplicate) id sets."""
    seen_counts: dict[str, int] = {}
    for entry in criteria_compliance:
        cid_raw = entry.get("criterion_id")
        if not isinstance(cid_raw, str):
            continue
        seen_counts[cid_raw] = seen_counts.get(cid_raw, 0) + 1
    seen_ids = set(seen_counts)
    duplicates = tuple(
        sorted(cid for cid, count in seen_counts.items() if count > 1)
    )
    configured_ids = {c.id for c in configured}
    missing = tuple(sorted(configured_ids - seen_ids))
    extras = tuple(sorted(seen_ids - configured_ids))
    return missing, extras, duplicates


def _required_noncompliant(
    criteria_compliance: list[dict[str, Any]],
    configured: tuple[CriterionDecl, ...],
) -> tuple[str, ...]:
    """Return ids of required criteria whose entry has compliant=False."""
    required_ids = {c.id for c in configured if c.required}
    by_id: dict[str, dict[str, Any]] = {}
    for entry in criteria_compliance:
        cid = entry.get("criterion_id")
        if isinstance(cid, str) and cid in required_ids:
            # If duplicates exist the coverage check already flags
            # them; here we just take the first observed entry per id.
            by_id.setdefault(cid, entry)
    bad: list[str] = []
    for cid, entry in by_id.items():
        if entry.get("compliant") is not True:
            bad.append(cid)
    return tuple(sorted(bad))


def check_decision_consistency(
    decision: str,
    criteria_compliance: list[dict[str, Any]],
    configured: tuple[CriterionDecl, ...],
    mode: DecisionConsistencyMode,
) -> DecisionConsistencyResult:
    """Validate a verdict's ``decision`` against its ``criteria_compliance``.

    Returns a ``DecisionConsistencyResult`` describing the outcome.
    The check is short-circuit: coverage failures (missing/extra/
    duplicate ids) take precedence over decision-vs-compliance
    failures, since the latter is undefined when coverage is wrong.

    When ``configured`` is empty, all checks trivially pass (no
    criteria configured ⇒ no constraint to enforce).
    """
    if not configured:
        return DecisionConsistencyResult(
            ok=True,
            reason="",
            missing_ids=(),
            extra_ids=(),
            duplicate_ids=(),
            noncompliant_required_ids=(),
        )
    missing, extras, duplicates = _coverage(criteria_compliance, configured)
    if duplicates:
        return DecisionConsistencyResult(
            ok=False,
            reason="duplicate_ids",
            missing_ids=missing,
            extra_ids=extras,
            duplicate_ids=duplicates,
            noncompliant_required_ids=(),
        )
    if missing:
        return DecisionConsistencyResult(
            ok=False,
            reason="missing_ids",
            missing_ids=missing,
            extra_ids=extras,
            duplicate_ids=(),
            noncompliant_required_ids=(),
        )
    if extras:
        return DecisionConsistencyResult(
            ok=False,
            reason="extra_ids",
            missing_ids=(),
            extra_ids=extras,
            duplicate_ids=(),
            noncompliant_required_ids=(),
        )
    noncompliant = _required_noncompliant(criteria_compliance, configured)
    if decision == "accept" and noncompliant:
        return DecisionConsistencyResult(
            ok=False,
            reason="accept_with_noncompliant",
            missing_ids=(),
            extra_ids=(),
            duplicate_ids=(),
            noncompliant_required_ids=noncompliant,
        )
    if (
        mode is DecisionConsistencyMode.STRICT_BIDIRECTIONAL
        and decision != "accept"
        and not noncompliant
    ):
        return DecisionConsistencyResult(
            ok=False,
            reason="non_accept_with_full_compliance",
            missing_ids=(),
            extra_ids=(),
            duplicate_ids=(),
            noncompliant_required_ids=(),
        )
    return DecisionConsistencyResult(
        ok=True,
        reason="",
        missing_ids=(),
        extra_ids=(),
        duplicate_ids=(),
        noncompliant_required_ids=(),
    )


# Workflow → mode mapping. iterate's stuck/iterate decisions both
# imply a material issue persists, so STRICT_BIDIRECTIONAL is the
# correct discipline. PRJI's non-accept routes (implement, rereview,
# reframe) carry route-specific semantics that F2.5b will cover; for
# F2.5a only the accept boundary is enforced.
_DECISION_CONSISTENCY_MODE_BY_WORKFLOW: dict[str, DecisionConsistencyMode] = {
    "iterate_until_acceptable": DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    "propose_review_judge_implement": DecisionConsistencyMode.ACCEPT_ONLY,
}


def mode_for_workflow(workflow_name: str) -> DecisionConsistencyMode:
    """Return the configured decision-consistency mode for a workflow.

    Defaults to ACCEPT_ONLY for unknown workflows: the universal
    accept-boundary invariant always applies; the stronger
    bidirectional check is per-workflow opt-in.
    """
    return _DECISION_CONSISTENCY_MODE_BY_WORKFLOW.get(
        workflow_name, DecisionConsistencyMode.ACCEPT_ONLY
    )


__all__ = [
    "DecisionConsistencyMode",
    "DecisionConsistencyResult",
    "check_decision_consistency",
    "mode_for_workflow",
]
