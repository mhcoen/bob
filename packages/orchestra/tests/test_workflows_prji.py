"""Tests for the propose_review_judge_implement (PRJI) workflow.

The workflow is a controller-driven loop with four roles: proposer
frames the work, reviewer is an independent critic, judge decides
what happens next via a four-branch schema-backed verdict, and
implementer applies workspace fixes when the judge calls for one.

Tests cover each of the four verdict branches (accept, implement,
rereview, reframe), all three counter caps (judge < 30, implement
< 20, propose < 6), the distinct-actor rule for the three non-judge
roles, and the workspace_mutation rule that binds implementer to a
mutating adapter and the other three roles to text-only adapters.
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
from orchestra.log import LogWriter
from orchestra.spine import (
    NO_INITIAL,
    InvocationRequest,
    PreparedInvocation,
    Workflow,
)
from orchestra.store import ArtifactStore


class _ScriptedAdapter:
    """Mock adapter (model or agent) that returns a sequence of
    responses keyed by ``state_id``. Each invocation pops the next
    response off the queue. Configurable backing string so the same
    class serves both ``model`` and ``agent`` registrations."""

    def __init__(
        self,
        responses: dict[str, list[str]],
        backing: str,
        workspace_mutation: str = "text_only",
    ) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.backing = backing
        self._workspace_mutation = workspace_mutation
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
            summary={"kind": self.backing},
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
            "backing": self.backing,
            "kind": "scripted",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": self._workspace_mutation,
        }


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _run_prji(
    tmp_path: Path,
    *,
    model_responses: dict[str, list[str]],
    agent_responses: dict[str, list[str]],
) -> tuple[
    _ScriptedAdapter, _ScriptedAdapter, Path, str, ArtifactStore
]:
    """Run the PRJI workflow with separate scripted adapters for the
    model and agent backings. Returns the model adapter, the agent
    adapter, the run directory, the terminal status, and the open
    store (caller closes)."""
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    model_adapter = _ScriptedAdapter(
        model_responses, backing="model", workspace_mutation="text_only"
    )
    agent_adapter = _ScriptedAdapter(
        agent_responses, backing="agent", workspace_mutation="mutating"
    )
    registry.actor_backings["model"] = lambda: model_adapter
    registry.actor_backings["agent"] = lambda: agent_adapter
    registry._adapter_cache.pop("model", None)
    registry._adapter_cache.pop("agent", None)
    # The pre-load registry already registered an identity_text_agent
    # parser; that takes care of the implementer state's text write.
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
            "task": "fix the bug in module X",
            "project_dir": str(tmp_path),
            "history": "prior session context",
        },
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return model_adapter, agent_adapter, run_dir, terminal, store


# --------------------------------------------------------------------
# Load and validate
# --------------------------------------------------------------------


def test_prji_workflow_loads_and_validates() -> None:
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "propose_review_judge_implement"
    state_names = {s.name for s in workflow.states}
    assert state_names == {"propose", "review", "judge", "implement"}
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    extracts = {
        (e.source_field, e.target) for e in verdict.extractions
    }
    assert extracts == {
        ("feedback", "judge_feedback"),
        ("fix_instructions", "fix_instructions"),
    }


# --------------------------------------------------------------------
# Verdict branch: accept on first judge call.
# --------------------------------------------------------------------


def test_prji_accept_on_first_judge(tmp_path: Path) -> None:
    model_responses = {
        "propose": ["FRAMING-1"],
        "review": ["REVIEW-1"],
        "judge": [
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "looks good",
                    "fix_instructions": "",
                }
            )
        ],
    }
    agent_responses: dict[str, list[str]] = {}
    _, _, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "done"
    finally:
        store.close()


# --------------------------------------------------------------------
# Verdict branch: implement -> review -> accept.
# --------------------------------------------------------------------


def test_prji_implement_then_accept(tmp_path: Path) -> None:
    model_responses = {
        "propose": ["FRAMING-1"],
        "review": ["REVIEW-1", "REVIEW-2"],
        "judge": [
            json.dumps(
                {
                    "decision": "implement",
                    "feedback": "needs the file edit",
                    "fix_instructions": "edit foo.py to do X",
                }
            ),
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "fix landed",
                    "fix_instructions": "",
                }
            ),
        ],
    }
    agent_responses = {"implement": ["edited foo.py"]}
    model_adapter, agent_adapter, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "done"
        # Implementer was called once.
        assert len([c for c in agent_adapter.calls if c["state_id"] == "implement"]) == 1
        # Reviewer's second pass should see the implementer output.
        review_calls = [c for c in model_adapter.calls if c["state_id"] == "review"]
        assert len(review_calls) == 2
        assert "edited foo.py" in review_calls[1]["prompt"]
    finally:
        store.close()


# --------------------------------------------------------------------
# Verdict branch: rereview -> review -> accept.
# --------------------------------------------------------------------


def test_prji_rereview_then_accept(tmp_path: Path) -> None:
    model_responses = {
        "propose": ["FRAMING-1"],
        "review": ["REVIEW-1", "REVIEW-2"],
        "judge": [
            json.dumps(
                {
                    "decision": "rereview",
                    "feedback": "look at section 3 again",
                    "fix_instructions": "",
                }
            ),
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "ok",
                    "fix_instructions": "",
                }
            ),
        ],
    }
    agent_responses: dict[str, list[str]] = {}
    model_adapter, _, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "done"
        review_calls = [c for c in model_adapter.calls if c["state_id"] == "review"]
        assert len(review_calls) == 2
        # Proposer was called once.
        propose_calls = [c for c in model_adapter.calls if c["state_id"] == "propose"]
        assert len(propose_calls) == 1
    finally:
        store.close()


# --------------------------------------------------------------------
# Verdict branch: reframe -> propose -> review -> accept.
# --------------------------------------------------------------------


def test_prji_reframe_then_accept(tmp_path: Path) -> None:
    model_responses = {
        "propose": ["FRAMING-1", "FRAMING-2"],
        "review": ["REVIEW-1", "REVIEW-2"],
        "judge": [
            json.dumps(
                {
                    "decision": "reframe",
                    "feedback": "the framing missed the point",
                    "fix_instructions": "",
                }
            ),
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "good now",
                    "fix_instructions": "",
                }
            ),
        ],
    }
    agent_responses: dict[str, list[str]] = {}
    model_adapter, _, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "done"
        propose_calls = [c for c in model_adapter.calls if c["state_id"] == "propose"]
        assert len(propose_calls) == 2
        # Second proposer pass sees the prior judge feedback.
        assert "missed the point" in propose_calls[1]["prompt"]
    finally:
        store.close()


# --------------------------------------------------------------------
# Counter caps: implement cap exhausted routes to stop.
# --------------------------------------------------------------------


def test_prji_implement_cap_routes_to_stop(tmp_path: Path) -> None:
    """Once attempts.implement reaches 20, the unguarded
    "on implement => stop" branch fires. Drives the loop with 20
    judge-implement-review cycles followed by a 21st implement
    verdict that the cap rejects."""
    implement_response = json.dumps(
        {
            "decision": "implement",
            "feedback": "fix something",
            "fix_instructions": "do X",
        }
    )
    model_responses = {
        "propose": ["FRAMING-1"],
        "review": ["REVIEW"] * 21,
        "judge": [implement_response] * 21,
    }
    agent_responses = {"implement": ["fix"] * 21}
    model_adapter, agent_adapter, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "stop"
        # Implementer was called exactly 20 times before the cap.
        impl_calls = [
            c for c in agent_adapter.calls if c["state_id"] == "implement"
        ]
        assert len(impl_calls) == 20
        # Judge fired 21 times: 20 led to implement, the 21st hit the
        # cap and routed to stop.
        judge_calls = [
            c for c in model_adapter.calls if c["state_id"] == "judge"
        ]
        assert len(judge_calls) == 21
    finally:
        store.close()


# --------------------------------------------------------------------
# Counter caps: propose cap exhausted on a reframe routes to stop.
# --------------------------------------------------------------------


def test_prji_propose_cap_routes_to_stop(tmp_path: Path) -> None:
    """The reframe path is bounded by attempts.propose < 6. The
    initial proposal counts as attempt 1; the 6th reframe (which
    would be the 7th propose call) hits the cap."""
    reframe_response = json.dumps(
        {
            "decision": "reframe",
            "feedback": "reframe again",
            "fix_instructions": "",
        }
    )
    model_responses = {
        "propose": ["FRAMING"] * 6,
        "review": ["REVIEW"] * 6,
        "judge": [reframe_response] * 6,
    }
    agent_responses: dict[str, list[str]] = {}
    model_adapter, _, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "stop"
        # Propose was called exactly 6 times (initial + 5 reframes).
        propose_calls = [
            c for c in model_adapter.calls if c["state_id"] == "propose"
        ]
        assert len(propose_calls) == 6
    finally:
        store.close()


# --------------------------------------------------------------------
# Counter caps: judge cap routes to stop on every nonterminal branch.
# --------------------------------------------------------------------


def test_prji_judge_cap_routes_to_stop_on_rereview(tmp_path: Path) -> None:
    """30 rereview verdicts: the 30th hits the judge cap on the
    rereview branch and routes to stop. The propose cap (6) and the
    implement cap (20) are not relevant here because rereview only
    re-runs the reviewer, not the proposer or implementer."""
    rereview_response = json.dumps(
        {
            "decision": "rereview",
            "feedback": "look again",
            "fix_instructions": "",
        }
    )
    model_responses = {
        "propose": ["FRAMING-1"],
        "review": ["REVIEW"] * 30,
        "judge": [rereview_response] * 30,
    }
    agent_responses: dict[str, list[str]] = {}
    model_adapter, _, _, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "stop"
        judge_calls = [
            c for c in model_adapter.calls if c["state_id"] == "judge"
        ]
        # The judge fires up to 30 times. The 30th call's outcome is
        # rereview, but with attempts.judge < 30 false, the unguarded
        # "on rereview => stop" branch fires.
        assert len(judge_calls) == 30
    finally:
        store.close()


# --------------------------------------------------------------------
# Distinct-actor rule for the three non-judge roles.
# --------------------------------------------------------------------


def _make_prji_config(
    *,
    proposer_adapter: str = "claude_code_text",
    proposer_model: str | None = "model-a",
    reviewer_adapter: str = "codex_text",
    reviewer_model: str | None = "model-b",
    judge_adapter: str = "claude_code_text",
    judge_model: str | None = "model-a",
    implementer_adapter: str = "claude_code_agent",
    implementer_model: str | None = "model-c",
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
            "implementer": RoleBinding(
                adapter=implementer_adapter, model=implementer_model
            ),
        },
        workflows={
            "propose_review_judge_implement": WorkflowConfig(
                pattern="propose_review_judge_implement"
            ),
        },
    )


def test_prji_distinct_actor_rule_accepts_distinct_actors() -> None:
    cfg = _make_prji_config()
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    bindings = _validate_role_bindings(
        workflow, "propose_review_judge_implement", cfg
    )
    assert bindings.keys() == {
        "proposer", "reviewer", "judge_role", "implementer"
    }


def test_prji_distinct_actor_rule_rejects_proposer_reviewer_collision() -> None:
    cfg = _make_prji_config(
        proposer_adapter="claude_code_text",
        proposer_model="m1",
        reviewer_adapter="claude_code_text",
        reviewer_model="m1",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(
            workflow, "propose_review_judge_implement", cfg
        )
    msg = str(exc.value)
    assert "proposer" in msg
    assert "reviewer" in msg


def test_prji_distinct_actor_rule_rejects_reviewer_implementer_collision() -> None:
    """reviewer and implementer must also be distinct. Implementer
    must additionally bind to a mutating adapter, so set both to
    the same agent variant."""
    cfg = _make_prji_config(
        reviewer_adapter="claude_code_agent",
        reviewer_model="m1",
        implementer_adapter="claude_code_agent",
        implementer_model="m1",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(
            workflow, "propose_review_judge_implement", cfg
        )
    # Either "reviewer/implementer collision" or the workspace_mutation
    # rule fires first; both are valid rejections.
    msg = str(exc.value)
    assert "implementer" in msg or "reviewer" in msg


def test_prji_distinct_actor_rule_judge_can_match_proposer() -> None:
    """Per the design doc: judge typically resolves to the same actor
    as proposer; the rule does not police that pair."""
    cfg = _make_prji_config(
        proposer_adapter="claude_code_text",
        proposer_model="m1",
        judge_adapter="claude_code_text",
        judge_model="m1",
        reviewer_adapter="codex_text",
        reviewer_model="m2",
        implementer_adapter="codex_agent",
        implementer_model="m3",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    bindings = _validate_role_bindings(
        workflow, "propose_review_judge_implement", cfg
    )
    assert bindings["proposer"].adapter == bindings["judge_role"].adapter


# --------------------------------------------------------------------
# Workspace mutation rule
# --------------------------------------------------------------------


def test_prji_workspace_mutation_rejects_text_implementer() -> None:
    """Binding the implementer to a text-only adapter is a config
    error: only the implementer may mutate the workspace, and a
    text-only implementer cannot."""
    cfg = _make_prji_config(
        implementer_adapter="codex_text",
        implementer_model="m3",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(
            workflow, "propose_review_judge_implement", cfg
        )
    msg = str(exc.value)
    assert "implementer" in msg
    # Either kind-mismatch or workspace_mutation rule fires; both
    # name the implementer and the binding.


def test_prji_workspace_mutation_rejects_mutating_proposer() -> None:
    """Binding the proposer to a mutating adapter is a config error:
    the proposer is text-only by contract."""
    cfg = _make_prji_config(
        proposer_adapter="codex_agent",
        proposer_model="m1",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(
            workflow, "propose_review_judge_implement", cfg
        )
    msg = str(exc.value)
    assert "proposer" in msg


def test_prji_workspace_mutation_rejects_mutating_reviewer() -> None:
    cfg = _make_prji_config(
        reviewer_adapter="claude_code_agent",
        reviewer_model="m2",
    )
    path = resolve_workflow_path(
        "propose_review_judge_implement", project_dir=None
    )
    workflow = load_workflow(path, _pre_load_registry())
    with pytest.raises(ConfigError) as exc:
        _validate_role_bindings(
            workflow, "propose_review_judge_implement", cfg
        )
    msg = str(exc.value)
    assert "reviewer" in msg


# --------------------------------------------------------------------
# Fail-closed contract checks on _adapter_workspace_mutation
#
# The earlier defaulting-to-"text_only" fallback could let a mutating
# adapter with broken metadata pass the PRJI proposer/reviewer/judge
# rule. _adapter_workspace_mutation is now class-attribute based and
# fails closed on every contract violation.
# --------------------------------------------------------------------


def test_adapter_workspace_mutation_rejects_unknown_adapter() -> None:
    """An unknown adapter name fails closed."""
    from orchestra.api import _adapter_workspace_mutation
    binding = RoleBinding(adapter="not_a_real_adapter", model="m")
    with pytest.raises(ConfigError) as exc:
        _adapter_workspace_mutation(binding)
    msg = str(exc.value)
    assert "not_a_real_adapter" in msg
    assert "_ADAPTER_CLASSES" in msg or "Known adapters" in msg


def test_adapter_workspace_mutation_rejects_class_missing_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter class without the WORKSPACE_MUTATION class attribute
    is rejected by the validator. The contract is fail-closed: a
    missing attribute is a hard error, not a silent default."""
    import orchestra.api as api_mod

    class _BrokenAdapter:
        pass

    monkeypatch.setitem(
        api_mod._ADAPTER_CLASSES, "broken_adapter", _BrokenAdapter
    )
    binding = RoleBinding(adapter="broken_adapter", model="m")
    with pytest.raises(ConfigError) as exc:
        api_mod._adapter_workspace_mutation(binding)
    msg = str(exc.value)
    assert "_BrokenAdapter" in msg
    assert "WORKSPACE_MUTATION" in msg


