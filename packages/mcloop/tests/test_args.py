"""Unit tests for CLI argument parsing and main helpers."""

import argparse
import dataclasses
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from plan_fixtures import assert_canonical_checkbox, canonical_plan_text

import mcloop.lifecycle as lifecycle_mod
from mcloop.audit import AuditResult, _run_audit_fix_cycle, _run_single_audit_round
from mcloop.errors import (
    _MAX_FIX_ATTEMPTS,
    _check_errors_json,
    _insert_bugs_section,
)
from mcloop.git_ops import _snapshot_worktree, _worktree_status
from mcloop.install_cmd import (
    _HOOK_SCRIPTS,
    _check_reviewer,
    _check_rtk,
    _cmd_install,
    _cmd_uninstall,
    _install_hooks,
    _install_recommended_permissions,
    _load_mcloop_config,
    _merge_settings,
    _print_install_summary,
    _print_uninstall_summary,
    _remove_config_json,
    _remove_hooks_dir,
    _remove_recommended_perms,
    _remove_telegram_env,
    _setup_env_security,
    _setup_sandbox,
    _setup_telegram,
    _unmerge_settings,
)
from mcloop.investigate_cmd import (
    MAX_VERIFICATION_ROUNDS,
    _append_verification_failure,
    _copy_project_settings,
    _dispatch_auto_action,
    _handle_auto_task,
    _handle_user_task,
    _investigation_failed,
    _investigation_passed,
    _launch_app_verification,
    _read_repro_steps,
    _replay_repro_steps,
    _verify_gui_survival,
)
from mcloop.investigator import _find_recent_crash_report, gather_bug_context
from mcloop.main import (
    BuildResult,
    ChainEntry,
    RunStatus,
    _all_tasks,
    _check_interrupted,
    _check_user_input,
    _main,
    _maybe_auto_wrap,
    _parse_args,
    _reinject_wrappers,
    _run_batch,
    _run_build,
    _save_interrupt_state,
    _write_eliminated_json,
    _write_ruledout_to_plan,
    resolve_chain,
    run_loop,
)
from mcloop.review_integration import (
    _cleanup_stale_reviews,
    _collect_review_findings,
    _get_commit_hash,
    _reviewer_procs,
    _spawn_reviewer,
    _terminate_reviewers,
)
from mcloop.session_context import SessionContext


def _parse(*argv):
    with patch("sys.argv", ["mcloop", *argv]):
        return _parse_args()


def _chain_args(
    *,
    cli: str | None = None,
    model: str | None = None,
    fallback_model: str | None = None,
) -> object:
    return argparse.Namespace(cli=cli, model=model, fallback_model=fallback_model)


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


def test_install_subcommand():
    args = _parse("install")
    assert args.command == "install"
    assert args.dry_run is False


def test_install_subcommand_dry_run():
    args = _parse("install", "--dry-run")
    assert args.command == "install"
    assert args.dry_run is True


def test_uninstall_subcommand():
    args = _parse("uninstall")
    assert args.command == "uninstall"
    assert args.dry_run is False


def test_uninstall_subcommand_dry_run():
    args = _parse("uninstall", "--dry-run")
    assert args.command == "uninstall"
    assert args.dry_run is True


def test_install_subcommand_with_file():
    args = _parse("--file", "custom.md", "install")
    assert args.command == "install"
    assert args.file == "custom.md"


class TestMaintainSubparserFlags:
    """`--cli`/`--model`/`--stop-after-one` live on the maintain subparser.

    The pre-subcommand spelling (parent-level position) is rejected by
    an argv prescan before parse_args, because argparse's subparser
    scoping silently overwrites the parent value with the subparser's
    default and post-parse validation cannot detect the bleed.
    """

    def test_maintain_canonical_cli(self):
        args = _parse("maintain", "--cli", "codex")
        assert args.command == "maintain"
        assert args.cli == "codex"

    def test_maintain_canonical_model(self):
        args = _parse("maintain", "--model", "opus")
        assert args.command == "maintain"
        assert args.model == "opus"

    def test_maintain_canonical_stop_after_one(self):
        args = _parse("maintain", "--stop-after-one")
        assert args.command == "maintain"
        assert args.stop_after_one is True

    def test_maintain_no_flags_uses_defaults(self):
        args = _parse("maintain")
        assert args.command == "maintain"
        assert args.cli is None
        assert args.model is None
        assert args.stop_after_one is False

    def test_pre_subcommand_cli_rejected(self, capsys):
        with pytest.raises(SystemExit):
            _parse("--cli", "codex", "maintain")
        err = capsys.readouterr().err
        assert "--cli" in err
        assert "maintain --cli" in err

    def test_pre_subcommand_cli_equals_rejected(self, capsys):
        with pytest.raises(SystemExit):
            _parse("--cli=codex", "maintain")
        err = capsys.readouterr().err
        assert "--cli" in err
        assert "maintain --cli" in err

    def test_pre_subcommand_model_rejected(self, capsys):
        with pytest.raises(SystemExit):
            _parse("--model", "opus", "maintain")
        err = capsys.readouterr().err
        assert "--model" in err
        assert "maintain --model" in err

    def test_pre_subcommand_stop_after_one_rejected(self, capsys):
        with pytest.raises(SystemExit):
            _parse("--stop-after-one", "maintain")
        err = capsys.readouterr().err
        assert "--stop-after-one" in err
        assert "maintain --stop-after-one" in err

    def test_loop_only_flag_with_maintain_still_gated(self, capsys):
        # Flags that did NOT move onto the maintain subparser remain
        # loop-only; the post-parse gate fires on them (not the prescan).
        with pytest.raises(SystemExit):
            _parse("--max-retries", "5", "maintain")
        err = capsys.readouterr().err
        assert "--max-retries" in err


def test_uninstall_subcommand_with_file():
    args = _parse("--file", "custom.md", "uninstall")
    assert args.command == "uninstall"
    assert args.file == "custom.md"


def test_idea_subcommand():
    args = _parse("idea", "cool feature")
    assert args.command == "idea"
    assert args.text == "cool feature"


def test_idea_subcommand_missing_text_exits():
    with pytest.raises(SystemExit):
        _parse("idea")


def test_invalid_subcommand_exits():
    with pytest.raises(SystemExit):
        _parse("bogus")


# --- _cmd_install: claude check ---


def test_install_exits_when_claude_not_on_path(tmp_path):
    with patch("mcloop.install_cmd.shutil.which", return_value=None):
        with pytest.raises(SystemExit) as exc:
            _cmd_install(tmp_path)
    assert exc.value.code == 1


def test_install_exits_when_claude_not_on_path_message(tmp_path, capsys):
    with patch("mcloop.install_cmd.shutil.which", return_value=None):
        with pytest.raises(SystemExit):
            _cmd_install(tmp_path)
    err = capsys.readouterr().err
    assert "not found on PATH" in err
    assert "npm install -g @anthropic-ai/claude-code" in err


def test_install_prints_claude_version(tmp_path, capsys):
    proc = MagicMock(returncode=0, stdout="claude 1.2.3\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path)
    out = capsys.readouterr().out
    assert "Found claude: claude 1.2.3" in out


def test_install_exits_when_version_fails(tmp_path, capsys):
    proc = MagicMock(returncode=1, stdout="")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
    ):
        with pytest.raises(SystemExit) as exc:
            _cmd_install(tmp_path)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "claude --version" in err


def test_install_calls_claude_version_with_found_path(tmp_path):
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/opt/bin/claude"),
        patch("subprocess.run", return_value=proc) as mock_run,
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path)
    mock_run.assert_called_once_with(
        ["/opt/bin/claude", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )


# --- _install_hooks ---


def _setup_hooks(tmp_path, create_sources=True):
    """Create a fake repo root with hook source files."""
    repo_root = tmp_path / "repo"
    fake_mcloop_dir = repo_root / "mcloop"
    fake_mcloop_dir.mkdir(parents=True)
    (fake_mcloop_dir / "main.py").write_text("")
    if create_sources:
        for name in _HOOK_SCRIPTS:
            (repo_root / name).write_text(f"# {name}\n")
    return repo_root, fake_mcloop_dir


def test_install_hooks_copies_scripts(tmp_path, capsys):
    """Copies hook scripts from repo root to hooks dir."""
    repo_root, fake_mcloop_dir = _setup_hooks(tmp_path)
    home = tmp_path / "home"

    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch.object(Path, "home", return_value=home):
            _install_hooks(dry_run=False)
    finally:
        install_mod.__file__ = orig_file

    dest_dir = home / ".mcloop" / "hooks"
    for name in _HOOK_SCRIPTS:
        assert (dest_dir / name).exists()
        assert (dest_dir / name).read_text() == f"# {name}\n"

    out = capsys.readouterr().out
    assert "copied:" in out


def test_install_hooks_skips_existing(tmp_path, capsys):
    """Skips copy when destination file already exists."""
    repo_root, fake_mcloop_dir = _setup_hooks(tmp_path)
    home = tmp_path / "home"
    hooks_dir = home / ".mcloop" / "hooks"
    hooks_dir.mkdir(parents=True)

    for name in _HOOK_SCRIPTS:
        (hooks_dir / name).write_text(f"# old {name}\n")

    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch.object(Path, "home", return_value=home):
            _install_hooks(dry_run=False)
    finally:
        install_mod.__file__ = orig_file

    # Files should NOT be overwritten
    for name in _HOOK_SCRIPTS:
        assert (hooks_dir / name).read_text() == f"# old {name}\n"

    out = capsys.readouterr().out
    assert "skip (exists):" in out


def test_install_hooks_dry_run(tmp_path, capsys):
    """Dry run prints what would be copied but doesn't create files."""
    repo_root, fake_mcloop_dir = _setup_hooks(tmp_path)
    home = tmp_path / "home"

    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch.object(Path, "home", return_value=home):
            _install_hooks(dry_run=True)
    finally:
        install_mod.__file__ = orig_file

    hooks_dir = home / ".mcloop" / "hooks"
    assert not hooks_dir.exists()

    out = capsys.readouterr().out
    assert "would copy:" in out


def test_install_hooks_warns_missing_source(tmp_path, capsys):
    """Warns when a source hook script is not found."""
    _repo_root, fake_mcloop_dir = _setup_hooks(tmp_path, create_sources=False)
    home = tmp_path / "home"

    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch.object(Path, "home", return_value=home):
            _install_hooks(dry_run=False)
    finally:
        install_mod.__file__ = orig_file

    err = capsys.readouterr().err
    assert "Warning: hook source not found" in err


# --- _merge_settings ---


def test_merge_settings_creates_new_file(tmp_path, capsys):
    """Creates settings.json when it doesn't exist."""
    home = tmp_path / "home"
    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=False)

    settings_path = home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "hooks" in data
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert len(data["hooks"]["SessionStart"]) == 1
    assert "telegram-permission-hook.py" in data["hooks"]["PreToolUse"][0]["command"]
    assert "session-start-hook.py" in data["hooks"]["SessionStart"][0]["command"]

    out = capsys.readouterr().out
    assert "added:" in out


def test_merge_settings_preserves_existing(tmp_path, capsys):
    """Preserves existing settings and adds hook entries."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))

    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=False)

    data = json.loads(settings_path.read_text())
    assert data["permissions"] == {"allow": ["Read"]}
    assert "hooks" in data
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_merge_settings_skips_existing_entries(tmp_path, capsys):
    """Skips hook entries that already exist."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "type": "command",
                    "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
                },
            ],
            "SessionStart": [
                {
                    "type": "command",
                    "command": "python3 ~/.mcloop/hooks/session-start-hook.py",
                },
            ],
        },
    }
    settings_path.write_text(json.dumps(existing))

    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=False)

    data = json.loads(settings_path.read_text())
    # Should not duplicate
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert len(data["hooks"]["SessionStart"]) == 1

    out = capsys.readouterr().out
    assert "skip (exists):" in out


def test_merge_settings_keeps_other_hooks(tmp_path):
    """Preserves existing hook entries from other tools."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "PreToolUse": [
                {"type": "command", "command": "other-hook.sh"},
            ],
        },
    }
    settings_path.write_text(json.dumps(existing))

    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=False)

    data = json.loads(settings_path.read_text())
    commands = [e["command"] for e in data["hooks"]["PreToolUse"]]
    assert "other-hook.sh" in commands
    assert "python3 ~/.mcloop/hooks/telegram-permission-hook.py" in commands
    assert len(data["hooks"]["PreToolUse"]) == 2


def test_merge_settings_dedupes_other_path_telegram_hook(tmp_path, capsys):
    """Removes a telegram hook registered at another path (e.g. prior bob install)."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /Users/x/.claude/hooks/telegram-permission-hook.py",
                        }
                    ],
                },
                {"type": "command", "command": "other-hook.sh"},
            ],
        },
    }
    settings_path.write_text(json.dumps(existing))

    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=False)

    data = json.loads(settings_path.read_text())
    pre = data["hooks"]["PreToolUse"]
    tg = [
        c
        for e in pre
        for c in ([e.get("command")] + [h.get("command") for h in e.get("hooks", [])])
        if c and "telegram-permission-hook.py" in c
    ]
    assert tg == ["python3 ~/.mcloop/hooks/telegram-permission-hook.py"]  # only mcloop's
    flat = [e.get("command") for e in pre]
    assert "other-hook.sh" in flat  # unrelated hook preserved
    assert "removed stale telegram hook:" in capsys.readouterr().out


def test_merge_settings_dry_run(tmp_path, capsys):
    """Dry run prints what would be added with diff but doesn't write."""
    home = tmp_path / "home"
    with patch.object(Path, "home", return_value=home):
        _merge_settings(dry_run=True)

    settings_path = home / ".claude" / "settings.json"
    assert not settings_path.exists()

    out = capsys.readouterr().out
    assert "would add:" in out
    assert "---" in out
    assert "++" in out
    assert "telegram-permission-hook" in out


def test_merge_settings_invalid_json(tmp_path, capsys):
    """Exits with error on invalid JSON."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{broken")

    with patch.object(Path, "home", return_value=home):
        with pytest.raises(SystemExit) as exc:
            _merge_settings(dry_run=False)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "invalid JSON" in err


def test_merge_settings_not_object(tmp_path, capsys):
    """Exits with error when settings.json is not a JSON object."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('"just a string"')

    with patch.object(Path, "home", return_value=home):
        with pytest.raises(SystemExit) as exc:
            _merge_settings(dry_run=False)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not a JSON object" in err


# --- _unmerge_settings ---


def test_unmerge_settings_no_file(tmp_path, capsys):
    """Skips when settings.json does not exist."""
    home = tmp_path / "home"
    with patch.object(Path, "home", return_value=home):
        results = _unmerge_settings(dry_run=False)
    assert len(results) == 1
    assert "no settings file" in results[0][1]
    out = capsys.readouterr().out
    assert "does not exist" in out


def test_unmerge_settings_removes_mcloop_entries(tmp_path, capsys):
    """Removes only entries pointing at ~/.mcloop/hooks/."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
                        },
                        {
                            "type": "command",
                            "command": "python3 ~/other-hook.py",
                        },
                    ],
                    "SessionStart": [
                        {
                            "type": "command",
                            "command": "python3 ~/.mcloop/hooks/session-start-hook.py",
                        },
                    ],
                },
                "other_setting": True,
            }
        )
    )

    with patch.object(Path, "home", return_value=home):
        results = _unmerge_settings(dry_run=False)

    settings = json.loads(settings_path.read_text())
    # Other hook preserved
    assert len(settings["hooks"]["PreToolUse"]) == 1
    assert "other-hook.py" in settings["hooks"]["PreToolUse"][0]["command"]
    # SessionStart removed entirely (empty list deleted)
    assert "SessionStart" not in settings["hooks"]
    # Other settings preserved
    assert settings["other_setting"] is True
    # Results contain removed entries
    removed = [r for r in results if "removed" in r[1]]
    assert len(removed) == 2


def test_unmerge_settings_no_mcloop_entries(tmp_path, capsys):
    """No changes when no mcloop entries exist."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {"type": "command", "command": "python3 ~/other.py"},
                ],
            },
        }
    )
    settings_path.write_text(original)

    with patch.object(Path, "home", return_value=home):
        results = _unmerge_settings(dry_run=False)

    # File unchanged
    assert settings_path.read_text() == original
    assert any("no mcloop entries" in r[1] for r in results)


def test_unmerge_settings_dry_run(tmp_path, capsys):
    """Dry run prints diff but does not modify file."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "type": "command",
                        "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
                    },
                ],
            },
        }
    )
    settings_path.write_text(original)

    with patch.object(Path, "home", return_value=home):
        results = _unmerge_settings(dry_run=True)

    # File unchanged
    assert settings_path.read_text() == original
    out = capsys.readouterr().out
    assert "would remove" in out
    assert any("dry run" in r[1] for r in results)


def test_unmerge_settings_invalid_json(tmp_path, capsys):
    """Exits on invalid JSON."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{bad json")

    with patch.object(Path, "home", return_value=home):
        with pytest.raises(SystemExit) as exc:
            _unmerge_settings(dry_run=False)
    assert exc.value.code == 1


def test_unmerge_settings_not_object(tmp_path, capsys):
    """Exits on non-object JSON."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('"just a string"')

    with patch.object(Path, "home", return_value=home):
        with pytest.raises(SystemExit) as exc:
            _unmerge_settings(dry_run=False)
    assert exc.value.code == 1


def test_unmerge_settings_empty_hooks(tmp_path, capsys):
    """Removes hooks key when all entries are removed."""
    home = tmp_path / "home"
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
                        },
                    ],
                },
                "other": 1,
            }
        )
    )

    with patch.object(Path, "home", return_value=home):
        _unmerge_settings(dry_run=False)

    settings = json.loads(settings_path.read_text())
    assert "hooks" not in settings
    assert settings["other"] == 1


def test_cmd_uninstall_calls_unmerge(tmp_path, capsys):
    """_cmd_uninstall calls all removal functions and prints summary."""
    with (
        patch(
            "mcloop.install_cmd._unmerge_settings",
            return_value=[("Settings (PreToolUse)", "removed")],
        ) as mock_unmerge,
        patch(
            "mcloop.install_cmd._remove_telegram_env",
            return_value=("telegram-hook.env", "removed"),
        ) as mock_tg,
        patch(
            "mcloop.install_cmd._remove_hooks_dir",
            return_value=[("hooks directory", "removed")],
        ) as mock_hooks,
        patch(
            "mcloop.install_cmd._remove_config_json",
            return_value=("config.json", "removed"),
        ) as mock_config,
        patch(
            "mcloop.install_cmd._remove_recommended_perms",
            return_value=("recommended-permissions.json", "removed"),
        ) as mock_perms,
    ):
        _cmd_uninstall(tmp_path)
    mock_unmerge.assert_called_once_with(dry_run=False)
    mock_tg.assert_called_once_with(dry_run=False)
    mock_hooks.assert_called_once_with(dry_run=False)
    mock_config.assert_called_once_with(dry_run=False)
    mock_perms.assert_called_once_with(dry_run=False)
    out = capsys.readouterr().out
    assert "uninstall" in out
    assert "Uninstall summary:" in out
    assert "Removed:" in out
    assert "Left in place:" in out
    assert "permissions.allow" in out
    assert "project-level .mcloop/ directories" in out
    assert "PLAN.md" in out
    assert "logs/" in out
    assert "sandbox" in out


def test_cmd_uninstall_dry_run(tmp_path, capsys):
    """_cmd_uninstall passes dry_run through."""
    with (
        patch(
            "mcloop.install_cmd._unmerge_settings",
            return_value=[("Settings (PreToolUse)", "would remove (dry run)")],
        ) as mock_unmerge,
        patch(
            "mcloop.install_cmd._remove_telegram_env",
            return_value=("telegram-hook.env", "would remove"),
        ) as mock_tg,
        patch(
            "mcloop.install_cmd._remove_hooks_dir",
            return_value=[("hooks directory", "would remove")],
        ) as mock_hooks,
        patch(
            "mcloop.install_cmd._remove_config_json",
            return_value=("config.json", "would remove"),
        ) as mock_config,
        patch(
            "mcloop.install_cmd._remove_recommended_perms",
            return_value=("recommended-permissions.json", "would remove"),
        ) as mock_perms,
    ):
        _cmd_uninstall(tmp_path, dry_run=True)
    mock_unmerge.assert_called_once_with(dry_run=True)
    mock_tg.assert_called_once_with(dry_run=True)
    mock_hooks.assert_called_once_with(dry_run=True)
    mock_config.assert_called_once_with(dry_run=True)
    mock_perms.assert_called_once_with(dry_run=True)
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "Would remove:" in out


def test_cmd_uninstall_skipped_items(tmp_path, capsys):
    """_cmd_uninstall shows 'Already absent' for items not found."""
    with (
        patch(
            "mcloop.install_cmd._unmerge_settings",
            return_value=[("Settings", "skipped (no settings file)")],
        ),
        patch(
            "mcloop.install_cmd._remove_telegram_env",
            return_value=("telegram-hook.env", "skipped (not found)"),
        ),
        patch(
            "mcloop.install_cmd._remove_hooks_dir",
            return_value=[("hooks directory", "skipped (not found)")],
        ),
        patch(
            "mcloop.install_cmd._remove_config_json",
            return_value=("config.json", "skipped (not found)"),
        ),
        patch(
            "mcloop.install_cmd._remove_recommended_perms",
            return_value=("recommended-permissions.json", "skipped (not found)"),
        ),
    ):
        _cmd_uninstall(tmp_path)
    out = capsys.readouterr().out
    assert "Already absent:" in out
    assert "Left in place:" in out
    # No "Removed:" section when nothing was removed
    assert "Removed:" not in out


def test_cmd_uninstall_mixed_results(tmp_path, capsys):
    """_cmd_uninstall shows mixed removed/skipped/left correctly."""
    with (
        patch(
            "mcloop.install_cmd._unmerge_settings",
            return_value=[("Settings (PreToolUse)", "removed")],
        ),
        patch(
            "mcloop.install_cmd._remove_telegram_env",
            return_value=("telegram-hook.env", "skipped (not found)"),
        ),
        patch(
            "mcloop.install_cmd._remove_hooks_dir",
            return_value=[("hooks directory", "removed")],
        ),
        patch(
            "mcloop.install_cmd._remove_config_json",
            return_value=("config.json", "skipped (not found)"),
        ),
        patch(
            "mcloop.install_cmd._remove_recommended_perms",
            return_value=("recommended-permissions.json", "removed"),
        ),
    ):
        _cmd_uninstall(tmp_path)
    out = capsys.readouterr().out
    assert "Removed:" in out
    assert "Already absent:" in out
    assert "Left in place:" in out


# --- _remove_telegram_env ---


def test_remove_telegram_env_exists(tmp_path, capsys):
    """Removes the file when it exists."""
    env_file = tmp_path / "telegram-hook.env"
    env_file.write_text("TOKEN=abc\n")
    with patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file):
        component, status = _remove_telegram_env()
    assert not env_file.exists()
    assert status == "removed"
    assert component == "telegram-hook.env"
    assert "Removed" in capsys.readouterr().out


def test_remove_telegram_env_not_found(tmp_path, capsys):
    """Skips when file does not exist."""
    env_file = tmp_path / "telegram-hook.env"
    with patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file):
        component, status = _remove_telegram_env()
    assert "not found" in status
    assert "not found" in capsys.readouterr().out


def test_remove_telegram_env_dry_run(tmp_path, capsys):
    """Dry run prints but does not delete."""
    env_file = tmp_path / "telegram-hook.env"
    env_file.write_text("TOKEN=abc\n")
    with patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file):
        component, status = _remove_telegram_env(dry_run=True)
    assert env_file.exists()
    assert "would remove" in status
    assert "Would remove" in capsys.readouterr().out


# --- _remove_hooks_dir ---


def test_remove_hooks_dir_exists(tmp_path, capsys):
    """Removes the hooks directory when it exists."""
    fake_home = tmp_path / "fake_home"
    fake_mcloop = fake_home / ".mcloop"
    fake_mcloop.mkdir(parents=True)
    fake_hooks = fake_mcloop / "hooks"
    fake_hooks.mkdir()
    (fake_hooks / "some-hook.py").write_text("# hook\n")
    with patch("mcloop.install_cmd.Path.home", return_value=fake_home):
        results = _remove_hooks_dir()
    assert not fake_hooks.exists()
    assert len(results) == 1
    assert results[0] == ("hooks directory", "removed")
    assert "Removed" in capsys.readouterr().out


def test_remove_hooks_dir_not_found(tmp_path, capsys):
    """Skips when hooks directory does not exist."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    with patch("mcloop.install_cmd.Path.home", return_value=fake_home):
        results = _remove_hooks_dir()
    assert len(results) == 1
    assert "not found" in results[0][1]
    assert "not found" in capsys.readouterr().out


