"""Exercise finite review and reservation failures without provider calls."""

import copy
import json

import pytest
from orchestra.config import RoleBinding

from duplo import bounded_review as br
from duplo import software_design as sd
from test_software_design import sample_design, verdict, install, make_inputs

ROLE = RoleBinding(adapter="codex_text", model="test")


@pytest.fixture(name="inputs")
def example_inputs(tmp_path):
    return make_inputs(tmp_path)


def limits(root, **values):
    path = root / ".duplo/review-limits.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(values))


def test_call_cap_survives_restart_and_explicit_reset_keeps_evidence(tmp_path, monkeypatch):
    limits(tmp_path, max_calls=1)
    calls = []
    monkeypatch.setattr(br, "_invoke", lambda *a: (calls.append(a) or "ok", "log"))
    br.ReviewSession(tmp_path).call("test", ROLE, "Instructions", {})
    with pytest.raises(sd.SoftwareDesignError, match="exhausted"):
        br.ReviewSession(tmp_path).call("test", ROLE, "Instructions", {})
    fresh = br.ReviewSession(tmp_path, reset=True)
    fresh.call("test", ROLE, "Instructions", {})
    assert len(calls) == 2
    assert len(fresh.state["sessions"]) == 2
    assert fresh.state["sessions"][0]["calls"][0]["output"] == "ok"


def test_interruption_consumes_reserved_call(tmp_path, monkeypatch):
    limits(tmp_path, max_calls=1)

    def interrupt(*a):
        state = json.loads((tmp_path / ".duplo/review-budget.json").read_text())
        assert state["sessions"][-1]["calls"][-1]["status"] == "reserved"
        raise KeyboardInterrupt

    monkeypatch.setattr(br, "_invoke", interrupt)
    with pytest.raises(KeyboardInterrupt):
        br.ReviewSession(tmp_path).call("test", ROLE, "Instructions", {})
    with pytest.raises(sd.SoftwareDesignError, match="exhausted"):
        br.ReviewSession(tmp_path).call("test", ROLE, "Instructions", {})


@pytest.mark.parametrize(
    "settings",
    [
        {"max_prompt_bytes": 2},
        {"max_total_prompt_bytes": 2},
        {"max_tokens": 100},
        {"max_calls": True},
        {"max_calls": -1},
    ],
)
def test_invalid_or_exceeded_limits_make_no_call(tmp_path, monkeypatch, settings):
    limits(tmp_path, **settings)
    calls = []
    monkeypatch.setattr(br, "_invoke", lambda *a: calls.append(a))
    with pytest.raises(sd.SoftwareDesignError):
        br.ReviewSession(tmp_path).call("test", ROLE, "Instructions", {})
    assert calls == []


def test_oversized_output_is_preserved_and_rejected(tmp_path, monkeypatch):
    limits(tmp_path, max_output_bytes=2)
    monkeypatch.setattr(br, "_invoke", lambda *a: ("oversized", "log"))
    session = br.ReviewSession(tmp_path)
    with pytest.raises(sd.SoftwareDesignError, match="output-size"):
        session.call("test", ROLE, "Instructions", {})
    assert session.current["calls"][0]["output"] == "oversized"
    assert session.current["calls"][0]["status"] == "failed"


def test_patch_preserves_unedited_decisions_and_rejects_stale_base():
    original = sample_design()
    patch = {
        "base_digest": sd._digest(original),
        "replace": {"purpose": "Revised purpose"},
        "decisions": [],
    }
    updated = br.apply_patch(original, patch, ["Dictation"])
    assert updated["decisions"] == original["decisions"]
    assert original["purpose"] != updated["purpose"]
    with pytest.raises(sd.SoftwareDesignError, match="digest"):
        br.apply_patch(updated, patch, ["Dictation"])


@pytest.mark.parametrize("change", ["unknown", "duplicate", "coverage"])
def test_invalid_patch_leaves_original_intact(change):
    original = sample_design()
    before = copy.deepcopy(original)
    patch = {"base_digest": sd._digest(original), "replace": {}, "decisions": []}
    if change == "unknown":
        patch["replace"]["made_up_section"] = "value"
    elif change == "duplicate":
        patch["decisions"] = original["decisions"] * 2
    else:
        patch["replace"]["coverage"] = []
    with pytest.raises(sd.SoftwareDesignError):
        br.apply_patch(original, patch, ["Dictation"])
    assert original == before


def test_judge_cannot_accept_open_blocker_or_omit_disposition():
    finding = {"id": "F001", "blocking": True}
    judgment = {"verdict": json.loads(verdict()), "dispositions": []}
    with pytest.raises(sd.SoftwareDesignError, match="Every finding"):
        br.adjudicate(judgment, [finding])
    judgment["dispositions"] = [{"id": "F001", "status": "fix", "reason": "Still broken"}]
    with pytest.raises(sd.SoftwareDesignError, match="blocking finding"):
        br.adjudicate(judgment, [finding])


def test_recovered_object_proposal_starts_with_review(tmp_path, monkeypatch, inputs):
    record = {
        "id": "recovered",
        "accepted": False,
        "inputs": inputs,
        "input_digest": sd._digest(inputs),
        "proposal": sample_design(),
    }
    path = tmp_path / sd.STATE_FILE
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "attempts": [record]}))
    adapter = install(monkeypatch, [], [verdict()])
    sd.ensure_design(tmp_path, inputs)
    assert [c["state_id"] for c in adapter.calls] == ["review", "judge"]
    assert adapter.calls[0]["prompt"].count("Worker ownership") == 1


