"""Unit tests for CLI argument parsing and main helpers."""

from unittest.mock import MagicMock, patch

import pytest

from mcloop.main import (
    _copy_project_settings,
    _find_recent_crash_report,
    _handle_user_task,
    _investigation_failed,
    _investigation_passed,
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
    """investigate creates a new worktree and runs mcloop subprocess."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="app crashes")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "app crashes"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.main._investigation_passed") as mock_passed,
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-app-crashes", False)

        from mcloop.main import main

        main()

    mock_create.assert_called_once_with("app crashes", cwd=tmp_path)
    # Verify subprocess was called with --no-audit in the worktree
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "--no-audit" in cmd
    assert mock_run.call_args[1]["cwd"] == str(wt_path)
    mock_passed.assert_called_once_with(wt_path, "investigate-app-crashes", tmp_path)
    captured = capsys.readouterr()
    assert "Created investigation worktree" in captured.err
    assert "investigate-app-crashes" in captured.err
    # PLAN.md should be generated in the worktree
    assert (wt_path / "PLAN.md").exists()
    plan_text = (wt_path / "PLAN.md").read_text()
    assert "Investigation Plan" in plan_text


def test_investigate_resumes_existing_worktree(tmp_path, capsys):
    """investigate resumes an existing worktree and runs mcloop subprocess."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="segfault")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "segfault"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.main._investigation_passed") as mock_passed,
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-segfault", True)

        from mcloop.main import main

        main()

    mock_run.assert_called_once()
    assert mock_run.call_args[1]["cwd"] == str(wt_path)
    mock_passed.assert_called_once()
    captured = capsys.readouterr()
    assert "Resuming investigation" in captured.err
    assert "investigate-segfault" in captured.err


def test_investigate_no_description_uses_fallback(tmp_path):
    """When no description is provided, uses 'investigation' as fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext()
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (
            wt_path,
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


# --- _copy_project_settings ---


def test_copy_project_settings_mcloop_json(tmp_path):
    """Copies mcloop.json when it exists."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "mcloop.json").write_text('{"checks": ["pytest"]}')

    _copy_project_settings(src, dst)

    assert (dst / "mcloop.json").exists()
    assert (dst / "mcloop.json").read_text() == '{"checks": ["pytest"]}'


def test_copy_project_settings_claude_dir(tmp_path):
    """Copies .claude/ directory when it exists."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    claude_dir = src / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text('{"key": "val"}')

    _copy_project_settings(src, dst)

    assert (dst / ".claude" / "settings.json").exists()
    assert (dst / ".claude" / "settings.json").read_text() == '{"key": "val"}'


def test_copy_project_settings_nothing_to_copy(tmp_path):
    """No error when neither mcloop.json nor .claude/ exist."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    _copy_project_settings(src, dst)

    assert not (dst / "mcloop.json").exists()
    assert not (dst / ".claude").exists()


