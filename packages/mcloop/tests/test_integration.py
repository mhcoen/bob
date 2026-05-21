"""Integration tests. Exercise the full loop with mocked subprocesses."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

from mcloop.audit import AuditResult, _run_audit_fix_cycle
from mcloop.checks import CheckResult
from mcloop.main import _checkpoint, _commit, run_loop
from mcloop.runner import RunResult
from tests.plan_fixtures import assert_canonical_checkbox, canonical_plan_text


def _make_project(tmp_path, checklist_text):
    """Set up a minimal project dir with a checklist file.

    Under the split-plan design, run_loop operates on CURRENT_PLAN.md
    (the active phase); PLAN.md is the master roadmap. We pre-create
    CURRENT_PLAN.md with the same content so tests can observe
    check-offs and failure markers directly.
    """
    md = tmp_path / "PLAN.md"
    md.write_text(canonical_plan_text(checklist_text))
    (tmp_path / "CURRENT_PLAN.md").write_text(canonical_plan_text(checklist_text))
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
    """Extract (message, level) pairs from notify mock calls.

    Filters out the startup "Starting: ..." notification so tests
    only assert on meaningful operational notifications.
    """
    result = []
    for c in mock_notify.call_args_list:
        msg = c.args[0]
        level = c.kwargs.get("level", c.args[1] if len(c.args) > 1 else "info")
        if msg.startswith("Starting:"):
            continue
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

    result = run_loop(md, no_audit=True)

    assert result.ok
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

    result = run_loop(md, no_audit=True)

    assert result.ok
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

    result = run_loop(md, max_retries=3, no_audit=True)

    assert result.ok
    assert mock_run.call_count == 2
    content = md.read_text()
    assert_canonical_checkbox(content, "x", "Flaky task")

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

    result = run_loop(md, max_retries=3, no_audit=True)

    assert result.ok
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

    result = run_loop(md, max_retries=3)

    assert not result.ok
    assert mock_run.call_count == 3
    # Under split-plan, task marker is written to CURRENT_PLAN.md (the active file)
    content = (md.parent / "CURRENT_PLAN.md").read_text()
    assert_canonical_checkbox(content, "!", "Hopeless task")
    assert_canonical_checkbox(content, " ", "Next task")

    # Only "giving up" after all retries exhausted — no per-retry notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] Hopeless task", "error")


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
        result = run_loop(md, max_retries=3, no_audit=True)

    assert result.ok
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

    result = run_loop(md, max_retries=3, no_audit=True)

    assert result.ok
    assert mock_run.call_count == 2
    assert mock_sleep.call_args_list.count(call(600)) == 1

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

    result = run_loop(md, no_audit=True)

    assert result.ok
    assert mock_run.call_count == 1

    # Only "All tasks completed" — no per-task notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert calls[0] == ("All tasks completed!", "info")


_CHECKS_FAIL = CheckResult(passed=False, output="FAILED", command="pytest")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_FAIL)
@patch("mcloop.main.run_task")
def test_noop_task_checks_fail_treated_as_failure(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """No file changes without acceptance evidence fails before global checks."""
    md = _make_project(tmp_path, "- [ ] Already done task\n")
    mock_run.return_value = _ok_run_result()

    result = run_loop(md, max_retries=3)

    assert not result.ok
    # No-op + checks fail is a terminal failure (no retry)
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    mock_checks.assert_not_called()
    content = md.read_text()
    assert_canonical_checkbox(content, " ", "Already done task")
    assert "- [x] T-" not in content

    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] Already done task", "error")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_noop_task_checks_pass_does_not_auto_check_without_acceptance_evidence(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """No file changes + global green is not task-specific acceptance evidence."""
    md = _make_project(tmp_path, "- [ ] Already done task\n")
    mock_run.return_value = _ok_run_result()

    result = run_loop(md, max_retries=3, no_audit=True)

    assert not result.ok
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    mock_checks.assert_not_called()
    content = md.read_text()
    assert_canonical_checkbox(content, " ", "Already done task")
    assert "- [x] T-" not in content

    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] Already done task", "error")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_noop_stage_gate_with_task_specific_evidence_is_success(
    mock_run,
    mock_checks,
    mock_meaningful,
    mock_commit,
    mock_checkpoint,
    mock_notify,
    tmp_path,
):
    """No file changes are valid when a verify task reports concrete evidence."""
    task_text = (
        "Verify Stage 13 gate: absent section, append, unchanged-TODO, "
        "reopen-DONE, reopen-FAILED, fix-key dedup, text-key dedup, "
        "id assignment, children preserved, field-stability rejection; "
        "ruff, ruff format, mypy strict, full pytest all green."
    )
    md = _make_project(tmp_path, f"- [ ] {task_text}\n")
    mock_run.return_value = _ok_run_result(
        output=(
            "Stage 13 gate verified - all four mandatory checks pass.\n"
            "`ruff check .` clean; `ruff format --check .` 40 files already formatted;\n"
            "pytest 626 passed / 2 skipped; `mypy .` no issues in 40 files.\n"
        )
    )

    result = run_loop(md, max_retries=3, no_audit=True)

    assert result.ok
    mock_run.assert_called_once()
    mock_commit.assert_not_called()
    mock_checks.assert_called_once_with(tmp_path)
    content = md.read_text()
    assert_canonical_checkbox(content, "x", task_text)

    calls = _notify_calls(mock_notify)
    assert calls == [("All tasks completed!", "info")]


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_bug_task_noop_is_failure_even_when_checks_pass(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Bug task + no file changes: failure even if checks pass.

    A BUGS.md entry asserts the code is broken; a session that exits
    without editing anything has not fixed it, regardless of whether
    checks happen to pass on the unchanged tree.
    """
    md = _make_project(tmp_path, "- [x] Already done plan task\n")
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n\n- [ ] Fix the thing\n"))
    mock_run.return_value = _ok_run_result()

    result = run_loop(md, max_retries=3)

    assert not result.ok
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    # Bug branch short-circuits before run_checks is called on the
    # no-change path. run_checks may still run elsewhere (e.g. not at
    # all here, since the loop exits on terminal failure), but must
    # never be invoked via the no-change code path for a bug task.
    assert mock_checks.call_count == 0

    bugs_content = (tmp_path / "BUGS.md").read_text()
    assert_canonical_checkbox(bugs_content, "!", "Fix the thing")

    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] Fix the thing", "error")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.main.run_task")
