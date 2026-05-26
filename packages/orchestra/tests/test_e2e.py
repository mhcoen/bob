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

from orchestra import cli
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


def test_resume_after_state_exit_without_transition_does_not_reexecute(
    tmp_path: Path,
) -> None:
    """Slice A regression: a crash between ``state_exit`` and
    ``transition`` must NOT cause the just-completed state's actor
    body to run again on resume. The resume path re-selects the
    transition from the reconstructed envelope and writes the missing
    record; the state has exactly one ``state_enter`` and one
    ``state_exit`` after resume, the artifact written by that single
    invocation remains visible, and the workflow proceeds normally.
    """
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
    log.close()
    store.close()

    # Truncate the log so the durable ``state_exit`` for ``respond``
    # is preserved but the trailing ``transition`` record is dropped.
    # Anything after the ``transition`` (including the new state's
    # ``state_enter``) is also dropped, simulating a crash in the
    # window between ``state_exit`` and the routing-decision write.
    log_path = run_dir / "log.jsonl"
    records_before_truncate = LogReader(log_path).read_all()
    truncate_at: int | None = None
    seen_state_exit = False
    for r in records_before_truncate:
        if (
            r.event == "state_exit"
            and r.state_id == "respond"
        ):
            seen_state_exit = True
            continue
        if seen_state_exit and r.event == "transition":
            truncate_at = r.seq
            break
    assert truncate_at is not None, (
        "fixture must produce a transition record after respond's "
        "state_exit for this test to simulate the crash window"
    )
    keep_records = [
        r for r in records_before_truncate if r.seq < truncate_at
    ]
    with open(log_path, "w", encoding="utf-8") as fh:
        for r in keep_records:
            fh.write(r.to_json() + "\n")

    # Replay should report the state as completed AND flag the
    # state_exit_without_transition recovery path. The respond
    # envelope must be available so the resume helper can pick the
    # transition from it.
    replay = replay_log(str(log_path))
    assert replay.current_state == "respond"
    assert replay.last_state_completed is True
    assert replay.state_exit_without_transition is True
    assert "respond" in replay.envelopes

    # An adapter that raises if it is ever asked to prepare or
    # invoke during resume. The Slice A fix means resume must NOT
    # call the actor again for ``respond``.
    class _ExplodeIfReinvoked:
        def __init__(self) -> None:
            self.calls = 0

        def prepare(self, request: Any) -> Any:
            self.calls += 1
            raise AssertionError(
                "respond's actor must not be re-invoked during resume; "
                "the state_exit was already durable before the crash"
            )

        def invoke(self, prepared: Any) -> Any:
            self.calls += 1
            raise AssertionError(
                "respond's actor must not be re-invoked during resume"
            )

        def cancel(self, prepared: Any) -> None:
            pass

    explode_adapter = _ExplodeIfReinvoked()

    MockHumanAdapter.set_shared_script(["accept"])

    registry2 = ProfileRegistry()
    # Inline artifact types that the workflow uses.
    for type_name in ("text", "json", "messages", "prompt", "schema", "document"):
        registry2.register_artifact_type(type_name)
    registry2.register_actor_backing("model", lambda: explode_adapter)
    from orchestra.adapters.mock_human import MockHumanAdapter as _Human
    from orchestra.adapters.mock_shell import MockShellAdapter as _Shell

    registry2.register_actor_backing("human", _Human)
    registry2.register_actor_backing("shell", _Shell)
    from orchestra.executor.parsers import identity_text_parser

    registry2.register_result_parser(identity_text_parser)

    workflow2 = load_workflow(FIXTURE, registry2)
    store2 = ArtifactStore(run_dir / "store.sqlite")
    log2 = LogWriter(
        log_path,
        replay.last_run_id,
        start_seq=replay.next_seq,
    )
    run_resume_hooks(workflow2, registry2, replay, log2)

    from orchestra.visibility import VisibilityIndex

    visibility_index = VisibilityIndex(persist_path=run_dir / "visibility.json")
    visibility_index.replace_from(replay.visibility_statuses)

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
        visibility_index=visibility_index,
    )
    if replay.state_exit_without_transition:
        executor2.resume_pending_transition(replay.current_state)

    terminal = executor2.run_to_completion()
    log2.write("run_end", fields={"terminal": terminal})
    log2.close()
    store2.close()

    assert terminal == "done"
    assert explode_adapter.calls == 0, (
        "the model adapter was invoked during resume; the Slice A "
        "fix must keep a completed state from re-running"
    )

    records = LogReader(log_path).read_all()
    respond_enters = [
        r
        for r in records
        if r.event == "state_enter" and r.state_id == "respond"
    ]
    respond_exits = [
        r
        for r in records
        if r.event == "state_exit" and r.state_id == "respond"
    ]
    respond_transitions = [
        r
        for r in records
        if r.event == "transition" and r.state_id == "respond"
    ]
    # The actor body ran exactly once (one state_enter and one
    # state_exit, both attempt 1). The missing transition was filled
    # in by resume_pending_transition.
    assert len(respond_enters) == 1
    assert len(respond_exits) == 1
    assert respond_enters[0].attempt == 1
    assert respond_exits[0].attempt == 1
    assert len(respond_transitions) == 1
    assert respond_transitions[0].fields["target"] == "confirm"

    MockHumanAdapter.clear_shared_script()


