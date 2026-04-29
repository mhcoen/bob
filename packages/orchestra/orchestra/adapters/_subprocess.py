"""Subprocess invocation, output capture, and logging helpers.

Lifted from mcloop/runner.py (specifically ``_build_session_env``,
``_run_session``, and ``_write_log``) so the two real adapters in this
package can share the same patterns: minimal environment, stream-json
output capture, monotonic-clock timeout, head-and-tail truncation of
unbounded output, and a per-invocation log file with prologue.

Subprocess management is intentionally simple compared to mcloop's
runner. Mcloop owns approval-file polling, watchdog forking, PID file
maintenance, and progress-dot printing. None of that belongs at the
adapter layer. The orchestra executor enforces timeouts at a higher
level. This helper applies the same wall-clock cap as mcloop so a
runaway subprocess does not hang the run.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

# Minimal environment passed to CLI subprocesses. Matches mcloop's
# _PASSTHROUGH_VARS exactly so behavior is identical at the parity
# layer. RTK_* are kept because mcloop's tee tooling looks for them.
PASSTHROUGH_VARS: frozenset[str] = frozenset(
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
        "RTK_DB_PATH",
        "RTK_TEE",
        "RTK_TEE_DIR",
    }
)

DEFAULT_TIMEOUT_S: int = 1800
"""Wall-clock cap matching mcloop's ``DEFAULT_TASK_TIMEOUT``."""

PROGRESS_QUEUE_INTERVAL: float = 3.0
"""How long the reader thread blocks before checking the timeout."""

_MAX_HEAD_LINES = 5_000
_MAX_TAIL_LINES = 45_000
_SENTINEL = object()


def build_session_env(*, task_label: str = "") -> dict[str, str]:
    """Build a minimal environment dict for a CLI subprocess.

    Matches mcloop's ``_build_session_env`` shape. Billing-mode handling
    (``billing: api`` injecting ``ANTHROPIC_API_KEY``) is mcloop-private
    and not lifted here. The caller can add credentials by mutating the
    returned dict before passing it to ``run_session``.
    """
    env = {k: v for k, v in os.environ.items() if k in PASSTHROUGH_VARS}
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    return env


def run_session(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT_S,
) -> tuple[str, int]:
    """Run ``cmd`` in ``cwd``, stream output, return (output, exit_code).

    Streams stdout and stderr (merged) line by line through a queue so a
    monotonic-clock timeout check can fire without blocking on read.
    Output is bounded by a head-and-tail buffer with a marker line where
    truncation happened. On timeout the process group is killed and
    exit code -2 is returned, matching mcloop's convention.
    """
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    if process.stdout is None:
        raise RuntimeError("stdout is None despite stdout=PIPE")

    line_q: queue.Queue[Any] = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_q.put(line)
        line_q.put(_SENTINEL)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    head_lines: list[str] = []
    tail_lines: deque[str] = deque(maxlen=_MAX_TAIL_LINES)
    dropped = 0

    started = time.monotonic()
    while True:
        if timeout and (time.monotonic() - started) > timeout:
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except OSError:
                process.kill()
            process.wait()
            return _assemble(head_lines, tail_lines, dropped), -2
        try:
            line = line_q.get(timeout=PROGRESS_QUEUE_INTERVAL)
        except queue.Empty:
            continue
        if line is _SENTINEL:
            break
        if len(head_lines) < _MAX_HEAD_LINES:
            head_lines.append(line)
        else:
            if len(tail_lines) == _MAX_TAIL_LINES:
                dropped += 1
            tail_lines.append(line)

    reader_thread.join(timeout=5)
    process.wait()
    return _assemble(head_lines, tail_lines, dropped), process.returncode


def write_log(
    log_dir: Path,
    task_text: str,
    cmd: list[str],
    output: str,
    exit_code: int,
) -> Path:
    """Persist a captured session to ``log_dir`` and return the path.

    Format mirrors mcloop's ``_write_log`` so existing tooling can read
    both. The slug is bounded to 50 characters to keep filenames sane.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
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


def _assemble(
    head_lines: list[str],
    tail_lines: deque[str],
    dropped: int,
) -> str:
    if not tail_lines:
        return "".join(head_lines)
    if dropped == 0:
        return "".join(head_lines) + "".join(tail_lines)
    marker = (
        f"\n... [truncated {dropped} line(s) "
        f"between head ({_MAX_HEAD_LINES}) and tail "
        f"({_MAX_TAIL_LINES})] ...\n"
    )
    return "".join(head_lines) + marker + "".join(tail_lines)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]
