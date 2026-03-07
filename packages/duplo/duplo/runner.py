"""Run McLoop on a target project directory."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

_mcloop_process: subprocess.Popen | None = None


def run_mcloop(target_dir: Path | str = ".") -> int:
    """Run ``mcloop`` in *target_dir*.

    Output is streamed directly to the terminal (not captured).  Returns the
    exit code of the mcloop process.  Handles ctrl-c and ctrl-z by killing
    the mcloop process tree.

    Args:
        target_dir: Directory containing the project's ``PLAN.md``.

    Returns:
        Exit code (0 on success, non-zero on failure).
    """
    global _mcloop_process
    cwd = Path(target_dir).resolve()

    prev_sigint = signal.getsignal(signal.SIGINT)
    prev_sigtstp = signal.getsignal(signal.SIGTSTP)

    def _kill_mcloop(signum, frame):
        if _mcloop_process is not None:
            try:
                os.killpg(os.getpgid(_mcloop_process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    _mcloop_process.kill()
                except OSError:
                    pass
        os._exit(130)

    try:
        _mcloop_process = subprocess.Popen(
            ["mcloop"],
            cwd=str(cwd),
            start_new_session=True,
        )
        signal.signal(signal.SIGINT, _kill_mcloop)
        signal.signal(signal.SIGTSTP, _kill_mcloop)
        _mcloop_process.wait()
        return _mcloop_process.returncode
    except FileNotFoundError:
        print("Error: mcloop is not installed or not on PATH.")
        return 1
    finally:
        _mcloop_process = None
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTSTP, prev_sigtstp)
