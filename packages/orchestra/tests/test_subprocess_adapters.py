"""Tests for the shared subprocess helpers in
``orchestra.adapters._subprocess``.

The helpers are lifted from McLoop's runner and inherit its
visual-feedback prints (progress dots, permission-denied banner,
Telegram-waiting banner). Those prints corrupt structured callers
like the orchestra REPL, so ``run_session`` accepts a ``silent``
keyword that suppresses them. These tests confirm the suppression
works without changing control flow.

The tests use small ``PROGRESS_QUEUE_INTERVAL`` and
``PROGRESS_DOT_INTERVAL`` values so a short ``sh -c "sleep ..."``
subprocess is enough to trigger a progress dot. No live LLM call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters import _subprocess


@pytest.fixture
def fast_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the dot-interval timers so the test does not need to
    wait three seconds for the first dot to fire."""
    monkeypatch.setattr(_subprocess, "PROGRESS_QUEUE_INTERVAL", 0.05)
    monkeypatch.setattr(_subprocess, "PROGRESS_DOT_INTERVAL", 0.1)


def test_run_session_silent_suppresses_progress_dots(
    tmp_path: Path,
    fast_progress: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A subprocess silent long enough to trigger several progress
    dots must produce no stdout output when ``silent=True``."""
    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 0.4; echo done"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "done" in output
    # No progress dots, no banners.
    assert "." not in captured.out
    assert "Permission denied" not in captured.out
    assert "Waiting for Telegram approval" not in captured.out


def test_run_session_default_emits_progress_dots(
    tmp_path: Path,
    fast_progress: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Legacy callers that omit the silent flag still get the
    progress dots they used to. Confirms silent defaults to False."""
    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 0.4; echo done"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "done" in output
    # At least one progress dot fired.
    assert "." in captured.out


def test_run_session_silent_does_not_change_exit_code(
    tmp_path: Path,
    fast_progress: None,
) -> None:
    """``silent`` only affects display, not control flow. A subprocess
    that exits nonzero still returns the same exit code under both
    silent settings."""
    _, code_silent = _subprocess.run_session(
        ["sh", "-c", "exit 7"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    _, code_loud = _subprocess.run_session(
        ["sh", "-c", "exit 7"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=False,
    )
    assert code_silent == 7
    assert code_loud == 7


def test_run_session_bad_utf8_byte_still_captures_trailing_output(
    tmp_path: Path,
) -> None:
    """A single invalid UTF-8 byte mid-stream must not truncate
    capture. With strict decoding the reader thread would raise on
    the bad byte, swallow the exception, queue the sentinel, and drop
    every later line including the final result record. The stream is
    opened with ``errors="replace"`` so the bad byte is replaced and
    the trailing record survives."""
    # printf emits: a normal line, a raw 0xff (invalid UTF-8), then the
    # trailing result record. exit 0 so the truncation is silent.
    output, exit_code = _subprocess.run_session(
        ["sh", "-c", r"printf 'first\n\377\nRESULT_RECORD\n'"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    assert exit_code == 0
    assert "first" in output
    assert "RESULT_RECORD" in output


# --------------------------------------------------------------------
# T-000004: run_session's wall-clock timeout contract. ``None`` is the
# only supported way to spell "no timeout"; ``0`` and other non-positive
# values are rejected so a future caller cannot resurrect the falsy-zero
# sentinel that silently disabled the guard.
# --------------------------------------------------------------------


@pytest.mark.parametrize("bad_timeout", [0, -1, -3600])
def test_run_session_rejects_non_positive_timeout(
    tmp_path: Path,
    bad_timeout: int,
) -> None:
    """A ``timeout`` of 0 or negative is a caller error, not a sentinel.
    ``run_session`` raises ``ValueError`` before it spawns anything so
    the falsy-zero bug (0 read as "no wall-clock cap") cannot recur."""
    with pytest.raises(ValueError, match="None"):
        _subprocess.run_session(
            ["true"],
            tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            timeout=bad_timeout,
            silent=True,
        )


def test_run_session_rejects_timeout_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rejection happens before ``subprocess.Popen`` is reached, so
    an invalid timeout never leaves a stray process or PID file."""
    called = False

    def _boom(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("Popen must not be called for an invalid timeout")

    monkeypatch.setattr("orchestra.adapters._subprocess.subprocess.Popen", _boom)
    with pytest.raises(ValueError):
        _subprocess.run_session(
            ["true"],
            tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            timeout=0,
            silent=True,
        )
    assert called is False


def test_run_session_none_timeout_disables_wall_clock_cap(
    tmp_path: Path,
    fast_progress: None,
) -> None:
    """``timeout=None`` disables the wall-clock cap. A quick subprocess
    finishes with its real exit code rather than being force-killed with
    the timeout code (TIMEOUT_KILL_EXIT), proving None is accepted and the guard never
    fires."""
    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 0.3; echo done"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=None,
        silent=True,
    )
    assert exit_code == 0
    assert "done" in output


def test_run_session_positive_timeout_still_kills_on_wall_clock(
    tmp_path: Path,
    fast_progress: None,
) -> None:
    """The guard still works for a legitimate positive cap: a process
    that outlives its wall-clock timeout is killed and returns
    TIMEOUT_KILL_EXIT (-102)."""
    _, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 5"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=1,
        silent=True,
    )
    assert exit_code == _subprocess.TIMEOUT_KILL_EXIT


# --------------------------------------------------------------------
# Pass-2 fix #4: write_log filenames do not collide under fan-out
# --------------------------------------------------------------------


def test_write_log_filename_includes_state_id_and_attempt(tmp_path: Path) -> None:
    """Two write_log calls in the same wall-clock second with the
    same task_label but different state_id values must produce
    distinct files. Pre-fix the filename was
    ``<second>_<slug>.log`` and concurrent fan-out children sharing
    a task_label would clobber each other on disk; post-fix the
    state_id and attempt discriminate so no collision occurs."""
    p1 = _subprocess.write_log(
        tmp_path,
        "shared task label",
        ["echo", "a"],
        "child a body",
        0,
        state_id="advise_a",
        attempt=1,
    )
    p2 = _subprocess.write_log(
        tmp_path,
        "shared task label",
        ["echo", "b"],
        "child b body",
        0,
        state_id="advise_b",
        attempt=1,
    )
    assert p1 != p2
    assert "advise-a" in p1.name
    assert "advise-b" in p2.name
    # And both bodies survived; pre-fix one would overwrite the other.
    assert p1.read_text().endswith("child a body\n")
    assert p2.read_text().endswith("child b body\n")


def test_write_log_filename_includes_attempt_for_retries(tmp_path: Path) -> None:
    """Two attempts of the same state in the same wall-clock second
    must not collide. The attempt suffix discriminates."""
    p1 = _subprocess.write_log(
        tmp_path,
        "task",
        ["echo"],
        "body 1",
        0,
        state_id="edit",
        attempt=1,
    )
    p2 = _subprocess.write_log(
        tmp_path,
        "task",
        ["echo"],
        "body 2",
        0,
        state_id="edit",
        attempt=2,
    )
    assert p1 != p2
    assert "a1" in p1.name
    assert "a2" in p2.name


def test_write_log_filename_unique_without_state_id(tmp_path: Path) -> None:
    """Even when callers do not pass state_id (legacy callers, tests
    that hand-call write_log), two calls in the same wall-clock
    second must still produce distinct paths via the monotonic
    nanosecond suffix."""
    p1 = _subprocess.write_log(tmp_path, "task", ["echo"], "body 1", 0)
    p2 = _subprocess.write_log(tmp_path, "task", ["echo"], "body 2", 0)
    assert p1 != p2
    assert p1.read_text().endswith("body 1\n")
    assert p2.read_text().endswith("body 2\n")


# --------------------------------------------------------------------
# Pass-7 fix: prompt content does not leak into transcript logs
# --------------------------------------------------------------------


def test_write_log_does_not_persist_prompt_argv(tmp_path: Path) -> None:
    """Pass-7 regression test: the cmd list write_log records must
    NOT contain the rendered prompt. Audit verified the leak by
    writing SECRET_TOKEN_123 through the pre-fix command path; the
    same token must be absent from the post-fix log even when the
    caller passes a cmd that mentions it (defensive: redaction
    happens at the call site, not at write_log itself, but the
    integration tests below assert the call site does the right
    thing).

    This test pins the call-site invariant: an adapter that omits
    the prompt from its argv produces a transcript whose prologue
    has no prompt content. The audit author's verification used
    write_log directly with the prompt in cmd; we now construct the
    cmd without it via the new _build_command signatures."""
    from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter

    adapter = ClaudeCodeTextAdapter()
    cmd = adapter._build_command(model="opus")
    log_path = _subprocess.write_log(
        tmp_path,
        "task containing SECRET_TOKEN_123 in label",
        cmd,
        "fake stream output",
        0,
    )
    body = log_path.read_text(encoding="utf-8")
    # The cmd line is in the log. It must not carry the prompt
    # because the adapter does not place it there. The task label is
    # logged separately; that surface is documented as caller-
    # provided and is the user's choice (a label should not include
    # secrets, but redacting the label is out of scope).
    cmd_line = next(line for line in body.splitlines() if line.startswith("Command:"))
    for arg in cmd:
        assert "SECRET_TOKEN_123" not in arg
    assert "SECRET_TOKEN_123" not in cmd_line


def test_run_session_pipes_prompt_via_stdin(tmp_path: Path) -> None:
    """Pass-7 regression test: when stdin_bytes is supplied,
    run_session writes those bytes to the subprocess's stdin and
    closes the pipe. Verified by running ``cat`` (which echoes
    stdin to stdout) with a SECRET_TOKEN_123 payload; the output
    contains the secret because the subprocess saw it on stdin,
    while the cmd list itself contains nothing more than ``cat``."""
    output, exit_code = _subprocess.run_session(
        ["cat"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
        stdin_bytes=b"SECRET_TOKEN_123\n",
    )
    assert exit_code == 0
    assert "SECRET_TOKEN_123" in output


def test_run_session_no_stdin_bytes_falls_back_to_devnull(
    tmp_path: Path,
) -> None:
    """The legacy mock-adapter path passes no stdin_bytes; run_session
    must keep its old behavior of routing /dev/null to the
    subprocess's stdin so any cat-like CLI gets EOF immediately
    instead of blocking on a non-existent pipe write."""
    output, exit_code = _subprocess.run_session(
        ["cat"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    assert exit_code == 0
    assert output == ""


# --------------------------------------------------------------------
# T-000001: live activity tracker for the actor_progress two-line
# format. The reader thread parses each stream-json line and updates a
# module-global activity string; the stateful progress reporter reads
# the value while emitting the "still running" ticker.
# --------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _isolate_activity() -> Any:
    """Clear the module-global activity before and after the test so
    one test's residue cannot leak into another."""
    _subprocess._clear_current_activity()
    yield
    _subprocess._clear_current_activity()


def test_record_activity_extracts_read_tool_use_path(_isolate_activity: Any) -> None:
    """A top-level ``assistant`` record with a ``tool_use`` block
    surfaces ``"<tool> <path>"`` as the current activity."""
    line = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Read","input":{"file_path":"/tmp/x.py"}}]}}'
    )
    _subprocess._record_activity_from_line(line)
    assert _subprocess.get_current_activity() == "Read /tmp/x.py"


def test_record_activity_extracts_bash_command(_isolate_activity: Any) -> None:
    """The Bash tool's activity is rendered as ``Bash <command>`` so
    the user sees what shell command the agent kicked off."""
    line = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Bash","input":{"command":"pytest tests/foo.py"}}]}}'
    )
    _subprocess._record_activity_from_line(line)
    assert _subprocess.get_current_activity() == "Bash pytest tests/foo.py"


def test_record_activity_handles_stream_event_shape(
    _isolate_activity: Any,
) -> None:
    """Some Claude Code versions wrap the tool_use announcement in a
    ``stream_event`` / ``content_block_start`` record instead of the
    top-level ``assistant`` shape. Both shapes must be honoured."""
    line = (
        '{"type":"stream_event","event":{"type":"content_block_start",'
        '"content_block":{"type":"tool_use","name":"Edit",'
        '"input":{"file_path":"/tmp/y.py"}}}}'
    )
    _subprocess._record_activity_from_line(line)
    assert _subprocess.get_current_activity() == "Edit /tmp/y.py"


def test_record_activity_overwrites_with_most_recent_tool_use(
    _isolate_activity: Any,
) -> None:
    """Each tool_use announcement overwrites the previous activity so
    the reporter always shows what the agent is doing *now*."""
    first = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Read","input":{"file_path":"/a"}}]}}'
    )
    second = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Edit","input":{"file_path":"/b"}}]}}'
    )
    _subprocess._record_activity_from_line(first)
    _subprocess._record_activity_from_line(second)
    assert _subprocess.get_current_activity() == "Edit /b"


def test_record_activity_ignores_non_tool_use_records(
    _isolate_activity: Any,
) -> None:
    """Hook events, init records, partial text deltas, and result
    summaries do not announce a new tool_use; the activity must not
    change in response to them."""
    _subprocess._set_current_activity("Read /baseline")
    for noise in (
        '{"type":"init"}',
        '{"type":"hook"}',
        '{"type":"result","subtype":"success","result":"done"}',
        '{"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"type":"text_delta","text":"hello"}}}',
        # Malformed lines must not raise.
        "not json at all",
        "",
        "{not json",
    ):
        _subprocess._record_activity_from_line(noise)
    assert _subprocess.get_current_activity() == "Read /baseline"


def test_run_session_clears_activity_on_completion(tmp_path: Path, fast_progress: None) -> None:
    """The activity tracker is per-session. A finished session must
    leave the global activity empty so a subsequent
    ``actor_progress`` ticker does not surface stale state."""
    _subprocess._set_current_activity("Read /from/previous/session")
    _subprocess.run_session(
        ["sh", "-c", "echo done"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=10,
        silent=True,
    )
    assert _subprocess.get_current_activity() == ""


def test_idle_kill_paused_while_live_pending_approval(
    tmp_path: Path,
    fast_progress: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live pending-approval file must freeze the idle clock.

    Regression: the idle-kill fired during Telegram approval waits
    (the hook blocks the tool call, so the stream goes silent), so a
    session waiting on a human was killed with -3 after IDLE_TIMEOUT_S
    and the attempt was burned. With an approval in flight the wait is
    not idleness. The pending file is named by the hook's pid; use our
    own pid so it counts as live.
    """
    import os

    monkeypatch.setattr(_subprocess, "IDLE_TIMEOUT_S", 0.3)
    pending_dir = tmp_path / ".mcloop" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / str(os.getpid())).write_text("Bash: pytest -x")

    # Sleeps well past IDLE_TIMEOUT_S while producing no output. With
    # the pending file honored, the session survives to completion.
    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 0.8; echo survived"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    assert exit_code == 0
    assert "survived" in output


def test_idle_kill_fires_despite_stale_pending_file(
    tmp_path: Path,
    fast_progress: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending file whose hook pid is dead must not freeze the clock.

    A SIGKILL of the session's process group kills the hook before its
    cleanup runs, orphaning the pending file. Honoring the orphan would
    disable the idle kill forever, so stale entries are pruned and the
    kill still fires (IDLE_KILL_EXIT).
    """
    monkeypatch.setattr(_subprocess, "IDLE_TIMEOUT_S", 0.3)
    pending_dir = tmp_path / ".mcloop" / "pending"
    pending_dir.mkdir(parents=True)
    # Pid 2**22 is above macOS/Linux default pid ranges: reliably dead.
    stale = pending_dir / str(2**22)
    stale.write_text("Bash: pytest -x")

    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 5; echo survived"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    assert exit_code == _subprocess.IDLE_KILL_EXIT
    assert "survived" not in output
    assert not stale.exists()


def test_dead_child_with_held_pipe_and_pending_file_reaps_promptly(
    tmp_path: Path,
    fast_progress: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead child whose grandchild still holds the pipe must be
    reaped within the timeout, pending approval or not.

    Regression (HIGH): with a live pending file the old freeze guard
    skipped into the liveness bailout, whose stdout.close() blocked on
    the reader thread's lock until the grandchild exited -- escaping
    every timeout (an 8s cap stretched to the grandchild's lifetime)
    and masking the kill sentinel with the child's raw exit code. The
    bailout now group-kills first, forcing pipe EOF.
    """
    import os
    import time

    pending = tmp_path / ".mcloop" / "pending"
    pending.mkdir(parents=True)
    # A pending file naming a live pid (our own) so the approval
    # freeze condition is genuinely met.
    (pending / str(os.getpid())).write_text("probe approval")

    start = time.monotonic()
    output, exit_code = _subprocess.run_session(
        # Child exits 7 immediately; grandchild keeps the merged pipe
        # open for 30s. Pre-fix this returned only after ~30s.
        ["sh", "-c", "sleep 30 & exit 7"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=8,
        silent=True,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 8, f"bailout blocked for {elapsed:.1f}s on a held pipe"
    assert exit_code == 7


def test_stale_denied_file_is_cleared_at_session_start(
    tmp_path: Path,
    fast_progress: None,
) -> None:
    """A denied file left by an orphaned hook from a PREVIOUS session
    must not kill the next session's first silent stretch."""
    pending = tmp_path / ".mcloop" / "pending"
    pending.mkdir(parents=True)
    (pending / "denied").write_text("stale denial from a dead session")

    output, exit_code = _subprocess.run_session(
        ["sh", "-c", "sleep 0.3; echo survived"],
        tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        silent=True,
    )
    assert exit_code == 0
    assert "survived" in output
    assert not (pending / "denied").exists()
