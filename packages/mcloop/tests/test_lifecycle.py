"""Unit tests for mcloop.lifecycle — interrupt state, orphan cleanup, process management."""

import json
import signal
import subprocess
import time
from unittest.mock import MagicMock, patch

import mcloop.lifecycle as lifecycle_mod
from mcloop.lifecycle import (
    _all_tasks,
    _check_interrupted,
    _graceful_kill_active_process,
    _kill_active_process,
    _kill_orphan_sessions,
    _save_interrupt_state,
    _write_eliminated_json,
    _write_ruledout_to_plan,
    register_signal_handlers,
)

# ── _all_tasks ──


def _task(text, children=None):
    from mcloop.checklist import Task

    return Task(
        text=text,
        checked=False,
        failed=False,
        line_number=0,
        indent_level=0,
        children=children or [],
    )


def test_all_tasks_flat():
    """Flattens a flat list of tasks."""
    tasks = [_task("a"), _task("b")]
    result = _all_tasks(tasks)
    assert [t.text for t in result] == ["a", "b"]


def test_all_tasks_nested():
    """Flattens nested task tree."""
    child = _task("child")
    parent = _task("parent", children=[child])
    result = _all_tasks([parent])
    assert [t.text for t in result] == ["parent", "child"]


def test_all_tasks_empty():
    """Empty input returns empty list."""
    assert _all_tasks([]) == []


# ── _write_ruledout_to_plan ──


def test_write_ruledout_inserts_line(tmp_path):
    """Inserts [RULEDOUT] line after matching task."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Fix the bug\n- [ ] Other task\n")
    _write_ruledout_to_plan(plan, "Fix the bug", "tried X, didn't work")
    content = plan.read_text()
    assert "[RULEDOUT] tried X, didn't work" in content
    lines = content.splitlines()
    assert lines[0] == "- [ ] Fix the bug"
    assert "RULEDOUT" in lines[1]


def test_write_ruledout_no_match(tmp_path):
    """Does nothing when task text doesn't match."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Other task\n")
    _write_ruledout_to_plan(plan, "Nonexistent task", "desc")
    assert "RULEDOUT" not in plan.read_text()


# ── _write_eliminated_json ──


def test_write_eliminated_creates_file(tmp_path):
    """Creates eliminated.json with new entry."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    _write_eliminated_json(tmp_path, "1.1", "approach A")
    data = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert "1.1" in data
    assert len(data["1.1"]) == 1
    assert data["1.1"][0]["approach"] == "approach A"


def test_write_eliminated_appends(tmp_path):
    """Appends to existing eliminated.json."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    _write_eliminated_json(tmp_path, "1.1", "approach A")
    _write_eliminated_json(tmp_path, "1.1", "approach B")
    data = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert len(data["1.1"]) == 2


# ── _kill_orphan_sessions ──


def test_kill_orphan_no_pid_file(tmp_path):
    """No-op when no active-pid file exists."""
    _kill_orphan_sessions(tmp_path)


def test_kill_orphan_dead_process(tmp_path):
    """Cleans up pid file when process is already dead."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text("999999 999999")
    with patch("mcloop.lifecycle.os.kill", side_effect=ProcessLookupError):
        _kill_orphan_sessions(tmp_path)
    assert not pid_file.exists()


def test_kill_orphan_alive_process_legacy_no_kill(tmp_path, capsys):
    """Legacy format (no cmd metadata) removes stale PID instead of killing."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text("12345 12345")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "no verification metadata" in captured.out


def test_kill_orphan_json_format(tmp_path):
    """Parses JSON active-pid format with cmd and started fields."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="claude -p --model opus\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    assert not pid_file.exists()


def test_kill_orphan_json_dead_process(tmp_path):
    """Cleans up JSON-format pid file when process is already dead."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {"pid": 999999, "pgid": 999999, "cmd": "claude", "started": "2026-04-08T10:00:00"}
        )
    )
    with patch("mcloop.lifecycle.os.kill", side_effect=ProcessLookupError):
        _kill_orphan_sessions(tmp_path)
    assert not pid_file.exists()


