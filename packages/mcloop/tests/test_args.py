"""Unit tests for CLI argument parsing and main helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcloop.main import (
    _find_recent_crash_report,
    _parse_args,
    _run_audit_fix_cycle,
    _run_single_audit_round,
    gather_bug_context,
    run_loop,
)


def _parse(*argv):
    with patch("sys.argv", ["mcloop", *argv]):
        return _parse_args()


def test_defaults():
    args = _parse()
    assert args.file == "PLAN.md"
    assert args.dry_run is False
    assert args.max_retries == 3
    assert args.model is None
    assert args.command is None
    assert args.no_audit is False


def test_no_audit_flag():
    args = _parse("--no-audit")
    assert args.no_audit is True


def test_file_flag():
    args = _parse("--file", "tasks.md")
    assert args.file == "tasks.md"


def test_dry_run_flag():
    args = _parse("--dry-run")
    assert args.dry_run is True


def test_max_retries_flag():
    args = _parse("--max-retries", "5")
    assert args.max_retries == 5


def test_model_flag():
    args = _parse("--model", "opus")
    assert args.model == "opus"


def test_sync_subcommand():
    args = _parse("sync")
    assert args.command == "sync"


def test_sync_subcommand_with_file():
    args = _parse("--file", "custom.md", "sync")
    assert args.command == "sync"
    assert args.file == "custom.md"


def test_audit_subcommand():
    args = _parse("audit")
    assert args.command == "audit"


def test_audit_subcommand_with_file():
    args = _parse("--file", "custom.md", "audit")
    assert args.command == "audit"
    assert args.file == "custom.md"


def test_no_subcommand_command_is_none():
    args = _parse("--dry-run")
    assert args.command is None


def test_investigate_subcommand():
    args = _parse("investigate")
    assert args.command == "investigate"
    assert args.description is None
    assert args.log is None


def test_investigate_with_description():
    args = _parse("investigate", "app crashes on startup")
    assert args.command == "investigate"
    assert args.description == "app crashes on startup"


def test_investigate_with_log():
    args = _parse("investigate", "--log", "/tmp/crash.log")
    assert args.command == "investigate"
    assert args.log == "/tmp/crash.log"
    assert args.description is None


def test_investigate_with_description_and_log():
    args = _parse("investigate", "segfault in parser", "--log", "err.txt")
    assert args.command == "investigate"
    assert args.description == "segfault in parser"
    assert args.log == "err.txt"


def test_invalid_subcommand_exits():
    with pytest.raises(SystemExit):
        _parse("bogus")


# --- _run_audit_fix_cycle ---


def _make_result(success=True, exit_code=0):
    r = MagicMock()
    r.success = success
    r.exit_code = exit_code
    return r


def test_run_audit_fix_cycle_no_bugs(tmp_path):
    """When audit writes 'No bugs found.', fix session is not run."""
    bugs_path = tmp_path / "BUGS.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with (
        patch("mcloop.main.run_audit", side_effect=fake_audit) as mock_audit,
        patch("mcloop.main.run_bug_fix") as mock_fix,
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_audit.assert_called_once()
    mock_fix.assert_not_called()
    assert not bugs_path.exists()


def test_run_audit_fix_cycle_with_bugs(tmp_path):
    """When audit finds bugs, fix session runs."""
    bugs_path = tmp_path / "BUGS.md"
    bug_content = "# Bugs\n\n## foo.py:1 — crash\n**Severity**: high\nBad.\n"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.write_text(bug_content)
        return _make_result()

    with (
        patch("mcloop.main.run_audit", side_effect=fake_audit),
        patch("mcloop.main.run_bug_fix", return_value=_make_result()) as mock_fix,
        patch("mcloop.main._has_meaningful_changes", return_value=False),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_called_once()


def test_run_audit_fix_cycle_audit_failure(tmp_path):
    """When audit session fails, fix session is not run."""
    with (
        patch("mcloop.main.run_audit", return_value=_make_result(success=False, exit_code=1)),
        patch("mcloop.main.run_bug_fix") as mock_fix,
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_not_called()


def test_run_audit_fix_cycle_no_bugs_md(tmp_path):
    """When audit succeeds but BUGS.md not written, fix session is not run."""
    with (
        patch("mcloop.main.run_audit", return_value=_make_result()),
        patch("mcloop.main.run_bug_fix") as mock_fix,
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_not_called()


def test_run_loop_no_audit_skips_audit(tmp_path):
    """When no_audit=True, _run_audit_fix_cycle is not called."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=True)

    mock_audit.assert_not_called()