def test_single_task_noop_false_positive_does_not_check_off_without_acceptance_evidence(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """Defect A regression: single task no-op + global green does not check off."""
    md = _make_project(tmp_path, "- [ ] Already done plan task\n")
    mock_run.return_value = _ok_run_result()

    result = run_loop(md, max_retries=3, no_audit=True)

    assert not result.ok
    assert mock_run.call_count == 1
    mock_checks.assert_not_called()
    mock_commit.assert_not_called()
    content = md.read_text()
    assert_canonical_checkbox(content, " ", "Already done plan task")
    assert "- [x] T-" not in content


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main.run_task")
def test_noop_then_checks_fail_is_terminal(
    mock_run, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """No file changes + checks fail is terminal — no retry, marked [!]."""
    md = _make_project(tmp_path, "- [ ] Retry task\n")
    mock_run.return_value = _ok_run_result()

    # No changes, checks fail → terminal failure (no retry)
    with (
        patch("mcloop.main._has_meaningful_changes", return_value=False),
        patch("mcloop.main.run_checks", return_value=_CHECKS_FAIL),
    ):
        result = run_loop(md, max_retries=3, no_audit=True)

    assert not result.ok
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    content = (md.parent / "CURRENT_PLAN.md").read_text()
    assert_canonical_checkbox(content, "!", "Retry task")

    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] Retry task", "error")


@patch("mcloop.main.notify")
@patch("mcloop.main._checkpoint")
@patch("mcloop.main._commit")
@patch("mcloop.main._has_meaningful_changes", return_value=False)
@patch("mcloop.main.run_checks", return_value=_CHECKS_FAIL)
@patch("mcloop.main.run_task")
def test_noop_with_max_retries_one(
    mock_run, mock_checks, mock_meaningful, mock_commit, mock_checkpoint, mock_notify, tmp_path
):
    """With max_retries=1, no changes + checks fail immediately marks task as failed."""
    md = _make_project(tmp_path, "- [ ] One-shot task\n")
    mock_run.return_value = _ok_run_result()

    result = run_loop(md, max_retries=1)

    assert not result.ok
    assert mock_run.call_count == 1
    mock_commit.assert_not_called()
    content = (md.parent / "CURRENT_PLAN.md").read_text()
    assert_canonical_checkbox(content, "!", "One-shot task")

    # Only "giving up" — no per-retry notifications
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert calls[0] == ("Giving up on: [T-000001] One-shot task", "error")


# --- _commit unit tests ---


def _commit_side_effect(*args, **kwargs):
    """Mock subprocess.run for _commit tests.

    Returns returncode=1 for 'git diff --cached --quiet' (staged changes exist),
    returncode=0 for everything else.
    """
    cmd = args[0] if args else kwargs.get("args", [])
    if cmd[:3] == ["git", "diff", "--cached"]:
        return MagicMock(returncode=1, stdout="", stderr="")
    return MagicMock(returncode=0, stdout="", stderr="")


@patch("mcloop.main.subprocess.run", side_effect=_commit_side_effect)
def test_commit_filters_sensitive_files(mock_run, tmp_path):
    """_commit skips sensitive files like _checkpoint does."""
    (tmp_path / ".git").mkdir()

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "add", "-u"] in commands
    assert ["git", "add", "-A"] not in commands