def test_kill_orphan_json_missing_pgid(tmp_path):
    """Falls back to pid when pgid is absent in JSON format."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps({"pid": 54321, "cmd": "claude", "started": "2026-04-08T10:00:00"})
    )
    ps_result = MagicMock(stdout="claude\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(54321, signal.SIGKILL)
    assert not pid_file.exists()


def test_kill_orphan_invalid_json_falls_back(tmp_path):
    """Falls back to legacy format when JSON is malformed."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text("{bad json")
    # Falls through to legacy parsing which also fails → cleans up
    _kill_orphan_sessions(tmp_path)
    assert not pid_file.exists()


def test_kill_orphan_verifies_cmd_match(tmp_path):
    """Kills process when stored cmd matches the live process."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="claude -p --model opus\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    assert not pid_file.exists()


def test_kill_orphan_skips_when_cmd_mismatch(tmp_path, capsys):
    """Does not kill when live process command differs from stored cmd."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="/usr/bin/vim myfile.py\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "pid=12345" in captured.out
    assert "/usr/bin/vim myfile.py" in captured.out
    assert "claude -p --model opus" in captured.out


def test_kill_orphan_removes_stale_pid_when_ps_fails(tmp_path, capsys):
    """Removes stale pid file and warns when ps command fails."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", side_effect=OSError("ps not found")),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "could not verify" in captured.out


def test_kill_orphan_removes_stale_pid_when_ps_times_out(tmp_path, capsys):
    """Removes stale pid file and warns when ps command times out."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch(
            "mcloop.lifecycle.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ps", 5),
        ),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "could not verify" in captured.out


