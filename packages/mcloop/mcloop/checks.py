"""Run a project's test/lint suite and report results."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    passed: bool
    output: str
    command: str


def _load_config_commands(project_dir: Path) -> list[str] | None:
    """Return checks from mcloop.json if present, else None."""
    config = project_dir / "mcloop.json"
    if not config.exists():
        return None
    try:
        data = json.loads(config.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    checks = data.get("checks")
    if isinstance(checks, list):
        return [str(c) for c in checks]
    return None


def run_checks(project_dir: str | Path) -> CheckResult:
    """Auto-detect and run the project's checks. Returns a CheckResult."""
    project_dir = Path(project_dir)
    commands = _load_config_commands(project_dir)
    if commands is None:
        commands = _detect_commands(project_dir)

    if not commands:
        return CheckResult(passed=True, output="No check commands detected", command="(none)")

    all_output: list[str] = []
    for cmd in commands:
        try:
            result = subprocess.run(
                shlex.split(cmd),
                shell=False,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            all_output.append(f"$ {cmd}\nTIMEOUT after 300s")
            return CheckResult(passed=False, output="\n".join(all_output), command=cmd)
        all_output.append(f"$ {cmd}\n{result.stdout}{result.stderr}")
        if result.returncode != 0:
            return CheckResult(
                passed=False,
                output="\n".join(all_output),
                command=cmd,
            )

    return CheckResult(
        passed=True,
        output="\n".join(all_output),
        command=" && ".join(commands),
    )


def _detect_commands(project_dir: Path) -> list[str]:
    """Detect which check commands to run based on project files."""
    commands: list[str] = []

    if (project_dir / "pyproject.toml").exists():
        toml_text = (project_dir / "pyproject.toml").read_text()
        if "ruff" in toml_text:
            commands.append("ruff check .")
        if "pytest" in toml_text:
            commands.append("pytest")

    if (project_dir / "package.json").exists():
        pkg = (project_dir / "package.json").read_text()
        if '"test"' in pkg:
            commands.append("npm test")

    if (project_dir / "Package.swift").exists():
        commands.append("swift build")

    if (project_dir / "Makefile").exists():
        commands.append("make check")

    return commands
