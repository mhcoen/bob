"""Launch, monitor, and inspect subprocesses."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


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
        import select

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