def test_adapter_workspace_mutation_rejects_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter class whose WORKSPACE_MUTATION attribute is not in
    the allowed vocabulary is rejected."""
    import orchestra.api as api_mod

    class _MisclassifiedAdapter:
        WORKSPACE_MUTATION = "kinda_mutating_sometimes"

    monkeypatch.setitem(
        api_mod._ADAPTER_CLASSES,
        "misclassified_adapter",
        _MisclassifiedAdapter,
    )
    binding = RoleBinding(adapter="misclassified_adapter", model="m")
    with pytest.raises(ConfigError) as exc:
        api_mod._adapter_workspace_mutation(binding)
    msg = str(exc.value)
    assert "_MisclassifiedAdapter" in msg
    assert "kinda_mutating_sometimes" in msg
    assert "mutating" in msg and "text_only" in msg


def test_adapter_workspace_mutation_accepts_valid_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A class with a valid WORKSPACE_MUTATION attribute passes
    without instantiation. The constructor below requires args; the
    validator never touches it. This pins the class-attribute path
    so a future refactor that re-introduces instantiation will
    break the test rather than silently change the contract surface."""
    import orchestra.api as api_mod

    class _StrictAdapter:
        WORKSPACE_MUTATION = "mutating"

        def __init__(self, required_arg: str) -> None:
            self.required_arg = required_arg

    monkeypatch.setitem(
        api_mod._ADAPTER_CLASSES, "strict_adapter", _StrictAdapter
    )
    binding = RoleBinding(adapter="strict_adapter", model="m")
    # No exception: the validator reads the class attribute directly.
    assert (
        api_mod._adapter_workspace_mutation(binding) == "mutating"
    )


