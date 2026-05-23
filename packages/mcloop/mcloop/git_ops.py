"""Git operations for mcloop: checkpoint, commit, push, change detection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mcloop import formatting
from mcloop.notify import notify

_SENSITIVE_PATTERNS = {".env", ".key", ".pem", "credentials.json", "secrets"}


def _git(
    args: list[str],
    cwd: Path,
    *,
    label: str = "",
    silent: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and report errors.

    Every git failure is printed to the terminal and sent via
    Telegram so the user is always aware of version control
    problems.
    """
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        cmd_str = " ".join(args)
        context = f" ({label})" if label else ""
        stderr = result.stderr.strip()
        msg = f"git error{context}: `{cmd_str}` exited {result.returncode}"
        if stderr:
            msg += f"\n    {stderr}"
        if not silent:
            print(formatting.error_msg(msg), flush=True)
        # Only notify for real git failures, not missing repos
        if not silent and "not a git repository" not in stderr:
            notify(msg, level="error")
    return result


def _refuse_nested_init(project_dir: Path) -> None:
    """Refuse to run ``git init`` inside a uv-workspace package.

    Walks strict ancestors of *project_dir* looking for a
    ``pyproject.toml`` whose contents declare ``[tool.uv.workspace]``.
    The presence of that table at an ancestor proves *project_dir* is
    inside a bob-style consolidated workspace (the workspace pyproject
    declaration is the only authoritative signal -- ancestor names like
    ``packages`` are not used). Creating a nested ``.git`` in a package
    subdirectory would shadow the workspace repository and break
    cross-package git operations, so the guard prints a structured
    error naming the workspace root, notifies, and exits 1.
    """
    for ancestor in Path(project_dir).resolve().parents:
        pyproject = ancestor / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            content = pyproject.read_text()
        except OSError:
            continue
        if "[tool.uv.workspace]" in content:
            msg = (
                "CRITICAL: refusing to run `git init` because mcloop is "
                f"running inside the uv workspace rooted at {ancestor}. "
                f"Re-run mcloop from the workspace root ({ancestor}) "
                "instead of a package subdirectory."
            )
            print(formatting.error_msg(msg), flush=True)
            notify(msg, level="error")
            sys.exit(1)


