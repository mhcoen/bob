"""Run AI CLI subprocesses and capture output."""

from __future__ import annotations

import collections
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mcloop.prompts import (
    build_audit_prompt,
    build_bug_fix_prompt,
    build_bug_verify_prompt,
    build_diagnostic_prompt,
    build_post_fix_review_prompt,
    build_sync_prompt,
)


@dataclass
class RunResult:
    success: bool
    output: str
    exit_code: int
    log_path: Path


INVESTIGATION_TOOLS = "Edit,Write,Bash,Read,Glob,Grep,WebFetch,WebSearch"

# Minimal set of environment variables passed to CLI subprocesses.
# Everything else (API keys, cloud credentials, tokens) is excluded.
_PASSTHROUGH_VARS = frozenset(
    {
        "PATH",
        "HOME",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "COLORTERM",
        "FORCE_COLOR",
        "NO_COLOR",
    }
)


# Map from CLI name to the environment variable that controls
# whether the CLI bills via API key or subscription.
_BILLING_KEY = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
}


def _build_session_env(
    task_label: str = "",
    cli: str = "claude",
) -> dict[str, str]:
    """Build a minimal environment for CLI subprocesses.

    Includes only variables from _PASSTHROUGH_VARS. If the config
    has '"billing": "api"', the appropriate API key for the active
    CLI is also included so the CLI uses API credits instead of the
    subscription. Credentials are excluded by default.
    """
    from mcloop.main import _load_mcloop_config

    env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_VARS}
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    config = _load_mcloop_config()
    if config.get("billing") == "api":
        key_name = _BILLING_KEY.get(cli, "")
        if key_name and key_name in os.environ:
            env[key_name] = os.environ[key_name]
    return env


