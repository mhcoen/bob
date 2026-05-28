"""Per-artifact git commit support for duplo phase boundaries.

Duplo's phase-completion points (extract_design, generate_spec,
generate_plan, etc.) call into this module after the artifact write
succeeds. High commit cadence preserves a recoverable run history:
every phase's output lands on disk AND in git as a separate commit so
a partial run can be rolled back per phase.

Two failure modes are handled silently:

  (a) Target directory is not inside a git repo. The helper logs a
      single "not in git repo, skipping commits" line at first attempt
      and returns False from every subsequent call. The artifact is on
      disk regardless; only the git side is skipped.

  (b) The git command itself fails (hook rejection, conflict, network
      error on push). The helper prints the underlying git error to
      stderr and returns False but does NOT raise — duplo's phase
      completion still returns success because the artifact write
      itself already succeeded; only the git-side bookkeeping failed.

Forbidden words in commit messages: claude, anthropic, happy,
co-authored-by. Use neutral descriptions only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_LOGGED_NOT_GIT_REPO: set[Path] = set()


def _resolve_cwd(path: Path) -> Path:
    """Return the directory that ``git -C`` should run from.

    Uses the artifact path's parent unless it's a file that doesn't
    exist yet (in which case the parent is the staging-area parent).
    """
    if path.is_dir():
        return path
    parent = path.parent
    return parent if parent != Path() else Path.cwd()


def _is_git_repo(cwd: Path) -> bool:
    """Detect whether ``cwd`` is inside a git working tree.

    Uses ``git rev-parse --git-dir`` rather than checking for a
    ``.git/`` directory directly so worktrees and submodules report
    correctly. Returns False on any subprocess error (git missing,
    permission denied, etc.) so the helper can fall through to the
    "not in git repo" silent path.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except (OSError, FileNotFoundError):
        return False


def commit_artifact(
    path: Path | str,
    label: str,
    *,
    push: bool = True,
) -> bool:
    """Commit one artifact and (optionally) push.

    Args:
      path: file or directory to ``git add``. Resolves to its parent for
        the working directory when needed.
      label: phase-shape name embedded in the commit message
        ("duplo <label>: <basename>"). Should be short, mechanical, no
        narrative.
      push: when True (default), ``git push origin HEAD`` after commit.
        Set False in tests that want to assert local commit shape
        without configuring a remote.

    Returns True on successful commit (and push if ``push=True``).
    Returns False on any failure path including not-in-git-repo,
    nothing-to-commit, hook rejection, push failure. Never raises.
    """
    path = Path(path)
    cwd = _resolve_cwd(path)
    if not _is_git_repo(cwd):
        key = cwd.resolve() if cwd.exists() else cwd
        if key not in _LOGGED_NOT_GIT_REPO:
            print(
                f"[duplo] not in git repo at {cwd}, skipping commits",
                file=sys.stderr,
            )
            _LOGGED_NOT_GIT_REPO.add(key)
        return False

    message = f"duplo {label}: {path.name}"

    try:
        subprocess.run(
            ["git", "add", "--", str(path)],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        if push:
            subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=str(cwd),
                check=True,
                capture_output=True,
                text=True,
            )
        return True
    except subprocess.CalledProcessError as exc:
        # Surface the error but do not raise: the artifact is on disk;
        # only the git side failed. Common causes: empty commit (nothing
        # to add), commit-hook rejection, push permission failure.
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        print(
            f"[duplo] git commit failed for {path.name} (label={label!r}): {detail}",
            file=sys.stderr,
        )
        return False


def reset_logged_not_git_repo() -> None:
    """Clear the per-process "already logged" cache.

    Tests that exercise the not-in-git-repo path repeatedly call this
    in their fixtures so the silent-after-first behavior doesn't bleed
    across tests.
    """
    _LOGGED_NOT_GIT_REPO.clear()