def test_budget_stop_preserves_unaccepted_candidate(tmp_path, monkeypatch, inputs):
    limits(tmp_path, max_calls=1)
    adapter = install(monkeypatch, [sample_design()], [])
    with pytest.raises(sd.SoftwareDesignError, match="exhausted"):
        sd.ensure_design(tmp_path, inputs)
    record = sd._read_state(tmp_path)["attempts"][-1]
    assert not record["accepted"]
    assert json.loads(record["proposal"]) == sample_design()
    assert len(adapter.calls) == 1
    assert not (tmp_path / sd.DESIGN_FILE).exists()


def test_single_call_workflow_uses_real_executor(tmp_path, monkeypatch):
    from test_plan_author_e2e import _ScriptedModelAdapter

    sd._deploy(tmp_path)
    adapter = _ScriptedModelAdapter({"call": ["response"]})
    real_run = br.orchestra.run_workflow

    def run(*args, **kwargs):
        def register(registry):
            registry.actor_backings["model"] = lambda: adapter
            registry._adapter_cache.pop("model", None)

        kwargs["registry_customizer"] = register
        return real_run(*args, **kwargs)

    monkeypatch.setattr(br.orchestra, "run_workflow", run)
    output = br.ReviewSession(tmp_path).call("test", ROLE, "instructions", {"text": "café"})
    assert output == "response"
    actual = adapter.calls[0]["prompt"]
    session = br.ReviewSession(tmp_path)
    assert session.current["calls"][0]["prompt_bytes"] == len(actual.encode())


def test_prompt_total_counts_prior_calls(tmp_path, monkeypatch):
    limits(tmp_path, max_total_prompt_bytes=10)
    monkeypatch.setattr(br, "_invoke", lambda *a: ("ok", "log"))
    session = br.ReviewSession(tmp_path)
    session.call("first", ROLE, "x", {})
    with pytest.raises(sd.SoftwareDesignError, match="prompt"):
        br.ReviewSession(tmp_path).call("second", ROLE, "x", {})
    assert len(session.current["calls"]) == 1


def test_empty_budget_file_cannot_replenish_allowance(tmp_path):
    path = tmp_path / ".duplo/review-budget.json"
    path.parent.mkdir()
    path.write_text("{}")
    with pytest.raises(sd.SoftwareDesignError, match="Malformed"):
        br.ReviewSession(tmp_path)
    assert path.read_text() == "{}"


def test_followup_keeps_cross_references_and_global_contracts():
    design = sample_design()
    design["decisions"][0]["rationale"] += " See D-002."
    for key in ("D-002", "D-003"):
        decision = copy.deepcopy(design["decisions"][0])
        decision.update(id=key, rationale="Independent choice")
        design["decisions"].append(decision)
    context = br.focused_candidate(design, None, [{"targets": ["D-001"], "status": "fix"}])
    assert [d["id"] for d in context["decisions"]] == ["D-001", "D-002"]
    assert context["omitted_decisions"][0]["id"] == "D-003"
    assert context["failure_behavior"] == design["failure_behavior"]


def test_settled_finding_cannot_be_reopened_without_new_evidence():
    finding = {"id": "F001", "blocking": True, "status": "dismissed"}
    judgment = {
        "verdict": json.loads(verdict("iterate")),
        "dispositions": [{"id": "F001", "status": "fix", "reason": "Changed my mind"}],
    }
    with pytest.raises(sd.SoftwareDesignError, match="new evidence"):
        br.adjudicate(judgment, [finding], [finding])


def test_changed_requirement_list_enters_review_before_patch(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict("stuck")])
    with pytest.raises(sd.SoftwareDesignError, match="not accepted"):
        sd.ensure_design(tmp_path, inputs)
    changed = copy.deepcopy(inputs)
    changed["requirements"].append(
        {"name": "Meeting", "description": "Record meetings", "category": "core"}
    )
    adapter = install(
        monkeypatch, [sample_design(("Dictation", "Meeting"))], [verdict("iterate"), verdict()]
    )
    record = sd.ensure_design(tmp_path, changed)
    assert adapter.calls[0]["state_id"] == "review"
    assert len(adapter.proposer_prompts()) == 1
    assert record["accepted"]


def test_interrupted_judgment_keeps_settled_finding_disposition(tmp_path, monkeypatch, inputs):
    install(monkeypatch, [sample_design()], [verdict()])
    sd.ensure_design(tmp_path, inputs)
    install(monkeypatch, [], [])
    invoke = br._invoke

    def interrupt(root, role, prompt, timeout):
        if prompt.startswith("Judge the design"):
            raise KeyboardInterrupt
        return invoke(root, role, prompt, timeout)

    monkeypatch.setattr(br, "_invoke", interrupt)
    with pytest.raises(KeyboardInterrupt):
        sd.ensure_design(tmp_path, inputs, refresh=True)
    record = sd._read_state(tmp_path)["attempts"][-1]
    assert record["findings"][0]["status"] == "resolved"
    assert not record["accepted"]
