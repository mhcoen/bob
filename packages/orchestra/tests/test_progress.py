"""Tests for the per-state progress reporter.

Covers the format string, the executor's callback hook firing once
per state_enter and once per state_exit, the api layer's wrapper
that enriches role into adapter and model from the resolved role
bindings, and the CLI's progress callback wiring.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import _pre_load_registry, _wrap_progress_callback
from orchestra.config import OrchestraConfig, RoleBinding, WorkflowConfig
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogWriter
from orchestra.progress import (
    ProgressEvent,
    format_event,
    silent_reporter,
    stderr_reporter,
)
from orchestra.spine import (
    NO_INITIAL,
    InvocationRequest,
    PreparedInvocation,
    Workflow,
)
from orchestra.store import ArtifactStore

# --------------------------------------------------------------------
# Format string
# --------------------------------------------------------------------


def test_format_event_state_enter() -> None:
    event = ProgressEvent(
        kind="state_enter",
        state_name="contrarian_advise",
        role="contrarian",
        adapter="claude_code_text",
        model="kimi-k2.6",
        index=2,
        total=7,
        elapsed_seconds=None,
    )
    assert (
        format_event(event)
        == "[2/7] contrarian (claude_code_text:kimi-k2.6) ... starting"
    )


def test_format_event_state_exit_includes_elapsed() -> None:
    event = ProgressEvent(
        kind="state_exit",
        state_name="contrarian_advise",
        role="contrarian",
        adapter="claude_code_text",
        model="kimi-k2.6",
        index=2,
        total=7,
        elapsed_seconds=4.825,
    )
    assert (
        format_event(event)
        == "[2/7] contrarian (claude_code_text:kimi-k2.6) ... done in 4.8s"
    )


def test_format_event_falls_back_to_state_name_when_role_missing() -> None:
    event = ProgressEvent(
        kind="state_enter",
        state_name="anonymize",
        role=None,
        adapter=None,
        model=None,
        index=7,
        total=13,
        elapsed_seconds=None,
    )
    rendered = format_event(event)
    assert "anonymize" in rendered
    assert "[7/13]" in rendered
    assert "starting" in rendered


def test_format_event_state_exit_with_no_elapsed() -> None:
    """A state_exit event with no elapsed time still renders cleanly."""
    event = ProgressEvent(
        kind="state_exit",
        state_name="x",
        role="x",
        adapter="a",
        model="m",
        index=1,
        total=1,
        elapsed_seconds=None,
    )
    assert format_event(event) == "[1/1] x (a:m) ... done"


# --------------------------------------------------------------------
# stderr_reporter and silent_reporter
# --------------------------------------------------------------------


def test_stderr_reporter_writes_one_line_per_event() -> None:
    buf = io.StringIO()
    reporter = stderr_reporter(stream=buf)
    reporter(
        ProgressEvent(
            kind="state_enter",
            state_name="frame",
            role="framer",
            adapter="claude_code_text",
            model="opus",
            index=1,
            total=7,
            elapsed_seconds=None,
        )
    )
    reporter(
        ProgressEvent(
            kind="state_exit",
            state_name="frame",
            role="framer",
            adapter="claude_code_text",
            model="opus",
            index=1,
            total=7,
            elapsed_seconds=2.5,
        )
    )
    lines = buf.getvalue().splitlines()
    assert lines == [
        "[1/7] framer (claude_code_text:opus) ... starting",
        "[1/7] framer (claude_code_text:opus) ... done in 2.5s",
    ]


def test_silent_reporter_drops_every_event() -> None:
    reporter = silent_reporter()
    # Should not raise, should produce no observable side effect.
    reporter(
        ProgressEvent(
            kind="state_enter",
            state_name="x",
            role="x",
            adapter="a",
            model="m",
            index=1,
            total=1,
            elapsed_seconds=None,
        )
    )


# --------------------------------------------------------------------
# api wrapper enriches role into adapter and model
# --------------------------------------------------------------------


def test_wrap_progress_callback_enriches_with_adapter_and_model() -> None:
    """The api-layer wrapper looks up the role binding to resolve
    adapter and model. The executor only knows the role name; the
    api owns the binding map."""
    bindings = {
        "framer": RoleBinding(adapter="claude_code_text", model="opus"),
    }
    received: list[ProgressEvent] = []

    def _user_cb(event: ProgressEvent) -> None:
        received.append(event)

    inner = _wrap_progress_callback(_user_cb, bindings)
    assert inner is not None
    inner("state_enter", "frame", "framer", 1, 7, None)
    inner("state_exit", "frame", "framer", 1, 7, 3.0)

    assert len(received) == 2
    enter, exit_ = received
    assert enter.adapter == "claude_code_text"
    assert enter.model == "opus"
    assert enter.role == "framer"
    assert enter.kind == "state_enter"
    assert exit_.elapsed_seconds == 3.0


def test_wrap_progress_callback_handles_unbound_role() -> None:
    """A state with a role that has no binding (or with no role at
    all) still surfaces an event. The adapter and model fields are
    None in that case."""
    received: list[ProgressEvent] = []
    inner = _wrap_progress_callback(lambda e: received.append(e), {})
    assert inner is not None
    inner("state_enter", "anonymize", None, 7, 13, None)
    assert received[0].adapter is None
    assert received[0].model is None
    assert received[0].role is None


def test_wrap_progress_callback_returns_none_when_user_callback_is_none() -> None:
    """The executor's no-op fast path requires the wrapper to return
    None when there is nothing to enrich."""
    assert _wrap_progress_callback(None, {}) is None


def test_wrap_progress_callback_swallows_user_exceptions() -> None:
    """A reporter that throws must not abort an in-flight run."""

    def _angry(_event: ProgressEvent) -> None:
        raise RuntimeError("boom")

    inner = _wrap_progress_callback(_angry, {})
    assert inner is not None
    # Should not raise.
    inner("state_enter", "x", None, 1, 1, None)


# --------------------------------------------------------------------
# Executor fires the callback once per state_enter and once per
# state_exit. End-to-end through the ask_council workflow under the
# recording-adapter harness.
# --------------------------------------------------------------------


class _RecordingModelAdapter:
    backing = "model"

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = dict(responses)

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        text = self._responses[prepared.inner["state_id"]]
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
        return {"backing": "model", "kind": "recording_mock"}


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


_COUNCIL_RESPONSES = {
    "frame": "FRAMED",
    "contrarian_advise": "ALPHA",
    "first_principles_advise": "BETA",
    "expansionist_advise": "GAMMA",
    "outsider_advise": "DELTA",
    "executor_lens_advise": "EPSILON",
    "synthesize": "VERDICT",
}


def test_executor_fires_callback_once_per_state_enter_and_exit(
    tmp_path: Path,
) -> None:
    """End-to-end: ask_council declares seven states. The executor
    must fire the callback once per state_enter (seven times) and once
    per state_exit (seven times)."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)

    events: list[tuple[str, str, str | None, int, int, float | None]] = []

    def _cb(
        kind: str,
        state_name: str,
        role: str | None,
        index: int,
        total: int,
        elapsed: float | None,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        events.append((kind, state_name, role, index, total, elapsed))

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
        external_inputs={"query": "q", "history": "h"},
        progress_callback=_cb,
    )
    terminal = executor.run_to_completion()
    log.close()
    store.close()
    assert terminal == "done"

    enters = [e for e in events if e[0] == "state_enter"]
    exits = [e for e in events if e[0] == "state_exit"]
    assert len(enters) == 7
    assert len(exits) == 7

    # Index numbering: each state's first state_enter assigns its
    # 1-based index; state_exit reuses the same index. Total is the
    # workflow's declared state count (7 for ask_council).
    for kind, _name, _role, index, total, _elapsed in events:
        assert 1 <= index <= 7
        assert total == 7
        if kind == "state_exit":
            assert isinstance(events[0][5], type(None))  # enters carry no elapsed
            # exits carry float elapsed
            elapsed = next(
                e[5] for e in events if e[0] == "state_exit" and e[3] == index
            )
            assert isinstance(elapsed, float) and elapsed >= 0.0


