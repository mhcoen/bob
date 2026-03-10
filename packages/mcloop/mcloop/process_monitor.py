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
class GUIResult:
    """Result of monitoring a GUI app."""

    crashed: bool  # True if process disappeared unexpectedly
    hung: bool  # True if main thread stuck in sample output
    duration: float  # Wall-clock seconds monitored
    sample_output: str | None = None  # macOS sample if hung
    crash_report: str | None = None  # From DiagnosticReports


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


def launch(
    command: str,
    cwd: str | Path | None = None,
    stdin: bool = False,
) -> LaunchedProcess:
    """Launch a process from a shell command string.

    Returns a LaunchedProcess with stdout/stderr merged into stdout.
    If stdin is True, a stdin pipe is opened for sending input.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.PIPE if stdin else None,
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


def send_input(proc: LaunchedProcess, data: str | bytes, close: bool = False) -> None:
    """Send data to a process's stdin.

    Args:
        proc: A LaunchedProcess with stdin piped (launched with stdin=True).
        data: String or bytes to write. Strings are encoded as UTF-8.
        close: If True, close stdin after writing (sends EOF).

    Raises:
        ValueError: If the process was not launched with stdin=True.
        OSError: If the process has already exited or stdin is closed.
    """
    if proc.process.stdin is None:
        raise ValueError("Process was not launched with stdin=True")
    if isinstance(data, str):
        data = data.encode("utf-8")
    proc.process.stdin.write(data)
    proc.process.stdin.flush()
    if close:
        proc.process.stdin.close()


def read_output(proc: LaunchedProcess, timeout_seconds: float = 0.0) -> bytes:
    """Non-blocking read of available output from a process.

    Waits up to timeout_seconds for data to become available on stdout.
    Returns whatever bytes are available, or empty bytes if nothing is
    ready within the timeout.

    Args:
        proc: A LaunchedProcess with stdout piped.
        timeout_seconds: Max seconds to wait for data (0 = non-blocking).

    Returns:
        Bytes read from stdout, possibly empty.
    """
    stdout = proc.process.stdout
    if stdout is None:
        return b""
    fd = stdout.fileno()
    readable, _, _ = select.select([fd], [], [], timeout_seconds)
    if readable:
        data = os.read(fd, 65536)
        if data:
            proc.last_output_at = time.monotonic()
        return data
    return b""


def send_signal(pid: int, sig: int) -> bool:
    """Send a signal to a process by PID.

    Args:
        pid: Process ID.
        sig: Signal number (e.g. signal.SIGINT, signal.SIGUSR1).

    Returns:
        True if the signal was sent, False if the process does not
        exist or permission was denied.
    """
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


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


def pgrep(process_name: str) -> list[int]:
    """Find PIDs matching a process name using pgrep.

    Returns a list of integer PIDs, or an empty list if none found.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    pids = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def is_main_thread_stuck(sample_output: str) -> bool:
    """Check if macOS sample output shows a stuck main thread.

    Looks for the main thread section and checks if it is blocked
    in common wait/hang patterns: dispatch semaphore wait,
    mach_msg_trap, __psynch_cvwait, kevent, or similar.

    Returns True if the main thread appears stuck.
    """
    if not sample_output:
        return False

    lines = sample_output.splitlines()
    in_main_thread = False
    main_thread_frames: list[str] = []

    for line in lines:
        stripped = line.strip()
        if "Thread_0" in stripped or "Thread 0" in stripped:
            in_main_thread = True
            main_thread_frames = []
            continue
        if in_main_thread:
            if (
                (stripped.startswith("Thread_") or stripped.startswith("Thread "))
                and "Thread 0" not in stripped
                and "Thread_0" not in stripped
            ):
                break
            main_thread_frames.append(stripped)

    if not main_thread_frames:
        return False

    stuck_patterns = [
        "mach_msg_trap",
        "mach_msg2_trap",
        "__psynch_cvwait",
        "__semwait_signal",
        "dispatch_semaphore_wait",
        "__select",
        "__sigwait",
        "kevent",
        "CFRunLoopRunSpecific",
    ]
    frame_text = "\n".join(main_thread_frames)
    for pattern in stuck_patterns:
        if pattern in frame_text:
            return True

    return False


def run_gui(
    command: str,
    process_name: str,
    timeout_seconds: float = 30.0,
    check_interval: float = 1.0,
    settle_seconds: float = 2.0,
) -> GUIResult:
    """Launch a GUI app, monitor for crash or hang.

    Launches the command, waits for settle_seconds for the process
    to start, then periodically checks if the process is alive via
    pgrep. If the process disappears, it is considered a crash.
    If the process is alive at the end of the timeout, it is sampled
    to check if the main thread is stuck.

    Args:
        command: Shell command to launch the app.
        process_name: Process name for pgrep (exact match).
        timeout_seconds: How long to monitor before concluding.
        check_interval: How often to check alive status.
        settle_seconds: Wait after launch for process to appear.

    Returns:
        GUIResult with crash/hang status and diagnostics.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    start = time.monotonic()
    try:
        time.sleep(settle_seconds)

        pids = pgrep(process_name)
        if not pids:
            crash_rpt = read_crash_report(process_name)
            return GUIResult(
                crashed=True,
                hung=False,
                duration=time.monotonic() - start,
                crash_report=crash_rpt,
            )

        pid = pids[0]

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= timeout_seconds:
                break

            time.sleep(check_interval)

            if not is_alive(pid):
                crash_rpt = read_crash_report(process_name)
                return GUIResult(
                    crashed=True,
                    hung=False,
                    duration=time.monotonic() - start,
                    crash_report=crash_rpt,
                )

        sample_out = sample(pid)
        stuck = is_main_thread_stuck(sample_out)

        return GUIResult(
            crashed=False,
            hung=stuck,
            duration=time.monotonic() - start,
            sample_output=sample_out if stuck else None,
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait()
