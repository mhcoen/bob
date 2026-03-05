"""Tests for loop.runner."""

import pytest

from mcloop.runner import _build_command, _slugify, _write_log


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


def test_build_command_unknown():
    with pytest.raises(ValueError, match="Unknown CLI"):
        _build_command("unknown", "task")


def test_slugify():
    assert _slugify("Add User Authentication!") == "add-user-authentication"
    assert len(_slugify("x" * 100)) <= 50


def test_slugify_special_chars():
    assert _slugify("Hello, World! 123") == "hello-world-123"
    assert _slugify("---leading-trailing---") == "leading-trailing"


def test_slugify_empty():
    assert _slugify("") == ""


def test_write_log(tmp_path):
    log_path = _write_log(
        tmp_path, "My task", ["claude", "-p", "do stuff"], "output here\n", 0
    )
    assert log_path.exists()
    content = log_path.read_text()
    assert "Task: My task" in content
    assert "Exit code: 0" in content
    assert "output here" in content
    assert "claude -p do stuff" in content


def test_write_log_filename_format(tmp_path):
    log_path = _write_log(tmp_path, "Add auth", ["cmd"], "out", 1)
    assert "add-auth" in log_path.name
    assert log_path.suffix == ".log"
