"""Tests for the interactive REPL.

Covers the slash-command dispatcher, the transcript formatter,
the default-verb selection, the run_verb routing path, and the
loop-level behavior (errors stay in the REPL, double Ctrl-C exits,
EOF exits cleanly). All real LLM calls are stubbed via
``run_verb`` and prompt_toolkit's ``PromptSession.prompt`` is
replaced with a queued list of canned inputs so the loop runs
deterministically without terminal I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestra import repl
from orchestra.config import (
    OrchestraConfig,
    RoleBinding,
    VerbBinding,
    WorkflowConfig,
)
from orchestra.errors import OrchestraError


def _config(default_verb: str | None = None) -> OrchestraConfig:
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_text", model="opus"
            ),
        },
        workflows={
            "ask_single": WorkflowConfig(pattern="ask_single"),
            "ask_propose_critique_synthesize": WorkflowConfig(
                pattern="ask_propose_critique_synthesize"
            ),
        },
        verbs={
            "ask": VerbBinding(workflow="ask_single"),
            "council": VerbBinding(
                workflow="ask_propose_critique_synthesize"
            ),
        },
    )
    if default_verb is not None:
        # OrchestraConfig is frozen; smuggle the default-verb hint via
        # object.__setattr__ so the test can exercise the explicit
        # default-verb override path without changing the dataclass
        # shape.
        object.__setattr__(cfg, "default_verb", default_verb)
    return cfg


# --------------------------------------------------------------------
# Transcript formatter
# --------------------------------------------------------------------


def test_format_history_empty_returns_empty_string() -> None:
    assert repl.format_history([]) == ""


def test_format_history_renders_user_assistant_pairs() -> None:
    turns = [
        repl.Turn(verb="ask", query="what is 2+2", answer="4"),
        repl.Turn(verb="ask", query="and 3+3", answer="6"),
    ]
    out = repl.format_history(turns)
    assert out.startswith("Prior conversation:\n")
    assert out.endswith("\n\n")
    assert "user: what is 2+2" in out
    assert "assistant: 4" in out
    assert "user: and 3+3" in out
    assert "assistant: 6" in out


# --------------------------------------------------------------------
# Default verb
# --------------------------------------------------------------------


def test_default_verb_picks_first_alphabetical() -> None:
    cfg = _config()
    assert repl._default_verb(cfg) == "ask"


def test_default_verb_uses_explicit_default_when_set() -> None:
    cfg = _config(default_verb="council")
    assert repl._default_verb(cfg) == "council"


def test_default_verb_returns_none_when_no_verbs() -> None:
    cfg = OrchestraConfig()
    assert repl._default_verb(cfg) is None


# --------------------------------------------------------------------
# Slash-command dispatcher
# --------------------------------------------------------------------


def _state_with_turns(*turns: repl.Turn) -> repl.ReplState:
    state = repl.ReplState(config=_config(), current_verb="ask")
    state.turns.extend(turns)
    return state


def test_slash_help_lists_commands_and_verbs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    out = repl.dispatch_slash(state, "/help")
    assert out.exit is False
    text = capsys.readouterr().out
    assert "Slash commands:" in text
    assert "/help" in text and "/exit" in text
    assert "Configured verbs:" in text
    assert "ask" in text


def test_slash_verb_with_no_arg_shows_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    repl.dispatch_slash(state, "/verb")
    assert state.current_verb == "ask"
    assert "current verb: ask" in capsys.readouterr().out


def test_slash_verb_switches(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    repl.dispatch_slash(state, "/verb council")
    assert state.current_verb == "council"
    assert "verb -> council" in capsys.readouterr().out


def test_slash_verb_unknown_keeps_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    repl.dispatch_slash(state, "/verb nope")
    assert state.current_verb == "ask"
    assert "unknown verb 'nope'" in capsys.readouterr().err


def test_slash_clear_drops_turns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns(
        repl.Turn(verb="ask", query="q", answer="a"),
        repl.Turn(verb="ask", query="q2", answer="a2"),
    )
    repl.dispatch_slash(state, "/clear")
    assert state.turns == []
    assert "cleared 2 turn(s)" in capsys.readouterr().out


def test_slash_history_prints_turns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns(
        repl.Turn(verb="ask", query="hello", answer="hi"),
    )
    repl.dispatch_slash(state, "/history")
    text = capsys.readouterr().out
    assert "Turn 1 (ask)" in text
    assert "you: hello" in text
    assert "assistant: hi" in text


def test_slash_save_writes_markdown(tmp_path: Path) -> None:
    state = _state_with_turns(
        repl.Turn(verb="ask", query="hello", answer="hi"),
    )
    target = tmp_path / "transcript.md"
    repl.dispatch_slash(state, f"/save {target}")
    assert target.exists()
    body = target.read_text()
    assert "# Orchestra session" in body
    assert "**You:**" in body and "hello" in body
    assert "**Assistant:**" in body and "hi" in body


def test_slash_save_writes_json_when_extension_says_so(
    tmp_path: Path,
) -> None:
    state = _state_with_turns(
        repl.Turn(verb="ask", query="q", answer="a"),
    )
    target = tmp_path / "transcript.json"
    repl.dispatch_slash(state, f"/save {target}")
    assert target.exists()
    data = json.loads(target.read_text())
    assert data == [{"verb": "ask", "query": "q", "answer": "a"}]


def test_slash_save_without_path_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    repl.dispatch_slash(state, "/save")
    assert "usage: /save <path>" in capsys.readouterr().err


def test_slash_unknown_command_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()
    out = repl.dispatch_slash(state, "/bogus")
    assert out.exit is False
    assert "unknown slash command" in capsys.readouterr().err


def test_slash_exit_signals_exit() -> None:
    state = _state_with_turns()
    out = repl.dispatch_slash(state, "/exit")
    assert out.exit is True


def test_slash_quit_signals_exit() -> None:
    state = _state_with_turns()
    out = repl.dispatch_slash(state, "/quit")
    assert out.exit is True


# --------------------------------------------------------------------
# handle_query
# --------------------------------------------------------------------


def test_handle_query_routes_through_run_verb(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns(
        repl.Turn(verb="ask", query="prior", answer="prior-answer"),
    )
    captured: dict[str, Any] = {}

    def _stub(verb: str, query: str, config: Any, *, history: str) -> str:
        captured["verb"] = verb
        captured["query"] = query
        captured["history"] = history
        return "Paris."

    monkeypatch.setattr(repl, "run_verb", _stub)
    repl.handle_query(state, "what is the capital of france")
    out = capsys.readouterr().out
    assert "Paris." in out
    assert captured["verb"] == "ask"
    assert captured["query"] == "what is the capital of france"
    assert "user: prior" in captured["history"]
    assert "assistant: prior-answer" in captured["history"]
    # The new turn was appended.
    assert state.turns[-1].query == "what is the capital of france"
    assert state.turns[-1].answer == "Paris."


def test_handle_query_error_keeps_state_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _state_with_turns()

    def _stub(verb: str, query: str, config: Any, *, history: str) -> str:
        raise OrchestraError("blew up")

    monkeypatch.setattr(repl, "run_verb", _stub)
    repl.handle_query(state, "anything")
    err = capsys.readouterr().err
    assert "blew up" in err
    # No turn appended on failure.
    assert state.turns == []


# --------------------------------------------------------------------
# Loop-level behavior with a fake PromptSession
# --------------------------------------------------------------------


class _FakeSession:
    """Drop-in PromptSession that returns canned input lines.

    Each call to ``prompt`` returns the next item; tuple items
    represent exception classes to raise in place of returning a
    line, e.g. ``(KeyboardInterrupt,)`` for Ctrl-C and
    ``(EOFError,)`` for Ctrl-D. After the queue empties, ``prompt``
    raises EOFError to terminate the loop.
    """

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def prompt(self, *_args: Any, **_kwargs: Any) -> str:
        if not self._items:
            raise EOFError
        item = self._items.pop(0)
        if isinstance(item, tuple) and len(item) == 1 and isinstance(item[0], type):
            raise item[0]()
        if isinstance(item, str):
            return item
        raise EOFError


def test_run_repl_dispatches_query_and_appends_turn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _config()
    captured: dict[str, Any] = {}

    def _stub(verb: str, query: str, config: Any, *, history: str) -> str:
        captured.setdefault("calls", []).append((verb, query, history))
        return f"answer-to-{query}"

    monkeypatch.setattr(repl, "run_verb", _stub)
    session = _FakeSession(
        [
            "first question",
            "second question",
            (EOFError,),
        ]
    )
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0
    out = capsys.readouterr().out
    assert "answer-to-first question" in out
    assert "answer-to-second question" in out
    # Second call's history mentions the first turn.
    calls = captured["calls"]
    assert len(calls) == 2
    assert calls[0][2] == ""
    assert "user: first question" in calls[1][2]
    assert "assistant: answer-to-first question" in calls[1][2]


def test_run_repl_handles_slash_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    monkeypatch.setattr(
        repl,
        "run_verb",
        lambda *a, **k: pytest.fail("run_verb should not fire"),
    )
    session = _FakeSession(["/exit"])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0


def test_run_repl_eof_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    session = _FakeSession([(EOFError,)])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0


def test_run_repl_double_ctrl_c_exits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _config()
    session = _FakeSession([(KeyboardInterrupt,), (KeyboardInterrupt,)])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0
    out = capsys.readouterr().out
    assert "double Ctrl-C" in out


def test_run_repl_single_ctrl_c_does_not_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single Ctrl-C cancels the line and prompts again. The
    subsequent EOFError exits the loop. The interrupt does not
    leak to the caller."""
    cfg = _config()
    session = _FakeSession([(KeyboardInterrupt,), (EOFError,)])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0


def test_run_repl_no_verbs_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = OrchestraConfig()
    session = _FakeSession([])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 1
    err = capsys.readouterr().err
    assert "no verbs configured" in err


def test_run_repl_verb_error_does_not_break_loop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _config()
    calls = {"n": 0}

    def _stub(verb: str, query: str, config: Any, *, history: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OrchestraError("first fails")
        return "second works"

    monkeypatch.setattr(repl, "run_verb", _stub)
    session = _FakeSession(["first", "second", (EOFError,)])
    rc = repl.run_repl(cfg, session=session)
    assert rc == 0
    out = capsys.readouterr()
    assert "first fails" in out.err
    assert "second works" in out.out
