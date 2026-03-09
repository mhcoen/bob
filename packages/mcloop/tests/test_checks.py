"""Tests for loop.checks."""

import json
import subprocess
from unittest.mock import patch

from mcloop.checks import (
    _classify_run_command,
    _detect_commands,
    _load_config,
    detect_app_type,
    get_check_commands,
    run_checks,
)


def test_detect_commands_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" in cmds


def test_detect_commands_pyproject_ruff_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" not in cmds


def test_detect_commands_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    cmds = _detect_commands(tmp_path, {})
    assert "npm test" in cmds


def test_detect_commands_package_json_no_test(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}')
    cmds = _detect_commands(tmp_path, {})
    assert "npm test" not in cmds


def test_detect_commands_makefile(tmp_path):
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path, {})
    assert "make check" in cmds


def test_detect_commands_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift package\n")
    cmds = _detect_commands(tmp_path, {})
    assert "swift build --disable-sandbox" in cmds


def test_detect_commands_empty(tmp_path):
    cmds = _detect_commands(tmp_path, {})
    assert cmds == []


def test_detect_commands_multiple(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "make check" in cmds


def test_load_config_no_file(tmp_path):
    assert _load_config(tmp_path) == {}


def test_load_config_with_checks(tmp_path):
    data = {"checks": ["ruff check .", "pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert _load_config(tmp_path) == data


def test_load_config_no_checks_key(tmp_path):
    data = {"other": "value"}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert _load_config(tmp_path) == data


def test_load_config_invalid_json(tmp_path):
    (tmp_path / "mcloop.json").write_text("not json")
    assert _load_config(tmp_path) == {}


def test_get_check_commands_explicit(tmp_path):
    data = {"checks": ["ruff check .", "pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert get_check_commands(tmp_path) == [
        "ruff check .",
        "pytest",
    ]


def test_get_check_commands_fallback(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = get_check_commands(tmp_path)
    assert "ruff check ." in cmds


def test_get_check_commands_checks_not_list(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": "not a list"}))
    # Falls back to detect since checks is not a list
    assert get_check_commands(tmp_path) == []


@patch("mcloop.checks.subprocess.run")
def test_run_checks_falls_back_to_autodetect_when_no_config(mock_run, tmp_path):
    # No mcloop.json present; pyproject.toml should trigger auto-detection
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=0, stdout="All good\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed
    assert mock_run.call_count == 1
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd == ["ruff", "check", "."]


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
    assert called_cmd == ["echo", "hello"]


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


# --- _classify_run_command tests ---


def test_classify_open_app():
    assert _classify_run_command("open MyApp.app") == "gui"


def test_classify_open_app_with_path():
    assert _classify_run_command("open /Applications/MyApp.app") == "gui"


def test_classify_open_non_app():
    """open without .app is not GUI (e.g. open a URL)."""
    assert _classify_run_command("open http://localhost:3000") == "cli"


def test_classify_run_sh():
    assert _classify_run_command("./run.sh") == "gui"


def test_classify_launch_sh():
    assert _classify_run_command("./launch.sh") == "gui"


def test_classify_path_sh():
    assert _classify_run_command("/usr/local/bin/run.sh") == "gui"


def test_classify_bare_binary():
    assert _classify_run_command("./myapp") == "cli"


def test_classify_build_binary():
    assert _classify_run_command(".build/debug/MyApp") == "cli"


def test_classify_python_script():
    assert _classify_run_command("python main.py") == "cli"


def test_classify_python3_script():
    assert _classify_run_command("python3 app.py") == "cli"


def test_classify_cargo_run():
    assert _classify_run_command("cargo run") == "cli"


def test_classify_go_run():
    assert _classify_run_command("go run .") == "cli"


def test_classify_swift_run():
    assert _classify_run_command("swift run MyApp") == "cli"


def test_classify_npm_start():
    assert _classify_run_command("npm start") == "web"


def test_classify_npm_run_dev():
    assert _classify_run_command("npm run dev") == "web"


def test_classify_flask_run():
    assert _classify_run_command("flask run") == "web"


def test_classify_uvicorn():
    assert _classify_run_command("uvicorn app:main") == "web"


def test_classify_gunicorn():
    assert _classify_run_command("gunicorn app:app") == "web"


def test_classify_python_m_flask():
    assert _classify_run_command("python -m flask run") == "web"


def test_classify_python_m_http_server():
    assert _classify_run_command("python -m http.server") == "web"


def test_classify_python_m_uvicorn():
    assert _classify_run_command("python3 -m uvicorn app:main") == "web"


def test_classify_empty():
    assert _classify_run_command("") == "cli"


# --- detect_app_type integration tests ---


def test_detect_app_type_from_config(tmp_path):
    config = {"run": "open MyApp.app"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "gui"


def test_detect_app_type_web_from_config(tmp_path):
    config = {"run": "npm start"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "web"


def test_detect_app_type_cli_from_config(tmp_path):
    config = {"run": "cargo run"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "cli"


def test_detect_app_type_no_run_command(tmp_path):
    assert detect_app_type(tmp_path) == "cli"


def test_detect_app_type_autodetected_npm(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node ."}}')
    assert detect_app_type(tmp_path) == "web"
