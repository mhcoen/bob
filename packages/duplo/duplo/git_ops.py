"""Per-artifact git automation for duplo phase boundaries.

Duplo's phase-completion points (extract_design, generate_spec,
generate_plan, etc.) call into this module after the artifact write
succeeds. duplo fully automates version control: every phase's output
lands on disk, in a local git commit, and on a private GitHub remote so
a partial run has recoverable history both locally and remotely.

Each ``commit_artifact`` call, in the project directory, does:

  1. If there is no local repo, ``git init`` + ``git branch -M main``.
  2. Stage the artifact and commit. The local commit is the floor: it
     must land before any remote work is attempted.
  3. If there is no ``origin`` remote, create a private GitHub repo named
     after the project directory and wire it as origin via the
     authenticated ``gh`` CLI (``gh repo create ... --push``). If the
     repo already exists on GitHub, wire the existing remote instead.
  4. Push ``HEAD`` (with upstream on first push).

Steps 1 and 3 are no-ops after the first call in a run (repo + remote
already exist); later calls just commit and push.

Git/GitHub problems must NEVER abort a duplo phase. The artifact is
already on disk, and once step 2 lands it is in local history. Remote
failures (gh missing/unauthenticated, repo-create error, push error)
degrade gracefully: a single clear warning is printed and the phase
continues. Commit-failure and push-failure are distinct messages — a
push that fails is NOT reported as a commit failure.

Forbidden words in commit messages: claude, anthropic, happy,
co-authored-by. Use neutral descriptions only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Per-process warn-once caches keyed by resolved repo root, so a warning
# about an unavailable remote is not repeated for every phase in a run.
_LOGGED_GH_UNAVAILABLE: set[Path] = set()

# Warn-once guard so the "remote disabled" notice prints at most once per run.
_LOGGED_REMOTE_DISABLED = False


def _remote_disabled() -> bool:
    """True when remote work (GitHub repo create / push) must be skipped.

    A hard safety switch, independent of any test subprocess mocking, so a
    duplo run can NEVER create a GitHub repo when it should not — including
    in-process tests, tests that spawn duplo as a child process, and any
    caller that forgot to mock ``subprocess``:

    - ``DUPLO_NO_GITHUB`` explicitly set wins both ways: a truthy value
      ("1"/"true"/...) forces remote off (a user can export it to stop
      duplo's auto repo-creation entirely); a falsy value ("0"/"false"/
      "no") forces it on (used by the git_ops unit tests that exercise the
      remote path against a mocked ``gh``).
    - Otherwise, when a pytest run is in progress (``PYTEST_CURRENT_TEST``
      is set per-test and inherited by child processes), remote work is
      suppressed by default — repos named after tmp dirs were the original
      leak, and this blocks it even for an unmocked or subprocess-spawned
      test path.
    """
    flag = os.environ.get("DUPLO_NO_GITHUB")
    if flag is not None and flag.strip() != "":
        return flag.strip().lower() not in ("0", "false", "no")
    return "PYTEST_CURRENT_TEST" in os.environ


def _resolve_cwd(path: Path) -> Path:
    """Return the directory that ``git`` should run from for *path*."""
    if path.is_dir():
        return path
    parent = path.parent
    return parent if parent != Path() else Path.cwd()


def _warn(message: str) -> None:
    """Print a single ``[duplo]`` warning line to stderr."""
    print(f"[duplo] {message}", file=sys.stderr)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand from *cwd*, capturing output (no check)."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _is_git_repo(cwd: Path) -> bool:
    """Detect whether *cwd* is inside a git working tree."""
    try:
        result = _run_git(["rev-parse", "--git-dir"], cwd)
        return result.returncode == 0
    except (OSError, FileNotFoundError):
        return False


def _repo_toplevel(cwd: Path) -> Path:
    """Return the repo's top-level directory, or *cwd* if it can't be found."""
    result = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return cwd


def _has_origin(repo_root: Path) -> bool:
    """Return True when an ``origin`` remote URL is configured."""
    return _run_git(["remote", "get-url", "origin"], repo_root).returncode == 0


def _gh_authenticated() -> bool:
    """Return True when the ``gh`` CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except (OSError, FileNotFoundError):
        return False


