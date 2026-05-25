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
import re
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


# Recognize an explicit test-subcommand branch in run.sh. We intentionally
# reject "run.sh exists and is executable" as sufficient: many run.sh
# scripts are generic argument forwarders (``"$PYTHON" -m <pkg> "$@"``)
# that pass "test" through to the project's CLI, which usually doesn't
# know what "test" means and exits non-zero. mcloop's check phase then
# burns three retries deterministically.
#
# Two recognized shapes (whichever appears non-comment in the script):
#
#   1. if/elif comparing $1 (or ${1:-}, etc.) to the literal "test":
#         if [[ "${1:-}" == "test" ]]
#         if [ "$1" = "test" ]
#         elif [[ "$1" = test ]]
#
#   2. case arm starting with ``test)`` at the start of a line:
#         case "${1:-}" in
#             test)
#                 ...
#                 ;;
#         esac
#
# Anything in a comment line (``#``-prefixed after stripping leading
# whitespace) is excluded so usage strings like ``# ./run.sh test ...``
# do not pose as support.
_RUNSH_TEST_IF_RE = re.compile(
    r"""
    \b(?:if|elif)\b      # if or elif keyword
    \s+
    \[\[?                # [ or [[
    \s*
    "?                   # optional opening quote
    \$\{?1[^"}\s]*\}?    # $1 or ${1...}
    "?                   # optional closing quote
    \s*
    (?:==|=)             # equality
    \s*
    "?                   # optional opening quote on rhs
    test
    "?                   # optional closing quote on rhs
    \b
    """,
    re.VERBOSE,
)

_RUNSH_TEST_CASE_ARM_RE = re.compile(r"^\s*test\)", re.MULTILINE)


def _strip_shell_comment_lines(text: str) -> str:
    """Drop whole-line shell comments. Inline comments after a command
    on the same line are uncommon and rarely contain test-arm patterns;
    we leave them in place rather than parse shell quoting properly."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _runsh_has_test_branch(text: str) -> bool:
    """Return True iff ``text`` (the body of run.sh) has a recognizable
    executable test-subcommand branch (if/elif on $1 == "test", or a
    case arm starting with ``test)``)."""
    code = _strip_shell_comment_lines(text)
    if _RUNSH_TEST_IF_RE.search(code):
        return True
    if _RUNSH_TEST_CASE_ARM_RE.search(code):
        return True
    return False


def _runsh_test_available(project_dir: Path) -> str | None:
    """Return ``"./run.sh test"`` only if run.sh has a real test branch.

    Existence + executable bit is necessary but NOT sufficient. The
    function inspects the script's body for an explicit
    test-subcommand handling branch (see ``_RUNSH_TEST_IF_RE`` and
    ``_RUNSH_TEST_CASE_ARM_RE``). Generic argument forwarders like::

        "$PYTHON" -m <pkg> "$@"

    return ``None`` so the resolver falls through to
    ``.venv/bin/pytest``.
    """
    run_sh = project_dir / "run.sh"
    if not (run_sh.is_file() and os.access(run_sh, os.X_OK)):
        return None
    try:
        text = run_sh.read_text(encoding="utf-8")
    except OSError:
        return None
    if _runsh_has_test_branch(text):
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
