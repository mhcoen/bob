"""Unit tests for CLI argument parsing and main helpers."""

from unittest.mock import MagicMock, call, patch

import pytest

from mcloop.audit import _run_audit_fix_cycle, _run_single_audit_round
from mcloop.investigator import _find_recent_crash_report, gather_bug_context
from mcloop.main import (
    _MAX_FIX_ATTEMPTS,
    MAX_VERIFICATION_ROUNDS,
    _append_verification_failure,
    _check_errors_json,
    _check_user_input,
    _copy_project_settings,
    _dispatch_auto_action,
    _error_signature_hash,
    _handle_auto_task,
    _handle_user_task,
    _insert_bugs_section,
    _investigation_failed,
    _investigation_passed,
    _launch_app_verification,
    _parse_args,
    _read_repro_steps,
    _reinject_wrappers,
    _replay_repro_steps,
    _verify_gui_survival,
    run_loop,
)
from mcloop.session_context import SessionContext


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
        patch("mcloop.audit.run_audit", side_effect=fake_audit) as mock_audit,
        patch("mcloop.audit.run_bug_fix") as mock_fix,
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
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()) as mock_fix,
        patch("mcloop.audit._has_meaningful_changes", return_value=False),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_called_once()


def test_run_audit_fix_cycle_audit_failure(tmp_path):
    """When audit session fails, fix session is not run."""
    with (
        patch("mcloop.audit.run_audit", return_value=_make_result(success=False, exit_code=1)),
        patch("mcloop.audit.run_bug_fix") as mock_fix,
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_not_called()


def test_run_audit_fix_cycle_no_bugs_md(tmp_path):
    """When audit succeeds but BUGS.md not written, fix session is not run."""
    with (
        patch("mcloop.audit.run_audit", return_value=_make_result()),
        patch("mcloop.audit.run_bug_fix") as mock_fix,
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
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()),
        patch("mcloop.audit._has_meaningful_changes", return_value=True),
        patch("mcloop.audit.run_checks", return_value=check_result),
        patch("mcloop.audit._commit") as mock_commit,
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
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            side_effect=fake_round,
        ),
        patch("mcloop.audit._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert call_count == 2


def test_audit_cycle_stops_after_one_round_when_no_fixes(tmp_path):
    """When the first round finds no bugs, second round is skipped."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=False,
        ) as mock_round,
        patch("mcloop.audit._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_round.assert_called_once()


def test_audit_cycle_caps_at_two_rounds(tmp_path):
    """Even if both rounds fix bugs, it stops at two."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=True,
        ) as mock_round,
        patch("mcloop.audit._save_audit_hash"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert mock_round.call_count == 2


def test_audit_cycle_saves_hash_after_completion(tmp_path):
    """Audit hash is saved after both rounds complete."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=False,
        ),
        patch("mcloop.audit._save_audit_hash") as mock_save,
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
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()),
        patch("mcloop.audit._has_meaningful_changes", return_value=True),
        patch("mcloop.audit.run_checks", return_value=check_result),
        patch("mcloop.audit._commit"),
    ):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is True


def test_single_audit_round_returns_false_on_no_bugs(tmp_path):
    """_run_single_audit_round returns False when no bugs found."""

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        (tmp_path / "BUGS.md").write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with patch("mcloop.audit.run_audit", side_effect=fake_audit):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is False


# --- _find_recent_crash_report ---


def test_find_recent_crash_report_no_dir(tmp_path):
    """Returns empty string when DiagnosticReports dir doesn't exist."""
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
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
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == ""


def test_find_recent_crash_report_finds_newest(tmp_path):
    """Returns contents of the newest recent .ips file."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "OldApp.ips").write_text("old crash")
    (reports_dir / "NewApp.ips").write_text("new crash")
    # Both are recent (just created), newest by mtime wins
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == "new crash"


def test_find_recent_crash_report_ignores_non_ips(tmp_path):
    """Ignores non-.ips files."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "crash.log").write_text("not ips")
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == ""


# --- gather_bug_context ---


def test_gather_bug_context_description_only(tmp_path):
    """Description is set from the argument."""
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, description="app crashes")
    assert ctx.user_description == "app crashes"
    assert ctx.crash_report == ""
    assert ctx.failure_history == ""


def test_gather_bug_context_log_file(tmp_path):
    """Reads the --log file into failure_history."""
    log_file = tmp_path / "error.log"
    log_file.write_text("Traceback: something broke\n")
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, log_path=str(log_file))
    assert "Traceback: something broke" in ctx.failure_history
    assert "From " in ctx.failure_history


def test_gather_bug_context_stdin(tmp_path):
    """Piped stdin text is included in failure_history."""
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, stdin_text="error from pipe\n")
    assert "error from pipe" in ctx.failure_history
    assert "From stdin:" in ctx.failure_history


def test_gather_bug_context_last_run_log(tmp_path):
    """Reads .mcloop/last-run.log into failure_history."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "last-run.log").write_text("previous run failed here\n")
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path)
    assert "previous run failed here" in ctx.failure_history
    assert "From last-run.log:" in ctx.failure_history


def test_gather_bug_context_crash_report(tmp_path):
    """Picks up crash report from DiagnosticReports."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "MyCrash.ips").write_text("crash data here")
    with (
        patch("mcloop.investigator.Path.home", return_value=tmp_path),
        patch("mcloop.investigator.detect_app_type", return_value=""),
    ):
        ctx = gather_bug_context(tmp_path)
    assert ctx.crash_report == "crash data here"


def test_gather_bug_context_app_type(tmp_path):
    """Populates app_type from detect_app_type."""
    with patch("mcloop.investigator.detect_app_type", return_value="gui"):
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
        patch("mcloop.investigator.Path.home", return_value=tmp_path),
        patch("mcloop.investigator.detect_app_type", return_value="cli"),
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
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, stdin_text="   \n  ")
    assert ctx.failure_history == ""


def test_gather_bug_context_missing_log_file(tmp_path):
    """Non-existent --log file is silently skipped."""
    with patch("mcloop.investigator.detect_app_type", return_value=""):
        ctx = gather_bug_context(tmp_path, log_path=str(tmp_path / "nonexistent.log"))
    assert ctx.failure_history == ""


def test_gather_bug_context_no_description_is_empty(tmp_path):
    """When description is None, user_description is empty string."""
    with patch("mcloop.investigator.detect_app_type", return_value=""):
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
    assert "--no-audit" in cmd
    assert "--allow-web-tools" in cmd
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


def test_investigate_verification_passes_calls_merge(tmp_path, capsys):
    """When verification passes, _investigation_passed is called."""
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
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch("mcloop.main._launch_app_verification", return_value=None) as mock_verify,
        patch("mcloop.main._investigation_passed") as mock_passed,
        patch("mcloop.main.notify") as mock_notify,
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-crash", False)

        from mcloop.main import main

        main()

    mock_verify.assert_called_once_with(wt_path)
    mock_passed.assert_called_once_with(wt_path, "investigate-crash", tmp_path)
    # Notification sent on successful verification
    mock_notify.assert_called_once()
    assert "verified" in mock_notify.call_args[0][0].lower()
    captured = capsys.readouterr()
    assert "Verification passed" in captured.out