def test_run_loop_audit_called_by_default(tmp_path):
    """By default, _run_audit_fix_cycle is called after all tasks complete."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=False)

    mock_audit.assert_called_once()


def test_single_audit_round_commits_when_checks_pass(tmp_path):
    """When fix session succeeds and checks pass, changes are committed."""
    bugs_path = tmp_path / "BUGS.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.write_text("# Bugs\n\n## foo.py:1 — crash\nBad.\n")
        return _make_result()

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main.run_audit", side_effect=fake_audit),
        patch("mcloop.main.run_bug_fix", return_value=_make_result()),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit") as mock_commit,
    ):
        _run_single_audit_round(tmp_path, tmp_path / "logs")

    mock_commit.assert_called_once_with(tmp_path, "Fix bugs from audit")


def test_audit_cycle_runs_two_rounds_when_first_fixes(tmp_path):
    """When the first round fixes bugs, a second round runs."""
    call_count = 0

    def fake_round(project_dir, log_dir, model=None):
        nonlocal call_count
        call_count += 1
        # First round finds and fixes bugs, second round finds nothing
        return call_count == 1

    with (
        patch("mcloop.main._should_skip_audit", return_value=False),
        patch(
            "mcloop.main._run_single_audit_round",
            side_effect=fake_round,
        ),
        patch("mcloop.main._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert call_count == 2


def test_audit_cycle_stops_after_one_round_when_no_fixes(tmp_path):
    """When the first round finds no bugs, second round is skipped."""
    with (
        patch("mcloop.main._should_skip_audit", return_value=False),
        patch(
            "mcloop.main._run_single_audit_round",
            return_value=False,
        ) as mock_round,
        patch("mcloop.main._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_round.assert_called_once()


def test_audit_cycle_caps_at_two_rounds(tmp_path):
    """Even if both rounds fix bugs, it stops at two."""
    with (
        patch("mcloop.main._should_skip_audit", return_value=False),
        patch(
            "mcloop.main._run_single_audit_round",
            return_value=True,
        ) as mock_round,
        patch("mcloop.main._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert mock_round.call_count == 2


def test_audit_cycle_saves_hash_after_completion(tmp_path):
    """Audit hash is saved after both rounds complete."""
    with (
        patch("mcloop.main._should_skip_audit", return_value=False),
        patch(
            "mcloop.main._run_single_audit_round",
            return_value=False,
        ),
        patch("mcloop.main._save_audit_hash") as mock_save,
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_save.assert_called_once_with(tmp_path)


def test_single_audit_round_returns_true_on_fix(tmp_path):
    """_run_single_audit_round returns True when bugs are fixed."""
    bugs_path = tmp_path / "BUGS.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.write_text("# Bugs\n\n## foo.py:1 — crash\nBad.\n")
        return _make_result()

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main.run_audit", side_effect=fake_audit),
        patch("mcloop.main.run_bug_fix", return_value=_make_result()),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
    ):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is True


def test_single_audit_round_returns_false_on_no_bugs(tmp_path):
    """_run_single_audit_round returns False when no bugs found."""

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        (tmp_path / "BUGS.md").write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with patch("mcloop.main.run_audit", side_effect=fake_audit):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is False


# --- _find_recent_crash_report ---


def test_find_recent_crash_report_no_dir(tmp_path):
    """Returns empty string when DiagnosticReports dir doesn't exist."""
    with patch("mcloop.main.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == ""


def test_find_recent_crash_report_no_recent(tmp_path):
    """Returns empty string when no .ips files are recent enough."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    old_file = reports_dir / "MyApp-2024-01-01.ips"
    old_file.write_text("old crash")
    import os

    os.utime(old_file, (0, 0))  # very old mtime
    with patch("mcloop.main.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == ""


def test_find_recent_crash_report_finds_newest(tmp_path):
    """Returns contents of the newest recent .ips file."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "OldApp.ips").write_text("old crash")
    (reports_dir / "NewApp.ips").write_text("new crash")
    # Both are recent (just created), newest by mtime wins
    with patch("mcloop.main.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == "new crash"


def test_find_recent_crash_report_ignores_non_ips(tmp_path):
    """Ignores non-.ips files."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "crash.log").write_text("not ips")
    with patch("mcloop.main.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == ""


# --- gather_bug_context ---


def test_gather_bug_context_description_only(tmp_path):
    """Description is set from the argument."""
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, description="app crashes")
    assert ctx.user_description == "app crashes"
    assert ctx.crash_report == ""
    assert ctx.failure_history == ""


def test_gather_bug_context_log_file(tmp_path):
    """Reads the --log file into failure_history."""
    log_file = tmp_path / "error.log"
    log_file.write_text("Traceback: something broke\n")
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, log_path=str(log_file))
    assert "Traceback: something broke" in ctx.failure_history
    assert "From " in ctx.failure_history


def test_gather_bug_context_stdin(tmp_path):
    """Piped stdin text is included in failure_history."""
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, stdin_text="error from pipe\n")
    assert "error from pipe" in ctx.failure_history
    assert "From stdin:" in ctx.failure_history


def test_gather_bug_context_last_run_log(tmp_path):
    """Reads .mcloop/last-run.log into failure_history."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "last-run.log").write_text("previous run failed here\n")
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path)
    assert "previous run failed here" in ctx.failure_history
    assert "From last-run.log:" in ctx.failure_history


def test_gather_bug_context_crash_report(tmp_path):
    """Picks up crash report from DiagnosticReports."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "MyCrash.ips").write_text("crash data here")
    with (
        patch("mcloop.main.Path.home", return_value=tmp_path),
        patch("mcloop.main.detect_app_type", return_value=""),
    ):
        ctx = gather_bug_context(tmp_path)
    assert ctx.crash_report == "crash data here"


def test_gather_bug_context_app_type(tmp_path):
    """Populates app_type from detect_app_type."""
    with patch("mcloop.main.detect_app_type", return_value="gui"):
        ctx = gather_bug_context(tmp_path)
    assert ctx.app_type == "gui"


def test_gather_bug_context_all_sources(tmp_path):
    """All sources combined into a single BugContext."""
    # Setup crash report
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "App.ips").write_text("crash info")

    # Setup last-run.log
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "last-run.log").write_text("last run output")

    # Setup --log file
    log_file = tmp_path / "my.log"
    log_file.write_text("log file output")

    with (
        patch("mcloop.main.Path.home", return_value=tmp_path),
        patch("mcloop.main.detect_app_type", return_value="cli"),
    ):
        ctx = gather_bug_context(
            tmp_path,
            description="segfault",
            log_path=str(log_file),
            stdin_text="piped text",
        )

    assert ctx.user_description == "segfault"
    assert ctx.crash_report == "crash info"
    assert ctx.app_type == "cli"
    assert "log file output" in ctx.failure_history
    assert "piped text" in ctx.failure_history
    assert "last run output" in ctx.failure_history


def test_gather_bug_context_empty_stdin_ignored(tmp_path):
    """Empty or whitespace-only stdin is not included."""
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, stdin_text="   \n  ")
    assert ctx.failure_history == ""


def test_gather_bug_context_missing_log_file(tmp_path):
    """Non-existent --log file is silently skipped."""
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, log_path=str(tmp_path / "nonexistent.log"))
    assert ctx.failure_history == ""


def test_gather_bug_context_no_description_is_empty(tmp_path):
    """When description is None, user_description is empty string."""
    with patch("mcloop.main.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, description=None)
    assert ctx.user_description == ""


# --- investigate worktree creation ---


def test_investigate_creates_worktree(tmp_path, capsys):
    """investigate creates a new worktree when none exists."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = Path("/fake/repo-investigate-app-crashes")

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "app crashes"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context") as mock_gather,
        patch("mcloop.main.worktree.create") as mock_create,
    ):
        mock_stdin.isatty.return_value = True
        mock_gather.return_value = MagicMock(
            user_description="app crashes",
            crash_report="",
            failure_history="",
            app_type="",
        )
        mock_create.return_value = (wt_path, "investigate-app-crashes", False)

        from mcloop.main import main

        main()

    mock_create.assert_called_once_with("app crashes", cwd=tmp_path)
    captured = capsys.readouterr()
    assert "Created investigation worktree" in captured.err
    assert "investigate-app-crashes" in captured.err