def test_remove_hooks_dir_dry_run(tmp_path, capsys):
    """Dry run lists each file and does not delete."""
    fake_home = tmp_path / "fake_home"
    fake_hooks = fake_home / ".mcloop" / "hooks"
    fake_hooks.mkdir(parents=True)
    (fake_hooks / "hook-a.py").write_text("# a\n")
    (fake_hooks / "hook-b.py").write_text("# b\n")
    with patch("mcloop.install_cmd.Path.home", return_value=fake_home):
        results = _remove_hooks_dir(dry_run=True)
    assert fake_hooks.exists()
    assert len(results) == 2
    names = {c for c, _ in results}
    assert "hooks/hook-a.py" in names
    assert "hooks/hook-b.py" in names
    for _, status in results:
        assert status == "would remove"
    out = capsys.readouterr().out
    assert "Would delete" in out
    assert "hook-a.py" in out
    assert "hook-b.py" in out


def test_remove_hooks_dir_dry_run_empty(tmp_path, capsys):
    """Dry run on empty hooks dir reports the directory itself."""
    fake_home = tmp_path / "fake_home"
    fake_hooks = fake_home / ".mcloop" / "hooks"
    fake_hooks.mkdir(parents=True)
    with patch("mcloop.install_cmd.Path.home", return_value=fake_home):
        results = _remove_hooks_dir(dry_run=True)
    assert results == [("hooks directory", "would remove")]
    assert "empty" in capsys.readouterr().out


# --- _remove_config_json ---


def test_remove_config_json_exists(tmp_path, capsys):
    """Removes config.json when it exists."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"keep_anthropic_api_key": true}\n')
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", config_file):
        component, status = _remove_config_json()
    assert not config_file.exists()
    assert status == "removed"
    assert component == "config.json"
    assert "Removed" in capsys.readouterr().out


def test_remove_config_json_not_found(tmp_path, capsys):
    """Skips when config.json does not exist."""
    config_file = tmp_path / "config.json"
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", config_file):
        component, status = _remove_config_json()
    assert "not found" in status
    assert "not found" in capsys.readouterr().out


def test_remove_config_json_dry_run(tmp_path, capsys):
    """Dry run prints but does not delete."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"keep_anthropic_api_key": true}\n')
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", config_file):
        component, status = _remove_config_json(dry_run=True)
    assert config_file.exists()
    assert "would remove" in status
    assert "Would remove" in capsys.readouterr().out


# --- _remove_recommended_perms ---


def test_remove_recommended_perms_exists(tmp_path, capsys):
    """Removes recommended-permissions.json when it exists."""
    perms_file = tmp_path / "recommended-permissions.json"
    perms_file.write_text('["/some/path"]\n')
    with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", perms_file):
        component, status = _remove_recommended_perms()
    assert not perms_file.exists()
    assert status == "removed"
    assert component == "recommended-permissions.json"
    assert "Removed" in capsys.readouterr().out


def test_remove_recommended_perms_not_found(tmp_path, capsys):
    """Skips when file does not exist."""
    perms_file = tmp_path / "recommended-permissions.json"
    with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", perms_file):
        component, status = _remove_recommended_perms()
    assert "not found" in status
    assert "not found" in capsys.readouterr().out


def test_remove_recommended_perms_dry_run(tmp_path, capsys):
    """Dry run prints but does not delete."""
    perms_file = tmp_path / "recommended-permissions.json"
    perms_file.write_text('["/some/path"]\n')
    with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", perms_file):
        component, status = _remove_recommended_perms(dry_run=True)
    assert perms_file.exists()
    assert "would remove" in status
    assert "Would remove" in capsys.readouterr().out


# --- _setup_telegram ---


def test_setup_telegram_env_vars(tmp_path, capsys):
    """Uses credentials from environment when both vars are set."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
    ):
        _setup_telegram(dry_run=False)

    assert env_file.exists()
    content = env_file.read_text()
    assert "TELEGRAM_BOT_TOKEN=tok" in content
    assert "TELEGRAM_CHAT_ID=123" in content
    out = capsys.readouterr().out
    assert "using credentials from environment" in out
    assert "Telegram Desktop" in out


def test_setup_telegram_env_vars_dry_run(tmp_path, capsys):
    """Dry run with env vars shows diff but does not write file."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
    ):
        _setup_telegram(dry_run=True)

    assert not env_file.exists()
    out = capsys.readouterr().out
    assert "using credentials from environment" in out
    assert "---" in out
    assert "+TELEGRAM_BOT_TOKEN=tok" in out


def test_setup_telegram_existing_file(tmp_path, capsys):
    """Skips prompting when env file already exists."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("TELEGRAM_BOT_TOKEN=old\nTELEGRAM_CHAT_ID=old\n")

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
    ):
        _setup_telegram(dry_run=False)

    out = capsys.readouterr().out
    assert "existing credentials" in out
    assert "Telegram Desktop" in out
    # File should not be overwritten
    assert "old" in env_file.read_text()


def test_setup_telegram_interactive_prompt(tmp_path, capsys):
    """Prompts interactively and writes env file."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=["my-token", "my-chat"]),
    ):
        _setup_telegram(dry_run=False)

    assert env_file.exists()
    content = env_file.read_text()
    assert "TELEGRAM_BOT_TOKEN=my-token" in content
    assert "TELEGRAM_CHAT_ID=my-chat" in content
    out = capsys.readouterr().out
    assert "Saved credentials" in out
    assert "Telegram Desktop" in out


def test_setup_telegram_interactive_empty_token(tmp_path, capsys):
    """Skips when user enters empty bot token."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=[""]),
    ):
        _setup_telegram(dry_run=False)

    assert not env_file.exists()
    err = capsys.readouterr().err
    assert "no bot token" in err


def test_setup_telegram_interactive_empty_chat_id(tmp_path, capsys):
    """Skips when user enters empty chat ID."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=["my-token", ""]),
    ):
        _setup_telegram(dry_run=False)

    assert not env_file.exists()
    err = capsys.readouterr().err
    assert "no chat ID" in err


def test_setup_telegram_interactive_eof(tmp_path, capsys):
    """Handles EOFError gracefully."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=EOFError),
    ):
        _setup_telegram(dry_run=False)

    assert not env_file.exists()
    out = capsys.readouterr().out
    assert "cancelled" in out


def test_setup_telegram_interactive_ctrl_c(tmp_path, capsys):
    """Handles KeyboardInterrupt gracefully."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=KeyboardInterrupt),
    ):
        _setup_telegram(dry_run=False)

    assert not env_file.exists()
    out = capsys.readouterr().out
    assert "cancelled" in out


def test_setup_telegram_dry_run_skips_prompt(tmp_path, capsys):
    """Dry run without env vars or file prints instructions but skips prompt."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
    ):
        _setup_telegram(dry_run=True)

    out = capsys.readouterr().out
    assert "dry run" in out


def test_setup_telegram_only_token_set(tmp_path, capsys):
    """Falls through to interactive when only token is set."""
    home = tmp_path / "home"
    env_file = home / ".claude" / "telegram-hook.env"

    with (
        patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok"}, clear=True),
        patch("mcloop.install_cmd._TELEGRAM_ENV_FILE", env_file),
        patch("builtins.input", side_effect=EOFError),
    ):
        _setup_telegram(dry_run=False)

    out = capsys.readouterr().out
    assert "using credentials from environment" not in out


def test_cmd_install_calls_setup_telegram(tmp_path):
    """_cmd_install calls _setup_telegram."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")) as mock_tg,
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path)
    mock_tg.assert_called_once_with(dry_run=False)


def test_cmd_install_passes_dry_run_to_setup_telegram(tmp_path):
    """_cmd_install passes dry_run to _setup_telegram."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")) as mock_tg,
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path, dry_run=True)
    mock_tg.assert_called_once_with(dry_run=True)


# --- _setup_env_security ---


def test_setup_env_security_subscription(tmp_path, capsys):
    """Returns subscription billing status by default."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        result = _setup_env_security()
    assert result == ("Environment", "minimal (subscription billing)")
    out = capsys.readouterr().out
    assert "Session environment" in out


def test_setup_env_security_api_billing(tmp_path, capsys):
    """Returns api billing status when configured."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"billing": "api"}))
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        result = _setup_env_security()
    assert result == ("Environment", "minimal (api billing)")
    out = capsys.readouterr().out
    assert "billing" in out.lower()


def test_setup_env_security_openrouter_billing(tmp_path, capsys):
    """Returns openrouter billing status when configured."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"billing": "openrouter"}))
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        result = _setup_env_security()
    assert result == ("Environment", "minimal (openrouter billing)")
    out = capsys.readouterr().out
    assert "billing" in out.lower()


def test_cmd_install_calls_setup_env_security(tmp_path):
    """_cmd_install calls _setup_env_security."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch(
            "mcloop.install_cmd._setup_env_security",
            return_value=("Environment", "ok"),
        ) as mock_env,
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path)
    mock_env.assert_called_once()


# --- _setup_sandbox ---


def test_setup_sandbox_already_enabled(tmp_path, capsys):
    """Skips when sandbox is already enabled."""
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"sandbox": {"enabled": True}}))
    with patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf):
        _setup_sandbox(dry_run=False)
    out = capsys.readouterr().out
    assert "already enabled" in out


def test_setup_sandbox_default_yes(tmp_path, capsys):
    """Empty input defaults to enable (yes)."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", return_value=""),
    ):
        _setup_sandbox(dry_run=False)
    out = capsys.readouterr().out
    assert "enabled" in out.lower()
    saved = json.loads(sf.read_text())
    assert saved["sandbox"]["enabled"] is True
    assert saved["sandbox"]["autoAllowBashIfSandboxed"] is True
    assert saved["sandbox"]["allowUnsandboxedCommands"] is False


def test_setup_sandbox_explicit_yes(tmp_path, capsys):
    """Answering y enables sandbox."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", return_value="y"),
    ):
        _setup_sandbox(dry_run=False)
    saved = json.loads(sf.read_text())
    assert saved["sandbox"]["enabled"] is True


def test_setup_sandbox_no(tmp_path, capsys):
    """Answering no does not enable sandbox."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", return_value="n"),
    ):
        _setup_sandbox(dry_run=False)
    out = capsys.readouterr().out
    assert "not enabled" in out
    saved = json.loads(sf.read_text())
    assert "sandbox" not in saved


def test_setup_sandbox_eof(tmp_path, capsys):
    """EOFError skips sandbox."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", side_effect=EOFError),
    ):
        _setup_sandbox(dry_run=False)
    out = capsys.readouterr().out
    assert "not enabled" in out.lower() or "skipped" in out.lower()


def test_setup_sandbox_ctrl_c(tmp_path, capsys):
    """KeyboardInterrupt skips sandbox."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", side_effect=KeyboardInterrupt),
    ):
        _setup_sandbox(dry_run=False)
    out = capsys.readouterr().out
    assert "not enabled" in out.lower() or "skipped" in out.lower()


def test_setup_sandbox_dry_run(tmp_path, capsys):
    """Dry run shows diff with sandbox defaults but does not write."""
    sf = tmp_path / "settings.json"
    sf.write_text("{}")
    with patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf):
        result = _setup_sandbox(dry_run=True)
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "---" in out
    assert "sandbox" in out
    assert "would enable" in result[1]
    saved = json.loads(sf.read_text())
    assert "sandbox" not in saved


def test_setup_sandbox_no_settings_file(tmp_path, capsys):
    """Creates settings.json if it doesn't exist."""
    sf = tmp_path / "settings.json"
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", return_value=""),
    ):
        _setup_sandbox(dry_run=False)
    assert sf.exists()
    saved = json.loads(sf.read_text())
    assert saved["sandbox"]["enabled"] is True


def test_setup_sandbox_preserves_existing_settings(tmp_path, capsys):
    """Preserves other settings when enabling sandbox."""
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    with (
        patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf),
        patch("builtins.input", return_value="y"),
    ):
        _setup_sandbox(dry_run=False)
    saved = json.loads(sf.read_text())
    assert saved["permissions"]["allow"] == ["Read"]
    assert saved["sandbox"]["enabled"] is True


def test_setup_sandbox_invalid_json(tmp_path, capsys):
    """Exits on invalid JSON."""
    sf = tmp_path / "settings.json"
    sf.write_text("not json")
    with patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf):
        with pytest.raises(SystemExit) as exc:
            _setup_sandbox(dry_run=False)
    assert exc.value.code == 1


def test_setup_sandbox_non_object_json(tmp_path, capsys):
    """Exits on non-object JSON."""
    sf = tmp_path / "settings.json"
    sf.write_text('"just a string"')
    with patch("mcloop.install_cmd._CLAUDE_SETTINGS", sf):
        with pytest.raises(SystemExit) as exc:
            _setup_sandbox(dry_run=False)
    assert exc.value.code == 1


def test_cmd_install_calls_setup_sandbox(tmp_path):
    """_cmd_install calls _setup_sandbox."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")) as mock_sb,
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path)
    mock_sb.assert_called_once_with(dry_run=False)


def test_cmd_install_passes_dry_run_to_setup_sandbox(tmp_path):
    """_cmd_install passes dry_run to _setup_sandbox."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")) as mock_sb,
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
    ):
        _cmd_install(tmp_path, dry_run=True)
    mock_sb.assert_called_once_with(dry_run=True)


# --- _install_recommended_permissions ---


def _setup_perms(tmp_path, example_content=None):
    """Create a fake repo root for recommended permissions tests."""
    repo_root = tmp_path / "repo"
    fake_mcloop_dir = repo_root / "mcloop"
    fake_mcloop_dir.mkdir(parents=True)
    (fake_mcloop_dir / "main.py").write_text("")
    if example_content is not None:
        (repo_root / "settings.example.json").write_text(example_content)
    return repo_root, fake_mcloop_dir


def test_install_recommended_permissions_writes_file(tmp_path, capsys):
    """Writes recommended-permissions.json from settings.example.json."""
    repo_root, fake_mcloop_dir = _setup_perms(
        tmp_path,
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git:*)", "WebSearch"]},
                "sandbox": {"enabled": True},
            }
        ),
    )
    dest = tmp_path / "recommended-permissions.json"
    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", dest):
            _install_recommended_permissions(dry_run=False)
    finally:
        install_mod.__file__ = orig_file
    assert dest.exists()
    content = json.loads(dest.read_text())
    assert content == {"permissions": {"allow": ["Bash(git:*)", "WebSearch"]}}
    out = capsys.readouterr().out
    assert "installed:" in out
    assert "does not modify runtime permissions" in out


def test_install_recommended_permissions_dry_run(tmp_path, capsys):
    """Dry run prints what would be written."""
    repo_root, fake_mcloop_dir = _setup_perms(
        tmp_path,
        json.dumps({"permissions": {"allow": ["Bash(git:*)"]}}),
    )
    dest = tmp_path / "recommended-permissions.json"
    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", dest):
            _install_recommended_permissions(dry_run=True)
    finally:
        install_mod.__file__ = orig_file
    assert not dest.exists()
    out = capsys.readouterr().out
    assert "---" in out
    assert "Bash(git:*)" in out
    assert "does not modify runtime permissions" in out


def test_install_recommended_permissions_missing_example(tmp_path, capsys):
    """Warns when settings.example.json is missing."""
    repo_root, fake_mcloop_dir = _setup_perms(tmp_path)
    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        _install_recommended_permissions(dry_run=False)
    finally:
        install_mod.__file__ = orig_file
    err = capsys.readouterr().err
    assert "settings.example.json not found" in err


def test_install_recommended_permissions_invalid_json(tmp_path, capsys):
    """Warns on invalid JSON in settings.example.json."""
    repo_root, fake_mcloop_dir = _setup_perms(tmp_path, "not json")
    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        _install_recommended_permissions(dry_run=False)
    finally:
        install_mod.__file__ = orig_file
    err = capsys.readouterr().err
    assert "invalid JSON" in err


def test_install_recommended_permissions_no_permissions_key(tmp_path, capsys):
    """Handles missing permissions key gracefully."""
    repo_root, fake_mcloop_dir = _setup_perms(
        tmp_path,
        json.dumps({"sandbox": {"enabled": True}}),
    )
    dest = tmp_path / "recommended-permissions.json"
    import mcloop.install_cmd as install_mod

    orig_file = install_mod.__file__
    install_mod.__file__ = str(fake_mcloop_dir / "install_cmd.py")
    try:
        with patch("mcloop.install_cmd._RECOMMENDED_PERMS_DEST", dest):
            _install_recommended_permissions(dry_run=False)
    finally:
        install_mod.__file__ = orig_file
    assert dest.exists()
    content = json.loads(dest.read_text())
    assert content == {"permissions": {"allow": []}}


def test_cmd_install_calls_install_recommended_permissions(tmp_path):
    """_cmd_install calls _install_recommended_permissions."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ) as mock_rp,
    ):
        _cmd_install(tmp_path)
    mock_rp.assert_called_once_with(dry_run=False)


def test_cmd_install_passes_dry_run_to_recommended_permissions(tmp_path):
    """_cmd_install passes dry_run to _install_recommended_permissions."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ) as mock_rp,
    ):
        _cmd_install(tmp_path, dry_run=True)
    mock_rp.assert_called_once_with(dry_run=True)


# --- _check_rtk ---


def test_check_rtk_found(capsys):
    """Prints note and returns status when rtk is on PATH."""
    with patch("mcloop.install_cmd.shutil.which", return_value="/usr/local/bin/rtk"):
        result = _check_rtk()
    out = capsys.readouterr().out
    assert "RTK detected" in out
    assert "rtk init" in out
    assert result is not None
    assert result[0] == "RTK"


def test_check_rtk_not_found(capsys):
    """Returns None when rtk is not on PATH."""
    with patch("mcloop.install_cmd.shutil.which", return_value=None):
        result = _check_rtk()
    assert capsys.readouterr().out == ""
    assert result is None


def test_cmd_install_calls_check_rtk(tmp_path):
    """_cmd_install calls _check_rtk."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
        patch("mcloop.install_cmd._check_rtk", return_value=None) as mock_rtk,
        patch("mcloop.install_cmd._check_reviewer", return_value=None),
    ):
        _cmd_install(tmp_path)
    mock_rtk.assert_called_once()


# --- _check_reviewer ---


def test_check_reviewer_no_config(tmp_path):
    """Returns None when .mcloop/config.json does not exist."""
    assert _check_reviewer(tmp_path) is None


def test_check_reviewer_no_reviewer_section(tmp_path):
    """Returns None when config has no reviewer section."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "config.json").write_text('{"other": "stuff"}')
    assert _check_reviewer(tmp_path) is None


def test_check_reviewer_with_api_key(tmp_path):
    """Returns status tuple when reviewer is configured and API key is set."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "config.json").write_text(
        '{"reviewer": {"model": "gpt-4", "base_url": "https://openrouter.ai/api/v1"}}'
    )
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}):
        result = _check_reviewer(tmp_path)
    assert result is not None
    assert result[0] == "Reviewer"
    assert "gpt-4" in result[1]
    assert "API key set" in result[1]


def test_check_reviewer_without_api_key(tmp_path):
    """Returns status tuple showing disabled when no API key."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "config.json").write_text(
        '{"reviewer": {"model": "gpt-4", "base_url": "https://openrouter.ai/api/v1"}}'
    )
    with patch.dict("os.environ", {}, clear=True):
        result = _check_reviewer(tmp_path)
    assert result is not None
    assert result[0] == "Reviewer"
    assert "OPENROUTER_API_KEY not set" in result[1]


def test_check_reviewer_invalid_json(tmp_path):
    """Returns None for invalid JSON."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "config.json").write_text("not json")
    assert _check_reviewer(tmp_path) is None


def test_check_reviewer_non_dict_reviewer(tmp_path):
    """Returns None when reviewer section is not a dict."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "config.json").write_text('{"reviewer": "string"}')
    assert _check_reviewer(tmp_path) is None


