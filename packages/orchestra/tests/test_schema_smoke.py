"""Integration tests for the schema-verdict runtime.

Drives a stripped Iterate-style workflow end to end through the
runner using mock_model adapters returning canned JSON. Covers each
schema-layer behavior the design document calls out:

  - Happy path (decision=accept routes to terminal),
  - Alternate decision (decision=iterate routes to loop and re-runs),
  - Parse error (returns 'error' outcome with reason='json_parse'),
  - Schema violation (returns 'error' outcome with reason='schema_violation'),
  - Outcome mismatch at workflow load time,
  - Schema file edited between crash and resume (refuses).
  - Extraction populates the target text artifact,
  - Optional source absent leaves target unchanged,
  - Boolean and numeric source fields converted to canonical text.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters.mock_model import MockModelAdapter
from orchestra.errors import ValidationError
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogReader, LogWriter
from orchestra.prompt_snapshot import (
    SnapshotIntegrityError,
    restore_prompt_snapshots,
    snapshot_prompt_sources,
)
from orchestra.registry.registry import (
    ProfileRegistry,
    with_core,
)
from orchestra.spine import NO_INITIAL, Envelope, Workflow
from orchestra.store import ArtifactStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "schema_smoke"
FIXTURE = FIXTURE_DIR / "schema_smoke.orc"


class _ScriptedModelAdapter:
    """Mock model adapter that returns scripted responses.

    Each ``invoke`` consumes one response from the scripted list. Run
    out and the test gets a clear AssertionError. Mirrors
    MockModelAdapter's payload shape so the schema layer sees a real
    'output' field.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._delegate = MockModelAdapter()

    def prepare(self, request: Any) -> Any:
        return self._delegate.prepare(request)

    def invoke(self, prepared: Any) -> dict[str, Any]:
        if not self._responses:
            raise AssertionError(
                "scripted model adapter: responses exhausted"
            )
        response = self._responses.pop(0)
        return {
            "output": response,
            "verdict": None,
            "fields": {},
            "tokens_in": 0,
            "tokens_out": len(response),
            "cost_usd": None,
            "transcript_ref": None,
        }

    def cancel(self, prepared: Any) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "model",
            "kind": "scripted",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
        }


def _build_registry(responses: list[str]) -> ProfileRegistry:
    """Build a registry with a scripted model adapter and the identity
    text parser. The schema-handled writes bypass parsers; the only
    parser-dispatched write is ``stub_artifact`` (text)."""
    reg = ProfileRegistry()
    for t in ("text", "json", "messages", "prompt", "schema", "document"):
        reg.register_artifact_type(t)
    adapter = _ScriptedModelAdapter(responses)
    reg.register_actor_backing("model", lambda: adapter)
    reg.register_actor_backing("human", _DummyAdapterFactory)
    reg.register_actor_backing("shell", _DummyAdapterFactory)
    from orchestra.executor.parsers import identity_text_parser
    reg.register_result_parser(identity_text_parser)
    return reg