# --------------------------------------------------------------------
# Phase 1 integration: a single canned trajectory exercising every
# verdict branch (accept, implement, rereview, reframe) before
# terminating in accept. Verifies the state-transition log shows
# all four branches taken and the workflow reaches the accept
# terminal cleanly.
# --------------------------------------------------------------------


def test_prji_each_branch_traversed(tmp_path: Path) -> None:
    """Trajectory:
        propose → review → judge(reframe)
        → propose → review → judge(implement)
        → implement → review → judge(rereview)
        → review → judge(accept) → done

    Four judge calls: reframe, implement, rereview, accept. All four
    nonterminal branches plus the terminal accept are exercised in
    one run. The test pins the per-judge outcome sequence so a
    regression that re-routes a verdict (or an executor change that
    misroutes one of the schema-derived outcomes) breaks the test
    rather than silently changing behavior.
    """
    model_responses = {
        "propose": ["FRAMING-1", "FRAMING-2"],
        "review": ["REVIEW-1", "REVIEW-2", "REVIEW-3", "REVIEW-4"],
        "judge": [
            json.dumps(
                {
                    "decision": "reframe",
                    "feedback": "reframe-feedback",
                    "fix_instructions": "",
                }
            ),
            json.dumps(
                {
                    "decision": "implement",
                    "feedback": "implement-feedback",
                    "fix_instructions": "do the fix",
                }
            ),
            json.dumps(
                {
                    "decision": "rereview",
                    "feedback": "rereview-feedback",
                    "fix_instructions": "",
                }
            ),
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "accept-feedback",
                    "fix_instructions": "",
                }
            ),
        ],
    }
    agent_responses = {"implement": ["fix applied"]}
    model_adapter, agent_adapter, run_dir, terminal, store = _run_prji(
        tmp_path,
        model_responses=model_responses,
        agent_responses=agent_responses,
    )
    try:
        assert terminal == "done"
        # Per-state call counts pin the trajectory shape.
        propose_calls = [
            c for c in model_adapter.calls if c["state_id"] == "propose"
        ]
        review_calls = [
            c for c in model_adapter.calls if c["state_id"] == "review"
        ]
        judge_calls = [
            c for c in model_adapter.calls if c["state_id"] == "judge"
        ]
        impl_calls = [
            c for c in agent_adapter.calls if c["state_id"] == "implement"
        ]
        # Two propose calls (initial + reframe), four reviews
        # (one per judge call), four judges (one per verdict), one
        # implement (from the implement verdict).
        assert len(propose_calls) == 2
        assert len(review_calls) == 4
        assert len(judge_calls) == 4
        assert len(impl_calls) == 1
        # Per-judge outcome sequence: every nonterminal branch fired
        # before the terminal accept.
        from orchestra.log import LogReader
        records = LogReader(run_dir / "log.jsonl").read_all()
        state_exits = [
            r for r in records
            if r.event == "state_exit" and r.state_id == "judge"
        ]
        outcomes = [r.fields["outcome"] for r in state_exits]
        assert outcomes == ["reframe", "implement", "rereview", "accept"]
        # State-transition log shows the four branches taken.
        transitions = [
            r for r in records
            if r.event == "transition" and r.state_id == "judge"
        ]
        targets = [r.fields["target"] for r in transitions]
        assert targets == ["propose", "implement", "review", "done"]
        # Final verdict artifact reflects the accept payload.
        verdict = store.read_latest("judge_verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        assert verdict.value["feedback"] == "accept-feedback"
    finally:
        store.close()
