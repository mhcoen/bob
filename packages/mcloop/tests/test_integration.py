"""Integration tests. Exercise the full loop with mocked subprocesses."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from mcloop.checks import CheckResult
from mcloop.main import _checkpoint, _commit, _run_audit_fix_cycle, run_loop
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

    stuck = run_loop(md, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    assert mock_commit.call_count == 2

    content = md.read_text()
    assert "- [ ]" not in content
    assert content.count("- [x]") == 2

    # Only "All tasks completed" — no per-task notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


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

    stuck = run_loop(md, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    content = md.read_text()
    assert "- [ ]" not in content

    # Only "All tasks completed" — no per-task notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


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

    stuck = run_loop(md, max_retries=3, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    content = md.read_text()
    assert "- [x] Flaky task" in content

    # No per-retry or per-task notifications — only "All tasks completed"
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


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
        CheckResult(passed=True, output="ok", command="ruff check ."),  # end-of-run full suite
    ]

    stuck = run_loop(md, max_retries=3, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    assert mock_checks.call_count == 3

    # No per-retry or per-task notifications — only "All tasks completed"
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


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

    # Only "giving up" after all retries exhausted — no per-retry notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("Giving up on: Hopeless task", "error")


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
        stuck = run_loop(md, max_retries=3, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2

    # Rate-limit warning + all done (no per-task completion notification)
    calls = _notify_calls(mock_notify)
    assert len(calls) == 2
    assert calls[0][1] == "warning"
    assert "Rate-limited" in calls[0][0]
    assert calls[1] == ("All tasks completed!", "info")


@patch("mcloop.main.time.sleep")
@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_session_limit_polls_then_retries(
    mock_run,
    mock_checks,
    mock_meaningful,
    mock_commit,
    mock_checkpoint,
    mock_notify,
    mock_sleep,
    tmp_path,
):
    """Session limit triggers a 10-minute poll, then retries successfully."""
    md = _make_project(tmp_path, "- [ ] Task\n")
    mock_run.side_effect = [
        _fail_run_result(output="credit balance is too low", exit_code=1),
        _ok_run_result(),
    ]

    stuck = run_loop(md, max_retries=3, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    mock_sleep.assert_called_once_with(600)

    calls = _notify_calls(mock_notify)
    assert any("Polling every 10m" in msg for msg, _ in calls)
    # No "retrying" notification — session limit was already reported
    assert not any("Retrying" in msg for msg, _ in calls)
    assert calls[-1] == ("All tasks completed!", "info")


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

    stuck = run_loop(md, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 1

    # Only "All tasks completed" — no per-task notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks")
@patch("mcloop.main.run_task")
def test_noop_task_treated_as_failure(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Task succeeds but produces no file changes: treated as failure, marked [!]."""
    md = _make_project(tmp_path, "- [ ] Already done task\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md, max_retries=3)

    assert stuck == ["Already done task"]
    assert mock_run.call_count == 3
    mock_commit.assert_not_called()
    mock_checks.assert_not_called()
    content = md.read_text()
    assert "- [!] Already done task" in content

    calls = _notify_calls(mock_notify)
    # Only "giving up" — no per-retry notifications
    assert len(calls) == 1
    assert calls[0] == ("Giving up on: Already done task", "error")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_noop_then_changes_succeeds(
    mock_run, mock_checks, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Task produces no changes on first attempt but makes changes on retry: succeeds."""
    md = _make_project(tmp_path, "- [ ] Retry task\n")
    mock_run.return_value = _ok_run_result()

    # First attempt: no changes. Second attempt: changes present.
    with patch("mcloop.main._has_meaningful_changes", side_effect=[False, True]):
        stuck = run_loop(md, max_retries=3, no_audit=True)

    assert stuck == []
    assert mock_run.call_count == 2
    mock_commit.assert_called_once()
    content = md.read_text()
    assert "- [x] Retry task" in content

    # No per-retry or per-task notifications — only "All tasks completed"
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks")
@patch("mcloop.main.run_task")
def test_noop_with_max_retries_one(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """With max_retries=1, a single no-op attempt immediately marks task as failed."""
    md = _make_project(tmp_path, "- [ ] One-shot task\n")
    mock_run.return_value = _ok_run_result()

    stuck = run_loop(md, max_retries=1)

    assert stuck == ["One-shot task"]
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    content = md.read_text()
    assert "- [!] One-shot task" in content

    # Only "giving up" — no per-retry notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("Giving up on: One-shot task", "error")


# --- _commit unit tests ---


@patch("mcloop.main.subprocess.run")
def test_commit_stages_all_files(mock_run, tmp_path):
    """_commit uses git add -A to include new and tracked files."""
    (tmp_path / ".git").mkdir()
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "add", "-A"] in commands


@patch("mcloop.main.subprocess.run")
def test_commit_commits_with_task_message(mock_run, tmp_path):
    """_commit creates a commit with the task text in the message."""
    (tmp_path / ".git").mkdir()
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    _commit(tmp_path, "my task description")

    commit_calls = [c for c in mock_run.call_args_list if c.args[0][0:2] == ["git", "commit"]]
    assert len(commit_calls) == 1
    assert any("my task description" in arg for arg in commit_calls[0].args[0])


@patch("mcloop.main.subprocess.run")
def test_commit_pushes_after_commit(mock_run, tmp_path):
    """_commit calls git push after committing when a remote exists."""
    (tmp_path / ".git").mkdir()
    remote_result = MagicMock(returncode=0)
    remote_result.stdout = "origin\n"
    mock_run.return_value = remote_result

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] in commands
    # push must come after commit
    commit_idx = commands.index(["git", "commit", "-m", "Complete: some task"])
    push_idx = commands.index(["git", "push"])
    assert push_idx > commit_idx


@patch("mcloop.main.subprocess.run")
def test_commit_skips_push_when_no_remote(mock_run, tmp_path):
    """_commit skips git push silently when no remote is configured."""
    (tmp_path / ".git").mkdir()
    no_remote_result = MagicMock()
    no_remote_result.stdout = ""
    mock_run.return_value = no_remote_result

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] not in commands


@patch("mcloop.main.subprocess.run")
def test_commit_calls_gh_create_when_no_remote(mock_run, tmp_path):
    """_commit calls gh repo create when no remote is configured."""
    (tmp_path / ".git").mkdir()
    no_remote = MagicMock()
    no_remote.stdout = ""
    mock_run.return_value = no_remote

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    gh_calls = [c for c in commands if c and c[0] == "gh"]
    assert len(gh_calls) == 1
    assert gh_calls[0][:3] == ["gh", "repo", "create"]
    assert "--private" in gh_calls[0]


@patch("mcloop.main.subprocess.run")
def test_commit_pushes_after_gh_create_succeeds(mock_run, tmp_path):
    """_commit pushes after gh repo create adds a remote."""
    (tmp_path / ".git").mkdir()
    remote_call_count = {"n": 0}

    def side_effect(cmd, **kwargs):
        m = MagicMock(returncode=0)
        if cmd == ["git", "remote"]:
            remote_call_count["n"] += 1
            m.stdout = "" if remote_call_count["n"] == 1 else "origin\n"
        else:
            m.stdout = ""
        return m

    mock_run.side_effect = side_effect

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] in commands


@patch("mcloop.main.subprocess.run")
def test_commit_skips_push_when_gh_create_fails(mock_run, tmp_path):
    """_commit skips push when gh repo create fails to add a remote."""
    (tmp_path / ".git").mkdir()
    no_remote = MagicMock()
    no_remote.stdout = ""
    mock_run.return_value = no_remote

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] not in commands


@patch("mcloop.main.subprocess.run", side_effect=OSError("git not found"))
def test_commit_propagates_errors(mock_run, tmp_path):
    """_commit propagates exceptions from subprocess calls."""
    import pytest

    (tmp_path / ".git").mkdir()
    with pytest.raises(OSError):
        _commit(tmp_path, "task")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_all_done_noop(mock_run, mock_checks, mock_commit, mock_checkpoint, mock_notify, tmp_path):
    """All items already checked, loop exits immediately."""
    md = _make_project(tmp_path, "- [x] Done\n- [x] Also done\n")

    stuck = run_loop(md, no_audit=True)

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
    (tmp_path / ".git").mkdir()
    dirty_result = MagicMock(returncode=0)
    dirty_result.stdout = "src/foo.py\n"
    dirty_result.stderr = ""
    mock_run.return_value = dirty_result

    _checkpoint(tmp_path)

    assert mock_run.call_count == 5
    assert mock_run.call_args_list[0] == call(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mock_run.call_args_list[1] == call(
        ["git", "add", "-u"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mock_run.call_args_list[2] == call(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mock_run.call_args_list[3] == call(
        ["git", "add", "--", "src/foo.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mock_run.call_args_list[4] == call(
        ["git", "commit", "-m", "mcloop: checkpoint"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


@patch("mcloop.main.subprocess.run")
def test_checkpoint_skips_when_clean(mock_run, tmp_path):
    """_checkpoint does nothing when there are no tracked modified files."""
    (tmp_path / ".git").mkdir()
    clean_result = MagicMock(returncode=0)
    clean_result.stdout = ""
    clean_result.stderr = ""
    mock_run.return_value = clean_result

    _checkpoint(tmp_path)

    assert mock_run.call_count == 1  # only the git status check


@patch("mcloop.main.subprocess.run", side_effect=OSError("git not found"))
def test_checkpoint_propagates_errors(mock_run, tmp_path):
    """_checkpoint propagates exceptions from subprocess calls."""
    import pytest

    (tmp_path / ".git").mkdir()
    with pytest.raises(OSError):
        _checkpoint(tmp_path)


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_checkpoint_called_before_loop(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """run_loop calls _checkpoint at start and before each task."""
    md = _make_project(tmp_path, "- [ ] Task one\n")
    mock_run.return_value = _ok_run_result()

    run_loop(md, no_audit=True)

    # Called once at start (no next_task) and once before the task
    assert mock_checkpoint.call_count >= 1
    # First call is the initial checkpoint (with verbose=True at startup)
    assert mock_checkpoint.call_args_list[0] == call(tmp_path, verbose=True)


# --- Audit notification tests ---


@patch("mcloop.main.notify")
@patch("mcloop.main._save_audit_hash")
@patch("mcloop.main._should_skip_audit", return_value=False)
@patch("mcloop.main.run_audit")
def test_audit_notifies_no_bugs(mock_audit, mock_skip, mock_save, mock_notify, tmp_path):
    """Audit cycle notifies when no bugs are found."""
    mock_audit.return_value = _ok_run_result()
    # No BUGS.md written by audit session

    _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("Audit complete: no bugs found.", "info")


@patch("mcloop.main.notify")
@patch("mcloop.main._save_audit_hash")
@patch("mcloop.main._should_skip_audit", return_value=False)
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=True)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_post_fix_review")
@patch("mcloop.main.run_bug_fix")
@patch("mcloop.main.run_bug_verify")
@patch("mcloop.main.run_audit")
def test_audit_notifies_bugs_fixed(
    mock_audit,
    mock_verify,
    mock_fix,
    mock_review,
    mock_checks,
    mock_meaningful,
    mock_commit,
    mock_save,
    mock_skip,
    mock_notify,
    tmp_path,
):
    """Audit cycle notifies when bugs are found and fixed."""
    bugs_path = tmp_path / "BUGS.md"

    def write_bugs(*args, **kwargs):
        bugs_path.write_text("# Bugs\n\n## Bug 1\nSomething wrong\n")
        return _ok_run_result()

    mock_audit.side_effect = write_bugs
    mock_verify.return_value = _ok_run_result(output="CONFIRMED: Bug 1")
    mock_fix.return_value = _ok_run_result()
    mock_review.return_value = _ok_run_result(output="LGTM no problems")

    _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    calls = _notify_calls(mock_notify)
    assert any("Audit complete" in msg for msg, _ in calls)
