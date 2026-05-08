"""Tests for the council_four workflow.

Pattern: a framer normalizes (state, question, ledger_slice,
design_context) into a council_brief; four proposers fire in parallel
from the same brief; a synthesizer reads all four proposals and
emits a structured verdict (decision + feedback +
agreements/disagreements/rejected_options + criteria_compliance) plus
a free-form plan artifact.

Tests cover:

- Workflow load + validate (states, fan-out + join, schema, extraction
  clauses, external inputs).
- Verdict schema shape (decision enum, required arrays).
- Distinct-actor invariant: missing roles, four-proposer pairwise
  distinct, synthesizer differs from each proposer.
- End-to-end smoke: scripted adapters fan out, synthesizer accepts,
  terminal=done; per-actor proposals retained as separate artifacts.
- End-to-end negative: synthesizer claims accept while a required
  criterion is non-compliant — F2.5a runtime invariant catches it,
  state exits via error outcome, terminal=stop.
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
    CriterionDecl,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
)
from orchestra.executor.criteria import DecisionConsistencyMode
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

# --------------------------------------------------------------------
# Scripted mock adapter
# --------------------------------------------------------------------


class _ScriptedModelAdapter:
    backing = "model"

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        self.calls.append({"state_id": request.state_id, "prompt": prompt})
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id, "prompt": prompt},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        sid: str = prepared.inner["state_id"]
        queue = self._responses.get(sid) or []
        if not queue:
            raise AssertionError(
                f"scripted adapter has no response for {sid!r}"
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


def _run_council(
    tmp_path: Path,
    *,
    responses: dict[str, list[str]],
    criteria: tuple[CriterionDecl, ...] = (),
    inputs: dict[str, str] | None = None,
) -> tuple[_ScriptedModelAdapter, Path, str, ArtifactStore]:
    path = resolve_workflow_path("council_four", project_dir=None)
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
    executor_inputs = {
        "state": "the current state",
        "question": "the question to be answered",
        "ledger_slice": "",
        "design_context": "",
    }
    if inputs:
        executor_inputs.update(inputs)
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs=executor_inputs,
        criteria=criteria,
        decision_consistency_mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return adapter, run_dir, terminal, store


# --------------------------------------------------------------------
# Load + validate
# --------------------------------------------------------------------


def test_council_workflow_loads_and_validates() -> None:
    path = resolve_workflow_path("council_four", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "council_four"
    state_names = {s.name for s in workflow.states}
    assert state_names == {
        "frame",
        "propose_code",
        "propose_codex",
        "propose_kimi",
        "propose_deepseek",
        "synthesize",
    }
    external_input_names = {ei.name for ei in workflow.external_inputs}
    assert external_input_names == {
        "state",
        "question",
        "ledger_slice",
        "design_context",
    }
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    assert verdict.schema_path.endswith("council_synthesis_verdict.json")
    extracts = {(e.source_field, e.target) for e in verdict.extractions}
    assert extracts == {
        ("decision", "judge_decision"),
        ("feedback", "judge_feedback"),
    }


# --------------------------------------------------------------------
# Schema shape: decision enum, required structured fields
# --------------------------------------------------------------------


def test_council_schema_decision_enum() -> None:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict.json"
    )
    schema = json.loads(path.read_text())
    assert schema["properties"]["decision"]["enum"] == [
        "accept",
        "reframe",
        "stuck",
    ]


def test_council_schema_requires_structured_arrays() -> None:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict.json"
    )
    schema = json.loads(path.read_text())
    required = set(schema["required"])
    assert {
        "decision",
        "feedback",
        "agreements",
        "disagreements",
        "rejected_options",
    }.issubset(required)
    assert schema["properties"]["agreements"]["type"] == "array"
    assert schema["properties"]["disagreements"]["type"] == "array"
    assert schema["properties"]["rejected_options"]["type"] == "array"
    diss = schema["properties"]["disagreements"]["items"]
    assert set(diss["required"]) == {"topic", "positions"}


# --------------------------------------------------------------------
# Distinct-actor invariant
# --------------------------------------------------------------------


def _binding(adapter: str, model: str | None) -> RoleBinding:
    return RoleBinding(adapter=adapter, model=model)


def _make_council_config(
    proposers: dict[str, RoleBinding] | None = None,
    synthesizer: RoleBinding | None = None,
    framer: RoleBinding | None = None,
) -> OrchestraConfig:
    default_proposers = {
        "proposer_code":     _binding("claude_code_text",          "sonnet"),
        "proposer_codex":    _binding("codex_text",                "gpt-5.5"),
        "proposer_kimi":     _binding("claude_code_text_kimi",     "kimi-k2.6"),
        "proposer_deepseek": _binding("claude_code_text_deepseek", "deepseek-v4-pro"),
    }
    if proposers:
        default_proposers.update(proposers)
    return OrchestraConfig(
        roles={
            "framer": framer or _binding("claude_code_text", "haiku"),
            **default_proposers,
            "synthesizer": (
                synthesizer or _binding("claude_code_text", "opus")
            ),
        },
        workflows={
            "council_four": WorkflowConfig(pattern="council_four"),
        },
    )


def _load_council_workflow() -> Workflow:
    path = resolve_workflow_path("council_four", project_dir=None)
    return load_workflow(path, _pre_load_registry())


def test_distinct_actor_rule_passes_with_five_distinct() -> None:
    cfg = _make_council_config()
    workflow = _load_council_workflow()
    bindings = _validate_role_bindings(workflow, "council_four", cfg)
    assert set(bindings) == {
        "framer",
        "proposer_code",
        "proposer_codex",
        "proposer_kimi",
        "proposer_deepseek",
        "synthesizer",
    }


def test_distinct_actor_rule_rejects_synthesizer_overlap() -> None:
    """Synthesizer cannot match any proposer; refuse-on-four."""
    cfg = _make_council_config(
        synthesizer=_binding("codex_text", "gpt-5.5"),
    )
    workflow = _load_council_workflow()
    with pytest.raises(ConfigError, match="synthesizer"):
        _validate_role_bindings(workflow, "council_four", cfg)


def test_distinct_actor_rule_rejects_proposer_overlap() -> None:
    """Two proposers resolving to the same actor is also rejected."""
    cfg = _make_council_config(
        proposers={
            "proposer_kimi": _binding("codex_text", "gpt-5.5"),
        },
    )
    workflow = _load_council_workflow()
    with pytest.raises(ConfigError, match="distinct"):
        _validate_role_bindings(workflow, "council_four", cfg)


def test_distinct_actor_rule_missing_synthesizer_fails() -> None:
    cfg = OrchestraConfig(
        roles={
            "framer":            _binding("claude_code_text", "haiku"),
            "proposer_code":     _binding("claude_code_text", "sonnet"),
            "proposer_codex":    _binding("codex_text", "gpt-5.5"),
            "proposer_kimi":     _binding("claude_code_text_kimi", "kimi-k2.6"),
            "proposer_deepseek": _binding("claude_code_text_deepseek", "deepseek-v4-pro"),
        },
        workflows={
            "council_four": WorkflowConfig(pattern="council_four"),
        },
    )
    workflow = _load_council_workflow()
    with pytest.raises(ConfigError, match="synthesizer"):
        _validate_role_bindings(workflow, "council_four", cfg)


def test_framer_can_match_a_proposer() -> None:
    """Framer's identity is unconstrained; the rule only polices the
    four proposers and the synthesizer."""
    cfg = _make_council_config(
        framer=_binding("claude_code_text", "sonnet"),  # same as proposer_code
    )
    workflow = _load_council_workflow()
    # Should not raise.
    bindings = _validate_role_bindings(workflow, "council_four", cfg)
    assert "framer" in bindings


# --------------------------------------------------------------------
# End-to-end: scripted accept path
# --------------------------------------------------------------------


def _accept_verdict() -> str:
    return json.dumps(
        {
            "decision": "accept",
            "feedback": "all proposals converge on the same plan shape",
            "agreements": [
                "single propose-review-judge cycle",
                "deterministic acceptance criteria",
            ],
            "disagreements": [
                {
                    "topic": "whether to retry on stuck",
                    "positions": [
                        "yes, with bounded retries",
                        "no, surface stuck to caller",
                    ],
                }
            ],
            "rejected_options": ["unbounded retry budget"],
        }
    )


def test_council_e2e_fan_out_and_synthesize_to_done(tmp_path: Path) -> None:
    """Frame, four proposers in parallel, synthesizer accept => done."""
    responses = {
        "frame": ["COUNCIL BRIEF: the question, restated."],
        "propose_code":     ["proposal from code: phased rollout."],
        "propose_codex":    ["proposal from codex: phased rollout."],
        "propose_kimi":     ["proposal from kimi: single-pass authoring."],
        "propose_deepseek": ["proposal from deepseek: phased rollout."],
        "synthesize": [_accept_verdict()],
    }
    adapter, run_dir, terminal, store = _run_council(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        states_called = [c["state_id"] for c in adapter.calls]
        # frame first, then four proposers in some order, then synthesize.
        assert states_called[0] == "frame"
        assert set(states_called[1:5]) == {
            "propose_code",
            "propose_codex",
            "propose_kimi",
            "propose_deepseek",
        }
        assert states_called[5] == "synthesize"

        # Each proposer's artifact landed independently.
        for art_name in (
            "proposal_code",
            "proposal_codex",
            "proposal_kimi",
            "proposal_deepseek",
        ):
            v = store.read_latest(art_name)
            assert v is not None and "proposal" in str(v.value)

        # The synthesizer's verdict is a valid council verdict.
        verdict = store.read_latest("judge_verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        assert verdict.value["agreements"]
        assert verdict.value["disagreements"][0]["topic"]
        assert verdict.value["rejected_options"]
        # decision and feedback got extracted.
        decision = store.read_latest("judge_decision")
        feedback = store.read_latest("judge_feedback")
        assert decision is not None and decision.value == "accept"
        assert feedback is not None and "converge" in feedback.value
    finally:
        store.close()


def test_council_synthesizer_reads_all_four_proposals(tmp_path: Path) -> None:
    """Synthesizer's prompt must contain content from each proposer."""
    responses = {
        "frame": ["COUNCIL BRIEF"],
        "propose_code":     ["UNIQUE-MARK-CODE"],
        "propose_codex":    ["UNIQUE-MARK-CODEX"],
        "propose_kimi":     ["UNIQUE-MARK-KIMI"],
        "propose_deepseek": ["UNIQUE-MARK-DEEPSEEK"],
        "synthesize": [_accept_verdict()],
    }
    adapter, _, terminal, store = _run_council(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        synth_call = next(c for c in adapter.calls if c["state_id"] == "synthesize")
        prompt = synth_call["prompt"]
        for marker in (
            "UNIQUE-MARK-CODE",
            "UNIQUE-MARK-CODEX",
            "UNIQUE-MARK-KIMI",
            "UNIQUE-MARK-DEEPSEEK",
        ):
            assert marker in prompt, (
                f"synthesizer prompt missing {marker!r}"
            )
    finally:
        store.close()


# --------------------------------------------------------------------
# End-to-end: F2.5a accept-consistency catches a non-compliant accept
# --------------------------------------------------------------------


def test_council_accept_with_noncompliant_violates(tmp_path: Path) -> None:
    """Synthesizer claims accept while reporting compliant=false on a
    required criterion. Runtime decision-consistency invariant catches
    it; state exits via error outcome; terminal=stop."""
    responses = {
        "frame": ["COUNCIL BRIEF"],
        "propose_code":     ["proposal A"],
        "propose_codex":    ["proposal B"],
        "propose_kimi":     ["proposal C"],
        "propose_deepseek": ["proposal D"],
        "synthesize": [
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "claiming compliance",
                    "agreements": ["everyone agreed"],
                    "disagreements": [],
                    "rejected_options": [],
                    "criteria_compliance": [
                        {
                            "criterion_id": "must_have_phases",
                            "observed_value": "no phases",
                            "compliant": False,
                        }
                    ],
                }
            )
        ],
    }
    criteria = (
        CriterionDecl(
            id="must_have_phases",
            description="Plan defines explicit phases.",
            required=True,
        ),
    )
    _, run_dir, terminal, store = _run_council(
        tmp_path, responses=responses, criteria=criteria
    )
    try:
        assert terminal == "stop"
        records = LogReader(run_dir / "log.jsonl").read_all()
        consistency_events = [
            r for r in records if r.event == "decision_consistency"
        ]
        assert len(consistency_events) == 1
        fields = consistency_events[0].fields
        assert fields["outcome"] == "violation"
        assert fields["reason"] == "accept_with_noncompliant"
        assert fields["noncompliant_required_ids"] == ["must_have_phases"]
    finally:
        store.close()
