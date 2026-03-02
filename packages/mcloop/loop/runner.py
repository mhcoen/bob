"""Run AI CLI subprocesses and capture output."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RunResult:
    success: bool
    output: str
    exit_code: int
    log_path: Path


def run_task(
    task_text: str,
    cli: str,
    project_dir: str | Path,
    log_dir: str | Path,
) -> RunResult:
    """Launch a CLI session to perform a task. Returns RunResult."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = _build_command(cli, task_text)
    env_extra = {"LOOP_ASK": "1"}

    result = subprocess.run(
        cmd,
        cwd=project_dir,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), **env_extra},
    )

    output = result.stdout + result.stderr
    log_path = _write_log(log_dir, task_text, cmd, output, result.returncode)

    return RunResult(
        success=result.returncode == 0,
        output=output,
        exit_code=result.returncode,
        log_path=log_path,
    )


def _build_command(cli: str, task_text: str) -> list[str]:
    if cli == "claude":
        return ["claude", "-p", task_text, "--allowedTools", "Edit,Write,Bash,Read,Glob,Grep"]
    elif cli == "codex":
        return ["codex", "-q", task_text]
    else:
        raise ValueError(f"Unknown CLI: {cli}")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


def _write_log(log_dir: Path, task_text: str, cmd: list[str], output: str, exit_code: int) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(task_text)
    log_path = log_dir / f"{timestamp}_{slug}.log"
    log_path.write_text(
        f"Task: {task_text}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Exit code: {exit_code}\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
    return log_path
