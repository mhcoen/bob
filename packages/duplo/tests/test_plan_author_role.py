"""Tests for the duplo-owned ``plan_author`` compound role binding.

``duplo.plan_author_role`` defines the ``plan_author`` Orchestra
compound role (pattern, proposer/reviewer/judge leaf bindings,
``max_rounds``, and role-scoped acceptance criteria) and ``duplo.init``
emits it into ``.orchestra/config.json`` under ``role_bindings``.

These tests pin two contracts:

  - the role resolves through Orchestra's config layer with
    distinct-enough leaf bindings (the reviewer is a different actor
    than the judge, the same independence rule the shared ``design``
    role enforces), and
  - the role-scoped criteria reach the executor via Orchestra phase_001
    extension point A (``CompoundRoleBinding.criteria`` ->
    ``derived_criteria`` -> Executor), and they encode only the
    judgment-level rules the structural validation transform does not
    already enforce.

No LLM is invoked: every assertion runs against parsed config objects
and Orchestra's pure resolution helpers.
"""

from __future__ import annotations

from typing import Any

from orchestra.api.dispatch import _resolve_compound_model_identifiers
from orchestra.config import CompoundRoleBinding, CriterionDecl, OrchestraConfig
from orchestra.executor.criteria import check_decision_consistency, mode_for_workflow

from duplo.init import _ORCHESTRA_COUNCIL_CONFIG
from duplo.plan_author_role import (
    MAX_ROUNDS,
    PLAN_AUTHOR_CRITERIA,
    ROLE_NAME,
    WORKFLOW_PATTERN,
    plan_author_role_binding,
    render_criteria_block,
)


def _compound() -> CompoundRoleBinding:
    """Parse the duplo-written config and return the plan_author binding."""
    cfg = OrchestraConfig.from_dict(_ORCHESTRA_COUNCIL_CONFIG)
    return cfg.role_bindings[ROLE_NAME]


def _configured_criteria() -> tuple[CriterionDecl, ...]:
    """The criterion decls the executor gates plan_author verdicts against.

    These are the exact ``CriterionDecl`` objects dispatch forwards to the
    Executor (extension point A), so running ``check_decision_consistency``
    against them mirrors what the live judge state does at runtime."""
    return tuple(_compound().criteria)


def _compliance(*, compliant: bool) -> list[dict[str, Any]]:
    """A ``criteria_compliance`` array naming exactly the configured ids.

    Sourced from :data:`PLAN_AUTHOR_CRITERIA` -- the same tuple the binding
    feeds the executor -- so the entries carry precisely the ids the
    consistency check expects, never a hardcoded second copy that could
    drift from the configuration."""
    return [
        {"criterion_id": c["id"], "observed_value": "ok", "compliant": compliant}
        for c in PLAN_AUTHOR_CRITERIA
    ]


def test_init_config_carries_plan_author_role_binding():
    """The config ``duplo init`` writes parses cleanly and exposes the
    plan_author compound role under ``role_bindings``."""
    cfg = OrchestraConfig.from_dict(_ORCHESTRA_COUNCIL_CONFIG)
    assert ROLE_NAME in cfg.role_bindings
    compound = cfg.role_bindings[ROLE_NAME]
    assert compound.pattern == "plan_author"
    assert compound.max_rounds == MAX_ROUNDS


def test_distinct_from_shared_design_role():
    """plan_author is its own role, not the shared ``design`` role: a
    different workflow pattern and a proposer slot ``design`` (a
    judge-first loop) does not have."""
    compound = _compound()
    assert compound.pattern != "design_loop"
    assert "proposer" in compound.bindings


def test_leaf_bindings_present_and_keyed_by_workflow_roles():
    """The three leaf bindings are keyed by the workflow's own role
    names so run_workflow can resolve each state's role."""
    compound = _compound()
    assert sorted(compound.bindings) == ["judge_role", "proposer", "reviewer"]


def test_role_resolves_with_distinct_enough_bindings():
    """The bare-model leaf bindings resolve through Orchestra's
    ProfileRegistry, and the reviewer resolves to a different actor than
    the judge so the critique is independent of the judge's training
    data -- the same distinctness the shared ``design`` role requires."""
    compound = _compound()
    resolved = _resolve_compound_model_identifiers(ROLE_NAME, compound.bindings)

    # Every leaf resolves to a concrete (adapter, model) pair.
    for binding in resolved.values():
        assert binding.adapter is not None
        assert binding.model is not None

    proposer = (resolved["proposer"].adapter, resolved["proposer"].model)
    reviewer = (resolved["reviewer"].adapter, resolved["reviewer"].model)
    judge = (resolved["judge_role"].adapter, resolved["judge_role"].model)

    # proposer=opus, judge=opus, reviewer=codex.
    assert proposer == ("claude_code_text", "opus")
    assert judge == ("claude_code_text", "opus")
    assert reviewer == ("codex_text", "gpt-5-codex")

    # The reviewer is a distinct actor from the judge (independence).
    assert reviewer != judge


def test_criteria_reach_the_executor_via_extension_point_a():
    """Extension point A: a compound role's ``criteria`` are forwarded
    to the executor. dispatch.py selects
    ``compound.criteria if compound.criteria else config.criteria``;
    with criteria declared, the plan_author set is what reaches the
    Executor -- not an empty/top-level fallback."""
    cfg = OrchestraConfig.from_dict(_ORCHESTRA_COUNCIL_CONFIG)
    compound = cfg.role_bindings[ROLE_NAME]

    assert compound.criteria, "plan_author must declare role-scoped criteria"

    # Mirror dispatch._run_role's derived_criteria selection.
    derived_criteria = compound.criteria if compound.criteria else cfg.criteria
    assert derived_criteria == compound.criteria

    ids = [c.id for c in derived_criteria]
    assert ids == [
        "task_granularity_5_to_15",
        "batch_user_auto_discipline",
        "feat_fix_annotations_present",
    ]
    # All gate acceptance.
    assert all(c.required for c in derived_criteria)


