"""Platform knowledge: macOS + Python CLI + pyproject.toml.

Operational knowledge for Python CLI tools built with pyproject.toml
on macOS.  Covers virtual environment creation, editable installs,
entry points, and the common ways Claude Code gets these wrong.

Registered at import time via :func:`~duplo.platforms.schema.register`.
"""

from __future__ import annotations

from duplo.platforms.schema import PlatformProfile, ScaffoldFile, register

# ---------------------------------------------------------------------------
# run.sh template
# ---------------------------------------------------------------------------

_RUN_SH = """\
#!/bin/bash
# Create venv (if missing), install in editable mode with dev deps,
# and run.
# Usage:
#   ./run.sh [args...]      Forward args to the CLI entry point.
#   ./run.sh test [args...] Run the project's test suite via
#                           .venv/bin/pytest. Extra args (filters,
#                           -k, -x, etc.) are forwarded to pytest.
#
# This script is the ONLY way to run the project. It guarantees:
#   1. A venv exists at .venv/
#   2. The package + every declared dev dep is installed in editable
#      mode (pip install -e '.[dev]')
#   3. The CLI entry point is available
#   4. `./run.sh test` is recognized as the test-runner subcommand
#      (mcloop's BC3 test_runner resolver picks this up).

set -euo pipefail

VENV_DIR=".venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
PYTEST="$VENV_DIR/bin/pytest"

# Create venv if it does not exist.
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Editable install with dev deps. Fail loudly if anything is wrong:
# previously this fell back to `pip install -e .` on dev-install
# failure and suppressed stderr, which silently shipped a venv
# missing pytest-xdist / pytest-timeout / ruff. The downstream
# pytest invocation then failed with `unrecognized arguments: -n`
# and burned retries that could not fix it.
"$PIP" install -e ".[dev]" --quiet

# Test subcommand: dispatch to pytest with any forwarded args.
# Anchored at $1 == "test" (literal subcommand) so a CLI flag
# that happens to contain "test" still falls through to the
# regular forwarder. Without this branch, ./run.sh test would
# forward "test" to the package's __main__, which is an
# argument the CLI doesn't understand; mcloop's check phase
# treats that exit code as a test failure and retries
# pointlessly.
if [[ "${1:-}" == "test" ]]; then
    shift
    exec "$PYTEST" "$@"
fi

# Run via python -m so it works even if entry point scripts
# have not been regenerated yet. The argument is the Python
# identifier form of the project name (hyphens replaced with
# underscores), matching the on-disk package directory.
exec "$PYTHON" -m {package_name} "$@"
"""

# ---------------------------------------------------------------------------
# pyproject.toml template
# ---------------------------------------------------------------------------

_PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}"
version = "0.1.0"
description = ""
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-xdist>=3",
    "pytest-timeout>=2",
    "pytest-randomly>=3",
    "ruff>=0.5",
]

[project.scripts]
{project_name} = "{package_name}.__main__:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["{package_name}*"]

[tool.pytest.ini_options]
addopts = "-n auto"
timeout = 60

[tool.ruff]
target-version = "py311"
line-length = 99
"""

# ---------------------------------------------------------------------------
# Package directory contents
#
# Scaffold creates the package directory with the underscore-form
# Python identifier name ({package_name}). It is mandatory that
# this directory exists with an importable __init__.py and a
# python -m-runnable __main__.py before run.sh's `pip install -e
# ".[dev]"` invocation, otherwise the editable install fails to
# locate the package.
# ---------------------------------------------------------------------------

_PKG_INIT_PY = '"""{project_name} package."""\n'

_PKG_MAIN_PY = """\
\"\"\"Entry point for ``python -m {package_name}``.\"\"\"

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    \"\"\"Console-script entry point for {project_name}.

    Phase 0 scaffold leaves this as a no-op that prints a usage
    line on --help and exits non-zero on any other invocation. The
    canonical-mode plan replaces this body with the project's
    actual CLI.
    \"\"\"
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--help", "-h"):
        print("{project_name} (scaffolded; not yet implemented)")
        return 0
    print(
        "{project_name}: this entry point is a scaffold stub. "
        "Replace mcloop's first phase task body with the real CLI.",
        file=sys.stderr,
    )
    return 2


if __name__ == \"__main__\":
    raise SystemExit(main())
