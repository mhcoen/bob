"""Deferred CLAUDE.md sync with pending queue and cap logic."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcloop.claude_md_check import SyncResult, auto_update_claude_md, check_claude_md_freshness
from mcloop.git_ops import _changed_files, _commit
from mcloop.notify import notify

_PENDING_FILENAME = "claude_md_pending.json"


def _pending_path(project_dir: Path) -> Path:
    return project_dir / ".mcloop" / _PENDING_FILENAME


def handle_sync(
    project_dir: Path,
    commit_sha: str,
    *,
    task_label: str = "",
) -> SyncResult:
    """Run CLAUDE.md sync after a code commit.

    On TRANSIENT_FAILED, writes a pending entry.  If a pending entry
    already exists (cap=1 exceeded), halts with a Telegram notification.

    Returns the SyncResult from auto_update_claude_md, or NO_WORK if
    CLAUDE.md was already fresh.
    """
    changed = _changed_files(project_dir)
    if check_claude_md_freshness(changed, project_dir):
        return SyncResult.NO_WORK

    result = auto_update_claude_md(project_dir)

    if result is SyncResult.TRANSIENT_FAILED:
        pending = _pending_path(project_dir)
        if pending.exists():
            # Cap exceeded — second consecutive failure.
            short_sha = commit_sha[:7] if commit_sha else "unknown"
            msg = (
                f"mcloop: CLAUDE.md sync 2 commits behind. "
                f"DeepSeek (x2) and Sonnet both failed. "
                f"Run paused at {task_label or short_sha}."
            )
            notify(msg, level="error")
            raise SystemExit(msg)

        # Write pending entry.
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
        # Sync succeeded — clear any pending entry from a prior run.
        pending = _pending_path(project_dir)
        if pending.exists():
            pending.unlink()

    return result


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

    result = auto_update_claude_md(project_dir)

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