def test_copy_project_settings_replaces_existing_claude_dir(tmp_path):
    """Existing .claude/ in dst is replaced."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / ".claude").mkdir()
    (src / ".claude" / "new.json").write_text("new")
    (dst / ".claude").mkdir()
    (dst / ".claude" / "old.json").write_text("old")

    _copy_project_settings(src, dst)

    assert (dst / ".claude" / "new.json").exists()
    assert not (dst / ".claude" / "old.json").exists()


# --- investigate plan generation ---


def test_investigate_generates_plan_with_context(tmp_path, capsys):
    """New investigation generates PLAN.md with bug context."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(
        user_description="crash on save",
        crash_report="EXC_BAD_ACCESS",
        app_type="gui",
    )
    mock_result = MagicMock(returncode=0)

    with (
        patch(
            "sys.argv",
            ["mcloop", "--file", str(plan), "investigate", "crash on save"],
        ),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-crash-on-save", False)

        from mcloop.main import main

        main()

    plan_text = (wt_path / "PLAN.md").read_text()
    assert "Investigation Plan" in plan_text
    assert "crash on save" in plan_text
    assert "EXC_BAD_ACCESS" in plan_text
    captured = capsys.readouterr()
    assert "generated PLAN.md" in captured.err


def test_investigate_resume_does_not_overwrite_plan(tmp_path, capsys):
    """Resuming does not regenerate PLAN.md or copy settings."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    existing_plan = wt_path / "PLAN.md"
    existing_plan.write_text("# Existing plan\n")

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="segfault")
    mock_result = MagicMock(returncode=0)

    with (
        patch(
            "sys.argv",
            ["mcloop", "--file", str(plan), "investigate", "segfault"],
        ),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings") as mock_copy,
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-segfault", True)

        from mcloop.main import main

        main()

    # Existing PLAN.md should not be overwritten
    assert existing_plan.read_text() == "# Existing plan\n"
    mock_copy.assert_not_called()
    captured = capsys.readouterr()
    assert "Resuming investigation" in captured.err


def test_investigate_copies_settings_on_new(tmp_path, capsys):
    """New investigation copies mcloop.json and .claude/ to worktree."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    (tmp_path / "mcloop.json").write_text('{"checks": ["ruff"]}')
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}")

    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="bug")
    mock_result = MagicMock(returncode=0)

    with (
        patch(
            "sys.argv",
            ["mcloop", "--file", str(plan), "investigate", "bug"],
        ),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-bug", False)

        from mcloop.main import main

        main()

    assert (wt_path / "mcloop.json").exists()
    assert (wt_path / ".claude" / "settings.json").exists()
    captured = capsys.readouterr()
    assert "copied mcloop.json" in captured.err
    assert "copied .claude/" in captured.err


# --- investigate subprocess launch ---


def test_investigate_runs_mcloop_with_no_audit(tmp_path):
    """investigate runs mcloop as subprocess with --no-audit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="crash")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "crash"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-crash", False)

        from mcloop.main import main

        main()

    cmd = mock_run.call_args[0][0]
    assert cmd[-1] == "--no-audit"
    assert "-m" in cmd
    assert "mcloop" in cmd
    assert mock_run.call_args[1]["cwd"] == str(wt_path)


def test_investigate_passes_model_to_subprocess(tmp_path):
    """--model flag is forwarded to the mcloop subprocess."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="bug")
    mock_result = MagicMock(returncode=0)

    with (
        patch(
            "sys.argv",
            ["mcloop", "--file", str(plan), "--model", "opus", "investigate", "bug"],
        ),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.main._investigation_passed"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-bug", False)

        from mcloop.main import main

        main()

    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "opus"


def test_investigate_propagates_nonzero_returncode(tmp_path):
    """Nonzero subprocess returncode calls _investigation_failed and exits."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="bug")
    mock_result = MagicMock(returncode=1)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "bug"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._investigation_failed") as mock_failed,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-bug", False)

        from mcloop.main import main

        main()

    assert exc_info.value.code == 1
    mock_failed.assert_called_once_with(wt_path, "investigate-bug")


# --- _investigation_passed ---


def test_investigation_passed_merges_on_yes(tmp_path, capsys):
    """When user confirms, merges branch and cleans up worktree."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("mcloop.main.worktree.current_branch", return_value="main"),
        patch("mcloop.main.subprocess.run") as mock_run,
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.worktree.merge") as mock_merge,
        patch("mcloop.main.worktree.remove") as mock_remove,
    ):
        # git log and git diff --stat
        mock_run.side_effect = [
            MagicMock(stdout="abc123 Fix the bug\n"),
            MagicMock(stdout=" src/main.py | 5 ++---\n"),
        ]
        _investigation_passed(wt_path, "investigate-bug", tmp_path)

    mock_merge.assert_called_once_with("investigate-bug", cwd=tmp_path)
    mock_remove.assert_called_once_with("investigate-bug", cwd=tmp_path)
    captured = capsys.readouterr()
    assert "Commits to merge:" in captured.err
    assert "abc123" in captured.err
    assert "Changed files:" in captured.err
    assert "Merged investigate-bug" in captured.err
    assert "Cleaned up worktree" in captured.err


def test_investigation_passed_skips_merge_on_no(tmp_path, capsys):
    """When user declines, worktree is left in place."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("mcloop.main.worktree.current_branch", return_value="main"),
        patch("mcloop.main.subprocess.run") as mock_run,
        patch("builtins.input", return_value="n"),
        patch("mcloop.main.worktree.merge") as mock_merge,
    ):
        mock_run.return_value = MagicMock(stdout="")
        _investigation_passed(wt_path, "investigate-bug", tmp_path)

    mock_merge.assert_not_called()
    captured = capsys.readouterr()
    assert "Skipped merge" in captured.err
    assert str(wt_path) in captured.err


def test_investigation_passed_skips_merge_on_eof(tmp_path, capsys):
    """When input raises EOFError, merge is skipped."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("mcloop.main.worktree.current_branch", return_value="main"),
        patch("mcloop.main.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", side_effect=EOFError),
        patch("mcloop.main.worktree.merge") as mock_merge,
    ):
        _investigation_passed(wt_path, "investigate-bug", tmp_path)

    mock_merge.assert_not_called()
    captured = capsys.readouterr()
    assert "Skipped merge" in captured.err


def test_investigation_passed_merge_failure(tmp_path, capsys):
    """When merge fails, prints error and exits 1."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("mcloop.main.worktree.current_branch", return_value="main"),
        patch("mcloop.main.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", return_value="y"),
        patch(
            "mcloop.main.worktree.merge",
            side_effect=RuntimeError("conflict"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        _investigation_passed(wt_path, "investigate-bug", tmp_path)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Merge failed" in captured.err


def test_investigation_passed_cleanup_failure_non_fatal(tmp_path, capsys):
    """When cleanup fails after merge, prints warning but doesn't exit."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("mcloop.main.worktree.current_branch", return_value="main"),
        patch("mcloop.main.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.worktree.merge"),
        patch(
            "mcloop.main.worktree.remove",
            side_effect=RuntimeError("locked"),
        ),
    ):
        _investigation_passed(wt_path, "investigate-bug", tmp_path)

    captured = capsys.readouterr()
    assert "Cleanup warning" in captured.err


