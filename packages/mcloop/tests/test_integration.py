"""Integration tests — exercise the full loop with mocked subprocesses."""

from pathlib import Path
from unittest.mock import patch

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

    # Both items should be checked off
    content = md.read_text()
    assert "- [ ]" not in content
    assert content.count("- [x]") == 2


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_nested_subtasks(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Subtasks complete first, then parent auto-checks."""
    md = _make_project(
        tmp_path,
        "- [ ] Parent\n  - [ ] Child A\n  - [ ] Child B\n",
    )
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    # Two CLI runs for the two children, parent auto-checks
    assert mock_run.call_count == 2
    content = md.read_text()
    assert "- [ ]" not in content


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


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_max_retries_exhausted_stops_loop(
    mock_run, mock_checks, mock_commit, mock_notify, tmp_path
):
    """Task fails all retries — marked [!] and loop stops."""
    md = _make_project(tmp_path, "- [ ] Hopeless task\n- [ ] Next task\n")
    mock_run.return_value = _fail_run_result()

    stuck = run_loop(md, max_retries=3)

    assert stuck == ["Hopeless task"]
    assert mock_run.call_count == 3  # no attempt on second task
    content = md.read_text()
    assert "- [!] Hopeless task" in content
    assert "- [ ] Next task" in content  # untouched


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_rate_limit_waits(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Rate limit detected — waits then retries."""
    md = _make_project(tmp_path, "- [ ] Task\n")
    mock_run.side_effect = [
        _fail_run_result(output="rate limit exceeded", exit_code=1),
        _ok_run_result(),
    ]

    with patch("loop.main.wait_for_reset", return_value="claude"):
        stuck = run_loop(md, max_retries=3)

    assert stuck == []
    assert mock_run.call_count == 2


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_skips_already_checked(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Already-checked items are skipped."""
    md = _make_project(tmp_path, "- [x] Done already\n- [ ] Still todo\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 1  # only the unchecked one


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_all_done_noop(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """All items already checked — loop exits immediately."""
    md = _make_project(tmp_path, "- [x] Done\n- [x] Also done\n")

    stuck = run_loop(md)

    assert stuck == []
    assert mock_run.call_count == 0


@patch("loop.main.notify")
@patch("loop.main._commit")
@patch("loop.main.run_checks", return_value=CheckResult(passed=True, output="ok", command="true"))
@patch("loop.main.run_task")
def test_notifications_sent(mock_run, mock_checks, mock_commit, mock_notify, tmp_path):
    """Verify notification calls for success and completion."""
    md = _make_project(tmp_path, "- [ ] Only task\n")
    mock_run.return_value = _ok_run_result()

    run_loop(md)

    messages = [call.args[0] for call in mock_notify.call_args_list]
    assert any("Completed" in m for m in messages)
    assert any("All tasks completed" in m for m in messages)
