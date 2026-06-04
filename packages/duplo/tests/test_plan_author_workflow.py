"""Workflow-load/parse tests for the duplo-owned ``plan_author`` fork.

``plan_author.orc`` is a DUPLO-OWNED, project-local fork of orchestra's
``iterate_until_acceptable`` (see T-000786). It ships under
``duplo/workflows/`` and is deployed to a duplo-managed project at
``<project>/.orchestra/workflows/plan_author.orc``; Orchestra resolves
that project-local copy ahead of any packaged workflow of the same name.

These tests pin the structural contract the rest of phase 9 depends on:
the new external inputs, the post-accept validation state, the proposer
seeing ``validation_feedback``, the judge cap expressed as ``max_rounds``,
and -- critically -- the validation state's cap routing terminating at
``done`` (so ``run_role`` derives CAPPED) rather than looping to
``max_total_steps`` (which it would derive as ERROR).

No real LLM calls are made: parsing and validation are static. The full
load test registers a stub ``validate_plan_body`` transform so the
workflow validates end-to-end without the (later, T-000787) real
implementation.
"""

from __future__ import annotations

from pathlib import Path

import duplo
from orchestra.loader import load_workflow
from orchestra.loader.parser import parse_workflow
from orchestra.registry.registry import with_core
from orchestra.spine import Comparison, Literal_, Reference

WORKFLOW_PATH = Path(duplo.__file__).resolve().parent / "workflows" / "plan_author.orc"


def _parse():
    return parse_workflow(WORKFLOW_PATH.read_text(), WORKFLOW_PATH)


def _state(workflow, name):
    return next(s for s in workflow.states if s.name == name)


def _role(workflow, name):
    return next(r for r in workflow.roles if r.name == name)


def test_workflow_file_exists_alongside_templates_and_schema():
    """The fork is self-contained: the .orc plus the templates and schema
    it references are co-located so the project-local deploy resolves them
    relative to the workflow's own directory."""
    base = WORKFLOW_PATH.parent
    assert WORKFLOW_PATH.is_file()
    assert (base / "templates" / "plan_author_proposer.md").is_file()
    assert (base / "templates" / "plan_author_reviewer.md").is_file()
    assert (base / "templates" / "plan_author_judge.md").is_file()
    assert (base / "schemas" / "iterate_judge_verdict.json").is_file()


def test_parses_as_plan_author():
    workflow = _parse()
    assert workflow.name == "plan_author"


def test_declares_required_phase_id_and_max_rounds_external_inputs():
    workflow = _parse()
    externals = {e.name: e.type for e in workflow.external_inputs}
    # Base inputs carried over from iterate_until_acceptable.
    assert externals["query"] == "text"
    assert externals["history"] == "text"
    # New inputs added by the fork.
    assert externals["required_phase_id"] == "text"
    assert externals["max_rounds"] == "int"


def test_declares_criteria_block_external_input():
    """The judge prompt's configured-criteria list is supplied as the
    ``criteria_block`` external input (rendered from PLAN_AUTHOR_CRITERIA by
    the adapter), so the judge emits exactly the configured criterion ids."""
    workflow = _parse()
    externals = {e.name: e.type for e in workflow.external_inputs}
    assert externals["criteria_block"] == "text"


def test_judge_role_and_state_consume_criteria_block():
    """The criteria block must reach the judge: it is a template var of the
    judge role and a read of the judge state, or the prompt renders the
    literal ``{criteria_block}`` and the judge never sees the ids."""
    workflow = _parse()
    judge_role = _role(workflow, "judge_role")
    assert "criteria_block" in judge_role.default_prompt.template_vars
    judge_state = _state(workflow, "judge")
    assert "criteria_block" in judge_state.reads


def test_judge_template_injects_criteria_block_and_forbids_invented_ids():
    """The judge template references the injected criteria block and tells
    the judge to emit one entry per configured id and no others, rather
    than pointing vaguely at the question for the criteria."""
    template = (WORKFLOW_PATH.parent / "templates" / "plan_author_judge.md").read_text()
    assert "{criteria_block}" in template
    # Exactly one entry per configured criterion, using only those ids.
    assert "EXACTLY ONE entry" in template
    assert "do not invent" in template.lower()


def test_keeps_proposer_reviewer_judge_states_and_adds_validate():
    workflow = _parse()
    names = [s.name for s in workflow.states]
    assert names == ["propose", "review", "judge", "validate"]
    # The proposer still writes the body artifact the loop refines.
    propose = _state(workflow, "propose")
    assert any(w.name == "proposal" for w in propose.writes)


