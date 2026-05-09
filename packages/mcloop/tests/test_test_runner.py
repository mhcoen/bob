"""Tests for mcloop.test_runner."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.test_runner import (
    NoTestRunnerAvailableError,
    _bare_pytest_available,
    _read_declared_test_command,
    _runsh_test_available,
    _venv_pytest_available,
    is_test_runner_available,
    resolve_test_command,
)

# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _write_pyproject(project_dir: Path, body: str) -> None:
    (project_dir / "pyproject.toml").write_text(body, encoding="utf-8")


def _write_runsh(project_dir: Path, executable: bool = True) -> None:
    run_sh = project_dir / "run.sh"
    run_sh.write_text(
        '#!/bin/bash\nset -euo pipefail\necho "test runner"\n',
        encoding="utf-8",
    )
    if executable:
        run_sh.chmod(0o755)


def _write_venv_pytest(project_dir: Path) -> None:
    venv_bin = project_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    pytest_path = venv_bin / "pytest"
    pytest_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pytest_path.chmod(0o755)


# ---------------------------------------------------------------------
# _read_declared_test_command
# ---------------------------------------------------------------------


def test_read_declared_returns_none_when_no_pyproject(tmp_path: Path) -> None:
    assert _read_declared_test_command(tmp_path) is None


def test_read_declared_returns_none_when_pyproject_malformed(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "not [valid")
    assert _read_declared_test_command(tmp_path) is None


def test_read_declared_returns_none_when_section_absent(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, '[project]\nname = "demo"\nversion = "0.1.0"\n')
    assert _read_declared_test_command(tmp_path) is None


def test_read_declared_returns_value_when_set(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [tool.mcloop]
            test_command = "./run.sh test"
            """
        ),
    )
    assert _read_declared_test_command(tmp_path) == "./run.sh test"


def test_read_declared_strips_whitespace(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [tool.mcloop]
            test_command = "  pytest -v  "
            """
        ),
    )
    assert _read_declared_test_command(tmp_path) == "pytest -v"


def test_read_declared_returns_none_when_value_empty(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [tool.mcloop]
            test_command = ""
            """
        ),
    )
    assert _read_declared_test_command(tmp_path) is None


def test_read_declared_returns_none_when_value_not_string(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [tool.mcloop]
            test_command = ["pytest", "-v"]
            """
        ),
    )
    assert _read_declared_test_command(tmp_path) is None


# ---------------------------------------------------------------------
# _runsh_test_available
# ---------------------------------------------------------------------


def test_runsh_returns_command_when_present_and_executable(tmp_path: Path) -> None:
    _write_runsh(tmp_path, executable=True)
    assert _runsh_test_available(tmp_path) == "./run.sh test"


def test_runsh_returns_none_when_absent(tmp_path: Path) -> None:
    assert _runsh_test_available(tmp_path) is None


def test_runsh_returns_none_when_present_but_not_executable(tmp_path: Path) -> None:
    _write_runsh(tmp_path, executable=False)
    (tmp_path / "run.sh").chmod(0o644)
    assert _runsh_test_available(tmp_path) is None


# ---------------------------------------------------------------------
# _venv_pytest_available
# ---------------------------------------------------------------------


def test_venv_pytest_returns_path_when_present(tmp_path: Path) -> None:
    _write_venv_pytest(tmp_path)
    expected = tmp_path / ".venv" / "bin" / "pytest"
    assert _venv_pytest_available(tmp_path) == str(expected)


def test_venv_pytest_returns_none_when_absent(tmp_path: Path) -> None:
    assert _venv_pytest_available(tmp_path) is None


# ---------------------------------------------------------------------
# _bare_pytest_available
# ---------------------------------------------------------------------


def test_bare_pytest_returns_pytest_when_on_path() -> None:
    with patch("mcloop.test_runner.shutil.which", return_value="/usr/bin/pytest"):
        assert _bare_pytest_available() == "pytest"


def test_bare_pytest_returns_none_when_off_path() -> None:
    with patch("mcloop.test_runner.shutil.which", return_value=None):
        assert _bare_pytest_available() is None


# ---------------------------------------------------------------------
# resolve_test_command — preference order
# ---------------------------------------------------------------------


def test_declared_command_wins_over_runsh(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [tool.mcloop]
            test_command = "make test"
            """
        ),
    )
    _write_runsh(tmp_path)
    _write_venv_pytest(tmp_path)
    assert resolve_test_command(tmp_path) == "make test"