def test_kill_orphan_no_verification_for_legacy_format(tmp_path, capsys):
    """Legacy format (no cmd) removes stale PID file instead of killing."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text("12345 12345")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run") as mock_ps,
    ):
        _kill_orphan_sessions(tmp_path)
    mock_ps.assert_not_called()
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "no verification metadata" in captured.out


def test_kill_orphan_partial_cmd_match(tmp_path):
    """Kills when stored cmd is a substring of the live command (e.g. wrapper)."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    # Live process might have a longer path prefix
    ps_result = MagicMock(stdout="/usr/local/bin/claude -p --model opus\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    assert not pid_file.exists()


def test_kill_orphan_ps_empty_output_removes_stale(tmp_path, capsys):
    """Removes stale PID file when ps returns empty output (cannot confirm)."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="\n")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out
    assert "ps returned no output" in captured.out


def test_kill_orphan_no_mcloop_dir(tmp_path):
    """No-op when .mcloop directory does not exist at all."""
    assert not (tmp_path / ".mcloop").exists()
    _kill_orphan_sessions(tmp_path)
    # Should return without error; no directory created
    assert not (tmp_path / ".mcloop").exists()


def test_kill_orphan_reused_pid_permission_error(tmp_path, capsys):
    """Stale PID with reused process detected via PermissionError path."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="/usr/sbin/cron\n")

    def kill_side_effect(pid, sig):
        if sig == 0:
            raise PermissionError("Operation not permitted")
        raise AssertionError("Should not send a real signal")

    with (
        patch("mcloop.lifecycle.os.kill", side_effect=kill_side_effect),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_not_called()
    assert not pid_file.exists()
    captured = capsys.readouterr()
    assert "Stale PID file removed" in captured.out


def test_kill_orphan_matching_process_via_permission_error(tmp_path):
    """Kills matching process discovered via PermissionError on os.kill(pid, 0)."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="claude -p --model opus\n")

    def kill_side_effect(pid, sig):
        if sig == 0:
            raise PermissionError("Operation not permitted")

    with (
        patch("mcloop.lifecycle.os.kill", side_effect=kill_side_effect),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    assert not pid_file.exists()


def test_kill_orphan_killpg_fails_falls_back_to_kill(tmp_path):
    """Falls back to os.kill when os.killpg raises OSError."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text(
        json.dumps(
            {
                "pid": 12345,
                "pgid": 12345,
                "cmd": "claude -p --model opus",
                "started": "2026-04-08T10:30:00",
            }
        )
    )
    ps_result = MagicMock(stdout="claude -p --model opus\n")
    kill_calls = []

    def kill_side_effect(pid, sig):
        kill_calls.append((pid, sig))

    with (
        patch("mcloop.lifecycle.os.kill", side_effect=kill_side_effect),
        patch("mcloop.lifecycle.os.killpg", side_effect=OSError("No such process")),
        patch("mcloop.lifecycle.subprocess.run", return_value=ps_result),
    ):
        _kill_orphan_sessions(tmp_path)
    # os.kill(pid, 0) for alive check, then os.kill(pid, SIGKILL) as fallback
    assert (12345, signal.SIGKILL) in kill_calls
    assert not pid_file.exists()


# ── _kill_active_process ──


def test_kill_active_process_sends_sigkill():
    """Kills active process group with SIGKILL."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 55555
    original = runner._active_process
    runner._active_process = mock_proc
    try:
        with (
            patch("mcloop.lifecycle.os.getpgid", return_value=55555),
            patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        ):
            _kill_active_process()
            mock_killpg.assert_called_once_with(55555, signal.SIGKILL)
            assert runner._active_process is None
    finally:
        runner._active_process = original


def test_kill_active_process_noop_when_none():
    """No-op when no active process."""
    import mcloop.runner as runner

    original = runner._active_process
    runner._active_process = None
    try:
        with patch("mcloop.lifecycle.os.killpg") as mock_killpg:
            _kill_active_process()
            mock_killpg.assert_not_called()
    finally:
        runner._active_process = original


# ── _graceful_kill_active_process ──


def test_graceful_kill_sends_sigterm():
    """Sends SIGTERM first, then cleans up."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 77777
    mock_proc.wait.return_value = 0
    original = runner._active_process
    runner._active_process = mock_proc
    try:
        with (
            patch("mcloop.lifecycle.os.getpgid", return_value=77777),
            patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        ):
            _graceful_kill_active_process()
            mock_killpg.assert_called_once_with(77777, signal.SIGTERM)
            assert runner._active_process is None
    finally:
        runner._active_process = original


def test_graceful_kill_escalates_to_sigkill():
    """Escalates to SIGKILL after timeout."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 77777
    mock_proc.wait.side_effect = [
        subprocess.TimeoutExpired("cmd", 2),
        0,
    ]
    original = runner._active_process
    runner._active_process = mock_proc
    try:
        with (
            patch("mcloop.lifecycle.os.getpgid", return_value=77777),
            patch("mcloop.lifecycle.os.killpg") as mock_killpg,
        ):
            _graceful_kill_active_process()
            assert mock_killpg.call_count == 2
            mock_killpg.assert_any_call(77777, signal.SIGTERM)
            mock_killpg.assert_any_call(77777, signal.SIGKILL)
    finally:
        runner._active_process = original


def test_graceful_kill_noop_when_none():
    """No-op when no active process."""
    import mcloop.runner as runner

    original = runner._active_process
    runner._active_process = None
    try:
        with patch("mcloop.lifecycle.os.killpg") as mock_killpg:
            _graceful_kill_active_process()
            mock_killpg.assert_not_called()
    finally:
        runner._active_process = original


# ── _save_interrupt_state ──


def test_save_interrupt_state_writes_json(tmp_path):
    """Writes interrupted.json with expected fields."""
    import mcloop.runner as runner_mod

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()

    orig = (
        lifecycle_mod._project_dir,
        lifecycle_mod._current_task_label,
        lifecycle_mod._current_task_text,
        lifecycle_mod._current_phase,
        lifecycle_mod._phase_start_time,
    )
    try:
        lifecycle_mod._project_dir = tmp_path
        lifecycle_mod._current_task_label = "2.1"
        lifecycle_mod._current_task_text = "Test task"
        lifecycle_mod._current_phase = "task"
        lifecycle_mod._phase_start_time = time.monotonic() - 5
        runner_mod._last_output_lines.clear()
        runner_mod._last_output_lines.append("output line")

        _save_interrupt_state()

        state_file = mcloop_dir / "interrupted.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["task_label"] == "2.1"
        assert data["task_text"] == "Test task"
        assert data["phase"] == "task"
        assert data["elapsed_seconds"] >= 4
        assert data["last_output"] == ["output line"]
    finally:
        (
            lifecycle_mod._project_dir,
            lifecycle_mod._current_task_label,
            lifecycle_mod._current_task_text,
            lifecycle_mod._current_phase,
            lifecycle_mod._phase_start_time,
        ) = orig


def test_save_interrupt_state_noop_when_no_project():
    """No-op when _project_dir is None."""
    orig = lifecycle_mod._project_dir
    try:
        lifecycle_mod._project_dir = None
        _save_interrupt_state()
    finally:
        lifecycle_mod._project_dir = orig


# ── _check_interrupted ──


def test_check_interrupted_no_file(tmp_path):
    """Returns None when no interrupted.json exists."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Task\n")
    result = _check_interrupted(tmp_path, plan)
    assert result is None


