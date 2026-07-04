"""Tests for the `bob install` command (bob_tools.bob_cli)."""

import json
from pathlib import Path

import pytest

from bob_tools import bob_cli


@pytest.fixture
def fake_hook(tmp_path: Path) -> Path:
    src = tmp_path / "telegram-permission-hook.py"
    src.write_text("#!/usr/bin/env python3\nprint('{}')\n")
    return src


def _run_install(
    home: Path,
    hook: Path,
    capsys: pytest.CaptureFixture[str],
) -> int:
    rc = bob_cli.main(["install", "--home", str(home), "--hook", str(hook)])
    return rc


def test_install_copies_hook_and_registers(
    tmp_path: Path,
    fake_hook: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    rc = _run_install(home, fake_hook, capsys)
    assert rc == 0

    dst = home / ".claude" / "hooks" / "telegram-permission-hook.py"
    assert dst.exists()
    assert dst.read_text() == fake_hook.read_text()

    settings = json.loads((home / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert f"python3 {dst}" in cmds


def test_install_is_idempotent(
    tmp_path: Path,
    fake_hook: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    assert _run_install(home, fake_hook, capsys) == 0
    assert _run_install(home, fake_hook, capsys) == 0

    settings = json.loads((home / ".claude" / "settings.json").read_text())
    dst = home / ".claude" / "hooks" / "telegram-permission-hook.py"
    cmds = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
    # registered exactly once despite two installs
    assert cmds.count(f"python3 {dst}") == 1
    out = capsys.readouterr().out
    assert "already registered" in out


def test_install_preserves_existing_hooks_and_backs_up(
    tmp_path: Path,
    fake_hook: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    settings = claude / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "existing.sh"}],
                        }
                    ]
                }
            }
        )
    )

    assert _run_install(home, fake_hook, capsys) == 0

    data = json.loads(settings.read_text())
    cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "existing.sh" in cmds  # not clobbered
    assert any("telegram-permission-hook.py" in c for c in cmds)
    assert (claude / "settings.json.bak").exists()  # backup made


def test_install_dedupes_prior_hook_at_other_path(
    tmp_path: Path,
    fake_hook: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Simulate a prior `mcloop install` that registered the hook at ~/.mcloop/hooks/,
    # plus an unrelated hook. `bob install` must consolidate to a single hook entry.
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    settings = claude / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python3 "
                                        "~/.mcloop/hooks/telegram-permission-hook.py"
                                    ),
                                }
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "keep-me.sh"}],
                        },
                    ]
                }
            }
        )
    )

    assert _run_install(home, fake_hook, capsys) == 0

    data = json.loads(settings.read_text())
    cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    hook_cmds = [c for c in cmds if "telegram-permission-hook.py" in c]
    assert len(hook_cmds) == 1  # the stale ~/.mcloop entry removed, exactly one remains
    assert hook_cmds[0].endswith("/.claude/hooks/telegram-permission-hook.py")
    assert "keep-me.sh" in cmds  # unrelated hook preserved
    assert "replaced 1" in capsys.readouterr().out


def test_install_missing_hook_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    rc = bob_cli.main(
        ["install", "--home", str(home), "--hook", str(tmp_path / "nope.py")]
    )
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_default_hook_points_at_repo_mcloop_copy() -> None:
    # The default hook path resolves to the real repo artifact.
    assert bob_cli._default_hook().name == "telegram-permission-hook.py"
    assert bob_cli._default_hook().parent.name == "mcloop"