# ====================================================================
# Round-2 regressions: state_exit_without_transition must not misfire
# across multi-state workflows, and resume must hydrate envelope
# payloads so guards evaluate correctly.
# ====================================================================


def _write_dummy_template(tmp_path: Path) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "dummy.md").write_text("dummy\n")


class _DenylistAdapter:
    """Wraps MockModelAdapter; raises if any state in ``denied`` is
    asked to prepare or invoke. Used to prove resume does not
    re-invoke a state's body when its ``state_exit`` is durable.
    """

    def __init__(self, denied: set[str]) -> None:
        from orchestra.adapters.mock_model import MockModelAdapter

        self._inner = MockModelAdapter()
        self.denied = set(denied)
        self.invocations: list[str] = []

    def prepare(self, request: Any) -> Any:
        if request.state_id in self.denied:
            raise AssertionError(
                f"state {request.state_id!r} must not be re-invoked "
                "on resume; the state_exit was durable before the crash"
            )
        self.invocations.append(f"prepare:{request.state_id}")
        return self._inner.prepare(request)

    def invoke(self, prepared: Any) -> Any:
        sid = prepared.request.state_id
        if sid in self.denied:
            raise AssertionError(
                f"state {sid!r} must not be re-invoked on resume"
            )
        self.invocations.append(f"invoke:{sid}")
        return self._inner.invoke(prepared)

    def cancel(self, prepared: Any) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return self._inner.describe()


def _registry_with_model_adapter(
    adapter_factory: Any,
) -> ProfileRegistry:
    """Build a registry whose ``model`` backing returns the given
    instance every time. Other backings remain the slice-1 mocks so
    existing fixtures keep working.
    """
    reg = ProfileRegistry()
    for type_name in ("text", "json", "messages", "prompt", "schema", "document"):
        reg.register_artifact_type(type_name)
    from orchestra.adapters.mock_human import MockHumanAdapter as _Human
    from orchestra.adapters.mock_shell import MockShellAdapter as _Shell
    from orchestra.executor.parsers import identity_text_parser

    reg.register_actor_backing("model", adapter_factory)
    reg.register_actor_backing("human", _Human)
    reg.register_actor_backing("shell", _Shell)
    reg.register_result_parser(identity_text_parser)
    return reg


def _truncate_log_after_state_exit(
    log_path: Path, state_id: str
) -> None:
    """Truncate ``log_path`` to drop everything from the named
    state's ``state_exit`` record onward EXCEPT the ``state_exit``
    itself. The result mimics a crash in the small window between
    the state_exit fsync and the following transition write."""
    records = LogReader(log_path).read_all()
    truncate_at: int | None = None
    seen_state_exit = False
    for r in records:
        if r.event == "state_exit" and r.state_id == state_id:
            seen_state_exit = True
            continue
        if seen_state_exit and r.event == "transition":
            truncate_at = r.seq
            break
    assert truncate_at is not None, (
        f"expected a transition record after {state_id!r}'s state_exit"
    )
    keep = [r for r in records if r.seq < truncate_at]
    with open(log_path, "w", encoding="utf-8") as fh:
        for r in keep:
            fh.write(r.to_json() + "\n")