"""


# ---------------------------------------------------------------------------
# .gitignore entries
# ---------------------------------------------------------------------------

_GITIGNORE_ENTRIES = [
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.egg-info/",
    "dist/",
    "build/",
    ".eggs/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".DS_Store",
]

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

_PROFILE = PlatformProfile(
    id="macos-python-cli",
    display_name="macOS + Python CLI + pyproject.toml",
    match_platform=["desktop", "macos", "cli"],
    match_language=["python"],
    match_any_preference=[
        "pyproject",
        "pyproject.toml",
        "cli",
        "command line",
    ],
    planner_rules=[
        (
            "Every task that runs the CLI MUST use ./run.sh, never "
            "python <file>.py or the bare entry point name. run.sh "
            "guarantees the venv exists and the package is installed."
        ),
        (
            "Phase 0 scaffold MUST NOT recreate run.sh, pyproject.toml, "
            ".gitignore, the package directory, or its __init__.py / "
            "__main__.py -- these are all pre-generated by duplo. Phase 0 "
            "tasks should USE the existing scaffold (./run.sh --help to "
            "verify the entry point works) and BUILD ON TOP of the stub "
            "__main__.py, replacing its body with the real CLI."
        ),
        (
            "The package directory uses the Python-identifier form of "
            "the project name (hyphens replaced with underscores). Plan "
            "tasks that import the package MUST use the underscore form, "
            "e.g. `from fswatch_run_smoke.cli import main`, never "
            '`importlib.import_module("fswatch-run-smoke.cli")`.'
        ),
        (
            "Always use pyproject.toml for project metadata, not "
            "setup.py or setup.cfg. Declare the CLI entry point "
            "under [project.scripts]."
        ),
        (
            "Test tasks MUST run via .venv/bin/pytest (or "
            "./run.sh -m pytest), never bare pytest, to ensure "
            "the correct venv is active."
        ),
        (
            "Do not generate tasks that create a venv or pip install "
            "manually. run.sh handles this. Tasks should assume "
            "run.sh has been executed at least once."
        ),
        (
            "When adding dependencies, add them to pyproject.toml "
            "[project.dependencies], not requirements.txt. Then "
            "re-run ./run.sh to pick them up."
        ),
    ],
    claude_md_rules=[
        (
            "NEVER run python <file>.py directly or call the CLI "
            "entry point by name outside the venv. Always use "
            "./run.sh which creates the venv, installs the package "
            "in editable mode, and runs via python -m."
        ),
        ("Do NOT recreate or overwrite run.sh or .gitignore -- these are managed by duplo."),
        (
            "Do NOT create a venv manually, do NOT run pip install "
            "manually. run.sh handles all of this. If you need a "
            "new dependency, add it to pyproject.toml and re-run "
            "./run.sh."
        ),
        (
            "Use pyproject.toml for ALL project configuration. "
            "Do not create setup.py, setup.cfg, or requirements.txt."
        ),
        ("Run tests via .venv/bin/pytest, never bare pytest."),
        (
            "The package MUST have a __main__.py so that "
            "python -m <package> works. This is what run.sh calls."
        ),
    ],
    scaffold_files=[
        ScaffoldFile(
            path="run.sh",
            content=_RUN_SH,
            executable=True,
        ),
        ScaffoldFile(
            path="pyproject.toml",
            content=_PYPROJECT_TOML,
        ),
        ScaffoldFile(
            path="{package_name}/__init__.py",
            content=_PKG_INIT_PY,
        ),
        ScaffoldFile(
            path="{package_name}/__main__.py",
            content=_PKG_MAIN_PY,
        ),
    ],
    prerequisites=[
        "Python 3.11+ (via Homebrew or system python3)",
        "pip (ships with Python 3.11+)",
    ],
    failure_modes=[
        (
            "Running python <file>.py without activating the venv: "
            "ImportError on every third-party dependency."
        ),
        (
            "Creating requirements.txt instead of declaring dependencies "
            "in pyproject.toml: makes editable install incomplete."
        ),
        (
            "Forgetting __main__.py: python -m <package> fails with "
            "'No module named <package>.__main__'."
        ),
        (
            "Running bare pytest outside the venv: picks up system "
            "pytest (if any) which cannot see project dependencies."
        ),
        (
            "Using setup.py alongside pyproject.toml: build system "
            "confusion, editable install may silently use the wrong one."
        ),
        (
            "Hardcoding python3.11 or python3.12 in scripts instead "
            "of using .venv/bin/python: breaks when Python minor "
            "version changes."
        ),
    ],
    bootstrap_steps=[
        "python3 -m venv .venv",
        ".venv/bin/pip install -e '.[dev]'",
    ],
    gitignore_entries=_GITIGNORE_ENTRIES,
)

register(_PROFILE)