@patch("mcloop.main.subprocess.run", side_effect=_commit_side_effect)
def test_commit_commits_with_task_message(mock_run, tmp_path):
    """_commit creates a commit with the task text in the message."""
    (tmp_path / ".git").mkdir()

    _commit(tmp_path, "my task description")

    commit_calls = [c for c in mock_run.call_args_list if c.args[0][0:2] == ["git", "commit"]]
    assert len(commit_calls) == 1
    assert any("my task description" in arg for arg in commit_calls[0].args[0])


@patch("mcloop.main.subprocess.run")
def test_commit_pushes_after_commit(mock_run, tmp_path):
    """_commit calls git push after committing when a remote exists."""
    (tmp_path / ".git").mkdir()

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd[:3] == ["git", "diff", "--cached"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        result = MagicMock(returncode=0, stderr="")
        result.stdout = "origin\n" if cmd == ["git", "remote"] else ""
        return result

    mock_run.side_effect = side_effect

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] in commands
    # push must come after commit
    commit_idx = next(i for i, c in enumerate(commands) if c[0:2] == ["git", "commit"])
    push_idx = commands.index(["git", "push"])
    assert push_idx > commit_idx


@patch("mcloop.main.subprocess.run")
def test_commit_skips_push_when_no_remote(mock_run, tmp_path):
    """_commit skips push when no remote is configured."""
    (tmp_path / ".git").mkdir()

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd[:3] == ["git", "diff", "--cached"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = side_effect

    _commit(tmp_path, "some task")

    commands = [call_args.args[0] for call_args in mock_run.call_args_list]
    assert ["git", "push"] not in commands


@patch("mcloop.main.subprocess.run")
def test_commit_raises_on_nothing_staged(mock_run, tmp_path):
    """_commit raises RuntimeError when nothing is staged."""
    import pytest

    (tmp_path / ".git").mkdir()
    # git diff --cached --quiet returns 0 = nothing staged
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="nothing to commit"):
        _commit(tmp_path, "task")


@patch("mcloop.main.subprocess.run")
def test_commit_raises_on_commit_failure(mock_run, tmp_path):
    """_commit raises RuntimeError when git commit fails."""
    import pytest

    (tmp_path / ".git").mkdir()

    def side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd[:3] == ["git", "diff", "--cached"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        if cmd[0:2] == ["git", "commit"]:
            return MagicMock(returncode=1, stdout="", stderr="commit failed")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = side_effect

    with pytest.raises(RuntimeError, match="git commit failed"):
        _commit(tmp_path, "task")


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

    result = run_loop(md, no_audit=True)

    assert result.ok
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


@patch("mcloop.audit.notify")
@patch("mcloop.audit._save_audit_hash")
@patch("mcloop.audit._should_skip_audit", return_value=False)
@patch("mcloop.audit.run_audit")
def test_audit_notifies_no_bugs(mock_audit, mock_skip, mock_save, mock_notify, tmp_path):
    """Audit cycle sends failure notification when BUGS.md not produced."""
    mock_audit.return_value = _ok_run_result()
    # No BUGS.md written by audit session → treated as failed

    result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.failed
    calls = _notify_calls(mock_notify)
    assert len(calls) == 1
    assert "failed" in calls[0][0].lower()
    mock_save.assert_not_called()


@patch("mcloop.audit.notify")
@patch("mcloop.audit._save_audit_hash")
@patch("mcloop.audit._should_skip_audit", return_value=False)
@patch("mcloop.audit._commit")
@patch("mcloop.audit._has_meaningful_changes", return_value=True)
@patch("mcloop.audit.run_checks", return_value=_CHECKS_PASS)
@patch("mcloop.audit.run_post_fix_review")
@patch("mcloop.audit.run_bug_fix")
@patch("mcloop.audit.run_bug_verify")
@patch("mcloop.audit.run_audit")
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
    # Audit output file moved from BUGS.md to .mcloop/audit-report.md so it
    # does not collide with the standalone BUGS.md bug-backlog checklist.
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def write_bugs(*args, **kwargs):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text("# Bugs\n\n## Bug 1\nSomething wrong\n")
        return _ok_run_result()

    mock_audit.side_effect = write_bugs
    mock_verify.return_value = _ok_run_result(output="CONFIRMED: Bug 1")
    mock_fix.return_value = _ok_run_result()
    mock_review.return_value = _ok_run_result(output="LGTM no problems")

    _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    calls = _notify_calls(mock_notify)
    assert any("Audit complete" in msg for msg, _ in calls)
