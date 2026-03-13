"""Gather project context for sync and audit sessions."""

from __future__ import annotations

import subprocess
from pathlib import Path


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

    _excluded = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    for path in sorted(project_dir.rglob("*.py")):
        if not _excluded.intersection(path.relative_to(project_dir).parts):
            rel = str(path.relative_to(project_dir))
            try:
                context[rel] = path.read_text()
            except Exception:
                pass

    return context


def gather_audit_context(project_dir: Path) -> dict[str, str]:
    """Collect README.md, CLAUDE.md, and all Python source files for auditing."""
    context: dict[str, str] = {}

    for name in ("README.md", "CLAUDE.md"):
        path = project_dir / name
        if path.exists():
            context[name] = path.read_text()

    _excluded = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    for path in sorted(project_dir.rglob("*.py")):
        if not _excluded.intersection(path.relative_to(project_dir).parts):
            rel = str(path.relative_to(project_dir))
            try:
                context[rel] = path.read_text()
            except Exception:
                pass

    return context