def test_runsh_wins_over_venv_pytest(tmp_path: Path) -> None:
    _write_runsh(tmp_path)
    _write_venv_pytest(tmp_path)
    assert resolve_test_command(tmp_path) == "./run.sh test"


def test_venv_pytest_wins_over_bare_pytest(tmp_path: Path) -> None:
    _write_venv_pytest(tmp_path)
    expected = str(tmp_path / ".venv" / "bin" / "pytest")
    with patch("mcloop.test_runner.shutil.which", return_value="/usr/bin/pytest"):
        assert resolve_test_command(tmp_path) == expected


def test_falls_back_to_bare_pytest(tmp_path: Path) -> None:
    with patch("mcloop.test_runner.shutil.which", return_value="/usr/bin/pytest"):
        assert resolve_test_command(tmp_path) == "pytest"


def test_raises_when_no_fallback_available(tmp_path: Path) -> None:
    with patch("mcloop.test_runner.shutil.which", return_value=None):
        with pytest.raises(NoTestRunnerAvailableError) as ei:
            resolve_test_command(tmp_path)
    msg = str(ei.value)
    assert "test_command" in msg
    assert "run.sh" in msg
    assert ".venv/bin/pytest" in msg
    assert "pytest" in msg


# ---------------------------------------------------------------------
# is_test_runner_available
# ---------------------------------------------------------------------


def test_is_available_true_when_runsh_present(tmp_path: Path) -> None:
    _write_runsh(tmp_path)
    assert is_test_runner_available(tmp_path) is True


def test_is_available_true_when_venv_pytest_present(tmp_path: Path) -> None:
    _write_venv_pytest(tmp_path)
    assert is_test_runner_available(tmp_path) is True


def test_is_available_false_when_nothing_present(tmp_path: Path) -> None:
    with patch("mcloop.test_runner.shutil.which", return_value=None):
        assert is_test_runner_available(tmp_path) is False


# ---------------------------------------------------------------------
# Integration: resolve_test_command via mcloop.checks
# ---------------------------------------------------------------------


def test_checks_uses_resolved_test_command(tmp_path: Path) -> None:
    """Integration: when [tool.mcloop].test_command is declared, the
    detected check command list contains that exact command, not the
    legacy bare 'pytest'."""
    from mcloop.checks import get_check_commands

    # The detection rule fires only when "pytest" appears in the
    # pyproject text. So include both [tool.mcloop] and a stub
    # [tool.pytest.ini_options] section.
    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"

            [tool.mcloop]
            test_command = "./run.sh test"

            [tool.pytest.ini_options]
            addopts = ""
            """
        ),
    )
    _write_runsh(tmp_path)
    commands = get_check_commands(tmp_path)
    assert "./run.sh test" in commands
    assert "pytest" not in commands


def test_checks_falls_back_to_venv_pytest(tmp_path: Path) -> None:
    """Backward compatibility: a project with no [tool.mcloop] but a
    .venv/bin/pytest gets the venv's pytest, not bare pytest."""
    from mcloop.checks import get_check_commands

    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"

            [tool.pytest.ini_options]
            addopts = ""
            """
        ),
    )
    _write_venv_pytest(tmp_path)
    commands = get_check_commands(tmp_path)
    venv_pytest_path = str(tmp_path / ".venv" / "bin" / "pytest")
    assert venv_pytest_path in commands
    assert "pytest" not in commands


def test_checks_legacy_fallback_when_no_runner_resolves(tmp_path: Path) -> None:
    """Backward compatibility: even when nothing resolves cleanly,
    the legacy bare-pytest entry is appended so subsequent invocation
    surfaces a clear command-not-found rather than silently dropping
    the test step."""
    from mcloop.checks import get_check_commands

    _write_pyproject(
        tmp_path,
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"

            [tool.pytest.ini_options]
            addopts = ""
            """
        ),
    )
    with patch("mcloop.test_runner.shutil.which", return_value=None):
        commands = get_check_commands(tmp_path)
    assert "pytest" in commands