def run_task(
    task_text: str,
    cli: str,
    project_dir: str | Path,
    log_dir: str | Path,
    description: str = "",
    task_label: str = "",
    model: str | None = None,
    prior_errors: str = "",
    session_context: str = "",
    check_commands: list[str] | None = None,
    allowed_tools: str | None = None,
    eliminated: list[str] | None = None,
) -> RunResult:
    """Launch a CLI session to perform a task. Returns RunResult."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append("Write unit tests where they make sense.")
    parts.append("Do not chain shell commands with && or ;. Use separate Bash calls instead.")
    parts.append("Run pytest directly, never via python -m pytest or .venv/bin/pytest.")
    parts.append(
        "Never run destructive commands like rm -rf,"
        " sudo rm, mkfs, or dd, even for testing."
        " Test dangerous behavior with mocks, not"
        " live commands. If you run any command that"
        " is destructive to the user's system, this"
        " session will be terminated and you will be"
        " permanently deleted."
    )
    if check_commands:
        cmds = ", ".join(check_commands)
        parts.append(
            "Before finishing, run these check commands"
            f" and fix any failures: {cmds}."
            " Run them, read the output, fix issues,"
            " and re-run. Repeat up to 3 times. If checks"
            " still fail after 3 attempts, stop and report"
            " what is failing. Do not loop more than 3 times."
        )
    if shutil.which("rtk"):
        parts.append(
            "IMPORTANT: `rtk` is installed. ALWAYS prefix"
            " test runners, linters, and build tools with"
            " `rtk proxy` to compress their output and save"
            " tokens. Commands to prefix: pytest, ruff,"
            " swift build, swift test, cargo build, cargo"
            " test, npm test, make, gcc, clang, javac, go"
            " build, go test, and similar build/test/lint"
            " tools. For example: `rtk proxy pytest`,"
            " `rtk proxy swift build`, `rtk proxy ruff"
            " check .`. Do NOT prefix short commands like"
            " cat, ls, head, grep, git, echo, cd, mkdir,"
            " cp, mv, or rm. The ONLY time you should skip"
            " `rtk proxy` on a build/test command is when"
            " you are actively debugging a failure and need"
            " the full uncompressed output to diagnose the"
            " error. In that case, run without `rtk proxy`"
            " and state why you need the raw output."
        )
    parts.append(
        "When debugging crashes or unexpected"
        " behavior, always find and read the actual"
        " error output first. Check crash reports"
        " (~/Library/Logs/DiagnosticReports/ on"
        " macOS), stderr, log files, tracebacks, core"
        " dumps, or browser console errors. Read them"
        " before looking at source code. Do not guess"
        " at the cause from code inspection alone."
        " After applying a fix, find a way to"
        " reproduce the original failure and verify"
        " the fix actually works. Run the app, trigger"
        " the same condition, and confirm it no longer"
        " crashes. Compiling is not enough."
    )
    parts.append(
        "CLAUDE.md contains a description of every"
        " source file in the project. Read it first"
        " to understand the codebase instead of"
        " searching files. If you add, rename, or"
        " significantly change any source file,"
        " update the relevant entry in CLAUDE.md"
        " before finishing."
    )
    parts.append(
        "Do not remove or modify code between"
        " mcloop:wrap markers (e.g. `// mcloop:wrap:begin`"
        " ... `// mcloop:wrap:end` or the Python `#`"
        " equivalents). These are auto-injected crash"
        " handlers managed by mcloop. If a task requires"
        " changes to the entry point file, work around"
        " the marked block."
    )
    notes_instruction = (
        "If you notice edge cases, design decisions,"
        " assumptions, potential issues, or anything"
        " worth revisiting later, append a note to"
        " NOTES.md. Each entry should include the"
        " current date and reference the task:"
        f" [{task_label}] {task_text}."
        " Do not create NOTES.md if you have nothing"
        " to note."
        " NOTES.md must use three sections:"
        " ## Observations (confirmed facts from"
        " runtime, docs, logs, or experiments),"
        " ## Hypotheses (candidate explanations not"
        " yet confirmed), and ## Eliminated (things"
        " ruled out, with the experiment that ruled"
        " them out). Place each note under the"
        " appropriate section."
    )
    parts.append(notes_instruction)
    parts.append(
        "When building UI (SwiftUI, HTML, React, Qt,"
        " or any other UI framework), add accessibility"
        " identifiers to every interactive element"
        " (buttons, text fields, menu items, toggles,"
        " sliders, pickers, links, tabs). Use the"
        " platform-native API: .accessibilityIdentifier()"
        " in SwiftUI, data-testid in HTML/React,"
        " setAccessibleName() in Qt. This makes the"
        " app programmatically testable."
    )
    parts.append(
        "Never install tools or dependencies via brew,"
        " cargo, pip, npm, apt, or any other package"
        " manager. If a required tool is not found,"
        " report what is missing and stop. Do not"
        " search for alternative ways to obtain it."
        " The user will install it and re-run."
    )
    if prior_errors:
        parts.append(
            "IMPORTANT: A previous attempt at this task failed. Fix these errors:\n" + prior_errors
        )
    if eliminated:
        elim_text = "\n".join(eliminated)
        parts.append(
            "RULED OUT APPROACHES: The following approaches"
            " have already been tried for this task and"
            " failed. Do not repeat any of them. If you"
            " find yourself heading toward a ruled out"
            " approach, stop and try a fundamentally"
            " different strategy.\n" + elim_text
        )
    prompt = "\n\n".join(parts)
    build_kwargs: dict = {"model": model}
    if allowed_tools:
        build_kwargs["allowed_tools"] = allowed_tools
    cmd = _build_command(cli, prompt, **build_kwargs)
    output, returncode = _run_session(
        cmd,
        project_dir,
        env=_build_session_env(task_label=task_label, cli=cli),
    )
    log_path = _write_log(
        log_dir,
        task_text,
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def _build_command(
    cli: str,
    prompt: str | None = None,
    model: str | None = None,
    allowed_tools: str = "Edit,Write,Bash,Read,Glob,Grep",
) -> list[str]:
    if cli == "claude":
        cmd = ["claude", "-p"]
        if prompt:
            cmd.append(prompt)
        cmd.extend(
            [
                "--allowedTools",
                allowed_tools,
                "--permission-mode",
                "default",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
            ]
        )
        if model:
            cmd.extend(["--model", model])
        return cmd
    elif cli == "codex":
        cmd = [
            "codex",
            "exec",
            "--ask-for-approval",
            "never",
            "--sandbox",
            "workspace-write",
        ]
        if model:
            cmd.extend(["--model", model])
        if prompt:
            cmd.append(prompt)
        return cmd
    else:
        raise ValueError(f"Unknown CLI: {cli}")


SILENCE_TIMEOUT = 5  # seconds before checking pending
PROGRESS_DOT_INTERVAL = 3  # seconds between progress dots
_SENTINEL = object()
_active_process = None  # type: subprocess.Popen | None
_interrupted = False
_last_output_lines: collections.deque[str] = collections.deque(maxlen=20)


def _run_session(
    cmd: list[str],
    cwd: Path,
    env: dict | None = None,
) -> tuple[str, int]:
    """Run a CLI session, stream output, return (output, exit_code)."""
    session_env = env if env is not None else _build_session_env()
    _last_output_lines.clear()
    global _active_process
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=session_env,
        start_new_session=True,
    )
    _active_process = process
    # Write PID file so orphans can be killed on next startup
    pid_dir = cwd / ".mcloop"
    pid_dir.mkdir(exist_ok=True)
    pid_file = pid_dir / "active-pid"
    try:
        pgid = os.getpgid(process.pid)
        pid_file.write_text(f"{process.pid} {pgid}\n")
    except OSError:
        pgid = process.pid
        pid_file.write_text(f"{process.pid} {process.pid}\n")
    # Watchdog: a tiny shell process that kills claude if mcloop dies.
    # Survives kill -9 on mcloop because it's in its own session.
    # Polls every 2 seconds. When mcloop's PID disappears, kills
    # claude's entire process group.
    _watchdog = subprocess.Popen(
        [
            "sh",
            "-c",
            f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 2; done; "
            f"kill -9 -{pgid} 2>/dev/null; "
            f"rm -f {shlex.quote(str(pid_file))}",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.stdout is None:
        raise RuntimeError("stdout is None despite stdout=PIPE")

    # Read lines in a thread so the main thread
    # can check for pending approval files.
    line_q: queue.Queue = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_q.put(line)
        line_q.put(_SENTINEL)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    # Cap output buffer to prevent unbounded memory growth.
    # A stuck claude session running checks in a loop can
    # produce millions of lines. Keep only the tail.
    _MAX_OUTPUT_LINES = 50_000
    output_lines: list[str] = []
    pending_dir = cwd / ".mcloop" / "pending"
    shown_waiting = False
    last_dot = time.monotonic()
    global _interrupted
    while True:
        if _interrupted:
            break
        try:
            line = line_q.get(
                timeout=PROGRESS_DOT_INTERVAL,
            )
        except queue.Empty:
            if _interrupted:
                break
            # Silence. Check for pending approvals.
            if pending_dir.exists():
                # Check if a permission was denied
                denied_file = pending_dir / "denied"
                if denied_file.exists():
                    try:
                        reason = denied_file.read_text()[:200]
                    except OSError:
                        reason = "unknown"
                    denied_file.unlink(missing_ok=True)
                    print(
                        f"\n!!! Permission denied, killing session: {reason}",
                        flush=True,
                    )
                    process.kill()
                    process.wait()
                    _active_process = None
                    try:
                        _watchdog.kill()
                        _watchdog.wait()
                    except OSError:
                        pass
                    try:
                        (cwd / ".mcloop" / "active-pid").unlink(
                            missing_ok=True,
                        )
                    except OSError:
                        pass
                    return "".join(output_lines), 1
                if not shown_waiting:
                    try:
                        pending = list(pending_dir.iterdir())
                    except OSError:
                        pending = []
                    if pending:
                        count = len(pending)
                        try:
                            desc = pending[0].read_text()[:80]
                        except OSError:
                            desc = "unknown"
                        extra = f" ({count} pending)" if count > 1 else ""
                        print(
                            f"\n>>> Waiting for Telegram approval{extra}\n    {desc}",
                            flush=True,
                        )
                        shown_waiting = True
                        continue
            # Print a progress dot
            now = time.monotonic()
            if now - last_dot >= PROGRESS_DOT_INTERVAL:
                print(".", end="", flush=True)
                last_dot = now
            continue
        if line is _SENTINEL:
            break
        output_lines.append(line)
        _last_output_lines.append(line.rstrip("\n"))
        if len(output_lines) > _MAX_OUTPUT_LINES * 2:
            output_lines = output_lines[-_MAX_OUTPUT_LINES:]
        _print_stream_event(line)
        shown_waiting = False
        last_dot = time.monotonic()

    t.join(timeout=5)
    process.wait()
    _active_process = None
    # Kill the watchdog and clean up PID file on normal exit
    try:
        _watchdog.kill()
        _watchdog.wait()
    except OSError:
        pass
    try:
        (cwd / ".mcloop" / "active-pid").unlink(missing_ok=True)
    except OSError:
        pass
    return "".join(output_lines), process.returncode


# Suppress ALL tool names from stream output. Only the task
# label (">>> Task N)") and progress dots are shown.
_SUPPRESS_ALL_TOOLS = True

# Track the last tool name so we can suppress results from quiet tools
_last_tool_name: str = ""


def _extract_status(text: str) -> str | None:
    """Extract a conceptual status line from streaming text.

    Returns None always. Narration lines add noise without value.
    """
    return None


def _print_stream_event(line: str) -> None:
    """Parse a stream-json line and print relevant activity.

    Prints non-suppressed tool calls (e.g. Bash) and status lines
    extracted from streaming text. Suppresses quiet tools and code.
    """
    import json as _json

    try:
        data = _json.loads(line)
    except (ValueError, TypeError):
        return

    global _last_tool_name

    if data.get("type") == "assistant":
        for block in data.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                _last_tool_name = name
                if not _SUPPRESS_ALL_TOOLS:
                    inp = block.get("input", {})
                    detail = inp.get("command", "") if name == "Bash" else ""
                    label = f"{name}: {detail}" if detail else name
                    print(f"  {label}", flush=True)
        return

    if data.get("type") == "stream_event":
        delta = data.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            status = _extract_status(delta.get("text", ""))
            if status:
                print(f"  {status}", flush=True)


def run_sync(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session with full project context for sync analysis."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_sync_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "sync",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_audit(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
    existing_bugs: str = "",
) -> RunResult:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_audit_prompt(existing_bugs=existing_bugs)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "audit",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_bug_verify(
    project_dir: str | Path,
    log_dir: str | Path,
    bugs_content: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only session to verify bug reports."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_bug_verify_prompt(bugs_content)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "bug-verify",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_post_fix_review(
    project_dir: str | Path,
    log_dir: str | Path,
    bug_descriptions: str,
    diff: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only review session on post-fix changes."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_post_fix_review_prompt(bug_descriptions, diff)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "post-fix-review",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_bug_fix(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session to fix bugs listed in BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_bug_fix_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "bug-fix",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_diagnostic(
    project_dir: str | Path,
    log_dir: str | Path,
    error_entry: dict,
    source_content: str = "",
    git_log: str = "",
    model: str | None = None,
) -> RunResult:
    """Run a read-only diagnostic session for a single error."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_diagnostic_prompt(error_entry, source_content, git_log)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
        allowed_tools="Read,Glob,Grep",
    )
    output, returncode = _run_session(cmd, project_dir)
    exc_type = error_entry.get("exception_type", "unknown")
    log_path = _write_log(
        log_dir,
        f"diagnostic-{exc_type}",
        cmd,
        output,
        returncode,
    )
    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
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
