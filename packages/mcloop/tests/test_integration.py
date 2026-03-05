"""Integration tests. Exercise the full loop with mocked subprocesses."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from mcloop.checks import CheckResult
from mcloop.main import _checkpoint, run_loop
from mcloop.runner import RunResult


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


_CHECKS_PASS = CheckResult(passed=True, output="ok", command="true")


def _notify_calls(mock_notify):
    """Extract (message, level) pairs from notify mock calls."""
    result = []
    for c in mock_notify.call_args_list:
        msg = c.args[0]
        level = c.kwargs.get("level", c.args[1] if len(c.args) > 1 else "info")
        result.append((msg, level))
    return result


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_full_cycle_two_tasks(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_nested_subtasks(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Subtasks complete first, then parent auto-checks. No notification for parent."""
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_retry_then_succeed(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks")
@patch("mcloop.main.run_task")
def test_checks_fail_then_pass(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_max_retries_exhausted_stops_loop(
    mock_run, mock_checks, mock_commit, mock_checkpoint, mock_notify, tmp_path
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_rate_limit_notifies(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Rate limit detected, notifies warning, waits, then succeeds."""
    md = _make_project(tmp_path, "- [ ] Task\n")
    mock_run.side_effect = [
        _fail_run_result(output="rate limit exceeded", exit_code=1),
        _ok_run_result(),
    ]

    with patch("mcloop.main.wait_for_reset", return_value="claude"):
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_skips_already_checked_no_extra_notifications(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
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


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks")
@patch("mcloop.main.run_task")
def test_skip_when_working_tree_clean(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Task succeeds but working tree is clean: skip commit, check off, notify skipped."""
    md = _make_project(tmp_path, "- [ ] Already done task\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    mock_checks.assert_not_called()
    content = md.read_text()
    assert "- [x] Already done task" in content

    calls = _notify_calls(mock_notify)
    assert len(calls) == 2
    assert calls[0] == ("Skipped (clean): Already done task", "info")
    assert calls[1] == ("All tasks completed!", "info")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_all_done_noop(
    mock_run, mock_checks, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """All items already checked, loop exits immediately."""
    md = _make_project(tmp_path, "- [x] Done\n- [x] Also done\n")

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 0

    # Only the final "all done" notification
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


# --- _checkpoint unit tests ---


@patch("mcloop.main.subprocess.run")
def test_checkpoint_commits_when_dirty(mock_run, tmp_path):
    """_checkpoint stages and commits when tracked files are modified."""
    dirty_result = MagicMock()
    dirty_result.stdout = "src/foo.py\n"
    mock_run.return_value = dirty_result

    _checkpoint(tmp_path)

    assert mock_run.call_count == 3
    assert mock_run.call_args_list[0] == call(
        ["git", "diff", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mock_run.call_args_list[1] == call(
        ["git", "add", "-u"],
        cwd=tmp_path,
        capture_output=True,
    )
    assert mock_run.call_args_list[2] == call(
        ["git", "commit", "-m", "mcloop: checkpoint before run"],
        cwd=tmp_path,
        capture_output=True,
    )


@patch("mcloop.main.subprocess.run")
def test_checkpoint_skips_when_clean(mock_run, tmp_path):
    """_checkpoint does nothing when there are no tracked modified files."""
    clean_result = MagicMock()
    clean_result.stdout = ""
    mock_run.return_value = clean_result

    _checkpoint(tmp_path)

    assert mock_run.call_count == 1  # only the git diff check


@patch("mcloop.main.subprocess.run", side_effect=OSError("git not found"))
def test_checkpoint_ignores_errors(mock_run, tmp_path):
    """_checkpoint swallows exceptions and does not propagate them."""
    _checkpoint(tmp_path)  # should not raise


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_checkpoint_called_before_loop(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """run_loop calls _checkpoint exactly once before processing tasks."""
    md = _make_project(tmp_path, "- [ ] Task one\n")
    mock_run.return_value = _ok_run_result()

    run_loop(md)

    mock_checkpoint.assert_called_once_with(tmp_path)