def test_validate_state_references_validate_plan_body_transform():
    workflow = _parse()
    validate = _state(workflow, "validate")
    assert validate.actor.kind == "transform"
    assert validate.actor.ref == "validate_plan_body"
    # The transform validates the proposed body and writes the gate result.
    assert validate.reads == ("proposal",)
    writes = {w.name: w.type for w in validate.writes}
    assert writes == {"validation_ok": "json", "validation_feedback": "text"}


def test_proposer_reads_validation_feedback():
    """The validation feedback MUST reach the proposer, or a re-draft is
    blind to the canonical-validation failure and the loop cannot
    converge on validation errors."""
    workflow = _parse()
    proposer = _role(workflow, "proposer")
    assert "validation_feedback" in proposer.default_prompt.template_vars
    propose = _state(workflow, "propose")
    assert "validation_feedback" in propose.reads


def test_judge_accept_routes_to_validation_not_done():
    """The judge can no longer accept straight to ``done``; an accepted
    body must pass the validation gate first."""
    workflow = _parse()
    judge = _state(workflow, "judge")
    accept = [t for t in judge.transitions if t.outcome == "accept"]
    assert len(accept) == 1
    assert accept[0].target == "validate"
    # And the accept transition is unconditional (no surprise guard).
    assert accept[0].guard is None


def test_judge_cap_uses_max_rounds_not_hardcoded_six():
    """The hardcoded ``attempts.judge < 6`` cap from the base workflow is
    replaced by the ``max_rounds`` external input."""
    workflow = _parse()
    judge = _state(workflow, "judge")
    iterate_guarded = [
        t for t in judge.transitions if t.outcome == "iterate" and t.guard is not None
    ]
    assert len(iterate_guarded) == 1
    guard = iterate_guarded[0].guard
    assert isinstance(guard, Comparison)
    assert guard.op == "<"
    assert guard.left == Reference(parts=("attempts", "judge"))
    # The right operand is the external input, not a literal 6.
    assert guard.right == Reference(parts=("max_rounds",))
    assert not isinstance(guard.right, Literal_)


def test_validation_cap_routing_terminates_as_capped_not_error():
    """A body that never validates must terminate at ``done`` (a
    non-accept terminal -> CAPPED), not loop until ``max_total_steps``
    (which ``run_role`` would derive as ERROR).

    The cap discipline mirrors the judge: the only re-entry to
    ``propose`` is guarded by ``attempts.judge < max_rounds``, and the
    final unconditional ``on complete`` falls through to the terminal
    ``done`` rather than looping.
    """
    workflow = _parse()
    validate = _state(workflow, "validate")
    complete = [t for t in validate.transitions if t.outcome == "complete"]
    assert len(complete) == 3

    # 1. validation_ok == true => done
    accept = complete[0]
    assert accept.target == "done"
    assert isinstance(accept.guard, Comparison)
    assert accept.guard.op == "=="
    assert accept.guard.left == Reference(parts=("validation_ok",))
    assert accept.guard.right == Literal_(value=True)

    # 2. attempts.judge < max_rounds => propose  (same cap as the judge)
    retry = complete[1]
    assert retry.target == "propose"
    assert isinstance(retry.guard, Comparison)
    assert retry.guard.op == "<"
    assert retry.guard.left == Reference(parts=("attempts", "judge"))
    assert retry.guard.right == Reference(parts=("max_rounds",))

    # 3. unconditional fallback => done  (cap reached, still not valid)
    fallthrough = complete[2]
    assert fallthrough.guard is None
    assert fallthrough.target == "done"

    # The ONLY path back into the loop from the validation gate is the
    # max_rounds-bounded retry; there is no unguarded path to ``propose``
    # that could spin to max_total_steps.
    to_propose = [t for t in validate.transitions if t.target == "propose"]
    assert all(t.guard is not None for t in to_propose)


def _stub_validate_plan_body(inputs, ctx):  # pragma: no cover - never invoked
    return {"validation_ok": True, "validation_feedback": ""}


def test_workflow_loads_and_validates_with_registered_transform():
    """End-to-end loadability: with ``validate_plan_body`` registered
    (as duplo will via run_role's registry_customizer), the fork passes
    full parse + validation, including template and schema resolution
    relative to its own directory."""
    registry = with_core()
    registry.register_transform(
        "validate_plan_body",
        _stub_validate_plan_body,
        input_schema={"proposal": str},
        output_schema={"validation_ok": bool, "validation_feedback": str},
    )
    workflow = load_workflow(WORKFLOW_PATH, registry)
    assert workflow.name == "plan_author"