def test_executor_state_enter_carries_role_for_role_bound_states(
    tmp_path: Path,
) -> None:
    """Every model state in ask_council declares a role. The
    executor surfaces that role through the callback so the api
    wrapper can resolve the binding."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)

    enters: list[tuple[str, str | None]] = []

    def _cb(
        kind: str,
        state_name: str,
        role: str | None,
        index: int,
        total: int,
        elapsed: float | None,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        if kind == "state_enter":
            enters.append((state_name, role))

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
        external_inputs={"query": "q", "history": "h"},
        progress_callback=_cb,
    )
    executor.run_to_completion()
    log.close()
    store.close()

    by_state = dict(enters)
    assert by_state["frame"] == "framer"
    assert by_state["contrarian_advise"] == "contrarian"
    assert by_state["synthesize"] == "chairman"


def test_executor_no_callback_is_a_no_op(tmp_path: Path) -> None:
    """Omitting progress_callback must not change executor behavior."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
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
        external_inputs={"query": "q", "history": "h"},
        # progress_callback omitted (defaults to None)
    )
    terminal = executor.run_to_completion()
    log.close()
    store.close()
    assert terminal == "done"


# --------------------------------------------------------------------
# CLI progress callback wiring
# --------------------------------------------------------------------


def test_cli_default_installs_stderr_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI installs a stderr reporter for every dispatch."""
    from orchestra import cli
    from orchestra.progress import ProgressEvent as PE

    fake_home = tmp_path / "home"
    (fake_home / ".orchestra").mkdir(parents=True)
    (fake_home / ".orchestra" / "config.json").write_text(
        '{"verbs": {"ask": {"workflow": "ask_single"}}, '
        '"roles": {"responder": {"adapter": "claude_code_text", "model": "opus"}}, '
        '"workflows": {"ask_single": {"pattern": "ask_single"}}}'
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    received_callback: list[Any] = []

    def _stub_run_verb(
        verb: str, query: str, config: Any, *, progress_callback: Any = None,
        **_kwargs: Any,
    ) -> str:
        received_callback.append(progress_callback)
        # Drive an event through the callback so capsys can see the
        # stderr write.
        if progress_callback is not None:
            progress_callback(
                PE(
                    kind="state_enter",
                    state_name="frame",
                    role="responder",
                    adapter="claude_code_text",
                    model="opus",
                    index=1,
                    total=1,
                    elapsed_seconds=None,
                )
            )
        return "answer"

    monkeypatch.setattr(cli, "run_verb", _stub_run_verb)

    rc = cli.main(["ask", "hello"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "[1/1] responder (claude_code_text:opus) ... starting" in err


# --------------------------------------------------------------------
# ChildBinding plus parallel-block format
# --------------------------------------------------------------------


def test_wrap_progress_callback_enriches_fan_out_children() -> None:
    """A fan_out_start event arrives at the wrapper with raw
    (state_name, role) pairs. The wrapper looks up each child's role
    in role_bindings and surfaces a ProgressEvent whose children
    field is a tuple of fully populated ChildBinding records."""
    from orchestra.progress import ChildBinding as CB

    bindings = {
        "framer": RoleBinding(adapter="claude_code_text", model="opus"),
        "contrarian": RoleBinding(adapter="claude_code_text", model="kimi-k2.6"),
        "first_principles": RoleBinding(
            adapter="claude_code_text", model="opus"
        ),
    }
    received: list[ProgressEvent] = []
    inner = _wrap_progress_callback(lambda e: received.append(e), bindings)
    assert inner is not None
    inner(
        "fan_out_start",
        "frame",
        "framer",
        2,
        7,
        None,
        (("contrarian_advise", "contrarian"), ("first_principles_advise", "first_principles")),
    )
    assert len(received) == 1
    event = received[0]
    assert event.kind == "fan_out_start"
    assert event.children is not None
    assert event.children == (
        CB(
            state_name="contrarian_advise",
            role="contrarian",
            adapter="claude_code_text",
            model="kimi-k2.6",
        ),
        CB(
            state_name="first_principles_advise",
            role="first_principles",
            adapter="claude_code_text",
            model="opus",
        ),
    )


def _emit(reporter: Any, kind: str, **kwargs: Any) -> None:
    """Helper: build a ProgressEvent and dispatch to the reporter."""
    defaults: dict[str, Any] = {
        "state_name": kwargs.get("state_name", "x"),
        "role": kwargs.get("role"),
        "adapter": kwargs.get("adapter"),
        "model": kwargs.get("model"),
        "index": kwargs.get("index", 1),
        "total": kwargs.get("total", 1),
        "elapsed_seconds": kwargs.get("elapsed_seconds"),
        "children": kwargs.get("children"),
    }
    reporter(ProgressEvent(kind=kind, **defaults))


def test_stateful_reporter_renders_parallel_block() -> None:
    """A fan_out_start, three child state_exits in completion order,
    and a fan_out_end render the spec'd parallel format. The wall-
    clock summary equals the longest individual elapsed value."""
    from orchestra.progress import ChildBinding as CB

    buf = io.StringIO()
    reporter = stderr_reporter(stream=buf)
    children = (
        CB(state_name="contrarian_advise", role="contrarian", adapter="a", model="m1"),
        CB(state_name="first_principles_advise", role="first_principles", adapter="a", model="m2"),
        CB(state_name="executor_lens_advise", role="executor_lens", adapter="a", model="m3"),
    )
    _emit(
        reporter,
        "fan_out_start",
        state_name="frame",
        role="framer",
        index=2,
        total=7,
        children=children,
    )
    # Children complete in non-start order with varied durations.
    _emit(
        reporter,
        "state_exit",
        state_name="contrarian_advise",
        role="contrarian",
        adapter="a",
        model="m1",
        index=2,
        total=7,
        elapsed_seconds=4.1,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="executor_lens_advise",
        role="executor_lens",
        adapter="a",
        model="m3",
        index=4,
        total=7,
        elapsed_seconds=6.3,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="first_principles_advise",
        role="first_principles",
        adapter="a",
        model="m2",
        index=3,
        total=7,
        elapsed_seconds=5.5,
    )
    _emit(
        reporter,
        "fan_out_end",
        state_name="frame",
        role="framer",
        index=2,
        total=7,
    )
    lines = buf.getvalue().splitlines()
    assert lines == [
        "[2-4/7] 3 framer children starting in parallel:",
        "   contrarian (a:m1)",
        "   first_principles (a:m2)",
        "   executor_lens (a:m3)",
        "[2-4/7] contrarian done in 4.1s",
        "[2-4/7] executor_lens done in 6.3s",
        "[2-4/7] first_principles done in 5.5s",
        "[2-4/7] all 3 done, parallel wall-clock 6.3s",
    ]


def test_stateful_reporter_suppresses_per_child_starting_lines() -> None:
    """Inside an open parallel block, per-child state_enter events
    are dropped. The fan_out_start header already lists every child;
    re-printing each child's "starting" line would be noise."""
    from orchestra.progress import ChildBinding as CB

    buf = io.StringIO()
    reporter = stderr_reporter(stream=buf)
    children = (
        CB(state_name="c1", role="r1", adapter="a", model="m"),
        CB(state_name="c2", role="r2", adapter="a", model="m"),
    )
    _emit(
        reporter,
        "fan_out_start",
        state_name="parent",
        role="parent_role",
        index=1,
        total=4,
        children=children,
    )
    # Per-child state_enter events that arrive while the block is
    # open must NOT add lines.
    _emit(reporter, "state_enter", state_name="c1", role="r1", adapter="a", model="m", index=1, total=4)
    _emit(reporter, "state_enter", state_name="c2", role="r2", adapter="a", model="m", index=2, total=4)
    _emit(
        reporter,
        "state_exit",
        state_name="c1",
        role="r1",
        adapter="a",
        model="m",
        index=1,
        total=4,
        elapsed_seconds=1.0,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="c2",
        role="r2",
        adapter="a",
        model="m",
        index=2,
        total=4,
        elapsed_seconds=2.0,
    )
    _emit(reporter, "fan_out_end", state_name="parent", role="parent_role", index=1, total=4)

    lines = buf.getvalue().splitlines()
    # Two starting events suppressed -> no per-child "... starting"
    # lines in the output.
    assert not any("... starting" in line for line in lines)
    # The summary's wall-clock is max(1.0, 2.0) = 2.0, not the sum.
    assert lines[-1] == "[1-2/4] all 2 done, parallel wall-clock 2.0s"


