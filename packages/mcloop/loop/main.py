"""Entry point — the main loop."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from loop.checklist import check_off, find_next, mark_failed, parse
from loop.checks import run_checks
from loop.notify import notify
from loop.ratelimit import (
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    wait_for_reset,
)
from loop.runner import run_task


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
            cli = wait_for_reset(rate_state, notify)

        print(f"\n>>> Task: {task.text} (using {cli})")

        success = False
        for attempt in range(1, max_retries + 1):
            result = run_task(task.text, cli, project_dir, log_dir)

            if is_rate_limited(result.output, result.exit_code):
                rate_state.mark_limited(cli)
                notify(f"Rate-limited on {cli}.", level="warning")
                cli = get_available_cli(rate_state, enabled_clis=enabled_clis)
                if cli is None:
                    cli = wait_for_reset(rate_state, notify)
                continue

            if not result.success:
                notify(
                    f"Task failed (attempt {attempt}/{max_retries}): {task.text}",
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
    parser = argparse.ArgumentParser(description="Loop — grind through a markdown checklist")
    parser.add_argument("--file", default="TODO.md", help="Checklist file (default: TODO.md)")
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
