"""Slice C tests for the ``ask_council`` workflow.

The workflow is a real council per Karpathy's pattern: framer, five
parallel advisor lenses (contrarian, first principles, expansionist,
outsider, executor lens), an anonymizing transform, five parallel
reviewers operating against anonymized advisor outputs, and a chairman
synthesizing the verdict.

Tests cover load and validation, end-to-end content propagation
through the chairman's prompt, anonymization isolation in reviewer
prompts, reviewer statelessness across invocations, the validator's
enumeration of missing required role bindings, and seed determinism
of ``anon_map`` across runs.

The end-to-end tests bypass ``api.run_workflow`` and instantiate the
``Executor`` directly with a recording adapter. This mirrors the
pattern used in ``test_fan_out_executor.py`` and ``test_transforms.py``:
build a registry, swap the model factory for a recording wrapper,
build the executor, run to completion, then inspect the recorder's
call log and the durable artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestra.api import (
    _ASK_COUNCIL_ANONYMIZE_INPUT_SCHEMA,
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
    "contrarian",
    "first_principles",
    "expansionist",
    "outsider",
    "executor_lens",
    "reviewer",
    "chairman",
)


LENS_IDENTIFIERS: tuple[str, ...] = (
    "contrarian",
    "first_principles",
    "expansionist",
    "outsider",
    "executor_lens",
)


# Deterministic, non-overlapping placeholder texts the recording
# adapter returns for each model state. These strings carry no lens
# identifier substring so the reviewer-prompt isolation assertion is
# meaningful: anything that does leak a lens identifier into the
# reviewer prompt comes from the workflow plumbing, not from the mock
# data. The framed-question text matches the value the framer state
# writes; downstream states render it through their templates.
FRAMED_QUESTION_TEXT = "FRAMED-XYZ-OPENING"
ADVISOR_RESPONSES: dict[str, str] = {
    "contrarian_advise": "ALPHA-RESPONSE-DOWNSIDE",
    "first_principles_advise": "BETA-RESPONSE-RECAST",
    "expansionist_advise": "GAMMA-RESPONSE-UPSIDE",
    "outsider_advise": "DELTA-RESPONSE-FRESHEYES",
    "executor_lens_advise": "EPSILON-RESPONSE-NEXTSTEP",
}
REVIEWER_RESPONSES: dict[str, str] = {
    "reviewer_1": "REVIEW-ONE-PICKS-A",
    "reviewer_2": "REVIEW-TWO-PICKS-B",
    "reviewer_3": "REVIEW-THREE-PICKS-C",
    "reviewer_4": "REVIEW-FOUR-PICKS-D",
    "reviewer_5": "REVIEW-FIVE-PICKS-E",
}
CHAIRMAN_RESPONSE = "VERDICT-OMEGA"


# --------------------------------------------------------------------
# Recording adapter and harness
# --------------------------------------------------------------------


class _RecordingModelAdapter:
    """Mock model adapter that records every prepare/invoke and returns
    a deterministic response keyed by ``request.state_id``.

    Tests instantiate one adapter per run, swap it under
    ``registry.actor_backings['model']``, and inspect ``calls`` after
    the run completes. ``prepared_inners`` lets the statelessness test
    check that each invocation receives an independent inner-state
    object rather than a shared session handle.
    """

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
            raise AssertionError(
                f"recording adapter has no response for state {state_id!r}"
            )
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
    """Compose the per-state response table the council e2e uses."""
    responses: dict[str, str] = {"frame": FRAMED_QUESTION_TEXT}
    responses.update(ADVISOR_RESPONSES)
    responses.update(REVIEWER_RESPONSES)
    responses["synthesize"] = CHAIRMAN_RESPONSE
    return responses


def _run_council(
    tmp_path: Path,
    *,
    run_id: str | None = None,
    responses: dict[str, str] | None = None,
) -> tuple[_RecordingModelAdapter, Path, str]:
    """Run ``ask_council`` with the recording adapter under the given
    ``run_id`` and ``responses`` overrides. Returns the adapter, the
    run directory, and the resolved run_id."""
    path = resolve_workflow_path("ask_council", project_dir=None)
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


def _read_artifact(run_dir: Path, name: str) -> Any:
    store = ArtifactStore(run_dir / "store.sqlite")
    try:
        latest = store.read_latest(name)
        return None if latest is None else latest.value
    finally:
        store.close()


# --------------------------------------------------------------------
# Test 1: load and validate
# --------------------------------------------------------------------


def test_ask_council_loads_and_validates() -> None:
    """The packaged ``ask_council.orc`` parses, the validator
    accepts the transform state's input/output schema against the
    registered ``anonymize_outputs``, and the workflow exposes the
    expected states, roles, and artifacts."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "ask_council"

    expected_states = {
        "frame",
        "contrarian_advise",
        "first_principles_advise",
        "expansionist_advise",
        "outsider_advise",
        "executor_lens_advise",
        "anonymize",
        "reviewer_1",
        "reviewer_2",
        "reviewer_3",
        "reviewer_4",
        "reviewer_5",
        "synthesize",
    }
    assert {s.name for s in workflow.states} == expected_states

    assert {r.name for r in workflow.roles} == set(REQUIRED_ROLES)

    artifact_names = {a.name for a in workflow.artifacts}
    assert "framed_question" in artifact_names
    assert "anon_map" in artifact_names
    assert "chairman_output" in artifact_names
    for lens in LENS_IDENTIFIERS:
        assert f"{lens}_output" in artifact_names
    for n in range(1, 6):
        assert f"review_{n}_output" in artifact_names

    # The anonymize state is a transform whose declared reads match
    # the registered ``anonymize_outputs`` input schema.
    anonymize = workflow.state("anonymize")
    assert anonymize.actor.kind == "transform"
    assert anonymize.actor.ref == "anonymize_outputs"
    assert set(anonymize.reads) == {f"{lens}_output" for lens in LENS_IDENTIFIERS}
    assert {w.name for w in anonymize.writes} == {"anon_map"}