def _gh_repo_create(repo_root: Path, name: str) -> str:
    """Create a private GitHub repo from *repo_root*, wire origin, and push.

    Returns one of:
      "created" — repo created, origin wired, pushed (one shot).
      "exists"  — the repo already exists remotely (caller wires it).
      "failed"  — any other failure (detail already warned by caller).
    """
    result = subprocess.run(
        ["gh", "repo", "create", name, "--source=.", "--remote=origin", "--private", "--push"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return "created"
    detail = (result.stderr or result.stdout or "").lower()
    if "already exists" in detail or "name already exists" in detail:
        return "exists"
    _warn(f"gh repo create failed for {name!r}: {(result.stderr or result.stdout or '').strip()}")
    return "failed"


def _gh_repo_ssh_url(repo_root: Path, name: str) -> str | None:
    """Return the ssh clone URL for an existing GitHub repo, or None."""
    result = subprocess.run(
        ["gh", "repo", "view", name, "--json", "sshUrl", "-q", ".sshUrl"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _git_push(repo_root: Path) -> tuple[bool, str]:
    """Push HEAD with upstream. Returns (ok, detail)."""
    result = _run_git(["push", "-u", "origin", "HEAD"], repo_root)
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "").strip()


def _ensure_remote_and_push(repo_root: Path) -> None:
    """Best-effort: ensure an origin remote exists and push HEAD to it.

    Never raises. On any unrecoverable remote problem a single warning is
    printed and control returns to the caller — the local commit already
    landed, so the phase proceeds either way.
    """
    if _remote_disabled():
        global _LOGGED_REMOTE_DISABLED
        if not _LOGGED_REMOTE_DISABLED:
            _warn("remote disabled (test env or DUPLO_NO_GITHUB); committed locally only.")
            _LOGGED_REMOTE_DISABLED = True
        return

    name = repo_root.name

    if _has_origin(repo_root):
        ok, detail = _git_push(repo_root)
        if not ok:
            _warn(f"git push failed (commit landed locally): {detail}")
        return

    # No origin yet: create the GitHub repo (or wire an existing one).
    if not _gh_authenticated():
        key = repo_root.resolve() if repo_root.exists() else repo_root
        if key not in _LOGGED_GH_UNAVAILABLE:
            _warn("gh unavailable/unauthenticated; committed locally, skipped GitHub.")
            _LOGGED_GH_UNAVAILABLE.add(key)
        return

    outcome = _gh_repo_create(repo_root, name)
    if outcome == "created":
        # gh repo create --push wired origin and pushed in one shot.
        return
    if outcome == "failed":
        # Warning already printed by _gh_repo_create; local commit stands.
        return

    # outcome == "exists": wire the existing remote and push.
    ssh_url = _gh_repo_ssh_url(repo_root, name)
    if ssh_url is None:
        _warn(f"GitHub repo {name!r} exists but its URL could not be read; skipped remote.")
        return
    add = _run_git(["remote", "add", "origin", ssh_url], repo_root)
    if add.returncode != 0 and not _has_origin(repo_root):
        _warn(f"could not wire origin for {name!r}: {(add.stderr or add.stdout).strip()}")
        return
    ok, detail = _git_push(repo_root)
    if not ok:
        _warn(f"git push failed (commit landed locally): {detail}")


def commit_artifact(
    path: Path | str,
    label: str,
    *,
    push: bool = True,
) -> bool:
    """Commit one artifact and (when ``push``) publish it to GitHub.

    Args:
      path: file or directory to ``git add``.
      label: phase-shape name embedded in the commit message
        ("duplo <label>: <basename>"). Short, mechanical, no narrative.
      push: when True (default), ensure a GitHub origin exists (creating
        the private repo on first call) and push HEAD. Set False in tests
        that want to assert local commit shape without remote work.

    Returns True when the local commit landed (regardless of whether the
    remote push succeeded — remote failures degrade to warnings). Returns
    False only when the local commit itself failed or there was nothing to
    commit. Never raises: git/GitHub problems never abort a duplo phase.
    """
    path = Path(path)
    cwd = _resolve_cwd(path)

    # Step 1: ensure a local repo exists.
    if not _is_git_repo(cwd):
        init = _run_git(["init"], cwd)
        if init.returncode != 0:
            _warn(f"git init failed at {cwd}: {(init.stderr or init.stdout).strip()}; skipped")
            return False
        _run_git(["branch", "-M", "main"], cwd)

    repo_root = _repo_toplevel(cwd)

    # Step 2: stage + commit. The local commit is the floor.
    message = f"duplo {label}: {path.name}"
    add = _run_git(["add", "--", str(path)], repo_root)
    if add.returncode != 0:
        _warn(f"git add failed for {path.name}: {(add.stderr or add.stdout).strip()}")
        return False

    commit = _run_git(["commit", "-m", message], repo_root)
    if commit.returncode != 0:
        detail = (commit.stdout or "") + (commit.stderr or "")
        if "nothing to commit" in detail.lower():
            # No change to record (e.g. artifact already committed). Not a
            # failure; still try to publish existing history when asked.
            if push:
                _ensure_remote_and_push(repo_root)
            return False
        _warn(f"git commit failed for {path.name} (label={label!r}): {detail.strip()}")
        return False

    # Steps 3-4: publish to GitHub (best effort).
    if push:
        _ensure_remote_and_push(repo_root)

    return True


def reset_logged_not_git_repo() -> None:
    """Clear the per-process warn-once caches.

    Tests that exercise the remote-unavailable / not-a-repo paths
    repeatedly call this in their fixtures so warn-once behavior does not
    bleed across tests.
    """
    _LOGGED_GH_UNAVAILABLE.clear()