def test_stateful_reporter_resumes_sequential_after_parallel_block() -> None:
    """Sequential states before and after a parallel block use the
    normal [N/total] counter format. The chairman state below is
    sequential; its line must not carry the range counter."""
    from orchestra.progress import ChildBinding as CB

    buf = io.StringIO()
    reporter = stderr_reporter(stream=buf)
    # Sequential framer.
    _emit(
        reporter,
        "state_enter",
        state_name="frame",
        role="framer",
        adapter="claude_code_text",
        model="opus",
        index=1,
        total=7,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="frame",
        role="framer",
        adapter="claude_code_text",
        model="opus",
        index=1,
        total=7,
        elapsed_seconds=3.2,
    )
    # Parallel block.
    children = (
        CB(state_name="c1", role="r1", adapter="a", model="m"),
        CB(state_name="c2", role="r2", adapter="a", model="m"),
    )
    _emit(reporter, "fan_out_start", state_name="frame", role="framer", index=2, total=7, children=children)
    _emit(
        reporter,
        "state_exit",
        state_name="c1",
        role="r1",
        adapter="a",
        model="m",
        index=2,
        total=7,
        elapsed_seconds=4.0,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="c2",
        role="r2",
        adapter="a",
        model="m",
        index=3,
        total=7,
        elapsed_seconds=5.0,
    )
    _emit(reporter, "fan_out_end", state_name="frame", role="framer", index=2, total=7)
    # Sequential chairman.
    _emit(
        reporter,
        "state_enter",
        state_name="synthesize",
        role="chairman",
        adapter="claude_code_text",
        model="opus",
        index=4,
        total=7,
    )
    _emit(
        reporter,
        "state_exit",
        state_name="synthesize",
        role="chairman",
        adapter="claude_code_text",
        model="opus",
        index=4,
        total=7,
        elapsed_seconds=5.1,
    )

    lines = buf.getvalue().splitlines()
    # First line is sequential, no range.
    assert lines[0] == "[1/7] framer (claude_code_text:opus) ... starting"
    # After the block closes, the chairman line must be sequential
    # (no "[N-M/...]" range), with its own start and done lines.
    assert "[4/7] chairman (claude_code_text:opus) ... starting" in lines
    assert "[4/7] chairman (claude_code_text:opus) ... done in 5.1s" in lines