def _resume_run(
    *,
    run_dir: Path,
    workflow_path: Path,
    registry: ProfileRegistry,
    external_inputs: dict[str, Any],
) -> str:
    """Mirror cmd_resume's wiring: replay, hydrate, dispatch
    state_exit_without_transition / open_fan_out, run to terminal."""
    from orchestra.resume import replay_log, run_resume_hooks
    from orchestra.visibility import VisibilityIndex

    log_path = run_dir / "log.jsonl"
    replay = replay_log(str(log_path))
    workflow = load_workflow(workflow_path, registry)
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(
        log_path, replay.last_run_id, start_seq=replay.next_seq
    )
    run_resume_hooks(workflow, registry, replay, log)
    vi = VisibilityIndex(persist_path=run_dir / "visibility.json")
    vi.replace_from(replay.visibility_statuses)
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs=external_inputs,
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
        visibility_index=vi,
        last_transition_state=replay.last_transition_state,
        last_transition_outcome=replay.last_transition_outcome,
    )
    try:
        if (
            replay.state_exit_without_transition
            and replay.current_state is not None
            and replay.current_state not in {"done", "stop"}
        ):
            executor.resume_pending_transition(replay.current_state)
        if replay.open_fan_out is not None:
            of = replay.open_fan_out
            children_field = of.get("children") or []
            if not isinstance(children_field, list):
                children_field = []
            children_list = [str(c) for c in children_field]
            completed = {
                name: env
                for name, env in replay.envelopes.items()
                if (
                    name in children_list
                    and env.attempt == replay.attempts.get(name)
                )
            }
            executor.resume_fan_out(
                parent_state_name=str(of.get("parent_state", "")),
                children=children_list,
                join_target=str(of.get("join_target", "")),
                error_target=str(of.get("error_target", "")),
                completed_children=completed,
            )
        terminal = executor.run_to_completion()
    finally:
        log.close()
        store.close()
    return terminal


def _build_two_state_fixture(tmp_path: Path) -> Path:
    _write_dummy_template(tmp_path)
    src = tmp_path / "ab.orc"
    src.write_text(
        """spec 0.1
workflow ab_test
  external_input topic text
  max_total_steps 10
  model m_a
  model m_b
  artifact a_out text
  artifact b_out text
  role rA
    prompt template "templates/dummy.md" with topic
  role rB
    prompt template "templates/dummy.md" with topic
  state s_a
    actor model m_a
    role rA
    reads topic
    writes a_out text
    on complete => s_b
    on error => stop
    on timeout => stop
  state s_b
    actor model m_b
    role rB
    reads a_out
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    return src


def test_resume_two_state_crash_window_does_not_reexecute_second_state(
    tmp_path: Path,
) -> None:
    """A->B workflow crashed between B.state_exit and B.transition.
    The round-1 detection looked at last_target which was set by
    A.transition and never cleared, so state_exit_without_transition
    misfired and B was re-entered. With last_target cleared on
    state_enter, B is correctly recognized as the unfinished
    transition and resume_pending_transition handles it without
    re-running B's actor.
    """
    src = _build_two_state_fixture(tmp_path)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hi"},
    )
    executor.step()  # s_a runs to completion incl. transition.
    executor.step()  # s_b runs to completion incl. transition.
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    deny = _DenylistAdapter(denied={"s_b"})
    reg2 = _registry_with_model_adapter(lambda: deny)

    terminal = _resume_run(
        run_dir=run_dir,
        workflow_path=src,
        registry=reg2,
        external_inputs={"topic": "hi"},
    )
    assert terminal == "done"
    assert deny.invocations == [], (
        "no model state should run on resume; both s_a and s_b "
        "completed before the crash"
    )

    records = LogReader(run_dir / "log.jsonl").read_all()
    s_b_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "s_b"
    ]
    s_b_exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "s_b"
    ]
    s_b_transitions = [
        r for r in records if r.event == "transition" and r.state_id == "s_b"
    ]
    assert len(s_b_enters) == 1
    assert len(s_b_exits) == 1
    assert len(s_b_transitions) == 1
    assert s_b_transitions[0].fields["target"] == "done"


def _build_three_state_fixture(tmp_path: Path) -> Path:
    _write_dummy_template(tmp_path)
    src = tmp_path / "abc.orc"
    src.write_text(
        """spec 0.1
workflow abc_test
  external_input topic text
  max_total_steps 10
  model m_a
  model m_b
  model m_c
  artifact a_out text
  artifact b_out text
  artifact c_out text
  role rX
    prompt template "templates/dummy.md" with topic
  state s_a
    actor model m_a
    role rX
    reads topic
    writes a_out text
    on complete => s_b
    on error => stop
    on timeout => stop
  state s_b
    actor model m_b
    role rX
    reads a_out
    writes b_out text
    on complete => s_c
    on error => stop
    on timeout => stop
  state s_c
    actor model m_c
    role rX
    reads b_out
    writes c_out text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    return src


def test_resume_three_state_crash_window_does_not_reexecute_last_state(
    tmp_path: Path,
) -> None:
    """A->B->C with crash between C.state_exit and C.transition.
    Earlier states have durable transitions so any "did anything
    transition?" heuristic would mis-detect; only per-state
    tracking via last_target-cleared-on-state_enter is correct."""
    src = _build_three_state_fixture(tmp_path)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hi"},
    )
    executor.step()  # s_a
    executor.step()  # s_b
    executor.step()  # s_c
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_c")

    deny = _DenylistAdapter(denied={"s_a", "s_b", "s_c"})
    reg2 = _registry_with_model_adapter(lambda: deny)

    terminal = _resume_run(
        run_dir=run_dir,
        workflow_path=src,
        registry=reg2,
        external_inputs={"topic": "hi"},
    )
    assert terminal == "done"
    assert deny.invocations == []

    records = LogReader(run_dir / "log.jsonl").read_all()
    for sid in ("s_a", "s_b", "s_c"):
        enters = [
            r
            for r in records
            if r.event == "state_enter" and r.state_id == sid
        ]
        exits = [
            r
            for r in records
            if r.event == "state_exit" and r.state_id == sid
        ]
        assert len(enters) == 1, sid
        assert len(exits) == 1, sid
    s_c_transitions = [
        r for r in records if r.event == "transition" and r.state_id == "s_c"
    ]
    assert len(s_c_transitions) == 1
    assert s_c_transitions[0].fields["target"] == "done"


