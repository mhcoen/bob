"""Run ``mypy`` against the duplo package and require zero errors.

If ``mypy`` is not installed in the active environment, the test is skipped
with a clear message. Install mypy with ``pip install mypy`` to enable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mypy_available() -> bool:
    if shutil.which("mypy") is not None:
        return True
    try:
        subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.mark.skipif(not _mypy_available(), reason="mypy is not installed in this environment")
def test_mypy_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "duplo"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"mypy reported errors:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
