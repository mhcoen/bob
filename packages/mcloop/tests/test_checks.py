"""Tests for loop.checks."""

import json
import subprocess
from unittest.mock import patch

from mcloop.checks import (
    _classify_run_command,
    _detect_commands,
    _load_config,
    _normalize_pytest,
    detect_app_type,
    get_check_commands,
    run_autofix,
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


def test_detect_commands_mypy_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = false\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds


def test_detect_commands_mypy_from_mypy_ini(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = False\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds


def test_detect_commands_mypy_absent(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." not in cmds


def test_detect_commands_mypy_with_ruff_and_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n[tool.mypy]\nstrict = false\n"
    )
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" in cmds
    assert "mypy ." in cmds


def test_detect_commands_mypy_ini_without_pyproject(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds
    # No pyproject.toml means no ruff/pytest detection
    assert "ruff check ." not in cmds
    assert "pytest" not in cmds


def test_detect_commands_mypy_not_double_added(tmp_path):
    """Both [tool.mypy] and mypy.ini present should still only add one 'mypy .'."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
    (tmp_path / "mypy.ini").write_text("[mypy]\n")
    cmds = _detect_commands(tmp_path, {})
    assert cmds.count("mypy .") == 1


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


# --- _normalize_pytest tests ---


def test_normalize_pytest_python_m_pytest():
    assert _normalize_pytest("python -m pytest") == "pytest"


def test_normalize_pytest_python3_m_pytest():
    assert _normalize_pytest("python3 -m pytest") == "pytest"


def test_normalize_pytest_python_m_pytest_with_args():
    assert _normalize_pytest("python -m pytest tests/ -x -q") == "pytest tests/ -x -q"


def test_normalize_pytest_venv_bin():
    assert _normalize_pytest(".venv/bin/pytest") == "pytest"


def test_normalize_pytest_venv_bin_with_args():
    assert _normalize_pytest(".venv/bin/pytest tests/ -v") == "pytest tests/ -v"


def test_normalize_pytest_already_normalized():
    assert _normalize_pytest("pytest") == "pytest"


def test_normalize_pytest_non_pytest():
    assert _normalize_pytest("ruff check .") == "ruff check ."


def test_get_check_commands_normalizes_python_m_pytest(tmp_path):
    data = {"checks": ["ruff check .", "python -m pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert get_check_commands(tmp_path) == ["ruff check .", "pytest"]


@patch("mcloop.checks.subprocess.run")
def test_run_checks_falls_back_when_check_timeout_is_non_integer(mock_run, tmp_path):
    """A non-integer check_timeout in mcloop.json must not crash run_checks.
    The default timeout (300s) is used instead.
    """
    data = {"checks": ["echo ok"], "check_timeout": "not a number"}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    mock_run.return_value = subprocess.CompletedProcess(
        args="", returncode=0, stdout="ok\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed is True
    # Confirm the default timeout reached subprocess.run.
    assert mock_run.call_args.kwargs["timeout"] == 300


@patch("mcloop.checks.subprocess.run")
def test_run_checks_falls_back_to_autodetect_when_no_config(mock_run, tmp_path):
    # No mcloop.json present; pyproject.toml should trigger auto-detection
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=0, stdout="All good\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed
    # run_checks is now side-effect-free: ruff check + ruff format --check, no autofix
    assert mock_run.call_count == 2
    # Both commands run when all pass; assert as a set (order asserted
    # separately by test_run_checks_runs_commands_in_order_until_failure).
    calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
    assert ("ruff", "check", ".") in calls
    assert ("ruff", "format", "--check", ".") in calls


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


def test_run_checks_resolves_bare_config_command_to_project_venv(tmp_path, monkeypatch):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    pytest_path = venv_bin / "pytest"
    pytest_path.write_text("#!/bin/sh\nprintf 'venv pytest\\n'\n")
    pytest_path.chmod(0o755)
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["pytest -q"]}))
    monkeypatch.setenv("PATH", "/nonexistent")

    result = run_checks(tmp_path)

    assert result.passed
    assert "venv pytest" in result.output


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
    """An earlier failing command short-circuits later ones.

    Configured order is: ruff check . -> ruff format --check . -> pytest.
    The first command fails; run_checks must not invoke the later
    commands at all (sequential fail-fast acceptance-gate contract).
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")

    invoked: list[tuple[str, ...]] = []

    def record(args, **kwargs):
        invoked.append(tuple(args))
        # First command (ruff check .) fails.
        if tuple(args[:2]) == ("ruff", "check"):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="E501\n")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    mock_run.side_effect = record
    result = run_checks(tmp_path)

    assert not result.passed
    assert result.command == "ruff check ."
    # Short-circuit means only the first command was ever invoked.
    assert invoked == [("ruff", "check", ".")]
    assert not any(a[:1] == ("pytest",) for a in invoked)


# --- _classify_run_command tests ---


def test_run_checks_runs_commands_in_order_until_failure(tmp_path):
    """Commands run in listed order; a failure stops the rest.

    run_checks is an acceptance gate: once a command fails, no later
    command may start (no later side effects). This replaces an older
    test that asserted concurrent launch -- an optimization stated as
    a semantic requirement, incompatible with fail-fast side-effect
    isolation. The contract under test here is order + short-circuit,
    not the execution strategy.
    """
    (tmp_path / "mcloop.json").write_text(
        json.dumps({"checks": ["cmd-one", "cmd-two", "cmd-three"]})
    )

    # All commands pass, so every command runs in listed order.
    order: list[str] = []

    def all_pass(args, **kwargs):
        order.append(args[0])
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    with patch("mcloop.checks.subprocess.run", side_effect=all_pass):
        result = run_checks(tmp_path)
    assert result.passed
    assert order == ["cmd-one", "cmd-two", "cmd-three"]

    # When cmd-two fails, cmd-one and cmd-two run and cmd-three never starts.
    order.clear()

    def fail_second(args, **kwargs):
        order.append(args[0])
        rc = 1 if args[0] == "cmd-two" else 0
        return subprocess.CompletedProcess(args=args, returncode=rc, stdout="", stderr="")

    with patch("mcloop.checks.subprocess.run", side_effect=fail_second):
        result = run_checks(tmp_path)
    assert not result.passed
    assert result.command == "cmd-two"
    assert order == ["cmd-one", "cmd-two"]
    assert "cmd-three" not in order


@patch("mcloop.checks.subprocess.run")
def test_run_autofix_calls_ruff_fix_and_format(mock_run, tmp_path):
    """run_autofix runs both ruff check --fix and ruff format."""
    ok = subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr="")
    mock_run.return_value = ok
    run_autofix(tmp_path)
    assert mock_run.call_count == 2
    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert cmds[0] == ["ruff", "check", "--fix", "."]
    assert cmds[1] == ["ruff", "format", "."]


def test_try_salvage_does_not_extend_noqa_like_comment(tmp_path):
    """A comment that contains "noqa" as a substring (e.g. "noqa-like
    workaround") must not be misclassified as an existing # noqa pragma
    and corrupted. The fresh pragma must be appended, not spliced into
    the existing comment.
    """
    from mcloop.checks import try_salvage_style_failures

    src = tmp_path / "mod.py"
    long_value = "a" * 70
    comment = "# This is a noqa-like workaround"
    line = f"x = '{long_value}'  {comment}\n"
    src.write_text(line)
    failure_output = "mod.py:1:100: E501 Line too long (130 > 100)\n"

    salvaged, patched = try_salvage_style_failures(tmp_path, failure_output)

    assert salvaged is True
    assert patched == ["mod.py"]
    new_line = src.read_text()
    # Original "noqa-like" must remain intact in the comment.
    assert "noqa-like workaround" in new_line
    # A fresh E501 noqa pragma must have been appended.
    assert "noqa: E501" in new_line


@patch("mcloop.checks.subprocess.run")
def test_run_autofix_handles_missing_ruff(mock_run, tmp_path):
    """run_autofix must not crash if ruff is not installed (FileNotFoundError)."""
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory: 'ruff'")
    # Should not raise.
    run_autofix(tmp_path)
    # Both autofix commands attempted.
    assert mock_run.call_count == 2


@patch("mcloop.checks.subprocess.run")
def test_run_checks_no_autofix_side_effects(mock_run, tmp_path):
    """run_checks does not invoke ruff --fix or ruff format."""
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="", returncode=0, stdout="ok\n", stderr=""
    )
    run_checks(tmp_path)
    # Only the gate commands, no autofix
    cmds = [call[0][0] for call in mock_run.call_args_list]
    for cmd in cmds:
        assert cmd != ["ruff", "check", "--fix", "."]
        assert cmd != ["ruff", "format", "."]


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