def test_cmd_install_calls_check_reviewer(tmp_path):
    """_cmd_install calls _check_reviewer with project_dir."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[]),
        patch("mcloop.install_cmd._merge_settings", return_value=[]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "ok")),
        patch("mcloop.install_cmd._setup_env_security", return_value=("Environment", "ok")),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "ok")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "ok"),
        ),
        patch("mcloop.install_cmd._check_rtk", return_value=None),
        patch("mcloop.install_cmd._check_reviewer", return_value=None) as mock_reviewer,
    ):
        _cmd_install(tmp_path)
    mock_reviewer.assert_called_once_with(tmp_path)


# --- _print_install_summary ---


def test_print_install_summary(capsys):
    """Prints a summary table with all components."""
    summary = [
        ("Hook (telegram)", "installed"),
        ("Hook (session-start)", "skipped (already installed)"),
        ("Settings (PreToolUse)", "configured"),
        ("Telegram", "configured (env vars)"),
        ("API key", "configured (strip)"),
        ("Sandbox", "skipped (already enabled)"),
        ("Permissions", "installed — merge manually"),
    ]
    _print_install_summary(summary)
    out = capsys.readouterr().out
    assert "Install summary:" in out
    assert "Hook (telegram)" in out
    assert "installed" in out
    assert "Permissions" in out
    assert "merge manually" in out
    # Should show action needed for manual items
    assert "Action needed:" in out


def test_print_install_summary_no_manual(capsys):
    """No action-needed section when nothing needs manual action."""
    summary = [
        ("Telegram", "configured"),
        ("Sandbox", "configured (enabled)"),
    ]
    _print_install_summary(summary)
    out = capsys.readouterr().out
    assert "Install summary:" in out
    assert "Action needed:" not in out


def test_print_install_summary_dry_run(capsys):
    """Dry run prefix in summary."""
    summary = [("Telegram", "skipped (dry run)")]
    _print_install_summary(summary, dry_run=True)
    out = capsys.readouterr().out
    assert "(dry run) Install summary:" in out


# --- _print_uninstall_summary ---


def test_print_uninstall_summary_removed(capsys):
    """Shows removed items and left-in-place items."""
    summary = [
        ("Settings (PreToolUse)", "removed"),
        ("telegram-hook.env", "removed"),
        ("permissions.allow entries", "left in place"),
    ]
    _print_uninstall_summary(summary)
    out = capsys.readouterr().out
    assert "Uninstall summary:" in out
    assert "Removed:" in out
    assert "Settings (PreToolUse)" in out
    assert "telegram-hook.env" in out
    assert "Left in place:" in out
    assert "permissions.allow entries" in out


def test_print_uninstall_summary_skipped(capsys):
    """Shows already-absent items."""
    summary = [
        ("telegram-hook.env", "skipped (not found)"),
        ("hooks directory", "skipped (not found)"),
    ]
    _print_uninstall_summary(summary)
    out = capsys.readouterr().out
    assert "Already absent:" in out
    assert "Removed:" not in out


def test_print_uninstall_summary_would_remove(capsys):
    """Shows would-remove items in dry run."""
    summary = [
        ("hooks directory", "would remove"),
        ("config.json", "would remove"),
    ]
    _print_uninstall_summary(summary)
    out = capsys.readouterr().out
    assert "Would remove:" in out
    assert "Removed:" not in out


def test_print_uninstall_summary_dry_run_prefix(capsys):
    """Dry run prefix in uninstall summary."""
    summary = [("Telegram", "would remove")]
    _print_uninstall_summary(summary, dry_run=True)
    out = capsys.readouterr().out
    assert "(dry run) Uninstall summary:" in out


def test_cmd_install_prints_summary(tmp_path, capsys):
    """_cmd_install prints install summary at the end."""
    proc = MagicMock(returncode=0, stdout="claude 1.0.0\n")
    with (
        patch("mcloop.install_cmd.shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=proc),
        patch("mcloop.install_cmd._install_hooks", return_value=[("Hooks", "installed")]),
        patch("mcloop.install_cmd._merge_settings", return_value=[("Settings", "ok")]),
        patch("mcloop.install_cmd._setup_telegram", return_value=("Telegram", "configured")),
        patch(
            "mcloop.install_cmd._setup_env_security",
            return_value=("Environment", "configured"),
        ),
        patch("mcloop.install_cmd._setup_sandbox", return_value=("Sandbox", "configured")),
        patch(
            "mcloop.install_cmd._install_recommended_permissions",
            return_value=("Permissions", "installed — merge manually"),
        ),
        patch("mcloop.install_cmd._check_rtk", return_value=None),
        patch("mcloop.install_cmd._check_reviewer", return_value=None),
    ):
        _cmd_install(tmp_path)
    out = capsys.readouterr().out
    assert "Install summary:" in out
    assert "Hooks" in out
    assert "Telegram" in out
    assert "Permissions" in out


def test_load_mcloop_config_missing(tmp_path):
    """Returns empty dict when config file doesn't exist."""
    cfg = tmp_path / "config.json"
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        assert _load_mcloop_config() == {}


def test_load_mcloop_config_invalid_json(tmp_path):
    """Returns empty dict on invalid JSON."""
    cfg = tmp_path / "config.json"
    cfg.write_text("not json")
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        assert _load_mcloop_config() == {}


def test_load_mcloop_config_valid(tmp_path):
    """Returns parsed config."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"keep_anthropic_api_key": True}))
    with patch("mcloop.install_cmd._MCLOOP_CONFIG", cfg):
        result = _load_mcloop_config()
    assert result == {"keep_anthropic_api_key": True}


# --- _run_audit_fix_cycle ---


def _make_result(success=True, exit_code=0, output=""):
    r = MagicMock()
    r.success = success
    r.exit_code = exit_code
    r.output = output
    return r


def test_run_audit_fix_cycle_no_bugs(tmp_path):
    """When audit writes 'No bugs found.', fix session is not run."""
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with (
        patch("mcloop.audit.run_audit", side_effect=fake_audit) as mock_audit,
        patch("mcloop.audit.run_bug_fix") as mock_fix,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.no_bugs
    mock_audit.assert_called_once()
    mock_fix.assert_not_called()
    assert not bugs_path.exists()


def test_run_audit_fix_cycle_with_bugs(tmp_path):
    """When audit finds bugs, fix session runs."""
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"
    bug_content = "# Bugs\n\n## foo.py:1 — crash\n**Severity**: high\nBad.\n"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text(bug_content)
        return _make_result()

    with (
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_verify", return_value=_make_result()),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()) as mock_fix,
        patch("mcloop.audit._has_meaningful_changes", return_value=False),
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_fix.assert_called_once()
    # Fix had no meaningful changes — bugs found but unfixed → failed
    assert result == AuditResult.failed


def test_run_audit_fix_cycle_audit_failure(tmp_path):
    """When audit session fails, result is failed."""
    with (
        patch("mcloop.audit.run_audit", return_value=_make_result(success=False, exit_code=1)),
        patch("mcloop.audit.run_bug_fix") as mock_fix,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.failed
    mock_fix.assert_not_called()


def test_run_audit_fix_cycle_no_bugs_md(tmp_path):
    """When audit succeeds but the audit report is not written, result is failed."""
    with (
        patch("mcloop.audit.run_audit", return_value=_make_result()),
        patch("mcloop.audit.run_bug_fix") as mock_fix,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.failed
    mock_fix.assert_not_called()


def test_run_loop_no_audit_skips_audit(tmp_path):
    """When no_audit=True, _run_audit_fix_cycle is not called."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("# Project\n\n## Phase 1: Only\n- [ ] [AUTO:run_cli] test\n")
    )

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._handle_auto_task", return_value="STATUS: OK\n"),
        patch(
            "mcloop.main.run_checks",
            return_value=MagicMock(passed=True, command="", output=""),
        ),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=True)

    mock_audit.assert_not_called()


def test_run_loop_audit_called_by_default(tmp_path):
    """By default, _run_audit_fix_cycle is called after all phases complete."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("# Project\n\n## Phase 1: Only\n- [ ] [AUTO:run_cli] test\n")
    )

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._handle_auto_task", return_value="STATUS: OK\n"),
        patch(
            "mcloop.main.run_checks",
            return_value=MagicMock(passed=True, command="", output=""),
        ),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=False)

    mock_audit.assert_called_once()


def test_single_audit_round_commits_when_checks_pass(tmp_path):
    """When fix session succeeds and checks pass, changes are committed."""
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text("# Bugs\n\n## foo.py:1 — crash\nBad.\n")
        return _make_result()

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_verify", return_value=_make_result()),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()),
        patch("mcloop.audit._has_meaningful_changes", return_value=True),
        patch("mcloop.audit.run_checks", return_value=check_result),
        patch(
            "mcloop.audit.run_post_fix_review",
            return_value=_make_result(
                output="--- REVIEW RESULT ---\nNO_PROBLEMS\n--- END REVIEW ---\n"
            ),
        ),
        patch("mcloop.audit._commit") as mock_commit,
    ):
        _run_single_audit_round(tmp_path, tmp_path / "logs")

    mock_commit.assert_called_once_with(tmp_path, "Fix bugs from audit")


def test_audit_cycle_runs_two_rounds_when_first_fixes(tmp_path):
    """When the first round fixes bugs, a second round runs."""
    call_count = 0

    def fake_round(project_dir, log_dir, model=None, rate_state=None):
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
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert call_count == 2
    assert result == AuditResult.fixed


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
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    mock_round.assert_called_once()
    assert result == AuditResult.no_bugs


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
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert mock_round.call_count == 2
    assert result == AuditResult.fixed


def test_audit_cycle_saves_hash_after_completion(tmp_path):
    """Audit hash is saved when result is no_bugs."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=False,
        ),
        patch("mcloop.audit._save_audit_hash") as mock_save,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.no_bugs
    mock_save.assert_called_once_with(tmp_path)


def test_single_audit_round_returns_true_on_fix(tmp_path):
    """_run_single_audit_round returns True when bugs are fixed."""
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text("# Bugs\n\n## foo.py:1 — crash\nBad.\n")
        return _make_result()

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch("mcloop.audit.run_bug_verify", return_value=_make_result()),
        patch("mcloop.audit.run_bug_fix", return_value=_make_result()),
        patch("mcloop.audit._has_meaningful_changes", return_value=True),
        patch("mcloop.audit.run_checks", return_value=check_result),
        patch(
            "mcloop.audit.run_post_fix_review",
            return_value=_make_result(
                output="--- REVIEW RESULT ---\nNO_PROBLEMS\n--- END REVIEW ---\n"
            ),
        ),
        patch("mcloop.audit._commit"),
    ):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is True


def test_single_audit_round_keeps_bugs_with_prefix_line_numbers(tmp_path):
    """REMOVED verdict for a header that is a prefix of another bug's title
    must not drop the longer bug. Filter must use exact match, not substring.
    """
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text(
            "# Bugs\n\n"
            "## foo.py:4 — null deref\n"
            "Body for line 4.\n\n"
            "## foo.py:42 — null deref\n"
            "Body for line 42.\n"
        )
        return _make_result()

    verify_result = MagicMock()
    verify_result.success = True
    verify_result.exit_code = 0
    verify_result.output = (
        "--- VERIFY RESULT ---\n"
        "REMOVED: foo.py:4 — null deref (already handled)\n"
        "CONFIRMED: foo.py:42 — null deref\n"
        "--- END VERIFY ---\n"
    )

    captured: dict[str, str] = {}

    def capture_post_verify(*args, **kwargs):
        captured["bugs_md"] = bugs_path.read_text()
        return _make_result(success=False, exit_code=1)

    with (
        patch("mcloop.audit.run_audit", side_effect=fake_audit),
        patch(
            "mcloop.audit.run_bug_verify",
            return_value=verify_result,
        ),
        patch("mcloop.audit.run_bug_fix", side_effect=capture_post_verify),
    ):
        _run_single_audit_round(tmp_path, tmp_path / "logs")

    rewritten = captured.get("bugs_md", "")
    assert "foo.py:42" in rewritten
    assert "Body for line 42." in rewritten
    # The line-4 bug was REMOVED, so its title must NOT appear in the
    # rewritten BUGS.md. (Substring match would have wrongly dropped line 42.)
    assert "## foo.py:4 —" not in rewritten


def test_single_audit_round_returns_false_on_no_bugs(tmp_path):
    """_run_single_audit_round returns False when no bugs found."""
    bugs_path = tmp_path / ".mcloop" / "audit-report.md"

    def fake_audit(project_dir, log_dir, model=None, existing_bugs=""):
        bugs_path.parent.mkdir(parents=True, exist_ok=True)
        bugs_path.write_text("# Bugs\n\nNo bugs found.\n")
        return _make_result()

    with patch("mcloop.audit.run_audit", side_effect=fake_audit):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is False


def test_single_audit_round_returns_none_on_session_failure(tmp_path):
    """_run_single_audit_round returns None when audit session crashes."""
    with patch("mcloop.audit.run_audit", return_value=_make_result(success=False, exit_code=1)):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is None


def test_single_audit_round_returns_none_when_no_bugs_md(tmp_path):
    """_run_single_audit_round returns None when the audit report is not produced."""
    with patch("mcloop.audit.run_audit", return_value=_make_result()):
        result = _run_single_audit_round(tmp_path, tmp_path / "logs")

    assert result is None


def test_audit_cycle_failed_does_not_save_hash(tmp_path):
    """Audit hash is NOT saved when result is failed."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=None,
        ),
        patch("mcloop.audit._save_audit_hash") as mock_save,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.failed
    mock_save.assert_not_called()


def test_audit_cycle_failed_sends_failure_notification(tmp_path):
    """Failed audit sends a distinct failure notification."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=None,
        ),
        patch("mcloop.audit.notify") as mock_notify,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.failed
    mock_notify.assert_called_once()
    assert "failed" in mock_notify.call_args[0][0].lower()


def test_audit_cycle_no_bugs_sends_no_bugs_notification(tmp_path):
    """no_bugs result sends 'no bugs found' notification."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=False,
        ),
        patch("mcloop.audit._save_audit_hash"),
        patch("mcloop.audit.notify") as mock_notify,
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    assert result == AuditResult.no_bugs
    mock_notify.assert_called_once()
    assert "no bugs found" in mock_notify.call_args[0][0].lower()


def test_audit_cycle_no_bugs_banner_omits_round_2(tmp_path, capsys):
    """When round 1 finds no bugs, output should not mention '2/2' or 'round 2'."""
    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            return_value=False,
        ),
        patch("mcloop.audit._save_audit_hash"),
        patch("mcloop.audit.notify"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    captured = capsys.readouterr().out
    assert "2/2" not in captured
    assert "round 2" not in captured.lower()


def test_audit_cycle_round2_banner_mentions_round_2(tmp_path, capsys):
    """When round 1 finds bugs and round 2 runs, output should mention round 2."""
    call_count = 0

    def fake_round(project_dir, log_dir, model=None, rate_state=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # bugs found and fixed
        return False  # no bugs on round 2

    with (
        patch("mcloop.audit._should_skip_audit", return_value=False),
        patch(
            "mcloop.audit._run_single_audit_round",
            side_effect=fake_round,
        ),
        patch("mcloop.audit._save_audit_hash"),
        patch("mcloop.audit.notify"),
    ):
        _run_audit_fix_cycle(tmp_path, tmp_path / "logs")

    captured = capsys.readouterr().out
    assert call_count == 2
    assert "round 2" in captured.lower()


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


def test_find_recent_crash_report_filters_by_process_name(tmp_path):
    """Only .ips files starting with process_name are returned."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "OtherApp-1.ips").write_text("unrelated crash")
    (reports_dir / "MyApp-1.ips").write_text("my crash")
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report(process_name="MyApp")
    assert result == "my crash"


def test_find_recent_crash_report_no_match_for_process_name(tmp_path):
    """Returns empty when no .ips file starts with process_name."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "OtherApp.ips").write_text("unrelated crash")
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report(process_name="MyApp")
    assert result == ""


def test_find_recent_crash_report_none_keeps_old_behavior(tmp_path):
    """Without process_name the newest .ips regardless of name wins."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "Whatever.ips").write_text("some crash")
    with patch("mcloop.investigator.Path.home", return_value=tmp_path):
        result = _find_recent_crash_report()
    assert result == "some crash"


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
    (reports_dir / f"{tmp_path.name}-20240101.ips").write_text("crash data here")
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


def test_gather_bug_context_ignores_unrelated_crash(tmp_path):
    """Crash reports for other processes are filtered out."""
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "SomeOtherApp-20240101.ips").write_text("unrelated")
    with (
        patch("mcloop.investigator.Path.home", return_value=tmp_path),
        patch("mcloop.investigator.detect_app_type", return_value=""),
    ):
        ctx = gather_bug_context(tmp_path)
    assert ctx.crash_report == ""


