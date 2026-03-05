"""Entry point for the main loop."""

from __future__ import annotations

import argparse
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
from mcloop.runner import run_task


def main() -> None:
    args = _parse_args()
    checklist_path = Path(args.file).resolve()

    if not checklist_path.exists():
        print(f"Checklist not found: {checklist_path}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        _dry_run(parse(checklist_path))
        return

    run_loop(checklist_path, max_retries=args.max_retries)


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    enabled_clis: tuple[str, ...] = ("claude",),
) -> list[str]:
    """Run the main loop. Returns list of stuck task texts."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

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
        while attempt < max_retries:
            attempt += 1
            result = run_task(task.text, cli, project_dir, log_dir, description, task_label=label)

            if is_rate_limited(result.output, result.exit_code):
                rate_state.mark_limited(cli)
                notify(f"Rate-limited on {cli}.", level="warning")
                cli = get_available_cli(rate_state, enabled_clis=enabled_clis)
                if cli is None:
                    cli = wait_for_reset(rate_state, notify, enabled_clis=enabled_clis)
                attempt -= 1  # don't count rate-limit as a real attempt
                continue

            if not result.success:
                notify(
                    f"Task failed (attempt {attempt}/{max_retries}): {task.text}",
                    level="error",
                )
                continue

            if not _has_meaningful_changes(project_dir):
                notify(
                    f"No files changed (attempt {attempt}/{max_retries}): {task.text}",
                    level="error",
                )
                continue

            check_result = run_checks(project_dir)
            if check_result.passed:
                _commit(project_dir, task.text)
                check_off(checklist_path, task)
                notify(f"Completed: {task.text}")
                success = True
                break
            else:
                notify(
                    f"Checks failed (attempt {attempt}/{max_retries}): {task.text}",
                    level="error",
                )

        if not success:
            mark_failed(checklist_path, task)
            notify(f"Giving up on: {task.text}", level="error")
            return [task.text]

    notify("All tasks completed!")
    return []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loop: grind through a markdown checklist")
    parser.add_argument("--file", default="PLAN.md", help="Checklist file (default: PLAN.md)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would run")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    return parser.parse_args()


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


def _commit(project_dir: Path, task_text: str) -> None:
    """Stage all changes and commit."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=project_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"Complete: {task_text}"],
            cwd=project_dir,
            capture_output=True,
        )
    except Exception:
        pass
