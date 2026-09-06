"""Exercise design review and publication with scripted model responses."""

from __future__ import annotations

import copy
import json

import pytest

from duplo import software_design as sd
from duplo.extractor import Feature
from duplo.questioner import BuildPreferences
from test_plan_author_e2e import _ScriptedModelAdapter


def sample_design(names=("Dictation",)):
    return {
        "purpose": "Insert locally recognized speech into the intended application.",
        "architecture": "A coordinator owns capture and schedules a separate inference worker.",
        "data_lifecycle": "Persist audio chunks before scheduling inference. Results name their input revision.",
        "failure_behavior": "An interrupted worker can retry recognition. Delivery requires a new user action after recovery.",
        "evolution": "Keep runtime-specific segmentation behind the worker interface.",
        "verification": "Review target selection before testing it. Test assertions may encode mistaken expectations.",
        "assumptions": [
            "Evaluate on Apple Silicon first; revisit platform support after measurements."
        ],
        "decisions": [
            {
                "id": "D-001",
                "title": "Worker ownership",
                "choice": "Use a separate inference worker.",
                "rationale": "A worker exit leaves capture and saved audio available.",
                "alternatives": [
                    {
                        "option": "Run inference in the UI process",
                        "tradeoff": "Simpler calls, with shared failure and memory pressure.",
                    }
                ],
                "consequences": "Version the request protocol and account for worker startup.",
                "reconsider_when": "Measured startup dominates short dictation latency.",
            }
        ],
        "coverage": [
            {
                "requirement": name,
                "decision_ids": ["D-001"],
                "explanation": "The coordinator owns the session across worker restarts.",
            }
            for name in names
        ],
        "open_questions": [],
    }


def verdict(decision="accept", **changes):
    checks = {
        k: True
        for k in sd._schema("software_design_verdict.json")["properties"]["checks"]["required"]
    }
    checks.update(changes)
    return json.dumps(
        {
            "decision": decision,
            "feedback": "Reviewed the worker lifetime and recovery policy.",
            "checks": checks,
        }
    )


def install(monkeypatch, proposals, judgments):
    adapter = _ScriptedModelAdapter(
        {
            "propose": [json.dumps(p) for p in proposals],
            "review": ["Check delivery after a worker restart." for _ in proposals],
            "judge": judgments,
        }
    )
    register = sd._register_validation

    def custom(requirements):
        inner = register(requirements)

        def configure(registry):
            inner(registry)
            registry.actor_backings["model"] = lambda: adapter
            registry._adapter_cache.pop("model", None)

        return configure

    monkeypatch.setattr(sd, "_register_validation", custom)
    return adapter


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "SPEC.md").write_text("## Purpose\nLocal dictation with recoverable recordings.\n")
    return sd.design_inputs(
        tmp_path,
        "Local dictation",
        [Feature("Dictation", "Speech to text", "core")],
        [BuildPreferences("macos", "swift", [], [])],
    )


def test_real_workflow_revises_blocking_design_and_binds_plan(tmp_path, monkeypatch, inputs):
    blocked = sample_design()
    blocked["open_questions"] = [
        {
            "question": "Who owns delivery after recovery?",
            "blocking": True,
            "resolution": "Choose a delivery policy.",
        }
    ]
    adapter = install(monkeypatch, [blocked, sample_design()], [verdict(), verdict()])
    record = sd.ensure_design(tmp_path, inputs)
    prompts = adapter.proposer_prompts()
    assert len(prompts) == 2
    assert "unresolved blocking questions" in prompts[1]
    assert "Local dictation" in prompts[0]
    assert "Worker ownership" in next(
        c["prompt"] for c in adapter.calls if c["state_id"] == "review"
    )
    assert sd.require_design(tmp_path, inputs)["id"] == record["id"]
    assert "D-001" in (tmp_path / sd.DESIGN_FILE).read_text()
    from duplo.council import typed_plan_from_synthesizer_text
    from duplo.planner import save_plan
    from bob_tools.planfile import load

    plan = typed_plan_from_synthesizer_text(
        "## Phase phase_001: Capture\n\n- [ ] Create capture.swift [accept: command-exit: true]\n",
        required_phase_id="phase_001",
    )
    save_plan(sd.bind_phase_plan(plan, record, ["Dictation"]), target_dir=tmp_path)
    assert f"sha256:{record['design_digest']}" in load(tmp_path / "PLAN.md").phases[0].prose
    assert "decisions: D-001" in load(tmp_path / "PLAN.md").phases[0].prose