# --- _investigation_failed ---


def test_investigation_failed_with_notes_and_plan(tmp_path, capsys):
    """Prints NOTES.md content and PLAN.md task summary."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    (wt_path / "NOTES.md").write_text("## Observations\n- The crash is in parser.py\n")
    (wt_path / "PLAN.md").write_text(
        "# Investigation Plan\n\n"
        "- [x] Reproduce the crash\n"
        "- [!] Fix the parser\n"
        "- [ ] Add regression test\n"
        "- [ ] Clean up\n"
    )

    _investigation_failed(wt_path, "investigate-crash")

    captured = capsys.readouterr()
    assert "Investigation incomplete" in captured.err
    assert "What was learned (NOTES.md):" in captured.err
    assert "The crash is in parser.py" in captured.err
    assert "Completed: 1 tasks" in captured.err
    assert "Failed: 1 tasks" in captured.err
    assert "[!] Fix the parser" in captured.err
    assert "Remaining: 2 tasks" in captured.err
    assert "[ ] Add regression test" in captured.err
    assert "[ ] Clean up" in captured.err
    assert str(wt_path) in captured.err
    assert "investigate-crash" in captured.err
    assert "Resume with: mcloop investigate" in captured.err


def test_investigation_failed_no_notes(tmp_path, capsys):
    """Works without NOTES.md."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    (wt_path / "PLAN.md").write_text("# Plan\n\n- [ ] First task\n")

    _investigation_failed(wt_path, "investigate-bug")

    captured = capsys.readouterr()
    assert "Investigation incomplete" in captured.err
    assert "NOTES.md" not in captured.err
    assert "Remaining: 1 tasks" in captured.err


def test_investigation_failed_no_plan(tmp_path, capsys):
    """Works without PLAN.md."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    _investigation_failed(wt_path, "investigate-bug")

    captured = capsys.readouterr()
    assert "Investigation incomplete" in captured.err
    assert str(wt_path) in captured.err


def test_investigation_failed_all_completed(tmp_path, capsys):
    """When all tasks are checked, shows completed count only."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    (wt_path / "PLAN.md").write_text("# Plan\n\n- [x] Done task\n")

    _investigation_failed(wt_path, "investigate-bug")

    captured = capsys.readouterr()
    assert "Completed: 1 tasks" in captured.err
    assert "Remaining:" not in captured.err
    assert "Failed:" not in captured.err


# --- _handle_user_task ---


def test_handle_user_task_collects_response(capsys):
    """Prints instructions and collects user response."""
    inputs = iter(["I see the window", "it has a blue icon", ""])
    with patch("builtins.input", side_effect=inputs):
        response = _handle_user_task("3", "Launch the app and check the icon")

    assert response == "I see the window\nit has a blue icon"
    captured = capsys.readouterr()
    assert "USER ACTION REQUIRED" in captured.out
    assert "Launch the app and check the icon" in captured.out
    assert "observation recorded" in captured.out


def test_handle_user_task_empty_response(capsys):
    """Handles EOF with no input."""
    with patch("builtins.input", side_effect=EOFError):
        response = _handle_user_task("1", "Check the screen")

    assert response == ""
    captured = capsys.readouterr()
    assert "No observation provided" in captured.out


def test_handle_user_task_keyboard_interrupt(capsys):
    """Handles Ctrl-C gracefully."""
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        response = _handle_user_task("1", "Check the screen")

    assert response == ""


# --- run_loop with [USER] tasks ---


def test_run_loop_user_task_skips_claude(tmp_path):
    """[USER] tasks pause for input and skip Claude Code session."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        "- [ ] [USER] Launch the app and verify the window appears\n- [ ] Fix the bug\n"
    )
    (tmp_path / ".git").mkdir()

    inputs = iter(["Window is visible", ""])

    with (
        patch("builtins.input", side_effect=inputs),
        patch("mcloop.main.run_task") as mock_run_task,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
    ):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = ""
        mock_result.exit_code = 0
        mock_run_task.return_value = mock_result

        mock_check_result = MagicMock()
        mock_check_result.passed = True
        mock_checks.return_value = mock_check_result

        run_loop(plan, no_audit=True)

    # run_task should only be called for the second task, not the [USER] task
    assert mock_run_task.call_count == 1
    call_args = mock_run_task.call_args
    assert "Fix the bug" in call_args[0][0]

    # The [USER] task should be checked off
    tasks = __import__("mcloop.checklist", fromlist=["parse"]).parse(plan)
    assert tasks[0].checked
