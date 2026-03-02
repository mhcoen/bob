"""Tests for loop.checks."""

from loop.checks import _detect_commands, run_checks


def test_detect_commands_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    cmds = _detect_commands(tmp_path)
    assert "ruff check ." in cmds
    assert "pytest" in cmds


def test_detect_commands_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    cmds = _detect_commands(tmp_path)
    assert "npm test" in cmds


def test_detect_commands_empty(tmp_path):
    cmds = _detect_commands(tmp_path)
    assert cmds == []


def test_run_checks_no_commands(tmp_path):
    result = run_checks(tmp_path)
    assert result.passed
    assert result.command == "(none)"