# --------------------------------------------------------------------
# Library default-on: run_workflow prints to stderr unless quiet=True
# --------------------------------------------------------------------


def _make_minimal_config() -> OrchestraConfig:
    """Build a config that satisfies ask_council's seven required
    role bindings using the same recording adapter shape the executor
    end-to-end tests use. The actual model strings do not matter
    because the runtime registry is replaced before the executor
    runs."""
    return OrchestraConfig(
        roles={
            "framer": RoleBinding(adapter="claude_code_text", model="opus"),
            "contrarian": RoleBinding(
                adapter="claude_code_text", model="kimi-k2.6"
            ),
            "first_principles": RoleBinding(
                adapter="claude_code_text", model="opus"
            ),
            "expansionist": RoleBinding(
                adapter="claude_code_text", model="sonnet"
            ),
            "outsider": RoleBinding(
                adapter="claude_code_text", model="kimi-k2.6"
            ),
            "executor_lens": RoleBinding(
                adapter="claude_code_text", model="opus"
            ),
            "chairman": RoleBinding(adapter="claude_code_text", model="opus"),
        },
        workflows={"council": WorkflowConfig(pattern="ask_council")},
        verbs={},
    )


def test_resolve_progress_callback_default_installs_stderr_reporter() -> None:
    """No user callback, no quiet flag -> stderr reporter."""
    from orchestra.api import _resolve_progress_callback

    cb = _resolve_progress_callback(None, quiet=False)
    assert cb is not None
    # The default reporter writes to stderr when called. Verify by
    # injecting a buffer-backed reporter would not be possible here
    # (the api creates one with default stream); just verify it is
    # callable and does not raise.
    cb(
        ProgressEvent(
            kind="state_enter",
            state_name="x",
            role=None,
            adapter=None,
            model=None,
            index=1,
            total=1,
            elapsed_seconds=None,
        )
    )


