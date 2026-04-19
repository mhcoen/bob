"""Deferred CLAUDE.md sync with pending queue and cap logic."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from mcloop.claude_md_check import SyncResult, auto_update_claude_md, check_claude_md_freshness
from mcloop.git_ops import _commit, _committed_files
from mcloop.notify import notify

_PENDING_FILENAME = "claude_md_pending.json"


def _pending_path(project_dir: Path) -> Path:
    return project_dir / ".mcloop" / _PENDING_FILENAME


def _run_sync_in_background(
    project_dir: Path,
    commit_sha: str,
    task_label: str,
) -> None:
    """Body of the daemon thread that performs the LLM call.

    Catches all exceptions so a failing thread does not block the main loop.
    """
    try:
        result = auto_update_claude_md(project_dir, commit_sha)
    except Exception as exc:
        print(
            f"  CLAUDE.md sync background thread failed: {exc}",
            flush=True,
        )
        return

    if result is SyncResult.TRANSIENT_FAILED:
        pending = _pending_path(project_dir)
        if pending.exists():
            short_sha = commit_sha[:7] if commit_sha else "unknown"
            msg = (
                f"mcloop: CLAUDE.md sync 2 commits behind. "
                f"DeepSeek (x2) and Sonnet both failed. "
                f"Run paused at {task_label or short_sha}."
            )
            notify(msg, level="error")
            return

        pending.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "commit_sha": commit_sha,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attempts": 1,
        }
        pending.write_text(json.dumps(entry, indent=2) + "\n")
        print(
            f"  CLAUDE.md sync deferred (pending for {commit_sha[:7]})",
            flush=True,
        )

    elif result is SyncResult.OK:
        pending = _pending_path(project_dir)
        if pending.exists():
            pending.unlink()


def handle_sync(
    project_dir: Path,
    commit_sha: str,
    *,
    task_label: str = "",
) -> threading.Thread | None:
    """Kick off CLAUDE.md sync after a code commit.

    The freshness check runs synchronously; the LLM call (which can take
    5-15 seconds) runs in a daemon thread so the main loop is not blocked.

    Returns the spawned thread, or None if there was no work to do.  The
    thread reference is provided mainly so tests can ``.join()`` on it;
    production callers do not need to wait for completion.
    """
    sha = commit_sha if isinstance(commit_sha, str) else ""
    changed = _committed_files(project_dir, sha) if sha else []
    if check_claude_md_freshness(changed, project_dir):
        return None

    thread = threading.Thread(
        target=_run_sync_in_background,
        args=(project_dir, commit_sha, task_label),
        daemon=True,
        name="mcloop-claude-md-sync",
    )
    thread.start()
    return thread


def reconcile_pending(project_dir: Path) -> None:
    """Attempt to reconcile any pending CLAUDE.md sync entry.

    Called at the top of run_loop after interrupt checks.  If no
    pending file exists, this is a no-op.
    """
    pending = _pending_path(project_dir)
    if not pending.exists():
        return

    try:
        entry = json.loads(pending.read_text())
    except (json.JSONDecodeError, OSError):
        pending.unlink(missing_ok=True)
        return

    commit_sha = entry.get("commit_sha", "")
    short_sha = commit_sha[:7] if commit_sha else "unknown"
    print(f"  Reconciling deferred CLAUDE.md sync for {short_sha}...", flush=True)

    result = auto_update_claude_md(project_dir, commit_sha)

    if result is SyncResult.OK:
        # Commit the CLAUDE.md update.
        try:
            _commit(project_dir, f"docs: sync CLAUDE.md (deferred from {short_sha})")
        except RuntimeError as exc:
            print(f"  CLAUDE.md reconcile commit failed: {exc}", flush=True)
        pending.unlink(missing_ok=True)
        print(f"  CLAUDE.md reconciled for {short_sha}", flush=True)

    elif result is SyncResult.TRANSIENT_FAILED:
        # Increment attempts, leave entry in place.
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_attempt"] = datetime.now(timezone.utc).isoformat()
        pending.write_text(json.dumps(entry, indent=2) + "\n")
        print(
            f"  CLAUDE.md reconcile failed (attempt {entry['attempts']})",
            flush=True,
        )

    else:
        # NO_WORK or PERMANENT_FAILED — remove the pending entry.
        pending.unlink(missing_ok=True)
