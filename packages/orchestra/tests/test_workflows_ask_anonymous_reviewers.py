"""Slice C tests for the ``ask_anonymous_reviewers`` workflow.

Twelve model calls: framer, five parallel panelists (intentionally
unnamed in the surface; distinguishable only by role binding so the
user can pin five different models), an anonymizing transform that
maps panel outputs onto letters A through E, five parallel reviewers
operating against the anonymized panel only, and a synthesizer
reconciling the reviews into a verdict. The synthesizer reads the
anonymized panel plus the five reviews and never sees panelist
identities.

Tests cover load and validation, total call count, anonymization
isolation in reviewer prompts, the synthesizer's view (anonymized
panel plus reviews, no panelist identities), and the validator's
enumeration of missing required role bindings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestra.api import (
    _ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA,
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

REQUIRED_ROLES: tuple[str, ...] = (
    "framer",
    "panelist_1",
    "panelist_2",
    "panelist_3",
    "panelist_4",
    "panelist_5",
    "reviewer",
    "synthesizer",
)


PANELIST_STATES: tuple[str, ...] = (
    "panelist_1_state",
    "panelist_2_state",
    "panelist_3_state",
    "panelist_4_state",
    "panelist_5_state",
)
REVIEWER_STATES: tuple[str, ...] = (
    "reviewer_1",
    "reviewer_2",
    "reviewer_3",
    "reviewer_4",
    "reviewer_5",
)


# Distinct, non-overlapping placeholder texts so the assertions about
# which texts appear in the synthesizer prompt and the reviewer
# prompts are unambiguous.
FRAMED_QUESTION_TEXT = "FRAMED-XYZ-OPENING"
PANELIST_RESPONSES: dict[str, str] = {
    "panelist_1_state": "ALPHA-RESPONSE-DOWNSIDE",
    "panelist_2_state": "BETA-RESPONSE-RECAST",
    "panelist_3_state": "GAMMA-RESPONSE-UPSIDE",
    "panelist_4_state": "DELTA-RESPONSE-FRESHEYES",
    "panelist_5_state": "EPSILON-RESPONSE-NEXTSTEP",
}
REVIEWER_RESPONSES: dict[str, str] = {
    "reviewer_1": "REVIEW-ONE-PICKS-A",
    "reviewer_2": "REVIEW-TWO-PICKS-B",
    "reviewer_3": "REVIEW-THREE-PICKS-C",
    "reviewer_4": "REVIEW-FOUR-PICKS-D",
    "reviewer_5": "REVIEW-FIVE-PICKS-E",
}
SYNTHESIZER_RESPONSE = "VERDICT-OMEGA"


# --------------------------------------------------------------------
# Recording adapter and harness
# --------------------------------------------------------------------


class _RecordingModelAdapter:
    backing = "model"

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = dict(responses)
        self.calls: list[dict[str, Any]] = []
        self.prepared_inners: list[Any] = []

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        record: dict[str, Any] = {
            "state_id": request.state_id,
            "attempt": request.attempt,
            "model": binding.get("model"),
            "role": binding.get("role"),
            "prompt": prompt,
        }
        self.calls.append(record)
        inner: dict[str, Any] = {
            "state_id": request.state_id,
            "prompt": prompt,
        }
        self.prepared_inners.append(inner)
        return PreparedInvocation(
            request=request,
            summary={
                "kind": "model",
                "model": binding.get("model"),
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:160],
            },
            inner=inner,
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        state_id: str = prepared.inner["state_id"]
        text = self._responses.get(state_id)
        if text is None:
            raise AssertionError(f"recording adapter has no response for state {state_id!r}")
        return {
            "output": text,
            "verdict": None,
            "fields": {},
            "tokens_in": len(prepared.inner["prompt"]),
            "tokens_out": len(text),
            "cost_usd": None,
            "transcript_ref": None,
        }

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "model",
            "kind": "recording_mock",
            "supports_cancel": False,
        }


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _default_responses() -> dict[str, str]:
    responses: dict[str, str] = {"frame": FRAMED_QUESTION_TEXT}
    responses.update(PANELIST_RESPONSES)
    responses.update(REVIEWER_RESPONSES)
    responses["synthesize"] = SYNTHESIZER_RESPONSE
    return responses


def _run_workflow(
    tmp_path: Path,
    *,
    run_id: str | None = None,
    responses: dict[str, str] | None = None,
) -> tuple[_RecordingModelAdapter, Path, str]:
    path = resolve_workflow_path("ask_anonymous_reviewers", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(responses or _default_responses())
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)
    rid = run_id or new_run_id()
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
            "query": "is the slate of options reasonable?",
            "history": "user has tried two prior approaches.",
        },
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert terminal == "done", f"expected done, got {terminal!r}"
    return adapter, run_dir, rid


# --------------------------------------------------------------------
# Test 1: load and validate
# --------------------------------------------------------------------


def test_workflow_loads_and_validates() -> None:
    """The packaged ``ask_anonymous_reviewers.orc`` parses, the
    validator accepts the transform state's input/output schema
    against the registered ``anonymize_outputs``, and the workflow
    exposes the expected states, roles, and artifacts."""
    path = resolve_workflow_path("ask_anonymous_reviewers", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "ask_anonymous_reviewers"

    expected_states = {
        "frame",
        *PANELIST_STATES,
        "anonymize",
        *REVIEWER_STATES,
        "synthesize",
    }
    assert {s.name for s in workflow.states} == expected_states

    assert {r.name for r in workflow.roles} == set(REQUIRED_ROLES)

    artifact_names = {a.name for a in workflow.artifacts}
    assert "framed_question" in artifact_names
    assert "anon_map" in artifact_names
    assert "synthesizer_output" in artifact_names
    for n in range(1, 6):
        assert f"panelist_{n}_output" in artifact_names
        assert f"review_{n}_output" in artifact_names

    anonymize = workflow.state("anonymize")
    assert anonymize.actor.kind == "transform"
    assert anonymize.actor.ref == "anonymize_outputs"
    assert set(anonymize.reads) == {f"panelist_{n}_output" for n in range(1, 6)}
    assert {w.name for w in anonymize.writes} == {"anon_map"}


# --------------------------------------------------------------------
# Test 2: total call count is exactly twelve
# --------------------------------------------------------------------


def test_workflow_makes_exactly_twelve_model_calls(tmp_path: Path) -> None:
    """Twelve model calls per architecture: framer + 5 panelists +
    5 reviewers + synthesizer. The anonymize transform is not a model
    call. Pin the count so a regression that adds or drops a state
    fails this test."""
    adapter, _run_dir, _rid = _run_workflow(tmp_path)
    assert len(adapter.calls) == 12
    state_ids = {c["state_id"] for c in adapter.calls}
    expected_model_states = {"frame", "synthesize", *PANELIST_STATES, *REVIEWER_STATES}
    assert state_ids == expected_model_states
    # The anonymize transform did not create a model call.
    assert "anonymize" not in state_ids


# --------------------------------------------------------------------
# Test 3: reviewer prompt anonymization isolation
# --------------------------------------------------------------------


def test_reviewer_prompts_carry_anon_keys_and_omit_panelist_identifiers(
    tmp_path: Path,
) -> None:
    """Every reviewer state's rendered prompt contains anon_map keys
    A through E and the panelist texts surfaced through anonymization,
    and contains no panelist state-id substring. This catches a
    regression that would leak which panelist produced which response
    into the reviewer surface."""
    adapter, _run_dir, _rid = _run_workflow(tmp_path)
    reviewer_calls = [c for c in adapter.calls if c["state_id"].startswith("reviewer_")]
    assert len(reviewer_calls) == 5
    for call in reviewer_calls:
        prompt: str = call["prompt"]
        for letter in ("A", "B", "C", "D", "E"):
            assert f"'{letter}':" in prompt, (
                f"reviewer {call['state_id']!r} prompt missing key {letter!r}"
            )
        for panelist_text in PANELIST_RESPONSES.values():
            assert panelist_text in prompt, (
                f"reviewer {call['state_id']!r} prompt missing panelist text {panelist_text!r}"
            )
        # The reviewer must not see the panelist state names or output
        # artifact names. Panelist identities are the thing
        # anonymization is supposed to hide.
        for state_name in PANELIST_STATES:
            assert state_name not in prompt, (
                f"reviewer {call['state_id']!r} prompt leaked panelist state {state_name!r}"
            )
        for n in range(1, 6):
            assert f"panelist_{n}_output" not in prompt, (
                f"reviewer {call['state_id']!r} prompt leaked "
                f"panelist artifact name panelist_{n}_output"
            )


# --------------------------------------------------------------------
# Test 4: synthesizer view (anonymized panel + reviews, no identities)
# --------------------------------------------------------------------


def test_synthesizer_prompt_carries_anonymized_panel_and_reviews(
    tmp_path: Path,
) -> None:
    """The synthesizer state's rendered prompt must carry the
    anonymized panel (anon_map letter keys plus panelist texts) and
    every reviewer text. The synthesizer must NOT see panelist
    state-id or artifact-name identifiers, because the whole point of
    this workflow is reconciling reviews of an anonymized panel."""
    adapter, _run_dir, _rid = _run_workflow(tmp_path)
    synth_calls = [c for c in adapter.calls if c["state_id"] == "synthesize"]
    assert len(synth_calls) == 1
    prompt: str = synth_calls[0]["prompt"]
    # Anonymized panel surfaces.
    for letter in ("A", "B", "C", "D", "E"):
        assert f"'{letter}':" in prompt, f"synthesizer prompt missing anon_map key {letter!r}"
    for panelist_text in PANELIST_RESPONSES.values():
        assert panelist_text in prompt
    # Every review text reaches the synthesizer.
    for review_text in REVIEWER_RESPONSES.values():
        assert review_text in prompt
    # Framed question landed.
    assert FRAMED_QUESTION_TEXT in prompt
    # No panelist identifiers leak. The synthesizer is by design
    # stripped of panel identity.
    for state_name in PANELIST_STATES:
        assert state_name not in prompt
    for n in range(1, 6):
        assert f"panelist_{n}_output" not in prompt


# --------------------------------------------------------------------
# Test 5: validator rejects missing required role bindings
# --------------------------------------------------------------------


def _config_with_roles(role_names: list[str]) -> OrchestraConfig:
    roles: dict[str, RoleBinding] = {
        name: RoleBinding(adapter="claude_code_text", model="opus") for name in role_names
    }
    workflows = {
        "ask_anonymous_reviewers": WorkflowConfig(pattern="ask_anonymous_reviewers"),
    }
    return OrchestraConfig(roles=roles, workflows=workflows, verbs={})


@pytest.mark.parametrize("missing_role", REQUIRED_ROLES)
def test_validator_rejects_config_missing_required_role(missing_role: str) -> None:
    """Removing any one of the eight required bindings causes
    ``_validate_role_bindings`` to raise a ``ConfigError`` whose
    message names the missing binding."""
    path = resolve_workflow_path("ask_anonymous_reviewers", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    other_roles = [r for r in REQUIRED_ROLES if r != missing_role]
    cfg = _config_with_roles(other_roles)
    with pytest.raises(ConfigError) as exc_info:
        _validate_role_bindings(workflow, "ask_anonymous_reviewers", cfg)
    message = str(exc_info.value)
    assert repr(missing_role) in message


def test_validator_accepts_config_with_all_required_roles() -> None:
    path = resolve_workflow_path("ask_anonymous_reviewers", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    cfg = _config_with_roles(list(REQUIRED_ROLES))
    resolved = _validate_role_bindings(workflow, "ask_anonymous_reviewers", cfg)
    assert set(resolved.keys()) == set(REQUIRED_ROLES)


# --------------------------------------------------------------------
# Test 6: anon_map schema matches workflow declaration
# --------------------------------------------------------------------


def test_anonymize_schema_matches_workflow_declaration() -> None:
    """The api's registered input schema for ``anonymize_outputs``
    covers exactly the five panelist outputs the workflow's anonymize
    state reads. A regression that adds or removes a panelist must
    update both the workflow and the schema; this test pins them
    together so the two move as one."""
    assert set(_ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA.keys()) == {
        f"panelist_{n}_output" for n in range(1, 6)
    }


# --------------------------------------------------------------------
# Test 7: deterministic anon_map across runs with the same key inputs
# --------------------------------------------------------------------


def _read_artifact(run_dir: Path, name: str) -> Any:
    store = ArtifactStore(run_dir / "store.sqlite")
    try:
        latest = store.read_latest(name)
        return None if latest is None else latest.value
    finally:
        store.close()


def test_anon_map_deterministic_across_runs(tmp_path: Path) -> None:
    """Two runs with the same ``run_id`` and the same panelist outputs
    produce a byte-identical ``anon_map``. Exercises Slice B's seed
    contract end-to-end through the panel fan-out."""
    rid = new_run_id()
    adapter_a, run_dir_a, _ = _run_workflow(
        tmp_path / "a", run_id=rid, responses=_default_responses()
    )
    adapter_b, run_dir_b, _ = _run_workflow(
        tmp_path / "b", run_id=rid, responses=_default_responses()
    )
    map_a = _read_artifact(run_dir_a, "anon_map")
    map_b = _read_artifact(run_dir_b, "anon_map")
    assert isinstance(map_a, dict) and isinstance(map_b, dict)
    assert map_a == map_b
    # Every anonymized value is one of the five panelist texts.
    assert sorted(map_a.values()) == sorted(PANELIST_RESPONSES.values())
    synth_a = next(c for c in adapter_a.calls if c["state_id"] == "synthesize")
    synth_b = next(c for c in adapter_b.calls if c["state_id"] == "synthesize")
    assert synth_a["prompt"] == synth_b["prompt"]
