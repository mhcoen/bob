"""Slice C tests for the ``ask_council`` workflow.

The workflow is a real council per Karpathy's pattern: a framer, five
parallel lens advisors (contrarian, first principles, expansionist,
outsider, executor lens), and a chairman synthesizing the verdict.
Seven model calls. The chairman receives every advisor's output with
the advisor's identity in clear. There is NO anonymization step in
this workflow; the anonymized peer-review variant lives at
``ask_anonymous_reviewers``.

Tests cover load and validation, end-to-end content propagation
through the chairman's prompt, and the validator's enumeration of
missing required role bindings.

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
# adapter returns for each model state. The framed-question text
# matches the value the framer state writes; downstream states render
# it through their templates.
FRAMED_QUESTION_TEXT = "FRAMED-XYZ-OPENING"
ADVISOR_RESPONSES: dict[str, str] = {
    "contrarian_advise": "ALPHA-RESPONSE-DOWNSIDE",
    "first_principles_advise": "BETA-RESPONSE-RECAST",
    "expansionist_advise": "GAMMA-RESPONSE-UPSIDE",
    "outsider_advise": "DELTA-RESPONSE-FRESHEYES",
    "executor_lens_advise": "EPSILON-RESPONSE-NEXTSTEP",
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
    the run completes.
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


# --------------------------------------------------------------------
# Test 1: load and validate
# --------------------------------------------------------------------


def test_ask_council_loads_and_validates() -> None:
    """The packaged ``ask_council.orc`` parses and the workflow exposes
    the seven-state corrected architecture: framer, five lens advisors,
    chairman. No anonymize state, no reviewer states."""
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
        "synthesize",
    }
    assert {s.name for s in workflow.states} == expected_states

    assert {r.name for r in workflow.roles} == set(REQUIRED_ROLES)

    artifact_names = {a.name for a in workflow.artifacts}
    assert "framed_question" in artifact_names
    assert "chairman_output" in artifact_names
    for lens in LENS_IDENTIFIERS:
        assert f"{lens}_output" in artifact_names

    # The corrected Council has no anonymize transform and no review
    # artifacts. Pin the absence so a regression cannot silently
    # reintroduce them.
    assert "anon_map" not in artifact_names
    for n in range(1, 6):
        assert f"review_{n}_output" not in artifact_names
    state_kinds = {s.name: s.actor.kind for s in workflow.states}
    assert "transform" not in state_kinds.values()


# --------------------------------------------------------------------
# Test 2: total call count is exactly seven
# --------------------------------------------------------------------


def test_ask_council_makes_exactly_seven_model_calls(tmp_path: Path) -> None:
    """The corrected Council architecture is seven model calls: framer
    + five lens advisors + chairman. Pin that count so a regression
    that adds an anonymize or review pass back in fails this test."""
    adapter, _run_dir, _rid = _run_council(tmp_path)
    assert len(adapter.calls) == 7
    state_ids = [c["state_id"] for c in adapter.calls]
    assert "frame" in state_ids
    assert "synthesize" in state_ids
    for lens in LENS_IDENTIFIERS:
        assert f"{lens}_advise" in state_ids
    # No reviewer or anonymize state ran.
    assert not any(s.startswith("reviewer_") for s in state_ids)
    assert "anonymize" not in state_ids


# --------------------------------------------------------------------
# Test 3: chairman prompt content
# --------------------------------------------------------------------


def test_chairman_prompt_contains_named_advisors(tmp_path: Path) -> None:
    """The chairman state's rendered prompt must carry every named
    advisor output text and must reference each lens by name. The
    chairman is meant to know which advisor said what; this is the
    distinguishing property of the corrected Council vs the
    anonymized variant."""
    adapter, _run_dir, _rid = _run_council(tmp_path)
    chairman_calls = [c for c in adapter.calls if c["state_id"] == "synthesize"]
    assert len(chairman_calls) == 1
    chairman_prompt: str = chairman_calls[0]["prompt"]
    for advisor_text in ADVISOR_RESPONSES.values():
        assert advisor_text in chairman_prompt, (
            f"chairman prompt missing advisor text {advisor_text!r}"
        )
    # The named lenses appear in the chairman's rendered prompt
    # (template lists each by display name). This is the property
    # that anonymous-reviewers explicitly does not preserve.
    for label in (
        "Contrarian",
        "First Principles",
        "Expansionist",
        "Outsider",
        "Executor",
    ):
        assert label in chairman_prompt, (
            f"chairman prompt missing lens label {label!r}"
        )
    # Sanity: framed question landed too.
    assert FRAMED_QUESTION_TEXT in chairman_prompt
    # The chairman must NOT receive anonymized-style A through E
    # output. That structure belongs to the synthesizer in the
    # ask_anonymous_reviewers workflow.
    for letter in ("A", "B", "C", "D", "E"):
        assert f"'{letter}':" not in chairman_prompt, (
            "chairman prompt must not contain anon_map-shaped keys"
        )


# --------------------------------------------------------------------
# Test 4: validator rejects missing required role bindings
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
    """Removing any one of the seven required bindings causes
    ``_validate_role_bindings`` to raise a ``ConfigError`` whose
    message names the missing binding. This exercises the contract
    that the user must bind all seven before ``ask_council`` runs."""
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


def test_validator_does_not_require_reviewer_role() -> None:
    """The corrected Council architecture does not have a reviewer
    state. Binding only the seven required roles (without any
    'reviewer' binding) must validate cleanly. This pins the schema
    change against the prior 12-call workflow that did require a
    'reviewer' binding."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    cfg = _config_with_roles(list(REQUIRED_ROLES))
    assert "reviewer" not in cfg.roles
    resolved = _validate_role_bindings(workflow, "ask_council", cfg)
    assert "reviewer" not in resolved
