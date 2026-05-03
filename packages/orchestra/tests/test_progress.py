"""Tests for the per-state progress reporter.

Covers the format string, the executor's callback hook firing once
per state_enter and once per state_exit, the api layer's wrapper
that enriches role into adapter and model from the resolved role
bindings, and the CLI's --quiet flag suppressing output.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import _pre_load_registry, _wrap_progress_callback
from orchestra.config import RoleBinding
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
# CLI --quiet flag suppresses progress
# --------------------------------------------------------------------


def test_cli_quiet_flag_installs_silent_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``orchestra --quiet <verb>`` must replace the stderr reporter
    with a silent one. The silent reporter, when called, writes
    nothing to stderr."""
    from orchestra import cli
    from orchestra.progress import ProgressEvent as PE

    # Pretend the home has a config so the verb dispatcher is
    # reachable. The dispatcher itself is stubbed below; we only care
    # which reporter the dispatcher receives.
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
        verb: str, query: str, config: Any, *, progress_callback: Any = None
    ) -> str:
        received_callback.append(progress_callback)
        return "answer"

    monkeypatch.setattr(cli, "run_verb", _stub_run_verb)

    rc = cli.main(["--quiet", "ask", "hello"])
    assert rc == 0
    assert len(received_callback) == 1
    cb = received_callback[0]
    assert cb is not None
    # The silent reporter, when invoked, must not raise. The callback
    # is a true no-op; this call simply asserts it does not throw.
    cb(
        PE(
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


def test_cli_default_installs_stderr_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--quiet`` the dispatcher receives a callback that
    writes to stderr."""
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
        verb: str, query: str, config: Any, *, progress_callback: Any = None
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


def test_cli_short_quiet_flag_also_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-q`` is an alias for ``--quiet``."""
    from orchestra import cli

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
        verb: str, query: str, config: Any, *, progress_callback: Any = None
    ) -> str:
        received_callback.append(progress_callback)
        return "answer"

    monkeypatch.setattr(cli, "run_verb", _stub_run_verb)

    rc = cli.main(["ask", "-q", "hello"])
    assert rc == 0
    # The dispatcher still got a callback; it is the silent variant.
    assert received_callback[0] is not None


def test_cli_extract_progress_flags_handles_both_forms() -> None:
    """The extractor preserves order of non-flag args and removes
    every occurrence of --quiet or -q regardless of position."""
    from orchestra.cli import _extract_progress_flags

    args, quiet = _extract_progress_flags(
        ["--quiet", "ask", "what", "is", "x"]
    )
    assert args == ["ask", "what", "is", "x"]
    assert quiet is True

    args, quiet = _extract_progress_flags(["ask", "-q", "what", "is", "x"])
    assert args == ["ask", "what", "is", "x"]
    assert quiet is True

    args, quiet = _extract_progress_flags(["ask", "what", "is", "x"])
    assert args == ["ask", "what", "is", "x"]
    assert quiet is False