def _ensure_git(project_dir: Path) -> None:
    """Initialize a git repo if one does not exist.

    Mcloop depends on git for checkpointing, commits, and
    change detection. If the project directory has no ``.git``
    this creates one with an initial commit so all subsequent
    git operations work.

    Before doing anything, ``_refuse_nested_init`` blocks the
    case where mcloop is running inside a uv workspace package
    subdirectory; creating a nested ``.git`` there would shadow
    the workspace repository.

    Prints a prominent warning and notifies via Telegram if
    git init fails, since mcloop cannot function safely
    without version control.
    """
    _refuse_nested_init(project_dir)
    git_dir = project_dir / ".git"
    if git_dir.exists():
        return
    print(
        formatting.error_msg("No git repository found. Initializing one now..."),
        flush=True,
    )
    try:
        result = subprocess.run(
            ["git", "init"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"CRITICAL: git init failed: {result.stderr.strip()}"
            print(formatting.error_msg(msg), flush=True)
            notify(msg, level="error")
            sys.exit(1)
        # Create .gitignore if missing
        gitignore = project_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".duplo/\nlogs/\n.mcloop/\n.build/\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=project_dir,
            capture_output=True,
        )
        commit_result = subprocess.run(
            ["git", "commit", "-m", "mcloop: initial commit"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if commit_result.returncode != 0:
            stderr = commit_result.stderr.strip()
            msg = f"CRITICAL: initial git commit failed: {stderr or 'unknown error'}"
            if "user.email" in stderr or "user.name" in stderr:
                msg += (
                    "\nConfigure git first:\n"
                    "  git config --global user.email 'you@example.com'\n"
                    "  git config --global user.name 'Your Name'"
                )
            print(formatting.error_msg(msg), flush=True)
            notify(msg, level="error")
            sys.exit(1)
        print(formatting.system_msg("Git repository initialized."), flush=True)
    except FileNotFoundError:
        msg = "CRITICAL: git is not installed or not on PATH. Mcloop cannot run without git."
        print(formatting.error_msg(msg), flush=True)
        notify(msg, level="error")
        sys.exit(1)


def _sanitize_commit_msg(text: str, max_len: int = 200) -> str:
    """Strip characters that break shell quoting in git commit messages."""
    cleaned = text.replace("`", "").replace("\u2014", "--").replace("\u2192", "->")
    cleaned = cleaned.replace('"', "'").replace("\n", " ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned


def _checkpoint(
    project_dir: Path,
    next_task: str = "",
    verbose: bool = False,
) -> None:
    """Stage and commit all changes as a checkpoint.

    Stages both tracked modifications and untracked files
    (except logs/ and .mcloop/) so orphaned files from
    failed runs get committed before the next task.
    """
    if not (project_dir / ".git").exists():
        print(
            formatting.error_msg("Git checkpoint skipped: no .git directory"),
            flush=True,
        )
        return
    result = _git(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        label="checkpoint status",
    )
    if result.returncode != 0 or not result.stdout.strip():
        if verbose:
            print(formatting.system_msg("No pending changes to commit."), flush=True)
        return
    if verbose:
        print(formatting.system_msg("Committing pending changes..."), flush=True)
    msg = "mcloop: checkpoint"
    if next_task:
        msg += f" (next: {_sanitize_commit_msg(next_task)})"
    _stage_safe(project_dir, label="checkpoint")
    result = _git(
        ["git", "commit", "-m", msg],
        cwd=project_dir,
        label="checkpoint commit",
        silent=True,
    )
    if result.returncode != 0:
        # Nothing to commit is normal — status can report changes
        # that add -u doesn't stage (e.g. untracked files that were
        # skipped by the sensitive-file filter). Not an error.
        pass


def _push_or_die(project_dir: Path) -> None:
    """Push to remote before starting any work.

    Ensures the remote is up to date so no work is done on top
    of an un-pushed state. If there is no remote, this is a no-op.
    If the push fails, mcloop exits immediately.
    """
    if not (project_dir / ".git").exists():
        return
    result = _git(
        ["git", "remote"],
        cwd=project_dir,
        label="pre-flight remote check",
    )
    if not result.stdout.strip():
        return  # no remote configured
    print(formatting.system_msg("Pushing to remote..."), flush=True)
    push_result = _git(
        ["git", "push"],
        cwd=project_dir,
        label="pre-flight push",
        silent=True,
    )
    if push_result.returncode != 0:
        print(
            formatting.error_msg("Pre-flight push failed. Fix the remote and re-run mcloop."),
            flush=True,
        )
        sys.exit(1)


def _stage_safe(project_dir: Path, *, label: str = "") -> None:
    """Stage all changes while skipping sensitive files."""
    _git(["git", "add", "-u"], cwd=project_dir, label=f"{label} add -u")
    untracked = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label=f"{label} ls untracked",
    )
    for f in untracked.stdout.strip().splitlines():
        f = f.strip()
        if not f:
            continue
        if any(s in f for s in _SENSITIVE_PATTERNS):
            continue
        _git(["git", "add", "--", f], cwd=project_dir, label=f"{label} add {f}")


def _commit(project_dir: Path, task_text: str, *, raw_message: bool = False) -> str:
    """Stage all changes, commit, and push. Returns the new HEAD hash."""
    if not (project_dir / ".git").exists():
        print(
            formatting.error_msg("Git commit skipped: no .git directory"),
            flush=True,
        )
        return ""
    _stage_safe(project_dir, label="commit")
    add_result = _git(
        ["git", "diff", "--cached", "--quiet"],
        cwd=project_dir,
        label="commit check staged",
        silent=True,
    )
    if add_result.returncode == 0:
        # Nothing staged — treat as failure so caller knows commit didn't happen
        raise RuntimeError("git commit failed: nothing to commit after staging")
    commit_result = _git(
        [
            "git",
            "commit",
            "-m",
            _sanitize_commit_msg(task_text)
            if raw_message
            else f"Complete: {_sanitize_commit_msg(task_text)}",
        ],
        cwd=project_dir,
        label="commit",
    )
    if commit_result.returncode != 0:
        raise RuntimeError(
            f"git commit failed (exit {commit_result.returncode}). "
            "Check git status and re-run mcloop."
        )
    result = _git(
        ["git", "remote"],
        cwd=project_dir,
        label="commit remote check",
    )
    commit_hash = _get_git_hash(project_dir)
    if not result.stdout.strip():
        print(
            formatting.system_msg("No git remote configured; skipping push."),
            flush=True,
        )
        return commit_hash
    print(formatting.system_msg("Pushing..."), flush=True)
    push_result = _git(
        ["git", "push"],
        cwd=project_dir,
        label="push",
        silent=True,
    )
    if push_result.returncode != 0:
        raise RuntimeError(
            f"git push failed (exit {push_result.returncode}). Fix the remote and re-run mcloop."
        )
    return commit_hash


def _has_meaningful_changes(project_dir: Path) -> bool:
    """Check for file changes beyond PLAN.md and logs/.

    Uses git status --porcelain which works even in repos
    with no commits (no HEAD).
    """
    result = _git(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        label="check changes",
    )
    if result.returncode != 0:
        return True
    all_files = []
    for line in result.stdout.strip().splitlines():
        # porcelain format: XY filename (or XY old -> new for renames)
        if len(line) > 3:
            name = line[3:]
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            all_files.append(name)
    meaningful = [
        f
        for f in all_files
        if f and not f.startswith("logs/") and not f.startswith(".mcloop/") and f != "PLAN.md"
    ]
    return len(meaningful) > 0


def _has_uncommitted_changes(project_dir: Path) -> bool:
    """Return True if git diff --quiet detects any uncommitted changes.

    Unlike _has_meaningful_changes this does not filter out metadata files —
    it checks whether the working tree is completely clean.
    """
    result = _git(
        ["git", "diff", "--quiet"],
        cwd=project_dir,
        label="uncommitted check",
        silent=True,
    )
    if result.returncode != 0:
        return True
    # Also check staged changes
    result = _git(
        ["git", "diff", "--quiet", "--cached"],
        cwd=project_dir,
        label="uncommitted check (staged)",
        silent=True,
    )
    return result.returncode != 0


def _get_diff(project_dir: Path) -> str:
    """Return the combined diff of staged and unstaged changes."""
    result = _git(
        ["git", "diff", "HEAD"],
        cwd=project_dir,
        label="get diff",
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # Fallback: unstaged diff only (no HEAD yet)
    result = _git(
        ["git", "diff"],
        cwd=project_dir,
        label="get diff (no HEAD)",
    )
    return result.stdout.strip()


def _worktree_status(project_dir: Path) -> str:
    """Return raw ``git status --porcelain`` output (unfiltered).

    Unlike :func:`_changed_files`, this includes *all* uncommitted changes —
    logs/, .mcloop/, PLAN.md, etc.  Used for before/after comparisons to
    detect whether a checker or autofix step introduced new changes.
    """
    result = _git(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        label="worktree status",
        silent=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _changed_files(project_dir: Path) -> list[str]:
    """Return list of files with uncommitted changes, excluding logs and metadata.

    Uses ``git diff --name-only HEAD`` plus ``git ls-files --others
    --exclude-standard`` instead of parsing ``git status --porcelain``
    output. The porcelain format is position-sensitive (status chars at
    cols 0-1, space at col 2, path at col 3+) and easy to mis-parse;
    the name-only commands return bare filenames with no prefix to
    strip, avoiding entire classes of slicing bugs.
    """
    files: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        name = name.strip()
        if not name or name in seen:
            return
        if name.startswith("logs/") or name.startswith(".mcloop/") or name == "PLAN.md":
            return
        seen.add(name)
        files.append(name)

    # Tracked files: modified, staged, or deleted relative to HEAD.
    # --name-only emits one clean path per line (handles renames by
    # emitting only the new name).
    diff_result = _git(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=project_dir,
        label="changed files (diff)",
        silent=True,
    )
    # Fall back to diff without HEAD when HEAD doesn't exist yet
    # (fresh repo with no commits).
    if diff_result.returncode != 0:
        diff_result = _git(
            ["git", "diff", "--name-only"],
            cwd=project_dir,
            label="changed files (diff, no HEAD)",
            silent=True,
        )
    if diff_result.returncode == 0:
        for line in diff_result.stdout.splitlines():
            _add(line)

    # Untracked files (ignores .gitignore entries via --exclude-standard).
    untracked_result = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label="changed files (untracked)",
        silent=True,
    )
    if untracked_result.returncode == 0:
        for line in untracked_result.stdout.splitlines():
            _add(line)

    return files


def _committed_files(project_dir: Path, commit_sha: str) -> list[str]:
    """Return list of files changed in *commit_sha*, excluding logs and metadata."""
    result = _git(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha],
        cwd=project_dir,
        label="committed files",
        silent=True,
    )
    if result.returncode != 0:
        return []
    files = []
    for f in result.stdout.strip().splitlines():
        f = f.strip()
        if f and not f.startswith("logs/") and not f.startswith(".mcloop/") and f != "PLAN.md":
            files.append(f)
    return files


def _get_committed_diff(project_dir: Path, commit_sha: str) -> str:
    """Return the diff introduced by *commit_sha*."""
    result = _git(
        ["git", "diff", f"{commit_sha}~1", commit_sha],
        cwd=project_dir,
        label="committed diff",
        silent=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _snapshot_worktree(project_dir: Path) -> tuple[list[str], list[str]]:
    """Snapshot modified and untracked files in the working tree.

    Returns (modified, untracked) where each is a list of file paths
    relative to the project root. Used at batch start so rollback can
    preserve pre-existing dirty state.
    """
    modified: list[str] = []
    untracked: list[str] = []
    diff_result = _git(
        ["git", "diff", "--name-only"],
        cwd=project_dir,
        label="snapshot modified",
    )
    if diff_result.returncode == 0:
        for line in diff_result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                modified.append(line)
    untracked_result = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label="snapshot untracked",
    )
    if untracked_result.returncode == 0:
        for line in untracked_result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                untracked.append(line)
    return modified, untracked


def _get_git_hash(project_dir: Path) -> str:
    """Return current HEAD commit hash."""
    if not (project_dir / ".git").exists():
        return ""
    result = _git(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        label="get HEAD hash",
    )
    return result.stdout.strip()