def test_investigate_verification_fails_then_passes(tmp_path, capsys):
    """Verification fails first round, passes second — merges."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n- [ ] Fix bug\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    (wt_path / "PLAN.md").write_text("# Project\n- [ ] Fix bug\n")

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="crash")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "crash"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch(
            "mcloop.main._launch_app_verification",
            side_effect=["App crashed", None],
        ) as mock_verify,
        patch("mcloop.main._append_verification_failure") as mock_append,
        patch("mcloop.main._investigation_passed") as mock_passed,
        patch("mcloop.main.notify"),
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-crash", False)

        from mcloop.main import main

        main()

    assert mock_verify.call_count == 2
    mock_append.assert_called_once_with(wt_path, "App crashed", 1)
    mock_passed.assert_called_once()
    captured = capsys.readouterr()
    assert "Verification passed" in captured.out


def test_investigate_verification_exhausts_rounds(tmp_path, capsys):
    """Verification fails all rounds — _investigation_failed is called."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n- [ ] Fix bug\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    (wt_path / "PLAN.md").write_text("# Project\n- [ ] Fix bug\n")

    from mcloop.investigator import BugContext
    from mcloop.main import MAX_VERIFICATION_ROUNDS

    ctx = BugContext(user_description="crash")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "crash"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.main.gather_bug_context", return_value=ctx),
        patch("mcloop.main.worktree.create") as mock_create,
        patch("mcloop.main._copy_project_settings"),
        patch("mcloop.main.subprocess.run", return_value=mock_result),
        patch(
            "mcloop.main._launch_app_verification",
            return_value="App crashed",
        ),
        patch("mcloop.main._append_verification_failure"),
        patch("mcloop.main._investigation_failed") as mock_failed,
        patch("mcloop.main.notify"),
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stdin.isatty.return_value = True
        mock_create.return_value = (wt_path, "investigate-crash", False)

        from mcloop.main import main

        main()

    assert exc_info.value.code == 1
    mock_failed.assert_called_once()
    captured = capsys.readouterr()
    assert f"{MAX_VERIFICATION_ROUNDS} rounds" in captured.out


# --- _launch_app_verification ---