def test_gather_bug_context_all_sources(tmp_path):
    """All sources combined into a single BugContext."""
    # Setup crash report
    reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    reports_dir.mkdir(parents=True)
    (reports_dir / f"{tmp_path.name}-20240101.ips").write_text("crash info")

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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.investigate_cmd._investigation_passed") as mock_passed,
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.investigate_cmd._investigation_passed") as mock_passed,
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context") as mock_gather,
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings") as mock_copy,
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result) as mock_run,
        patch("mcloop.investigate_cmd._investigation_passed"),
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._investigation_failed") as mock_failed,
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
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch("mcloop.investigate_cmd._launch_app_verification", return_value=None) as mock_verify,
        patch("mcloop.investigate_cmd._investigation_passed") as mock_passed,
        patch("mcloop.investigate_cmd.notify") as mock_notify,
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
    plan.write_text(canonical_plan_text("# Project\n- [ ] Fix bug\n"))
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    (wt_path / "PLAN.md").write_text(canonical_plan_text("# Project\n- [ ] Fix bug\n"))

    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="crash")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "crash"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch(
            "mcloop.investigate_cmd._launch_app_verification",
            side_effect=["App crashed", None],
        ) as mock_verify,
        patch("mcloop.investigate_cmd._append_verification_failure") as mock_append,
        patch("mcloop.investigate_cmd._investigation_passed") as mock_passed,
        patch("mcloop.investigate_cmd.notify"),
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
    plan.write_text(canonical_plan_text("# Project\n- [ ] Fix bug\n"))
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    (wt_path / "PLAN.md").write_text(canonical_plan_text("# Project\n- [ ] Fix bug\n"))

    from mcloop.investigate_cmd import MAX_VERIFICATION_ROUNDS
    from mcloop.investigator import BugContext

    ctx = BugContext(user_description="crash")
    mock_result = MagicMock(returncode=0)

    with (
        patch("sys.argv", ["mcloop", "--file", str(plan), "investigate", "crash"]),
        patch("sys.stdin") as mock_stdin,
        patch("mcloop.investigate_cmd.gather_bug_context", return_value=ctx),
        patch("mcloop.investigate_cmd.worktree.create") as mock_create,
        patch("mcloop.investigate_cmd._copy_project_settings"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=mock_result),
        patch(
            "mcloop.investigate_cmd._launch_app_verification",
            return_value="App crashed",
        ),
        patch("mcloop.investigate_cmd._append_verification_failure"),
        patch("mcloop.investigate_cmd._investigation_failed") as mock_failed,
        patch("mcloop.investigate_cmd.notify"),
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
    with patch("mcloop.investigate_cmd.detect_run", return_value=None):
        result = _launch_app_verification(tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert captured.out == ""


def test_launch_app_verification_gui_ok(tmp_path, capsys):
    """GUI app that starts OK is reported, killed, and returns None."""
    gui_result = MagicMock(crashed=False, hung=False, duration=5.0)
    with (
        patch("mcloop.investigate_cmd.detect_run", return_value="swift run MyApp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = [1234]
        result = _launch_app_verification(tmp_path)
    assert result is None
    mock_pm.run_gui.assert_called_once_with(
        "swift run MyApp", "MyApp", timeout_seconds=15, kill_on_return=False
    )
    mock_pm.kill.assert_called_once_with(1234)
    captured = capsys.readouterr()
    assert "running OK" in captured.out


def test_launch_app_verification_gui_crashed(tmp_path, capsys):
    """GUI app that crashes returns failure description."""
    gui_result = MagicMock(
        crashed=True, hung=False, duration=2.0, crash_report="crash info\nline2"
    )
    with (
        patch("mcloop.investigate_cmd.detect_run", return_value="open Foo.app"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="swift run MyApp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="cargo run"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="cli"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="./myapp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="cli"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="./myapp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="cli"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="npm start"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="web"),
    ):
        result = _launch_app_verification(tmp_path)
    assert result is None
    captured = capsys.readouterr()
    assert "Skipping launch for web app" in captured.out


def test_launch_app_verification_gui_process_name_from_app_bundle(tmp_path, capsys):
    """Process name is extracted from .app bundle path."""
    gui_result = MagicMock(crashed=False, hung=False, duration=3.0)
    with (
        patch("mcloop.investigate_cmd.detect_run", return_value="open MyApp.app"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
        patch("mcloop.process_monitor") as mock_pm,
    ):
        mock_pm.run_gui.return_value = gui_result
        mock_pm.pgrep.return_value = []
        _launch_app_verification(tmp_path)
    mock_pm.run_gui.assert_called_once_with(
        "open MyApp.app", "MyApp", timeout_seconds=15, kill_on_return=False
    )


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
        patch("mcloop.investigate_cmd.detect_run", return_value="swift run MyApp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="swift run MyApp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="./myapp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="cli"),
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
        patch("mcloop.investigate_cmd.detect_run", return_value="swift run MyApp"),
        patch("mcloop.investigate_cmd.detect_app_type", return_value="gui"),
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
        patch("mcloop.investigate_cmd.worktree.current_branch", return_value="main"),
        patch("mcloop.investigate_cmd.subprocess.run") as mock_run,
        patch("builtins.input", return_value="y"),
        patch("mcloop.investigate_cmd.worktree.merge") as mock_merge,
        patch("mcloop.investigate_cmd.worktree.remove") as mock_remove,
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
        patch("mcloop.investigate_cmd.worktree.current_branch", return_value="main"),
        patch("mcloop.investigate_cmd.subprocess.run") as mock_run,
        patch("builtins.input", return_value="n"),
        patch("mcloop.investigate_cmd.worktree.merge") as mock_merge,
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
        patch("mcloop.investigate_cmd.worktree.current_branch", return_value="main"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", side_effect=EOFError),
        patch("mcloop.investigate_cmd.worktree.merge") as mock_merge,
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
        patch("mcloop.investigate_cmd.worktree.current_branch", return_value="main"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", return_value="y"),
        patch(
            "mcloop.investigate_cmd.worktree.merge",
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
        patch("mcloop.investigate_cmd.worktree.current_branch", return_value="main"),
        patch("mcloop.investigate_cmd.subprocess.run", return_value=MagicMock(stdout="")),
        patch("builtins.input", return_value="y"),
        patch("mcloop.investigate_cmd.worktree.merge"),
        patch(
            "mcloop.investigate_cmd.worktree.remove",
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
    assert "## Stage 2: Verification fix (round 1)" in plan
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
        canonical_plan_text(
            "# Investigation Plan\n\n"
            "- [x] Reproduce the crash\n"
            "- [!] Fix the parser\n"
            "- [ ] Add regression test\n"
            "- [ ] Clean up\n"
        )
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

    (wt_path / "PLAN.md").write_text(canonical_plan_text("# Plan\n\n- [ ] First task\n"))

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

    (wt_path / "PLAN.md").write_text("# Plan\n\n## Stage 1: Tasks\n\n- [x] Done task\n")

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
        canonical_plan_text(
            "- [ ] [USER] Launch the app and verify the window appears\n- [ ] Fix the bug\n"
        )
    )
    (tmp_path / ".git").mkdir()

    inputs = iter(["Window is visible", "", "y"])

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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    from mcloop._planfile_compat import parse

    tasks = parse(plan)
    assert tasks[0].checked


def test_run_loop_user_task_failure_files_full_observation_to_bugs(tmp_path):
    """Failed [USER] observations file to BUGS.md untruncated and unflattened."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text(
            "- [ ] [USER] Launch the app and verify the window appears\n- [ ] Fix the bug\n"
        )
    )
    (tmp_path / ".git").mkdir()

    # Multi-line observation longer than 200 chars to prove no truncation.
    obs_lines = [
        "Window opens but title bar shows wrong text.",
        "Reproduction steps:",
        "  1. Launch app",
        "  2. Click 'Open' in the toolbar",
        "  3. Title now reads 'Untitled' instead of the filename",
        "Additional context: this regressed sometime after the toolbar refactor "
        "landed; on the previous build the title reflected the document path.",
        "Expected: window title reflects the opened file path.",
    ]
    multi_line_obs = "\n".join(obs_lines)
    assert len(multi_line_obs) > 200
    assert "\n" in multi_line_obs

    # _handle_user_task reads observation lines until EOF, then prompts y/N.
    inputs = iter([*obs_lines, "", "n"])

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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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

    bugs_path = tmp_path / "BUGS.md"
    assert bugs_path.exists(), "BUGS.md should be created when user task fails"
    bugs_text = bugs_path.read_text()

    # The observation must appear verbatim with newlines preserved.
    assert multi_line_obs in bugs_text, (
        "Full multi-line observation should be preserved in BUGS.md; got:\n" + bugs_text
    )
    # Old flattening behavior must be gone.
    assert " | " not in bugs_text
    # Old truncation marker must be gone.
    assert "..." not in bugs_text
    # The observation should be inside a fenced block.
    assert "```" in bugs_text
    # The [USER] task should remain unchecked (left for re-verification).
    from mcloop._planfile_compat import parse

    tasks = parse(plan)
    assert not tasks[0].checked


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
    plan.write_text(canonical_plan_text("- [ ] First task\n- [ ] Second task\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
        "mcloop.investigate_cmd._dispatch_auto_action",
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
        "mcloop.investigate_cmd._dispatch_auto_action",
        side_effect=RuntimeError("osascript failed"),
    ):
        result = _handle_auto_task("1", "screenshot", "MyApp")

    assert "ERROR" in result
    assert "osascript failed" in result


def test_handle_auto_task_truncates_long_result(capsys):
    """Long results are truncated in display but not in return value."""
    long_result = "x" * 1000
    with patch(
        "mcloop.investigate_cmd._dispatch_auto_action",
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

    call_args = mock.call_args[0]
    assert call_args[0] == "MyApp"
    assert call_args[1].endswith("/auto_screenshot_MyApp.png")
    assert "mcloop_" in call_args[1]
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
    plan.write_text(
        canonical_plan_text("- [ ] [AUTO:run_cli] ./my_app --test\n- [ ] Fix the bug\n")
    )
    (tmp_path / ".git").mkdir()

    with (
        patch(
            "mcloop.investigate_cmd._dispatch_auto_action",
            return_value="STATUS: OK",
        ) as mock_dispatch,
        patch("mcloop.main.run_task") as mock_run_task,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    from mcloop._planfile_compat import parse as parse_checklist

    tasks = parse_checklist(plan)
    assert tasks[0].checked


def test_run_loop_auto_task_nonzero_fails_and_leaves_task_unchecked(tmp_path):
    """Defect B regression: a nonzero AUTO run_cli result fails the run."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("- [ ] [AUTO:run_cli] ./my_app --test\n- [ ] Fix the bug\n")
    )
    (tmp_path / ".git").mkdir()

    with (
        patch(
            "mcloop.investigate_cmd._dispatch_auto_action",
            return_value="exit_code: 1\nSTATUS: CRASHED\noutput:\nboom",
        ) as mock_dispatch,
        patch("mcloop.main.run_task") as mock_run_task,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
    ):
        result = run_loop(plan, no_audit=True)

    assert not result.ok
    mock_dispatch.assert_called_once_with("run_cli", "./my_app --test")
    mock_run_task.assert_not_called()
    mock_checks.assert_not_called()

    from mcloop._planfile_compat import parse as parse_checklist

    tasks = parse_checklist(plan)
    assert not tasks[0].checked
    assert not tasks[1].checked


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


def test_chain_config_resolution():
    config = {
        "chain": [
            {
                "comment": "primary",
                "enabled": True,
                "cli": "claude",
                "model": "opus",
            },
            {
                "comment": "disabled middle",
                "enabled": False,
                "cli": "codex",
                "model": "gpt-5-codex",
            },
            {
                "comment": "direct provider",
                "cli": "claude",
                "model": "kimi-k2.6",
                "executor": {"use_slug_model": False},
            },
        ]
    }

    chain = resolve_chain(config, _chain_args())

    assert chain == [
        ChainEntry(cli="claude", model="opus", comment="primary"),
        ChainEntry(
            cli="claude",
            model="kimi-k2.6",
            executor={"use_slug_model": False},
            comment="direct provider",
        ),
    ]


def test_chain_config_resolution_all_disabled_raises():
    config = {
        "chain": [
            {"enabled": False, "cli": "claude", "model": "opus"},
            {"enabled": False, "cli": "codex", "model": "gpt-5-codex"},
        ]
    }

    with pytest.raises(ValueError, match="chain has no enabled tiers"):
        resolve_chain(config, _chain_args())


def test_chain_config_absent_uses_legacy_model_fallback():
    chain = resolve_chain(
        {"cli": "codex", "model": "gpt-5-codex", "fallback_model": "gpt-5-codex-mini"},
        _chain_args(),
    )

    assert chain == [
        ChainEntry(cli="codex", model="gpt-5-codex"),
        ChainEntry(cli="codex", model="gpt-5-codex-mini"),
    ]


def test_chain_config_model_flag_collapses_chain():
    config = {
        "chain": [
            {"cli": "claude", "model": "opus"},
            {"cli": "codex", "model": "gpt-5-codex"},
        ]
    }

    chain = resolve_chain(
        config,
        _chain_args(cli="codex", model="gpt-5-codex-mini", fallback_model="opus"),
    )

    assert chain == [ChainEntry(cli="codex", model="gpt-5-codex-mini")]


def test_chain_advances_on_rate_limit(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    marked_limited = []

    class SpyRateLimitState:
        def __init__(self):
            self.limited = {}

        def mark_limited(self, cli, cooldown=300):
            marked_limited.append(cli)
            self.limited[cli] = time.time() + cooldown

        def is_limited(self, cli):
            return cli in self.limited

    calls = []

    def fake_run_task(task_text, cli, project_dir, log_dir, description="", **kwargs):
        calls.append((cli, kwargs.get("model"), kwargs.get("executor_override")))
        result = MagicMock()
        if len(calls) == 1:
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

    with (
        patch("mcloop.main.RateLimitState", SpyRateLimitState),
        patch("mcloop.main.run_task", side_effect=fake_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main.ensure_conftest_guard", return_value=False),
        patch("mcloop.main.ensure_pytest_optimizations", return_value=False),
        patch("mcloop.main.validate_project_dependencies"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main.load_reviewer_config", return_value=None),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._commit"),
    ):
        result = run_loop(
            plan,
            chain=[
                ChainEntry(cli="claude", model="opus"),
                ChainEntry(
                    cli="codex",
                    model="gpt-5-codex",
                    executor={"env_overrides": {"ENABLE_TOOL_SEARCH": "false"}},
                ),
            ],
            no_audit=True,
        )

    assert result.ok
    assert marked_limited == ["claude"]
    assert calls[:2] == [
        ("claude", "opus", None),
        ("codex", "gpt-5-codex", {"env_overrides": {"ENABLE_TOOL_SEARCH": "false"}}),
    ]


def test_chain_all_clis_limited_waits(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    class PreLimitedRateState:
        def __init__(self):
            self.limited = {
                "claude": time.time() + 300,
                "codex": time.time() + 300,
            }

        def mark_limited(self, cli, cooldown=300):
            self.limited[cli] = time.time() + cooldown

        def is_limited(self, cli):
            return cli in self.limited

    wait_calls = []

    def fake_wait_for_reset(state, notify_fn=None, enabled_clis=("claude", "codex")):
        wait_calls.append(enabled_clis)
        state.limited.clear()
        return "claude"

    def fake_run_task(*args, **kwargs):
        result = MagicMock()
        result.success = True
        result.output = ""
        result.exit_code = 0
        return result

    mock_check_result = MagicMock()
    mock_check_result.passed = True

    with (
        patch("mcloop.main.RateLimitState", PreLimitedRateState),
        patch("mcloop.main.wait_for_reset", side_effect=fake_wait_for_reset),
        patch("mcloop.main.time.sleep"),
        patch("mcloop.main.run_task", side_effect=fake_run_task),
        patch("mcloop.main.run_checks", return_value=mock_check_result),
        patch("mcloop.main.notify"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main.ensure_conftest_guard", return_value=False),
        patch("mcloop.main.ensure_pytest_optimizations", return_value=False),
        patch("mcloop.main.validate_project_dependencies"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main.load_reviewer_config", return_value=None),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._commit"),
    ):
        result = run_loop(
            plan,
            chain=[
                ChainEntry(cli="claude", model="opus"),
                ChainEntry(cli="codex", model="gpt-5-codex"),
            ],
            no_audit=True,
        )

    assert result.ok
    assert wait_calls == [("claude", "codex")]


def test_run_loop_switches_to_fallback_on_rate_limit(tmp_path):
    """When rate-limited with fallback_model set, switches model."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        result = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert result.ok
    # First 2 attempts used primary, third used fallback
    assert models_used[0] == "opus"
    assert models_used[1] == "opus"
    assert models_used[2] == "sonnet"


def test_fallback_model_prints_message(tmp_path, capsys):
    """Prints 'Primary model failed, retrying with <model>' on fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        result = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert not result.ok
    # 2 primary + 2 fallback = 4 total attempts
    assert len(models_used) == 4
    assert models_used[:2] == ["opus", "opus"]
    assert models_used[2:] == ["sonnet", "sonnet"]
    # Task is marked failed in the checklist (PLAN.md under split-plan)
    assert "[!]" in (tmp_path / "PLAN.md").read_text()


def test_fallback_model_also_exhausted_notifies(tmp_path):
    """When both models exhaust retries, sends a 'giving up' notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
    # R4 = Option B: notify body surfaces [T-NNNNNN] alongside text.
    assert giving_up_calls[0] == call("Giving up on: [T-000001] Do something", level="error")


def test_no_fallback_retry_without_flag(tmp_path):
    """Without fallback_model, exhausted retries just fail."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        result = run_loop(
            plan,
            max_retries=2,
            model="opus",
            no_audit=True,
        )

    assert not result.ok
    # Only 2 attempts, no fallback
    assert len(models_used) == 2
    assert models_used == ["opus", "opus"]


def test_fallback_same_as_primary_skips_fallback(tmp_path):
    """When fallback_model equals primary model, no extra retry round."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        result = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="opus",
            no_audit=True,
        )

    assert not result.ok
    # Same model as fallback: only 2 attempts, not 4
    assert len(models_used) == 2
    assert models_used == ["opus", "opus"]


def test_fallback_gets_fresh_retries(tmp_path):
    """Fallback model gets its own full set of retries (not shared)."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        result = run_loop(
            plan,
            max_retries=3,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert result.ok
    # 3 primary + 3 fallback = 6 total, fallback succeeded on 3rd try
    assert len(models_used) == 6
    assert models_used[:3] == ["opus", "opus", "opus"]
    assert models_used[3:] == ["sonnet", "sonnet", "sonnet"]


def test_fallback_resets_per_task(tmp_path):
    """Each task starts with the primary model, even after a prior fallback."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task one\n- [ ] Task two\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._commit"),
        patch(
            "mcloop.main.get_available_cli",
            return_value="claude",
        ),
    ):
        result = run_loop(
            plan,
            max_retries=2,
            model="opus",
            fallback_model="sonnet",
            no_audit=True,
        )

    assert result.ok
    # Task 1: opus, opus (fail), sonnet (succeed)
    # Task 2: should start with opus again
    assert models_used[0] == "opus"
    assert models_used[1] == "opus"
    assert models_used[2] == "sonnet"
    # Task 2 starts fresh with primary model
    assert models_used[3] == "opus"


# --- _maybe_auto_wrap tests ---


def test_auto_wrap_no_run_command(tmp_path):
    """When detect_run returns None, no wrapping happens."""
    with patch("mcloop.main.detect_run", return_value=None):
        _maybe_auto_wrap(tmp_path)
    # No .mcloop/wrap/ created
    assert not (tmp_path / ".mcloop" / "wrap").exists()


def test_auto_wrap_already_wrapped(tmp_path):
    """When .mcloop/wrap/ has files, auto-wrap is skipped."""
    from mcloop.wrap import PYTHON_WRAPPER

    wrap_dir = tmp_path / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True)
    (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)

    with patch("mcloop.main.detect_run") as mock_run:
        _maybe_auto_wrap(tmp_path)

    mock_run.assert_not_called()


def test_auto_wrap_injects_and_commits(tmp_path, capsys):
    """First runnable task triggers auto-wrap, commit, and push."""
    entry = tmp_path / "main.py"
    entry.write_text("print('hello')\n")

    git_result = MagicMock()
    git_result.returncode = 0

    with (
        patch("mcloop.main.detect_run", return_value="python main.py"),
        patch("mcloop.main._stage_safe") as mock_stage,
        patch("mcloop.main._git", return_value=git_result) as mock_git,
    ):
        _maybe_auto_wrap(tmp_path)

    captured = capsys.readouterr()
    assert "Injected crash handlers." in captured.out

    # _stage_safe called once for safe staging
    mock_stage.assert_called_once()
    # Should have committed: commit, remote check, push
    assert mock_git.call_count == 3
    commit_call = mock_git.call_args_list[0]
    assert "Inject mcloop crash handlers" in commit_call[0][0]

    # Entry point should have markers
    content = entry.read_text()
    assert "# mcloop:wrap:begin" in content

    # Canonical wrappers saved
    assert (tmp_path / ".mcloop" / "wrap" / "python_wrapper.py").exists()


def test_auto_wrap_language_detection_fails(tmp_path):
    """When wrap_project raises ValueError, auto-wrap silently skips."""
    with (
        patch("mcloop.main.detect_run", return_value="cargo run"),
        patch("mcloop.wrap.wrap_project", side_effect=ValueError("no lang")),
    ):
        _maybe_auto_wrap(tmp_path)
    # No crash, no .mcloop/wrap/
    assert not (tmp_path / ".mcloop" / "wrap").exists()


def test_auto_wrap_push_failure(tmp_path, capsys):
    """When push fails after auto-wrap, prints error but continues."""
    entry = tmp_path / "main.py"
    entry.write_text("print('hello')\n")

    def fake_git(cmd, cwd=None, label="", silent=False):
        result = MagicMock()
        if "push" in cmd:
            result.returncode = 1
        else:
            result.returncode = 0
        return result

    with (
        patch("mcloop.main.detect_run", return_value="python main.py"),
        patch("mcloop.main._git", side_effect=fake_git),
    ):
        _maybe_auto_wrap(tmp_path)

    captured = capsys.readouterr()
    assert "Push after auto-wrap failed" in captured.out


def test_run_loop_calls_auto_wrap_after_commit(tmp_path):
    """run_loop calls _maybe_auto_wrap after each successful commit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._maybe_auto_wrap") as mock_auto_wrap,
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    mock_auto_wrap.assert_called_once_with(tmp_path)


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
    with (
        patch("mcloop.main._stage_safe") as mock_stage,
        patch("mcloop.main._git", return_value=git_result) as mock_git,
    ):
        _reinject_wrappers(tmp_path)

    # _stage_safe called once for safe staging
    mock_stage.assert_called_once()
    # Should have committed the re-injection: commit, remote check, push
    assert mock_git.call_count == 3
    commit_call = mock_git.call_args_list[0]
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
    with (
        patch("mcloop.main._stage_safe"),
        patch("mcloop.main._git", return_value=git_result) as mock_git,
    ):
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

    with (
        patch("mcloop.main._stage_safe"),
        patch("mcloop.main._git", side_effect=fake_git),
    ):
        _reinject_wrappers(tmp_path)

    captured = capsys.readouterr()
    assert "Push after re-injection failed" in captured.out


def test_run_loop_calls_reinject_after_commit(tmp_path):
    """run_loop calls _reinject_wrappers after each successful commit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="abc123 commit\n")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 2
    # Fix tasks now land in BUGS.md (standalone bug backlog), not PLAN.md
    bugs_text = (tmp_path / "BUGS.md").read_text()
    assert "## Bugs" in bugs_text
    assert "Guard against None" in bugs_text

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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    bugs_text = (tmp_path / "BUGS.md").read_text()
    assert "## Bugs" in bugs_text
    assert "Fix crash: RuntimeError" in bugs_text


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
        patch("mcloop.errors.run_diagnostic") as mock_diag,
    ):
        result = _check_errors_json(tmp_path)

    assert result is False
    # Should not run diagnostics when there's no PLAN.md
    mock_diag.assert_not_called()
    out = capsys.readouterr().out
    assert "No PLAN.md found" in out


def test_check_errors_appends_when_no_tasks(tmp_path):
    """Creates BUGS.md with ## Bugs section when BUGS.md does not exist."""
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result),
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        _check_errors_json(tmp_path)

    bugs_text = (tmp_path / "BUGS.md").read_text()
    assert "## Bugs" in bugs_text
    assert "Fix crash: TypeError: none + int at main.py:10" in bugs_text


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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result),
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result),
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
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
        patch("mcloop.errors.run_diagnostic", return_value=diag_result) as mock_diag,
        patch("subprocess.run") as mock_git,
    ):
        mock_git.return_value = MagicMock(returncode=0, stdout="")
        result = _check_errors_json(tmp_path)

    assert result is True
    assert mock_diag.call_count == 1


# --- _insert_bugs_section ---


def test_insert_bugs_section_before_stage(tmp_path):
    """Creates BUGS.md with ## Bugs header when file does not exist."""
    bugs = tmp_path / "BUGS.md"

    _insert_bugs_section(bugs, ["- [ ] Fix X"])

    text = bugs.read_text()
    assert "## Bugs" in text
    assert "- [ ] Fix X" in text
    # Header must precede task line
    lines = text.splitlines()
    bugs_idx = next(i for i, ln in enumerate(lines) if "## Bugs" in ln)
    task_idx = next(i for i, ln in enumerate(lines) if "Fix X" in ln)
    assert bugs_idx < task_idx


def test_insert_bugs_section_before_checkbox(tmp_path):
    """Creates BUGS.md with ## Bugs header and single task line."""
    bugs = tmp_path / "BUGS.md"

    _insert_bugs_section(bugs, ["- [ ] Fix Y"])

    text = bugs.read_text()
    assert "## Bugs" in text
    assert "Fix Y" in text


def test_insert_bugs_section_appends_to_existing(tmp_path):
    """Appends new tasks to an existing BUGS.md, preserving old content."""
    bugs = tmp_path / "BUGS.md"
    bugs.write_text("## Bugs\n\n- [x] Old bug\n")

    _insert_bugs_section(bugs, ["- [ ] New bug"])

    text = bugs.read_text()
    assert "Old bug" in text
    assert "New bug" in text
    # New bug is appended after old bug
    lines = text.splitlines()
    old_idx = next(i for i, ln in enumerate(lines) if "Old bug" in ln)
    new_idx = next(i for i, ln in enumerate(lines) if "New bug" in ln)
    assert old_idx < new_idx


def test_insert_bugs_section_appends_to_end(tmp_path):
    """Creates a fresh BUGS.md from nothing with the given task lines."""
    bugs = tmp_path / "BUGS.md"

    _insert_bugs_section(bugs, ["- [ ] Fix Z"])

    text = bugs.read_text()
    assert "## Bugs" in text
    assert "Fix Z" in text


# --- Bug-only mode ---


def test_run_loop_bug_only_skips_audit_and_stages(tmp_path):
    """Bug-only mode: fixes bugs, skips audit and stage transitions."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("## Stage 1: Core\n- [ ] Add feature\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("## Stage 1: Core\n- [ ] Add feature\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
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
        result = run_loop(plan)

    mock_audit.assert_not_called()
    # The feature task should NOT have been worked on
    from mcloop._planfile_compat import parse as cl_parse

    tasks = cl_parse(plan)
    feature = [t for t in tasks if t.stage != "Bugs"][0]
    assert not feature.checked
    assert result.ok


def test_run_loop_bug_only_returns_stuck_bugs(tmp_path):
    """Bug-only mode: returns failure when fix fails."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
    (tmp_path / ".git").mkdir()

    mock_result = MagicMock()
    mock_result.success = False
    mock_result.output = "error"
    mock_result.exit_code = 1

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=mock_result),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification") as mock_verify,
    ):
        result = run_loop(plan)

    # Task fails all retries → failure status
    assert not result.ok
    mock_verify.assert_not_called()


def test_run_loop_bug_only_verifies_app(tmp_path, capsys):
    """Bug-only mode: launches app verification after all bugs fixed."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
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


def test_run_loop_bug_only_failed_verification_returns_failure(tmp_path):
    """Bug-only mode: failed app verification produces RunStatus("failure")."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
        patch("mcloop.main._check_errors_json"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification", return_value="App crashed on launch"),
    ):
        status = run_loop(plan)

    assert status.status == "failure"
    assert "Bug verification failed" in (status.detail or "")
    assert "App crashed on launch" in (status.detail or "")


def test_run_loop_bug_only_keeps_errors_json_on_stuck(tmp_path):
    """Bug-only mode: keeps errors.json when bugs could not be fixed."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
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
    plan.write_text(canonical_plan_text("# Plan\n\n## Phase 1: Only\n- [ ] [AUTO:run_cli] test\n"))

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._handle_auto_task", return_value="STATUS: OK\n"),
        patch(
            "mcloop.main.run_checks",
            return_value=MagicMock(passed=True, command="", output=""),
        ),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        run_loop(plan, no_audit=False)

    mock_audit.assert_called_once()


# ── _all_tasks ──


def test_all_tasks_flat():
    """Flattens a flat list of tasks."""
    from mcloop._planfile_compat import Task

    tasks = [
        Task("A", False, False, 0, 0),
        Task("B", False, False, 1, 0),
    ]
    result = _all_tasks(tasks)
    assert [t.text for t in result] == ["A", "B"]


def test_all_tasks_nested():
    """Flattens nested tasks depth-first."""
    from mcloop._planfile_compat import Task

    child1 = Task("C1", False, False, 2, 2)
    child2 = Task("C2", False, False, 3, 2)
    parent = Task("P", False, False, 1, 0, children=[child1, child2])
    root = Task("R", False, False, 0, 0)
    result = _all_tasks([root, parent])
    assert [t.text for t in result] == ["R", "P", "C1", "C2"]


def test_all_tasks_empty():
    """Empty input returns empty list."""
    assert _all_tasks([]) == []


# ── _save_interrupt_state ──


def test_save_interrupt_state_writes_json(tmp_path):
    """Writes interrupted.json with all expected fields."""
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
        lifecycle_mod._current_task_label = "1.2"
        lifecycle_mod._current_task_text = "Fix the bug"
        lifecycle_mod._current_phase = "task"
        lifecycle_mod._phase_start_time = time.monotonic() - 10
        runner_mod._last_output_lines.clear()
        runner_mod._last_output_lines.append("some output")

        _save_interrupt_state()

        state_file = mcloop_dir / "interrupted.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["task_label"] == "1.2"
        assert data["task_text"] == "Fix the bug"
        assert data["phase"] == "task"
        assert data["elapsed_seconds"] >= 9
        assert "timestamp" in data
        assert data["last_output"] == ["some output"]
    finally:
        (
            lifecycle_mod._project_dir,
            lifecycle_mod._current_task_label,
            lifecycle_mod._current_task_text,
            lifecycle_mod._current_phase,
            lifecycle_mod._phase_start_time,
        ) = orig


def test_save_interrupt_state_noop_when_no_project_dir():
    """Does nothing when _project_dir is None."""
    orig = lifecycle_mod._project_dir
    try:
        lifecycle_mod._project_dir = None
        _save_interrupt_state()
    finally:
        lifecycle_mod._project_dir = orig


def test_save_interrupt_state_handles_write_oserror(tmp_path):
    """Handles OSError on write gracefully."""
    import mcloop.runner as runner_mod

    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    orig = lifecycle_mod._project_dir
    try:
        lifecycle_mod._project_dir = tmp_path
        lifecycle_mod._current_phase = "task"
        lifecycle_mod._phase_start_time = time.monotonic()
        runner_mod._last_output_lines.clear()
        # Make the target a directory so write_text fails
        (mcloop_dir / "interrupted.json").mkdir()
        # Should not raise
        _save_interrupt_state()
    finally:
        lifecycle_mod._project_dir = orig


# ── _check_interrupted ──


def test_check_interrupted_no_file(tmp_path):
    """Returns None when no interrupted.json exists."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task\n"))
    result = _check_interrupted(tmp_path, plan)
    assert result is None


def test_check_interrupted_user_prompt_auto_retry(tmp_path):
    """Returns 'retry' automatically for user_prompt phase."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "task_label": "1",
        "task_text": "test",
        "phase": "user_prompt",
        "elapsed_seconds": 5,
        "timestamp": "2026-01-01T00:00:00",
        "last_output": [],
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] test\n"))

    result = _check_interrupted(tmp_path, plan)
    assert result == "retry"
    assert not (mcloop_dir / "interrupted.json").exists()


def test_check_interrupted_corrupt_json(tmp_path):
    """Corrupt JSON deletes the file and returns None."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "interrupted.json").write_text("{bad json")
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] test\n"))

    result = _check_interrupted(tmp_path, plan)
    assert result is None
    assert not (mcloop_dir / "interrupted.json").exists()


def test_check_interrupted_retry_on_r(tmp_path, monkeypatch):
    """Returns 'retry' when user types 'r'."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "task_label": "1",
        "task_text": "test",
        "phase": "task",
        "elapsed_seconds": 5,
        "timestamp": "2026-01-01T00:00:00",
        "last_output": [],
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] test\n"))

    monkeypatch.setattr("builtins.input", lambda _="": "r")
    result = _check_interrupted(tmp_path, plan)
    assert result == "retry"


def test_check_interrupted_skip_marks_failed(tmp_path, monkeypatch):
    """'skip' marks the task as failed in PLAN.md."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "task_label": "1",
        "task_text": "Fix something",
        "phase": "task",
        "elapsed_seconds": 5,
        "timestamp": "2026-01-01T00:00:00",
        "last_output": [],
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Fix something\n- [ ] Other\n"))

    monkeypatch.setattr("builtins.input", lambda _="": "s")
    result = _check_interrupted(tmp_path, plan)
    assert result == "skip"
    assert_canonical_checkbox(plan.read_text(), "!", "Fix something")


def test_check_interrupted_audit_prompt(tmp_path, monkeypatch):
    """Audit phase shows audit-specific prompt."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "task_label": "1",
        "task_text": "audit",
        "phase": "audit",
        "elapsed_seconds": 5,
        "timestamp": "2026-01-01T00:00:00",
        "last_output": [],
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] audit\n"))

    captured = []
    orig_print = print

    def capture_print(*args, **kwargs):
        captured.append(" ".join(str(a) for a in args))
        orig_print(*args, **kwargs)

    monkeypatch.setattr("builtins.input", lambda _="": "r")
    with patch("builtins.print", side_effect=capture_print):
        result = _check_interrupted(tmp_path, plan)
    assert result == "retry"
    assert any("esume audit" in line for line in captured)


def test_check_interrupted_d_writes_ruledout(tmp_path, monkeypatch):
    """'d' option accepts description, writes [RULEDOUT] and eliminated.json."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    state = {
        "task_label": "1.1",
        "task_text": "Fix crash",
        "phase": "task",
        "elapsed_seconds": 5,
        "timestamp": "2026-01-01T00:00:00",
        "last_output": [],
    }
    (mcloop_dir / "interrupted.json").write_text(json.dumps(state))
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Fix crash\n"))

    inputs = iter(["d", "tried restarting", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    result = _check_interrupted(tmp_path, plan)
    assert result == "retry"
    assert "[RULEDOUT] tried restarting" in plan.read_text()
    elim = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert "1.1" in elim


# ── _write_ruledout_to_plan ──


def test_write_ruledout_inserts_after_task(tmp_path):
    """Inserts [RULEDOUT] line after the matching task."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Fix crash\n- [ ] Other task\n")

    _write_ruledout_to_plan(plan, "Fix crash", "tried restart")

    content = plan.read_text()
    lines = content.splitlines()
    assert lines[0] == "- [ ] Fix crash"
    assert lines[1] == "  [RULEDOUT] tried restart"
    assert lines[2] == "- [ ] Other task"


def test_write_ruledout_indented_task(tmp_path):
    """Inserts [RULEDOUT] at correct indentation for nested tasks."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Parent\n  - [ ] Child task\n")

    _write_ruledout_to_plan(plan, "Child task", "bad approach")

    content = plan.read_text()
    lines = content.splitlines()
    assert lines[1] == "  - [ ] Child task"
    assert lines[2] == "    [RULEDOUT] bad approach"


def test_write_ruledout_task_not_found(tmp_path):
    """Does nothing if task text is not found."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] Some task\n")

    _write_ruledout_to_plan(plan, "Nonexistent task", "whatever")

    assert plan.read_text() == "- [ ] Some task\n"


# ── _write_eliminated_json ──


def test_write_eliminated_json_creates_file(tmp_path):
    """Creates eliminated.json if it does not exist."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()

    _write_eliminated_json(tmp_path, "1.1", "tried restart")

    elim = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert "1.1" in elim
    assert len(elim["1.1"]) == 1
    assert elim["1.1"][0]["approach"] == "tried restart"


def test_write_eliminated_json_appends_to_existing(tmp_path):
    """Appends to existing entries for the same task label."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "eliminated.json").write_text(
        json.dumps({"1.1": [{"approach": "first", "timestamp": "2026-01-01"}]})
    )

    _write_eliminated_json(tmp_path, "1.1", "second approach")

    elim = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert len(elim["1.1"]) == 2
    assert elim["1.1"][1]["approach"] == "second approach"


def test_write_eliminated_json_corrupt_starts_fresh(tmp_path):
    """Handles corrupt existing JSON by starting fresh."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (mcloop_dir / "eliminated.json").write_text("{bad json")

    _write_eliminated_json(tmp_path, "2.1", "new approach")

    elim = json.loads((mcloop_dir / "eliminated.json").read_text())
    assert "2.1" in elim
    assert elim["2.1"][0]["approach"] == "new approach"


# ── [BATCH] support ──


def _make_batch_args(tmp_path, children=None):
    """Helper to create common _run_batch arguments."""
    from mcloop._planfile_compat import Task
    from mcloop.ratelimit import RateLimitState

    project_dir = tmp_path / "proj"
    project_dir.mkdir(exist_ok=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    checklist = tmp_path / "PLAN.md"

    if children is None:
        children = [
            Task(
                text="Add feature A",
                checked=False,
                failed=False,
                line_number=1,
                indent_level=2,
            ),
            Task(
                text="Add feature B",
                checked=False,
                failed=False,
                line_number=2,
                indent_level=2,
            ),
        ]

    parent = Task(
        text="[BATCH] Build components",
        checked=False,
        failed=False,
        line_number=0,
        indent_level=0,
        children=children,
    )
    tasks = [parent]

    md_lines = ["- [ ] [BATCH] Build components\n"]
    for child in children:
        md_lines.append(f"  - [ ] {child.text}\n")
    checklist.write_text("".join(md_lines))

    ctx = SessionContext()
    rate_state = RateLimitState()

    return {
        "batch_children": children,
        "tasks": tasks,
        "checklist_path": checklist,
        "project_dir": project_dir,
        "log_dir": log_dir,
        "description": "Test project",
        "first_label": "1.1",
        "ctx": ctx,
        "rate_state": rate_state,
        "cli": "claude",
        "current_model": None,
        "fallback_model": None,
        "max_retries": 3,
        "project_checks": [],
        "allowed_tools": None,
        "run_start": time.monotonic(),
        "completed": [],
        "notes_snapshot": None,
    }


def test_run_batch_combines_text(tmp_path):
    """_run_batch builds 'Do all of the following' numbered prompt."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit"),
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        _run_batch(**args)

        # Verify the combined text format
        call_args = mock_run.call_args
        prompt = call_args[0][0]
        assert prompt.startswith("Do all of the following in order:")
        assert "1. Add feature A" in prompt
        assert "2. Add feature B" in prompt


def test_batch_noop_false_positive_does_not_check_off_without_acceptance_evidence(
    tmp_path,
):
    """Defect A regression: batch no-op + global green does not check off."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=False),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.check_off") as mock_check_off,
    ):
        mock_run.return_value = MagicMock(success=True, output="already satisfied")
        mock_checks.return_value = MagicMock(passed=True)

        status, reason = _run_batch(**args)

    assert status == "failed"
    assert "no acceptance evidence" in reason
    mock_checks.assert_not_called()
    mock_check_off.assert_not_called()


def test_run_batch_success_checks_off_children(tmp_path):
    """On success, _run_batch checks off all children and returns 'success'."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit"),
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.check_off") as mock_check_off,
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "success"
        assert mock_check_off.call_count == 2
        assert len(args["completed"]) == 2


def test_run_batch_task_failure_returns_failed(tmp_path):
    """When run_task fails, _run_batch returns 'failed' without checking off."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main.check_off") as mock_check_off,
    ):
        mock_run.return_value = MagicMock(success=False, output="error")

        result = _run_batch(**args)

        assert result[0] == "failed"
        mock_check_off.assert_not_called()
        assert len(args["completed"]) == 0


def test_run_batch_checks_fail_rolls_back(tmp_path):
    """When checks fail, _run_batch rolls back with selective checkout and rm."""
    args = _make_batch_args(tmp_path)

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="changed.py\n")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect) as mock_git,
        patch("mcloop.main.check_off") as mock_check_off,
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        mock_check_off.assert_not_called()
        # Verify rollback: selective git checkout (not blanket)
        git_calls = mock_git.call_args_list
        checkout_calls = [
            c for c in git_calls if len(c[0][0]) >= 4 and c[0][0][:3] == ["git", "checkout", "--"]
        ]
        assert len(checkout_calls) == 1
        assert checkout_calls[0][0][0][3] == "changed.py"
        # No git clean calls
        clean_calls = [c for c in git_calls if "clean" in str(c[0][0])]
        assert len(clean_calls) == 0


