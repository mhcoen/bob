"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json as _json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from mcloop.checklist import (
    Task,
    check_off,
    current_stage,
    find_next,
    get_stages,
    mark_failed,
    parse,
    parse_description,
    stage_status,
)
from mcloop.checks import detect_build, detect_run, get_check_commands, run_checks
from mcloop.notify import notify
from mcloop.ratelimit import (
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    is_session_limited,
    wait_for_reset,
)
from mcloop.runner import bugs_md_has_bugs, run_audit, run_bug_fix, run_sync, run_task


def main() -> None:
    def _handle_sigint(sig, frame):
        print("\nInterrupted.", flush=True)
        from mcloop.runner import _active_process
        if _active_process is not None:
            try:
                _active_process.kill()
            except OSError:
                pass
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTSTP, _handle_sigint)
    _main()


def _main() -> None:
    args = _parse_args()
    checklist_path = Path(args.file).resolve()

    if not checklist_path.exists():
        print(f"Checklist not found: {checklist_path}", file=sys.stderr)
        sys.exit(1)

    if args.command == "sync":
        _cmd_sync(checklist_path)
        return

    if args.command == "audit":
        _cmd_audit(checklist_path)
        return

    if args.dry_run:
        _dry_run(parse(checklist_path))
        return

    run_loop(
        checklist_path,
        max_retries=args.max_retries,
        model=args.model,
        no_audit=args.no_audit,
    )


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    enabled_clis: tuple[str, ...] = ("claude",),
    model: str | None = None,
    no_audit: bool = False,
) -> list[str]:
    """Run the main loop. Returns list of stuck task texts."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    project_checks = get_check_commands(project_dir)

    _checkpoint(project_dir)

    # Clean up stale pending files from previous runs
    pending_dir = project_dir / ".mcloop" / "pending"
    if pending_dir.exists():
        for f in pending_dir.iterdir():
            f.unlink(missing_ok=True)

    notes_snapshot = _snapshot_notes(project_dir)
    ctx = SessionContext()
    run_start = time.monotonic()
    completed: list[str] = []
    failed_task: str | None = None
    failed_reason: str = ""

    while True:
        tasks = parse(checklist_path)
        task = find_next(tasks)
        if task is None:
            break

        # If this is a parent with all children done, just check it off
        if task.children and all(c.checked for c in task.children):
            check_off(checklist_path, task)
            continue

        cli = get_available_cli(rate_state, enabled_clis=enabled_clis)
        if cli is None:
            cli = wait_for_reset(rate_state, notify, enabled_clis=enabled_clis)

        label = _task_label(tasks, task)
        has_subtasks = "." in label
        ctx.update_group(label, has_subtasks)
        _checkpoint(
            project_dir,
            next_task=f"{label}) {task.text}",
        )
        print(f"\n>>> Task {label}) {task.text} (using {cli})")

        task_start = time.monotonic()
        success = False
        attempt = 0
        last_error = ""
        while attempt < max_retries:
            attempt += 1
            result = run_task(
                task.text,
                cli,
                project_dir,
                log_dir,
                description,
                task_label=label,
                model=model,
                prior_errors=last_error,
                session_context=ctx.text(),
                check_commands=project_checks,
            )

            if is_session_limited(
                result.output, result.exit_code,
            ):
                _checkpoint(project_dir)
                total = time.monotonic() - run_start
                _print_summary(
                    completed, None, "",
                    parse(checklist_path), total,
                    project_dir, notes_snapshot,
                )
                notify(
                    "Session limit reached.",
                    level="warning",
                )
                print(
                    "\n>>> Session limit reached."
                    " Waiting for reset."
                    " Press Ctrl-C to exit.",
                    flush=True,
                )
                try:
                    while True:
                        time.sleep(60)
                except KeyboardInterrupt:
                    print("\nExiting.", flush=True)
                return [task.text]

            if is_rate_limited(result.output, result.exit_code):
                rate_state.mark_limited(cli)
                notify(f"Rate-limited on {cli}.", level="warning")
                cli = get_available_cli(rate_state, enabled_clis=enabled_clis)
                if cli is None:
                    cli = wait_for_reset(rate_state, notify, enabled_clis=enabled_clis)
                attempt -= 1  # don't count rate-limit as a real attempt
                continue

            if not result.success:
                last_error = _tail(result.output, 50)
                print(
                    f"\n!!! Task failed (attempt {attempt}/{max_retries})",
                    flush=True,
                )
                print(
                    f"    Exit code: {result.exit_code}",
                    flush=True,
                )
                _print_error_tail(result.output)
                notify(
                    f"Task failed (attempt {attempt}/{max_retries}): " + task.text,
                    level="error",
                )
                continue

            if not _has_meaningful_changes(project_dir):
                last_error = "Task produced no file changes"
                print(
                    f"\n!!! No-op task (attempt {attempt}/{max_retries}): {task.text}",
                    flush=True,
                )
                notify(
                    f"No-op task (attempt {attempt}/{max_retries}): " + task.text,
                    level="error",
                )
                continue

            check_result = run_checks(project_dir)
            if check_result.passed:
                _commit(project_dir, task.text)
                check_off(checklist_path, task)
                elapsed = _format_elapsed(time.monotonic() - task_start)
                completed.append(f"{label}) {task.text}")
                print(
                    f"\n>>> Completed {label}) [{elapsed}]",
                    flush=True,
                )
                ctx.add(
                    label,
                    task.text,
                    elapsed,
                    result.output,
                )
                notify(f"Completed: {task.text}")
                success = True
                break
            else:
                last_error = f"Command: {check_result.command}\n" + _tail(check_result.output, 50)
                print(
                    f"\n!!! Checks failed "
                    f"(attempt {attempt}/{max_retries}): "
                    f"{check_result.command}",
                    flush=True,
                )
                _print_error_tail(check_result.output)
                notify(
                    f"Checks failed (attempt {attempt}/{max_retries}): " + task.text,
                    level="error",
                )

        if not success:
            elapsed = _format_elapsed(time.monotonic() - task_start)
            mark_failed(checklist_path, task)
            failed_task = f"{label}) {task.text} [{elapsed}]"
            failed_reason = last_error
            notify(
                f"Giving up on: {task.text}",
                level="error",
            )
            total = time.monotonic() - run_start
            _print_summary(
                completed,
                failed_task,
                failed_reason,
                parse(checklist_path),
                total,
                project_dir,
                notes_snapshot,
            )
            return [task.text]

    # Check if we stopped at a stage boundary
    final_tasks = parse(checklist_path)
    status = stage_status(final_tasks)

    if status.startswith("stage_complete:"):
        done_stage = status.split(":", 1)[1]
        next_stg = current_stage(parse(checklist_path))
        _run_build(project_dir)
        total = time.monotonic() - run_start
        _print_summary(
            completed,
            None,
            "",
            final_tasks,
            total,
            project_dir,
            notes_snapshot,
            completed_stage=done_stage,
        )
        msg = f"{done_stage} complete."
        if next_stg:
            msg += f" Run mcloop again to start {next_stg}."
        notify(msg)
        return []

    if not no_audit:
        _run_audit_fix_cycle(
            project_dir,
            log_dir,
            model=model,
        )

    _run_build(project_dir)

    total = time.monotonic() - run_start
    _print_summary(
        completed,
        None,
        "",
        [],
        total,
        project_dir,
        notes_snapshot,
    )
    notify("All tasks completed!")
    return []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loop: grind through a markdown checklist")
    parser.add_argument("--file", default="PLAN.md", help="Checklist file (default: PLAN.md)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would run")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    parser.add_argument("--model", default=None, help="Claude model to use (e.g., opus, sonnet)")
    parser.add_argument(
        "--no-audit", action="store_true", help="Skip the post-completion bug audit cycle"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("sync", help="Sync PLAN.md with the codebase")
    subparsers.add_parser("audit", help="Audit the codebase and write BUGS.md")
    return parser.parse_args()


def _cmd_audit(checklist_path: Path) -> None:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    result = run_audit(project_dir, log_dir)
    if not result.success:
        print(f"audit: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    bugs_path = project_dir / "BUGS.md"
    if bugs_path.exists():
        print(bugs_path.read_text())
    else:
        print("audit: BUGS.md was not written", file=sys.stderr)


def _cmd_sync(checklist_path: Path) -> None:
    """Launch a Claude Code session with full project context for sync analysis."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    original = checklist_path.read_text() if checklist_path.exists() else ""
    result = run_sync(project_dir, log_dir)
    if not result.success:
        print(f"sync: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    proposed = checklist_path.read_text() if checklist_path.exists() else ""
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


def _dry_run(tasks) -> None:
    """Print the task tree without executing anything."""
    stages = get_stages(tasks)
    last_stage = ""

    def _print(task_list, depth=0):
        nonlocal last_stage
        for t in task_list:
            if stages and t.stage != last_stage:
                last_stage = t.stage
                print(f"\n  [{t.stage}]")
            marker = "[x]" if t.checked else "[ ]"
            print(f"{'  ' * depth}- {marker} {t.text}")
            if t.children:
                _print(t.children, depth + 1)

    _print(tasks)
    active = current_stage(tasks)
    next_task = find_next(tasks)
    if next_task:
        label = f" (in {active})" if active else ""
        print(f"\nNext task{label}: {next_task.text}")
    elif active is None and stages:
        print("\nAll stages complete.")
    else:
        print("\nNo unchecked tasks remaining.")


def _format_elapsed(seconds: float) -> str:
    """Format seconds into human-readable elapsed time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m {secs}s"


def _tail(text: str, max_lines: int = 50) -> str:
    """Return the last N lines of text."""
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def _print_summary(
    completed: list[str],
    failed_task: str | None,
    failed_reason: str,
    remaining_tasks: list[Task],
    total_seconds: float = 0,
    project_dir: Path | None = None,
    notes_snapshot: tuple[str, int] | None = None,
    completed_stage: str = "",
) -> None:
    """Print a summary of what McLoop did."""
    print("\n" + "=" * 40, flush=True)
    print("McLoop Summary", flush=True)
    print("=" * 40, flush=True)
    if total_seconds > 0:
        print(
            f"Total time: {_format_elapsed(total_seconds)}",
            flush=True,
        )

    if completed:
        print(
            f"Completed: {len(completed)} task(s)",
            flush=True,
        )
        for item in completed:
            print(f"  {item}", flush=True)

    if failed_task:
        print(f"\nFailed: {failed_task}", flush=True)
        if failed_reason:
            for line in failed_reason.splitlines()[:10]:
                print(f"  {line}", flush=True)

    # Count remaining unchecked tasks
    def _count_unchecked(tasks: list[Task]) -> int:
        n = 0
        for t in tasks:
            if not t.checked and not t.failed:
                n += 1
            n += _count_unchecked(t.children)
        return n

    remaining = _count_unchecked(remaining_tasks)
    if remaining:
        print(
            f"\nRemaining: {remaining} task(s)",
            flush=True,
        )

    if completed_stage:
        print(
            f"\n>>> {completed_stage} complete. Run mcloop again for the next stage.",
            flush=True,
        )
    elif not completed and not failed_task:
        print(
            "All tasks were already complete.",
            flush=True,
        )

    suggestions = _whitelist_suggestions()
    if suggestions:
        print(
            "\nWhitelist suggestions (approved this session):",
            flush=True,
        )
        print(
            "  Add to permissions.allow in",
            flush=True,
        )
        print(
            "    ~/.claude/settings.json (global)",
            flush=True,
        )
        print(
            "    .claude/settings.json (project)",
            flush=True,
        )
        for s in suggestions:
            print(f'  "{s}",', flush=True)

    if project_dir:
        run_cmd = detect_run(project_dir)
        if run_cmd:
            print(
                f"\nTo run: {run_cmd}",
                flush=True,
            )

    if project_dir:
        _print_notes_update(
            project_dir,
            notes_snapshot,
        )

    print("=" * 40, flush=True)


SESSION_FILE = Path.home() / ".claude" / "telegram-hook-session.json"
SETTINGS_FILE = Path.home() / ".claude" / "settings.json"


def _whitelist_suggestions() -> list[str]:
    """Read session-approved patterns and suggest allowlist entries."""
    try:
        data = _json.loads(SESSION_FILE.read_text())
        patterns = data.get("patterns", [])
    except (OSError, _json.JSONDecodeError):
        return []
    if not patterns:
        return []

    # Load current allowlist
    try:
        settings = _json.loads(SETTINGS_FILE.read_text())
        allow = settings.get("permissions", {}).get("allow", [])
    except (OSError, _json.JSONDecodeError):
        allow = []

    # Never suggest whitelisting dangerous commands
    dangerous = {
        "rm",
        "rmdir",
        "kill",
        "killall",
        "pkill",
        "chmod",
        "chown",
        "sudo",
        "su",
        "dd",
        "mkfs",
        "mv",
        "shutdown",
        "reboot",
    }

    allow_set = set(allow)
    suggestions = []
    for pattern in sorted(patterns):
        # Convert "Bash:ruff check ." to "Bash(ruff check:*)"
        if ":" in pattern:
            tool, arg = pattern.split(":", 1)
            first_word = arg.split()[0] if arg.split() else arg
            if first_word in dangerous:
                continue
            rule = f"{tool}({first_word}:*)"
        else:
            rule = pattern
        if rule not in allow_set:
            suggestions.append(rule)
            allow_set.add(rule)  # dedup
    return suggestions


def _print_error_tail(output: str, max_lines: int = 30) -> None:
    """Print the last N lines of output to help diagnose failures."""
    lines = output.strip().splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    if tail:
        print("    --- last output ---", flush=True)
        for line in tail:
            print(f"    {line}", flush=True)
        print("    ---", flush=True)


def _task_label(tasks: list[Task], target: Task) -> str:
    """Return a label like '3' or '3.2' for a task's position in the tree."""

    def _search(task_list: list[Task], prefix: str) -> str | None:
        for i, task in enumerate(task_list, 1):
            label = f"{prefix}{i}" if prefix else str(i)
            if task is target:
                return label
            if task.children:
                found = _search(task.children, f"{label}.")
                if found:
                    return found
        return None

    return _search(tasks, "") or "?"


def _has_meaningful_changes(project_dir: Path) -> bool:
    """Check for file changes beyond PLAN.md and logs/.

    Uses git status --porcelain which works even in repos
    with no commits (no HEAD).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return True
        all_files = []
        for line in result.stdout.strip().splitlines():
            # porcelain format: XY filename
            if len(line) > 3:
                all_files.append(line[3:])
        meaningful = [
            f for f in all_files
            if f
            and not f.startswith("logs/")
            and not f.startswith(".mcloop/")
            and f != "PLAN.md"
        ]
        return len(meaningful) > 0
    except Exception:
        return True


def _snapshot_notes(
    project_dir: Path,
) -> tuple[str, int]:
    """Capture hash and line count of NOTES.md."""
    notes_path = project_dir / "NOTES.md"
    if not notes_path.exists():
        return ("", 0)
    content = notes_path.read_text()
    h = hashlib.md5(content.encode()).hexdigest()
    return (h, len(content.splitlines()))


def _print_notes_update(
    project_dir: Path,
    snapshot: tuple[str, int] | None,
) -> None:
    """Show NOTES.md changes since snapshot."""
    notes_path = project_dir / "NOTES.md"
    if not notes_path.exists():
        return
    content = notes_path.read_text()
    current_hash = hashlib.md5(content.encode()).hexdigest()
    lines = content.splitlines()

    old_hash, old_count = snapshot or ("", 0)

    if old_hash == "" and old_count == 0:
        # NOTES.md is new this run
        print(
            f"\nNOTES.md created ({len(lines)} lines). Review for observations.",
            flush=True,
        )
    elif current_hash != old_hash:
        new_count = len(lines) - old_count
        if new_count > 0:
            print(
                f"\nNOTES.md updated ({new_count} new lines).",
                flush=True,
            )
        else:
            print(
                "\nNOTES.md was modified.",
                flush=True,
            )
        # Show the last entry header
        for line in reversed(lines):
            if line.startswith("## "):
                print(
                    f"  Last entry: {line}",
                    flush=True,
                )
                break
    # If hash unchanged, say nothing


def _run_build(project_dir: Path) -> None:
    """Run the auto-detected or configured build command."""
    build_cmd = detect_build(project_dir)
    if not build_cmd:
        return
    print(
        f"\n>>> Building: {build_cmd}",
        flush=True,
    )
    try:
        result = subprocess.run(
            shlex.split(build_cmd),
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            print(">>> Build succeeded", flush=True)
        else:
            print(
                f"!!! Build failed (exit {result.returncode})",
                flush=True,
            )
            _print_error_tail(result.stdout + result.stderr)
    except Exception as e:
        print(f"!!! Build error: {e}", flush=True)


MAX_FLAT_CONTEXT_ENTRIES = 5


class SessionContext:
    """Rolling context shared between task sessions within a run.

    Resets when moving to a new top-level task group.
    For flat tasks (no subtasks), keeps the last N entries.
    """

    def __init__(self) -> None:
        self._entries: list[str] = []
        self._current_group: str = ""

    def update_group(self, label: str, has_subtasks: bool) -> None:
        """Reset context if we moved to a new top-level group."""
        group = label.split(".")[0]
        if group != self._current_group:
            self._entries.clear()
            self._current_group = group
        if not has_subtasks:
            # Flat tasks: trim to last N
            if len(self._entries) > MAX_FLAT_CONTEXT_ENTRIES:
                self._entries = self._entries[-MAX_FLAT_CONTEXT_ENTRIES:]

    def add(
        self,
        label: str,
        task_text: str,
        elapsed: str,
        output: str,
    ) -> None:
        """Append a brief summary of a completed task."""
        # Extract the last few meaningful lines
        lines = output.strip().splitlines()
        summary_lines = []
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip JSON blobs from stream output
            if stripped.startswith("{"):
                continue
            summary_lines.append(stripped)
            if len(summary_lines) >= 3:
                break
        summary_lines.reverse()
        summary = "; ".join(summary_lines)[:200]
        entry = f"[{label}] {task_text} ({elapsed})"
        if summary:
            entry += f": {summary}"
        self._entries.append(entry)

    def text(self) -> str:
        """Return context string for inclusion in prompts."""
        return "\n".join(self._entries)


AUDIT_HASH_FILE = ".mcloop-last-audit"


def _get_git_hash(project_dir: Path) -> str:
    """Return current HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _should_skip_audit(project_dir: Path) -> bool:
    """Skip audit if no source files changed since last audit."""
    hash_file = project_dir / AUDIT_HASH_FILE
    if not hash_file.exists():
        return False
    last_hash = hash_file.read_text().strip()
    if not last_hash:
        return False
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", last_hash, "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        changed = [
            f
            for f in result.stdout.strip().splitlines()
            if f and not f.startswith("logs/") and f != "PLAN.md" and f != AUDIT_HASH_FILE
        ]
        return len(changed) == 0
    except Exception:
        return False


def _save_audit_hash(project_dir: Path) -> None:
    """Write current HEAD hash to .mcloop-last-audit."""
    h = _get_git_hash(project_dir)
    if h:
        (project_dir / AUDIT_HASH_FILE).write_text(h + "\n")


def _run_audit_fix_cycle(
    project_dir: Path,
    log_dir: Path,
    model: str | None = None,
) -> None:
    """Run one audit session; if bugs are found, run a fix session then delete BUGS.md."""
    if _should_skip_audit(project_dir):
        print(
            "\n>>> Audit skipped (no changes since last audit)",
            flush=True,
        )
        return

    bugs_path = project_dir / "BUGS.md"

    # Resume from existing BUGS.md if present
    if bugs_path.exists():
        bugs_content = bugs_path.read_text()
        if bugs_md_has_bugs(bugs_content):
            print(
                "\n>>> Found existing BUGS.md, resuming fix cycle...",
                flush=True,
            )
        else:
            print(
                "\n>>> Existing BUGS.md has no bugs",
                flush=True,
            )
            bugs_path.unlink()
            _save_audit_hash(project_dir)
            return
    else:
        print("\n>>> Running bug audit...", flush=True)
        audit_result = run_audit(
            project_dir,
            log_dir,
            model=model,
        )
        if not audit_result.success:
            print(
                f"audit: session exited with code {audit_result.exit_code}, skipping fix",
                flush=True,
            )
            return

        if not bugs_path.exists():
            print(
                "audit: BUGS.md not written, skipping fix",
                flush=True,
            )
            return

        bugs_content = bugs_path.read_text()
        if not bugs_md_has_bugs(bugs_content):
            print("audit: no bugs found", flush=True)
            bugs_path.unlink()
            _save_audit_hash(project_dir)
            return

    max_fix_attempts = 3
    for attempt in range(1, max_fix_attempts + 1):
        print(
            f"\n>>> Fixing bugs (attempt {attempt}/{max_fix_attempts})...",
            flush=True,
        )
        fix_result = run_bug_fix(
            project_dir,
            log_dir,
            model=model,
        )

        if not fix_result.success:
            print(
                f"bug-fix: session exited with code {fix_result.exit_code}",
                flush=True,
            )
            break

        if not _has_meaningful_changes(project_dir):
            print(
                "bug-fix: no changes made",
                flush=True,
            )
            break

        check_result = run_checks(project_dir)
        if check_result.passed:
            _commit(project_dir, "Fix bugs from audit")
            bugs_path.unlink(missing_ok=True)
            _save_audit_hash(project_dir)
            return

        error_ctx = f"Command: {check_result.command}\n" + _tail(check_result.output, 50)
        print(
            f"\n!!! Bug fix checks failed (attempt {attempt}/{max_fix_attempts})",
            flush=True,
        )
        _print_error_tail(check_result.output)

        # Append error to BUGS.md so next attempt sees it
        bugs_path.write_text(bugs_content + "\n\n## Post-fix check failure\n" + error_ctx)


def _checkpoint(
    project_dir: Path,
    next_task: str = "",
) -> None:
    """Stage and commit all changes as a checkpoint.

    Stages both tracked modifications and untracked files
    (except logs/ and .mcloop/) so orphaned files from
    failed runs get committed before the next task.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return
        msg = "mcloop: checkpoint"
        if next_task:
            msg += f" (next: {next_task})"
        # Stage tracked changes
        subprocess.run(
            ["git", "add", "-u"],
            cwd=project_dir,
            capture_output=True,
        )
        # Also stage untracked files (orphans from
        # failed runs). git add -A respects .gitignore.
        subprocess.run(
            ["git", "add", "-A"],
            cwd=project_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=project_dir,
            capture_output=True,
        )
    except Exception:
        pass


def _commit(project_dir: Path, task_text: str) -> None:
    """Stage all changes, commit, and push."""
    try:
        subprocess.run(
            ["git", "add", "-u"],
            cwd=project_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Complete: {task_text}"],
            cwd=project_dir,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "remote"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            subprocess.run(
                [
                    "gh",
                    "repo",
                    "create",
                    project_dir.name,
                    "--private",
                    "--source=.",
                    "--remote=origin",
                ],
                cwd=project_dir,
                capture_output=True,
            )
            result = subprocess.run(
                ["git", "remote"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
        if result.stdout.strip():
            subprocess.run(
                ["git", "push"],
                cwd=project_dir,
                capture_output=True,
            )
    except Exception:
        pass