def test_iteration_cap_cannot_publish_a_structurally_valid_unaccepted_design(
    tmp_path, monkeypatch, inputs
):
    install(monkeypatch, [sample_design()] * 4, [verdict("iterate", alternatives=False)] * 4)
    with pytest.raises(sd.SoftwareDesignError, match="not accepted"):
        sd.ensure_design(tmp_path, inputs)
    assert not (tmp_path / sd.DESIGN_FILE).exists()
    assert not (tmp_path / "PLAN.md").exists()
    assert not sd._read_state(tmp_path)["attempts"][-1]["accepted"]


def test_false_judge_check_refuses_publication(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict(maintainability=False)])
    with pytest.raises(sd.SoftwareDesignError, match="accept every"):
        sd.ensure_design(tmp_path, inputs)
    assert not (tmp_path / sd.DESIGN_FILE).exists()


def test_retry_carries_rejected_proposal_and_review_without_accepting_it(
    tmp_path, monkeypatch, inputs
):
    install(monkeypatch, [sample_design()], [verdict("stuck", failures=False)])
    with pytest.raises(sd.SoftwareDesignError, match="not accepted"):
        sd.ensure_design(tmp_path, inputs)
    rejected = sd._read_state(tmp_path)["attempts"][-1]
    assert not (tmp_path / sd.DESIGN_FILE).exists()

    adapter = install(monkeypatch, [sample_design()], [verdict()])
    record = sd.ensure_design(tmp_path, inputs)
    prompt = adapter.proposer_prompts()[0]
    assert "Worker ownership" in prompt
    assert rejected["review"] in prompt
    assert '"accepted": false' in prompt
    assert sd._read_state(tmp_path)["attempts"][0] == rejected
    assert sd.require_design(tmp_path, inputs)["id"] == record["id"]


@pytest.mark.parametrize("change", ["spec", "reference", "document"])
def test_changes_during_review_preserve_originals(tmp_path, monkeypatch, inputs, change):
    if change == "reference":
        (tmp_path / "ref").mkdir()
        (tmp_path / "ref/context.md").write_text("Initial context")
        (tmp_path / "SPEC.md").write_text("## References\n- ref/context.md\n  role: docs\n")
        inputs["files"] = sd._files(tmp_path)
    adapter = install(monkeypatch, [sample_design()], [verdict()])
    invoke = adapter.invoke

    def mutate(prepared):
        if prepared.inner["state_id"] == "judge":
            name = {"spec": "SPEC.md", "reference": "ref/context.md", "document": sd.DESIGN_FILE}[
                change
            ]
            (tmp_path / name).write_text("User edit")
        return invoke(prepared)

    monkeypatch.setattr(adapter, "invoke", mutate)
    with pytest.raises(sd.SoftwareDesignError, match="changed during review"):
        sd.ensure_design(tmp_path, inputs)
    if change == "document":
        assert (tmp_path / sd.DESIGN_FILE).read_text() == "User edit"


def test_changed_requirements_invalidate_cached_review(tmp_path, monkeypatch, inputs):
    adapter = install(monkeypatch, [sample_design()], [verdict()])
    sd.ensure_design(tmp_path, inputs)
    calls = len(adapter.calls)
    sd.ensure_design(tmp_path, inputs)
    assert len(adapter.calls) == calls
    changed = copy.deepcopy(inputs)
    changed["requirements"][0]["description"] = "Changed behavior"
    with pytest.raises(sd.SoftwareDesignError, match="stale"):
        sd.require_design(tmp_path, changed)


