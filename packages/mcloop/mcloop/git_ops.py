"""Git operations for mcloop: checkpoint, commit, push, change detection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcloop import formatting
from mcloop.notify import notify

_SENSITIVE_PATTERNS = {".env", ".key", ".pem", "credentials.json", "secrets"}

# Bound every git call so a hung remote or a credential/hook prompt cannot
# wedge the autonomous loop forever. GIT_TERMINAL_PROMPT=0 turns an
# interactive credential prompt into an immediate failure instead of a
# silent block; the timeout catches slow-network / hung-hook cases.
_GIT_TIMEOUT_S = 300


def run_git_bounded(
    args: list[str],
    cwd: Path | str | None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command bounded and non-interactive.

    The single low-level git runner: applies the timeout and
    ``GIT_TERMINAL_PROMPT=0`` guards, and converts a timeout into an
    ordinary failed ``CompletedProcess`` (exit 124) so callers handle
    it through their normal error paths. Silent — wrappers add their
    own reporting.
    """
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout if isinstance(exc.stdout, str) else ""
        partial_err = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(
            args,
            returncode=124,
            stdout=partial_out,
            stderr=partial_err + f"\ngit command timed out after {_GIT_TIMEOUT_S}s",
        )


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
    result = run_git_bounded(args, cwd)
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
    ancestor = _find_uv_workspace_ancestor(project_dir)
    if ancestor is not None:
        msg = (
            "CRITICAL: refusing to run `git init` because mcloop is "
            f"running inside the uv workspace rooted at {ancestor}. "
            f"Re-run mcloop from the workspace root ({ancestor}) "
            "instead of a package subdirectory."
        )
        print(formatting.error_msg(msg), flush=True)
        notify(msg, level="error")
        sys.exit(1)


def _find_uv_workspace_ancestor(project_dir: Path) -> Path | None:
    """Return the nearest ancestor declaring a uv workspace, if any."""
    for ancestor in Path(project_dir).resolve().parents:
        pyproject = ancestor / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            content = pyproject.read_text()
        except OSError:
            continue
        if "[tool.uv.workspace]" in content:
            return ancestor
    return None