def test_investigate_resumes_existing_worktree(tmp_path, capsys):
    """investigate resumes an existing worktree instead of creating new."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = Path("/fake/repo-investigate-segfault")

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "segfault"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context") as mock_gather,
        patch("mcloop.main.worktree.create") as mock_create,
    ):
        mock_stdin.isatty.return_value = True
        mock_gather.return_value = MagicMock(
            user_description="segfault",
            crash_report="",
            failure_history="",
            app_type="",
        )
        mock_create.return_value = (wt_path, "investigate-segfault", True)

        from mcloop.main import main

        main()

    captured = capsys.readouterr()
    assert "Resuming investigation" in captured.err
    assert "investigate-segfault" in captured.err


def test_investigate_no_description_uses_fallback(tmp_path):
    """When no description is provided, uses 'investigation' as fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context") as mock_gather,
        patch("mcloop.main.worktree.create") as mock_create,
    ):
        mock_stdin.isatty.return_value = True
        mock_gather.return_value = MagicMock(
            user_description="",
            crash_report="",
            failure_history="",
            app_type="",
        )
        mock_create.return_value = (
            Path("/fake/repo-investigate-investigation"),
            "investigate-investigation",
            False,
        )

        from mcloop.main import main

        main()

    mock_create.assert_called_once_with("investigation", cwd=tmp_path)


def test_investigate_worktree_error_exits(tmp_path):
    """When worktree creation fails, exits with error message."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "bug"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context") as mock_gather,
        patch("mcloop.main.worktree.create") as mock_create,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stdin.isatty.return_value = True
        mock_gather.return_value = MagicMock(
            user_description="bug",
            crash_report="",
            failure_history="",
            app_type="",
        )
        mock_create.side_effect = RuntimeError("branch already exists")

        from mcloop.main import main

        main()

    assert exc_info.value.code == 1
