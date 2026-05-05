"""Tests for the iterate_until_acceptable workflow.

Pattern: a proposer produces an initial draft once; a reviewer-judge
loop runs until the judge accepts or the iteration cap is reached.
The proposal is fixed across iterations; only the review iterates.
The judge's verdict is schema-backed JSON with a feedback field
extracted into an artifact the reviewer reads on the next pass.

Tests cover: workflow load and validate, accept-on-first-try,
accept-after-multiple-iterations (loops back through review), accept-
on-cap (the unguarded ``on iterate => done`` fallback), schema
violation routing to error/stop, and the workflow-specific
distinct-actor config validation rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import (
    _pre_load_registry,
    _validate_role_bindings,
)
from orchestra.config import (
    ConfigError,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
)
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogReader, LogWriter
from orchestra.spine import (
    NO_INITIAL,
    InvocationRequest,
    PreparedInvocation,
    Workflow,
)
from orchestra.store import ArtifactStore


class _ScriptedModelAdapter:
    """Mock model adapter that returns a sequence of responses keyed
    by ``state_id``. Each state has its own queue; pop the next
    response on each invocation. Supports failures by injecting an
    exception placeholder."""

    backing = "model"

    def __init__(
        self,
        responses: dict[str, list[str]],
    ) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        record: dict[str, Any] = {
            "state_id": request.state_id,
            "model": binding.get("model"),
            "role": binding.get("role"),
            "prompt": prompt,
        }
        self.calls.append(record)
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id, "prompt": prompt},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        state_id: str = prepared.inner["state_id"]
        queue = self._responses.get(state_id) or []
        if not queue:
            raise AssertionError(
                f"scripted adapter has no response for {state_id!r}"
            )
        text = queue.pop(0)
        return {
            "output": text,
            "verdict": None,
            "fields": {},
            "tokens_in": 0,
            "tokens_out": len(text),
            "cost_usd": None,
            "transcript_ref": None,
        }

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "model",
            "kind": "scripted",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": "text_only",
        }


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _run_iterate(
    tmp_path: Path,
    *,
    responses: dict[str, list[str]],
) -> tuple[_ScriptedModelAdapter, Path, str, ArtifactStore]:
    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _ScriptedModelAdapter(responses)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)
    rid = new_run_id()
    run_dir = tmp_path / f"run_{rid}"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(path)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={
            "query": "the question",
            "history": "prior context",
        },
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return adapter, run_dir, terminal, store


# --------------------------------------------------------------------
# Load and validate
# --------------------------------------------------------------------


def test_iterate_workflow_loads_and_validates() -> None:
    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "iterate_until_acceptable"
    state_names = {s.name for s in workflow.states}
    assert state_names == {"propose", "review", "judge"}
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    assert verdict.schema_path.endswith("iterate_judge_verdict.json")
    # The single extract clause must reference feedback.
    assert len(verdict.extractions) == 1
    assert verdict.extractions[0].source_field == "feedback"
    assert verdict.extractions[0].target == "judge_feedback"


# --------------------------------------------------------------------
# Accept on first try
# --------------------------------------------------------------------


def test_iterate_accept_on_first_try(tmp_path: Path) -> None:
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW-1"],
        "judge": [json.dumps({"decision": "accept", "feedback": "fine"})],
    }
    adapter, _, terminal, store = _run_iterate(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        # One invocation each.
        states = [c["state_id"] for c in adapter.calls]
        assert states == ["propose", "review", "judge"]
        proposal = store.read_latest("proposal")
        assert proposal is not None and proposal.value == "DRAFT-1"
        verdict = store.read_latest("judge_verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        feedback = store.read_latest("judge_feedback")
        assert feedback is not None
        assert feedback.value == "fine"
    finally:
        store.close()


# --------------------------------------------------------------------
# Accept after multiple iterations
# --------------------------------------------------------------------


def test_iterate_accept_after_two_iterations(tmp_path: Path) -> None:
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW-1", "REVIEW-2", "REVIEW-3"],
        "judge": [
            json.dumps({"decision": "iterate", "feedback": "needs work"}),
            json.dumps({"decision": "iterate", "feedback": "still off"}),
            json.dumps({"decision": "accept", "feedback": "ok now"}),
        ],
    }
    adapter, _, terminal, store = _run_iterate(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        states = [c["state_id"] for c in adapter.calls]
        # Expected: propose, then review/judge x3
        assert states[0] == "propose"
        assert states[1::2] == ["review", "review", "review"]
        assert states[2::2] == ["judge", "judge", "judge"]
        # Final verdict accepted; feedback artifact carries the most
        # recent judge feedback ("ok now").
        verdict = store.read_latest("judge_verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        feedback = store.read_latest("judge_feedback")
        assert feedback is not None
        assert feedback.value == "ok now"
        # The reviewer's prompt on iteration 2 should include the
        # iteration-1 feedback (extracted into judge_feedback).
        review_calls = [c for c in adapter.calls if c["state_id"] == "review"]
        assert len(review_calls) == 3
        # First review pass sees empty initial feedback.
        assert "needs work" not in review_calls[0]["prompt"]
        # Second review pass sees iteration-1 judge feedback.
        assert "needs work" in review_calls[1]["prompt"]
        # Third review pass sees iteration-2 judge feedback.
        assert "still off" in review_calls[2]["prompt"]
    finally:
        store.close()


# --------------------------------------------------------------------
# Accept on cap: 6 judge invocations all return iterate; the
# unguarded `on iterate => done` fires.
# --------------------------------------------------------------------


def test_iterate_accept_on_cap(tmp_path: Path) -> None:
    iterate_response = json.dumps(
        {"decision": "iterate", "feedback": "still iterate"}
    )
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW"] * 6,
        "judge": [iterate_response] * 6,
    }
    adapter, run_dir, terminal, store = _run_iterate(
        tmp_path, responses=responses
    )
    try:
        # accept-on-cap: workflow terminates done with the proposal.
        assert terminal == "done"
        states = [c["state_id"] for c in adapter.calls]
        # propose + (review, judge) x 6 = 13 calls.
        assert states.count("judge") == 6
        assert states.count("review") == 6
        records = LogReader(run_dir / "log.jsonl").read_all()
        sv = [r for r in records if r.event == "schema_validation"]
        # Six judge invocations -> six schema_validation records.
        assert len(sv) == 6
        # The final transition's outcome on the judge state is iterate
        # (not accept) but the unguarded fallback routes to done.
        state_exits = [r for r in records if r.event == "state_exit"]
        last_judge_exit = next(
            r for r in reversed(state_exits) if r.state_id == "judge"
        )
        assert last_judge_exit.fields["outcome"] == "iterate"
        transitions = [r for r in records if r.event == "transition"]
        last_judge_transition = next(
            r for r in reversed(transitions) if r.state_id == "judge"
        )
        assert last_judge_transition.fields["target"] == "done"
    finally:
        store.close()


# --------------------------------------------------------------------
# Schema violation routes to stop with reason="schema_violation".
# --------------------------------------------------------------------


def test_iterate_schema_violation_routes_to_error(tmp_path: Path) -> None:
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW-1"],
        # decision is required and must be in enum; "punt" violates the enum.
        "judge": [json.dumps({"decision": "punt", "feedback": "x"})],
    }
    adapter, _, terminal, store = _run_iterate(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "stop"
    finally:
        store.close()


# --------------------------------------------------------------------
# Distinct-actor config validation rule
# --------------------------------------------------------------------


def _make_iterate_config(
    proposer_adapter: str = "claude_code_text",
    proposer_model: str | None = "model-a",
    reviewer_adapter: str = "codex_text",
    reviewer_model: str | None = "model-b",
    judge_adapter: str = "claude_code_text",
    judge_model: str | None = "model-a",
) -> OrchestraConfig:
    return OrchestraConfig(
        roles={
            "proposer": RoleBinding(
                adapter=proposer_adapter, model=proposer_model
            ),
            "reviewer": RoleBinding(
                adapter=reviewer_adapter, model=reviewer_model
            ),
            "judge_role": RoleBinding(
                adapter=judge_adapter, model=judge_model
            ),
        },
        workflows={
            "iterate_until_acceptable": WorkflowConfig(
                pattern="iterate_until_acceptable"
            ),
        },
    )


def test_iterate_distinct_actor_rule_accepts_distinct_actors() -> None:
    cfg = _make_iterate_config()
    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    bindings = _validate_role_bindings(workflow, "iterate_until_acceptable", cfg)
    assert "proposer" in bindings
    assert "reviewer" in bindings


def test_iterate_distinct_actor_rule_rejects_collision() -> None:
    """proposer and reviewer bound to the same (adapter, model) is a
    config error: independent review depends on training-data
    separation between the two actors."""
    cfg = _make_iterate_config(
        proposer_adapter="claude_code_text",
        proposer_model="m1",
        reviewer_adapter="claude_code_text",
        reviewer_model="m1",
    )
    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(workflow, "iterate_until_acceptable", cfg)
    msg = str(exc.value)
    assert "proposer" in msg
    assert "reviewer" in msg


def test_iterate_distinct_actor_rule_judge_can_match_proposer() -> None:
    """Judge typically resolves to the same actor as proposer; the
    rule does not police that pair."""
    cfg = _make_iterate_config(
        proposer_adapter="claude_code_text",
        proposer_model="m1",
        reviewer_adapter="codex_text",
        reviewer_model="m2",
        judge_adapter="claude_code_text",
        judge_model="m1",
    )
    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    bindings = _validate_role_bindings(workflow, "iterate_until_acceptable", cfg)
    assert "judge_role" in bindings
