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


def test_kill_orphan_alive_process(tmp_path):
    """Kills alive orphan process group."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    pid_file = mcloop_dir / "active-pid"
    pid_file.write_text("12345 12345")
    with (
        patch("mcloop.lifecycle.os.kill"),
        patch("mcloop.lifecycle.os.killpg") as mock_killpg,
    ):
        _kill_orphan_sessions(tmp_path)
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
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
