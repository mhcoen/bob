"""Tests for mcloop.targeted — source-to-test file mapping."""

import subprocess
from unittest.mock import patch

from mcloop.targeted import (
    is_test_command,
    map_to_tests,
    targeted_pytest_command,
)


def test_map_basic(tmp_path):
    """Source file maps to test file by naming convention."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    result = map_to_tests(["mcloop/checks.py"], tmp_path)
    assert result == ["tests/test_checks.py"]


def test_map_multiple(tmp_path):
    """Multiple source files map to their test files."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    (tmp_path / "tests" / "test_runner.py").write_text("")
    result = map_to_tests(
        ["mcloop/checks.py", "mcloop/runner.py"],
        tmp_path,
    )
    assert result == ["tests/test_checks.py", "tests/test_runner.py"]


def test_map_no_matching_test(tmp_path):
    """Source file with no corresponding test returns empty."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["mcloop/main.py"], tmp_path)
    assert result == []


def test_map_skips_non_python(tmp_path):
    """Non-Python files are ignored."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["README.md", "mcloop.json"], tmp_path)
    assert result == []


def test_map_skips_test_files(tmp_path):
    """Test files themselves are not re-mapped."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_test_foo.py").write_text("")
    result = map_to_tests(["tests/test_foo.py"], tmp_path)
    assert result == []


def test_map_skips_dunder_files(tmp_path):
    """__init__.py and similar are skipped."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["mcloop/__init__.py"], tmp_path)
    assert result == []


def test_map_deduplicates(tmp_path):
    """Same test file from different source paths appears once."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    result = map_to_tests(
        ["mcloop/checks.py", "src/checks.py"],
        tmp_path,
    )
    assert result == ["tests/test_checks.py"]


def test_map_no_tests_dir(tmp_path):
    """Missing tests/ directory returns empty."""
    result = map_to_tests(["mcloop/checks.py"], tmp_path)
    assert result == []


def test_targeted_pytest_command():
    cmd = targeted_pytest_command(["tests/test_checks.py"])
    assert cmd == "pytest tests/test_checks.py"


def test_targeted_pytest_command_multiple():
    cmd = targeted_pytest_command(
        ["tests/test_checks.py", "tests/test_runner.py"],
    )
    assert cmd == "pytest tests/test_checks.py tests/test_runner.py"


def test_is_test_command():
    assert is_test_command("pytest")
    assert is_test_command("pytest tests/test_foo.py")
    assert not is_test_command("ruff check .")
    assert not is_test_command("npm test")
    assert not is_test_command("make check")


def test_run_checks_with_targeted(tmp_path):
    """run_checks narrows pytest to targeted test files."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(
            tmp_path,
            changed_files=["mcloop/checks.py"],
        )
        # Should have run ruff and targeted pytest
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0] == ["ruff", "check", "."]
        assert calls[1] == ["pytest", "tests/test_checks.py"]


def test_run_checks_targeted_no_matching_tests(tmp_path):
    """When no test files match, test command is skipped."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(
            tmp_path,
            changed_files=["mcloop/main.py"],
        )
        # Should only run ruff (pytest skipped — no matching tests)
        assert mock_run.call_count == 1
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd == ["ruff", "check", "."]


def test_run_checks_no_changed_files_runs_full(tmp_path):
    """Without changed_files, full test suite runs."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(tmp_path)
        # Should run both ruff and pytest (full)
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0] == ["ruff", "check", "."]
        assert calls[1] == ["pytest"]