def test_resolve_progress_callback_quiet_true_suppresses_user_callback() -> None:
    """quiet=True wins over a passed user_callback. Caller asked for
    silence; honor it. The returned callback drops events."""
    from orchestra.api import _resolve_progress_callback

    received: list[ProgressEvent] = []
    user_cb = received.append
    cb = _resolve_progress_callback(user_cb, quiet=True)
    assert cb is not None
    cb(
        ProgressEvent(
            kind="state_enter",
            state_name="x",
            role=None,
            adapter=None,
            model=None,
            index=1,
            total=1,
            elapsed_seconds=None,
        )
    )
    # The user's callback was NOT invoked because quiet=True
    # short-circuits to silent_reporter.
    assert received == []


def test_resolve_progress_callback_user_callback_wins_over_default() -> None:
    """When the caller passes a callback and does not set quiet, the
    user's callback is used (not the default stderr reporter)."""
    from orchestra.api import _resolve_progress_callback

    received: list[ProgressEvent] = []
    user_cb = received.append
    cb = _resolve_progress_callback(user_cb, quiet=False)
    assert cb is user_cb
    cb(
        ProgressEvent(
            kind="state_enter",
            state_name="x",
            role=None,
            adapter=None,
            model=None,
            index=1,
            total=1,
            elapsed_seconds=None,
        )
    )
    assert len(received) == 1


