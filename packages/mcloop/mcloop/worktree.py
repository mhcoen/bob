"""Git worktree management for investigation branches."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _run_git(
    *args: str,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _slugify(text: str) -> str:
    """Turn a description into a branch-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    # Limit length to keep branch names reasonable
    return slug[:60].rstrip("-")


def current_branch(cwd: str | Path | None = None) -> str:
    """Return the current git branch name."""
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Not a git repository: {result.stderr.strip()}")
    return result.stdout.strip()


def create(
    description: str,
    cwd: str | Path | None = None,
) -> tuple[Path, str]:
    """Create a worktree for an investigation.

    Returns (worktree_path, branch_name).
    The worktree is created as a sibling directory of the repo root,
    named ``<repo>-investigate-<slug>``. The branch is named
    ``investigate/<slug>``.
    """
    branch = current_branch(cwd=cwd)

    slug = _slugify(description)
    if not slug:
        raise ValueError("Description produced an empty slug")

    branch_name = f"investigate/{slug}"

    # Find repo root
    root_result = _run_git(
        "rev-parse",
        "--show-toplevel",
        cwd=cwd,
    )
    if root_result.returncode != 0:
        raise RuntimeError(root_result.stderr.strip())
    repo_root = Path(root_result.stdout.strip())

    worktree_path = repo_root.parent / f"{repo_root.name}-investigate-{slug}"

    result = _run_git(
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        branch,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create worktree: {result.stderr.strip()}")

    return worktree_path, branch_name


def exists(description: str, cwd: str | Path | None = None) -> bool:
    """Check if a worktree already exists for this investigation."""
    slug = _slugify(description)
    if not slug:
        return False

    for wt in list_worktrees(cwd=cwd):
        if slug in wt["path"]:
            return True
    return False


def list_worktrees(
    cwd: str | Path | None = None,
) -> list[dict[str, str]]:
    """List active investigation worktrees.

    Returns a list of dicts with 'path', 'branch', and 'commit' keys.
    Only includes worktrees whose branch starts with ``investigate/``.
    """
    result = _run_git("worktree", "list", "--porcelain", cwd=cwd)
    if result.returncode != 0:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            current["commit"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            # Strip refs/heads/ prefix
            branch = ref.removeprefix("refs/heads/")
            current["branch"] = branch
        elif line == "" and current:
            if current.get("branch", "").startswith("investigate/"):
                worktrees.append(current)
            current = {}

    # Handle last entry (no trailing blank line)
    if current and current.get("branch", "").startswith("investigate/"):
        worktrees.append(current)

    return worktrees


def merge(
    branch_name: str,
    cwd: str | Path | None = None,
) -> None:
    """Merge an investigation branch back to the source branch.

    The investigation branch must start with ``investigate/``.
    Merges into the currently checked-out branch.
    """
    if not branch_name.startswith("investigate/"):
        raise ValueError(f"Not an investigation branch: {branch_name}")

    result = _run_git("merge", branch_name, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Merge failed: {result.stderr.strip()}")


def remove(
    branch_name: str,
    cwd: str | Path | None = None,
) -> None:
    """Remove a worktree and its branch after successful merge.

    Finds the worktree path from the branch name, removes the worktree,
    then deletes the branch.
    """
    if not branch_name.startswith("investigate/"):
        raise ValueError(f"Not an investigation branch: {branch_name}")

    # Find the worktree path for this branch
    worktree_path = None
    for wt in list_worktrees(cwd=cwd):
        if wt.get("branch") == branch_name:
            worktree_path = wt["path"]
            break

    if worktree_path:
        result = _run_git(
            "worktree",
            "remove",
            worktree_path,
            cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to remove worktree: {result.stderr.strip()}")

    # Delete the branch
    result = _run_git("branch", "-d", branch_name, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to delete branch: {result.stderr.strip()}")
