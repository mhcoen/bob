"""Git operations for mcloop: checkpoint, commit, push, change detection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mcloop import formatting
from mcloop.notify import notify


def _git(
    args: list[str],
    cwd: Path,
    *,
    label: str = "",
    silent: bool = False,
) -> subprocess.CompletedProcess:
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
        print(formatting.error_msg(msg), flush=True)
        # Only notify for real git failures, not missing repos
        if not silent and "not a git repository" not in stderr:
            notify(msg, level="error")
    return result


def _ensure_git(project_dir: Path) -> None:
    """Initialize a git repo if one does not exist.

    Mcloop depends on git for checkpointing, commits, and
    change detection. If the project directory has no ``.git``
    this creates one with an initial commit so all subsequent
    git operations work.

    Prints a prominent warning and notifies via Telegram if
    git init fails, since mcloop cannot function safely
    without version control.
    """
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
        subprocess.run(
            ["git", "commit", "-m", "mcloop: initial commit"],
            cwd=project_dir,
            capture_output=True,
        )
        print(formatting.system_msg("Git repository initialized."), flush=True)
    except FileNotFoundError:
        msg = "CRITICAL: git is not installed or not on PATH. Mcloop cannot run without git."
        print(formatting.error_msg(msg), flush=True)
        notify(msg, level="error")
        sys.exit(1)


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
        msg += f" (next: {next_task})"
    _git(["git", "add", "-u"], cwd=project_dir, label="checkpoint add -u")
    # Stage untracked files individually, skipping sensitive patterns
    untracked = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label="checkpoint ls untracked",
    )
    _sensitive = {".env", ".key", ".pem", "credentials.json", "secrets"}
    for f in untracked.stdout.strip().splitlines():
        f = f.strip()
        if not f:
            continue
        if any(s in f for s in _sensitive):
            continue
        _git(["git", "add", "--", f], cwd=project_dir, label=f"checkpoint add {f}")
    _git(
        ["git", "commit", "-m", msg],
        cwd=project_dir,
        label="checkpoint commit",
    )


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


def _commit(project_dir: Path, task_text: str) -> None:
    """Stage all changes, commit, and push."""
    if not (project_dir / ".git").exists():
        print(
            formatting.error_msg("Git commit skipped: no .git directory"),
            flush=True,
        )
        return
    _git(["git", "add", "-A"], cwd=project_dir, label="commit add")
    _git(
        ["git", "commit", "-m", f"Complete: {task_text}"],
        cwd=project_dir,
        label="commit",
    )
    result = _git(
        ["git", "remote"],
        cwd=project_dir,
        label="commit remote check",
    )
    if not result.stdout.strip():
        print(
            formatting.system_msg("No git remote configured; skipping push."),
            flush=True,
        )
        return
    if result.stdout.strip():
        print(formatting.system_msg("Pushing..."), flush=True)
        push_result = _git(
            ["git", "push"],
            cwd=project_dir,
            label="push",
            silent=True,
        )
        if push_result.returncode != 0:
            raise RuntimeError(
                f"git push failed (exit {push_result.returncode})."
                f" Fix the remote and re-run mcloop."
            )


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


def _changed_files(project_dir: Path) -> list[str]:
    """Return list of files with uncommitted changes, excluding logs and metadata."""
    result = _git(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        label="changed files",
    )
    if result.returncode != 0:
        return []
    files = []
    for line in result.stdout.strip().splitlines():
        if len(line) > 3:
            f = line[3:]
            if " -> " in f:
                f = f.split(" -> ", 1)[1]
            if f and not f.startswith("logs/") and not f.startswith(".mcloop/") and f != "PLAN.md":
                files.append(f)
    return files


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
