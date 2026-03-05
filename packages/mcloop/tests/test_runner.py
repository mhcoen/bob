"""Tests for loop.runner."""

from mcloop.runner import _build_command, _slugify


def test_build_command_claude():
    cmd = _build_command("claude", "fix the bug")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "fix the bug" in cmd


def test_build_command_codex():
    cmd = _build_command("codex", "fix the bug")
    assert cmd[0] == "codex"
    assert "-q" in cmd
    assert "fix the bug" in cmd


def test_slugify():
    assert _slugify("Add User Authentication!") == "add-user-authentication"
    assert len(_slugify("x" * 100)) <= 50