def test_check_interrupted_user_prompt_auto_retry(tmp_path):
    """Auto-retries for user_prompt phase."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {"phase": "user_prompt", "task_label": "1", "task_text": "t"}
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] t\n")
    result = _check_interrupted(tmp_path, plan)
    assert result == "retry"
    assert not (mcloop_dir / "interrupted.json").exists()


def test_check_interrupted_retry_on_r(tmp_path, monkeypatch):
    """Returns 'retry' when user chooses 'r'."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "phase": "task",
        "task_label": "1",
        "task_text": "t",
        "elapsed_seconds": 5,
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] t\n")
    monkeypatch.setattr("builtins.input", lambda _="": "r")
    result = _check_interrupted(tmp_path, plan)
    assert result == "retry"


def test_check_interrupted_skip_marks_active_file_not_master(tmp_path, monkeypatch):
    """Skip marks [!] in CURRENT_PLAN.md when task lives there, not master PLAN.md."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "phase": "task",
        "task_label": "1",
        "task_text": "Fix something",
        "elapsed_seconds": 5,
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    master = tmp_path / "PLAN.md"
    master.write_text("## Stage 1\n- [ ] Fix something\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Stage 1\n- [ ] Fix something\n")
    bugs = tmp_path / "BUGS.md"
    bugs.write_text("## Bugs\n\n")

    monkeypatch.setattr("builtins.input", lambda _="": "s")
    result = _check_interrupted(
        tmp_path,
        master,
        active_paths=[bugs, current, master],
    )
    assert result == "skip"
    assert "- [!] Fix something" in current.read_text()
    assert "- [!]" not in master.read_text()


def test_check_interrupted_skip_marks_bugs_over_current(tmp_path, monkeypatch):
    """Skip prefers BUGS.md when the task is unchecked there."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "phase": "task",
        "task_label": "B1",
        "task_text": "Crash on startup",
        "elapsed_seconds": 5,
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    master = tmp_path / "PLAN.md"
    master.write_text("## Stage 1\n- [ ] Other\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Stage 1\n- [ ] Other\n")
    bugs = tmp_path / "BUGS.md"
    bugs.write_text("## Bugs\n\n- [ ] Crash on startup\n")

    monkeypatch.setattr("builtins.input", lambda _="": "s")
    result = _check_interrupted(
        tmp_path,
        master,
        active_paths=[bugs, current, master],
    )
    assert result == "skip"
    assert "- [!] Crash on startup" in bugs.read_text()
    assert "- [!]" not in current.read_text()
    assert "- [!]" not in master.read_text()