def test_run_batch_noop_auto_checks(tmp_path):
    """No changes plus global green alone must not auto-check children."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=False),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.check_off") as mock_check_off,
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "failed"
        assert "no acceptance evidence" in result[1]
        mock_checks.assert_not_called()
        mock_check_off.assert_not_called()
        assert len(args["completed"]) == 0


def test_run_loop_batch_detection(tmp_path):
    """When find_next returns a child whose parent has [BATCH], batch is triggered."""
    md = (
        "# Test project\n\n"
        "- [ ] [BATCH] Build components\n"
        "  - [ ] Add feature A\n"
        "  - [ ] Add feature B\n"
    )
    md = canonical_plan_text(md)
    plan = tmp_path / "PLAN.md"
    plan.write_text(md)
    current = tmp_path / "PLAN.md"
    current.write_text(md)

    # Set up git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
    )

    with (
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._run_batch") as mock_batch,
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_checks") as mock_full_checks,
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main.detect_build", return_value=None),
        patch("mcloop.main.detect_run", return_value=None),
        patch("mcloop.main.get_check_commands", return_value=[]),
    ):
        # First call: batch succeeds; second parse finds nothing left
        mock_batch.return_value = ("success", "")
        mock_full_checks.return_value = MagicMock(passed=True)

        # run_loop will call _run_batch because parent has [BATCH]
        # After "success", it continues the loop and find_next returns
        # the same children again (since we didn't really check them off).
        # Make batch return "success" first time, then simulate completion.

        def batch_side_effect(*a, **kw):
            # Check off tasks in the active file (PLAN.md) to
            # stop the loop under split-plan semantics.
            content = current.read_text()
            content = content.replace(
                "- [ ] T-000001: Add feature A",
                "- [x] T-000001: Add feature A",
            )
            content = content.replace(
                "- [ ] T-000002: Add feature B",
                "- [x] T-000002: Add feature B",
            )
            content = content.replace(
                "- [ ] T-000003: [BATCH] Build components",
                "- [x] T-000003: [BATCH] Build components",
            )
            current.write_text(content)
            return ("success", "")

        mock_batch.side_effect = batch_side_effect

        run_loop(plan, max_retries=3)

        # Verify _run_batch was called (batch detection worked)
        assert mock_batch.call_count >= 1


def test_run_loop_batch_disabled_by_config(tmp_path):
    """When config has "batch": false, _run_batch is never called."""
    md = (
        "# Test project\n\n"
        "- [ ] [BATCH] Build components\n"
        "  - [ ] Add feature A\n"
        "  - [ ] Add feature B\n"
    )
    md = canonical_plan_text(md)
    plan = tmp_path / "PLAN.md"
    plan.write_text(md)
    current = tmp_path / "PLAN.md"
    current.write_text(md)

    # Set up git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
    )

    call_count = 0

    def run_and_check(*a, **kw):
        nonlocal call_count
        call_count += 1
        # Task runs target the active file (PLAN.md under split-plan)
        content = current.read_text()
        if call_count == 1:
            content = content.replace("- [ ] Add feature A", "- [x] Add feature A", 1)
        elif call_count == 2:
            content = content.replace("- [ ] Add feature B", "- [x] Add feature B", 1)
            # Also check off parent
            content = content.replace(
                "- [ ] [BATCH] Build components",
                "- [x] [BATCH] Build components",
            )
        current.write_text(content)
        return MagicMock(success=True, output="done")

    with (
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._run_batch") as mock_batch,
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._load_mcloop_config", return_value={"batch": False}),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main.detect_build", return_value=None),
        patch("mcloop.main.detect_run", return_value=None),
        patch("mcloop.main.get_check_commands", return_value=[]),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._commit"),
    ):
        mock_checks.return_value = MagicMock(passed=True)
        mock_run.side_effect = run_and_check

        run_loop(plan, max_retries=3)

        # _run_batch should never be called when batch is disabled
        mock_batch.assert_not_called()
        # Individual tasks were executed instead
        assert call_count == 2


# ── _snapshot_worktree ──


def test_snapshot_worktree_captures_modified_and_untracked(tmp_path):
    """_snapshot_worktree returns modified tracked files and untracked files."""
    diff_out = MagicMock(returncode=0, stdout="src/main.py\nlib/utils.py\n")
    ls_out = MagicMock(returncode=0, stdout="new_file.txt\ntmp.log\n")
    with patch("mcloop.git_ops._git", side_effect=[diff_out, ls_out]):
        modified, untracked = _snapshot_worktree(tmp_path)
    assert modified == ["src/main.py", "lib/utils.py"]
    assert untracked == ["new_file.txt", "tmp.log"]


def test_snapshot_worktree_empty_on_clean_tree(tmp_path):
    """Clean working tree returns empty lists."""
    empty = MagicMock(returncode=0, stdout="")
    with patch("mcloop.git_ops._git", return_value=empty):
        modified, untracked = _snapshot_worktree(tmp_path)
    assert modified == []
    assert untracked == []


def test_snapshot_worktree_handles_git_failure(tmp_path):
    """Git command failures return empty lists (graceful degradation)."""
    fail = MagicMock(returncode=128, stdout="")
    with patch("mcloop.git_ops._git", return_value=fail):
        modified, untracked = _snapshot_worktree(tmp_path)
    assert modified == []
    assert untracked == []


# ── _worktree_status tests ──


def test_worktree_status_returns_raw_porcelain(tmp_path):
    """_worktree_status returns unfiltered git status --porcelain output."""
    raw = " M PLAN.md\n M logs/run.log\n M src/main.py\n"
    mock_result = MagicMock(returncode=0, stdout=raw)
    with patch("mcloop.git_ops._git", return_value=mock_result):
        status = _worktree_status(tmp_path)
    assert "PLAN.md" in status
    assert "logs/run.log" in status
    assert "src/main.py" in status


def test_worktree_status_includes_filtered_files(tmp_path):
    """_worktree_status includes files that _changed_files would filter out."""
    raw = " M PLAN.md\n M .mcloop/state.json\n M logs/debug.log\n"
    mock_result = MagicMock(returncode=0, stdout=raw)
    with patch("mcloop.git_ops._git", return_value=mock_result):
        status = _worktree_status(tmp_path)
    # All files appear — no filtering applied
    assert "PLAN.md" in status
    assert ".mcloop/state.json" in status
    assert "logs/debug.log" in status


def test_worktree_status_empty_on_clean_tree(tmp_path):
    """Clean working tree returns empty string."""
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("mcloop.git_ops._git", return_value=mock_result):
        status = _worktree_status(tmp_path)
    assert status == ""


def test_worktree_status_handles_git_failure(tmp_path):
    """Git failure returns empty string."""
    mock_result = MagicMock(returncode=128, stdout="")
    with patch("mcloop.git_ops._git", return_value=mock_result):
        status = _worktree_status(tmp_path)
    assert status == ""


def test_worktree_status_reordered_lines_not_treated_as_change(tmp_path):
    """Same files in different order should NOT be detected as a change.

    git status --porcelain output order can vary; the comparison uses sets
    of lines so reordering alone does not trigger a false positive.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    # Pre-check and post-check return same files but in different order
    worktree_calls = iter(
        [
            " M src/main.py\n M src/utils.py",  # pre-check
            " M src/utils.py\n M src/main.py",  # post-check (reordered)
        ]
    )

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["src/main.py"]),
        patch("mcloop.main._worktree_status", side_effect=worktree_calls),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    # Commit SHOULD be called since the status is the same (just reordered)
    mock_commit.assert_called_once()


def test_empty_pre_check_status_not_false_positive(tmp_path):
    """Empty pre_check_status should not cause false positive change detection.

    When there are no uncommitted changes before checks, _worktree_status
    returns "". Splitting "" yields [''], so set("".splitlines()) is {''},
    while an empty post-check set() != {''} would always be True — a false
    positive. The fix handles empty status specially.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    # Both pre-check and post-check return empty (no changes)
    worktree_calls = iter(
        [
            "",  # pre-check: no uncommitted changes
            "",  # post-check: still no uncommitted changes
        ]
    )

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["src/main.py"]),
        patch("mcloop.main._worktree_status", side_effect=worktree_calls),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    # Commit SHOULD be called — empty pre and post means no change
    mock_commit.assert_called_once()


def test_checker_introduced_changes_to_filtered_file_detected(tmp_path):
    """When a checker modifies a filtered file (e.g. PLAN.md), the change is detected.

    This is the core bug fix: previously _changed_files was used for detection,
    which would miss changes to PLAN.md, logs/, and .mcloop/ files.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    # Simulate checker modifying PLAN.md: _worktree_status returns different
    # values before and after run_checks.  The retry loop runs up to 3
    # attempts, each calling _worktree_status twice (pre + post).
    worktree_calls = iter(
        [
            " M src/main.py",  # attempt 1 pre-check
            " M src/main.py\n M PLAN.md",  # attempt 1 post-check (changed!)
            " M src/main.py",  # attempt 2 pre-check
            " M src/main.py\n M PLAN.md",  # attempt 2 post-check (changed!)
            " M src/main.py",  # attempt 3 pre-check
            " M src/main.py\n M PLAN.md",  # attempt 3 post-check (changed!)
        ]
    )

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["src/main.py"]),
        patch("mcloop.main._worktree_status", side_effect=worktree_calls),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    # Commit should NOT have been called because checker introduced changes
    mock_commit.assert_not_called()


