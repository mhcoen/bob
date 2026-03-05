"""Tests for loop.checks."""

import json
import subprocess
from unittest.mock import patch

from mcloop.checks import _detect_commands, _load_config_commands, run_checks


def test_detect_commands_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    cmds = _detect_commands(tmp_path)
    assert "ruff check ." in cmds
    assert "pytest" in cmds


def test_detect_commands_pyproject_ruff_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = _detect_commands(tmp_path)
    assert "ruff check ." in cmds
    assert "pytest" not in cmds


def test_detect_commands_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    cmds = _detect_commands(tmp_path)
    assert "npm test" in cmds


def test_detect_commands_package_json_no_test(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}')
    cmds = _detect_commands(tmp_path)
    assert "npm test" not in cmds


def test_detect_commands_makefile(tmp_path):
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path)
    assert "make check" in cmds


def test_detect_commands_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift package\n")
    cmds = _detect_commands(tmp_path)
    assert "swift build" in cmds


def test_detect_commands_empty(tmp_path):
    cmds = _detect_commands(tmp_path)
    assert cmds == []


def test_detect_commands_multiple(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path)
    assert "ruff check ." in cmds
    assert "make check" in cmds


def test_load_config_commands_no_file(tmp_path):
    assert _load_config_commands(tmp_path) is None


def test_load_config_commands_with_checks(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["ruff check .", "pytest"]}))
    assert _load_config_commands(tmp_path) == ["ruff check .", "pytest"]


def test_load_config_commands_no_checks_key(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"other": "value"}))
    assert _load_config_commands(tmp_path) is None


def test_load_config_commands_invalid_json(tmp_path):
    (tmp_path / "mcloop.json").write_text("not json")
    assert _load_config_commands(tmp_path) is None


def test_load_config_commands_checks_not_list(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": "ruff check ."}))
    assert _load_config_commands(tmp_path) is None


@patch("mcloop.checks.subprocess.run")
def test_run_checks_uses_config_commands(mock_run, tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["echo hello"]}))
    # Also add a pyproject.toml to ensure auto-detect is NOT used
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="echo hello", returncode=0, stdout="hello\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed
    assert mock_run.call_count == 1
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd == "echo hello"


def test_run_checks_config_overrides_autodetect(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["true"]}))
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    result = run_checks(tmp_path)
    assert result.passed


def test_run_checks_no_commands(tmp_path):
    result = run_checks(tmp_path)
    assert result.passed
    assert result.command == "(none)"


@patch("mcloop.checks.subprocess.run")
def test_run_checks_all_pass(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=0, stdout="All good\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed


@patch("mcloop.checks.subprocess.run")
def test_run_checks_first_fails(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=1, stdout="", stderr="E501 line too long\n"
    )
    result = run_checks(tmp_path)
    assert not result.passed
    assert result.command == "ruff check ."
    assert "E501" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_timeout(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="ruff check .", timeout=300)
    result = run_checks(tmp_path)
    assert not result.passed
    assert "TIMEOUT" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_second_command_fails(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.side_effect = [
        subprocess.CompletedProcess(args="ruff check .", returncode=0, stdout="ok\n", stderr=""),
        subprocess.CompletedProcess(args="pytest", returncode=1, stdout="FAILED\n", stderr=""),
    ]
    result = run_checks(tmp_path)
    assert not result.passed
    assert result.command == "pytest"