def test_interrupted_refresh_blocks_reuse_and_keeps_old_design(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict()])
    sd.ensure_design(tmp_path, inputs)
    before = (tmp_path / sd.DESIGN_FILE).read_bytes()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(sd.orchestra, "run_workflow", interrupt)
    with pytest.raises(KeyboardInterrupt):
        sd.ensure_design(tmp_path, inputs, refresh=True)
    assert (tmp_path / sd.DESIGN_FILE).read_bytes() == before
    with pytest.raises(sd.SoftwareDesignError, match="No accepted"):
        sd.require_design(tmp_path, inputs)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "missing", "empty"])
def test_invalid_design_references(mutation):
    design = sample_design()
    if mutation == "duplicate":
        design["decisions"] *= 2
    elif mutation == "unknown":
        design["coverage"][0]["decision_ids"] = ["D-999"]
    elif mutation == "missing":
        design["coverage"][0]["requirement"] = "Unrequested feature"
    else:
        design["decisions"][0]["alternatives"] = []
    with pytest.raises(sd.SoftwareDesignError):
        sd.validate_design(design, ["Dictation"])


def test_corrupt_state_is_preserved(tmp_path, inputs):
    path = tmp_path / sd.STATE_FILE
    path.parent.mkdir()
    path.write_text('{"schema_version":999,"attempts":[]}')
    before = path.read_bytes()
    with pytest.raises(sd.SoftwareDesignError, match="Unsupported"):
        sd.ensure_design(tmp_path, inputs)
    assert path.read_bytes() == before


def test_structured_platform_read_without_model_call():
    from duplo.spec_reader import _parse_spec

    spec = _parse_spec(
        "## Architecture\n- platform: macos\n  language: swift\n  build: spm\n\nLocal inference.\n"
    )
    assert len(spec.platform_entries) == 1
    assert spec.platform_entries[0].language == "swift"


def test_feature_completion_does_not_invalidate_design_inputs(tmp_path, inputs):
    features = [
        Feature(
            "Dictation", "Speech to text", "core", status="implemented", implemented_in="phase_001"
        )
    ]
    after = sd.design_inputs(
        tmp_path, "Local dictation", features, [BuildPreferences("macos", "swift", [], [])]
    )
    assert after == inputs


@pytest.mark.parametrize("changed_file", ["SPEC.md", "PLAN.md"])
def test_phase_generation_refuses_changed_inputs_before_save(
    tmp_path, monkeypatch, inputs, changed_file
):
    from duplo import pipeline
    from duplo.council import typed_plan_from_synthesizer_text

    monkeypatch.chdir(tmp_path)
    install(monkeypatch, [sample_design()], [verdict()])
    record = sd.ensure_design(tmp_path, inputs)

    def generate(*args, **kwargs):
        (tmp_path / changed_file).write_text("Changed while planning")
        return typed_plan_from_synthesizer_text(
            "## Phase phase_001: Capture\n\n- [ ] Create capture.swift [accept: command-exit: true]\n",
            required_phase_id="phase_001",
        )

    monkeypatch.setattr(pipeline, "generate_phase_plan", generate)
    monkeypatch.setattr(pipeline, "load_frame_descriptions", lambda: [])
    count, total = pipeline._run_phase_generation_loop(
        roadmap=[{"phase": 0, "title": "Capture", "features": ["Dictation"]}],
        start_idx=0,
        phases_completed=0,
        source_url="",
        features=[Feature("Dictation", "Speech to text", "core")],
        preferences=[BuildPreferences("macos", "swift", [], [])],
        spec=None,
        spec_prompt="Local dictation",
        platform_addendum="",
        prior_phases_files=[],
        project_name="Dictation",
        software_design=record,
    )
    assert (count, total) == (0, 1)
    if changed_file == "PLAN.md":
        assert (tmp_path / "PLAN.md").read_text() == "Changed while planning"
    else:
        assert not (tmp_path / "PLAN.md").exists()