# ── Autofix metadata-only changes detection ──


def test_run_batch_autofix_metadata_only_fails(tmp_path):
    """_run_batch fails when autofix modifies only metadata files (e.g. PLAN.md).

    _changed_files filters out metadata files, so if autofix modifies PLAN.md
    the change would be invisible. _has_uncommitted_changes catches this.
    """
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=True),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._git", return_value=MagicMock(returncode=0, stdout="")),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "failed"
        mock_commit.assert_not_called()
        # Checks should not have been run since we bail early
        mock_checks.assert_not_called()


def test_run_batch_autofix_no_uncommitted_succeeds(tmp_path):
    """No changed files/no uncommitted changes still needs acceptance evidence."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=False),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main.run_checks") as mock_checks,
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "failed"
        assert "no acceptance evidence" in result[1]
        mock_checks.assert_not_called()


def test_individual_task_autofix_metadata_only_retries(tmp_path):
    """Individual task loop retries when autofix modifies only metadata files.

    Same bug as _run_batch: _changed_files filters metadata, so autofix
    changes to PLAN.md would be invisible without _has_uncommitted_changes.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=True),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        mock_checks.return_value = MagicMock(passed=True)

        run_loop(plan, no_audit=True)

    # Commit should NOT have been called because autofix modified metadata-only
    mock_commit.assert_not_called()


# ── Batch rollback with pre-batch dirty state ──


def test_run_batch_rollback_preserves_pre_batch_untracked(tmp_path):
    """Pre-batch untracked files are not removed on rollback."""
    args = _make_batch_args(tmp_path)
    project_dir = args["project_dir"]
    # Create a new untracked file that the batch produced
    new_file = project_dir / "batch_new.py"
    new_file.write_text("batch created this")
    # Create a pre-batch untracked file that should be preserved
    pre_file = project_dir / "scratch.txt"
    pre_file.write_text("pre-existing")

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="scratch.txt\nnotes.md\nbatch_new.py\n")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch(
            "mcloop.main._snapshot_worktree",
            return_value=([], ["scratch.txt", "notes.md"]),
        ),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect),
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        # batch_new.py should have been removed (new untracked file)
        assert not new_file.exists()
        # Pre-batch untracked file should be preserved
        assert pre_file.exists()


def test_run_batch_rollback_removes_new_untracked_dir(tmp_path):
    """New untracked directories created by the batch are removed on rollback."""
    args = _make_batch_args(tmp_path)
    project_dir = args["project_dir"]
    # Create a new directory that the batch produced
    new_dir = project_dir / "batch_output"
    new_dir.mkdir()
    (new_dir / "result.txt").write_text("batch output")

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="batch_output\n")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect),
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        assert not new_dir.exists()


def test_run_batch_rollback_selective_checkout_with_pre_modified(tmp_path):
    """Pre-batch modified files are not reverted during rollback."""
    args = _make_batch_args(tmp_path)

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="keep_dirty.py\nbatch_file.py\n")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch(
            "mcloop.main._snapshot_worktree",
            return_value=(["keep_dirty.py"], []),
        ),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect) as mock_git,
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        git_calls = mock_git.call_args_list
        # Should NOT have a blanket "git checkout ." call
        checkout_dot_calls = [c for c in git_calls if c[0][0] == ["git", "checkout", "."]]
        assert len(checkout_dot_calls) == 0
        # Should have selective checkout for batch_file.py only
        checkout_file_calls = [
            c
            for c in git_calls
            if len(c[0][0]) >= 3
            and c[0][0][0] == "git"
            and c[0][0][1] == "checkout"
            and c[0][0][2] == "--"
        ]
        # batch_file.py should be reverted, keep_dirty.py should not
        reverted_files = [c[0][0][3] for c in checkout_file_calls]
        assert "batch_file.py" in reverted_files
        assert "keep_dirty.py" not in reverted_files


def test_run_batch_rollback_no_pre_modified_selective_checkout(tmp_path):
    """Without pre-batch modified files, rollback still uses selective checkout."""
    args = _make_batch_args(tmp_path)

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="new_file.py\n")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect) as mock_git,
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        git_calls = mock_git.call_args_list
        # Should NOT use blanket checkout
        checkout_dot_calls = [c for c in git_calls if c[0][0] == ["git", "checkout", "."]]
        assert len(checkout_dot_calls) == 0
        # Should NOT use git clean
        clean_calls = [c for c in git_calls if "clean" in str(c[0][0])]
        assert len(clean_calls) == 0
        # Should selectively checkout batch-modified files
        checkout_file_calls = [
            c for c in git_calls if len(c[0][0]) >= 4 and c[0][0][:3] == ["git", "checkout", "--"]
        ]
        assert len(checkout_file_calls) == 1
        assert checkout_file_calls[0][0][0][3] == "new_file.py"


def test_run_batch_rollback_mixed_modified_and_untracked(tmp_path):
    """Rollback handles both pre-batch modified AND untracked files simultaneously."""
    args = _make_batch_args(tmp_path)
    project_dir = args["project_dir"]
    # Batch created a new untracked file
    batch_new = project_dir / "batch_new.py"
    batch_new.write_text("batch created")
    # Pre-batch untracked file that must survive
    pre_untracked = project_dir / "user_notes.txt"
    pre_untracked.write_text("user notes")

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            # Both pre-dirty and batch-touched files show as modified
            return MagicMock(returncode=0, stdout="pre_dirty.py\nbatch_touched.py\n")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="user_notes.txt\nbatch_new.py\n")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch(
            "mcloop.main._snapshot_worktree",
            return_value=(["pre_dirty.py"], ["user_notes.txt"]),
        ),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect) as mock_git,
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        git_calls = mock_git.call_args_list
        # Only batch_touched.py should be checked out, not pre_dirty.py
        checkout_calls = [
            c for c in git_calls if len(c[0][0]) >= 4 and c[0][0][:3] == ["git", "checkout", "--"]
        ]
        reverted = [c[0][0][3] for c in checkout_calls]
        assert "batch_touched.py" in reverted
        assert "pre_dirty.py" not in reverted
        # batch_new.py removed, user_notes.txt preserved
        assert not batch_new.exists()
        assert pre_untracked.exists()


def test_run_batch_rollback_git_diff_empty_stdout(tmp_path):
    """Rollback handles empty git diff output (no modified files)."""
    args = _make_batch_args(tmp_path)
    project_dir = args["project_dir"]
    # Batch created only an untracked file, no modifications
    batch_file = project_dir / "new_output.py"
    batch_file.write_text("new content")

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(returncode=0, stdout="new_output.py\n")
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect) as mock_git,
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        # No checkout calls since no modified files
        git_calls = mock_git.call_args_list
        checkout_calls = [
            c for c in git_calls if len(c[0][0]) >= 4 and c[0][0][:3] == ["git", "checkout", "--"]
        ]
        assert len(checkout_calls) == 0
        # But untracked file should still be removed
        assert not batch_file.exists()


def test_run_batch_rollback_multiple_new_untracked_with_pre_existing(tmp_path):
    """Multiple new untracked files are removed while multiple pre-existing survive."""
    args = _make_batch_args(tmp_path)
    project_dir = args["project_dir"]
    # Pre-existing untracked files
    pre1 = project_dir / "scratch.txt"
    pre1.write_text("scratch")
    pre2 = project_dir / "local_config.ini"
    pre2.write_text("config")
    # Batch-created untracked files
    batch1 = project_dir / "generated_a.py"
    batch1.write_text("gen a")
    batch2 = project_dir / "generated_b.py"
    batch2.write_text("gen b")

    def git_side_effect(cmd, cwd, **kwargs):
        if cmd[1:3] == ["diff", "--name-only"]:
            return MagicMock(returncode=0, stdout="")
        if cmd[1:3] == ["ls-files", "--others"]:
            return MagicMock(
                returncode=0,
                stdout="scratch.txt\nlocal_config.ini\ngenerated_a.py\ngenerated_b.py\n",
            )
        return MagicMock(returncode=0, stdout="")

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch(
            "mcloop.main._snapshot_worktree",
            return_value=([], ["scratch.txt", "local_config.ini"]),
        ),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git", side_effect=git_side_effect),
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=False, command="pytest")

        result = _run_batch(**args)

        assert result[0] == "failed"
        # Pre-existing files survive
        assert pre1.exists()
        assert pre2.exists()
        # Batch-created files removed
        assert not batch1.exists()
        assert not batch2.exists()


def test_run_batch_rollback_task_failure_no_rollback(tmp_path):
    """When the task itself fails (not checks), no rollback is needed."""
    args = _make_batch_args(tmp_path)

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._snapshot_worktree", return_value=(["dirty.py"], ["scratch.txt"])),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes") as mock_changes,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._git") as mock_git,
        patch("mcloop.main.check_off"),
    ):
        mock_run.return_value = MagicMock(success=False, output="error")

        result = _run_batch(**args)

        assert result[0] == "failed"
        # No rollback git calls (diff, checkout, ls-files) should happen
        # because the task failed before checks
        mock_changes.assert_not_called()
        mock_checks.assert_not_called()
        # _git is called for checkpoint/snapshot, but not for rollback
        rollback_calls = [
            c
            for c in mock_git.call_args_list
            if any(
                kw.get("label", "").startswith("batch rollback")
                for kw in [c[1]]
                if isinstance(c[1], dict)
            )
        ]
        assert len(rollback_calls) == 0


def test_run_batch_worktree_status_reordered_lines_not_treated_as_change(tmp_path):
    """_run_batch: same worktree status in different order should NOT be detected as a change.

    git status --porcelain output order can vary; the comparison uses sets
    of lines so reordering alone does not trigger a false positive.
    """
    args = _make_batch_args(tmp_path)

    worktree_calls = iter(
        [
            " M src/main.py\n M src/utils.py",  # pre-check
            " M src/utils.py\n M src/main.py",  # post-check (reordered)
        ]
    )

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["src/main.py"]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", side_effect=worktree_calls),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.check_off"),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main.run_autofix"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "success"
        mock_commit.assert_called_once()


def test_run_batch_empty_pre_check_status_not_false_positive(tmp_path):
    """_run_batch: empty pre_check_status should not cause false positive.

    When _worktree_status returns "" before and after checks, the comparison
    should detect no change. Previously set("".splitlines()) yielded {''}
    which != set() (empty post), always triggering a false positive.
    """
    args = _make_batch_args(tmp_path)

    worktree_calls = iter(
        [
            "",  # pre-check: no uncommitted changes
            "",  # post-check: still no uncommitted changes
        ]
    )

    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["src/main.py"]),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", side_effect=worktree_calls),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.check_off"),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main.run_autofix"),
    ):
        mock_run.return_value = MagicMock(success=True, output="done")
        mock_checks.return_value = MagicMock(passed=True)

        result = _run_batch(**args)

        assert result[0] == "success"
        mock_commit.assert_called_once()


# --- Reviewer integration ---


def test_get_commit_hash(tmp_path):
    """_get_commit_hash returns the HEAD commit hash."""
    result = MagicMock()
    result.stdout = "abc123def\n"
    with patch("mcloop.main.subprocess.run", return_value=result) as mock_run:
        h = _get_commit_hash(tmp_path)
    assert h == "abc123def"
    mock_run.assert_called_once()
    args = mock_run.call_args
    assert args[0][0] == ["git", "rev-parse", "HEAD"]
    assert args[1]["cwd"] == tmp_path


def test_get_commit_hash_empty(tmp_path):
    """_get_commit_hash returns empty string on failure."""
    result = MagicMock()
    result.stdout = ""
    with patch("mcloop.main.subprocess.run", return_value=result):
        h = _get_commit_hash(tmp_path)
    assert h == ""


def test_spawn_reviewer(tmp_path):
    """_spawn_reviewer spawns a subprocess and appends to _reviewer_procs."""
    proc = MagicMock()
    with (
        patch("mcloop.review_integration._get_commit_hash", return_value="abc123"),
        patch("mcloop.review_integration.subprocess.Popen", return_value=proc) as mock_popen,
    ):
        saved = list(_reviewer_procs)
        _reviewer_procs.clear()
        try:
            _spawn_reviewer(tmp_path)
            assert proc in _reviewer_procs
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args
            cmd = call_args[0][0]
            assert "-m" in cmd
            assert "mcloop.reviewer" in cmd
            assert "abc123" in cmd
            assert str(tmp_path) in cmd
            assert call_args[1]["start_new_session"] is True
        finally:
            _reviewer_procs.clear()
            _reviewer_procs.extend(saved)


def test_spawn_reviewer_no_hash(tmp_path):
    """_spawn_reviewer does nothing if commit hash is empty."""
    with (
        patch("mcloop.review_integration._get_commit_hash", return_value=""),
        patch("mcloop.review_integration.subprocess.Popen") as mock_popen,
    ):
        _spawn_reviewer(tmp_path)
    mock_popen.assert_not_called()


def test_terminate_reviewers():
    """_terminate_reviewers terminates all procs and clears the list."""
    p1 = MagicMock()
    p2 = MagicMock()
    saved = list(_reviewer_procs)
    _reviewer_procs.clear()
    _reviewer_procs.extend([p1, p2])
    try:
        _terminate_reviewers()
        p1.terminate.assert_called_once()
        p2.terminate.assert_called_once()
        assert len(_reviewer_procs) == 0
    finally:
        _reviewer_procs.clear()
        _reviewer_procs.extend(saved)


def test_terminate_reviewers_oserror():
    """_terminate_reviewers handles OSError gracefully."""
    p = MagicMock()
    p.terminate.side_effect = OSError("no such process")
    saved = list(_reviewer_procs)
    _reviewer_procs.clear()
    _reviewer_procs.append(p)
    try:
        _terminate_reviewers()
        assert len(_reviewer_procs) == 0
    finally:
        _reviewer_procs.clear()
        _reviewer_procs.extend(saved)


def test_cleanup_stale_reviews(tmp_path):
    """_cleanup_stale_reviews removes old files, keeps recent ones."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    old_file = reviews_dir / "old.json"
    old_file.write_text("[]")
    import os

    # Set mtime to 48 hours ago
    old_time = time.time() - 172800
    os.utime(old_file, (old_time, old_time))
    new_file = reviews_dir / "new.json"
    new_file.write_text("[]")
    _cleanup_stale_reviews(tmp_path)
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_stale_reviews_no_dir(tmp_path):
    """_cleanup_stale_reviews does nothing if directory doesn't exist."""
    _cleanup_stale_reviews(tmp_path)  # Should not raise


def test_cleanup_stale_reviews_ignores_non_json(tmp_path):
    """_cleanup_stale_reviews ignores non-.json files."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    txt_file = reviews_dir / "notes.txt"
    txt_file.write_text("keep me")
    import os

    old_time = time.time() - 172800
    os.utime(txt_file, (old_time, old_time))
    _cleanup_stale_reviews(tmp_path)
    assert txt_file.exists()


def test_collect_review_findings_no_dir(tmp_path):
    """_collect_review_findings does nothing if reviews dir doesn't exist."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Test\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert ctx.text() == ""


def test_collect_review_findings_adds_to_context(tmp_path):
    """High-confidence findings (< 3 errors) are added to session context."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Test\n")
    findings = [
        {
            "file": "a.py",
            "line_range": [1, 5],
            "severity": "warning",
            "description": "possible bug",
            "confidence": "high",
        }
    ]
    (reviews_dir / "abc123.json").write_text(json.dumps(findings))
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert "possible bug" in ctx.text()
    assert "Review findings" in ctx.text()
    assert not (reviews_dir / "abc123.json").exists()


def test_collect_review_findings_inserts_bugs(tmp_path):
    """3+ high-confidence error-severity findings insert a bug task."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Test\n\n- [ ] Do something\n"))
    findings = [
        {
            "file": f"f{i}.py",
            "line_range": [1, 2],
            "severity": "error",
            "description": f"critical bug {i}",
            "confidence": "high",
        }
        for i in range(3)
    ]
    (reviews_dir / "def456.json").write_text(json.dumps(findings))
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    # Reviewer findings now land in the standalone BUGS.md, not PLAN.md
    bugs_text = (tmp_path / "BUGS.md").read_text()
    assert "## Bugs" in bugs_text
    assert "Fix review finding from commit def456" in bugs_text
    # Each finding gets its own task
    assert bugs_text.count("- [ ] Fix review finding from commit def456") == 3
    # Should NOT add to context — went to bugs instead
    assert "Review findings" not in ctx.text()
    assert not (reviews_dir / "def456.json").exists()


def test_collect_review_findings_skips_low_confidence(tmp_path):
    """Low-confidence findings are ignored."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Test\n")
    findings = [
        {
            "file": "a.py",
            "line_range": [1, 2],
            "severity": "error",
            "description": "maybe a bug",
            "confidence": "low",
        }
    ]
    (reviews_dir / "abc.json").write_text(json.dumps(findings))
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert ctx.text() == ""


def test_collect_review_findings_invalid_json(tmp_path):
    """Invalid JSON review files are deleted without error."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Test\n")
    (reviews_dir / "bad.json").write_text("not json{{{")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert not (reviews_dir / "bad.json").exists()


def test_run_loop_prints_reviewer_status(tmp_path):
    """run_loop prints reviewer status when configured."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")
    config = {"model": "gpt-4", "base_url": "https://api.example.com", "api_key": "k"}

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.load_reviewer_config", return_value=config),
        patch(
            "mcloop.main.format_reviewer_status",
            return_value="gpt-4 via api.example.com (API key set)",
        ),
        patch("mcloop.main._cleanup_stale_reviews"),
        patch("builtins.print") as mock_print,
    ):
        run_loop(plan)

    printed = " ".join(str(c) for c in mock_print.call_args_list)
    assert "Reviewer:" in printed
    assert "gpt-4 via api.example.com" in printed


def test_run_loop_spawns_reviewer_after_commit(tmp_path):
    """run_loop spawns a reviewer after a successful commit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Project\n\n- [ ] Fix bug\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] Fix bug\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    config = {"model": "m", "api_key": "k"}

    def fake_transition(master_path, current_plan_path):
        current_plan_path.unlink(missing_ok=True)
        return None

    spawn_mock = MagicMock()
    main_patches = patch.multiple(
        "mcloop.main",
        _checkpoint=MagicMock(),
        _push_or_die=MagicMock(),
        _kill_orphan_sessions=MagicMock(),
        _ensure_git=MagicMock(),
        _check_user_input=MagicMock(return_value=None),
        run_task=MagicMock(return_value=result),
        _has_meaningful_changes=MagicMock(return_value=True),
        _changed_files=MagicMock(return_value=["a.py"]),
        _worktree_status=MagicMock(return_value=""),
        handle_sync=MagicMock(),
        run_checks=MagicMock(return_value=check_result),
        _run_build=MagicMock(return_value=BuildResult(ran=False, passed=True)),
        _commit=MagicMock(),
        _maybe_auto_wrap=MagicMock(),
        _reinject_wrappers=MagicMock(),
        _run_audit_fix_cycle=MagicMock(),
        _print_summary=MagicMock(),
        notify=MagicMock(),
        _collect_review_findings=MagicMock(),
        load_reviewer_config=MagicMock(return_value=config),
        format_reviewer_status=MagicMock(return_value=""),
        _cleanup_stale_reviews=MagicMock(),
        _spawn_reviewer=spawn_mock,
    )
    with main_patches:
        run_loop(plan)

    spawn_mock.assert_called_once()


def test_run_loop_no_reviewer_when_not_configured(tmp_path):
    """run_loop does not spawn reviewer when config is None."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Project\n\n- [ ] Fix bug\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] Fix bug\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_transition(master_path, current_plan_path):
        current_plan_path.unlink(missing_ok=True)
        return None

    spawn_mock = MagicMock()
    main_patches = patch.multiple(
        "mcloop.main",
        _checkpoint=MagicMock(),
        _push_or_die=MagicMock(),
        _kill_orphan_sessions=MagicMock(),
        _ensure_git=MagicMock(),
        _check_user_input=MagicMock(return_value=None),
        run_task=MagicMock(return_value=result),
        _has_meaningful_changes=MagicMock(return_value=True),
        _changed_files=MagicMock(return_value=["a.py"]),
        _worktree_status=MagicMock(return_value=""),
        handle_sync=MagicMock(),
        run_checks=MagicMock(return_value=check_result),
        _run_build=MagicMock(return_value=BuildResult(ran=False, passed=True)),
        _commit=MagicMock(),
        _maybe_auto_wrap=MagicMock(),
        _reinject_wrappers=MagicMock(),
        _run_audit_fix_cycle=MagicMock(),
        _print_summary=MagicMock(),
        notify=MagicMock(),
        _collect_review_findings=MagicMock(),
        load_reviewer_config=MagicMock(return_value=None),
        format_reviewer_status=MagicMock(return_value=""),
        _cleanup_stale_reviews=MagicMock(),
        _spawn_reviewer=spawn_mock,
    )
    with main_patches:
        run_loop(plan)

    spawn_mock.assert_not_called()


# --- CLAUDE.md freshness gate tests ---


def test_run_loop_commits_then_defers_claude_md_sync(tmp_path):
    """run_loop commits code first, then calls handle_sync for CLAUDE.md."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._changed_files", return_value=["mcloop/foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit", return_value="abc123") as mock_commit,
        patch("mcloop.main.handle_sync") as mock_sync,
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main.mark_failed"),
    ):
        run_loop(plan, no_audit=True)

    mock_commit.assert_called_once()
    mock_sync.assert_called_once()


