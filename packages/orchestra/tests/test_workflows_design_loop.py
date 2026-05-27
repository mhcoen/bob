"""Mechanical tests for the design_loop workflow (T-000015).

The design_loop pattern is a judge-first iterative-design state machine:
the judge produces (or revises) an artifact, the reviewer emits a
critique only, and the judge then either revises again or declares the
artifact done. The cap on judge invocations is the ``max_rounds``
external input.

This file covers the mechanical-behavior contract listed in T-000015:

  - round threading: judge runs once per round, reviewer once between
  - cap enforcement at exactly max_rounds (terminates CAPPED)
  - same-model rejection: judge and reviewer must be distinct actors
  - first-turn done rejected as malformed
  - malformed reviewer output recoverable on retry / fatal on second
  - malformed judge output recoverable on retry / fatal on second
  - produce action on a subsequent invocation rejected as malformed
  - adapter failure preserves transcript and terminates ERROR
  - transcript JSONL is incremental (one turn per role completion)

Some scenarios depend on workflow wiring that has not landed yet
(the new produce/revise/done schema with retry-once-then-fail
transitions). Those tests skip with a clear reason so the scenarios
remain documented and can be enabled when the wiring catches up. The
documented gap is captured in NOTES.md under [2.10] [T-000015].

Tests never make real subprocess calls; the model backing is replaced
by a ``_ScriptedModelAdapter`` that returns canned responses keyed by
``state_id``. Each test runs the executor end-to-end through the
in-memory state machine, then inspects the run log, the transcript
JSONL, and the artifact store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import (
    Turn,
    WorkflowApiError,
    _derive_termination,
    _IncrementalTranscriptWriter,
    _pre_load_registry,
    run_role,
)
from orchestra.errors import OrchestraError
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogReader, LogWriter
from orchestra.schema import SchemaError, load_schema
from orchestra.spine import (
    NO_INITIAL,
    InvocationRequest,
    PreparedInvocation,
    Workflow,
)
from orchestra.store import ArtifactStore

# --------------------------------------------------------------------
# Scripted adapter helper
# --------------------------------------------------------------------


class _ScriptedModelAdapter:
    """Mock model adapter that returns a sequence of responses keyed
    by ``state_id``. Each state has its own queue; pop the next
    response on each invocation. ``RAISE`` markers in the queue raise
    a synthetic adapter failure on that call so adapter-failure paths
    can be exercised without real subprocess invocations.
    """

    backing = "model"

    RAISE = object()

    def __init__(self, responses: dict[str, list[Any]]) -> None:
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
            raise AssertionError(f"scripted adapter has no response for {state_id!r}")
        next_value = queue.pop(0)
        if next_value is _ScriptedModelAdapter.RAISE:
            raise OrchestraError(f"synthetic adapter failure for {state_id!r}")
        text = next_value
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


def _run_design_loop(
    tmp_path: Path,
    *,
    responses: dict[str, list[Any]],
    max_rounds: int = 4,
) -> tuple[_ScriptedModelAdapter, Path, str, ArtifactStore, Workflow]:
    """Build and execute the design_loop workflow with a scripted
    adapter. Returns ``(adapter, run_dir, terminal, store, workflow)``.

    The caller is responsible for closing ``store`` after inspection.
    The on_state_exit hook installs the same incremental transcript
    writer the public api uses, so transcript-related assertions are
    pinned against the production behavior.
    """
    path = resolve_workflow_path("design_loop", project_dir=None)
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
    transcript_path = run_dir / "transcript.jsonl"
    transcript_writer = _IncrementalTranscriptWriter(transcript_path, run_dir, workflow)
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
            "max_rounds": max_rounds,
        },
        on_state_exit=transcript_writer,
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return adapter, run_dir, terminal, store, workflow


def _read_transcript(run_dir: Path) -> list[dict[str, Any]]:
    """Read the incremental transcript JSONL file from a run."""
    path = run_dir / "transcript.jsonl"
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _verdict(decision: str, feedback: str = "") -> str:
    """Build a JSON judge verdict matching the current
    iterate_judge_verdict.json schema referenced by design_loop.orc."""
    return json.dumps({"decision": decision, "feedback": feedback})


# --------------------------------------------------------------------
# Workflow loads cleanly
# --------------------------------------------------------------------


def test_design_loop_workflow_loads_and_validates() -> None:
    """design_loop.orc parses, the judge state is the start state,
    and the only states are judge and review (no propose)."""
    path = resolve_workflow_path("design_loop", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "design_loop"
    assert workflow.start_state_name() == "judge"
    state_names = {s.name for s in workflow.states}
    assert state_names == {"judge", "review"}
    declared = {ext.name for ext in workflow.external_inputs}
    assert {"query", "history", "max_rounds"} <= declared


# --------------------------------------------------------------------
# T-000015 case: round threading with cap=3 converging at round 3
# --------------------------------------------------------------------


def test_round_threading_with_cap3_converging_at_round_3(tmp_path: Path) -> None:
    """Judge issues iterate on rounds 1 and 2, accept on round 3 with
    max_rounds=3. The workflow runs (judge, review) twice then judge a
    third time and terminates via ``on accept => done``. Three judge
    completions sit in the transcript, and the last judge transition
    targets ``done`` with the schema-derived accept outcome.
    """
    responses = {
        "judge": [
            _verdict("iterate", "needs structural work"),
            _verdict("iterate", "still missing item 3"),
            _verdict("accept", "ok now"),
        ],
        "review": [
            json.dumps({"issues": [], "rationale": "first pass"}),
            json.dumps({"issues": [], "rationale": "second pass"}),
        ],
    }
    adapter, run_dir, terminal, store, _ = _run_design_loop(
        tmp_path, responses=responses, max_rounds=3
    )
    try:
        assert terminal == "done"
        judge_calls = [c for c in adapter.calls if c["state_id"] == "judge"]
        review_calls = [c for c in adapter.calls if c["state_id"] == "review"]
        assert len(judge_calls) == 3
        assert len(review_calls) == 2
        records = LogReader(run_dir / "log.jsonl").read_all()
        state_exits = [r for r in records if r.event == "state_exit"]
        last_judge_exit = next(r for r in reversed(state_exits) if r.state_id == "judge")
        assert last_judge_exit.fields["outcome"] == "accept"
        transitions = [r for r in records if r.event == "transition"]
        last_judge_transition = next(r for r in reversed(transitions) if r.state_id == "judge")
        assert last_judge_transition.fields["target"] == "done"
        assert last_judge_transition.fields["outcome"] == "accept"
    finally:
        store.close()


# --------------------------------------------------------------------
# T-000015 case: cap enforcement at exactly max_rounds terminates CAPPED
# --------------------------------------------------------------------


def test_cap_enforcement_at_exactly_max_rounds_terminates_capped(tmp_path: Path) -> None:
    """With max_rounds=3 and the judge issuing iterate on every round,
    the third judge invocation hits the cap guard. The guard expression
    ``attempts.judge < max_rounds`` is false (3 < 3), so the unguarded
    fallback ``on iterate => done`` fires. Terminal is ``done`` with
    the cap-hit transition outcome=iterate target=done. ``_derive_termination``
    classifies this as CAPPED because the target is done but the outcome
    is not the judge's own done action.
    """
    iterate = _verdict("iterate", "more work")
    responses = {
        "judge": [iterate, iterate, iterate],
        "review": [
            json.dumps({"issues": [], "rationale": "r1"}),
            json.dumps({"issues": [], "rationale": "r2"}),
        ],
    }
    adapter, run_dir, terminal, store, _ = _run_design_loop(
        tmp_path, responses=responses, max_rounds=3
    )
    try:
        assert terminal == "done"
        judge_calls = [c for c in adapter.calls if c["state_id"] == "judge"]
        review_calls = [c for c in adapter.calls if c["state_id"] == "review"]
        # Cap=3: judge runs 3 times total; review runs only between
        # consecutive judge rounds, so twice.
        assert len(judge_calls) == 3
        assert len(review_calls) == 2
        records = LogReader(run_dir / "log.jsonl").read_all()
        transitions = [r for r in records if r.event == "transition"]
        last_judge_transition = next(r for r in reversed(transitions) if r.state_id == "judge")
        assert last_judge_transition.fields["target"] == "done"
        assert last_judge_transition.fields["outcome"] == "iterate"
        # Termination classification: outcome != "done" but target ==
        # "done" so this is CAPPED.
        termination, error = _derive_termination(run_dir / "log.jsonl")
        assert termination == "CAPPED"
        assert error is None
    finally:
        store.close()


# --------------------------------------------------------------------
# T-000015 case: same-model rejection at workflow start
# --------------------------------------------------------------------


def test_same_model_rejection_at_workflow_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_role('design', ...)`` resolves the compound binding and
    rejects judge and reviewer that resolve to the same actor. The
    workflow never executes; the error names the role and reports
    "same actor" so the misconfiguration is visible up front.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg_dir = tmp_path / ".orchestra"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "role_bindings": {
                    "design": {
                        "pattern": "design_loop",
                        "judge_role": {"model": "opus"},
                        "reviewer": {"model": "opus"},
                    },
                },
            }
        )
    )
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", project_dir=tmp_path)
    msg = str(excinfo.value)
    assert "design" in msg
    assert "same actor" in msg


# --------------------------------------------------------------------
# T-000015 case: adapter failure preserves transcript and terminates ERROR
# --------------------------------------------------------------------


def test_judge_adapter_failure_preserves_transcript_and_terminates_error(
    tmp_path: Path,
) -> None:
    """The judge runs once successfully (iterate → review), then on
    the second judge invocation the adapter raises. The workflow
    terminates on ``stop`` via ``on error => stop`` and the transcript
    JSONL retains the first judge turn plus the reviewer turn — the
    completions that landed before the failure are durable on disk.
    """
    responses = {
        "judge": [
            _verdict("iterate", "needs revision"),
            _ScriptedModelAdapter.RAISE,
        ],
        "review": [json.dumps({"issues": [], "rationale": "r1"})],
    }
    adapter, run_dir, terminal, store, _ = _run_design_loop(
        tmp_path, responses=responses, max_rounds=4
    )
    try:
        assert terminal == "stop"
        records = LogReader(run_dir / "log.jsonl").read_all()
        transitions = [r for r in records if r.event == "transition"]
        last_transition = transitions[-1]
        assert last_transition.fields["target"] == "stop"
        # _derive_termination classifies this as ERROR with the actor's
        # failure preserved in the ErrorRecord.
        termination, error = _derive_termination(run_dir / "log.jsonl")
        assert termination == "ERROR"
        assert error is not None
        assert error.kind == "actor_failure"
        assert error.state == "judge"
        # Transcript: first judge completion + reviewer completion + the
        # failed judge state_exit. All three are durable on disk.
        transcript = _read_transcript(run_dir)
        states_in_transcript = [t["state"] for t in transcript]
        assert states_in_transcript[:2] == ["judge", "review"]
        # The failed judge's state_exit also lands in the transcript
        # because the writer fires after every state_exit (error or
        # ok); the entry's status is "error".
        assert any(t["state"] == "judge" and t["status"] == "error" for t in transcript)
    finally:
        store.close()


def test_reviewer_adapter_failure_preserves_transcript_and_terminates_error(
    tmp_path: Path,
) -> None:
    """The judge runs once (iterate → review), the reviewer adapter
    raises on its first invocation. ``on error => stop`` fires on the
    review state; terminal=stop; the judge's first turn is preserved
    in the transcript.
    """
    responses = {
        "judge": [_verdict("iterate", "needs revision")],
        "review": [_ScriptedModelAdapter.RAISE],
    }
    adapter, run_dir, terminal, store, _ = _run_design_loop(
        tmp_path, responses=responses, max_rounds=4
    )
    try:
        assert terminal == "stop"
        termination, error = _derive_termination(run_dir / "log.jsonl")
        assert termination == "ERROR"
        assert error is not None
        assert error.kind == "actor_failure"
        assert error.state == "review"
        transcript = _read_transcript(run_dir)
        # First entry: judge completion (status=ok). Second entry: the
        # failed reviewer state_exit (status=error). Both durable.
        assert transcript[0]["state"] == "judge"
        assert transcript[0]["status"] == "ok"
        assert transcript[1]["state"] == "review"
        assert transcript[1]["status"] == "error"
    finally:
        store.close()


# --------------------------------------------------------------------
# T-000015 case: transcript JSONL is incremental
# --------------------------------------------------------------------


def test_transcript_jsonl_is_incremental_with_one_line_per_role_completion(
    tmp_path: Path,
) -> None:
    """Each state completion appends exactly one well-formed JSON line
    to ``transcript.jsonl``. The line count after the run equals the
    number of state_exit records on the log, and every line decodes
    into a Turn-shaped object carrying role, state, attempt, status,
    outcome, and started_at/ended_at strings.
    """
    responses = {
        "judge": [
            _verdict("iterate", "r1"),
            _verdict("accept", "done"),
        ],
        "review": [json.dumps({"issues": [], "rationale": "r1"})],
    }
    _, run_dir, terminal, store, _ = _run_design_loop(tmp_path, responses=responses, max_rounds=4)
    try:
        assert terminal == "done"
        records = LogReader(run_dir / "log.jsonl").read_all()
        state_exits = [r for r in records if r.event == "state_exit"]
        transcript = _read_transcript(run_dir)
        assert len(transcript) == len(state_exits)
        # Order matches state_exit order.
        for log_record, line in zip(state_exits, transcript, strict=True):
            assert line["state"] == log_record.state_id
            assert line["status"] == log_record.fields["status"]
            assert line["outcome"] == log_record.fields["outcome"]
        # Schema: every line carries the Turn dataclass fields. Use the
        # in-memory Turn class as the reference for required keys.
        required_keys = set(Turn.__dataclass_fields__.keys())
        for line in transcript:
            assert required_keys <= set(line.keys())

    finally:
        store.close()


def test_transcript_jsonl_is_written_before_run_end(tmp_path: Path) -> None:
    """The transcript writer fsyncs each line immediately. A run that
    completes leaves a non-empty transcript.jsonl with each line ending
    in a newline, so a partial read at any point yields whole records.
    """
    responses = {
        "judge": [_verdict("accept", "fast accept")],
        "review": [],
    }
    _, run_dir, terminal, store, _ = _run_design_loop(tmp_path, responses=responses, max_rounds=4)
    try:
        assert terminal == "done"
        transcript_path = run_dir / "transcript.jsonl"
        raw = transcript_path.read_bytes()
        assert raw.endswith(b"\n")
        # File is well-formed JSONL.
        lines = raw.decode("utf-8").splitlines()
        assert lines, "transcript should not be empty after a completed run"
        for line in lines:
            json.loads(line)
        # At least one judge turn was recorded.
        first = json.loads(lines[0])
        assert first["state"] == "judge"
        assert first["status"] == "ok"
    finally:
        store.close()


# --------------------------------------------------------------------
# Malformed judge output: schema-violation routes to the error outcome.
# Current design_loop.orc has no `retry max` configuration on judge or
# review, so a single failure terminates ERROR immediately. The retry-
# once-then-fail and retry-then-fatal-on-second scenarios from T-000015
# are skipped below until that wiring lands.
# --------------------------------------------------------------------


def test_judge_invalid_json_routes_to_error_and_terminates(tmp_path: Path) -> None:
    """Non-JSON judge output triggers ``_apply_schema_layer``'s
    json_parse failure path. The state exits with outcome=error and
    the unretried ``on error => stop`` fires. ``_derive_termination``
    reads the actor_failure record off the last state_exit and
    classifies the run as ERROR.
    """
    responses = {
        "judge": ["this is not JSON"],
        "review": [],
    }
    _, run_dir, terminal, store, _ = _run_design_loop(tmp_path, responses=responses, max_rounds=4)
    try:
        assert terminal == "stop"
        records = LogReader(run_dir / "log.jsonl").read_all()
        schema_records = [r for r in records if r.event == "schema_validation"]
        assert schema_records
        assert schema_records[-1].fields["outcome"] == "parse_error"
        termination, error = _derive_termination(run_dir / "log.jsonl")
        assert termination == "ERROR"
        assert error is not None
        assert error.kind == "actor_failure"
        assert error.state == "judge"
        # The detail field carries the schema_validation reason so
        # consumers can distinguish parse errors from schema violations.
        assert error.detail is not None
        assert error.detail.get("reason") == "json_parse"
    finally:
        store.close()


def test_judge_schema_violating_decision_routes_to_error(tmp_path: Path) -> None:
    """Valid JSON whose ``decision`` field is not in the schema's enum
    (here a fictitious "ponder" value) fails schema validation. The
    schema layer emits a ``schema_validation`` record with
    outcome=schema_error and the state exits via ``on error => stop``.
    """
    responses = {
        "judge": [json.dumps({"decision": "ponder", "feedback": "x"})],
        "review": [],
    }
    _, run_dir, terminal, store, _ = _run_design_loop(tmp_path, responses=responses, max_rounds=4)
    try:
        assert terminal == "stop"
        records = LogReader(run_dir / "log.jsonl").read_all()
        schema_records = [r for r in records if r.event == "schema_validation"]
        assert schema_records[-1].fields["outcome"] == "schema_error"
        termination, error = _derive_termination(run_dir / "log.jsonl")
        assert termination == "ERROR"
        assert error is not None
        assert error.kind == "actor_failure"
        assert error.detail is not None
        assert error.detail.get("reason") == "schema_violation"
    finally:
        store.close()


# --------------------------------------------------------------------
# Schema-layer support gap for the new design_loop_judge.json.
# --------------------------------------------------------------------


def test_design_loop_judge_schema_uses_unsupported_oneof_keyword() -> None:
    """The new design_loop_judge.json schema files the three judge
    output variants (produce / revise / done) under a root ``oneOf``.
    Orchestra's v0 schema layer rejects ``oneOf`` (and any of $ref /
    anyOf / allOf / not). This test pins that gap: until the schema
    layer grows oneOf support, or the schema is restructured into a
    single-object form with a ``decision`` enum, design_loop.orc cannot
    bind to design_loop_judge.json. The workflow continues to use
    iterate_judge_verdict.json.
    """
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "design_loop_judge.json"
    )
    assert schema_path.is_file()
    with pytest.raises(SchemaError) as excinfo:
        load_schema(schema_path)
    # The schema layer rejects either at the unsupported oneOf keyword
    # or at the root type check; both indicate the same gap.
    msg = str(excinfo.value)
    assert "oneOf" in msg or "'type'" in msg or "type" in msg


# --------------------------------------------------------------------
# T-000015 scenarios that depend on workflow wiring not yet in place.
# Skipped with a clear reason so the cases remain documented for the
# next wiring step.
# --------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Requires the design_loop_judge.json (produce/revise/done) "
        "schema wired into design_loop.orc plus a workflow-level "
        "first-turn-done guard. The orchestra v0 schema layer does "
        "not yet support the oneOf shape design_loop_judge.json uses, "
        "and design_loop.orc still binds iterate_judge_verdict.json. "
        "See test_design_loop_judge_schema_uses_unsupported_oneof_keyword."
    )
)
def test_first_turn_done_rejected_as_malformed(tmp_path: Path) -> None:
    """First-invocation judge output with ``action: \"done\"`` must
    fail because no artifact has been produced yet. The contract is
    documented in the design_loop_judge.md template; the workflow-
    level enforcement is pending. When wired, this test should drive
    the judge to emit ``{action: "done", rationale: "..."}`` on its
    first call and expect the workflow to terminate ERROR (or to
    retry once and then fail) with a malformed-output diagnostic."""
    raise NotImplementedError


@pytest.mark.skip(
    reason=(
        "Requires the design_loop_judge.json schema wired into "
        "design_loop.orc with first-turn-vs-subsequent enforcement. "
        "See test_design_loop_judge_schema_uses_unsupported_oneof_keyword."
    )
)
def test_judge_produce_on_subsequent_invocation_rejected_as_malformed(
    tmp_path: Path,
) -> None:
    """After the reviewer's first critique, the judge must emit
    ``revise`` or ``done`` (not ``produce``). The template forbids
    produce on subsequent turns; the workflow-level enforcement is
    pending. When wired, this test should drive the judge to emit
    iterate-then-produce and expect the workflow to terminate ERROR
    (or to retry once and then fail) with a malformed-output
    diagnostic."""
    raise NotImplementedError


@pytest.mark.skip(
    reason=(
        "Requires `on error retry max 1 then stop` on the review "
        "state plus a reviewer-output schema (design_loop_review.json) "
        "wired into design_loop.orc. The current workflow has no "
        "schema on review_output and no retry transition, so a "
        "malformed reviewer output is not detectable at the orchestra "
        "layer and there is no retry budget to consume."
    )
)
def test_malformed_reviewer_output_recoverable_on_retry(tmp_path: Path) -> None:
    """Reviewer emits invalid JSON on attempt 1, valid JSON on
    attempt 2. With ``on error retry max 1 then stop`` declared on
    the review state and design_loop_review.json bound as the review
    schema, the workflow should re-enter review once and continue."""
    raise NotImplementedError


@pytest.mark.skip(
    reason=("Requires the same wiring as test_malformed_reviewer_output_recoverable_on_retry.")
)
def test_malformed_reviewer_output_fatal_on_second_failure(tmp_path: Path) -> None:
    """Reviewer emits invalid JSON twice in a row. After the retry
    budget is exhausted the transition falls to ``then stop`` and the
    workflow terminates ERROR with the second failure recorded."""
    raise NotImplementedError


@pytest.mark.skip(
    reason=(
        "Requires `on error retry max 1 then stop` on the judge "
        "state. The current design_loop.orc has only `on error => "
        "stop`, so a malformed judge output terminates immediately "
        "without consuming a retry budget. Covered partially by "
        "test_judge_invalid_json_routes_to_error_and_terminates and "
        "test_judge_schema_violating_decision_routes_to_error, which "
        "pin the no-retry path."
    )
)
def test_malformed_judge_output_recoverable_on_retry(tmp_path: Path) -> None:
    """Judge emits invalid JSON on attempt 1, valid JSON on attempt 2.
    With ``on error retry max 1 then stop`` the workflow should re-
    enter judge once and continue to the review state."""
    raise NotImplementedError


@pytest.mark.skip(
    reason=("Requires the same wiring as test_malformed_judge_output_recoverable_on_retry.")
)
def test_malformed_judge_output_fatal_on_second_failure(tmp_path: Path) -> None:
    """Judge emits invalid JSON twice. After the retry budget is
    exhausted the workflow terminates ERROR with the second failure
    recorded in the error envelope."""
    raise NotImplementedError


# --------------------------------------------------------------------
# Sanity: same-model rejection at the validator level (not just via
# run_role). Covers OrchestraConfig.role_bindings declaring matching
# adapter+model for both judge and reviewer; the rejection fires
# before workflow execution.
# --------------------------------------------------------------------


def test_design_role_validator_rejects_identical_adapter_model_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-actor rejection covers the (adapter, model) tuple, not
    just the short identifier form. Distinct identifiers that resolve
    to the same (adapter, model) tuple still trip the validator.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg_dir = tmp_path / ".orchestra"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "role_bindings": {
                    "design": {
                        "pattern": "design_loop",
                        "judge_role": {
                            "adapter": "claude_code_text",
                            "model": "opus",
                        },
                        "reviewer": {
                            "adapter": "claude_code_text",
                            "model": "opus",
                        },
                    },
                },
            }
        )
    )
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", project_dir=tmp_path)
    msg = str(excinfo.value)
    assert "same actor" in msg
    assert "claude_code_text" in msg
