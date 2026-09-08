"""Whole-plan publication and recovery using scripted responses."""

import argparse
import json
from types import SimpleNamespace

import pytest
from bob_tools.planfile import load
from orchestra.config import RoleBinding

from duplo import bounded_review as br
from duplo import design_plan as dp
from duplo import software_design as sd
from test_software_design import sample_design, verdict, install, make_inputs


def milestone(kind):
    return (
        f"- [ ] [AUTO:run_cli] Demonstrate the application path "
        f"[milestone: {kind}] [demonstrates: input reaches output] "
        "[simulated: runtime until phase_002] [replaces: none] "
        "[accept: command-exit: python3 smoke.py]\n"
    )


BODY = (
    "## Phase phase_001: Foundation\n\n"
    "- [ ] Establish build settings [accept: command-exit: swift build]\n\n"
    + milestone("scaffold")
    + "## Phase phase_002: Dictation\n\n"
    '- [ ] Implement Dictation [feat: "Dictation"] [accept: command-exit: swift test]\n'
    + milestone("integration")
)


@pytest.fixture(name="inputs")
def example_inputs(tmp_path):
    return make_inputs(tmp_path)


def actors(monkeypatch):
    monkeypatch.setattr(
        dp,
        "_actors",
        lambda root: (
            RoleBinding(adapter="codex_text", model="author"),
            RoleBinding(adapter="claude_code_text", model="reviewer"),
            [],
        ),
    )


def responses(monkeypatch, *, reject=False, mutate=None, interrupt=False, body=BODY):
    calls = []

    def invoke(root, role, prompt, timeout):
        calls.append(prompt)
        if prompt.startswith("Write the complete"):
            return body, "author-log"
        if mutate:
            mutate()
        if interrupt:
            raise KeyboardInterrupt
        return json.dumps(
            {
                "decision": "reject" if reject else "accept",
                "checks": {
                    k: not reject
                    for k in (
                        "requirements",
                        "dependencies",
                        "interfaces",
                        "verification",
                        "scope",
                        "scaffolded_integration",
                    )
                },
                "feedback": "The feature has a preceding build phase and a verification command.",
            }
        ), "review-log"

    monkeypatch.setattr(br, "_invoke", invoke)
    return calls


def accepted(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict()])
    return sd.ensure_design(tmp_path, inputs)


def test_whole_plan_uses_two_calls_then_reuses_receipt(tmp_path, monkeypatch, inputs):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    calls = responses(monkeypatch)
    spec = SimpleNamespace(scope_include=["Dictation"])
    dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path))
    plan = load(tmp_path / "PLAN.md")
    assert len(plan.phases) == 2
    assert all(record["design_digest"] in p.prose for p in plan.phases)
    assert len(calls) == 2
    assert calls[1].count("Worker ownership") == 1
    before = (tmp_path / "PLAN.md").read_bytes()
    dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path))
    assert len(calls) == 2
    assert (tmp_path / "PLAN.md").read_bytes() == before


def test_rejected_plan_is_saved_without_publication_or_automatic_rereview(
    tmp_path, monkeypatch, inputs
):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    calls = responses(monkeypatch, reject=True)
    spec = SimpleNamespace(scope_include=["Dictation"])
    with pytest.raises(sd.SoftwareDesignError, match="not accepted"):
        dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path))
    assert not (tmp_path / "PLAN.md").exists()
    receipt = json.loads((tmp_path / ".duplo/design-plan.json").read_text())
    assert receipt["review"]["decision"] == "reject"
    assert "phase_002" in receipt["candidate"]
    with pytest.raises(sd.SoftwareDesignError, match="already used"):
        dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path))
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["SPEC.md", "PLAN.md"])
def test_plan_review_preserves_concurrent_edits(tmp_path, monkeypatch, inputs, name):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    responses(monkeypatch, mutate=lambda: (tmp_path / name).write_text("User edit"))
    with pytest.raises(sd.SoftwareDesignError, match="stale|changed"):
        dp.generate_design_plan(
            tmp_path,
            inputs,
            record,
            SimpleNamespace(scope_include=["Dictation"]),
            br.ReviewSession(tmp_path),
        )
    assert (tmp_path / name).read_text() == "User edit"