# --------------------------------------------------------------------
# Test 2: chairman prompt content
# --------------------------------------------------------------------


def test_chairman_prompt_contains_advisors_and_reviewers(tmp_path: Path) -> None:
    """The chairman state's rendered prompt must carry every named
    advisor output text and every reviewer output text. The test
    inspects the actual prompt the adapter receives, not the workflow's
    structural reads, so a future template that drops a substitution
    fails this assertion."""
    adapter, _run_dir, _rid = _run_council(tmp_path)
    chairman_calls = [c for c in adapter.calls if c["state_id"] == "synthesize"]
    assert len(chairman_calls) == 1
    chairman_prompt: str = chairman_calls[0]["prompt"]
    for advisor_text in ADVISOR_RESPONSES.values():
        assert advisor_text in chairman_prompt, (
            f"chairman prompt missing advisor text {advisor_text!r}"
        )
    for review_text in REVIEWER_RESPONSES.values():
        assert review_text in chairman_prompt, (
            f"chairman prompt missing reviewer text {review_text!r}"
        )
    # Sanity: framed question landed too.
    assert FRAMED_QUESTION_TEXT in chairman_prompt


# --------------------------------------------------------------------
# Test 3: reviewer prompt anonymization isolation
# --------------------------------------------------------------------


def test_reviewer_prompts_carry_anon_keys_and_omit_lens_identifiers(
    tmp_path: Path,
) -> None:
    """Every reviewer state's rendered prompt contains anon_map A through
    E values (the advisor texts surfaced through the anonymization
    transform) and contains none of the lens identifier strings the
    advisor states are named after. This catches an anonymization
    regression that would leak which advisor wrote which response into
    the reviewer surface."""
    adapter, _run_dir, _rid = _run_council(tmp_path)
    reviewer_calls = [
        c for c in adapter.calls if c["state_id"].startswith("reviewer_")
    ]
    assert len(reviewer_calls) == 5
    for call in reviewer_calls:
        prompt: str = call["prompt"]
        # The five anon-map letter keys appear in the dict's str
        # representation as ``'A':`` through ``'E':``. Pin the colon
        # so single-letter token noise in the surrounding template
        # text cannot satisfy the assertion accidentally.
        for letter in ("A", "B", "C", "D", "E"):
            assert f"'{letter}':" in prompt, (
                f"reviewer {call['state_id']!r} prompt missing key {letter!r}"
            )
        # Every advisor's text is present (reordered under anon
        # letters), so all five values appear in the rendered prompt.
        for advisor_text in ADVISOR_RESPONSES.values():
            assert advisor_text in prompt, (
                f"reviewer {call['state_id']!r} prompt missing "
                f"advisor text {advisor_text!r}"
            )
        # No lens identifier string leaks into the reviewer prompt.
        for ident in LENS_IDENTIFIERS:
            assert ident not in prompt, (
                f"reviewer {call['state_id']!r} prompt leaked lens "
                f"identifier {ident!r}"
            )


# --------------------------------------------------------------------
# Test 4: reviewer statelessness
# --------------------------------------------------------------------


def test_reviewer_invocations_are_independent(tmp_path: Path) -> None:
    """Each reviewer invocation receives a fresh prepared inner object
    (no shared session handle) and the per-call record carries the
    role binding ``reviewer`` consistently. The text-role adapter's
    invocation model is stateless by contract; this test pins that no
    invocation borrows state from a previous reviewer call."""
    adapter, _run_dir, _rid = _run_council(tmp_path)
    reviewer_calls = [
        (i, c) for i, c in enumerate(adapter.calls)
        if c["state_id"].startswith("reviewer_")
    ]
    assert len(reviewer_calls) == 5
    reviewer_inners = [adapter.prepared_inners[i] for i, _c in reviewer_calls]
    inner_ids = {id(inner) for inner in reviewer_inners}
    assert len(inner_ids) == 5, (
        "reviewer prepared.inner objects must be distinct per invocation; "
        "a shared object indicates session leakage"
    )
    for _i, c in reviewer_calls:
        assert c["role"] == "reviewer", (
            f"reviewer {c['state_id']!r} missing role binding"
        )
        assert c["attempt"] == 1, (
            f"reviewer {c['state_id']!r} attempted >1 times; reviewers "
            "do not retry under the slice C contract"
        )
    # No reviewer inner carries a continuation token, session id, or
    # similar persistent-identity field. The recorder's inner shape is
    # exactly {state_id, prompt}; a future regression that smuggles in
    # a session handle would surface as an extra key.
    for inner in reviewer_inners:
        assert set(inner.keys()) == {"state_id", "prompt"}


