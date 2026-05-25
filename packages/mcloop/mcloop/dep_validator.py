"""Pre-flight check that declared project deps are installed in the venv.

mcloop's run_loop calls ``ensure_pytest_optimizations`` which adds
``pytest-xdist`` and ``pytest-timeout`` to the project's pyproject.toml
dev deps. Declaring them does not install them. If the project's
``.venv`` was provisioned before the deps were declared, or by a
run.sh that silently fell back to a non-dev install, later pytest
invocations fail with ``unrecognized arguments: -n`` and burn retries
that cannot succeed (the venv contents do not change between retries).

This module catches that mismatch at startup, before the per-task loop
enters its first task. If any declared dependency is missing in the
project venv, ``MissingDependenciesError`` is raised with a concrete
fix command naming every missing package.

The check uses ``pip show`` rather than ``import``: pip distribution
names can differ from import names (``pytest-xdist`` imports as
``xdist``) and resolving that mapping is brittle. ``pip show`` works
on the distribution name as declared in pyproject.toml.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


class MissingDependenciesError(RuntimeError):
    """Raised when declared project deps are not installed in the venv."""


def _dep_name(requirement: str) -> str:
    """Return the bare package name from a PEP 508 requirement string."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return match.group(1) if match else ""


def _read_declared_dependencies(project_dir: Path) -> list[str] | None:
    """Return the list of declared dependency names, or None on error/no-file.

    Returns ``None`` when ``pyproject.toml`` is absent or malformed (the
    caller no-ops). Returns ``[]`` when the file parses but declares no
    deps.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    project = data.get("project", {})
    deps: list[str] = []
    for r in project.get("dependencies", []) or []:
        name = _dep_name(str(r))
        if name:
            deps.append(name)
    optional = project.get("optional-dependencies", {}) or {}
    for r in optional.get("dev", []) or []:
        name = _dep_name(str(r))
        if name:
            deps.append(name)
    seen: set[str] = set()
    out: list[str] = []
    for n in deps:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _resolve_project_venv_python(project_dir: Path) -> Path | None:
    """Return the venv's python executable path, or None if absent."""
    candidate = project_dir / ".venv" / "bin" / "python"
    if candidate.is_file() or candidate.is_symlink():
        return candidate
    return None


def _pip_show_check(venv_python: Path, name: str) -> bool:
    """Return True when ``name`` is installed in the venv hosting ``venv_python``."""
    pip_path = venv_python.parent / "pip"
    if pip_path.is_file() or pip_path.is_symlink():
        cmd = [str(pip_path), "show", name]
    else:
        cmd = [str(venv_python), "-m", "pip", "show", name]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def validate_project_dependencies(project_dir: Path) -> None:
    """Verify declared project dependencies are installed in the venv.

    No-op when:
      - ``pyproject.toml`` is absent
      - ``pyproject.toml`` is malformed (do not compound an existing
        problem)
      - the project's ``.venv/`` has not been provisioned yet (caller
        relies on ``run.sh`` to provision; we cannot check what does
        not exist)
      - no dependencies are declared

    Raises ``MissingDependenciesError`` when at least one declared dep
    is not installed in the venv. The error message names every
    missing package so the user can fix all in one pass.
    """
    declared = _read_declared_dependencies(project_dir)
    if not declared:
        return
    venv_python = _resolve_project_venv_python(project_dir)
    if venv_python is None:
        return

    missing = [name for name in declared if not _pip_show_check(venv_python, name)]
    if not missing:
        return

    venv_dir = venv_python.parent.parent
    venv_pip = venv_python.parent / "pip"
    raise MissingDependenciesError(
        "Project declares dependencies that are not installed in "
        f"{venv_dir}: {', '.join(missing)}. "
        f"Run `{venv_pip} install -e '.[dev]'` to fix."
    )


__all__ = [
    "MissingDependenciesError",
    "validate_project_dependencies",
]
