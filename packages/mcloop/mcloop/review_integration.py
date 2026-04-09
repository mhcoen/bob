"""Reviewer integration: spawn, collect, and manage reviewer subprocesses.

reviewer.py owns the review logic itself (API calls, diff parsing,
finding extraction). This module owns spawning reviewer subprocesses,
collecting their results from disk, and managing their lifecycle
within run_loop.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
import time
from pathlib import Path

from mcloop import formatting
from mcloop.errors import _insert_bugs_section
from mcloop.session_context import SessionContext

_reviewer_procs: list[subprocess.Popen] = []


def _get_commit_hash(project_dir: Path) -> str:
    """Return the current HEAD commit hash."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    return result.stdout.strip()


def _spawn_reviewer(project_dir: Path) -> None:
    """Spawn a background reviewer process for the latest commit."""
    commit_hash = _get_commit_hash(project_dir)
    if not commit_hash:
        return
    print(
        formatting.system_msg(f"Reviewer: analyzing {commit_hash[:8]}..."),
        flush=True,
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcloop.reviewer", commit_hash, str(project_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _reviewer_procs.append(proc)


def _cleanup_stale_reviews(project_dir: Path) -> None:
    """Remove .mcloop/reviews/*.json files older than 24 hours."""
    reviews_dir = project_dir / ".mcloop" / "reviews"
    if not reviews_dir.exists():
        return
    cutoff = time.time() - 86400
    for f in reviews_dir.iterdir():
        if f.suffix == ".json":
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def _purge_all_reviews(project_dir: Path) -> None:
    """Remove all .mcloop/reviews/*.json files.

    Called at startup when the reviewer is disabled to prevent stale
    findings from previous runs from being collected.
    """
    reviews_dir = project_dir / ".mcloop" / "reviews"
    if not reviews_dir.exists():
        return
    for f in reviews_dir.iterdir():
        if f.suffix == ".json":
            try:
                f.unlink()
            except OSError:
                pass


def _collect_review_findings(
    project_dir: Path,
    checklist_path: Path,
    ctx: SessionContext,
) -> None:
    """Scan .mcloop/reviews/ for completed reviews.

    High-confidence findings are added to session context.
    If a single commit has 3+ high-confidence error-severity findings,
    a fix task is inserted into the Bugs section of PLAN.md instead.
    """
    reviews_dir = project_dir / ".mcloop" / "reviews"
    if not reviews_dir.exists():
        return
    for f in list(reviews_dir.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            raw = _json.loads(f.read_text())
        except (OSError, _json.JSONDecodeError):
            f.unlink(missing_ok=True)
            continue
        f.unlink(missing_ok=True)
        # Support both formats: bare list (old) and dict with
        # "findings" key (new, includes elapsed_seconds).
        if isinstance(raw, dict):
            data = raw.get("findings", [])
            elapsed = raw.get("elapsed_seconds", 0)
            commit = (raw.get("commit") or f.stem)[:8]
        elif isinstance(raw, list):
            data = raw
            elapsed = 0
            commit = f.stem[:8]
        else:
            continue
        elapsed_str = f" [{elapsed:.0f}s]" if elapsed else ""
        high_conf = [
            item for item in data if isinstance(item, dict) and item.get("confidence") == "high"
        ]
        if not high_conf:
            print(
                formatting.system_msg(f"Reviewer: {commit} clean{elapsed_str}"),
                flush=True,
            )
            continue
        high_errors = [item for item in high_conf if item.get("severity") == "error"]
        if len(high_errors) >= 3:
            # Insert one task per finding into Bugs section
            tasks = []
            for item in high_errors:
                desc = item.get("description", "")
                tasks.append(f"- [ ] Fix review finding from commit {commit[:8]}: {desc}")
            _insert_bugs_section(checklist_path, tasks)
            print(
                formatting.system_msg(
                    f"Reviewer: {len(high_errors)} critical findings"
                    f" from {commit}{elapsed_str} → added to Bugs"
                ),
                flush=True,
            )
        else:
            # Add to session context
            lines = ["Review findings from previous tasks:"]
            for item in high_conf:
                file = item.get("file", "?")
                desc = item.get("description", "")
                sev = item.get("severity", "info")
                lines.append(f"  [{sev}] {file}: {desc}")
            ctx.add_user_input("\n".join(lines))
            print(
                formatting.system_msg(
                    f"Reviewer: {len(high_conf)} finding(s)"
                    f" from {commit}{elapsed_str} added to context"
                ),
                flush=True,
            )


def _terminate_reviewers() -> None:
    """Terminate all active reviewer subprocesses."""
    for proc in _reviewer_procs:
        try:
            proc.terminate()
        except OSError:
            pass
    _reviewer_procs.clear()
