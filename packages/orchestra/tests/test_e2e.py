"""End-to-end tests A, B, C from the implementation plan.

These tests drive the full runner spine: loader -> validator ->
executor -> adapter -> result parser -> artifact store -> log -> resume.

Test A is the happy path. Test B is parser-failure rollback. Test C is
resume-from-interrupted-state. The three together cover the slice's
definition of done items 1-3 (unit tests already pass on their own
files).

Determinism check (definition of done item 4) is in
``test_e2e_determinism.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogReader, LogWriter
from orchestra.registry.registry import ProfileRegistry, ResultParser, with_core
from orchestra.resume import replay_log, run_resume_hooks
from orchestra.spine import NO_INITIAL, Workflow
from orchestra.store import ArtifactStore

FIXTURE = Path(__file__).parent / "fixtures" / "slice1" / "echo.orc"


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


# ====================================================================
# Test A: happy path
# ====================================================================


def test_a_happy_path(tmp_path: Path) -> None:
    MockHumanAdapter.clear_shared_script()
    MockHumanAdapter.set_shared_script(["accept"])

    registry = with_core()
    workflow = load_workflow(FIXTURE, registry)

    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(FIXTURE)})

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello world"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()

    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    events = [r.event for r in records]

    assert events[0] == "run_start"
    assert events[-1] == "run_end"

    state_enters = [r for r in records if r.event == "state_enter"]
    assert [r.state_id for r in state_enters] == ["respond", "confirm"]

    aw = [r for r in records if r.event == "artifact_write"]
    assert len(aw) == 1
    assert aw[0].fields["artifact"] == "response"

    pr = [r for r in records if r.event == "parser_run"]
    assert len(pr) == 1
    assert pr[0].state_id == "respond"

    exits = [r for r in records if r.event == "state_exit"]
    assert len(exits) == 2
    assert all(r.fields["status"] == "ok" for r in exits)
    assert exits[0].fields["outcome"] == "complete"
    assert exits[1].fields["outcome"] == "accept"

    committed = store.list_versions("response")
    assert len(committed) == 1
    v = store.read_latest("response")
    assert v is not None
    assert v.value.startswith("[mock-llm response to:")

    payload_files = sorted((run_dir / "payloads").glob("*.json"))
    assert len(payload_files) == 2

    store.close()
    MockHumanAdapter.clear_shared_script()


# ====================================================================
# Test B: parser failure rollback
# ====================================================================


def _faulty_text_parser_fn(envelope: Any, _store: object) -> list[tuple[str, Any]]:
    raise RuntimeError("intentional parser failure")


def test_b_parser_failure_rollback(tmp_path: Path) -> None:
    MockHumanAdapter.clear_shared_script()
    MockHumanAdapter.set_shared_script(["accept"])

    registry = ProfileRegistry()
    for t in ("text", "json", "messages", "prompt", "schema", "document"):
        registry.register_artifact_type(t)
    from orchestra.adapters.mock_human import MockHumanAdapter as MH
    from orchestra.adapters.mock_model import MockModelAdapter
    from orchestra.adapters.mock_shell import MockShellAdapter

    registry.register_actor_backing("model", MockModelAdapter)
    registry.register_actor_backing("human", MH)
    registry.register_actor_backing("shell", MockShellAdapter)
    registry.register_result_parser(
        ResultParser(
            name="faulty_text",
            backing_filter=("model",),
            artifact_type_filter=("text",),
            fn=_faulty_text_parser_fn,
        )
    )

    workflow = load_workflow(FIXTURE, registry)

    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(FIXTURE)})

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()

    assert terminal == "stop"

    records = LogReader(run_dir / "log.jsonl").read_all()

    aw = [r for r in records if r.event == "artifact_write"]
    assert aw == []

    respond_exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "respond"
    ]
    assert len(respond_exits) == 1
    exit_fields = respond_exits[0].fields
    assert exit_fields["status"] == "error"
    assert exit_fields["outcome"] == "error"
    err = exit_fields["error"]
    assert err is not None
    assert err["kind"] == "parser_failure"

    committed = store.list_versions("response")
    assert committed == []

    pr = [r for r in records if r.event == "parser_run"]
    assert len(pr) == 1
    assert pr[0].fields["parser"] == "faulty_text"

    confirm_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "confirm"
    ]
    assert confirm_enters == []

    store.close()
    MockHumanAdapter.clear_shared_script()


# ====================================================================
# Test C: resume from interrupted state
# ====================================================================


def test_c_resume_from_interrupted_state(tmp_path: Path) -> None:
    """Simulate a crash on entry to ``confirm``, then resume."""
    MockHumanAdapter.clear_shared_script()

    registry = with_core()
    workflow = load_workflow(FIXTURE, registry)

    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(FIXTURE)})

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hi"},
    )
    target = executor.step()
    assert target == "confirm", f"expected respond -> confirm, got {target!r}"

    log.write(
        "state_enter",
        state_id="confirm",
        attempt=1,
        fields={
            "attempts": {"respond": 1, "confirm": 1},
            "retries": {"respond": 0, "confirm": 0},
        },
    )
    log.write(
        "actor_prepare",
        state_id="confirm",
        attempt=1,
        fields={
            "summary": {
                "kind": "human",
                "options": ["accept", "reject"],
                "prompt_chars": 0,
                "prompt_preview": "",
            }
        },
    )
    log.close()
    store.close()

    MockHumanAdapter.set_shared_script(["accept"])

    replay = replay_log(str(run_dir / "log.jsonl"))
    assert replay.current_state == "confirm"
    assert replay.last_state_completed is False
    assert replay.attempts.get("confirm") == 1
    assert replay.attempts.get("respond") == 1
    # The respond envelope is reconstructed from its state_exit.
    assert "respond" in replay.envelopes
    assert replay.envelopes["respond"].outcome == "complete"

    registry2 = with_core()
    workflow2 = load_workflow(FIXTURE, registry2)
    store2 = ArtifactStore(run_dir / "store.sqlite")
    log2 = LogWriter(
        run_dir / "log.jsonl",
        replay.last_run_id,
        start_seq=replay.next_seq,
    )
    run_resume_hooks(workflow2, registry2, replay, log2)

    executor2 = Executor(
        workflow=workflow2,
        registry=registry2,
        store=store2,
        log=log2,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs={"topic": "hi"},
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
    )
    terminal = executor2.run_to_completion()
    log2.write("run_end", fields={"terminal": terminal})
    log2.close()
    store2.close()

    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()

    rh = [r for r in records if r.event == "resume_hook"]
    assert rh == []

    confirm_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "confirm"
    ]
    assert len(confirm_enters) == 2
    assert confirm_enters[0].attempt == 1
    assert confirm_enters[1].attempt == 2

    confirm_exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "confirm"
    ]
    assert len(confirm_exits) == 1
    assert confirm_exits[0].fields["outcome"] == "accept"

    respond_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "respond"
    ]
    respond_exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "respond"
    ]
    assert len(respond_enters) == 1
    assert len(respond_exits) == 1
    assert respond_enters[0].attempt == 1
    assert respond_exits[0].attempt == 1

    MockHumanAdapter.clear_shared_script()