def test_command_resumes_only_unsaved_phases(tmp_path, monkeypatch):
    import argparse
    from duplo import design_command, planner, roadmap
    from duplo.plan_author_adapter import PlanAuthorError
    from bob_tools.planfile import load

    monkeypatch.chdir(tmp_path)
    (tmp_path / "SPEC.md").write_text(
        "## Purpose\nBuild local dictation with recoverable recordings and explicit delivery ownership.\n"
        "## Architecture\n- platform: macos\n  language: swift\n  build: spm\n"
        "## Scope\ninclude:\n  - Dictation\n"
    )
    adapter = install(monkeypatch, [sample_design()], [verdict()])
    monkeypatch.setattr(
        roadmap,
        "query",
        lambda *args, **kwargs: json.dumps(
            [
                {
                    "phase": 0,
                    "title": "Scaffold",
                    "goal": "Start",
                    "features": [],
                    "test": "Build",
                },
                {
                    "phase": 1,
                    "title": "Dictation",
                    "goal": "Record",
                    "features": ["Dictation"],
                    "test": "Record",
                },
            ]
        ),
    )
    calls = []
    fail = True

    def author(**kwargs):
        nonlocal fail
        phase_id = kwargs["required_phase_id"]
        calls.append(phase_id)
        assert "Worker ownership" in kwargs["prompt"]
        if phase_id == "phase_002" and fail:
            fail = False
            raise PlanAuthorError("Interrupted phase authoring")
        feature = ' [feat: "Dictation"]' if phase_id == "phase_002" else ""
        return f"## Phase {phase_id}: Build\n\n- [ ] Create capture.swift{feature} [accept: command-exit: true]\n"

    monkeypatch.setattr(planner, "run_plan_author", author)
    args = argparse.Namespace(refresh=False, plan=True)
    with pytest.raises(PlanAuthorError):
        design_command.run_design(args)
    first = load(tmp_path / "PLAN.md").phases[0]
    model_calls = len(adapter.calls)
    design_command.run_design(args)
    plan = load(tmp_path / "PLAN.md")
    assert len(plan.phases) == 2
    assert plan.phases[0].phase_id == first.phase_id
    assert plan.phases[0].prose == first.prose
    assert [(t.task_id, t.text, t.status, t.annotations) for t in plan.phases[0].tasks] == [
        (t.task_id, t.text, t.status, t.annotations) for t in first.tasks
    ]
    assert calls == ["phase_001", "phase_002", "phase_002"]
    assert len(adapter.calls) == model_calls
    assert all("decisions: D-001" in p.prose for p in plan.phases)


def test_same_actor_configuration_is_rejected(tmp_path, monkeypatch):
    from orchestra.config import OrchestraConfig

    config = OrchestraConfig.from_dict(
        {
            "role_bindings": {
                "software_design": {
                    "pattern": "software_design",
                    "max_rounds": 4,
                    "author": {"model": "opus"},
                    "reviewer": {"model": "opus"},
                    "judge_role": {"model": "opus"},
                }
            }
        }
    )
    monkeypatch.setattr(sd, "load_config", lambda **kwargs: config)
    with pytest.raises(sd.SoftwareDesignError, match="different actor"):
        sd._configuration(tmp_path)


def test_changed_review_policy_requires_a_new_review(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict()])
    sd.ensure_design(tmp_path, inputs)
    previous = (tmp_path / sd.DESIGN_FILE).read_bytes()
    monkeypatch.setattr(sd, "_policy_digest", lambda: "changed-policy")
    with pytest.raises(sd.SoftwareDesignError, match="policy changed"):
        sd.require_design(tmp_path, inputs)
    assert (tmp_path / sd.DESIGN_FILE).read_bytes() == previous