def test_check_interrupted_d_writes_ruledout_to_active_file(tmp_path, monkeypatch):
    """Describe writes [RULEDOUT] under the task in CURRENT_PLAN.md, not master."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "phase": "task",
        "task_label": "1",
        "task_text": "Fix crash",
        "elapsed_seconds": 5,
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    master = tmp_path / "PLAN.md"
    master.write_text("## Stage 1\n- [ ] Fix crash\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Stage 1\n- [ ] Fix crash\n")

    inputs = iter(["d", "tried restarting", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    result = _check_interrupted(
        tmp_path,
        master,
        active_paths=[current, master],
    )
    assert result == "retry"
    assert "[RULEDOUT] tried restarting" in current.read_text()
    assert "[RULEDOUT]" not in master.read_text()


# ── register_signal_handlers ──


def test_register_signal_handlers_installs_all_signals():
    """Installs handlers for SIGINT, SIGTSTP, SIGTERM, and SIGHUP."""
    process_ref = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP):
            handler = signal.getsignal(sig)
            assert callable(handler)
            assert handler is not originals[sig]
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_register_signal_handlers_handler_sets_interrupted():
    """The installed handler sets _interrupted on the process_ref."""
    process_ref = MagicMock()
    process_ref._interrupted = False
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch("mcloop.lifecycle._save_interrupt_state"),
            patch("mcloop.lifecycle._graceful_kill_active_process"),
            patch("mcloop.lifecycle.os._exit"),
        ):
            handler(signal.SIGINT, None)
        assert process_ref._interrupted is True
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_register_signal_handlers_calls_cleanup_callback():
    """The handler invokes the cleanup callback before killing."""
    process_ref = MagicMock()
    cleanup = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref, cleanup_callback=cleanup)
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch("mcloop.lifecycle._save_interrupt_state"),
            patch("mcloop.lifecycle._graceful_kill_active_process"),
            patch("mcloop.lifecycle.os._exit"),
        ):
            handler(signal.SIGINT, None)
        cleanup.assert_called_once()
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_register_signal_handlers_no_cleanup_callback():
    """Works without a cleanup callback."""
    process_ref = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref, cleanup_callback=None)
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch("mcloop.lifecycle._save_interrupt_state"),
            patch("mcloop.lifecycle._graceful_kill_active_process"),
            patch("mcloop.lifecycle.os._exit"),
        ):
            handler(signal.SIGINT, None)
        # No exception raised — cleanup_callback=None is handled
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_register_signal_handlers_exits_with_130():
    """The handler exits with code 130."""
    process_ref = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch("mcloop.lifecycle._save_interrupt_state"),
            patch("mcloop.lifecycle._graceful_kill_active_process"),
            patch("mcloop.lifecycle.os._exit") as mock_exit,
        ):
            handler(signal.SIGINT, None)
        mock_exit.assert_called_once_with(130)
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_sigtstp_triggers_full_handler_flow():
    """SIGTSTP (Ctrl-Z) triggers save, cleanup, graceful kill, and exit."""
    process_ref = MagicMock()
    process_ref._interrupted = False
    cleanup = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref, cleanup_callback=cleanup)
        handler = signal.getsignal(signal.SIGTSTP)
        with (
            patch("mcloop.lifecycle._save_interrupt_state") as mock_save,
            patch(
                "mcloop.lifecycle._graceful_kill_active_process",
            ) as mock_kill,
            patch("mcloop.lifecycle.os._exit") as mock_exit,
        ):
            handler(signal.SIGTSTP, None)
        assert process_ref._interrupted is True
        mock_save.assert_called_once()
        cleanup.assert_called_once()
        mock_kill.assert_called_once()
        mock_exit.assert_called_once_with(130)
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_sigterm_triggers_full_handler_flow():
    """SIGTERM (kill) triggers save, cleanup, graceful kill, and exit."""
    process_ref = MagicMock()
    process_ref._interrupted = False
    cleanup = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref, cleanup_callback=cleanup)
        handler = signal.getsignal(signal.SIGTERM)
        with (
            patch("mcloop.lifecycle._save_interrupt_state") as mock_save,
            patch(
                "mcloop.lifecycle._graceful_kill_active_process",
            ) as mock_kill,
            patch("mcloop.lifecycle.os._exit") as mock_exit,
        ):
            handler(signal.SIGTERM, None)
        assert process_ref._interrupted is True
        mock_save.assert_called_once()
        cleanup.assert_called_once()
        mock_kill.assert_called_once()
        mock_exit.assert_called_once_with(130)
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_sighup_triggers_full_handler_flow():
    """SIGHUP triggers save, cleanup, graceful kill, and exit."""
    process_ref = MagicMock()
    process_ref._interrupted = False
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        handler = signal.getsignal(signal.SIGHUP)
        with (
            patch("mcloop.lifecycle._save_interrupt_state") as mock_save,
            patch(
                "mcloop.lifecycle._graceful_kill_active_process",
            ) as mock_kill,
            patch("mcloop.lifecycle.os._exit") as mock_exit,
        ):
            handler(signal.SIGHUP, None)
        assert process_ref._interrupted is True
        mock_save.assert_called_once()
        mock_kill.assert_called_once()
        mock_exit.assert_called_once_with(130)
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_handler_call_order_save_before_kill():
    """Handler saves interrupt state before killing the active process."""
    process_ref = MagicMock()
    call_order = []
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch(
                "mcloop.lifecycle._save_interrupt_state",
                side_effect=lambda: call_order.append("save"),
            ),
            patch(
                "mcloop.lifecycle._graceful_kill_active_process",
                side_effect=lambda: call_order.append("kill"),
            ),
            patch("mcloop.lifecycle.os._exit"),
        ):
            handler(signal.SIGINT, None)
        assert call_order == ["save", "kill"]
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_handler_call_order_cleanup_before_kill():
    """Handler calls cleanup callback after save but before kill."""
    process_ref = MagicMock()
    call_order = []
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(
            process_ref,
            cleanup_callback=lambda: call_order.append("cleanup"),
        )
        handler = signal.getsignal(signal.SIGINT)
        with (
            patch(
                "mcloop.lifecycle._save_interrupt_state",
                side_effect=lambda: call_order.append("save"),
            ),
            patch(
                "mcloop.lifecycle._graceful_kill_active_process",
                side_effect=lambda: call_order.append("kill"),
            ),
            patch("mcloop.lifecycle.os._exit"),
        ):
            handler(signal.SIGINT, None)
        assert call_order == ["save", "cleanup", "kill"]
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_all_signals_share_same_handler():
    """All four signals use the same handler function."""
    process_ref = MagicMock()
    originals = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
    }
    try:
        register_signal_handlers(process_ref)
        handlers = {
            sig: signal.getsignal(sig)
            for sig in (signal.SIGINT, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)
        }
        # All should be the same function object
        handler_set = set(id(h) for h in handlers.values())
        assert len(handler_set) == 1
    finally:
        for sig, orig in originals.items():
            signal.signal(sig, orig)


def test_graceful_kill_with_process_group_fallback():
    """Falls back to proc.terminate() when killpg raises OSError."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 88888
    mock_proc.wait.return_value = 0
    original = runner._active_process
    runner._active_process = mock_proc
    try:
        with (
            patch(
                "mcloop.lifecycle.os.getpgid",
                return_value=88888,
            ),
            patch(
                "mcloop.lifecycle.os.killpg",
                side_effect=ProcessLookupError,
            ),
        ):
            _graceful_kill_active_process()
            mock_proc.terminate.assert_called_once()
    finally:
        runner._active_process = original
