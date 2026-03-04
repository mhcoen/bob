"""Integration tests. Exercise the full loop with mocked subprocesses."""

from pathlib import Path
from unittest.mock import call, patch

from loop.checks import CheckResult
from loop.main import run_loop
from loop.runner import RunResult


def _make_project(tmp_path, checklist_text):
    """Set up a minimal project dir with a checklist file."""
    md = tmp_path / "PLAN.md"
    md.write_text(checklist_text)
    (tmp_path / "logs").mkdir()
    return md


def _ok_run_result(**overrides):
    defaults = dict(success=True, output="done", exit_code=0, log_path=Path("/dev/null"))
    defaults.update(overrides)
    return RunResult(**defaults)


def _fail_run_result(**overrides):
    defaults = dict(success=False, output="error", exit_code=1, log_path=Path("/dev/null"))
    defaults.update(overrides)
    return RunResult(**defaults)


def _notify_calls(mock_notify):
    """Extract (message, level) pairs from notify mock calls."""
    result = []
    for c in mock_notify.call_args_list:
        msg = c.args[0]
        level = c.kwargs.get("level", c.args[1] if len(c.args) > 1 else "info")
        result.append((msg, level))
    return result


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_full_cycle_two_tasks(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Two simple tasks both succeed on first attempt."""
    md = _make_project(tmp_path, "- [ ] Task one\n- [ ] Task two\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 2
    assert mock_commit.call_count == 2

    content = md.read_text()
    assert "- [ ]" not in content
    assert content.count("- [x]") == 2

    # Notifications: one "Completed" per task + "All tasks completed"
    calls = _notify_calls(mock_notify)
    assert len(calls) == 3
    assert calls[0] == ("Completed: Task one", "info")
    assert calls[1] == ("Completed: Task two", "info")
    assert calls[2] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_nested_subtasks(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Subtasks complete first, then parent auto-checks. No notification for auto-checked parent."""
    md = _make_project(
        tmp_path,
        "- [ ] Parent\n  - [ ] Child A\n  - [ ] Child B\n",
    )
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 2
    content = md.read_text()
    assert "- [ ]" not in content

    # Notifications: one per child + all done. Parent auto-check has no notification.
    calls = _notify_calls(mock_notify)
    assert len(calls) == 3
    assert calls[0] == ("Completed: Child A", "info")
    assert calls[1] == ("Completed: Child B", "info")
    assert calls[2] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_retry_then_succeed(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Task fails once then succeeds on retry."""
    md = _make_project(tmp_path, "- [ ] Flaky task\n")
    mock_run.side_effect = [_fail_run_result(), _ok_run_result()]

    stuck = run_loop(md, max_retries=3)

    assert stuck == []
    assert mock_run.call_count == 2
    content = md.read_text()
    assert "- [x] Flaky task" in content

    # Notifications: one failure + one completion + all done
    calls = _notify_calls(mock_notify)
    assert len(calls) == 3
    assert calls[0] == ("Task failed (attempt 1/3): Flaky task", "error")
    assert calls[1] == ("Completed: Flaky task", "info")
    assert calls[2] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks")
@patch("loop.main.run_task")
def test_checks_fail_then_pass(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """CLI succeeds but checks fail on first attempt, pass on second."""
    md = _make_project(tmp_path, "- [ ] Needs fixing\n")
    mock_run.return_value = _ok_run_result()
    mock_checks.side_effect = [
        CheckResult(passed=False, output="lint error", command="ruff check ."),
        CheckResult(passed=True, output="ok", command="ruff check ."),
    ]

    stuck = run_loop(md, max_retries=3)

    assert stuck == []
    assert mock_run.call_count == 2
    assert mock_checks.call_count == 2

    # Notifications: one check failure + one completion + all done
    calls = _notify_calls(mock_notify)
    assert len(calls) == 3
    assert calls[0] == ("Checks failed (attempt 1/3): Needs fixing", "error")
    assert calls[1] == ("Completed: Needs fixing", "info")
    assert calls[2] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_max_retries_exhausted_stops_loop(
    mock_run, mock_checks, mock_commit, mock_notify, tmp_path
):
    """Task fails all retries, marked [!] and loop stops."""
    md = _make_project(tmp_path, "- [ ] Hopeless task\n- [ ] Next task\n")
    mock_run.return_value = _fail_run_result()

    stuck = run_loop(md, max_retries=3)

    assert stuck == ["Hopeless task"]
    assert mock_run.call_count == 3
    content = md.read_text()
    assert "- [!] Hopeless task" in content
    assert "- [ ] Next task" in content

    # Notifications: one error per attempt + giving up. No "all done".
    calls = _notify_calls(mock_notify)
    assert len(calls) == 4
    assert calls[0] == ("Task failed (attempt 1/3): Hopeless task", "error")
    assert calls[1] == ("Task failed (attempt 2/3): Hopeless task", "error")
    assert calls[2] == ("Task failed (attempt 3/3): Hopeless task", "error")
    assert calls[3] == ("Giving up on: Hopeless task", "error")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_rate_limit_notifies(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Rate limit detected, notifies warning, waits, then succeeds."""
    md = _make_project(tmp_path, "- [ ] Task\n")
    mock_run.side_effect = [
        _fail_run_result(output="rate limit exceeded", exit_code=1),
        _ok_run_result(),
    ]

    with patch("loop.main.wait_for_reset", return_value="claude"):
        stuck = run_loop(md, max_retries=3)

    assert stuck == []
    assert mock_run.call_count == 2

    # Notifications: rate-limit warning + completed + all done
    calls = _notify_calls(mock_notify)
    assert len(calls) == 3
    assert calls[0][1] == "warning"
    assert "Rate-limited" in calls[0][0]
    assert calls[1] == ("Completed: Task", "info")
    assert calls[2] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_skips_already_checked_no_extra_notifications(
    mock_run, mock_checks, mock_commit, mock_notify, tmp_path
):
    """Already-checked items are skipped. No notifications for them."""
    md = _make_project(tmp_path, "- [x] Done already\n- [ ] Still todo\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 1

    # Only notifications for the one task that ran + all done
    calls = _notify_calls(mock_notify)
    assert len(calls) == 2
    assert calls[0] == ("Completed: Still todo", "info")
    assert calls[1] == ("All tasks completed!", "info")


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_all_done_noop(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """All items already checked, loop exits immediately."""
    md = _make_project(tmp_path, "- [x] Done\n- [x] Also done\n")

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 0

    # Only the final "all done" notification
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")