class _DummyAdapterFactory:
    def __init__(self) -> None:
        pass


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _run(
    workflow: Workflow,
    registry: ProfileRegistry,
    run_dir: Path,
    inputs: dict[str, Any],
) -> tuple[str, dict[str, Envelope], ArtifactStore, Path]:
    run_id = new_run_id()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log_path = run_dir / "log.jsonl"
    log = LogWriter(log_path, run_id)
    log.write("run_start", fields={"workflow_path": str(FIXTURE)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs=inputs,
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return terminal, executor._envelopes, store, log_path


# --------------------------------------------------------------------
# Happy path: decision=accept routes to done on first try.
# --------------------------------------------------------------------


def test_schema_smoke_accept_first_try(tmp_path: Path) -> None:
    workflow = load_workflow(FIXTURE, with_core())
    responses = [json.dumps({"decision": "accept", "feedback": "fine"})]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminal, envelopes, store, log_path = _run(
        workflow, registry, run_dir, {"query": "anything"}
    )
    try:
        assert terminal == "done"
        env = envelopes["judge"]
        assert env.outcome == "accept"
        assert env.status == "ok"
        verdict = store.read_latest("verdict")
        assert verdict is not None
        assert verdict.value == {"decision": "accept", "feedback": "fine"}
        feedback = store.read_latest("judge_feedback")
        assert feedback is not None
        assert feedback.value == "fine"
        records = LogReader(log_path).read_all()
        sv = [r for r in records if r.event == "schema_validation"]
        assert len(sv) == 1
        assert sv[0].fields["outcome"] == "valid"
        assert sv[0].fields["decision"] == "accept"
    finally:
        store.close()


# --------------------------------------------------------------------
# Alternate decision: iterate -> repeat -> judge -> accept
# --------------------------------------------------------------------


def test_schema_smoke_iterate_then_accept(tmp_path: Path) -> None:
    workflow = load_workflow(FIXTURE, with_core())
    responses = [
        json.dumps({"decision": "iterate", "feedback": "needs work"}),
        "stub-output",
        json.dumps({"decision": "accept", "feedback": "ok now"}),
    ]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminal, envelopes, store, log_path = _run(
        workflow, registry, run_dir, {"query": "anything"}
    )
    try:
        assert terminal == "done"
        verdict = store.read_latest("verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        feedback = store.read_latest("judge_feedback")
        assert feedback is not None
        assert feedback.value == "ok now"
        records = LogReader(log_path).read_all()
        sv = [r for r in records if r.event == "schema_validation"]
        # Two judge invocations -> two schema_validation records
        assert len(sv) == 2
        assert [r.fields["decision"] for r in sv] == ["iterate", "accept"]
    finally:
        store.close()


# --------------------------------------------------------------------
# Parse error: malformed JSON output routes to error.
# --------------------------------------------------------------------


def test_schema_smoke_parse_error(tmp_path: Path) -> None:
    workflow = load_workflow(FIXTURE, with_core())
    responses = ["not json at all"]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminal, envelopes, store, log_path = _run(
        workflow, registry, run_dir, {"query": "anything"}
    )
    try:
        assert terminal == "stop"
        env = envelopes["judge"]
        assert env.outcome == "error"
        assert env.error is not None
        assert env.error.detail is not None
        assert env.error.detail.get("reason") == "json_parse"
        records = LogReader(log_path).read_all()
        sv = [r for r in records if r.event == "schema_validation"]
        assert len(sv) == 1
        assert sv[0].fields["outcome"] == "parse_error"
    finally:
        store.close()


# --------------------------------------------------------------------
# Schema violation: valid JSON but enum/required mismatch.
# --------------------------------------------------------------------


def test_schema_smoke_schema_violation(tmp_path: Path) -> None:
    workflow = load_workflow(FIXTURE, with_core())
    responses = [json.dumps({"decision": "punt", "feedback": "x"})]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminal, envelopes, store, log_path = _run(
        workflow, registry, run_dir, {"query": "q"}
    )
    try:
        assert terminal == "stop"
        env = envelopes["judge"]
        assert env.outcome == "error"
        assert env.error is not None
        assert env.error.detail is not None
        assert env.error.detail.get("reason") == "schema_violation"
        records = LogReader(log_path).read_all()
        sv = [r for r in records if r.event == "schema_validation"]
        assert len(sv) == 1
        assert sv[0].fields["outcome"] == "schema_error"
        # No verdict artifact committed.
        assert store.read_latest("verdict") is None
    finally:
        store.close()


def test_schema_smoke_missing_required_field_violation(tmp_path: Path) -> None:
    """A valid-JSON object that omits a required field is a schema
    violation (rather than a parse error)."""
    workflow = load_workflow(FIXTURE, with_core())
    responses = [json.dumps({"decision": "accept"})]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    terminal, envelopes, store, _ = _run(
        workflow, registry, run_dir, {"query": "q"}
    )
    try:
        assert terminal == "stop"
        env = envelopes["judge"]
        assert env.outcome == "error"
        assert env.error is not None
        assert env.error.detail is not None
        assert env.error.detail.get("reason") == "schema_violation"
    finally:
        store.close()


# --------------------------------------------------------------------
# Outcome mismatch at load time.
# --------------------------------------------------------------------


def test_schema_smoke_outcome_mismatch_load_error(tmp_path: Path) -> None:
    """A workflow that omits a transition for an enum value is a load
    error (the validator's enum-coverage check)."""
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "schemas").mkdir()
    shutil.copy(
        FIXTURE_DIR / "schemas" / "two_branch.json",
        bad_dir / "schemas" / "two_branch.json",
    )
    (bad_dir / "prompts").mkdir()
    shutil.copy(
        FIXTURE_DIR / "prompts" / "judge.md",
        bad_dir / "prompts" / "judge.md",
    )
    src = bad_dir / "wf.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input query text
  max_total_steps 5
  model m_judge
  artifact verdict json
    schema "schemas/two_branch.json"
    extract feedback => fb text
  artifact fb text
    initial ""
  role judge_role
    prompt template "prompts/judge.md" with query
  state judge
    actor model m_judge
    role judge_role
    reads query
    writes verdict json
    writes fb text
    on accept => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    assert "iterate" in str(exc.value)


# --------------------------------------------------------------------
# Schema file edited between snapshot and restore (resume) refuses.
# --------------------------------------------------------------------


def test_schema_smoke_schema_drift_refused(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copytree(
        FIXTURE_DIR, project_dir, dirs_exist_ok=True
    )
    workflow = load_workflow(project_dir / "schema_smoke.orc", with_core())
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    schema_entry = next(e for e in manifest if e["kind"] == "schema")
    snap = Path(schema_entry["snapshot_path"])
    snap.chmod(0o600)
    snap.write_text("{}")
    # Reload the workflow fresh and try to restore — expect refusal.
    fresh = load_workflow(project_dir / "schema_smoke.orc", with_core())
    with pytest.raises(SnapshotIntegrityError):
        restore_prompt_snapshots(fresh, manifest)


# --------------------------------------------------------------------
# Extraction tests.
# --------------------------------------------------------------------


def test_schema_extraction_populates_target_artifact(tmp_path: Path) -> None:
    """The extract clause writes the target text artifact with the
    field's value at the same time as the json artifact write."""
    workflow = load_workflow(FIXTURE, with_core())
    responses = [
        json.dumps({"decision": "accept", "feedback": "explicit text"}),
    ]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, _, store, _ = _run(workflow, registry, run_dir, {"query": "q"})
    try:
        feedback = store.read_latest("judge_feedback")
        assert feedback is not None
        assert feedback.value == "explicit text"
    finally:
        store.close()


def test_schema_extraction_canonical_boolean_and_number(tmp_path: Path) -> None:
    """Booleans render as 'true'/'false' and numbers as their str()
    form when extracted into a text artifact."""
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "v.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["decision", "is_ok", "score"],
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "stop"],
                    },
                    "is_ok": {"type": "boolean"},
                    "score": {"type": "integer"},
                },
            }
        )
    )
    src = tmp_path / "wf.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input query text
  max_total_steps 5
  model m
  artifact verdict json
    schema "schemas/v.json"
    extract is_ok => ok_text text
    extract score => score_text text
  artifact ok_text text
    initial ""
  artifact score_text text
    initial ""
  role r
    prompt template "p.md" with query
  state s
    actor model m
    role r
    reads query
    writes verdict json
    writes ok_text text
    writes score_text text
    on accept => done
    on stop => stop
    on error => stop
    on timeout => stop
