"""Run McLoop on a target project directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_mcloop(target_dir: Path | str = ".") -> int:
    """Run ``mcloop sync`` in *target_dir*.

    Output is streamed directly to the terminal (not captured).  Returns the
    exit code of the mcloop process.

    Args:
        target_dir: Directory containing the project's ``PLAN.md``.

    Returns:
        Exit code (0 on success, non-zero on failure).
    """
    cwd = Path(target_dir).resolve()
    try:
        result = subprocess.run(
            ["mcloop"],
            cwd=str(cwd),
        )
    except FileNotFoundError:
        print("Error: mcloop is not installed or not on PATH.")
        return 1
    return result.returncode
