"""Create target project directory and initialise a git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse


def project_name_from_url(url: str) -> str:
    """Derive a default project name from *url*.

    Uses the hostname with dots replaced by hyphens, e.g.
    ``https://linear.app`` → ``linear-app``.
    """
    hostname = urlparse(url).hostname or "project"
    return hostname.replace(".", "-")


def create_project_dir(project_dir: Path | str) -> Path:
    """Create *project_dir* and run ``git init`` inside it.

    The directory is created (including any missing parents).  If it already
    exists the function raises :exc:`FileExistsError` so the caller can decide
    whether to abort or proceed.

    Returns the resolved :class:`~pathlib.Path` to the created directory.
    """
    path = Path(project_dir).resolve()
    if path.exists():
        raise FileExistsError(f"Directory already exists: {path}")
    path.mkdir(parents=True)
    (path / ".duplo").mkdir()
    result = subprocess.run(
        ["git", "init", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git init failed: {result.stderr.strip()}")
    return path
