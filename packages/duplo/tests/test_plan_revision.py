"""Autonomous revision generation, bounded correction and stopped-project publication."""

import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest
from bob_tools.json_state import StateError
from bob_tools.planfile import load, migrate, parse_plan, save
from mcloop.completion import project_owner
from orchestra.config import RoleBinding

from duplo import plan_revision as revision
from duplo.revision_edits import apply_edits, plan_hash
from duplo.software_design import SoftwareDesignError


@pytest.fixture
def project(tmp_path, monkeypatch, real_local_git):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    save(
        tmp_path / "PLAN.md",
        migrate(
            parse_plan(
                "<!-- bob-plan-format: 1 -->\n## Phase 1: App\n"
                "- [x] T-000001: Existing Core [accept: command-exit: true]\n"
                "- [ ] T-000002: Add runtime [accept: command-exit: true]\n"
            )
        ),
    )
    (tmp_path / "SPEC.md").write_text("Local application with persistent output")
    design = dict.fromkeys(
        ("purpose", "architecture", "data_lifecycle", "failure_behavior", "evolution"),
        "Accepted contracts",
    )
    design["decisions"] = [
        {
            "id": "D-001",
            "title": "Core",
            "choice": "Use existing Core",
            "consequences": "Preserve its ports",
        }
    ]
    record = {"inputs": {}, "design": design, "design_digest": "accepted"}
    monkeypatch.setattr(revision, "_read_state", lambda root: {"attempts": [record]})
    monkeypatch.setattr(revision, "require_design", lambda root, inputs: record)
    monkeypatch.setattr(
        revision,
        "_actors",
        lambda root: (RoleBinding(adapter="codex_text", model="author"), None, []),
    )
    monkeypatch.setattr(
        revision,
        "load_policy",
        lambda root: SimpleNamespace(
            enabled=True,
            model="z-ai/glm-test",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        ),
    )
    return tmp_path


def proposal(root):
    plan = load(root / "PLAN.md")
    return {
        "base_sha256": plan_hash(plan),
        "rationale": "Connect the existing Core to output.",
        "operations": [
            {
                "op": "update",
                "id": "T-000002",
                "task": {"text": "Wire the application and implement smoke.py"},
            },
            {
                "op": "insert_after",
                "anchor": "T-000002",
                "key": "gate",
                "task": {
                    "text": "Exercise the application",
                    "kind": "auto",
                    "annotations": {
                        "milestone": "scaffold",
                        "demonstrates": "persisted visible output",
                        "simulated": "none",
                        "replaces": "none",
                        "accept": "command-exit: python3 smoke.py",
                    },
                },
            },
        ],
    }


def verdict(accept=True):
    return {
        "decision": "accept" if accept else "reject",
        "checks": dict.fromkeys(revision.CHECKS, accept),
        "feedback": "The path is connected." if accept else "T-000002 must wire the entry point.",
    }


def install(monkeypatch, outputs):
    calls = []

    def invoke(root, role, prompt, timeout):
        calls.append((role, prompt))
        result = outputs[len(calls) - 1]
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = result()
        return json.dumps(result), "model.log"

    monkeypatch.setattr(revision, "_review_invoker", lambda policy: invoke)
    return calls


def test_generates_reviewed_candidate_without_changing_active_plan(project, monkeypatch):
    original = (project / "PLAN.md").read_bytes()
    calls = install(monkeypatch, [proposal(project), verdict()])
    receipt = revision.generate_revision(project)
    assert (project / "PLAN.md").read_bytes() == original
    candidate = load(receipt.parent / "PLAN.md")
    assert candidate.phases[0].tasks[0] == load(project / "PLAN.md").phases[0].tasks[0]
    assert candidate.phases[0].tasks[-1].task_id == "T-000003"
    assert calls[1][0].adapter == "openrouter"
    assert "T-000001" in calls[1][1] and "[x]" in calls[1][1]
    assert revision.generate_revision(project) == receipt
    assert len(calls) == 2


@pytest.mark.parametrize("first", ["invalid", "reject"])
def test_repairs_structure_and_review_findings_without_manual_edits(project, monkeypatch, first):
    draft = proposal(project)
    bad = deepcopy(draft)
    bad["operations"][1]["task"]["annotations"].pop("milestone")
    outputs = (
        [bad, draft, verdict()]
        if first == "invalid"
        else [draft, verdict(False), draft, verdict()]
    )
    calls = install(monkeypatch, outputs)
    receipt = revision.generate_revision(project)
    assert receipt.exists()
    assert len(calls) == len(outputs)
    assert "feedback" in calls[-2][1]
    assert (
        "Structural validation failed" in calls[1][1]
        if first == "invalid"
        else ("must wire the entry point" in calls[2][1])
    )