def test_missing_scope_is_rejected_before_plan_review(tmp_path, monkeypatch, inputs):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    calls = responses(monkeypatch, body=BODY.split("## Phase phase_002")[0])
    with pytest.raises(sd.SoftwareDesignError, match="requires correction"):
        dp.generate_design_plan(
            tmp_path,
            inputs,
            record,
            SimpleNamespace(scope_include=["Dictation"]),
            br.ReviewSession(tmp_path),
        )
    assert len(calls) == 1
    assert not (tmp_path / "PLAN.md").exists()


def test_resume_after_interrupted_review_needs_explicit_allowance(tmp_path, monkeypatch, inputs):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    responses(monkeypatch, interrupt=True)
    spec = SimpleNamespace(scope_include=["Dictation"])
    with pytest.raises(KeyboardInterrupt):
        dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path))
    calls = responses(monkeypatch)
    dp.generate_design_plan(tmp_path, inputs, record, spec, br.ReviewSession(tmp_path, reset=True))
    assert len(calls) == 1
    assert calls[0].startswith("Review the complete")
    assert len(load(tmp_path / "PLAN.md").phases) == 2


def test_command_wires_shared_budget_and_whole_plan(tmp_path, monkeypatch):
    from duplo import design_command

    monkeypatch.chdir(tmp_path)
    (tmp_path / "SPEC.md").write_text(
        "## Purpose\nBuild local dictation with recoverable recordings and explicit delivery ownership.\n"
        "## Architecture\n- platform: macos\n  language: swift\n  build: spm\n"
        "## Scope\ninclude:\n  - Dictation\n"
    )
    install(monkeypatch, [sample_design()], [verdict()])
    design_command.run_design(argparse.Namespace(refresh=False, plan=False))
    actors(monkeypatch)
    calls = responses(monkeypatch)
    design_command.run_design(argparse.Namespace(refresh=False, plan=True))
    assert len(calls) == 2
    session = br.ReviewSession(tmp_path)
    assert len(session.current["calls"]) == 5
    assert len(load(tmp_path / "PLAN.md").phases) == 2


def test_new_plan_cannot_claim_completed_work(tmp_path, monkeypatch, inputs):
    record = accepted(tmp_path, monkeypatch, inputs)
    actors(monkeypatch)
    calls = responses(monkeypatch, body=BODY.replace("[ ]", "[x]"))
    with pytest.raises(sd.SoftwareDesignError, match="unchecked"):
        dp.generate_design_plan(
            tmp_path,
            inputs,
            record,
            SimpleNamespace(scope_include=["Dictation"]),
            br.ReviewSession(tmp_path),
        )
    assert len(calls) == 1
    assert not (tmp_path / "PLAN.md").exists()


def test_whole_plan_uses_effective_project_criteria(tmp_path, monkeypatch):
    from orchestra.config import OrchestraConfig

    config = OrchestraConfig.from_dict(
        {
            "role_bindings": {
                "plan_author": {
                    "pattern": "plan_author",
                    "proposer": {"model": "opus"},
                    "reviewer": {"model": "codex"},
                    "judge_role": {"model": "opus"},
                    "criteria": [
                        {
                            "id": "recovery_contract",
                            "description": "Preserve recovery ownership.",
                            "required": True,
                        }
                    ],
                }
            }
        }
    )
    monkeypatch.setattr(dp, "load_config", lambda **kwargs: config)
    _, _, criteria = dp._actors(tmp_path)
    assert [c["id"] for c in criteria] == ["recovery_contract"]
    required = {"recovery_contract": True, "optional_naming": False}
    review = {
        "decision": "accept",
        "checks": {"recovery_contract": False, "optional_naming": True},
        "feedback": "Recovery work is missing.",
    }
    with pytest.raises(sd.SoftwareDesignError, match="not accepted"):
        dp._validate_review(review, required)
    review["checks"] = {"recovery_contract": True, "optional_naming": False}
    dp._validate_review(review, required)
