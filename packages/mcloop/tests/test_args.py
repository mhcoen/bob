"""Unit tests for CLI argument parsing and main helpers."""

from unittest.mock import MagicMock, patch

import pytest

from mcloop.main import _parse_args, _run_audit_fix_cycle, run_loop


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

    def fake_audit(project_dir, log_dir, model=None):
        bugs_path.write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with patch("mcloop.main.run_audit", side_effect=fake_audit) as mock_audit, \
         patch("mcloop.main.run_bug_fix") as mock_fix:
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_audit.assert_called_once()
    mock_fix.assert_not_called()
    assert not bugs_path.exists()


def test_run_audit_fix_cycle_with_bugs(tmp_path):
    """When audit finds bugs, fix session runs and BUGS.md is deleted."""
    bugs_path = tmp_path / "BUGS.md"
    bug_content = "# Bugs\n\n## foo.py:1 — crash\n**Severity**: high\nBad.\n"

    def fake_audit(project_dir, log_dir, model=None):
        bugs_path.write_text(bug_content)
        return _make_result()

    check_result = MagicMock()
    check_result.passed = False

    with patch("mcloop.main.run_audit", side_effect=fake_audit), \
         patch("mcloop.main.run_bug_fix", return_value=_make_result()) as mock_fix, \
         patch("mcloop.main._has_meaningful_changes", return_value=False):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_called_once()
    assert not bugs_path.exists()


def test_run_audit_fix_cycle_audit_failure(tmp_path):
    """When audit session fails, fix session is not run."""
    with patch("mcloop.main.run_audit", return_value=_make_result(success=False, exit_code=1)), \
         patch("mcloop.main.run_bug_fix") as mock_fix:
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_not_called()


def test_run_audit_fix_cycle_no_bugs_md(tmp_path):
    """When audit succeeds but BUGS.md not written, fix session is not run."""
    with patch("mcloop.main.run_audit", return_value=_make_result()), \
         patch("mcloop.main.run_bug_fix") as mock_fix:
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_not_called()


def test_run_loop_no_audit_skips_audit(tmp_path):
    """When no_audit=True, _run_audit_fix_cycle is not called."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")

    with patch("mcloop.main._checkpoint"), \
         patch("mcloop.main.parse", return_value=[]), \
         patch("mcloop.main._run_audit_fix_cycle") as mock_audit, \
         patch("mcloop.main._print_summary"), \
         patch("mcloop.main.notify"):
        run_loop(plan, no_audit=True)

    mock_audit.assert_not_called()


def test_run_loop_audit_called_by_default(tmp_path):
    """By default, _run_audit_fix_cycle is called after all tasks complete."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")

    with patch("mcloop.main._checkpoint"), \
         patch("mcloop.main.parse", return_value=[]), \
         patch("mcloop.main._run_audit_fix_cycle") as mock_audit, \
         patch("mcloop.main._print_summary"), \
         patch("mcloop.main.notify"):
        run_loop(plan, no_audit=False)

    mock_audit.assert_called_once()


def test_run_audit_fix_cycle_commits_when_checks_pass(tmp_path):
    """When fix session succeeds and checks pass, changes are committed."""
    bugs_path = tmp_path / "BUGS.md"

    def fake_audit(project_dir, log_dir, model=None):
        bugs_path.write_text("# Bugs\n\n## foo.py:1 — crash\nBad.\n")
        return _make_result()

    check_result = MagicMock()
    check_result.passed = True

    with patch("mcloop.main.run_audit", side_effect=fake_audit), \
         patch("mcloop.main.run_bug_fix", return_value=_make_result()), \
         patch("mcloop.main._has_meaningful_changes", return_value=True), \
         patch("mcloop.main.run_checks", return_value=check_result), \
         patch("mcloop.main._commit") as mock_commit:
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_commit.assert_called_once_with(tmp_path, "Fix bugs from audit")
