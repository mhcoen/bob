"""Launch, monitor, and inspect subprocesses."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CLIResult:
    """Result of running a CLI app to completion."""

    exit_code: int | None  # None if killed due to hang
    output: str  # Combined stdout/stderr
    hung: bool  # True if killed due to no-output timeout
    duration: float  # Wall-clock seconds
    sample_output: str | None = None  # macOS sample if hung


@dataclass
class LaunchedProcess:
    """A launched subprocess with its output pipe."""

    pid: int
    process: subprocess.Popen
    started_at: float = field(default_factory=time.monotonic)
    last_output_at: float = field(default_factory=time.monotonic)


def launch(command: str, cwd: str | Path | None = None) -> LaunchedProcess:
    """Launch a process from a shell command string.

    Returns a LaunchedProcess with stdout/stderr merged into stdout.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
    )
    now = time.monotonic()
    return LaunchedProcess(
        pid=proc.pid,
        process=proc,
        started_at=now,
        last_output_at=now,
    )


def is_alive(pid: int) -> bool:
    """Check if a process is alive by PID."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack permission to signal it.
        return True


def is_hung(proc: LaunchedProcess, timeout_seconds: float) -> bool:
    """Detect a hung process: alive but no output for timeout_seconds.

    Reads available output from the process pipe and updates
    last_output_at if any data is found. Returns True if the process
    is alive but has not produced output within the timeout window.
    """
    if proc.process.poll() is not None:
        return False

    # Try to read available output without blocking.
    stdout = proc.process.stdout
    if stdout is not None:
        fd = stdout.fileno()
        readable, _, _ = select.select([fd], [], [], 0)
        if readable:
            data = os.read(fd, 65536)
            if data:
                proc.last_output_at = time.monotonic()
                return False

    elapsed = time.monotonic() - proc.last_output_at
    return elapsed >= timeout_seconds


def sample(pid: int, duration_seconds: float = 1.0) -> str:
    """Sample a process on macOS using the `sample` command.

    Returns the sample output as a string, or an error message
    if sampling fails.
    """
    try:
        result = subprocess.run(
            ["sample", str(pid), str(int(duration_seconds))],
            capture_output=True,
            text=True,
            timeout=duration_seconds + 10,
        )
        return result.stdout or result.stderr
    except FileNotFoundError:
        return "sample command not found (not macOS?)"
    except subprocess.TimeoutExpired:
        return f"sample timed out after {duration_seconds + 10}s"


def kill(pid: int, graceful_timeout: float = 5.0) -> bool:
    """Kill a process. Tries SIGTERM first, then SIGKILL.

    Returns True if the process was successfully terminated.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    deadline = time.monotonic() + graceful_timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.1)

    # Still alive — force kill.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


def read_crash_report(process_name: str) -> str | None:
    """Read the most recent crash report matching a process name.

    Searches ~/Library/Logs/DiagnosticReports/ for .ips files
    whose name starts with the process name. Returns the contents
    of the most recently modified match, or None if not found.
    """
    reports_dir = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    if not reports_dir.is_dir():
        return None

    matches: list[Path] = []
    for entry in reports_dir.iterdir():
        if entry.name.startswith(process_name) and entry.suffix == ".ips":
            matches.append(entry)

    if not matches:
        return None

    newest = max(matches, key=lambda p: p.stat().st_mtime)
    try:
        return newest.read_text()
    except OSError:
        return None


def run_cli(
    command: str,
    cwd: str | Path | None = None,
    timeout_seconds: float = 30.0,
    hang_seconds: float = 10.0,
    poll_interval: float = 0.1,
) -> CLIResult:
    """Launch a CLI app, capture output, detect crash or hang.

    Runs the command and monitors it until it exits or hangs.
    A crash is a non-zero exit code. A hang is detected when
    the process produces no output for hang_seconds.

    Args:
        command: Shell command to run.
        cwd: Working directory.
        timeout_seconds: Max wall-clock time before killing.
        hang_seconds: No-output duration that triggers hang detection.
        poll_interval: How often to check for output/status.

    Returns:
        CLIResult with exit code, output, and hang/crash info.
    """
    proc = launch(command, cwd=cwd)
    chunks: list[bytes] = []
    start = time.monotonic()
    stdout_fd = proc.process.stdout.fileno() if proc.process.stdout else -1

    while True:
        # Check wall-clock timeout.
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            sample_out = sample(proc.pid)
            kill(proc.pid)
            # Drain remaining output.
            if proc.process.stdout:
                rest = proc.process.stdout.read()
                if rest:
                    chunks.append(rest)
            proc.process.wait()
            return CLIResult(
                exit_code=None,
                output=b"".join(chunks).decode("utf-8", errors="replace"),
                hung=True,
                duration=time.monotonic() - start,
                sample_output=sample_out,
            )

        # Read available output without blocking.
        if stdout_fd >= 0:
            readable, _, _ = select.select([stdout_fd], [], [], poll_interval)
            if readable:
                data = os.read(stdout_fd, 65536)
                if data:
                    chunks.append(data)
                    proc.last_output_at = time.monotonic()

        # Check if process exited.
        ret = proc.process.poll()
        if ret is not None:
            # Drain any remaining output.
            if proc.process.stdout:
                rest = proc.process.stdout.read()
                if rest:
                    chunks.append(rest)
            return CLIResult(
                exit_code=ret,
                output=b"".join(chunks).decode("utf-8", errors="replace"),
                hung=False,
                duration=time.monotonic() - start,
            )

        # Check for hang (no output for hang_seconds).
        silence = time.monotonic() - proc.last_output_at
        if silence >= hang_seconds:
            sample_out = sample(proc.pid)
            kill(proc.pid)
            if proc.process.stdout:
                rest = proc.process.stdout.read()
                if rest:
                    chunks.append(rest)
            proc.process.wait()
            return CLIResult(
                exit_code=None,
                output=b"".join(chunks).decode("utf-8", errors="replace"),
                hung=True,
                duration=time.monotonic() - start,
                sample_output=sample_out,
            )
