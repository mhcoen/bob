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
    p1 = _subprocess.write_log(
        tmp_path, "task", ["echo"], "body 1", 0
    )
    p2 = _subprocess.write_log(
        tmp_path, "task", ["echo"], "body 2", 0
    )
    assert p1 != p2
    assert p1.read_text().endswith("body 1\n")
    assert p2.read_text().endswith("body 2\n")
