"""Resolve the test command for a project.

mcloop's check phase historically appended a bare ``pytest`` to its
detected-commands list whenever a project's pyproject.toml mentioned
pytest. That couples mcloop to a specific assumption about where
pytest lives (PATH? an unactivated venv? the system's homebrew
install?). Projects that scaffold their own venv via run.sh, or that
put their tests under a custom command, broke against the assumption.

This module abstracts the lookup. Resolution order:

  1. ``[tool.mcloop].test_command`` in the project's pyproject.toml.
     Trusted verbatim — the project owner knows their own runner.
  2. ``./run.sh test`` if ``run.sh`` exists and is executable.
  3. ``.venv/bin/pytest`` if the file exists.
  4. Bare ``pytest`` if it resolves on ``PATH``.

The first match wins. ``NoTestRunnerAvailableError`` is raised when
no fallback works, naming each option that was tried so the user can
fix any of them.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path


class NoTestRunnerAvailableError(RuntimeError):
    """Raised when the resolver finds no usable test command."""


_PYPROJECT_TEST_COMMAND_KEY = ("tool", "mcloop", "test_command")


def _read_declared_test_command(project_dir: Path) -> str | None:
    """Return ``[tool.mcloop].test_command`` from pyproject.toml.

    Returns ``None`` when:
      - pyproject.toml is absent
      - pyproject.toml is malformed
      - the [tool.mcloop] section or test_command key is absent
      - the value is not a non-empty string
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    cursor: object = data
    for key in _PYPROJECT_TEST_COMMAND_KEY:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
        if cursor is None:
            return None
    if isinstance(cursor, str) and cursor.strip():
        return cursor.strip()
    return None


def _runsh_test_available(project_dir: Path) -> str | None:
    """Return ``"./run.sh test"`` if run.sh exists and is executable."""
    run_sh = project_dir / "run.sh"
    if run_sh.is_file() and os.access(run_sh, os.X_OK):
        return "./run.sh test"
    return None


def _venv_pytest_available(project_dir: Path) -> str | None:
    """Return ``.venv/bin/pytest`` path string if present."""
    venv_pytest = project_dir / ".venv" / "bin" / "pytest"
    if venv_pytest.is_file():
        return str(venv_pytest)
    return None


def _bare_pytest_available() -> str | None:
    """Return ``"pytest"`` if resolvable on PATH."""
    if shutil.which("pytest") is not None:
        return "pytest"
    return None


def resolve_test_command(project_dir: Path) -> str:
    """Resolve the test command for ``project_dir``.

    The fallback chain is documented in the module docstring. The
    first match wins. Raises ``NoTestRunnerAvailableError`` when none
    of the four fallbacks works.

    Note: the declared ``[tool.mcloop].test_command`` is trusted
    verbatim. The resolver does not validate the declared command's
    first token against PATH; the project owner is responsible for
    declaring something runnable.
    """
    declared = _read_declared_test_command(project_dir)
    if declared is not None:
        return declared
    runsh = _runsh_test_available(project_dir)
    if runsh is not None:
        return runsh
    venv = _venv_pytest_available(project_dir)
    if venv is not None:
        return venv
    bare = _bare_pytest_available()
    if bare is not None:
        return bare
    raise NoTestRunnerAvailableError(
        "No test runner found for "
        f"{project_dir}. Tried [tool.mcloop].test_command in "
        "pyproject.toml, ./run.sh test, .venv/bin/pytest, and bare "
        "'pytest' on PATH; none of them resolved. Set one of these "
        "before re-running mcloop."
    )


def is_test_runner_available(project_dir: Path) -> bool:
    """Return True iff ``resolve_test_command`` would not raise."""
    try:
        resolve_test_command(project_dir)
    except NoTestRunnerAvailableError:
        return False
    return True


__all__ = [
    "NoTestRunnerAvailableError",
    "is_test_runner_available",
    "resolve_test_command",
]
