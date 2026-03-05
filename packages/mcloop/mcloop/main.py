"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import difflib
import json as _json
import subprocess
import sys
from pathlib import Path

from mcloop.checklist import Task, check_off, find_next, mark_failed, parse, parse_description
from mcloop.checks import run_checks
from mcloop.notify import notify
from mcloop.ratelimit import (
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    wait_for_reset,
)
from mcloop.runner import run_audit, run_sync, run_task


def main() -> None:
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

    run_loop(checklist_path, max_retries=args.max_retries, model=args.model)


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    enabled_clis: tuple[str, ...] = ("claude",),
    model: str | None = None,
) -> list[str]:
    """Run the main loop. Returns list of stuck task texts."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    _checkpoint(project_dir)

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
        print(f"\n>>> Task {label}) {task.text} (using {cli})")

        success = False
        attempt = 0
        last_error = ""
        while attempt < max_retries:
            attempt += 1
            result = run_task(
                task.text, cli, project_dir, log_dir,
                description, task_label=label,
                model=model, prior_errors=last_error,
            )

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
                    f"\n!!! Task failed "
                    f"(attempt {attempt}/{max_retries})",
                    flush=True,
                )
                print(
                    f"    Exit code: {result.exit_code}",
                    flush=True,
                )
                _print_error_tail(result.output)
                notify(
                    f"Task failed "
                    f"(attempt {attempt}/{max_retries}): "
                    + task.text,
                    level="error",
                )
                continue

            if not _has_meaningful_changes(project_dir):
                print(f"\n>>> Working tree clean, skipping commit for: {task.text}", flush=True)
                check_off(checklist_path, task)
                completed.append(
                    f"{label}) {task.text} (clean)"
                )
                notify(f"Skipped (clean): {task.text}")
                success = True
                break

            check_result = run_checks(project_dir)
            if check_result.passed:
                _commit(project_dir, task.text)
                check_off(checklist_path, task)
                completed.append(f"{label}) {task.text}")
                notify(f"Completed: {task.text}")
                success = True
                break
            else:
                last_error = (
                    f"Command: {check_result.command}\n"
                    + _tail(check_result.output, 50)
                )
                print(
                    f"\n!!! Checks failed "
                    f"(attempt {attempt}/{max_retries}): "
                    f"{check_result.command}",
                    flush=True,
                )
                _print_error_tail(check_result.output)
                notify(
                    f"Checks failed "
                    f"(attempt {attempt}/{max_retries}): "
                    + task.text,
                    level="error",
                )

        if not success:
            mark_failed(checklist_path, task)
            failed_task = f"{label}) {task.text}"
            failed_reason = last_error
            notify(
                f"Giving up on: {task.text}",
                level="error",
            )
            _print_summary(
                completed, failed_task, failed_reason,
                parse(checklist_path),
            )
            return [task.text]

    _print_summary(completed, None, "", [])
    notify("All tasks completed!")
    return []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loop: grind through a markdown checklist")
    parser.add_argument("--file", default="PLAN.md", help="Checklist file (default: PLAN.md)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would run")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    parser.add_argument("--model", default=None, help="Claude model to use (e.g., opus, sonnet)")
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

    def _print(task_list, depth=0):
        for t in task_list:
            marker = "[x]" if t.checked else "[ ]"
            print(f"{'  ' * depth}- {marker} {t.text}")
            if t.children:
                _print(t.children, depth + 1)

    _print(tasks)
    next_task = find_next(tasks)
    if next_task:
        print(f"\nNext task: {next_task.text}")
    else:
        print("\nNo unchecked tasks remaining.")


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
) -> None:
    """Print a summary of what McLoop did."""
    print("\n" + "=" * 40, flush=True)
    print("McLoop Summary", flush=True)
    print("=" * 40, flush=True)

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

    if not completed and not failed_task:
        print(
            "All tasks were already complete.",
            flush=True,
        )

    suggestions = _whitelist_suggestions()
    if suggestions:
        print(
            "\nWhitelist suggestions "
            "(approved this session):",
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
        "rm", "rmdir", "kill", "killall", "pkill",
        "chmod", "chown", "sudo", "su", "dd",
        "mkfs", "mv", "shutdown", "reboot",
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
    """Check if there are staged or unstaged changes beyond PLAN.md and logs/."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        all_files = (result.stdout + untracked.stdout).strip().splitlines()
        meaningful = [
            f for f in all_files
            if f and not f.startswith("logs/") and f != "PLAN.md"
        ]
        return len(meaningful) > 0
    except Exception:
        return True  # if git fails, don't block


def _checkpoint(project_dir: Path) -> None:
    """Stage and commit all tracked modified files as a checkpoint."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return
        subprocess.run(["git", "add", "-u"], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "mcloop: checkpoint before run"],
            cwd=project_dir,
            capture_output=True,
        )
    except Exception:
        pass


def _commit(project_dir: Path, task_text: str) -> None:
    """Stage all changes, commit, and push."""
    try:
        subprocess.run(["git", "add", "-u"], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"Complete: {task_text}"],
            cwd=project_dir,
            capture_output=True,
        )
        result = subprocess.run(["git", "remote"], cwd=project_dir, capture_output=True, text=True)
        if not result.stdout.strip():
            subprocess.run(
                [
                    "gh", "repo", "create", project_dir.name,
                    "--private", "--source=.", "--remote=origin",
                ],
                cwd=project_dir,
                capture_output=True,
            )
            result = subprocess.run(
                ["git", "remote"], cwd=project_dir, capture_output=True, text=True
            )
        if result.stdout.strip():
            subprocess.run(["git", "push"], cwd=project_dir, capture_output=True)
    except Exception:
        pass
