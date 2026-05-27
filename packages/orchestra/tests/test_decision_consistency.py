"""Tests for orchestra.executor.criteria: decision-consistency invariant.

F2.5a's universal accept-boundary plus iterate's stronger
bidirectional discipline. The pure function ``check_decision_consistency``
takes a verdict, the verdict's ``criteria_compliance`` array, the
configured ``CriterionDecl`` tuple, and a mode, and returns a
``DecisionConsistencyResult``. The cases here pin every reason slug
in the result type plus the mode-specific differences.
"""

from __future__ import annotations

from orchestra.config import CriterionDecl
from orchestra.executor.criteria import (
    DecisionConsistencyMode,
    check_decision_consistency,
    mode_for_workflow,
)


def _conf(*ids: str, required: bool = True) -> tuple[CriterionDecl, ...]:
    return tuple(CriterionDecl(id=cid, description=f"d-{cid}", required=required) for cid in ids)


def _entry(cid: str, *, compliant: bool, observed: str = "x") -> dict[str, object]:
    return {
        "criterion_id": cid,
        "observed_value": observed,
        "compliant": compliant,
    }


# --------------------------------------------------------------------
# Empty configuration: trivially passes
# --------------------------------------------------------------------


def test_empty_configured_passes_for_any_decision() -> None:
    for decision in ("accept", "iterate", "stuck"):
        for mode in DecisionConsistencyMode:
            result = check_decision_consistency(
                decision=decision,
                criteria_compliance=[],
                configured=(),
                mode=mode,
            )
            assert result.ok, f"empty configured should pass for {decision} in {mode}"


# --------------------------------------------------------------------
# Coverage failures: missing, extra, duplicate
# --------------------------------------------------------------------


def test_missing_id_fails() -> None:
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[_entry("a", compliant=True)],
        configured=_conf("a", "b"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert not result.ok
    assert result.reason == "missing_ids"
    assert result.missing_ids == ("b",)


def test_extra_id_fails() -> None:
    result = check_decision_consistency(
        decision="iterate",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("ghost", compliant=True),
        ],
        configured=_conf("a"),
        mode=DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert not result.ok
    assert result.reason == "extra_ids"
    assert result.extra_ids == ("ghost",)


def test_duplicate_id_fails() -> None:
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("a", compliant=False),
        ],
        configured=_conf("a"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert not result.ok
    assert result.reason == "duplicate_ids"
    assert result.duplicate_ids == ("a",)


def test_duplicate_takes_precedence_over_missing() -> None:
    """Coverage checks short-circuit: duplicates reported before missing."""
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("a", compliant=True),
        ],
        configured=_conf("a", "b"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert result.reason == "duplicate_ids"


# --------------------------------------------------------------------
# Accept-boundary invariant
# --------------------------------------------------------------------


def test_accept_with_all_compliant_passes() -> None:
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("b", compliant=True),
        ],
        configured=_conf("a", "b"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert result.ok


def test_accept_with_required_noncompliant_fails() -> None:
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("b", compliant=False),
        ],
        configured=_conf("a", "b"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert not result.ok
    assert result.reason == "accept_with_noncompliant"
    assert result.noncompliant_required_ids == ("b",)


def test_accept_with_optional_noncompliant_passes() -> None:
    """required=False criteria do not gate accept."""
    configured = (
        CriterionDecl(id="a", description="d-a", required=True),
        CriterionDecl(id="b", description="d-b", required=False),
    )
    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("b", compliant=False),
        ],
        configured=configured,
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert result.ok


# --------------------------------------------------------------------
# Mode differences: ACCEPT_ONLY vs STRICT_BIDIRECTIONAL
# --------------------------------------------------------------------


def test_non_accept_with_full_compliance_passes_in_accept_only() -> None:
    """PRJI-style: non-accept routes pass even with all-compliant artifact.

    F2.5b will define non-accept route policy. F2.5a leaves it alone.
    """
    result = check_decision_consistency(
        decision="iterate",
        criteria_compliance=[_entry("a", compliant=True)],
        configured=_conf("a"),
        mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    assert result.ok


def test_non_accept_with_full_compliance_fails_in_strict_bidirectional() -> None:
    """iterate-style: stuck/iterate on a fully-compliant artifact is self-contradictory.

    F2 wording: stuck is "the same material issue persists". A fully-
    compliant artifact has no material issue, so non-accept decisions
    are forbidden.
    """
    result = check_decision_consistency(
        decision="stuck",
        criteria_compliance=[_entry("a", compliant=True)],
        configured=_conf("a"),
        mode=DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert not result.ok
    assert result.reason == "non_accept_with_full_compliance"


def test_non_accept_with_some_noncompliant_passes_in_strict_bidirectional() -> None:
    """iterate-style: non-accept is fine when some required criterion fails."""
    result = check_decision_consistency(
        decision="iterate",
        criteria_compliance=[
            _entry("a", compliant=True),
            _entry("b", compliant=False),
        ],
        configured=_conf("a", "b"),
        mode=DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert result.ok


# --------------------------------------------------------------------
# mode_for_workflow lookup
# --------------------------------------------------------------------


def test_mode_for_prji() -> None:
    assert (
        mode_for_workflow("propose_review_judge_implement") is DecisionConsistencyMode.ACCEPT_ONLY
    )


def test_mode_for_unknown_defaults_to_accept_only() -> None:
    assert mode_for_workflow("some_unknown_workflow") is DecisionConsistencyMode.ACCEPT_ONLY
