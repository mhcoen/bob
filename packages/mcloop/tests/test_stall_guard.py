"""Tests for the agent-session stall guard (runner.py).

Covers the pure pieces -- ``parse_tool_signatures`` and ``StallTracker`` --
plus a loop-level test that drives ``_run_session`` with a synthetic
stream-json stream and asserts a repeated identical tool call aborts the
session with ``STALL_EXIT_CODE`` and a process-group kill.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcloop.runner import (
    STALL_EXIT_CODE,
    STALL_REPEAT_THRESHOLD,
    StallTracker,
    ToolSignature,
    _run_session,
    parse_tool_signatures,
)

# --- StallTracker -----------------------------------------------------------


def test_tracker_trips_on_threshold_consecutive() -> None:
    """Default threshold (4): observe() is False for the first 3 and True on
    the 4th identical signature."""
    tracker = StallTracker()
    sig = ToolSignature(name="Bash", command="pytest -q")
    results = [tracker.observe(sig) for _ in range(STALL_REPEAT_THRESHOLD)]
    assert results == [False, False, False, True]
    assert tracker.count == STALL_REPEAT_THRESHOLD
    assert tracker.repeated == sig


def test_tracker_resets_on_different_signature() -> None:
    """A,A,A,B,A never reaches the count-of-4 because B resets the run."""
    tracker = StallTracker(threshold=4)
    a = ToolSignature(name="Bash", command="make")
    b = ToolSignature(name="Bash", command="ls")
    seq = [a, a, a, b, a]
    tripped = [tracker.observe(s) for s in seq]
    assert tripped == [False, False, False, False, False]
    # After the final A the run length is 1 (B reset it), not 4.
    assert tracker.count == 1
    assert tracker.repeated == a


def test_tracker_threshold_is_configurable() -> None:
    """threshold=2 trips on the second identical signature."""
    tracker = StallTracker(threshold=2)
    sig = ToolSignature(name="Read", command='{"file_path": "x"}')
    assert tracker.observe(sig) is False
    assert tracker.observe(sig) is True


# --- parse_tool_signatures --------------------------------------------------


def _assistant_line(blocks: list[dict]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": blocks}})


def test_parse_bash_uses_command() -> None:
    line = _assistant_line(
        [{"type": "tool_use", "name": "Bash", "input": {"command": "  pytest -q  "}}]
    )
    sigs = parse_tool_signatures(line, backend="claude")
    assert sigs == [ToolSignature(name="Bash", command="pytest -q")]


def test_parse_non_bash_keys_on_sorted_json_input() -> None:
    line = _assistant_line([{"type": "tool_use", "name": "Read", "input": {"b": 2, "a": 1}}])
    sigs = parse_tool_signatures(line, backend="claude")
    assert sigs == [ToolSignature(name="Read", command='{"a": 1, "b": 2}')]


def test_parse_multiple_tool_use_blocks_in_order() -> None:
    line = _assistant_line(
        [
            {"type": "text", "text": "doing work"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "a"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "b"}},
        ]
    )
    sigs = parse_tool_signatures(line, backend="claude")
    assert sigs == [
        ToolSignature(name="Bash", command="a"),
        ToolSignature(name="Bash", command="b"),
    ]


def test_parse_ignores_non_tool_lines() -> None:
    # non-json
    assert parse_tool_signatures("not json", backend="claude") == []
    # assistant message with only a text block (no tool_use)
    assert (
        parse_tool_signatures(_assistant_line([{"type": "text", "text": "hi"}]), backend="claude")
        == []
    )
    # a tool_result / user line
    assert (
        parse_tool_signatures(
            json.dumps({"type": "user", "message": {"content": []}}), backend="claude"
        )
        == []
    )


def test_parse_codex_backend_yields_nothing() -> None:
    """Codex is an explicit known gap: even a claude-shaped line yields []."""
    line = _assistant_line([{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}])
    assert parse_tool_signatures(line, backend="codex") == []


# --- loop-level: _run_session aborts on a stall ----------------------------


class _FakeProc:
    """Minimal Popen stand-in: stdout iterates the given lines once."""

    def __init__(self, lines: list[str], pid: int = 4321):
        self.stdout = iter(lines)
        self.pid = pid
        self.returncode = 0

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


def _bash_line(cmd: str) -> str:
    return _assistant_line([{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}])


def _text_line(text: str) -> str:
    return _assistant_line([{"type": "text", "text": text}])


def _run_with_stream(lines: list[str], tmp_path: Path, killpg: MagicMock):
    """Drive _run_session against a synthetic stream; returns (output, code)."""
    main_proc = _FakeProc(lines)
    watchdog = MagicMock()
    with (
        patch("mcloop.runner.subprocess.Popen", side_effect=[main_proc, watchdog]),
        patch("mcloop.runner.os.getpgid", return_value=4321),
        patch("mcloop.runner.os.killpg", killpg),
    ):
        return _run_session(["claude", "-p"], tmp_path, env={"X": "1"}, timeout=3600)


def test_run_session_aborts_on_repeated_identical_bash(tmp_path: Path) -> None:
    """Four identical Bash calls -- interleaved with text/partial lines that
    must NOT reset the counter -- abort with STALL_EXIT_CODE and a group kill.
    Lines after the trip (the 5th distinct call) are never consumed."""
    killpg = MagicMock()
    lines = [
        _bash_line("pytest -q"),
        _text_line("partial chatter"),
        _bash_line("pytest -q"),
        json.dumps({"type": "stream_event", "delta": "tokens"}),
        _bash_line("pytest -q"),
        _text_line("more chatter"),
        _bash_line("pytest -q"),  # 4th identical -> trips here
        _bash_line("rm -rf /"),  # must never be reached
    ]
    output, code = _run_with_stream(lines, tmp_path, killpg)
    assert code == STALL_EXIT_CODE
    killpg.assert_called_once_with(4321, 9)
    # The session was torn down before consuming the post-trip line.
    assert "rm -rf /" not in output


def test_run_session_no_stall_runs_to_completion(tmp_path: Path) -> None:
    """Distinct calls never trip the guard: the stream drains to its sentinel
    and the normal returncode path is taken (no process-group kill)."""
    killpg = MagicMock()
    lines = [
        _bash_line("step 1"),
        _bash_line("step 2"),
        _bash_line("step 3"),
        _bash_line("step 4"),
        _bash_line("step 5"),
    ]
    _output, code = _run_with_stream(lines, tmp_path, killpg)
    assert code == 0
    killpg.assert_not_called()
