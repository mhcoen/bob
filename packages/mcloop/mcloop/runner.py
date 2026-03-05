"""Run AI CLI subprocesses and capture output."""

from __future__ import annotations

import json as _json
import os
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
    description: str = "",
    task_label: str = "",
    model: str | None = None,
    prior_errors: str = "",
) -> RunResult:
    """Launch a CLI session to perform a task. Returns RunResult."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    parts.append(f"Task: {task_text}")
    parts.append("Write unit tests where they make sense.")
    parts.append(
        "Do not chain shell commands with && or ;."
        " Use separate Bash calls instead."
    )
    if prior_errors:
        parts.append(
            "IMPORTANT: A previous attempt at this task"
            " failed. Fix these errors:\n"
            + prior_errors
        )
    prompt = "\n\n".join(parts)
    cmd = _build_command(cli, prompt, model=model)
    env = dict(os.environ)
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    process = subprocess.Popen(
        cmd,
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_lines.append(line)
        _print_stream_event(line)
    process.wait()

    output = "".join(output_lines)
    log_path = _write_log(log_dir, task_text, cmd, output, process.returncode)

    return RunResult(
        success=process.returncode == 0,
        output=output,
        exit_code=process.returncode,
        log_path=log_path,
    )


def _build_command(cli: str, task_text: str, model: str | None = None) -> list[str]:
    if cli == "claude":
        cmd = [
            "claude", "-p", task_text,
            "--allowedTools", "Edit,Write,Bash,Read,Glob,Grep",
            "--permission-mode", "default",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd
    elif cli == "codex":
        return ["codex", "-q", task_text]
    else:
        raise ValueError(f"Unknown CLI: {cli}")


def _print_stream_event(line: str) -> None:
    """Parse a stream-json line and print relevant info."""
    line = line.strip()
    if not line:
        return
    try:
        event = _json.loads(line)
    except _json.JSONDecodeError:
        print(line, flush=True)
        return

    etype = event.get("type", "")

    # Streaming text tokens
    if etype == "stream_event":
        delta = event.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            print(delta.get("text", ""), end="", flush=True)
        return

    # Tool use summary
    if etype == "assistant" and "message" in event:
        for block in event["message"].get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                tool_input = block.get("input", {})
                if name == "Bash":
                    print(f"\n>>> Bash: {tool_input.get('command', '')[:120]}", flush=True)
                elif name in ("Write", "Edit"):
                    print(f"\n>>> {name}: {tool_input.get('file_path', '')}", flush=True)
                elif name == "Read":
                    print(f"\n>>> Read: {tool_input.get('file_path', '')}", flush=True)
                else:
                    print(f"\n>>> {name}", flush=True)

    # Tool results
    if etype == "result":
        result = event.get("result", "")
        if isinstance(result, str) and result:
            print(f"\n{result[:200]}", flush=True)


def gather_sync_context(project_dir: Path) -> dict[str, str]:
    """Collect PLAN.md, README.md, CLAUDE.md, git log, file tree, and source files."""
    context: dict[str, str] = {}

    for name in ("PLAN.md", "README.md", "CLAUDE.md"):
        path = project_dir / name
        if path.exists():
            context[name] = path.read_text()

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-30"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            context["git_log"] = result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            context["file_tree"] = result.stdout.strip()
    except Exception:
        pass

    for path in sorted(project_dir.rglob("*.py")):
        if ".git" not in path.parts:
            rel = str(path.relative_to(project_dir))
            try:
                context[rel] = path.read_text()
            except Exception:
                pass

    return context


def build_sync_prompt(context: dict[str, str]) -> str:
    """Build the prompt for the sync Claude session."""
    parts = []
    for name, content in context.items():
        parts.append(f"=== {name} ===\n{content}")
    context_block = "\n\n".join(parts)

    instructions = (
        "You are synchronizing PLAN.md with the actual codebase.\n\n"
        "Your task: identify features, fixes, or changes that are reflected in the "
        "code (or git history) but are not yet documented in PLAN.md, then append "
        "them as checked items.\n\n"
        "Rules:\n"
        "1. APPEND ONLY. Never modify, reorder, or delete any existing items.\n"
        "2. New items must be checked: - [x]\n"
        "3. Match the granularity of existing items — keep new entries at the same "
        "level of detail as surrounding items.\n"
        "4. Only add items for changes that are clearly implemented.\n"
        "5. Do not duplicate existing items, even if worded differently.\n"
        "6. Add new items at the end of the most relevant section, or at the end of "
        "PLAN.md if no section fits.\n\n"
        "Project context follows.\n\n"
    )
    return instructions + context_block


def run_sync(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session with full project context for sync analysis."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    context = gather_sync_context(project_dir)
    prompt = build_sync_prompt(context)
    cmd = _build_command("claude", prompt, model=model)

    process = subprocess.Popen(
        cmd,
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_lines.append(line)
        _print_stream_event(line)
    process.wait()

    output = "".join(output_lines)
    log_path = _write_log(log_dir, "sync", cmd, output, process.returncode)

    return RunResult(
        success=process.returncode == 0,
        output=output,
        exit_code=process.returncode,
        log_path=log_path,
    )


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