def test_run_loop_claude_md_freshness_passes(tmp_path):
    """run_loop commits when CLAUDE.md freshness check passes."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    def fake_find_next(tasks):
        # Return first unchecked leaf task (split-plan calls find_next
        # twice per iteration: once for bugs, once for plan tasks).
        for t in tasks:
            if not t.checked:
                return t
        return None

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["mcloop/foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit") as mock_commit,
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.find_next", side_effect=fake_find_next),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        run_loop(plan, no_audit=True)

    mock_commit.assert_called_once()


def test_run_loop_claude_md_sync_failure_does_not_block_commit(tmp_path):
    """CLAUDE.md sync failure no longer blocks or retries the task commit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
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
        patch("mcloop.main._changed_files", return_value=["mcloop/foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit", return_value="def456") as mock_commit,
        patch("mcloop.main.handle_sync") as mock_sync,
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main.mark_failed"),
    ):
        run_loop(plan, no_audit=True)

    # Commit happens regardless of sync outcome
    mock_commit.assert_called_once()
    mock_sync.assert_called_once()


def test_full_suite_failure_at_stage_boundary_skips_build_and_notify(tmp_path, capsys):
    """Full suite failure at stage boundary skips _run_build and stage-complete notification."""
    plan = tmp_path / "PLAN.md"
    # Stage 1 has one task (will be completed), Stage 2 has another (won't run)
    plan.write_text(
        canonical_plan_text(
            "# Plan\n\n"
            "## Stage 1: Setup\n\n"
            "- [ ] Init project\n\n"
            "## Stage 2: Build\n\n"
            "- [ ] Build app\n"
        )
    )
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = False
    full_suite_check.command = "pytest"
    full_suite_check.output = "FAILED test_foo.py"

    # Per-task checks pass, full suite fails
    check_call_count = 0

    def checks_side_effect(project_dir, changed_files=None):
        nonlocal check_call_count
        check_call_count += 1
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._run_build") as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan)

    mock_build.assert_not_called()
    mock_audit.assert_not_called()
    # Stage-complete notification should not have been sent
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    assert not any("complete" in m for m in notify_messages)
    # Explicit failure notification should have been sent
    assert any("red repo" in m and "phase boundary" in m for m in notify_messages)
    captured = capsys.readouterr()
    assert "Full suite failed at phase boundary" in captured.out


def test_full_suite_failure_at_end_of_run_skips_build_audit_notify(tmp_path, capsys):
    """Full suite failure at end of run skips _run_build, audit, and all-done notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = False
    full_suite_check.command = "pytest"
    full_suite_check.output = "FAILED test_bar.py"

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["bar.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._run_build") as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan)

    mock_build.assert_not_called()
    mock_audit.assert_not_called()
    # All-done notification should not have been sent
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    assert not any("completed" in m for m in notify_messages)
    # Explicit failure notification should have been sent
    # (Under split-plan, end-of-run full suite always runs at the final phase boundary)
    assert any("red repo" in m and "phase boundary" in m for m in notify_messages)
    captured = capsys.readouterr()
    assert "Full suite failed at phase boundary" in captured.out


def test_full_suite_pass_at_stage_boundary_proceeds_normally(tmp_path, capsys):
    """Default-mode run advances across two phases without exiting.

    Updated for the phase-transition contract: when the full suite +
    build pass at a stage boundary AND ``stop_after_stage`` is False,
    the loop advances to the next phase rather than breaking. Build
    runs at each boundary (so twice for two stages), audit runs once
    at the end after all phases complete, and the summary's
    ``completed_stage`` is the last phase processed.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text(
            "# Plan\n\n"
            "## Stage 1: Setup\n\n"
            "- [ ] Init project\n\n"
            "## Stage 2: Build\n\n"
            "- [ ] Build app\n"
        )
    )
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary") as mock_summary,
        patch("mcloop.main.notify") as mock_notify,
        patch(
            "mcloop.main._run_build",
            return_value=BuildResult(ran=True, passed=True, command="make"),
        ) as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan)

    # Build runs at each stage boundary. With two stages, two builds.
    assert mock_build.call_count == 2
    # Audit runs once at the end (no_audit not set; loop advanced
    # through both phases and reached next_phase is None).
    mock_audit.assert_called_once()
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    # Advancing across the boundary calls notify("Starting Stage 2:
    # Build").
    assert any("Starting" in m and "Stage 2: Build" in m for m in notify_messages)
    # Final "All tasks completed!" notification fires after audit.
    assert any("All tasks completed" in m for m in notify_messages)
    # No failure notification.
    assert not any("red repo" in m for m in notify_messages)
    # Summary's completed_stage is the LAST phase processed.
    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs.get("completed_stage") == "Stage 2: Build"
    captured = capsys.readouterr()
    assert "Full test suite passed" in captured.out
    # The advancing path prints the "Advancing to <next_phase>"
    # marker between stage 1 and stage 2.
    assert "Advancing to Stage 2: Build" in captured.out


def test_default_mode_advances_across_phases_without_exiting(tmp_path, capsys):
    """Per the phase-transition fix: in default mode (no
    --stop-after-stage), the loop advances at each phase boundary
    rather than breaking. The outer ``while True`` re-parses
    PLAN.md (refreshed by phase advancement) and picks up
    the next phase's tasks. The run only exits when phase advancement
    returns None (all phases done)."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text(
            "# Plan\n\n"
            "## Stage 1: First\n\n"
            "- [ ] Task one\n\n"
            "## Stage 2: Second\n\n"
            "- [ ] Task two\n\n"
            "## Stage 3: Third\n\n"
            "- [ ] Task three\n"
        )
    )
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
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch(
            "mcloop.main._run_build",
            return_value=BuildResult(ran=True, passed=True, command="make"),
        ) as mock_build,
        patch("mcloop.main._print_summary") as mock_summary,
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        result = run_loop(plan)

    # Three stages -> three full-suite + build invocations.
    assert mock_build.call_count == 3
    # Audit runs once after all phases complete.
    mock_audit.assert_called_once()
    # The advancing prints land between phases.
    captured = capsys.readouterr()
    assert "Advancing to Stage 2: Second" in captured.out
    assert "Advancing to Stage 3: Third" in captured.out
    # Final summary's completed_stage is the LAST phase.
    _, kwargs = mock_summary.call_args
    assert kwargs.get("completed_stage") == "Stage 3: Third"


def test_full_suite_pass_at_end_of_run_proceeds_normally(tmp_path, capsys):
    """Full suite pass at end of run runs build, audit, and sends all-done notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["bar.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary") as mock_summary,
        patch("mcloop.main.notify") as mock_notify,
        patch(
            "mcloop.main._run_build",
            return_value=BuildResult(ran=True, passed=True, command="make"),
        ) as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan)

    # Build should run after full suite passes
    mock_build.assert_called_once()
    # Audit should run at end of run
    mock_audit.assert_called_once()
    # All-done notification should be sent
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    assert any("All tasks completed" in m for m in notify_messages)
    # No failure notification
    assert not any("red repo" in m for m in notify_messages)
    # Summary should be called
    mock_summary.assert_called_once()
    captured = capsys.readouterr()
    assert "Full test suite passed" in captured.out


def test_build_failure_at_stage_boundary_returns_failure(tmp_path, capsys):
    """Build failure at stage boundary sends error notification and returns failure."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text(
            "# Plan\n\n"
            "## Stage 1: Setup\n\n"
            "- [ ] Init project\n\n"
            "## Stage 2: Build\n\n"
            "- [ ] Build app\n"
        )
    )
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    failed_build = BuildResult(ran=True, passed=False, command="make build")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._run_build", return_value=failed_build),
    ):
        status = run_loop(plan)

    assert status.status == "failure"
    assert "Build failed" in status.detail
    assert "phase boundary" in status.detail
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    assert any("Build failed" in m and "phase boundary" in m for m in notify_messages)
    # Stage-complete notification should NOT be sent
    assert not any("complete." in m for m in notify_messages)


def test_build_failure_at_end_of_run_returns_failure(tmp_path, capsys):
    """Build failure at end of run sends error notification and returns failure."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    failed_build = BuildResult(ran=True, passed=False, command="swift build")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["bar.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._run_build", return_value=failed_build),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        status = run_loop(plan)

    assert status.status == "failure"
    assert "Build failed" in status.detail
    # Under split-plan, build runs at phase boundary (final phase for end-of-run cases)
    assert "phase boundary" in status.detail
    notify_messages = [str(c) for c in mock_notify.call_args_list]
    assert any("Build failed" in m and "phase boundary" in m for m in notify_messages)
    # All-done notification should NOT be sent
    assert not any("All tasks completed" in m for m in notify_messages)


# --- _run_build returns BuildResult ---


def test_run_build_no_build_command(tmp_path):
    """When no build command is detected, _run_build returns passed=True, ran=False."""
    with patch("mcloop.main.detect_build", return_value=None):
        result = _run_build(tmp_path)
    assert result.ran is False
    assert result.passed is True


def test_run_build_success(tmp_path):
    """Successful build returns passed=True."""
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = "ok"
    completed.stderr = ""
    with (
        patch("mcloop.main.detect_build", return_value="make build"),
        patch("mcloop.main.subprocess.run", return_value=completed),
    ):
        result = _run_build(tmp_path)
    assert result.ran is True
    assert result.passed is True
    assert result.command == "make build"


def test_run_build_failure(tmp_path):
    """Failed build returns passed=False with output."""
    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "error: something broke"
    with (
        patch("mcloop.main.detect_build", return_value="swift build"),
        patch("mcloop.main.subprocess.run", return_value=completed),
    ):
        result = _run_build(tmp_path)
    assert result.ran is True
    assert result.passed is False
    assert result.command == "swift build"
    assert "something broke" in result.output


def test_run_build_exception(tmp_path):
    """Build exception returns passed=False."""
    with (
        patch("mcloop.main.detect_build", return_value="make"),
        patch("mcloop.main.subprocess.run", side_effect=OSError("not found")),
    ):
        result = _run_build(tmp_path)
    assert result.ran is True
    assert result.passed is False
    assert "not found" in result.output


# --- RunStatus and _main() exit code ---


def test_run_status_ok_property():
    """RunStatus.ok is True only for success status."""
    assert RunStatus("success").ok is True
    assert RunStatus("failure").ok is False
    assert RunStatus("interrupted").ok is False


def test_run_status_with_stuck_and_detail():
    """RunStatus stores stuck tasks and detail."""
    s = RunStatus("failure", stuck=["task A"], detail="something broke")
    assert s.stuck == ["task A"]
    assert s.detail == "something broke"
    assert not s.ok


def test_main_exits_nonzero_on_failure(tmp_path):
    """_main() calls sys.exit(1) when run_loop returns failure."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task\n"))

    failure = RunStatus("failure", detail="Task failed")
    with (
        patch("mcloop.main._parse_args") as mock_args,
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.run_loop", return_value=failure),
    ):
        mock_args.return_value = MagicMock(
            file=str(plan),
            command=None,
            dry_run=False,
            max_retries=3,
            cli=None,
            model=None,
            fallback_model=None,
            no_audit=False,
            reviewer=False,
            allow_web_tools=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _main()
    assert exc_info.value.code == 1


def test_main_exits_zero_on_success(tmp_path):
    """_main() does not call sys.exit when run_loop returns success."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task\n"))

    success = RunStatus("success")
    with (
        patch("mcloop.main._parse_args") as mock_args,
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.run_loop", return_value=success),
    ):
        mock_args.return_value = MagicMock(
            file=str(plan),
            command=None,
            dry_run=False,
            max_retries=3,
            cli=None,
            model=None,
            fallback_model=None,
            no_audit=False,
            reviewer=False,
            allow_web_tools=False,
        )
        # Should not raise SystemExit
        _main()


def test_run_loop_failure_returns_failure_status(tmp_path):
    """run_loop returns failure status when a task exhausts retries."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Do something\n"))
    (tmp_path / ".git").mkdir()

    mock_result = MagicMock()
    mock_result.success = False
    mock_result.output = "error output"
    mock_result.exit_code = 1

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=mock_result),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, max_retries=1, no_audit=True)

    assert result.status == "failure"
    assert not result.ok
    assert "Do something" in result.detail


def test_run_loop_success_returns_success_status(tmp_path):
    """run_loop returns success status when all tasks complete."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\nNo tasks.\n")
    (tmp_path / ".git").mkdir()

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, no_audit=True)

    assert result.status == "success"
    assert result.ok


def test_main_exits_nonzero_on_interrupted(tmp_path):
    """_main() calls sys.exit(1) when run_loop returns interrupted status."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task\n"))

    interrupted = RunStatus("interrupted", detail="User interrupted")
    with (
        patch("mcloop.main._parse_args") as mock_args,
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.run_loop", return_value=interrupted),
    ):
        mock_args.return_value = MagicMock(
            file=str(plan),
            command=None,
            dry_run=False,
            max_retries=3,
            cli=None,
            model=None,
            fallback_model=None,
            no_audit=False,
            reviewer=False,
            allow_web_tools=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _main()
    assert exc_info.value.code == 1


def test_main_exits_nonzero_on_failure_with_stuck_tasks(tmp_path):
    """_main() exits nonzero when run_loop returns failure with stuck tasks."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task A\n- [ ] Task B\n"))

    failure = RunStatus(
        "failure",
        stuck=["Task A", "Task B"],
        detail="2 tasks stuck",
    )
    with (
        patch("mcloop.main._parse_args") as mock_args,
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.run_loop", return_value=failure),
    ):
        mock_args.return_value = MagicMock(
            file=str(plan),
            command=None,
            dry_run=False,
            max_retries=3,
            cli=None,
            model=None,
            fallback_model=None,
            no_audit=False,
            reviewer=False,
            allow_web_tools=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _main()
    assert exc_info.value.code == 1


def test_run_status_frozen():
    """RunStatus is immutable (frozen dataclass)."""
    status = RunStatus("success")
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.status = "failure"


# --- terminal_failure sentinel tests ---
# Verify each failure mode produces its distinct notification AND skips
# the success/all-done notification. This prevents the recurring bug
# where a new failure mode is added but forgets to skip the success path.


def _run_loop_with_patches(plan, extra_patches=None, **kwargs):
    """Helper to run run_loop with standard mocks, returning (status, mock_notify).

    extra_patches is a dict of "mcloop.main.X" -> mock_value overrides.
    """
    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True

    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    patches = {
        "mcloop.main._checkpoint": None,
        "mcloop.main._push_or_die": None,
        "mcloop.main._kill_orphan_sessions": None,
        "mcloop.main._ensure_git": None,
        "mcloop.main._check_errors_json": True,
        "mcloop.main._has_meaningful_changes": True,
        "mcloop.main._changed_files": ["foo.py"],
        "mcloop.main._worktree_status": "",
        "mcloop.main.handle_sync": None,
        "mcloop.main._check_user_input": None,
        "mcloop.main.run_task": result,
        "mcloop.main.run_checks": checks_side_effect,
        "mcloop.main._commit": "",
        "mcloop.main._reinject_wrappers": None,
        "mcloop.main._print_summary": None,
        "mcloop.main._build_and_write_summary": None,
    }
    if extra_patches:
        patches.update(extra_patches)

    # Build context manager stack
    from contextlib import ExitStack

    with ExitStack() as stack:
        mock_notify = stack.enter_context(patch("mcloop.main.notify"))
        for name, val in patches.items():
            if isinstance(val, Exception):
                stack.enter_context(patch(name, side_effect=val))
            elif callable(val) and not isinstance(val, MagicMock):
                stack.enter_context(patch(name, side_effect=val))
            elif val is None:
                stack.enter_context(patch(name))
            elif isinstance(val, bool):
                stack.enter_context(patch(name, return_value=val))
            elif isinstance(val, (list, str)):
                stack.enter_context(patch(name, return_value=val))
            elif isinstance(val, MagicMock):
                stack.enter_context(patch(name, return_value=val))
            else:
                stack.enter_context(patch(name, return_value=val))

        status = run_loop(plan, **kwargs)
    return status, mock_notify


def _notify_messages(mock_notify):
    """Extract message strings from notify mock calls."""
    return [str(c) for c in mock_notify.call_args_list]


def test_terminal_failure_commit_failure_skips_success(tmp_path):
    """Commit failure sets terminal_failure, skips build/audit/all-done notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    status, mock_notify = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main._commit": RuntimeError("git commit failed"),
            "mcloop.main._run_build": MagicMock(),
            "mcloop.main._run_audit_fix_cycle": MagicMock(),
        },
    )

    assert status.status == "failure"
    assert "Commit failed" in status.detail
    msgs = _notify_messages(mock_notify)
    assert not any("All tasks completed" in m for m in msgs)
    assert not any("complete." in m for m in msgs)


def test_terminal_failure_commit_failure_skips_build(tmp_path):
    """Commit failure should not run _run_build."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True
    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit", side_effect=RuntimeError("git commit failed")),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main._build_and_write_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_build") as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan)

    mock_build.assert_not_called()
    mock_audit.assert_not_called()


def test_terminal_failure_task_exhausted_skips_success(tmp_path):
    """Task exhausting retries sets terminal_failure, skips all-done notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Hopeless task\n"))
    (tmp_path / ".git").mkdir()

    fail_result = MagicMock()
    fail_result.success = False
    fail_result.output = "error"
    fail_result.exit_code = 1

    from mcloop.checks import CheckResult

    status, mock_notify = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main.run_task": fail_result,
            "mcloop.main.run_checks": CheckResult(passed=True, output="ok", command="true"),
            "mcloop.main._run_build": MagicMock(),
            "mcloop.main._run_audit_fix_cycle": MagicMock(),
        },
        max_retries=2,
    )

    assert status.status == "failure"
    assert "Task failed" in status.detail
    msgs = _notify_messages(mock_notify)
    # Distinct notification for giving up
    assert any("Giving up" in m for m in msgs)
    # Success path skipped
    assert not any("All tasks completed" in m for m in msgs)


def test_terminal_failure_task_exhausted_skips_build(tmp_path):
    """Task failure should not run _run_build or audit."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Hopeless task\n"))
    (tmp_path / ".git").mkdir()

    fail_result = MagicMock()
    fail_result.success = False
    fail_result.output = "error"
    fail_result.exit_code = 1

    from mcloop.checks import CheckResult

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=fail_result),
        patch(
            "mcloop.main.run_checks",
            return_value=CheckResult(passed=True, output="ok", command="true"),
        ),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_build") as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
    ):
        run_loop(plan, max_retries=1)

    mock_build.assert_not_called()
    mock_audit.assert_not_called()


def test_terminal_failure_audit_failure_skips_build_and_success(tmp_path):
    """Audit failure sets terminal_failure, skips build and all-done notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    ok_build = BuildResult(ran=True, passed=True, command="make")

    status, mock_notify = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main._run_audit_fix_cycle": AuditResult.failed,
            "mcloop.main._run_build": ok_build,
        },
    )

    assert status.status == "failure"
    assert "Audit failed" in status.detail
    msgs = _notify_messages(mock_notify)
    # Distinct audit failure notification
    assert any("audit session failed" in m for m in msgs)
    # Success path skipped
    assert not any("All tasks completed" in m for m in msgs)


def test_terminal_failure_audit_failure_skips_build_call(tmp_path):
    """Audit failure sets terminal_failure and skips the success notification.

    Under split-plan, _run_build runs at the phase boundary (before audit),
    so build is expected to have been called once. The key invariant is
    that audit failure does NOT trigger a second build call nor a
    completion notification.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True
    full_suite_check = MagicMock()
    full_suite_check.passed = True

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.run_checks", side_effect=checks_side_effect),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
        patch("mcloop.main._run_audit_fix_cycle", return_value=AuditResult.failed),
        patch(
            "mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)
        ) as mock_build,
    ):
        run_loop(plan)

    # Build runs once at phase boundary but not a second time after audit failure
    assert mock_build.call_count <= 1
    msgs = [str(c) for c in mock_notify.call_args_list]
    assert not any("All tasks completed" in m for m in msgs)


def test_terminal_failure_full_suite_end_of_run_distinct_notification(tmp_path):
    """Full suite failure at end of run produces distinct notification, skips success."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    result = MagicMock()
    result.success = True
    result.output = "done"
    result.exit_code = 0

    per_task_check = MagicMock()
    per_task_check.passed = True
    full_suite_check = MagicMock()
    full_suite_check.passed = False
    full_suite_check.command = "pytest"
    full_suite_check.output = "FAILED"

    def checks_side_effect(project_dir, changed_files=None):
        if changed_files is not None:
            return per_task_check
        return full_suite_check

    status, mock_notify = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main.run_checks": checks_side_effect,
            "mcloop.main._run_build": MagicMock(),
            "mcloop.main._run_audit_fix_cycle": MagicMock(),
        },
    )

    assert status.status == "failure"
    assert "Full suite failed" in status.detail
    # Under split-plan, full suite runs at phase boundaries (final phase = end of run)
    assert "phase boundary" in status.detail
    msgs = _notify_messages(mock_notify)
    assert any("red repo" in m and "phase boundary" in m for m in msgs)
    assert not any("All tasks completed" in m for m in msgs)


def test_terminal_failure_build_end_of_run_distinct_notification(tmp_path):
    """Build failure at end of run produces distinct notification, skips success."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    failed_build = BuildResult(ran=True, passed=False, command="swift build")

    status, mock_notify = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main._run_build": failed_build,
            "mcloop.main._run_audit_fix_cycle": MagicMock(),
        },
        no_audit=True,
    )

    assert status.status == "failure"
    assert "Build failed" in status.detail
    # Under split-plan, build runs at phase boundaries (final phase = end of run)
    assert "phase boundary" in status.detail
    msgs = _notify_messages(mock_notify)
    assert any("Build failed" in m and "phase boundary" in m for m in msgs)
    assert not any("All tasks completed" in m for m in msgs)


# ---- Run summary tests ----


def test_run_summary_schema_fields():
    """RunSummary dataclass has all required fields."""
    from mcloop.run_summary import CheckEntry, RunSummary, TaskEntry

    s = RunSummary(
        run_start="2026-01-01T00:00:00+00:00",
        run_end="2026-01-01T00:05:00+00:00",
        elapsed_seconds=300.0,
        mode="plan",
    )
    assert s.terminal_status == ""
    assert s.failure_detail == ""
    assert s.stop_reason == ""
    assert s.tasks == []
    assert s.checks == []
    assert s.commit_hashes == []
    assert s.stuck == []
    assert s.full_suite_passed is None
    assert s.build_passed is None
    assert s.audit_result is None

    t = TaskEntry(
        label="1",
        text="Do thing",
        outcome="success",
        elapsed=10.0,
        model="opus",
        attempts=1,
        commit_hash="abc123",
    )
    assert t.label == "1"
    assert t.commit_hash == "abc123"

    c = CheckEntry(command="pytest", passed=True, elapsed=5.0)
    assert c.command == "pytest"