def test_interrupted_review_reuses_completed_author_call(project, monkeypatch):
    calls = install(monkeypatch, [proposal(project), KeyboardInterrupt(), verdict()])
    with pytest.raises(KeyboardInterrupt):
        revision.generate_revision(project)
    receipt = revision.generate_revision(project)
    assert receipt.exists()
    assert len([role for role, _ in calls if role.adapter == "codex_text"]) == 1


def test_rejection_cannot_trigger_unbounded_calls_on_rerun(project, monkeypatch):
    calls = install(
        monkeypatch, [proposal(project), verdict(False), proposal(project), verdict(False)]
    )
    for _ in range(2):
        with pytest.raises(SoftwareDesignError, match="correction allowance"):
            revision.generate_revision(project)
    assert len(calls) == 4
    assert not list((project / ".mcloop/plan-revisions").glob("*/receipt.json"))


def test_project_change_during_call_prevents_staging(project, monkeypatch):
    def change():
        (project / "SPEC.md").write_text("Changed specification")
        return proposal(project)

    calls = install(monkeypatch, [change])
    with pytest.raises(SoftwareDesignError, match="Project changed"):
        revision.generate_revision(project)
    assert len(calls) == 1


def test_running_project_and_input_budget_prevent_model_calls(project, monkeypatch):
    calls = install(monkeypatch, [])
    with project_owner(project), pytest.raises(StateError, match="owns"):
        revision.generate_revision(project)
    with pytest.raises(SoftwareDesignError, match="allowance"):
        revision.generate_revision(project, max_input_bytes=10)
    assert not calls


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.update(base_sha256="wrong"),
        lambda d: d["operations"][0].update(id="T-000001"),
        lambda d: d["operations"].append({"op": "delete", "id": "T-000001"}),
        lambda d: d["operations"][1]["task"].update(deps=["T-999999"]),
    ],
)
def test_model_cannot_rewrite_history_or_invent_dependencies(project, edit):
    from bob_tools.planfile import PlanValidationError

    data = proposal(project)
    edit(data)
    with pytest.raises((StateError, PlanValidationError)):
        apply_edits(load(project / "PLAN.md"), data)


def test_resume_after_staging_does_not_duplicate_proposal(project, monkeypatch):
    calls = install(monkeypatch, [proposal(project), verdict()])
    real = revision.atomic_write_json

    def interrupt(path, value):
        if path.name == "generation.json":
            raise KeyboardInterrupt()
        return real(path, value)

    monkeypatch.setattr(revision, "atomic_write_json", interrupt)
    with pytest.raises(KeyboardInterrupt):
        revision.generate_revision(project)
    monkeypatch.setattr(revision, "atomic_write_json", real)
    receipt = revision.generate_revision(project)
    assert receipt.exists() and len(calls) == 2
    assert len(list((project / ".mcloop/plan-revisions").glob("*/receipt.json"))) == 1


def test_malformed_review_is_corrected_without_reauthoring(project, monkeypatch):
    calls = install(monkeypatch, [proposal(project), {"decision": "accept"}, verdict()])
    receipt = revision.generate_revision(project)
    assert receipt.exists()
    assert [role.adapter for role, _ in calls] == ["codex_text", "openrouter", "openrouter"]


def test_openrouter_request_uses_glm_and_records_accounting(tmp_path, monkeypatch):
    import io

    policy = SimpleNamespace(api_key_env="TEST_REVISION_KEY")
    monkeypatch.setenv("TEST_REVISION_KEY", "fixture-key")
    body = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(verdict())}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.001},
    }
    captured = []

    def request(req, timeout):
        captured.append((req, timeout))
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(revision.urllib.request, "urlopen", request)
    result, path = revision._review_invoker(policy)(
        tmp_path, RoleBinding(adapter="openrouter", model="z-ai/glm-test"), "Review", 300
    )
    req, timeout = captured[0]
    assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
    payload = json.loads(req.data)
    assert payload["model"] == "z-ai/glm-test" and payload["max_tokens"] == 3000
    assert timeout == 90
    assert json.loads((tmp_path / path).read_text())["usage"] == body["usage"]
    assert json.loads(result)["decision"] == "accept"