def _build_fan_out_parent_fixture(tmp_path: Path) -> Path:
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan_parent.orc"
    src.write_text(
        """spec 0.1
workflow fan_parent_test
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_b
  model m_join
  model m_abort
  artifact framing text
  artifact a_out text
  artifact b_out text
  artifact joined text
  artifact aborted text
  role rX
    prompt template "templates/dummy.md" with topic
  state launch
    actor model m_parent
    role rX
    reads topic
    writes framing text
    on complete fan_out [child_a, child_b] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state child_a
    actor model m_a
    role rX
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state child_b
    actor model m_b
    role rX
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role rX
    reads a_out, b_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role rX
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    return src


def test_resume_fan_out_parent_crash_window_does_not_reexecute_parent(
    tmp_path: Path,
) -> None:
    """Fan-out parent's state_exit is durable but neither
    fan_out_start nor the parent's transition was written. Resume
    must not re-run the parent's actor body. resume_pending_transition
    re-selects the fan-out transition and dispatches the fan-out
    group from scratch.
    """
    src = _build_fan_out_parent_fixture(tmp_path)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hi"},
    )
    # Run launch's per-state body but not its fan-out execution.
    # We do this by writing only launch's records up through
    # state_exit, then truncating the log so neither fan_out_start
    # nor launch's transition is durable. The cleanest way is to
    # let executor.step() run the linear path which will normally
    # invoke fan-out, but we instead rebuild the log carefully.
    # Approach: run step() (which runs the full launch state
    # including fan-out). Then truncate the log so only launch's
    # state_enter through state_exit survive.
    executor.step()
    log.close()
    store.close()

    # Truncate everything from launch's state_exit onward except
    # the state_exit itself.
    log_path = run_dir / "log.jsonl"
    records = LogReader(log_path).read_all()
    truncate_at: int | None = None
    seen_state_exit = False
    for r in records:
        if r.event == "state_exit" and r.state_id == "launch":
            seen_state_exit = True
            continue
        if seen_state_exit:
            truncate_at = r.seq
            break
    assert truncate_at is not None
    keep = [r for r in records if r.seq < truncate_at]
    with open(log_path, "w", encoding="utf-8") as fh:
        for r in keep:
            fh.write(r.to_json() + "\n")

    deny = _DenylistAdapter(denied={"launch"})
    reg2 = _registry_with_model_adapter(lambda: deny)

    terminal = _resume_run(
        run_dir=run_dir,
        workflow_path=src,
        registry=reg2,
        external_inputs={"topic": "hi"},
    )
    assert terminal == "done"

    records2 = LogReader(log_path).read_all()
    launch_enters = [
        r for r in records2 if r.event == "state_enter" and r.state_id == "launch"
    ]
    launch_exits = [
        r for r in records2 if r.event == "state_exit" and r.state_id == "launch"
    ]
    assert len(launch_enters) == 1, "launch must not be re-entered"
    assert len(launch_exits) == 1, "only the original state_exit"
    fan_starts = [r for r in records2 if r.event == "fan_out_start"]
    fan_ends = [r for r in records2 if r.event == "fan_out_end"]
    assert len(fan_starts) == 1
    assert len(fan_ends) == 1
    assert fan_ends[0].fields["aggregate"] == "success"


def _build_payload_guard_fixture(tmp_path: Path) -> Path:
    _write_dummy_template(tmp_path)
    src = tmp_path / "payload_guard.orc"
    src.write_text(
        """spec 0.1
workflow payload_guard_test
  external_input topic text
  max_total_steps 10
  model m_a
  model m_high
  model m_low
  artifact a_out text
  artifact high_out text
  artifact low_out text
  role rX
    prompt template "templates/dummy.md" with topic
  state s_a
    actor model m_a
    role rX
    reads topic
    writes a_out text
    on complete when s_a.payload.tokens_out > 5 => s_high
    on complete => s_low
    on error => stop
    on timeout => stop
  state s_high
    actor model m_high
    role rX
    reads a_out
    writes high_out text
    on complete => done
    on error => stop
    on timeout => stop
  state s_low
    actor model m_low
    role rX
    reads a_out
    writes low_out text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    return src


def test_resume_with_payload_guard_picks_correct_branch(
    tmp_path: Path,
) -> None:
    """A guard reads ``s_a.payload.tokens_out``. The mock model
    returns tokens_out > 5, so the live path routes to ``s_high``.
    Resume must hydrate the envelope's payload from its durable
    file so the guard evaluates True and picks the same branch.

    Without payload hydration the guard walks an empty payload dict,
    raises KeyError, and resume_pending_transition propagates the
    failure: this regression test fails under the unfixed code.
    """
    src = _build_payload_guard_fixture(tmp_path)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hi"},
    )
    executor.step()  # s_a
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_a")

    deny = _DenylistAdapter(denied={"s_a", "s_low"})
    reg2 = _registry_with_model_adapter(lambda: deny)

    terminal = _resume_run(
        run_dir=run_dir,
        workflow_path=src,
        registry=reg2,
        external_inputs={"topic": "hi"},
    )
    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    s_a_transitions = [
        r for r in records if r.event == "transition" and r.state_id == "s_a"
    ]
    assert len(s_a_transitions) == 1
    assert s_a_transitions[0].fields["target"] == "s_high", (
        "the guard reads s_a.payload.tokens_out; the live path picks "
        "s_high because tokens_out > 5. Resume must select the same "
        "branch, which requires hydrating the envelope's payload from "
        "its durable file."
    )
    s_high_exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "s_high"
    ]
    assert len(s_high_exits) == 1
    s_low_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "s_low"
    ]
    assert len(s_low_enters) == 0, "s_low must not have been entered"