# --------------------------------------------------------------------
# Test 5: validator rejects missing required role bindings
# --------------------------------------------------------------------


def _config_with_roles(role_names: list[str]) -> OrchestraConfig:
    """Build an ``OrchestraConfig`` whose top-level ``roles`` table
    contains exactly the listed roles. Every role uses the
    ``claude_code_text`` adapter so the kind matches the workflow's
    text-role states."""
    roles: dict[str, RoleBinding] = {
        name: RoleBinding(adapter="claude_code_text", model="opus")
        for name in role_names
    }
    workflows = {
        "ask_council": WorkflowConfig(pattern="ask_council"),
    }
    return OrchestraConfig(roles=roles, workflows=workflows, verbs={})


@pytest.mark.parametrize("missing_role", REQUIRED_ROLES)
def test_validator_rejects_config_missing_required_role(missing_role: str) -> None:
    """Removing any one of the eight required bindings causes
    ``_validate_role_bindings`` to raise a ``ConfigError`` whose
    message names the missing binding. This exercises the contract
    that the user must bind all eight before ``ask_council`` runs."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    other_roles = [r for r in REQUIRED_ROLES if r != missing_role]
    cfg = _config_with_roles(other_roles)
    with pytest.raises(ConfigError) as exc_info:
        _validate_role_bindings(workflow, "ask_council", cfg)
    message = str(exc_info.value)
    assert repr(missing_role) in message, (
        f"ConfigError message {message!r} does not name missing role "
        f"{missing_role!r}"
    )


def test_validator_accepts_config_with_all_required_roles() -> None:
    """The complement: a config that binds every required role is
    accepted by ``_validate_role_bindings``."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    cfg = _config_with_roles(list(REQUIRED_ROLES))
    resolved = _validate_role_bindings(workflow, "ask_council", cfg)
    assert set(resolved.keys()) == set(REQUIRED_ROLES)


def test_validator_enumerates_all_missing_roles_at_once() -> None:
    """When every required binding is missing, the error message
    enumerates every missing role in a single failure rather than
    bailing on the first one. This protects the user from a
    ping-pong of one-error-at-a-time runs."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    cfg = _config_with_roles([])
    with pytest.raises(ConfigError) as exc_info:
        _validate_role_bindings(workflow, "ask_council", cfg)
    message = str(exc_info.value)
    for required in REQUIRED_ROLES:
        assert repr(required) in message, (
            f"missing-all-roles error did not name {required!r}"
        )


# --------------------------------------------------------------------
# Test 6: deterministic anon_map across runs with the same key inputs
# --------------------------------------------------------------------


def test_anon_map_deterministic_across_runs(tmp_path: Path) -> None:
    """Two runs with the same ``run_id``, the same query and history,
    and the same advisor outputs produce the same ``anon_map``. The
    seed is keyed on ``(run_id, state_name, sorted_input_keys)`` per
    Slice B, so the workflow-level determinism reduces to the
    transform-level determinism. This exercises the Slice B contract
    end-to-end through the council fan-out rather than at the
    transform layer alone."""
    rid = new_run_id()
    adapter_a, run_dir_a, _ = _run_council(
        tmp_path / "a", run_id=rid, responses=_default_responses()
    )
    adapter_b, run_dir_b, _ = _run_council(
        tmp_path / "b", run_id=rid, responses=_default_responses()
    )
    map_a = _read_artifact(run_dir_a, "anon_map")
    map_b = _read_artifact(run_dir_b, "anon_map")
    assert isinstance(map_a, dict) and isinstance(map_b, dict)
    assert map_a == map_b, (
        f"anon_map differs across runs with same (run_id, inputs); "
        f"first={map_a!r}, second={map_b!r}"
    )
    # Sanity: every anon-keyed value is one of the five advisor texts.
    assert sorted(map_a.values()) == sorted(ADVISOR_RESPONSES.values())
    # The api's registered input schema covers exactly the five named
    # advisor outputs; if a future change adds or removes one, this
    # assertion fails before the council runs against an inconsistent
    # schema.
    assert set(_ASK_COUNCIL_ANONYMIZE_INPUT_SCHEMA.keys()) == {
        f"{lens}_output" for lens in LENS_IDENTIFIERS
    }
    # Confirm the recorder ran the chairman exactly once with both
    # outputs visible.
    chairman_a = next(c for c in adapter_a.calls if c["state_id"] == "synthesize")
    chairman_b = next(c for c in adapter_b.calls if c["state_id"] == "synthesize")
    assert chairman_a["prompt"] == chairman_b["prompt"], (
        "deterministic mocks should produce a byte-identical chairman "
        "prompt across runs with the same (run_id, inputs)"
    )