def test_run_workflow_default_prints_progress_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: calling run_workflow without quiet or
    progress_callback prints the per-state progress lines to
    stderr. Stdout is not used by the executor; only the final
    answer reaches stdout (via the caller, not run_workflow)."""
    from orchestra.api import run_workflow

    cfg = _make_minimal_config()
    # Patch the registry's model adapter so the workflow actually
    # runs without any subprocesses. The recording adapter returns
    # canned text per state_id so every state can complete.
    from orchestra import api as _api

    real_build = _api._build_registry

    def _patched_build(role_bindings: dict[str, Any]) -> Any:
        reg = real_build(role_bindings)
        adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
        reg.actor_backings["model"] = lambda: adapter
        reg._adapter_cache.pop("model", None)
        return reg

    import unittest.mock

    with unittest.mock.patch.object(_api, "_build_registry", _patched_build):
        result = run_workflow(
            "council",
            {"query": "q", "history": "h"},
            cfg,
            data_root=tmp_path / "runs",
        )
    assert result.terminal == "done"
    captured = capsys.readouterr()
    # The default stderr reporter prints at least the framer's
    # starting line and a parallel header for the five lens
    # advisors.
    assert "[1/7] framer (claude_code_text:opus) ... starting" in captured.err
    assert "starting in parallel" in captured.err
    assert "all 5 done, parallel wall-clock" in captured.err
    # Stdout is empty: run_workflow does not print the answer.
    assert captured.out == ""


def test_run_workflow_quiet_true_silences_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """quiet=True suppresses every progress line. Stderr stays clean
    so library callers that want to capture output for their own
    purposes can do so."""
    from orchestra import api as _api
    from orchestra.api import run_workflow

    real_build = _api._build_registry

    def _patched_build(role_bindings: dict[str, Any]) -> Any:
        reg = real_build(role_bindings)
        adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
        reg.actor_backings["model"] = lambda: adapter
        reg._adapter_cache.pop("model", None)
        return reg

    import unittest.mock

    cfg = _make_minimal_config()
    with unittest.mock.patch.object(_api, "_build_registry", _patched_build):
        result = run_workflow(
            "council",
            {"query": "q", "history": "h"},
            cfg,
            data_root=tmp_path / "runs",
            quiet=True,
        )
    assert result.terminal == "done"
    captured = capsys.readouterr()
    # No progress lines reach stderr under quiet=True.
    assert "starting" not in captured.err
    assert "starting in parallel" not in captured.err


# --------------------------------------------------------------------
# Executor surfaces fan_out_start and fan_out_end events
# --------------------------------------------------------------------


def test_executor_emits_fan_out_start_and_fan_out_end(
    tmp_path: Path,
) -> None:
    """End-to-end: the council workflow has one fan-out group with
    five children. The executor must surface exactly one
    fan_out_start (with five children listed) and one fan_out_end."""
    path = resolve_workflow_path("ask_council", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _RecordingModelAdapter(_COUNCIL_RESPONSES)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)

    events: list[
        tuple[str, str, str | None, int, int, float | None, Any]
    ] = []

    def _cb(
        kind: str,
        state_name: str,
        role: str | None,
        index: int,
        total: int,
        elapsed: float | None,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        events.append((kind, state_name, role, index, total, elapsed, children))

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
        external_inputs={"query": "q", "history": "h"},
        progress_callback=_cb,
    )
    executor.run_to_completion()
    log.close()
    store.close()

    starts = [e for e in events if e[0] == "fan_out_start"]
    ends = [e for e in events if e[0] == "fan_out_end"]
    assert len(starts) == 1
    assert len(ends) == 1
    start = starts[0]
    children = start[6]
    assert children is not None
    assert len(children) == 5
    child_states = {c[0] for c in children}
    assert child_states == {
        "contrarian_advise",
        "first_principles_advise",
        "expansionist_advise",
        "outsider_advise",
        "executor_lens_advise",
    }