# --------------------------------------------------------------------
# Pass-2 fix #1: refuse resume when commit landed without state_exit
# --------------------------------------------------------------------


def test_cmd_resume_refuses_agent_state_with_committed_artifacts_no_state_exit(
    tmp_path: Path,
) -> None:
    """If an agent state's commit_tentative ran but the process died
    before state_exit was written, the artifact version is durable in
    the store and the log shows artifact_write records for the state
    with no matching state_exit. Re-entering the state would invoke
    the agent a second time and re-mutate the workspace. cmd_resume
    must refuse with a targeted error."""
    import argparse

    _write_dummy_template(tmp_path)
    src = tmp_path / "agent.orc"
    src.write_text(
        """spec 0.1
workflow ag
  external_input topic text
  max_total_steps 5
  model m_help
  agent worker
    model m_help
    adapter claude_code_agent
    context_policy fresh
  artifact reply text
  role r
    prompt template "templates/dummy.md"
  state edit
    actor agent worker
    role r
    reads topic
    writes reply text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    log_path = run_dir / "log.jsonl"
    with open(log_path, "w", encoding="utf-8") as fh:
        records = [
            {"ts": "2026-05-03T00:00:00.000Z", "run_id": run_id, "seq": 0,
             "event": "run_start", "state_id": None, "attempt": None,
             "workflow_path": str(src), "external_inputs": {"topic": "x"}},
            {"ts": "2026-05-03T00:00:01.000Z", "run_id": run_id, "seq": 1,
             "event": "state_enter", "state_id": "edit", "attempt": 1,
             "attempts": {"edit": 1}, "retries": {"edit": 0},
             "invocation_id": f"{run_id}::edit::1"},
            {"ts": "2026-05-03T00:00:02.000Z", "run_id": run_id, "seq": 2,
             "event": "actor_prepare", "state_id": "edit", "attempt": 1},
            {"ts": "2026-05-03T00:00:03.000Z", "run_id": run_id, "seq": 3,
             "event": "actor_invoke_start", "state_id": "edit", "attempt": 1},
            {"ts": "2026-05-03T00:00:04.000Z", "run_id": run_id, "seq": 4,
             "event": "actor_invoke_end", "state_id": "edit", "attempt": 1,
             "payload_ref": None, "duration_ms": 0, "summary": {}},
            {"ts": "2026-05-03T00:00:05.000Z", "run_id": run_id, "seq": 5,
             "event": "artifact_write", "state_id": "edit", "attempt": 1,
             "artifact": "reply", "version_id": "v1",
             "invocation_id": f"{run_id}::edit::1"},
        ]
        import json as _json
        for r in records:
            fh.write(_json.dumps(r, sort_keys=True) + "\n")

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli
    rc = cli.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_refuses_agent_state_with_store_commit_no_log_entry(
    tmp_path: Path,
) -> None:
    """Pass-3 fix #2: a crash in the window between commit_tentative
    and the first artifact_write log call leaves the store with a
    durable version tagged with the invocation_id while the log
    shows no artifact_write at all. The pass-2 refusal logic only
    consulted the log, so this window slipped past the gate. The
    pass-3 refusal queries the store keyed by invocation_id; finding
    a committed version there is sufficient to refuse, regardless of
    whether the log mentions it."""
    import argparse

    _write_dummy_template(tmp_path)
    src = tmp_path / "agent.orc"
    src.write_text(
        """spec 0.1
workflow ag
  external_input topic text
  max_total_steps 5
  model m_help
  agent worker
    model m_help
    adapter claude_code_agent
    context_policy fresh
  artifact reply text
  role r
    prompt template "templates/dummy.md"
  state edit
    actor agent worker
    role r
    reads topic
    writes reply text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    # Log shows state_enter but NO artifact_write. The crash landed
    # between commit_tentative and the artifact_write log call.
    log_path = run_dir / "log.jsonl"
    with open(log_path, "w", encoding="utf-8") as fh:
        records = [
            {"ts": "2026-05-03T00:00:00.000Z", "run_id": run_id, "seq": 0,
             "event": "run_start", "state_id": None, "attempt": None,
             "workflow_path": str(src), "external_inputs": {"topic": "x"}},
            {"ts": "2026-05-03T00:00:01.000Z", "run_id": run_id, "seq": 1,
             "event": "state_enter", "state_id": "edit", "attempt": 1,
             "attempts": {"edit": 1}, "retries": {"edit": 0},
             "invocation_id": f"{run_id}::edit::1"},
            {"ts": "2026-05-03T00:00:02.000Z", "run_id": run_id, "seq": 2,
             "event": "actor_prepare", "state_id": "edit", "attempt": 1},
            {"ts": "2026-05-03T00:00:03.000Z", "run_id": run_id, "seq": 3,
             "event": "actor_invoke_start", "state_id": "edit", "attempt": 1},
            {"ts": "2026-05-03T00:00:04.000Z", "run_id": run_id, "seq": 4,
             "event": "actor_invoke_end", "state_id": "edit", "attempt": 1,
             "payload_ref": None, "duration_ms": 0, "summary": {}},
        ]
        import json as _json
        for r in records:
            fh.write(_json.dumps(r, sort_keys=True) + "\n")

    # Store has a durable committed version tagged with the
    # invocation_id (the commit_tentative ran but the log-write that
    # references it never landed). Construct one with the same
    # tentative-write/commit dance the executor uses.
    invocation_id = f"{run_id}::edit::1"
    store = ArtifactStore(run_dir / "store.sqlite")
    store.declare("reply", "text")
    handle = store.tentative_write(
        "reply", "draft body", written_by="edit#1",
        invocation_id=invocation_id,
    )
    store.commit_tentative([handle])
    # Sanity: the store reports the version under the invocation id.
    orphans = store.list_committed_by_invocation(invocation_id)
    assert len(orphans) == 1
    assert orphans[0].name == "reply"
    store.close()

    # Confirm replay observed no artifact_write (the pre-fix signal).
    rep = replay_log(str(log_path))
    assert rep.committed_without_exit == set(), (
        "synthetic log has no artifact_write records, so the pass-2 "
        "log-only refusal would have missed this window"
    )

    # cmd_resume must refuse via the store-side query.
    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli
    rc = cli.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_uses_snapshot_when_template_edited_after_run_start(
    tmp_path: Path,
) -> None:
    """Pass-5 redesign: editing a prompt template between crash and
    resume no longer reaches the actor. The snapshot captured at
    run_start is what the resumed workflow opens, not the live
    declared path. The pass-4 manifest gate would have refused with
    exit 2; the pass-5 snapshot accepts the resume because the
    original bytes are still pinned in the run directory."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    template = tmp_path / "templates" / "dummy.md"
    assert template.is_file()

    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)

    from orchestra.prompt_snapshot import snapshot_prompt_sources
    workflow, snapshot_manifest = snapshot_prompt_sources(workflow, run_dir)

    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src.resolve()),
            "workflow_digest": cli._workflow_digest(src),
            "prompt_snapshot_manifest": snapshot_manifest,
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()
    executor.step()
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    # Edit the LIVE template. The snapshot in run_dir is unchanged.
    template.write_text(template.read_text(encoding="utf-8") + "\nedited\n")

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    # Resume completes successfully because it reads the snapshot,
    # not the edited live file. The pass-4 gate would have returned 2.
    assert rc == 0


def test_cmd_resume_refuses_when_snapshot_file_mutated(tmp_path: Path) -> None:
    """Mid-run mutation of a snapshot file is a hard refusal. The
    snapshot is the run's read-only input set; if its bytes change,
    the original input is no longer recoverable and the resumed
    actor would see different data than the original run."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)

    from orchestra.prompt_snapshot import snapshot_prompt_sources
    workflow, snapshot_manifest = snapshot_prompt_sources(workflow, run_dir)

    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src.resolve()),
            "workflow_digest": cli._workflow_digest(src),
            "prompt_snapshot_manifest": snapshot_manifest,
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()
    executor.step()
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    # Mutate the snapshot file directly. This is the "buggy adapter
    # wrote outside its sandbox" or "user manually edited" case;
    # resume must refuse because the original bytes are gone.
    snap_path = Path(snapshot_manifest[0]["snapshot_path"])
    snap_path.write_text("MUTATED MID-RUN\n")

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_refuses_when_workflow_file_changed(tmp_path: Path) -> None:
    """Pass-3 fix #3: a .orc file modified between the original run
    and resume can route the next transition to a target the original
    workflow never named. cmd_resume now records a sha256 of the
    workflow bytes at run_start and refuses on mismatch."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src),
            "workflow_digest": cli._workflow_digest(src),
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()  # s_a runs to completion incl. transition.
    executor.step()  # s_b runs to completion incl. transition.
    log.close()
    store.close()

    # Truncate after s_b's state_exit so resume would otherwise need
    # to re-select the missing transition.
    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    # Modify the workflow file so its digest changes. The semantic
    # change matters but the digest catches any byte change.
    src.write_text(src.read_text(encoding="utf-8") + "\n# drifted\n")

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_refuses_when_prompt_template_changed(tmp_path: Path) -> None:
    """Pass-4 fix #2: editing a file-backed prompt template between
    crash and resume changes what the actor sees while the .orc file
    digest still matches. cmd_resume must consult the prompt manifest
    and refuse on mismatch."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    template = tmp_path / "templates" / "dummy.md"
    assert template.is_file()

    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    from helpers.legacy_prompt_manifest import compute_prompt_manifest
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src),
            "workflow_digest": cli._workflow_digest(src),
            "prompt_manifest": compute_prompt_manifest(workflow),
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()
    executor.step()
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    # The .orc itself is unchanged, but the prompt template the role
    # reads is edited. Pre-fix the workflow_digest gate would not
    # catch this; the manifest gate must.
    template.write_text(template.read_text(encoding="utf-8") + "\nedited\n")

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_refuses_when_prompt_template_removed(tmp_path: Path) -> None:
    """Removing a file-backed prompt template altogether is the same
    class of failure as editing it. The manifest entry would mismatch
    on `<missing>` and resume must refuse."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    template = tmp_path / "templates" / "dummy.md"
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    from helpers.legacy_prompt_manifest import compute_prompt_manifest
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src),
            "workflow_digest": cli._workflow_digest(src),
            "prompt_manifest": compute_prompt_manifest(workflow),
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()
    executor.step()
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")
    template.unlink()

    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    assert rc == 2


def test_cmd_resume_accepts_unchanged_workflow_after_run_start_digest(
    tmp_path: Path,
) -> None:
    """Pin the happy path: a recorded digest that matches the current
    file's digest must not block resume."""
    import argparse

    src = _build_two_state_fixture(tmp_path)
    run_id = new_run_id()
    data_root = tmp_path / "runs"
    run_dir = data_root / run_id
    run_dir.mkdir(parents=True)

    reg = with_core()
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(src),
            "workflow_digest": cli._workflow_digest(src),
            "workflow_name": workflow.name,
            "spec_version": workflow.spec_version,
            "external_inputs": {"topic": "x"},
            "max_total_steps": workflow.max_total_steps,
        },
    )
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    executor.step()
    executor.step()
    log.close()
    store.close()

    _truncate_log_after_state_exit(run_dir / "log.jsonl", "s_b")

    # File unchanged. Resume must NOT refuse because of digest.
    args = argparse.Namespace(
        run_id=run_id,
        data_root=str(data_root),
    )
    from orchestra import cli as cli_mod
    rc = cli_mod.cmd_resume(args)
    # Resume should reach terminal "done" via the resume path; rc 0
    # means successful end. (A non-zero rc here would indicate digest
    # blocked resumption when it should not have.)
    assert rc == 0