def test_run_summary_write_and_latest(tmp_path):
    """write_run_summary writes dated file and copies to latest.json."""
    from mcloop.run_summary import RunSummary, write_run_summary

    s = RunSummary(
        run_start="2026-04-10T12:00:00+00:00",
        run_end="2026-04-10T12:05:00+00:00",
        elapsed_seconds=300.0,
        mode="plan",
        terminal_status="success",
    )
    path = write_run_summary(tmp_path, s)
    assert path.exists()
    assert "20260410_120000" in path.name

    latest = tmp_path / ".mcloop" / "runs" / "latest.json"
    assert latest.exists()

    data = json.loads(path.read_text())
    latest_data = json.loads(latest.read_text())
    assert data == latest_data
    assert data["mode"] == "plan"
    assert data["terminal_status"] == "success"
    assert data["elapsed_seconds"] == 300.0


def test_run_summary_all_fields_populated(tmp_path):
    """A fully populated RunSummary serializes all fields."""
    from mcloop.run_summary import CheckEntry, RunSummary, TaskEntry, write_run_summary

    s = RunSummary(
        run_start="2026-04-10T12:00:00+00:00",
        run_end="2026-04-10T12:10:00+00:00",
        elapsed_seconds=600.0,
        mode="plan",
        tasks=[
            TaskEntry("1", "Do task", "success", 120.5, "opus", 1, "abc123"),
            TaskEntry("2", "Fix bug", "failed", 60.0, "sonnet", 3, ""),
        ],
        checks=[
            CheckEntry("pytest", True, 30.0),
            CheckEntry("ruff check .", True, 2.5),
        ],
        full_suite_passed=True,
        build_passed=True,
        audit_result="no_bugs",
        terminal_status="success",
        failure_detail="",
        stuck=[],
        commit_hashes=["abc123", "def456"],
    )
    path = write_run_summary(tmp_path, s)
    data = json.loads(path.read_text())

    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["label"] == "1"
    assert data["tasks"][0]["commit_hash"] == "abc123"
    assert data["tasks"][1]["outcome"] == "failed"
    assert len(data["checks"]) == 2
    assert data["full_suite_passed"] is True
    assert data["build_passed"] is True
    assert data["audit_result"] == "no_bugs"
    assert data["commit_hashes"] == ["abc123", "def456"]
    assert data["stop_reason"] == ""


def test_failed_task_summary_does_not_inherit_prior_changed_files(tmp_path):
    """A failing second task must not inherit the first task's
    ``changed_files`` list.

    Scenario: task 1 succeeds with ``changed_files == ["foo.py"]``.
    Task 2's ``run_task`` returns non-success on every attempt, so
    the inner loop short-circuits at the failure branch (line ~1271)
    and never reaches the autofix-then-changed-files block. Without
    a per-task reset, ``changed_files`` from task 1 stays in scope
    and shows up in task 2's no-success summary entry.

    The fix resets ``changed_files = []`` and ``result = None`` at
    the top of every task iteration, so the no-success path
    serializes the empty default rather than the previous task's
    list.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] First task\n- [ ] Second task\n"))
    (tmp_path / ".git").mkdir()

    call_count = {"run_task": 0}

    def fake_run_task(*args, **kwargs):
        call_count["run_task"] += 1
        r = MagicMock()
        if call_count["run_task"] == 1:
            r.success = True
            r.output = "ok\n"
            r.exit_code = 0
            r.log_path = tmp_path / "logs" / "task-1.log"
        else:
            r.success = False
            r.output = "boom\n"
            r.exit_code = 1
            r.log_path = tmp_path / "logs" / f"task-{call_count['run_task']}.log"
        return r

    captured: dict = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    from mcloop.checks import CheckResult

    status, _ = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main.run_task": fake_run_task,
            "mcloop.main._changed_files": ["foo.py"],
            "mcloop.main.run_checks": CheckResult(passed=True, output="ok", command="true"),
            "mcloop.main._build_and_write_summary": capture_summary,
        },
        max_retries=1,
    )

    tasks_summary = captured.get("task_entries", [])
    assert len(tasks_summary) == 2, (
        f"expected two task entries, got {len(tasks_summary)}: {tasks_summary}"
    )
    first, second = tasks_summary
    assert first.outcome == "success"
    assert first.changed_files == ["foo.py"]
    assert second.outcome == "failed"
    assert second.changed_files == [], (
        "Second task summary entry inherited changed_files from "
        f"the first task: {second.changed_files!r}"
    )
    assert second.exit_code == 1
    assert status.status == "failure"


def test_task_entry_carries_parity_fields(tmp_path):
    """``TaskEntry`` carries the four parity fields the orchestra
    integration smoke test needs and the writer round-trips them.

    The fields mirror ``CodeEditResult`` so a reader can compare the
    two backends directly off the JSON without scraping the per-task
    log. Defaults match an empty result so older call sites that have
    not been updated still serialize a valid entry.
    """
    from mcloop.run_summary import RunSummary, TaskEntry, write_run_summary

    populated = TaskEntry(
        label="1",
        text="Add hello world comment",
        outcome="success",
        elapsed=12.5,
        model="opus",
        attempts=1,
        commit_hash="deadbeef",
        success=True,
        exit_code=0,
        log_path="/tmp/logs/session-1.log",
        changed_files=["README.md", "PLAN.md"],
    )
    assert populated.success is True
    assert populated.exit_code == 0
    assert populated.log_path == "/tmp/logs/session-1.log"
    assert populated.changed_files == ["README.md", "PLAN.md"]

    default = TaskEntry(label="2", text="x", outcome="failed", elapsed=1.0)
    assert default.success is False
    assert default.exit_code == 0
    assert default.log_path == ""
    assert default.changed_files == []

    s = RunSummary(
        run_start="2026-04-29T12:00:00+00:00",
        run_end="2026-04-29T12:00:13+00:00",
        elapsed_seconds=13.0,
        mode="plan",
        tasks=[populated, default],
    )
    path = write_run_summary(tmp_path, s)
    data = json.loads(path.read_text())
    first = data["tasks"][0]
    assert first["success"] is True
    assert first["exit_code"] == 0
    assert first["log_path"] == "/tmp/logs/session-1.log"
    assert first["changed_files"] == ["README.md", "PLAN.md"]

    second = data["tasks"][1]
    assert second["success"] is False
    assert second["exit_code"] == 0
    assert second["log_path"] == ""
    assert second["changed_files"] == []


def test_run_summary_successful_run(tmp_path):
    """Successful run_loop produces a run summary with success status."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)
        # Also capture positional args
        captured["_positional"] = args

    status, _ = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main._build_and_write_summary": capture_summary,
        },
        no_audit=True,
    )

    assert status.status == "success"
    assert captured.get("terminal_status") == "success"
    assert captured.get("mode") == "plan"


def test_run_summary_failed_run(tmp_path):
    """Failed run produces a summary with failure detail."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    fail_result = MagicMock()
    fail_result.success = False
    fail_result.output = "error"
    fail_result.exit_code = 1

    from mcloop.checks import CheckResult

    status, _ = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main.run_task": fail_result,
            "mcloop.main.run_checks": CheckResult(passed=True, output="ok", command="true"),
            "mcloop.main._build_and_write_summary": capture_summary,
        },
        max_retries=1,
    )

    assert status.status == "failure"
    assert "failure" in captured.get("terminal_status", "")
    assert captured.get("failure_detail", "") != ""


def test_run_summary_interrupted_run(tmp_path):
    """Interrupted run still produces a summary."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Do task\n"))
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    result = MagicMock()
    result.success = True
    result.output = "session limit"
    result.exit_code = 2

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result),
        patch("mcloop.main.is_session_limited", return_value=True),
        patch("mcloop.main.time.sleep", side_effect=KeyboardInterrupt),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main._build_and_write_summary", side_effect=capture_summary),
        patch("mcloop.main.notify"),
    ):
        status = run_loop(plan)

    assert status.status == "interrupted"
    assert captured.get("terminal_status") == "interrupted"


def test_commit_returns_hash(tmp_path):
    """_commit returns the new HEAD hash after committing."""
    import subprocess

    from mcloop.git_ops import _commit, _get_git_hash

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    # Create initial commit
    (tmp_path / "a.txt").write_text("a")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    # Make a change and commit via _commit
    (tmp_path / "b.txt").write_text("b")
    with patch("mcloop.git_ops.notify"):
        result_hash = _commit(tmp_path, "Add b.txt")

    assert result_hash != ""
    assert len(result_hash) == 40
    assert result_hash == _get_git_hash(tmp_path)


# --- --stop-after-stage and --stop-after-one tests ---


def test_parse_args_stop_after_stage():
    """--stop-after-stage flag is parsed correctly."""
    args = _parse("--stop-after-stage")
    assert args.stop_after_stage is True


def test_parse_args_stop_after_one():
    """--stop-after-one flag is parsed correctly."""
    args = _parse("--stop-after-one")
    assert args.stop_after_one is True


def test_parse_args_stop_flags_default_false():
    """Stop flags default to False."""
    args = _parse()
    assert args.stop_after_stage is False
    assert args.stop_after_one is False


def test_parse_args_retry():
    """--retry flag is parsed correctly."""
    args = _parse("--retry")
    assert args.retry is True


def test_parse_args_retry_default_false():
    args = _parse()
    assert args.retry is False


def test_run_loop_retry_resets_failed_markers(tmp_path):
    """run_loop(retry=True) flips [!] back to [ ] in active files before picking tasks."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n- [ ] unrelated master task\n"))
    current = tmp_path / "PLAN.md"
    current.write_text(canonical_plan_text("# Plan\n- [!] failed feature\n- [ ] next feature\n"))
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(canonical_plan_text("## Bugs\n- [!] failed bug\n"))
    (tmp_path / ".git").mkdir()

    with (
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit", return_value=""),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["bugfix.txt"]),
        patch("mcloop.main._worktree_status", return_value=" M bugfix.txt"),
        patch("mcloop.main.get_check_commands", return_value=[]),
        patch("mcloop.main.detect_build", return_value=None),
        patch("mcloop.main.detect_run", return_value=None),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        mock_checks.return_value = MagicMock(passed=True)

        def runner_side_effect(*a, **kw):
            (tmp_path / "bugfix.txt").write_text("done")
            content = bugs.read_text()
            content = content.replace(
                "- [ ] T-000001: failed bug",
                "- [x] T-000001: failed bug",
                1,
            )
            bugs.write_text(content)
            return MagicMock(success=True, output="done", exit_code=0)

        mock_run.side_effect = runner_side_effect

        run_loop(plan, max_retries=3, stop_after_one=True, retry=True)

    assert "- [!]" not in current.read_text()
    assert_canonical_checkbox(current.read_text(), " ", "failed feature")
    assert "- [!]" not in bugs.read_text()


def test_run_loop_retry_false_leaves_failed_markers(tmp_path):
    """Without --retry, failed markers remain intact."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n- [ ] unrelated master task\n"))
    current = tmp_path / "PLAN.md"
    current.write_text(canonical_plan_text("# Plan\n- [!] failed feature\n- [ ] next feature\n"))
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(canonical_plan_text("## Bugs\n- [!] failed bug\n"))
    (tmp_path / ".git").mkdir()

    with (
        patch("mcloop.main._check_errors_json", return_value=True),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.run_task") as mock_run,
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main._commit", return_value=""),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main.get_check_commands", return_value=[]),
        patch("mcloop.main.detect_build", return_value=None),
        patch("mcloop.main.detect_run", return_value=None),
        patch("mcloop.main._run_audit_fix_cycle"),
    ):
        mock_checks.return_value = MagicMock(passed=True)

        def runner_side_effect(*a, **kw):
            content = current.read_text()
            content = content.replace(
                "- [ ] T-000002: next feature",
                "- [x] T-000002: next feature",
                1,
            )
            current.write_text(content)
            return MagicMock(success=True, output="done", exit_code=0)

        mock_run.side_effect = runner_side_effect

        run_loop(plan, max_retries=3, stop_after_one=True, retry=False)

    # The [!] markers must survive when --retry is not passed.
    assert_canonical_checkbox(current.read_text(), "!", "failed feature")
    assert_canonical_checkbox(bugs.read_text(), "!", "failed bug")


def test_stop_after_one_exits_after_single_task(tmp_path):
    """--stop-after-one runs one task then exits with success."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n- [ ] First task\n- [ ] Second task\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    assert result.detail == "Stopped after one task as requested"
    # Only the first task should have been checked off (in active PLAN.md)
    from mcloop._planfile_compat import parse as cl_parse

    tasks = cl_parse(tmp_path / "PLAN.md")
    assert tasks[0].checked
    assert not tasks[1].checked
    # Distinct notification
    mock_notify.assert_any_call("Stopped after one task as requested")


def test_stop_after_one_bypasses_batch(tmp_path):
    """--stop-after-one skips batching and runs a single task."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("# Plan\n- [ ] [BATCH] Parent\n  - [ ] Child A\n  - [ ] Child B\n")
    )
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock) as mock_run,
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._run_batch") as mock_batch,
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    # Batch should NOT have been called
    mock_batch.assert_not_called()
    # run_task should have been called once (single child)
    mock_run.assert_called_once()
    # Only Child A should be checked off (in active PLAN.md)
    from mcloop._planfile_compat import parse as cl_parse

    tasks = cl_parse(tmp_path / "PLAN.md")
    parent = tasks[0]
    assert parent.children[0].checked
    assert not parent.children[1].checked


def test_stop_after_one_works_in_bug_only_mode(tmp_path):
    """--stop-after-one works in bug-only mode."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Bug A\n- [ ] Bug B\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    assert result.detail == "Stopped after one task as requested"
    # Only first bug fixed (bugs live in the standalone BUGS.md)
    from mcloop._planfile_compat import parse as cl_parse

    bugs = cl_parse(tmp_path / "BUGS.md")
    bug_items = [t for t in bugs if t.stage == "Bugs"]
    assert bug_items[0].checked
    assert not bug_items[1].checked
    mock_notify.assert_any_call("Stopped after one task as requested")


def test_stop_after_stage_warns_in_bug_only_mode(tmp_path, capsys):
    """--stop-after-stage prints warning and is ignored in bug-only mode."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Fix crash\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._launch_app_verification", return_value=None),
    ):
        result = run_loop(plan, stop_after_stage=True)

    assert result.ok
    captured = capsys.readouterr()
    assert "--stop-after-stage ignored in bug-only mode" in captured.out


def test_stop_after_stage_distinct_notification(tmp_path):
    """--stop-after-stage produces a distinct success notification."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n## Stage 2: Extra\n- [ ] Task B\n")
    )
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify") as mock_notify,
    ):
        result = run_loop(plan, stop_after_stage=True, no_audit=True)

    assert result.ok
    # Check the notification contains the phase-completion message
    notify_msgs = [str(c) for c in mock_notify.call_args_list]
    assert any("Stage 1: Core complete" in msg for msg in notify_msgs)


def test_stop_after_one_no_post_loop_processing(tmp_path):
    """--stop-after-one skips full-suite check, audit, and build."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n- [ ] Task B\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result) as mock_checks,
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._run_build") as mock_build,
        patch("mcloop.main._run_audit_fix_cycle") as mock_audit,
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    # run_checks is called once (during the task check), NOT again for full-suite
    assert mock_checks.call_count == 1
    mock_build.assert_not_called()
    mock_audit.assert_not_called()


def test_main_passes_stop_flags_to_run_loop(tmp_path):
    """_main() forwards --stop-after-stage and --stop-after-one to run_loop."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] Task\n"))

    with (
        patch("mcloop.main._parse_args") as mock_args,
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.run_loop", return_value=RunStatus("success")) as mock_loop,
    ):
        mock_args.return_value = MagicMock(
            file=str(plan),
            command=None,
            dry_run=False,
            max_retries=3,
            cli=None,
            model=None,
            fallback_model=None,
            no_audit=False,
            reviewer=False,
            allow_web_tools=False,
            stop_after_stage=True,
            stop_after_one=True,
        )
        _main()

    _, kwargs = mock_loop.call_args
    assert kwargs["stop_after_stage"] is True
    assert kwargs["stop_after_one"] is True


def test_stop_after_one_prints_stop_reason(tmp_path, capsys):
    """--stop-after-one prints stop reason in terminal summary (not patched)."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n- [ ] Task B\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    captured = capsys.readouterr()
    assert "Stopped after one task as requested" in captured.out


def test_stop_after_one_bug_only_prints_stop_reason(tmp_path, capsys):
    """--stop-after-one in bug-only mode prints stop reason in terminal summary."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Bug A\n- [ ] Bug B\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    captured = capsys.readouterr()
    assert "Stopped after one task as requested" in captured.out


def test_stop_after_stage_prints_stop_reason(tmp_path, capsys):
    """--stop-after-stage prints distinct stop reason in terminal summary."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n## Stage 2: Extra\n- [ ] Task B\n")
    )
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_stage=True, no_audit=True)

    assert result.ok
    captured = capsys.readouterr()
    # Under split-plan, the stop-after-stage message uses the completed-phase form
    assert "Stage 1: Core complete" in captured.out
    assert "Run mcloop again" in captured.out


def test_normal_stage_complete_no_stop_reason(tmp_path, capsys):
    """Default-mode multi-stage runs DO NOT exit at the first stage
    boundary — the loop advances to the next phase. Updated for the
    phase-transition contract: ``Stage 1: Core complete. Run mcloop
    again to start ...`` is a ``--stop-after-stage`` message; in
    default mode the run processes all stages and ends with
    "All tasks completed!"."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n## Stage 2: Extra\n- [ ] Task B\n")
    )
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, no_audit=True)

    assert result.ok
    captured = capsys.readouterr()
    # The loop advanced to Stage 2 instead of exiting at boundary.
    assert "Advancing to Stage 2: Extra" in captured.out
    # The OLD "Run mcloop again" message belongs to --stop-after-stage;
    # default mode must not print it.
    assert "Run mcloop again" not in captured.out


def test_run_summary_stop_after_one_terminal_status(tmp_path):
    """--stop-after-one sets terminal_status='stopped' and stop_reason='stop_after_one'."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n- [ ] First task\n- [ ] Second task\n"))
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main._build_and_write_summary", side_effect=capture_summary),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    assert captured.get("terminal_status") == "stopped"
    assert captured.get("stop_reason") == "stop_after_one"
    assert captured.get("failure_detail", "") == ""


def test_run_summary_stop_after_one_bug_only_terminal_status(tmp_path):
    """--stop-after-one in bug-only mode sets stopped status."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "PLAN.md").write_text(canonical_plan_text("- [ ] placeholder\n"))
    (tmp_path / "BUGS.md").write_text(canonical_plan_text("## Bugs\n- [ ] Bug A\n- [ ] Bug B\n"))
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main._build_and_write_summary", side_effect=capture_summary),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_one=True)

    assert result.ok
    assert captured.get("terminal_status") == "stopped"
    assert captured.get("stop_reason") == "stop_after_one"
    assert captured.get("failure_detail", "") == ""


def test_run_summary_stop_after_stage_terminal_status(tmp_path):
    """--stop-after-stage sets terminal_status='stopped' and stop_reason='stop_after_stage'."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        canonical_plan_text("## Stage 1: Core\n- [ ] Task A\n## Stage 2: Extra\n- [ ] Task B\n")
    )
    (tmp_path / ".git").mkdir()

    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["foo.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        patch("mcloop.main.run_task", return_value=result_mock),
        patch("mcloop.main.run_checks", return_value=check_result),
        patch("mcloop.main._commit"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main._run_build", return_value=BuildResult(ran=False, passed=True)),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main._build_and_write_summary", side_effect=capture_summary),
        patch("mcloop.main.notify"),
    ):
        result = run_loop(plan, stop_after_stage=True, no_audit=True)

    assert result.ok
    assert captured.get("terminal_status") == "stopped"
    assert captured.get("stop_reason") == "stop_after_stage"
    assert captured.get("failure_detail", "") == ""


def test_run_summary_stop_reason_field_in_schema():
    """RunSummary includes stop_reason field that serializes correctly."""
    from mcloop.run_summary import RunSummary

    s = RunSummary(
        run_start="2026-04-10T12:00:00+00:00",
        run_end="2026-04-10T12:05:00+00:00",
        elapsed_seconds=300.0,
        mode="plan",
        terminal_status="stopped",
        stop_reason="stop_after_one",
    )
    assert s.terminal_status == "stopped"
    assert s.stop_reason == "stop_after_one"
    assert s.failure_detail == ""


def test_run_summary_stop_reason_writes_to_json(tmp_path):
    """stop_reason is persisted in the JSON output."""
    from mcloop.run_summary import RunSummary, write_run_summary

    s = RunSummary(
        run_start="2026-04-10T12:00:00+00:00",
        run_end="2026-04-10T12:05:00+00:00",
        elapsed_seconds=300.0,
        mode="plan",
        terminal_status="stopped",
        stop_reason="stop_after_stage",
    )
    path = write_run_summary(tmp_path, s)
    data = json.loads(path.read_text())
    assert data["terminal_status"] == "stopped"
    assert data["stop_reason"] == "stop_after_stage"
    assert data["failure_detail"] == ""


def test_run_summary_normal_success_no_stop_reason(tmp_path):
    """Normal successful run has empty stop_reason and terminal_status='success'."""
    captured = {}

    def capture_summary(*args, **kwargs):
        captured.update(kwargs)

    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n- [ ] Only task\n"))
    (tmp_path / ".git").mkdir()

    result_mock = MagicMock()
    result_mock.success = True
    result_mock.output = "done"
    result_mock.exit_code = 0

    check_result = MagicMock()
    check_result.passed = True

    status, _ = _run_loop_with_patches(
        plan,
        extra_patches={
            "mcloop.main._build_and_write_summary": capture_summary,
        },
        no_audit=True,
    )

    assert status.status == "success"
    assert captured.get("terminal_status") == "success"
    assert captured.get("stop_reason", "") == ""
