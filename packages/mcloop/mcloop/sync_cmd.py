"""Sync subcommand: update PLAN.md to match the codebase."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path


def _cmd_sync(checklist_path: Path, *, dry_run: bool = False) -> None:
    """Launch a Claude Code session with full project context for sync analysis."""
    from mcloop.git_ops import _ensure_git
    from mcloop.main import _kill_orphan_sessions
    from mcloop.runner import run_sync

    project_dir = checklist_path.parent
    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    log_dir = project_dir / "logs"
    mode = "(dry run)" if dry_run else ""
    print(f"Syncing PLAN.md with codebase {mode}...".strip(), flush=True)
    original = checklist_path.read_text() if checklist_path.exists() else ""
    import mcloop.runner as _runner

    _runner._SUPPRESS_ALL_TOOLS = False
    result = run_sync(project_dir, log_dir)
    _runner._SUPPRESS_ALL_TOOLS = True
    if not result.success:
        print(f"sync: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    proposed = checklist_path.read_text() if checklist_path.exists() else ""
    if dry_run:
        if proposed != original:
            _show_diff(original, proposed, checklist_path.name)
        else:
            print("No changes to PLAN.md.")
        checklist_path.write_text(original)
        print("Dry run: no changes applied.")
        return
    if not _confirm_sync_changes(checklist_path, original, proposed):
        checklist_path.write_text(original)
        print("Changes discarded.")
    elif proposed != original:
        print("Changes applied.")


def _show_diff(original: str, proposed: str, filename: str = "PLAN.md") -> None:
    """Print a unified diff between original and proposed content."""
    lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    print("".join(lines), end="")


def _confirm_sync_changes(
    checklist_path: Path,
    original: str,
    proposed: str,
    *,
    _input=input,
) -> bool:
    """Show a diff of proposed PLAN.md changes and prompt the user to confirm.

    Returns True if changes should be kept, False if they should be discarded.
    """
    if proposed == original:
        print("No changes to PLAN.md.")
        return True
    _show_diff(original, proposed, checklist_path.name)
    answer = _input("\nApply these changes? [y/N] ").strip().lower()
    return answer == "y"