# --------------------------------------------------------------------
# Pass-2 fix #2: retry counter survives a crash after retry transition
# --------------------------------------------------------------------


def _build_retry_fixture(tmp_path: Path) -> Path:
    """``edit`` retries up to once on error then stops. The retry
    transition routes back to ``edit`` so a successful retry
    increments retries[edit] from 0 to 1."""
    _write_dummy_template(tmp_path)
    src = tmp_path / "retry.orc"
    src.write_text(
        """spec 0.1
workflow retry_test
  external_input topic text
  max_total_steps 10
  model m_e
  artifact reply text
  role r
    prompt template "templates/dummy.md"
  state edit
    actor model m_e
    role r
    reads topic
    writes reply text
    on complete => done
    on error retry max 1 then stop
    on timeout => stop
"""
    )
    return src


def test_resume_after_retry_transition_does_not_reset_retries(
    tmp_path: Path,
) -> None:
    """Workflow with `on error retry max 1 => edit`. First attempt
    errors so the executor takes the retry branch and writes a
    transition record back to ``edit`` with outcome=error. Crash
    immediately after that transition. On resume, the second
    invocation must count as the retry (retries[edit]=1, attempts
    [edit]=2). Without the fix, replay restores attempts/retries from
    the last state_enter (which had retries=0), the executor's in-memory
    _last_state/_last_outcome reset to None, and the next entry
    treats this as a fresh entry that resets retries to 0 instead of
    incrementing it. The retry budget effectively doubles.

    Verified pre-fix this test fails with retries[edit]=0 after the
    second entry. Post-fix it shows retries[edit]=1.
    """
    src = _build_retry_fixture(tmp_path)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    # First run: fail on the first invoke, succeed on the second so
    # the workflow reaches done. Then we will rewind to the durable
    # transition and resume.
    from orchestra.adapters.mock_model import MockModelAdapter

    invoke_count = {"n": 0}

    class _Flaky:
        def __init__(self) -> None:
            self._inner = MockModelAdapter()

        def prepare(self, request: Any) -> Any:
            return self._inner.prepare(request)

        def invoke(self, prepared: Any) -> Any:
            invoke_count["n"] += 1
            if invoke_count["n"] == 1:
                raise RuntimeError("synthetic first-attempt failure")
            return self._inner.invoke(prepared)

        def cancel(self, prepared: Any) -> None:
            return None

        def describe(self) -> dict[str, Any]:
            return self._inner.describe()

    reg = _registry_with_model_adapter(lambda: _Flaky())
    workflow = load_workflow(src, reg)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )
    terminal = executor.run_to_completion()
    log.close()
    store.close()
    assert terminal == "done"

    # Truncate the log right after the durable retry transition. We
    # keep records up to and including the first transition record
    # (which routed back to edit on error) and drop everything after.
    records = LogReader(run_dir / "log.jsonl").read_all()
    keep_until: int | None = None
    for r in records:
        if (
            r.event == "transition"
            and r.state_id == "edit"
            and r.fields.get("target") == "edit"
        ):
            keep_until = r.seq
            break
    assert keep_until is not None, (
        "expected a durable retry transition that targets 'edit'"
    )
    truncated = [r for r in records if r.seq <= keep_until]
    with open(run_dir / "log.jsonl", "w", encoding="utf-8") as fh:
        for r in truncated:
            fh.write(r.to_json() + "\n")

    # Resume. The second-attempt invoke happens here; the post-resume
    # snapshot should show attempts[edit]=2 and retries[edit]=1.
    invoke_count["n"] = 1  # already failed once before truncation
    reg2 = _registry_with_model_adapter(lambda: _Flaky())
    terminal2 = _resume_run(
        run_dir=run_dir,
        workflow_path=src,
        registry=reg2,
        external_inputs={"topic": "x"},
    )
    assert terminal2 == "done"

    # Inspect the resumed log: the second state_enter for ``edit``
    # must have attempts[edit]=2 and retries[edit]=1. Pre-fix
    # retries[edit] would be 0 here.
    records = LogReader(run_dir / "log.jsonl").read_all()
    edit_enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "edit"
    ]
    assert len(edit_enters) >= 2
    second_enter = edit_enters[1]
    assert second_enter.attempt == 2
    assert second_enter.fields["attempts"]["edit"] == 2
    assert second_enter.fields["retries"]["edit"] == 1, (
        "retries[edit] must be 1 on the second entry; pre-fix it "
        "would be 0 because _last_state/_last_outcome were not "
        "reconstructed from the log"
    )