def test_launch_app_verification_no_run_cmd(tmp_path, capsys):
    """When no run command is detected, returns None."""
    with patch("mcloop.main.detect_run", return_value=None):
        result = _launch_app_verification(tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert captured.out == ""


def test_launch_app_verification_gui_ok(tmp_path, capsys):
    """GUI app that starts OK is reported, killed, and returns None."""
    gui_result = MagicMock(crashed=False, hung=False, duration=5.0)
    with (
        patch("mcloop.main.detect_run", return_value="swift run MyApp"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = [1234]
        result = _launch_app_verification(tmp_path)
    assert result is None
    mock_pm.run_gui.assert_called_once_with("swift run MyApp", "MyApp", timeout_seconds=15)
    mock_pm.kill.assert_called_once_with(1234)
    captured = capsys.readouterr()
    assert "running OK" in captured.out


def test_launch_app_verification_gui_crashed(tmp_path, capsys):
    """GUI app that crashes returns failure description."""
    gui_result = MagicMock(
        crashed=True, hung=False, duration=2.0, crash_report="crash info\nline2"
    )
    with (
        patch("mcloop.main.detect_run", return_value="open Foo.app"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = []
        result = _launch_app_verification(tmp_path)
    assert result is not None
    assert "crashed" in result.lower()
    captured = capsys.readouterr()
    assert "CRASHED" in captured.out


def test_launch_app_verification_gui_hung(tmp_path, capsys):
    """GUI app that hangs returns failure description."""
    gui_result = MagicMock(crashed=False, hung=True, duration=15.0)
    with (
        patch("mcloop.main.detect_run", return_value="swift run MyApp"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = [5678]
        result = _launch_app_verification(tmp_path)
    assert result is not None
    assert "hung" in result.lower()
    mock_pm.kill.assert_called_once_with(5678)
    captured = capsys.readouterr()
    assert "HUNG" in captured.out


def test_launch_app_verification_cli_ok(tmp_path, capsys):
    """CLI app that exits 0 returns None."""
    cli_result = MagicMock(hung=False, exit_code=0, duration=1.5, output="")
    with (
        patch("mcloop.main.detect_run", return_value="cargo run"),
        patch("mcloop.main.detect_app_type", return_value="cli"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_cli.return_value = cli_result
        result = _launch_app_verification(tmp_path)
    assert result is None
    mock_pm.run_cli.assert_called_once_with(
        "cargo run", cwd=str(tmp_path), timeout_seconds=15, hang_seconds=10
    )
    captured = capsys.readouterr()
    assert "exited OK" in captured.out


def test_launch_app_verification_cli_crash(tmp_path, capsys):
    """CLI app with non-zero exit returns failure description."""
    cli_result = MagicMock(hung=False, exit_code=1, duration=0.5, output="error: segfault")
    with (
        patch("mcloop.main.detect_run", return_value="./myapp"),
        patch("mcloop.main.detect_app_type", return_value="cli"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_cli.return_value = cli_result
        result = _launch_app_verification(tmp_path)
    assert result is not None
    assert "exited with code 1" in result
    captured = capsys.readouterr()
    assert "exited with code 1" in captured.out


def test_launch_app_verification_cli_hung(tmp_path, capsys):
    """CLI app that hangs returns failure description."""
    cli_result = MagicMock(hung=True, exit_code=None, duration=10.0, output="")
    with (
        patch("mcloop.main.detect_run", return_value="./myapp"),
        patch("mcloop.main.detect_app_type", return_value="cli"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_cli.return_value = cli_result
        result = _launch_app_verification(tmp_path)
    assert result is not None
    assert "hung" in result.lower()
    captured = capsys.readouterr()
    assert "HUNG" in captured.out


def test_launch_app_verification_web_skipped(tmp_path, capsys):
    """Web apps are skipped and return None."""
    with (
        patch("mcloop.main.detect_run", return_value="npm start"),
        patch("mcloop.main.detect_app_type", return_value="web"),
    ):
        result = _launch_app_verification(tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert "Skipping launch for web app" in captured.out


def test_launch_app_verification_gui_process_name_from_app_bundle(tmp_path, capsys):
    """Process name is extracted from .app bundle path."""
    gui_result = MagicMock(crashed=False, hung=False, duration=3.0)
    with (
        patch("mcloop.main.detect_run", return_value="open MyApp.app"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = []
        _launch_app_verification(tmp_path)
    mock_pm.run_gui.assert_called_once_with("open MyApp.app", "MyApp", timeout_seconds=15)


# --- _read_repro_steps ---


def test_read_repro_steps_no_file(tmp_path):
    """Returns empty list when repro-steps.json does not exist."""
    assert _read_repro_steps(tmp_path) == []


def test_read_repro_steps_valid(tmp_path):
    """Reads and returns valid steps."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    import json

    steps = [
        {"action": "window_exists", "args": "MyApp"},
        {"action": "click_button", "args": "MyApp | Start"},
    ]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))
    result = _read_repro_steps(tmp_path)
    assert len(result) == 2
    assert result[0]["action"] == "window_exists"
    assert result[1]["args"] == "MyApp | Start"


def test_read_repro_steps_malformed_json(tmp_path):
    """Returns empty list on invalid JSON."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "repro-steps.json").write_text("not json")
    assert _read_repro_steps(tmp_path) == []


def test_read_repro_steps_not_a_list(tmp_path):
    """Returns empty list when JSON is not a list."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "repro-steps.json").write_text('{"action": "x"}')
    assert _read_repro_steps(tmp_path) == []


def test_read_repro_steps_skips_bad_entries(tmp_path):
    """Skips entries missing action or args keys."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    import json

    steps = [
        {"action": "window_exists", "args": "MyApp"},
        {"bad": "entry"},
        "not a dict",
        {"action": "click_button"},  # missing args
    ]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))
    result = _read_repro_steps(tmp_path)
    assert len(result) == 1
    assert result[0]["action"] == "window_exists"


# --- _replay_repro_steps ---


def test_replay_repro_steps_dispatches_actions():
    """Dispatches each step and collects results."""
    steps = [
        {"action": "window_exists", "args": "MyApp"},
        {"action": "list_elements", "args": "MyApp"},
    ]
    with (
        patch("mcloop.app_interact.window_exists", return_value=True),
        patch(
            "mcloop.app_interact.list_elements",
            return_value="button 1, button 2",
        ),
    ):
        results = _replay_repro_steps(steps)
    assert len(results) == 2
    assert "True" in results[0]
    assert "button 1" in results[1]


def test_replay_repro_steps_catches_exceptions():
    """Exceptions in dispatch are caught and reported."""
    steps = [{"action": "click_button", "args": "App | BadBtn"}]
    with patch(
        "mcloop.app_interact.click_button",
        side_effect=RuntimeError("no such button"),
    ):
        results = _replay_repro_steps(steps)
    assert len(results) == 1
    assert results[0].startswith("ERROR:")


# --- _launch_app_verification with repro steps ---


def test_launch_app_verification_gui_replays_repro_steps(tmp_path, capsys):
    """GUI app that runs OK replays repro-steps.json and returns None."""
    import json

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    steps = [{"action": "window_exists", "args": "MyApp"}]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))

    gui_result = MagicMock(crashed=False, hung=False, duration=5.0)
    with (
        patch("mcloop.main.detect_run", return_value="swift run MyApp"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
        patch("mcloop.app_interact.window_exists", return_value=True) as mock_we,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = [1234]
        mock_pm.sample.return_value = "sample"
        mock_pm.is_main_thread_stuck.return_value = False
        result = _launch_app_verification(tmp_path)
    assert result is None
    # window_exists is called twice: once during repro replay, once during survival check
    assert mock_we.call_count == 2
    captured = capsys.readouterr()
    assert "Replaying 1 reproduction step" in captured.out
    assert "Step 1" in captured.out


def test_launch_app_verification_gui_no_repro_on_crash(tmp_path, capsys):
    """GUI app that crashes does not replay repro steps."""
    import json

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    steps = [{"action": "window_exists", "args": "MyApp"}]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))

    gui_result = MagicMock(crashed=True, hung=False, duration=2.0, crash_report=None)
    with (
        patch("mcloop.main.detect_run", return_value="swift run MyApp"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = []
        _launch_app_verification(tmp_path)
    captured = capsys.readouterr()
    assert "Replaying" not in captured.out


def test_launch_app_verification_cli_replays_repro_steps(tmp_path, capsys):
    """CLI app that exits OK replays repro-steps.json."""
    import json

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    steps = [{"action": "run_cli", "args": "./myapp --check"}]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))

    cli_result = MagicMock(hung=False, exit_code=0, duration=1.5, output="")
    repro_cli = MagicMock(exit_code=0, hung=False, output="ok", sample_output=None)
    with (
        patch("mcloop.main.detect_run", return_value="./myapp"),
        patch("mcloop.main.detect_app_type", return_value="cli"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_cli.side_effect = [cli_result, repro_cli]
        _launch_app_verification(tmp_path)
    captured = capsys.readouterr()
    assert "Replaying 1 reproduction step" in captured.out


# --- _verify_gui_survival ---


def test_verify_gui_survival_app_alive_and_responsive(capsys):
    """Returns None when process is alive, not hung, and has a window."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = [1234]
    mock_pm.sample.return_value = "sample output"
    mock_pm.is_main_thread_stuck.return_value = False
    with patch("mcloop.app_interact.window_exists", return_value=True):
        result = _verify_gui_survival("MyApp", mock_pm)
    assert result is None
    captured = capsys.readouterr()
    assert "alive, responsive, window present" in captured.out


def test_verify_gui_survival_app_crashed(capsys):
    """Returns failure description when process disappears after replay."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = []
    mock_pm.read_crash_report.return_value = None
    result = _verify_gui_survival("MyApp", mock_pm)
    assert result is not None
    assert "crashed" in result.lower()
    captured = capsys.readouterr()
    assert "Post-replay: app CRASHED" in captured.out


def test_verify_gui_survival_app_crashed_with_report(capsys):
    """Returns failure with crash report when available."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = []
    mock_pm.read_crash_report.return_value = "crash line 1\ncrash line 2"
    result = _verify_gui_survival("MyApp", mock_pm)
    assert result is not None
    assert "crash line 1" in result
    captured = capsys.readouterr()
    assert "Post-replay: app CRASHED" in captured.out
    assert "crash line 1" in captured.err


def test_verify_gui_survival_app_hung(capsys):
    """Returns failure description when main thread is stuck."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = [1234]
    mock_pm.sample.return_value = "sample output"
    mock_pm.is_main_thread_stuck.return_value = True
    result = _verify_gui_survival("MyApp", mock_pm)
    assert result is not None
    assert "hung" in result.lower()
    captured = capsys.readouterr()
    assert "Post-replay: app HUNG" in captured.out


def test_verify_gui_survival_no_window(capsys):
    """Returns failure when app is alive but has no window."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = [1234]
    mock_pm.sample.return_value = "sample output"
    mock_pm.is_main_thread_stuck.return_value = False
    with patch("mcloop.app_interact.window_exists", return_value=False):
        result = _verify_gui_survival("MyApp", mock_pm)
    assert result is not None
    assert "no windows" in result
    captured = capsys.readouterr()
    assert "Post-replay: app has no windows" in captured.out


def test_verify_gui_survival_window_check_fails(capsys):
    """Returns None when window_exists raises (alive and responsive)."""
    mock_pm = MagicMock()
    mock_pm.pgrep.return_value = [1234]
    mock_pm.sample.return_value = "sample output"
    mock_pm.is_main_thread_stuck.return_value = False
    with patch(
        "mcloop.app_interact.window_exists",
        side_effect=RuntimeError("osascript failed"),
    ):
        result = _verify_gui_survival("MyApp", mock_pm)
    assert result is None
    captured = capsys.readouterr()
    assert "alive and responsive" in captured.out
    assert "window present" not in captured.out


def test_launch_verification_gui_survival_check_after_replay(tmp_path, capsys):
    """GUI verification runs survival check after replaying repro steps."""
    import json

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    steps = [{"action": "window_exists", "args": "MyApp"}]
    (mcloop_dir / "repro-steps.json").write_text(json.dumps(steps))

    gui_result = MagicMock(crashed=False, hung=False, duration=5.0)
    with (
        patch("mcloop.main.detect_run", return_value="swift run MyApp"),
        patch("mcloop.main.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
        patch("mcloop.app_interact.window_exists", return_value=True),
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = [1234]
        mock_pm.sample.return_value = "sample"
        mock_pm.is_main_thread_stuck.return_value = False
        _launch_app_verification(tmp_path)
    captured = capsys.readouterr()
    assert "Replaying" in captured.out
    assert "alive, responsive, window present" in captured.out


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


# --- _append_verification_failure ---


def test_append_verification_failure_creates_notes(tmp_path, capsys):
    """Creates NOTES.md with observations header when it doesn't exist."""
    _append_verification_failure(tmp_path, "App crashed on launch", 1)
    notes = (tmp_path / "NOTES.md").read_text()
    assert "## Observations" in notes
    assert "Verification round 1 failed" in notes
    assert "App crashed on launch" in notes


def test_append_verification_failure_appends_to_existing_notes(tmp_path, capsys):
    """Appends to existing NOTES.md without duplicating header."""
    (tmp_path / "NOTES.md").write_text("## Observations\n\n- Prior note\n")
    _append_verification_failure(tmp_path, "App hung", 2)
    notes = (tmp_path / "NOTES.md").read_text()
    assert notes.count("## Observations") == 1
    assert "Prior note" in notes
    assert "Verification round 2 failed" in notes


def test_append_verification_failure_adds_plan_tasks(tmp_path, capsys):
    """Appends new fix tasks to PLAN.md."""
    (tmp_path / "PLAN.md").write_text("# Plan\n\n- [x] Fix the bug\n")
    _append_verification_failure(tmp_path, "App crashed", 1)
    plan = (tmp_path / "PLAN.md").read_text()
    assert "## Verification fix (round 1)" in plan
    assert "- [ ] Investigate and fix verification failure" in plan
    assert "App crashed" in plan
    assert "- [ ] Verify the fix resolves the issue" in plan


def test_append_verification_failure_prints_status(tmp_path, capsys):
    """Prints a status message about the retry."""
    (tmp_path / "PLAN.md").write_text("# Plan\n")
    _append_verification_failure(tmp_path, "App hung", 1)
    captured = capsys.readouterr()
    assert "Verification failed" in captured.out
    assert f"round 1/{MAX_VERIFICATION_ROUNDS}" in captured.out


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


# --- _check_user_input ---


def test_check_user_input_reads_pending_lines():
    """Reads lines available on stdin without blocking."""
    lines = ["fix the alignment\n", "use blue not red\n"]
    call_count = 0

    def fake_select(rlist, wlist, xlist, timeout):
        nonlocal call_count
        call_count += 1
        if call_count <= len(lines):
            return (rlist, [], [])
        return ([], [], [])

    line_idx = 0

    def fake_readline():
        nonlocal line_idx
        if line_idx < len(lines):
            result = lines[line_idx]
            line_idx += 1
            return result
        return ""

    with (
        patch("mcloop.main.sys.stdin") as mock_stdin,
        patch("mcloop.main.select.select", side_effect=fake_select),
    ):
        mock_stdin.isatty.return_value = True
        mock_stdin.readline = fake_readline
        result = _check_user_input()
    assert result == "fix the alignment\nuse blue not red"


def test_check_user_input_empty_when_nothing_typed():
    """Returns empty string when no input is pending."""
    with (
        patch("mcloop.main.sys.stdin") as mock_stdin,
        patch("mcloop.main.select.select", return_value=([], [], [])),
    ):
        mock_stdin.isatty.return_value = True
        result = _check_user_input()
    assert result == ""


def test_check_user_input_not_a_tty():
    """Returns empty string when stdin is not a tty."""
    with patch("mcloop.main.sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = _check_user_input()
    assert result == ""


# --- SessionContext.add_user_input ---


def test_session_context_add_user_input():
    """User input appears in session context text."""
    ctx = SessionContext()
    ctx.add_user_input("please use the new API instead")
    text = ctx.text()
    assert "[user] please use the new API instead" in text


def test_session_context_user_input_interleaved():
    """User input is interleaved with task entries."""
    ctx = SessionContext()
    ctx.add("1", "First task", "5s", "done")
    ctx.add_user_input("try a different approach")
    ctx.add("2", "Second task", "3s", "done")
    text = ctx.text()
    lines = text.splitlines()
    assert any("[user]" in line for line in lines)
    assert lines.index(next(line for line in lines if "[user]" in line)) == 1


# --- run_loop picks up user input ---


def test_run_loop_picks_up_user_input(tmp_path):
    """User input typed between tasks is passed to session context."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] First task\n- [ ] Second task\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_check_user_input():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "use the v2 API"
        return ""

    with (
        patch("mcloop.main._check_user_input", side_effect=fake_check_user_input),
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

    # First task should have user input in session_context
    first_call = mock_run_task.call_args_list[0]
    assert "use the v2 API" in first_call.kwargs.get(
        "session_context", first_call[1].get("session_context", "")
    )


# --- _handle_auto_task ---


def test_handle_auto_task_prints_and_returns(capsys):
    """Auto task prints observation header and result."""
    with patch(
        "mcloop.main._dispatch_auto_action",
        return_value="window_exists(MyApp): True",
    ):
        result = _handle_auto_task("3", "window_exists", "MyApp")

    assert result == "window_exists(MyApp): True"
    captured = capsys.readouterr()
    assert "AUTO OBSERVATION" in captured.out
    assert "Task 3" in captured.out
    assert "window_exists" in captured.out


def test_handle_auto_task_exception(capsys):
    """Auto task catches exceptions and returns error string."""
    with patch(
        "mcloop.main._dispatch_auto_action",
        side_effect=RuntimeError("osascript failed"),
    ):
        result = _handle_auto_task("1", "screenshot", "MyApp")

    assert "ERROR" in result
    assert "osascript failed" in result


def test_handle_auto_task_truncates_long_result(capsys):
    """Long results are truncated in display but not in return value."""
    long_result = "x" * 1000
    with patch(
        "mcloop.main._dispatch_auto_action",
        return_value=long_result,
    ):
        result = _handle_auto_task("1", "list_elements", "MyApp")

    assert result == long_result  # full result returned
    captured = capsys.readouterr()
    assert "..." in captured.out  # truncated in display


# --- _dispatch_auto_action ---


def test_dispatch_run_cli():
    """run_cli action dispatches to process_monitor.run_cli."""
    mock_result = MagicMock()
    mock_result.exit_code = 0
    mock_result.hung = False
    mock_result.output = "hello world"
    mock_result.sample_output = None

    with patch("mcloop.process_monitor.run_cli", return_value=mock_result) as mock:
        result = _dispatch_auto_action("run_cli", "./my_app --flag")

    mock.assert_called_once_with("./my_app --flag")
    assert "OK" in result
    assert "hello world" in result


def test_dispatch_run_cli_crash():
    """run_cli reports CRASHED on non-zero exit."""
    mock_result = MagicMock()
    mock_result.exit_code = 1
    mock_result.hung = False
    mock_result.output = "segfault"
    mock_result.sample_output = None

    with patch("mcloop.process_monitor.run_cli", return_value=mock_result):
        result = _dispatch_auto_action("run_cli", "./my_app")

    assert "CRASHED" in result


def test_dispatch_run_cli_hung():
    """run_cli reports HUNG when process was killed."""
    mock_result = MagicMock()
    mock_result.exit_code = None
    mock_result.hung = True
    mock_result.output = ""
    mock_result.sample_output = "main thread stuck"

    with patch("mcloop.process_monitor.run_cli", return_value=mock_result):
        result = _dispatch_auto_action("run_cli", "./my_app")

    assert "HUNG" in result
    assert "main thread stuck" in result


def test_dispatch_run_gui():
    """run_gui action parses 'command | process_name' format."""
    mock_result = MagicMock()
    mock_result.crashed = False
    mock_result.hung = False
    mock_result.duration = 5.0
    mock_result.crash_report = None
    mock_result.sample_output = None

    with patch("mcloop.process_monitor.run_gui", return_value=mock_result) as mock:
        result = _dispatch_auto_action(
            "run_gui",
            "open .build/debug/MyApp | MyApp",
        )

    mock.assert_called_once_with("open .build/debug/MyApp", "MyApp")
    assert "OK" in result


def test_dispatch_run_gui_missing_pipe():
    """run_gui returns error if pipe separator is missing."""
    result = _dispatch_auto_action("run_gui", "open .build/debug/MyApp")
    assert "ERROR" in result


def test_dispatch_window_exists():
    """window_exists action checks via app_interact."""
    with patch("mcloop.app_interact.window_exists", return_value=True) as mock:
        result = _dispatch_auto_action("window_exists", "MyApp")

    mock.assert_called_once_with("MyApp")
    assert "True" in result


def test_dispatch_screenshot():
    """screenshot action captures via app_interact."""
    with patch("mcloop.app_interact.screenshot_window") as mock:
        result = _dispatch_auto_action("screenshot", "MyApp")

    mock.assert_called_once_with("MyApp", "/tmp/auto_screenshot_MyApp.png")
    assert "screenshot saved" in result


def test_dispatch_list_elements():
    """list_elements action returns UI tree."""
    with patch(
        "mcloop.app_interact.list_elements",
        return_value="button OK, text field Name",
    ) as mock:
        result = _dispatch_auto_action("list_elements", "MyApp")

    mock.assert_called_once_with("MyApp")
    assert "button OK" in result


def test_dispatch_click_button():
    """click_button parses 'app_name | button_label' format."""
    with patch("mcloop.app_interact.click_button") as mock:
        result = _dispatch_auto_action("click_button", "MyApp | OK")

    mock.assert_called_once_with("MyApp", "OK")
    assert "clicked" in result


def test_dispatch_click_button_missing_pipe():
    """click_button returns error if pipe separator is missing."""
    result = _dispatch_auto_action("click_button", "MyApp")
    assert "ERROR" in result


def test_dispatch_unknown_action():
    """Unknown action returns error."""
    result = _dispatch_auto_action("fly_to_moon", "please")
    assert "ERROR" in result
    assert "unknown auto action" in result


# --- run_loop with [AUTO] tasks ---


def test_run_loop_auto_task_skips_claude(tmp_path):
    """[AUTO] tasks execute automatically and skip Claude Code session."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] [AUTO:run_cli] ./my_app --test\n- [ ] Fix the bug\n")
    (tmp_path / ".git").mkdir()

    with (
        patch("mcloop.main._dispatch_auto_action", return_value="STATUS: OK") as mock_dispatch,
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

    # _dispatch_auto_action called for the AUTO task
    mock_dispatch.assert_called_once_with("run_cli", "./my_app --test")

    # run_task only called for the second task
    assert mock_run_task.call_count == 1
    call_args = mock_run_task.call_args
    assert "Fix the bug" in call_args[0][0]

    # The AUTO task should be checked off
    from mcloop.checklist import parse as parse_checklist

    tasks = parse_checklist(plan)
    assert tasks[0].checked


# --- --fallback-model ---


def test_fallback_model_flag():
    args = _parse("--fallback-model", "sonnet")
    assert args.fallback_model == "sonnet"


def test_fallback_model_default_is_none():
    args = _parse()
    assert args.fallback_model is None


def test_fallback_model_with_model():
    args = _parse("--model", "opus", "--fallback-model", "sonnet")
    assert args.model == "opus"
    assert args.fallback_model == "sonnet"


def test_run_loop_switches_to_fallback_on_rate_limit(tmp_path):
    """When rate-limited with fallback_model set, switches model."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # First call: rate limited
            result.success = False
            result.output = "rate limit exceeded"
            result.exit_code = 1
        else:
            # Second call: succeeds
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main.wait_for_reset", return_value="claude"),
    ):
        run_loop(
            plan,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    # First attempt used primary model, second used fallback
    assert models_used[0] == "opus"
    assert models_used[1] == "sonnet"


def test_run_loop_no_fallback_without_flag(tmp_path):
    """Without fallback_model, rate limit does not change model."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.success = False
            result.output = "rate limit exceeded"
            result.exit_code = 1
        else:
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main.wait_for_reset", return_value="claude"),
    ):
        run_loop(
            plan,
            model="opus",
            no_audit=True,
        )

    # Both attempts should use the same model
    assert models_used[0] == "opus"
    assert models_used[1] == "opus"


def test_fallback_model_retry_on_exhaustion(tmp_path):
    """When all retries fail on primary, retries with fallback model."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count <= 2:
            # First 2 calls (primary model retries): fail
            result.success = False
            result.output = "some error"
            result.exit_code = 1
        else:
            # Third call (fallback model): succeeds
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main._has_meaningful_changes",
            return_value=True,
        ),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert stuck == []
    # First 2 attempts used primary, third used fallback
    assert models_used[0] == "opus"
    assert models_used[1] == "opus"
    assert models_used[2] == "sonnet"


def test_fallback_model_prints_message(tmp_path, capsys):
    """Prints 'Primary model failed, retrying with <model>' on fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count <= 2:
            result.success = False
            result.output = "some error"
            result.exit_code = 1
        else:
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    with (
        patch("mcloop.main.run_task", side_effect=fake_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main._has_meaningful_changes",
            return_value=True,
        ),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    captured = capsys.readouterr().out
    assert "Primary model failed, retrying with sonnet" in captured


def test_fallback_model_also_exhausted(tmp_path):
    """When both primary and fallback exhaust retries, task fails."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    def fake_run_task(*args, **kwargs):
        result = MagicMock()
        result.success = False
        result.output = "always fails"
        result.exit_code = 1
        return result

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert stuck == ["Do something"]
    # 2 primary + 2 fallback = 4 total attempts
    assert len(models_used) == 4
    assert models_used[:2] == ["opus", "opus"]
    assert models_used[2:] == ["sonnet", "sonnet"]
    # Task is marked failed in the checklist
    assert "[!]" in plan.read_text()


def test_fallback_model_also_exhausted_notifies(tmp_path):
    """When both models exhaust retries, sends a 'giving up' notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    def fake_run_task(*args, **kwargs):
        result = MagicMock()
        result.success = False
        result.output = "always fails"
        result.exit_code = 1
        return result

    with (
        patch("mcloop.main.run_task", side_effect=fake_run_task),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    # "Giving up" notification is sent with error level
    giving_up_calls = [c for c in mock_notify.call_args_list if "Giving up" in str(c)]
    assert len(giving_up_calls) == 1
    assert giving_up_calls[0] == call("Giving up on: Do something", level="error")


def test_no_fallback_retry_without_flag(tmp_path):
    """Without fallback_model, exhausted retries just fail."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    def fake_run_task(*args, **kwargs):
        result = MagicMock()
        result.success = False
        result.output = "always fails"
        result.exit_code = 1
        return result

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=2,
            model="opus",
            no_audit=True,
        )

    assert stuck == ["Do something"]
    # Only 2 attempts, no fallback
    assert len(models_used) == 2
    assert models_used == ["opus", "opus"]


def test_fallback_same_as_primary_skips_fallback(tmp_path):
    """When fallback_model equals primary model, no extra retry round."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    def fake_run_task(*args, **kwargs):
        result = MagicMock()
        result.success = False
        result.output = "always fails"
        result.exit_code = 1
        return result

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="opus",
            no_audit=True,
        )

    assert stuck == ["Do something"]
    # Same model as fallback: only 2 attempts, not 4
    assert len(models_used) == 2
    assert models_used == ["opus", "opus"]


def test_fallback_gets_fresh_retries(tmp_path):
    """Fallback model gets its own full set of retries (not shared)."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count <= 3:
            # 3 primary retries fail
            result.success = False
            result.output = "error"
            result.exit_code = 1
        elif call_count <= 5:
            # 2 fallback retries fail
            result.success = False
            result.output = "error"
            result.exit_code = 1
        else:
            # 3rd fallback retry succeeds
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main._has_meaningful_changes",
            return_value=True,
        ),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=3,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert stuck == []
    # 3 primary + 3 fallback = 6 total, fallback succeeded on 3rd try
    assert len(models_used) == 6
    assert models_used[:3] == ["opus", "opus", "opus"]
    assert models_used[3:] == ["sonnet", "sonnet", "sonnet"]


def test_fallback_resets_per_task(tmp_path):
    """Each task starts with the primary model, even after a prior fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Task one\n- [ ] Task two\n")
    (tmp_path / ".git").mkdir()

    call_count = 0

    def fake_run_task(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count <= 2:
            # Task 1: primary retries fail
            result.success = False
            result.output = "error"
            result.exit_code = 1
        else:
            # Task 1 fallback + Task 2 primary: succeed
            result.success = True
            result.output = ""
            result.exit_code = 0
        return result

    models_used = []

    def tracking_run_task(*args, **kwargs):
        models_used.append(kwargs.get("model"))
        return fake_run_task(*args, **kwargs)

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    with (
        patch("mcloop.main.run_task", side_effect=tracking_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch(
            "mcloop.main._has_meaningful_changes",
            return_value=True,
        ),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        stuck = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert stuck == []
    # Task 1: opus, opus (fail), sonnet (succeed)
    # Task 2: should start with opus again
    assert models_used[0] == "opus"
    assert models_used[1] == "opus"
    assert models_used[2] == "sonnet"
    # Task 2 starts fresh with primary model
    assert models_used[3] == "opus"


# --- _reinject_wrappers tests ---


def test_reinject_no_wrap_dir(tmp_path):
    """When .mcloop/wrap/ does not exist, _reinject_wrappers is a no-op."""
    _reinject_wrappers(tmp_path)
    # No exception, no files created


def test_reinject_empty_wrap_dir(tmp_path):
    """When .mcloop/wrap/ exists but has no wrapper files, no-op."""
    (tmp_path / ".mcloop" / "wrap").mkdir(parents=True)
    _reinject_wrappers(tmp_path)


def test_reinject_markers_intact_swift(tmp_path):
    """When Swift markers are present, no re-injection happens."""
    from mcloop.wrap import SWIFT_WRAPPER, inject

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "swift_wrapper.swift").write_text(SWIFT_WRAPPER)

    # Create a Swift entry point with markers intact
    src = tmp_path / "Sources" / "MyApp"
    src.mkdir(parents=True)
    entry = src / "MyApp.swift"
    original = "import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n"
    entry.write_text(inject(original, "swift"))

    with patch("mcloop.main._git") as mock_git:
        _reinject_wrappers(tmp_path)

    mock_git.assert_not_called()


def test_reinject_markers_stripped_swift(tmp_path):
    """When Swift markers are stripped, re-injects and commits."""
    from mcloop.wrap import SWIFT_WRAPPER

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "swift_wrapper.swift").write_text(SWIFT_WRAPPER)

    src = tmp_path / "Sources" / "MyApp"
    src.mkdir(parents=True)
    entry = src / "MyApp.swift"
    # Write entry point WITHOUT markers
    entry.write_text("import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n")

    git_result = MagicMock()
    git_result.returncode = 0
    with patch("mcloop.main._git", return_value=git_result) as mock_git:
        _reinject_wrappers(tmp_path)

    # Should have committed the re-injection
    assert mock_git.call_count == 3  # add, commit, push
    commit_call = mock_git.call_args_list[1]
    assert "Re-inject mcloop crash handlers" in commit_call[0][0]

    # Entry point should now have markers
    content = entry.read_text()
    assert "// mcloop:wrap:begin" in content
    assert "// mcloop:wrap:end" in content


def test_reinject_markers_intact_python(tmp_path):
    """When Python markers are present, no re-injection happens."""
    from mcloop.wrap import PYTHON_WRAPPER, inject

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)

    entry = tmp_path / "main.py"
    original = "print('hello')\n"
    entry.write_text(inject(original, "python"))

    with patch("mcloop.main._git") as mock_git:
        _reinject_wrappers(tmp_path)

    mock_git.assert_not_called()


def test_reinject_markers_stripped_python(tmp_path):
    """When Python markers are stripped, re-injects and commits."""
    from mcloop.wrap import PYTHON_WRAPPER

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)

    entry = tmp_path / "main.py"
    entry.write_text("print('hello')\n")

    git_result = MagicMock()
    git_result.returncode = 0
    with patch("mcloop.main._git", return_value=git_result) as mock_git:
        _reinject_wrappers(tmp_path)

    assert mock_git.call_count == 3
    content = entry.read_text()
    assert "# mcloop:wrap:begin" in content
    assert "# mcloop:wrap:end" in content


def test_reinject_no_entry_point(tmp_path):
    """When canonical wrapper exists but no entry point found, no-op."""
    from mcloop.wrap import PYTHON_WRAPPER

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)
    # No main.py or __main__.py

    with patch("mcloop.main._git") as mock_git:
        _reinject_wrappers(tmp_path)

    mock_git.assert_not_called()


def test_reinject_push_failure_prints_error(tmp_path, capsys):
    """When push fails after re-injection, prints error but doesn't raise."""
    from mcloop.wrap import PYTHON_WRAPPER

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)

    entry = tmp_path / "main.py"
    entry.write_text("print('hello')\n")

    def fake_git(cmd, cwd=None, label="", silent=False):
        result = MagicMock()
        if "push" in cmd:
            result.returncode = 1
        else:
            result.returncode = 0
        return result

    with patch("mcloop.main._git", side_effect=fake_git):
        _reinject_wrappers(tmp_path)

    captured = capsys.readouterr()
    assert "Push after re-injection failed" in captured.out


def test_run_loop_calls_reinject_after_commit(tmp_path):
    """run_loop calls _reinject_wrappers after each successful commit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Do something\n")
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    call_count = 0

    def fake_find_next(tasks):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tasks[0] if tasks else None
        return None

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers") as mock_reinject,
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    mock_reinject.assert_called_once_with(tmp_path)


# --- _check_errors_json ---


def _make_errors_json(tmp_path, entries):
    """Helper to create .mcloop/errors.json with given entries."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir(exist_ok=True)
    import json

    (mcloop_dir / "errors.json").write_text(json.dumps(entries))
    return mcloop_dir / "errors.json"


def _make_plan(tmp_path, content="# Plan\n\n- [ ] First task\n"):
    plan = tmp_path / "PLAN.md"
    plan.write_text(content)
    return plan


def test_check_errors_no_file(tmp_path):
    """Returns True when no errors.json exists."""
    assert _check_errors_json(tmp_path) is True


def test_check_errors_empty_list(tmp_path):
    """Returns True when errors.json is an empty list."""
    _make_errors_json(tmp_path, [])
    assert _check_errors_json(tmp_path) is True


def test_check_errors_invalid_json(tmp_path):
    """Returns True when errors.json has invalid JSON."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "errors.json").write_text("not json{{{")
    assert _check_errors_json(tmp_path) is True


def test_check_errors_user_declines(tmp_path, capsys):
    """Returns True without adding tasks when user says no."""
    entries = [
        {
            "timestamp": "2026-03-10T10:00:00+00:00",
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
        }
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    with patch("builtins.input", return_value="n"):
        result = _check_errors_json(tmp_path)

    assert result is True
    # Plan should not be modified
    plan_text = (tmp_path / "PLAN.md").read_text()
    assert "Fix crash" not in plan_text
    # Summary should have been printed
    out = capsys.readouterr().out
    assert "1 bug(s)" in out
    assert "ValueError" in out


def test_check_errors_user_accepts(tmp_path, capsys):
    """Runs diagnostics, adds fix tasks under ## Bugs, clears errors.json."""
    entries = [
        {
            "timestamp": "2026-03-10T10:00:00+00:00",
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
        },
        {
            "timestamp": "2026-03-10T10:01:00+00:00",
            "exception_type": "IndexError",
            "description": "list index out of range",
            "source_file": "lib.py",
            "line": 99,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)
    # Create source files for diagnostic context
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "lib.py").write_text("y = []\n")

    diag_result = MagicMock(
        success=True,
        output="--- FIX DESCRIPTION ---\nGuard against None\n--- END FIX ---",
    )
    with (
        patch("builtins.input", return_value=""),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="abc123 commit\n")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 2
    plan_text = (tmp_path / "PLAN.md").read_text()
    # Should have ## Bugs section with diagnostic fix descriptions
    assert "## Bugs" in plan_text
    assert "Guard against None" in plan_text
    # Bugs section should come before original task
    lines = plan_text.splitlines()
    bugs_idx = next(i for i, ln in enumerate(lines) if "## Bugs" in ln)
    original_idx = next(i for i, ln in enumerate(lines) if "First task" in ln)
    assert bugs_idx < original_idx

    # errors.json should still exist (cleared after bugs are fixed, not at diagnosis)
    assert (tmp_path / ".mcloop" / "errors.json").exists()

    out = capsys.readouterr().out
    assert "Added 2 fix task(s)" in out


def test_check_errors_default_yes(tmp_path):
    """Empty input (just Enter) defaults to yes, runs diagnostics."""
    entries = [
        {
            "exception_type": "RuntimeError",
            "description": "oops",
        }
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    # Diagnostic fails — falls back to generic description
    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value=""),
        patch("mcloop.main.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    plan_text = (tmp_path / "PLAN.md").read_text()
    assert "## Bugs" in plan_text
    assert "Fix crash: RuntimeError" in plan_text


def test_check_errors_eof(tmp_path):
    """Returns False on EOFError (piped input)."""
    entries = [{"exception_type": "E", "description": "d"}]
    _make_errors_json(tmp_path, entries)

    with patch("builtins.input", side_effect=EOFError):
        result = _check_errors_json(tmp_path)

    assert result is False


def test_check_errors_keyboard_interrupt(tmp_path):
    """Returns False on KeyboardInterrupt."""
    entries = [{"exception_type": "E", "description": "d"}]
    _make_errors_json(tmp_path, entries)

    with patch("builtins.input", side_effect=KeyboardInterrupt):
        result = _check_errors_json(tmp_path)

    assert result is False


def test_check_errors_long_description_truncated(tmp_path, capsys):
    """Long descriptions are truncated in display."""
    entries = [
        {
            "exception_type": "E",
            "description": "x" * 200,
        }
    ]
    _make_errors_json(tmp_path, entries)

    with patch("builtins.input", return_value="n"):
        _check_errors_json(tmp_path)

    out = capsys.readouterr().out
    assert "..." in out


def test_check_errors_no_plan_file(tmp_path, capsys):
    """Handles missing PLAN.md gracefully (no diagnostic sessions)."""
    entries = [{"exception_type": "E", "description": "d"}]
    _make_errors_json(tmp_path, entries)

    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic") as mock_diag,
    ):
        result = _check_errors_json(tmp_path)

    assert result is True
    # Should not run diagnostics when there's no PLAN.md
    mock_diag.assert_not_called()
    out = capsys.readouterr().out
    assert "No PLAN.md found" in out


def test_check_errors_appends_when_no_tasks(tmp_path):
    """Appends ## Bugs section when PLAN.md has no existing task lines."""
    entries = [
        {
            "exception_type": "TypeError",
            "description": "none + int",
            "source_file": "main.py",
            "line": 10,
        }
    ]
    _make_errors_json(tmp_path, entries)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\nJust a description.\n")

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        _check_errors_json(tmp_path)

    plan_text = plan.read_text()
    assert "## Bugs" in plan_text
    assert "Fix crash: TypeError: none + int at main.py:10" in plan_text


def test_check_errors_diagnostic_reads_source(tmp_path):
    """Diagnostic session receives source file content."""
    entries = [
        {
            "exception_type": "KeyError",
            "description": "missing key",
            "source_file": "data.py",
            "line": 5,
        }
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)
    (tmp_path / "data.py").write_text("d = {}\nv = d['x']\n")

    diag_result = MagicMock(
        success=True,
        output="--- FIX DESCRIPTION ---\nUse .get() in data.py:5\n--- END FIX ---",
    )
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="abc commit\n")
        _check_errors_json(tmp_path)

    # Verify source content was passed to diagnostic
    call_kwargs = mock_diag.call_args
    assert "d = {}" in call_kwargs.kwargs.get(
        "source_content", call_kwargs[0][3] if len(call_kwargs[0]) > 3 else ""
    )


def test_check_errors_passes_model(tmp_path):
    """Model parameter is forwarded to diagnostic sessions."""
    entries = [{"exception_type": "E", "description": "d"}]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        _check_errors_json(tmp_path, model="opus")

    assert mock_diag.call_args.kwargs["model"] == "opus"


def test_check_errors_complete_format(tmp_path, capsys):
    """All documented errors.json fields are handled correctly."""
    entries = [
        {
            "id": "a1b2c3d4",
            "timestamp": "2026-03-10T10:00:00+00:00",
            "exception_type": "ValueError",
            "description": "invalid literal for int()",
            "stack_trace": "Traceback...\n  File app.py, line 42\nValueError",
            "source_file": "app.py",
            "line": 42,
            "app_state": {"counter": "5", "mode": "edit"},
            "last_action": "button_click:save",
            "fix_attempts": 0,
        },
        {
            "id": "e5f6a7b8",
            "timestamp": "2026-03-10T10:01:00+00:00",
            "signal": 11,
            "exception_type": "Signal",
            "description": "Received signal 11",
            "stack_trace": "Thread 0:\n  0x00007fff...",
            "source_file": "core.c",
            "line": 100,
            "app_state": {},
            "last_action": "",
            "fix_attempts": 1,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)
    (tmp_path / "app.py").write_text("x = int('bad')\n")
    (tmp_path / "core.c").write_text("int main() { return 0; }\n")

    diag_result = MagicMock(
        success=True,
        output="--- FIX DESCRIPTION ---\nValidate input\n--- END FIX ---",
    )
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="abc commit\n")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 2

    # Summary shows both entries with location
    out = capsys.readouterr().out
    assert "ValueError" in out
    assert "Signal" in out
    assert "app.py:42" in out
    assert "core.c:100" in out

    # fix_attempts incremented after diagnosis
    import json

    updated = json.loads((tmp_path / ".mcloop" / "errors.json").read_text())
    for entry in updated:
        if entry["exception_type"] == "ValueError":
            assert entry["fix_attempts"] == 1
        elif entry["exception_type"] == "Signal":
            assert entry["fix_attempts"] == 2


def test_check_errors_signal_entry_display(tmp_path, capsys):
    """Signal entries display correctly with signal number in description."""
    entries = [
        {
            "id": "deadbeef",
            "timestamp": "2026-03-10T12:00:00+00:00",
            "signal": 6,
            "exception_type": "Signal",
            "description": "Received signal 6",
            "stack_trace": "Thread 0:\n  abort()",
            "source_file": "main.swift",
            "line": 55,
            "app_state": {"view": "main"},
            "last_action": "menu_click:quit",
            "fix_attempts": 0,
        }
    ]
    _make_errors_json(tmp_path, entries)

    with patch("builtins.input", return_value="n"):
        _check_errors_json(tmp_path)

    out = capsys.readouterr().out
    assert "Signal" in out
    assert "Received signal 6" in out
    assert "main.swift:55" in out


# --- _error_signature_hash ---


def test_error_signature_hash_basic():
    """Hash uses exception_type + source_file + line."""
    entry = {"exception_type": "ValueError", "source_file": "app.py", "line": 42}
    h = _error_signature_hash(entry)
    assert isinstance(h, str)
    assert len(h) == 16
    # Same input produces same hash
    assert _error_signature_hash(entry) == h


def test_error_signature_hash_different_errors():
    """Different errors produce different hashes."""
    e1 = {"exception_type": "ValueError", "source_file": "app.py", "line": 42}
    e2 = {"exception_type": "TypeError", "source_file": "app.py", "line": 42}
    e3 = {"exception_type": "ValueError", "source_file": "lib.py", "line": 42}
    assert _error_signature_hash(e1) != _error_signature_hash(e2)
    assert _error_signature_hash(e1) != _error_signature_hash(e3)


def test_error_signature_hash_fallback_stack_trace():
    """Falls back to stack_trace when location fields missing."""
    entry = {"stack_trace": "Traceback...\n  File app.py\nValueError"}
    h = _error_signature_hash(entry)
    assert len(h) == 16


def test_error_signature_hash_fallback_description():
    """Falls back to exception_type + description as last resort."""
    entry = {"exception_type": "RuntimeError", "description": "oops"}
    h = _error_signature_hash(entry)
    assert len(h) == 16


# --- _check_errors_json loop limit ---


def test_check_errors_all_unresolvable(tmp_path, capsys):
    """Returns False when all errors exceed max fix attempts."""
    entries = [
        {
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
            "fix_attempts": _MAX_FIX_ATTEMPTS,
        },
        {
            "exception_type": "TypeError",
            "description": "none + int",
            "source_file": "lib.py",
            "line": 10,
            "fix_attempts": _MAX_FIX_ATTEMPTS + 1,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    result = _check_errors_json(tmp_path)

    assert result is False
    out = capsys.readouterr().out
    assert "unresolvable" in out.lower()
    assert "ValueError" in out
    assert "TypeError" in out
    assert f"attempted {_MAX_FIX_ATTEMPTS}x" in out


def test_check_errors_mixed_resolvable_unresolvable(tmp_path, capsys):
    """Skips unresolvable, diagnoses only resolvable entries."""
    entries = [
        {
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
            "fix_attempts": _MAX_FIX_ATTEMPTS,
        },
        {
            "exception_type": "IndexError",
            "description": "list index out of range",
            "source_file": "lib.py",
            "line": 99,
            "fix_attempts": 1,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    # Only the resolvable error should be diagnosed
    assert mock_diag.call_count == 1
    out = capsys.readouterr().out
    assert "unresolvable" in out.lower()
    assert "1 bug(s)" in out
    assert "Added 1 fix task(s)" in out


def test_check_errors_increments_fix_attempts(tmp_path):
    """Fix attempts are incremented and written back after diagnosis."""
    import json

    entries = [
        {
            "exception_type": "ValueError",
            "description": "bad",
            "source_file": "a.py",
            "line": 1,
            "fix_attempts": 1,
        },
    ]
    errors_path = _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        _check_errors_json(tmp_path)

    # Read back errors.json — fix_attempts should be incremented
    updated = json.loads(errors_path.read_text())
    assert updated[0]["fix_attempts"] == 2


def test_check_errors_new_entry_gets_fix_attempts(tmp_path):
    """Entries without fix_attempts get it set to 1 after first diagnosis."""
    import json

    entries = [
        {
            "exception_type": "RuntimeError",
            "description": "oops",
        },
    ]
    errors_path = _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        _check_errors_json(tmp_path)

    updated = json.loads(errors_path.read_text())
    assert updated[0]["fix_attempts"] == 1


def test_check_errors_just_below_limit_is_resolvable(tmp_path):
    """Entry with fix_attempts = MAX - 1 is still diagnosed (boundary)."""
    entries = [
        {
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
            "fix_attempts": _MAX_FIX_ATTEMPTS - 1,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 1


def test_check_errors_at_limit_is_unresolvable(tmp_path, capsys):
    """Entry with fix_attempts = MAX is unresolvable (boundary)."""
    entries = [
        {
            "exception_type": "ValueError",
            "description": "bad value",
            "source_file": "app.py",
            "line": 42,
            "fix_attempts": _MAX_FIX_ATTEMPTS,
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    result = _check_errors_json(tmp_path)

    assert result is False
    out = capsys.readouterr().out
    assert "unresolvable" in out.lower()


def test_check_errors_non_integer_fix_attempts_treated_as_zero(tmp_path):
    """Non-integer fix_attempts is treated as 0 (resolvable)."""
    entries = [
        {
            "exception_type": "TypeError",
            "description": "none + int",
            "source_file": "lib.py",
            "line": 10,
            "fix_attempts": "not_a_number",
        },
    ]
    _make_errors_json(tmp_path, entries)
    _make_plan(tmp_path)

    diag_result = MagicMock(success=False, output="")
    with (
        patch("builtins.input", return_value="y"),
        patch("mcloop.main.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 1


# --- _insert_bugs_section ---


def test_insert_bugs_section_before_stage(tmp_path):
    """Inserts ## Bugs before first ## Stage header."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\n## Stage 1: Setup\n\n- [ ] Task A\n")

    _insert_bugs_section(plan, ["- [ ] Fix X"])

    text = plan.read_text()
    lines = text.splitlines()
    bugs_idx = next(i for i, ln in enumerate(lines) if "## Bugs" in ln)
    stage_idx = next(i for i, ln in enumerate(lines) if "## Stage" in ln)
    assert bugs_idx < stage_idx


def test_insert_bugs_section_before_checkbox(tmp_path):
    """Inserts ## Bugs before first checkbox when no stages."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\n- [ ] Task A\n")

    _insert_bugs_section(plan, ["- [ ] Fix Y"])

    text = plan.read_text()
    lines = text.splitlines()
    bugs_idx = next(i for i, ln in enumerate(lines) if "## Bugs" in ln)
    task_idx = next(i for i, ln in enumerate(lines) if "Task A" in ln)
    assert bugs_idx < task_idx
    assert "Fix Y" in text


def test_insert_bugs_section_appends_to_existing(tmp_path):
    """Appends to existing ## Bugs section."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\n## Bugs\n\n- [x] Old bug\n\n## Stage 1: Setup\n\n- [ ] Task\n")

    _insert_bugs_section(plan, ["- [ ] New bug"])

    text = plan.read_text()
    assert "Old bug" in text
    assert "New bug" in text
    # New bug should be between Bugs and Stage headers
    lines = text.splitlines()
    new_idx = next(i for i, ln in enumerate(lines) if "New bug" in ln)
    stage_idx = next(i for i, ln in enumerate(lines) if "## Stage" in ln)
    assert new_idx < stage_idx


def test_insert_bugs_section_appends_to_end(tmp_path):
    """Appends ## Bugs at end when no stages or checkboxes."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\nJust a description.")

    _insert_bugs_section(plan, ["- [ ] Fix Z"])

    text = plan.read_text()
    assert "## Bugs" in text
    assert "Fix Z" in text


# --- Bug-only mode ---


def test_run_loop_bug_only_skips_audit_and_stages(tmp_path):
    """Bug-only mode: fixes bugs, skips audit and stage transitions."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n## Stage 1: Core\n- [ ] Add feature\n")
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._launch_app_verification", return_value=None),
    ):
        stuck = run_loop(plan)

    mock_audit.assert_not_called()
    # The feature task should NOT have been worked on
    from mcloop.checklist import parse as cl_parse

    tasks = cl_parse(plan)
    feature = [t for t in tasks if t.stage != "Bugs"][0]
    assert not feature.checked
    assert stuck == []


def test_run_loop_bug_only_returns_stuck_bugs(tmp_path):
    """Bug-only mode: returns stuck bugs when fix fails."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n")
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = False
    result.output = "error"
    result.exit_code = 1

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification") as mock_verify,
    ):
        stuck = run_loop(plan)

    # Task fails all retries → returned as stuck, exits before verification
    assert stuck == ["Fix crash"]
    mock_verify.assert_not_called()


def test_run_loop_bug_only_verifies_app(tmp_path, capsys):
    """Bug-only mode: launches app verification after all bugs fixed."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n")
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification", return_value=None) as mock_verify,
    ):
        run_loop(plan)

    mock_verify.assert_called_once_with(tmp_path)


def test_run_loop_bug_only_clears_errors_json(tmp_path):
    """Bug-only mode: clears errors.json after all bugs fixed and verified."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n")
    (tmp_path / ".git").mkdir()
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    errors_path = mcloop_dir / "errors.json"
    errors_path.write_text('[{"exception_type": "ValueError"}]')

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification", return_value=None),
    ):
        run_loop(plan)

    # errors.json should be deleted after successful bug-only completion
    assert not errors_path.exists()


def test_run_loop_bug_only_keeps_errors_json_on_failure(tmp_path):
    """Bug-only mode: keeps errors.json when verification fails."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n")
    (tmp_path / ".git").mkdir()
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    errors_path = mcloop_dir / "errors.json"
    errors_path.write_text('[{"exception_type": "ValueError"}]')

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification", return_value="App crashed"),
    ):
        run_loop(plan)

    # errors.json should still exist when verification failed
    assert errors_path.exists()


def test_run_loop_bug_only_keeps_errors_json_on_stuck(tmp_path):
    """Bug-only mode: keeps errors.json when bugs could not be fixed."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("## Bugs\n- [ ] Fix crash\n")
    (tmp_path / ".git").mkdir()
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    errors_path = mcloop_dir / "errors.json"
    errors_path.write_text('[{"exception_type": "ValueError"}]')

    result = MagicMock()
    result.success = False
    result.output = "error"
    result.exit_code = 1

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification"),
    ):
        run_loop(plan)

    # errors.json should still exist when bugs couldn't be fixed
    assert errors_path.exists()


def test_run_loop_no_bugs_runs_normally(tmp_path):
    """Without ## Bugs, run_loop does not activate bug-only mode."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\nNo tasks.\n")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=False)

    mock_audit.assert_called_once()
