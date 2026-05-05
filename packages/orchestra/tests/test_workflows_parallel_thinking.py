"""Tests for the parallel_thinking workflow.

The workflow frames a question, fans out to five panelists in
parallel, joins through the finish_panel transform, and terminates.
There is no synthesizer: the consumer reads the per-panelist outputs
by name. A failed panelist routes the workflow to stop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestra.api import _pre_load_registry
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

PANELIST_STATES: tuple[str, ...] = ("p1", "p2", "p3", "p4", "p5")

FRAMED_QUESTION_TEXT = "FRAMED-QUESTION-XYZ"
PANELIST_RESPONSES: dict[str, str] = {
    "p1": "ANSWER-FROM-PANELIST-1",
    "p2": "ANSWER-FROM-PANELIST-2",
    "p3": "ANSWER-FROM-PANELIST-3",
    "p4": "ANSWER-FROM-PANELIST-4",
    "p5": "ANSWER-FROM-PANELIST-5",
}


class _RecordingModelAdapter:
    """Mock model adapter that returns a deterministic response keyed
    by ``request.state_id`` and records every prepare call."""

    backing = "model"

    def __init__(
        self,
        responses: dict[str, str],
        *,
        fail_states: set[str] | None = None,
    ) -> None:
        self._responses = dict(responses)
        self._fail = set(fail_states or ())
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
            summary={"kind": "model", "model": binding.get("model")},
            inner={"state_id": request.state_id, "prompt": prompt},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        state_id: str = prepared.inner["state_id"]
        if state_id in self._fail:
            raise RuntimeError(f"injected failure on state {state_id}")
        text = self._responses.get(state_id)
        if text is None:
            raise AssertionError(
                f"recording adapter has no response for {state_id!r}"
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


def _default_responses() -> dict[str, str]:
    out: dict[str, str] = {"frame": FRAMED_QUESTION_TEXT}
    out.update(PANELIST_RESPONSES)
    return out


def _run_parallel_thinking(
    tmp_path: Path,
    *,
    responses: dict[str, str] | None = None,
    fail_states: set[str] | None = None,
) -> tuple[_RecordingModelAdapter, Path, str, ArtifactStore]:
    path = resolve_workflow_path("parallel_thinking", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(
        responses or _default_responses(),
        fail_states=fail_states,
    )
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
            "query": "should we adopt approach X?",
            "history": "prior context goes here",
        },
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return adapter, run_dir, terminal, store


# --------------------------------------------------------------------
# Load and validate
# --------------------------------------------------------------------


def test_parallel_thinking_loads_and_validates() -> None:
    path = resolve_workflow_path("parallel_thinking", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "parallel_thinking"
    state_names = {s.name for s in workflow.states}
    assert state_names == {"frame", "p1", "p2", "p3", "p4", "p5", "finish"}
    finish = workflow.state("finish")
    assert finish.actor.kind == "transform"
    assert finish.actor.ref == "finish_panel"


# --------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------


def test_parallel_thinking_happy_path(tmp_path: Path) -> None:
    adapter, run_dir, terminal, store = _run_parallel_thinking(tmp_path)
    try:
        assert terminal == "done"
        # Six model calls (one framer, five panelists).
        called_states = [c["state_id"] for c in adapter.calls]
        assert called_states[0] == "frame"
        assert sorted(called_states[1:]) == sorted(PANELIST_STATES)
        # Each panelist's output is committed under its own artifact.
        for i, state_name in enumerate(PANELIST_STATES, start=1):
            art = store.read_latest(f"panelist_{i}_output")
            assert art is not None
            assert art.value == PANELIST_RESPONSES[state_name]
        framed = store.read_latest("framed_question")
        assert framed is not None
        assert framed.value == FRAMED_QUESTION_TEXT
        # Finish marker is set by the transform.
        finish_art = store.read_latest("finish_marker")
        assert finish_art is not None
        assert finish_art.value == "ok"
    finally:
        store.close()


# --------------------------------------------------------------------
# Panelist failure routes the workflow to stop
# --------------------------------------------------------------------


def test_parallel_thinking_panelist_failure_routes_to_stop(
    tmp_path: Path,
) -> None:
    adapter, run_dir, terminal, store = _run_parallel_thinking(
        tmp_path, fail_states={"p3"}
    )
    try:
        assert terminal == "stop", (
            "a failed panelist must route the workflow to stop "
            "(partial panel is not a meaningful result)"
        )
        # finish_marker should not be committed because the join was
        # never reached.
        finish_art = store.read_latest("finish_marker")
        assert finish_art is None
    finally:
        store.close()


# --------------------------------------------------------------------
# Fan-out propagates the framed question to every panelist
# --------------------------------------------------------------------


def test_parallel_thinking_each_panelist_sees_framed_question(
    tmp_path: Path,
) -> None:
    adapter, run_dir, terminal, _store = _run_parallel_thinking(tmp_path)
    try:
        assert terminal == "done"
        for state in PANELIST_STATES:
            calls_for_state = [c for c in adapter.calls if c["state_id"] == state]
            assert len(calls_for_state) == 1
            prompt = calls_for_state[0]["prompt"]
            assert FRAMED_QUESTION_TEXT in prompt, (
                f"panelist {state!r} prompt did not include the framed "
                f"question text. prompt={prompt!r}"
            )
    finally:
        _store.close()


# --------------------------------------------------------------------
# fan_out and join records appear in the log
# --------------------------------------------------------------------


def test_parallel_thinking_fan_out_records_in_log(tmp_path: Path) -> None:
    _, run_dir, terminal, store = _run_parallel_thinking(tmp_path)
    try:
        assert terminal == "done"
        records = LogReader(run_dir / "log.jsonl").read_all()
        events = [r.event for r in records]
        assert "fan_out_start" in events
        assert "fan_out_end" in events
    finally:
        store.close()