def _find_ancestor_git(project_dir: Path) -> Path | None:
    """Return the nearest ancestor containing a Git marker, if any."""
    for ancestor in Path(project_dir).resolve().parents:
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _ensure_git(project_dir: Path) -> None:
    """Initialize a git repo if one does not exist.

    Mcloop depends on git for checkpointing, commits, and
    change detection. If neither ``project_dir`` nor any of
    its ancestors has a ``.git`` entry, this creates one with
    an initial commit so all subsequent git operations work.

    Walks upward from ``project_dir`` looking for ``.git``
    (file or directory -- ``.git`` is a file inside worktrees).
    If any strict ancestor has ``.git``, mcloop is running
    inside a parent repository (e.g. a consolidated workspace
    checkout) and uses that repo without creating a nested one.

    ``_refuse_nested_init`` remains as a defense-in-depth
    backstop: if execution reaches the initialization path
    inside a uv workspace package subdirectory (no ``.git``
    anywhere up the tree), the guard refuses to create a
    nested ``.git`` that would shadow the workspace repo.

    Prints a prominent warning and notifies via Telegram if
    git init fails, since mcloop cannot function safely
    without version control.
    """
    git_dir = project_dir / ".git"
    if git_dir.exists():
        return
    if _find_ancestor_git(project_dir) is not None:
        return
    _refuse_nested_init(project_dir)
    print(
        formatting.error_msg("No git repository found. Initializing one now..."),
        flush=True,
    )
    try:
        result = run_git_bounded(["git", "init"], project_dir)
        if result.returncode != 0:
            msg = f"CRITICAL: git init failed: {result.stderr.strip()}"
            print(formatting.error_msg(msg), flush=True)
            notify(msg, level="error")
            sys.exit(1)
        # Create .gitignore if missing
        gitignore = project_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".duplo/\nlogs/\n.mcloop/\n.build/\n")
        run_git_bounded(["git", "add", "-A"], project_dir)
        commit_result = run_git_bounded(
            ["git", "commit", "-m", "mcloop: initial commit"],
            project_dir,
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


def _has_git_repo(project_dir: Path) -> bool:
    """Return True if ``project_dir`` is inside a git repo.

    Recognizes the consolidated workspace layout where ``.git`` lives at
    a strict ancestor of ``project_dir`` (e.g. a uv-workspace package
    subdirectory). Matches the discovery logic in :func:`_ensure_git`.
    """
    if (project_dir / ".git").exists():
        return True
    return _find_ancestor_git(project_dir) is not None


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
    if not _has_git_repo(project_dir):
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
    if not _has_git_repo(project_dir):
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
    if not _has_git_repo(project_dir):
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

    ``git status --porcelain`` emits paths relative to the repo root and
    does not accept ``--relative``. On a consolidated workspace layout
    where *project_dir* is a package subdirectory (e.g. cwd =
    ``workspace/packages/mcloop`` and the repo root is ``workspace``),
    the porcelain prefix is stripped so paths remain package-relative,
    matching the ``--relative`` behavior of the sibling diff helpers.
    """
    prefix_result = _git(
        ["git", "rev-parse", "--show-prefix"],
        cwd=project_dir,
        label="worktree status prefix",
        silent=True,
    )
    prefix = ""
    if prefix_result.returncode == 0:
        prefix = prefix_result.stdout.rstrip("\n")
    result = _git(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        label="worktree status",
        silent=True,
    )
    if result.returncode != 0:
        return ""
    # Trim only trailing whitespace; the porcelain format is column-
    # positional (status chars at 0-1, space at 2, path at 3+), so
    # stripping leading whitespace would shift the first line and break
    # the prefix-based path slicing below.
    output = result.stdout.rstrip()
    if not prefix or not output:
        return output
    stripped_lines: list[str] = []
    for line in output.splitlines():
        # A status line is "XY <path>"; the path begins at index 3.
        if len(line) > 3 and line[3:].startswith(prefix):
            rest = line[3 + len(prefix) :]
            # Rename entries are "ORIG -> NEW"; strip the prefix from
            # the destination path too so both sides stay relative.
            arrow = " -> "
            if arrow in rest:
                before, after = rest.split(arrow, 1)
                if after.startswith(prefix):
                    after = after[len(prefix) :]
                rest = before + arrow + after
            stripped_lines.append(line[:3] + rest)
        else:
            stripped_lines.append(line)
    return "\n".join(stripped_lines)


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
    # emitting only the new name). --relative scopes output to the
    # subprocess cwd and emits paths relative to it, so on a
    # consolidated workspace layout (cwd = packages/mcloop, repo root
    # = workspace), paths stay package-relative.
    diff_result = _git(
        ["git", "diff", "--name-only", "--relative", "HEAD"],
        cwd=project_dir,
        label="changed files (diff)",
        silent=True,
    )
    # Fall back to diff without HEAD when HEAD doesn't exist yet
    # (fresh repo with no commits).
    if diff_result.returncode != 0:
        diff_result = _git(
            ["git", "diff", "--name-only", "--relative"],
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
    """Return list of files changed in *commit_sha*, excluding logs and metadata.

    ``--relative`` keeps paths relative to the subprocess cwd, so on a
    consolidated workspace layout the returned filenames are package-relative
    rather than rooted at the workspace.
    """
    result = _git(
        [
            "git",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--relative",
            "-r",
            commit_sha,
        ],
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


def _committed_files_since(project_dir: Path, base_sha: str) -> list[str]:
    """Return meaningful files changed between *base_sha* and HEAD.

    Used by the task verdict to detect work that earlier attempts of
    the same task committed via the rate-limit / session-limit
    checkpoint (see ``_checkpoint`` callsite in the task loop). When a
    task runs multiple attempts and an early attempt's work was
    committed before a later attempt observed "task already complete,"
    the working tree is clean at task end but the cumulative diff
    against the task-start SHA is not. This helper surfaces that
    cumulative diff so the verdict counts it as success rather than a
    no-op failure.

    Filters out the same metadata paths as :func:`_has_meaningful_changes`
    and :func:`_committed_files` (``logs/``, ``.mcloop/``, ``PLAN.md``).
    Returns an empty list when *base_sha* is empty, when HEAD is at
    *base_sha*, or when the repo lookup fails.
    """
    if not base_sha:
        return []
    if not _has_git_repo(project_dir):
        return []
    head_sha = _get_git_hash(project_dir)
    if not head_sha or head_sha == base_sha:
        return []
    result = _git(
        [
            "git",
            "diff",
            "--name-only",
            "--relative",
            f"{base_sha}..HEAD",
        ],
        cwd=project_dir,
        label="committed files since",
        silent=True,
    )
    if result.returncode != 0:
        return []
    files: list[str] = []
    seen: set[str] = set()
    for f in result.stdout.strip().splitlines():
        f = f.strip()
        if not f or f in seen:
            continue
        if f.startswith("logs/") or f.startswith(".mcloop/") or f == "PLAN.md":
            continue
        seen.add(f)
        files.append(f)
    return files


_TASK_BASELINE_REL = ".mcloop/task-baseline"


def _write_task_baseline(project_dir: Path, base_sha: str) -> None:
    """Persist the task's pre-edit baseline SHA for the in-session adapter.

    The run loop captures ``task_start_sha`` immediately after the
    pre-task checkpoint (see ``main.py``). Writing it to
    ``.mcloop/task-baseline`` lets the sanctioned in-session test adapter
    (``mcloop verify``) diff the agent's edits against the exact pre-edit
    tree, so its scoped verdict matches the loop's scoped gate. Empty
    SHAs are ignored. Best-effort: a write failure leaves no baseline,
    which the adapter treats as fail-closed.
    """
    if not base_sha:
        return
    path = project_dir / _TASK_BASELINE_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(base_sha.strip() + "\n")
    except OSError:
        pass


def _read_task_baseline(project_dir: Path) -> str:
    """Return the recorded task baseline SHA, or "" if none/unreadable."""
    path = project_dir / _TASK_BASELINE_REL
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _changed_files_since(project_dir: Path, base_sha: str) -> list[str] | None:
    """Return meaningful files changed since *base_sha*, working tree included.

    Unlike :func:`_committed_files_since` (committed range only), this
    diffs *base_sha* directly against the working tree so an in-session
    adapter sees edits the agent has not yet committed. Untracked files
    are added via ``git ls-files``. The same metadata paths as the
    sibling helpers (``logs/``, ``.mcloop/``, ``PLAN.md``) are filtered.

    Returns ``None`` -- the fail-closed signal for the adapter -- when
    *base_sha* is empty, the repo is missing, or the diff command errors.
    Returns ``[]`` when the baseline resolves but genuinely nothing
    changed, so the caller can distinguish "cannot resolve" from "no
    changes".
    """
    if not base_sha:
        return None
    if not _has_git_repo(project_dir):
        return None
    diff_result = _git(
        ["git", "diff", "--name-only", "--relative", base_sha],
        cwd=project_dir,
        label="changed files since baseline",
        silent=True,
    )
    if diff_result.returncode != 0:
        return None
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

    for line in diff_result.stdout.splitlines():
        _add(line)
    untracked_result = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label="changed files since baseline (untracked)",
        silent=True,
    )
    if untracked_result.returncode == 0:
        for line in untracked_result.stdout.splitlines():
            _add(line)
    return files


def read_file_at_head(project_dir: Path, path: str) -> str | None:
    """Return the contents of *path* at HEAD, or None if unavailable.

    Used by the run_checks behavioral gate to obtain a changed file's
    pre-edit baseline so the change can be classified as behavioral or
    provably non-behavioral. *path* is interpreted relative to
    *project_dir*: the ``HEAD:./<path>`` pathspec resolves relative to
    the subprocess cwd, so this works in both the standalone and the
    consolidated-workspace layout (where the repo root is an ancestor).

    Returns None on any git error -- no repo, a new file not yet in HEAD,
    or a path that does not exist at HEAD -- which the caller treats as
    fail-closed (the change cannot be proven non-behavioral). Runs
    quietly: this is a read used in a hot path, not a user action, so a
    miss is expected and must not print or notify.
    """
    if not _has_git_repo(project_dir):
        return None
    result = run_git_bounded(["git", "show", f"HEAD:./{path}"], project_dir)
    if result.returncode != 0:
        return None
    return result.stdout


def _get_committed_diff(project_dir: Path, commit_sha: str) -> str:
    """Return the diff introduced by *commit_sha*.

    ``--relative`` keeps the diff header paths relative to the subprocess
    cwd, so package-scoped callers see package-relative paths even when
    the repo root is a workspace ancestor.
    """
    result = _git(
        ["git", "diff", "--relative", f"{commit_sha}~1", commit_sha],
        cwd=project_dir,
        label="committed diff",
        silent=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _split_nul_paths(stdout: str) -> list[str]:
    """Split NUL-delimited git ``-z`` output into a list of paths.

    ``-z`` output is exempt from ``core.quotepath`` escaping and keeps
    filenames containing newlines intact, so paths round-trip verbatim
    to checkout/unlink operations.
    """
    return [p for p in stdout.split("\0") if p]


def _snapshot_worktree(project_dir: Path) -> tuple[list[str], list[str]]:
    """Snapshot modified and untracked files in the working tree.

    Returns (modified, untracked) where each is a list of file paths
    relative to the project root. Used at batch start so rollback can
    preserve pre-existing dirty state.
    """
    modified: list[str] = []
    untracked: list[str] = []
    diff_result = _git(
        ["git", "diff", "--name-only", "--relative", "-z"],
        cwd=project_dir,
        label="snapshot modified",
    )
    if diff_result.returncode == 0:
        modified = _split_nul_paths(diff_result.stdout)
    # Staged changes are dirty state too: `git diff` alone is
    # unstaged-only and `ls-files --others` skips the index, so a file
    # the user had `git add`ed before the batch was invisible to the
    # snapshot -- and a file the BATCH staged escaped rollback entirely,
    # then surfaced in the next attempt's diff-vs-HEAD and was laundered
    # into its commit. Fold the staged set into `modified`.
    staged_result = _git(
        ["git", "diff", "--cached", "--name-only", "--relative", "-z"],
        cwd=project_dir,
        label="snapshot staged",
    )
    if staged_result.returncode == 0:
        for f in _split_nul_paths(staged_result.stdout):
            if f not in modified:
                modified.append(f)
    untracked_result = _git(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=project_dir,
        label="snapshot untracked",
    )
    if untracked_result.returncode == 0:
        untracked = _split_nul_paths(untracked_result.stdout)
    return modified, untracked


def _rollback_batch_changes(
    project_dir: Path,
    pre_batch_modified: list[str],
    pre_batch_untracked: list[str],
) -> None:
    """Discard uncommitted changes a batch attempt made, preserving
    files that were already dirty before the batch started.

    Selective rollback: only files the batch touched are reverted or
    removed. NUL-delimited (``-z``) output is exempt from
    ``core.quotepath`` escaping so non-ASCII and newline-containing
    names round-trip verbatim. ``--relative`` matches
    :func:`_snapshot_worktree`'s diff exactly -- without it, a project
    dir nested inside a larger repo (a workspace package) yields
    repo-root-prefixed paths that never match the snapshot, silently
    defeating both the preserve set and the checkout.

    Every non-success exit from a batch attempt must call this before
    returning: the retry loop re-snapshots the worktree on re-entry, so
    partial edits left behind get absorbed into the next attempt's
    "pre-batch" baseline -- permanently shielded from rollback and
    eventually committed unreviewed.
    """
    import shutil

    pre_mod_set = set(pre_batch_modified)
    # First unstage anything the batch itself staged: `git checkout -- f`
    # restores the worktree from the INDEX, so a batch-staged edit would
    # survive it, and a batch-staged NEW file is invisible to both the
    # unstaged diff and `ls-files --others`. After restore --staged the
    # file falls through to the ordinary modified/untracked passes below.
    current_staged = _git(
        ["git", "diff", "--cached", "--name-only", "--relative", "-z"],
        cwd=project_dir,
        label="batch rollback staged",
    )
    for f in _split_nul_paths(current_staged.stdout):
        if f not in pre_mod_set:
            _git(
                ["git", "restore", "--staged", "--", f],
                cwd=project_dir,
                label=f"batch rollback unstage {f}",
            )
    current_modified = _git(
        ["git", "diff", "--name-only", "--relative", "-z"],
        cwd=project_dir,
        label="batch rollback diff",
    )
    for f in _split_nul_paths(current_modified.stdout):
        if f not in pre_mod_set:
            _git(
                ["git", "checkout", "--", f],
                cwd=project_dir,
                label=f"batch rollback {f}",
            )
    # Remove only new untracked files created by the batch.
    current_untracked = _git(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=project_dir,
        label="batch rollback untracked",
    )
    pre_untracked_set = set(pre_batch_untracked)
    for f in _split_nul_paths(current_untracked.stdout):
        if f not in pre_untracked_set:
            fpath = project_dir / f
            # A symlink must be unlinked even when it points at a
            # directory: is_dir() follows the link and rmtree raises
            # on symlinks (and would delete the link target's contents).
            if fpath.is_symlink() or fpath.is_file():
                fpath.unlink()
            elif fpath.is_dir():
                shutil.rmtree(fpath)


def _get_git_hash(project_dir: Path) -> str:
    """Return current HEAD commit hash."""
    if not _has_git_repo(project_dir):
        return ""
    result = _git(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        label="get HEAD hash",
    )
    return result.stdout.strip()