"""
    )
    (tmp_path / "p.md").write_text("hi {query}\n")
    workflow = load_workflow(src, with_core())
    responses = [
        json.dumps(
            {"decision": "accept", "is_ok": False, "score": 7}
        )
    ]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, _, store, _ = _run(workflow, registry, run_dir, {"query": "q"})
    try:
        ok = store.read_latest("ok_text")
        assert ok is not None
        assert ok.value == "false"
        sc = store.read_latest("score_text")
        assert sc is not None
        assert sc.value == "7"
    finally:
        store.close()


def test_schema_extraction_optional_source_omitted_keeps_initial(
    tmp_path: Path,
) -> None:
    """An optional source field not present in the validated object
    leaves the target artifact unchanged. With initial=='' on first
    write, the target reads back as the empty initial string."""
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    # Optional 'note' field — extraction target is NOT read by any
    # state, so the validator does not require 'note' in 'required'.
    (schema_dir / "v.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["decision"],
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "stop"],
                    },
                    "note": {"type": "string"},
                },
            }
        )
    )
    src = tmp_path / "wf.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input query text
  max_total_steps 5
  model m
  artifact verdict json
    schema "schemas/v.json"
    extract note => note_text text
  artifact note_text text
    initial "INITIAL"
  role r
    prompt template "p.md" with query
  state s
    actor model m
    role r
    reads query
    writes verdict json
    writes note_text text
    on accept => done
    on stop => stop
    on error => stop
    on timeout => stop
"""
    )
    (tmp_path / "p.md").write_text("hi {query}\n")
    workflow = load_workflow(src, with_core())
    responses = [json.dumps({"decision": "accept"})]
    registry = _build_registry(responses)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, _, store, _ = _run(workflow, registry, run_dir, {"query": "q"})
    try:
        n = store.read_latest("note_text")
        assert n is not None
        # Optional field absent -> initial value retained.
        assert n.value == "INITIAL"
    finally:
        store.close()