def test_criteria_encode_judgment_rules_only_not_structural_rules():
    """Criteria cover the judgment-level rules from
    ``planner._PHASE_SYSTEM`` (granularity, [BATCH]/[USER]/[AUTO],
    [feat:]/[fix:]) and must NOT duplicate the hard structural rules
    enforced by the validate_plan_body transform (canonical header,
    required phase id, no ## Bugs, no project H1)."""
    text = " ".join(c["description"] for c in PLAN_AUTHOR_CRITERIA).lower()

    # Judgment-level rules are present.
    assert "5" in text and "15" in text
    assert "[batch]" in text
    assert "[user]" in text
    assert "[auto" in text
    assert "[feat:" in text
    assert "[fix:" in text

    # Structural rules owned by the transform are NOT restated as prose.
    assert "## bugs" not in text
    assert "phase_nnn" not in text
    assert "# h1" not in text


def test_render_criteria_block_enumerates_every_configured_criterion():
    """The judge-prompt criteria block is generated from the same
    ``PLAN_AUTHOR_CRITERIA`` the binding feeds the executor: it lists each
    configured id and description exactly once, so the judge is asked for
    precisely the ids ``check_decision_consistency`` enforces and the two
    cannot drift."""
    block = render_criteria_block()

    for criterion in PLAN_AUTHOR_CRITERIA:
        # The exact id the consistency check expects is named verbatim.
        assert f"id: {criterion['id']}" in block
        # And its description rides along so the judge can grade it.
        assert criterion["description"] in block

    # Exactly one "id:" line per configured criterion -- no extras, none
    # dropped -- matching the executor's exactly-one-entry-per-id rule.
    assert block.count("id: ") == len(PLAN_AUTHOR_CRITERIA)

    # No id from outside the configured set leaks in (the bug was the judge
    # inventing ids from the _PHASE_SYSTEM prose rules).
    configured_ids = {c["id"] for c in PLAN_AUTHOR_CRITERIA}
    emitted_ids = {
        line.split("id: ", 1)[1].strip() for line in block.splitlines() if "id: " in line
    }
    assert emitted_ids == configured_ids


def test_role_binding_dict_matches_declared_constants():
    """The JSON-ready dict the init flow writes uses the module's
    declared model identifiers and round cap (no drift)."""
    binding = plan_author_role_binding()
    assert binding["pattern"] == "plan_author"
    assert binding["max_rounds"] == MAX_ROUNDS
    assert binding["proposer"] == {"model": "opus"}
    assert binding["reviewer"] == {"model": "codex"}
    assert binding["judge_role"] == {"model": "opus"}
    assert len(binding["criteria"]) == len(PLAN_AUTHOR_CRITERIA)


def test_verdict_with_configured_ids_clears_consistency_for_accept_and_iterate():
    """Regression for the plan_author ``missing_ids`` bug (T-000001 fix,
    T-000002 guard): a judge verdict that reports exactly the three
    configured criterion ids -- which the repaired judge prompt now demands
    -- clears ``check_decision_consistency`` for BOTH an ``accept`` (all
    compliant) and an ``iterate`` (a required criterion non-compliant),
    with no ``missing_ids`` and no ``extra_ids``.

    This runs the same pure check the executor runs post-schema in the
    judge state (``_executor_schema``), against the same criteria dispatch
    forwards, so it pins the exact runtime contract the bug violated."""
    configured = _configured_criteria()
    mode = mode_for_workflow(WORKFLOW_PATTERN)

    accept = check_decision_consistency(
        decision="accept",
        criteria_compliance=_compliance(compliant=True),
        configured=configured,
        mode=mode,
    )
    assert accept.ok
    assert accept.reason == ""
    assert accept.missing_ids == ()
    assert accept.extra_ids == ()

    # An iterate verdict that flags a non-compliant criterion (the realistic
    # iterate case) also survives: reporting the configured ids is what keeps
    # the loop alive, independent of the per-criterion compliance values.
    iterate = check_decision_consistency(
        decision="iterate",
        criteria_compliance=_compliance(compliant=False),
        configured=configured,
        mode=mode,
    )
    assert iterate.ok
    assert iterate.missing_ids == ()
    assert iterate.extra_ids == ()


def test_verdict_inventing_ids_is_rejected_as_missing_ids():
    """The regression has teeth: had the judge invented an id from the
    ``_PHASE_SYSTEM`` prose rules (the original bug) instead of using the
    configured ids, the SAME consistency check flags every configured id as
    ``missing`` and the invented one as ``extra`` -- the ``missing_ids``
    failure that routed the judge state through its error outcome."""
    configured = _configured_criteria()
    invented = [
        {
            "criterion_id": "every_item_leaves_project_building",
            "observed_value": "ok",
            "compliant": True,
        },
    ]

    result = check_decision_consistency(
        decision="accept",
        criteria_compliance=invented,
        configured=configured,
        mode=mode_for_workflow(WORKFLOW_PATTERN),
    )

    assert not result.ok
    assert result.reason == "missing_ids"
    assert set(result.missing_ids) == {c["id"] for c in PLAN_AUTHOR_CRITERIA}
    assert "every_item_leaves_project_building" in result.extra_ids
