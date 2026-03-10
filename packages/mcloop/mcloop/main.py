"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json as _json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from mcloop import formatting, worktree
from mcloop.checklist import (
    Task,
    check_off,
    current_stage,
    find_next,
    get_stages,
    is_auto_task,
    is_user_task,
    mark_failed,
    parse,
    parse_auto_task,
    parse_description,
    stage_status,
    user_task_instructions,
)
from mcloop.checks import detect_app_type, detect_build, detect_run, get_check_commands, run_checks
from mcloop.investigator import gather_bug_context, generate_plan
from mcloop.notify import notify
from mcloop.ratelimit import (
    SESSION_LIMIT_POLL,
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    is_session_limited,
    wait_for_reset,
)
from mcloop.runner import (
    INVESTIGATION_TOOLS,
    bugs_md_has_bugs,
    parse_bugs_md,
    parse_verification_output,
    review_found_problems,
    run_audit,
    run_bug_fix,
    run_bug_verify,
    run_post_fix_review,
    run_sync,
    run_task,
)


def _kill_orphan_sessions(project_dir: Path) -> None:
    """Kill orphan claude processes from a previous mcloop run.

    When mcloop is killed with kill -9, the claude subprocess
    survives because it runs in its own session. The PID is
    recorded in .mcloop/active-pid so the next run can kill it.
    """
    pid_file = project_dir / ".mcloop" / "active-pid"
    if not pid_file.exists():
        return
    try:
        content = pid_file.read_text().strip()
        parts = content.split()
        pid = int(parts[0])
        pgid = int(parts[1]) if len(parts) > 1 else pid
    except (OSError, ValueError, IndexError):
        pid_file.unlink(missing_ok=True)
        return
    # Check if the process is still alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Already dead
        pid_file.unlink(missing_ok=True)
        return
    except PermissionError:
        pass  # alive but we can't signal it
    # Kill the entire process group
    print(
        formatting.error_msg(f"Killing orphan claude process (pid={pid}) from previous run"),
        flush=True,
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    pid_file.unlink(missing_ok=True)


def _kill_active_process() -> None:
    """Kill any active claude subprocess and its process group.

    Called by both the signal handler and the atexit handler
    so orphan claude processes cannot survive mcloop exiting.
    """
    import mcloop.runner as _runner

    proc = _runner._active_process
    if proc is not None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        _runner._active_process = None


def _read_repro_steps(wt_path: Path) -> list[dict]:
    """Read reproduction steps from .mcloop/repro-steps.json.

    Returns a list of {"action": ..., "args": ...} dicts, or an
    empty list if the file does not exist or is malformed.
    """
    repro_file = wt_path / ".mcloop" / "repro-steps.json"
    if not repro_file.is_file():
        return []
    try:
        data = _json.loads(repro_file.read_text())
    except (OSError, _json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    steps = []
    for entry in data:
        if isinstance(entry, dict) and "action" in entry and "args" in entry:
            steps.append(entry)
    return steps


def _replay_repro_steps(steps: list[dict]) -> list[str]:
    """Replay reproduction steps via _dispatch_auto_action.

    Returns a list of result strings from each step.
    """
    results: list[str] = []
    for step in steps:
        action = step["action"]
        args = step["args"]
        try:
            result = _dispatch_auto_action(action, str(args))
        except Exception as exc:
            result = f"ERROR: {action}({args}) raised {exc}"
        results.append(result)
    return results


def _verify_gui_survival(process_name: str, pm) -> str | None:
    """Check that a GUI app survived repro-step replay.

    Verifies the process is still alive, not hung (via sample),
    and has at least one window open.

    Returns a failure description string, or None if the app survived.
    """
    from mcloop import app_interact

    pids = pm.pgrep(process_name)
    if not pids:
        crash_rpt = pm.read_crash_report(process_name)
        print(
            formatting.error_msg("Post-replay: app CRASHED"),
            flush=True,
        )
        if crash_rpt:
            lines = crash_rpt.splitlines()[:20]
            print("\n".join(lines), file=sys.stderr)
        return f"App crashed after repro-step replay. Crash report: {crash_rpt or 'none'}"

    pid = pids[0]
    sample_out = pm.sample(pid)
    if pm.is_main_thread_stuck(sample_out):
        print(
            formatting.error_msg("Post-replay: app HUNG"),
            flush=True,
        )
        return "App hung after repro-step replay (main thread stuck)"

    try:
        has_window = app_interact.window_exists(process_name)
    except Exception:
        has_window = None

    if has_window is False:
        print(
            formatting.error_msg("Post-replay: app has no windows"),
            flush=True,
        )
        return "App has no windows after repro-step replay"
    elif has_window is True:
        print(
            formatting.system_msg("Post-replay: app alive, responsive, window present"),
            flush=True,
        )
    else:
        print(
            formatting.system_msg("Post-replay: app alive and responsive"),
            flush=True,
        )
    return None


def _launch_app_verification(wt_path: Path) -> str | None:
    """Launch the app from the worktree to verify the fix works.

    Uses the process monitor to run the app and reports whether it
    starts successfully, crashes, or hangs. If .mcloop/repro-steps.json
    exists, replays the reproduction steps after a successful launch.

    Returns a failure description string, or None if verification passed.
    """
    from mcloop import process_monitor

    run_cmd = detect_run(wt_path)
    if not run_cmd:
        return None

    app_type = detect_app_type(wt_path)
    print(
        formatting.system_msg(f"Verifying fix: {run_cmd}"),
        flush=True,
    )

    if app_type == "gui":
        # Extract process name from run command for pgrep.
        parts = shlex.split(run_cmd)
        # "swift run AppName" -> "AppName", "open Foo.app" -> "Foo"
        process_name = parts[-1]
        if process_name.endswith(".app"):
            process_name = process_name.rsplit("/", 1)[-1][: -len(".app")]
        result = process_monitor.run_gui(
            run_cmd,
            process_name,
            timeout_seconds=15,
        )
        failure = None
        if result.crashed:
            print(
                formatting.error_msg("Verification: app CRASHED"),
                flush=True,
            )
            if result.crash_report:
                lines = result.crash_report.splitlines()[:20]
                print("\n".join(lines), file=sys.stderr)
            failure = f"App crashed on launch. Crash report: {result.crash_report or 'none'}"
        elif result.hung:
            print(
                formatting.error_msg("Verification: app HUNG"),
                flush=True,
            )
            failure = "App hung on launch (not responding)"
        else:
            print(
                formatting.system_msg(f"Verification: app running OK ({result.duration:.1f}s)"),
                flush=True,
            )
            # Replay reproduction steps while the app is still running.
            repro_steps = _read_repro_steps(wt_path)
            if repro_steps:
                print(
                    formatting.system_msg(f"Replaying {len(repro_steps)} reproduction step(s)..."),
                    flush=True,
                )
                repro_results = _replay_repro_steps(repro_steps)
                repro_errors = []
                for i, res in enumerate(repro_results, 1):
                    failed = res.startswith("ERROR:")
                    msg = f"  Step {i}: {res.splitlines()[0]}"
                    if failed:
                        print(formatting.error_msg(msg), flush=True)
                        repro_errors.append(msg.strip())
                    else:
                        print(formatting.system_msg(msg), flush=True)
                # Post-replay survival check.
                survival_failure = _verify_gui_survival(process_name, process_monitor)
                if survival_failure:
                    failure = survival_failure
                elif repro_errors:
                    failure = "Repro-step replay had errors: " + "; ".join(repro_errors)
        # Clean up: kill the launched GUI app.
        pids = process_monitor.pgrep(process_name)
        for pid in pids:
            process_monitor.kill(pid)
        return failure
    elif app_type == "cli":
        result = process_monitor.run_cli(
            run_cmd,
            cwd=str(wt_path),
            timeout_seconds=15,
            hang_seconds=10,
        )
        if result.hung:
            print(
                formatting.error_msg("Verification: app HUNG"),
                flush=True,
            )
            return "App hung during CLI verification"
        elif result.exit_code != 0:
            print(
                formatting.error_msg(f"Verification: app exited with code {result.exit_code}"),
                flush=True,
            )
            output_tail = ""
            if result.output:
                tail = result.output.strip().splitlines()[-10:]
                for line in tail:
                    print(f"  {line}", file=sys.stderr)
                output_tail = "\n".join(tail)
            return f"App exited with code {result.exit_code}." + (
                f" Output:\n{output_tail}" if output_tail else ""
            )
        else:
            print(
                formatting.system_msg(f"Verification: app exited OK ({result.duration:.1f}s)"),
                flush=True,
            )
            # Replay reproduction steps (e.g. re-run with specific args).
            repro_steps = _read_repro_steps(wt_path)
            if repro_steps:
                print(
                    formatting.system_msg(f"Replaying {len(repro_steps)} reproduction step(s)..."),
                    flush=True,
                )
                repro_results = _replay_repro_steps(repro_steps)
                repro_errors = []
                for i, res in enumerate(repro_results, 1):
                    failed = res.startswith("ERROR:")
                    msg = f"  Step {i}: {res.splitlines()[0]}"
                    if failed:
                        print(formatting.error_msg(msg), flush=True)
                        repro_errors.append(msg.strip())
                    else:
                        print(formatting.system_msg(msg), flush=True)
                if repro_errors:
                    return "Repro-step replay had errors: " + "; ".join(repro_errors)
            return None
    else:
        # Web apps: just note the run command, don't launch a server
        print(
            formatting.system_msg(f"Skipping launch for web app: {run_cmd}"),
            flush=True,
        )
        return None


MAX_VERIFICATION_ROUNDS = 3


def _append_verification_failure(
    wt_path: Path,
    failure: str,
    round_num: int,
) -> None:
    """Append verification failure info to the worktree for the next run.

    Adds an observation to NOTES.md and appends new tasks to PLAN.md
    so mcloop can pick them up in the next run.
    """
    # Append to NOTES.md
    notes_path = wt_path / "NOTES.md"
    notes_header = ""
    if not notes_path.exists():
        notes_header = "## Observations\n\n"
    with open(notes_path, "a") as f:
        if notes_header:
            f.write(notes_header)
        f.write(f"- Verification round {round_num} failed: {failure}\n")

    # Append new fix tasks to PLAN.md
    plan_path = wt_path / "PLAN.md"
    with open(plan_path, "a") as f:
        f.write(f"\n## Verification fix (round {round_num})\n\n")
        f.write(f"- [ ] Investigate and fix verification failure: {failure}\n")
        f.write("- [ ] Verify the fix resolves the issue\n")

    print(
        formatting.system_msg(
            f"Verification failed (round {round_num}/{MAX_VERIFICATION_ROUNDS})."
            " Feeding failure back into investigation..."
        ),
        flush=True,
    )


def _investigation_passed(
    wt_path: Path,
    branch: str,
    project_dir: Path,
) -> None:
    """All investigation tasks passed. Show diff and offer to merge back."""
    print("\n--- Investigation complete (all tasks passed) ---", file=sys.stderr)

    source_branch = worktree.current_branch(cwd=project_dir)

    # Show commits on the investigation branch
    log_result = subprocess.run(
        ["git", "log", "--oneline", source_branch + ".." + branch],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    if log_result.stdout.strip():
        print("\nCommits to merge:", file=sys.stderr)
        print(log_result.stdout.rstrip(), file=sys.stderr)

    # Show changed files summary
    diff_result = subprocess.run(
        ["git", "diff", "--stat", source_branch + "..." + branch],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
    )
    if diff_result.stdout.strip():
        print("\nChanged files:", file=sys.stderr)
        print(diff_result.stdout.rstrip(), file=sys.stderr)

    # Ask for confirmation
    print("", file=sys.stderr)
    try:
        answer = input(f"Merge {branch} back into main? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        answer = ""

    if answer.strip().lower() not in ("y", "yes"):
        print(
            f"Skipped merge. Worktree remains at {wt_path}",
            file=sys.stderr,
        )
        return

    try:
        worktree.merge(branch, cwd=project_dir)
        print(f"Merged {branch} into main.", file=sys.stderr)
    except RuntimeError as exc:
        print(f"Merge failed: {exc}", file=sys.stderr)
        print(f"Worktree remains at {wt_path}", file=sys.stderr)
        sys.exit(1)

    try:
        worktree.remove(branch, cwd=project_dir)
        print("Cleaned up worktree and branch.", file=sys.stderr)
    except RuntimeError as exc:
        print(f"Cleanup warning: {exc}", file=sys.stderr)


def _investigation_failed(wt_path: Path, branch: str) -> None:
    """Investigation had failures. Print state and leave worktree."""
    print(
        "\n--- Investigation incomplete (some tasks failed) ---",
        file=sys.stderr,
    )

    # Show what was learned from NOTES.md
    notes_path = wt_path / "NOTES.md"
    if notes_path.exists():
        notes = notes_path.read_text().strip()
        if notes:
            print("\nWhat was learned (NOTES.md):", file=sys.stderr)
            print(notes, file=sys.stderr)

    # Show what remains from PLAN.md
    plan_path = wt_path / "PLAN.md"
    if plan_path.exists():
        tasks = parse(plan_path)
        completed = []
        remaining = []
        failed = []

        def _collect(task_list: list[Task]) -> None:
            for task in task_list:
                if task.children:
                    _collect(task.children)
                elif task.failed:
                    failed.append(task.text)
                elif task.checked:
                    completed.append(task.text)
                else:
                    remaining.append(task.text)

        _collect(tasks)

        if completed:
            print(f"\nCompleted: {len(completed)} tasks", file=sys.stderr)
        if failed:
            print(f"Failed: {len(failed)} tasks", file=sys.stderr)
            for text in failed:
                print(f"  [!] {text}", file=sys.stderr)
        if remaining:
            print(f"Remaining: {len(remaining)} tasks", file=sys.stderr)
            for text in remaining:
                print(f"  [ ] {text}", file=sys.stderr)

    print(f"\nWorktree: {wt_path}", file=sys.stderr)
    print(f"Branch:   {branch}", file=sys.stderr)
    print("Resume with: mcloop investigate", file=sys.stderr)


def main() -> None:
    import atexit

    atexit.register(_kill_active_process)

    def _handle_sigint(sig, frame):
        print("\nInterrupted.", flush=True)
        _kill_active_process()
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTSTP, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    signal.signal(signal.SIGHUP, _handle_sigint)
    _main()


def _main() -> None:
    args = _parse_args()
    checklist_path = Path(args.file).resolve()

    if not checklist_path.exists():
        print(f"Checklist not found: {checklist_path}", file=sys.stderr)
        sys.exit(1)

    if args.command == "sync":
        _cmd_sync(checklist_path, dry_run=args.dry_run)
        return

    if args.command == "audit":
        _cmd_audit(checklist_path)
        return

    if args.command == "investigate":
        project_dir = checklist_path.parent
        stdin_text = ""
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
        ctx = gather_bug_context(
            project_dir,
            description=args.description,
            log_path=args.log,
            stdin_text=stdin_text,
        )
        print("Bug context gathered:", file=sys.stderr)
        if ctx.user_description:
            print(f"  description: {ctx.user_description}", file=sys.stderr)
        if ctx.crash_report:
            print("  crash report: found", file=sys.stderr)
        if ctx.failure_history:
            sources = ctx.failure_history.count("From ")
            print(f"  log sources: {sources}", file=sys.stderr)
        if ctx.app_type:
            print(f"  app type: {ctx.app_type}", file=sys.stderr)

        # Create or resume a git worktree for the investigation
        wt_description = ctx.user_description or "investigation"
        try:
            wt_path, branch, resumed = worktree.create(wt_description, cwd=project_dir)
        except (ValueError, RuntimeError) as exc:
            print(f"Error creating worktree: {exc}", file=sys.stderr)
            sys.exit(1)

        if resumed:
            print(f"Resuming investigation in {wt_path}", file=sys.stderr)
        else:
            print(
                f"Created investigation worktree at {wt_path}",
                file=sys.stderr,
            )
            # Generate investigation PLAN.md
            plan_content = generate_plan(ctx)
            (wt_path / "PLAN.md").write_text(plan_content)
            print("  generated PLAN.md", file=sys.stderr)

            # Copy mcloop.json and .claude/ settings from parent project
            _copy_project_settings(project_dir, wt_path)

        print(f"  branch: {branch}", file=sys.stderr)

        # Run mcloop in the worktree directory with --no-audit,
        # retrying if post-fix verification fails.
        cmd = [sys.executable, "-m", "mcloop", "--no-audit", "--allow-web-tools"]
        if args.model:
            cmd.extend(["--model", args.model])
        if args.fallback_model:
            cmd.extend(["--fallback-model", args.fallback_model])

        for verify_round in range(1, MAX_VERIFICATION_ROUNDS + 1):
            print(f"Running mcloop in {wt_path} ...", file=sys.stderr)
            result = subprocess.run(cmd, cwd=str(wt_path))

            if result.returncode != 0:
                _investigation_failed(wt_path, branch)
                sys.exit(result.returncode)

            # All tasks passed — verify the fix actually works
            failure = _launch_app_verification(wt_path)
            if failure is None:
                break

            # Verification failed — feed failure back and retry
            if verify_round >= MAX_VERIFICATION_ROUNDS:
                print(
                    formatting.error_msg(
                        f"Verification failed after {MAX_VERIFICATION_ROUNDS} rounds. Giving up."
                    ),
                    flush=True,
                )
                _investigation_failed(wt_path, branch)
                sys.exit(1)

            _append_verification_failure(wt_path, failure, verify_round)

        print(
            formatting.system_msg("Verification passed."),
            flush=True,
        )
        notify("Investigation verified: fix works. Ready to merge.")
        _investigation_passed(wt_path, branch, project_dir)
        return

    if args.dry_run:
        _dry_run(parse(checklist_path))
        return

    run_loop(
        checklist_path,
        max_retries=args.max_retries,
        model=args.model,
        fallback_model=args.fallback_model,
        no_audit=args.no_audit,
        allowed_tools=INVESTIGATION_TOOLS if args.allow_web_tools else None,
    )


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    enabled_clis: tuple[str, ...] = ("claude",),
    model: str | None = None,
    fallback_model: str | None = None,
    no_audit: bool = False,
    allowed_tools: str | None = None,
) -> list[str]:
    """Run the main loop. Returns list of stuck task texts."""
    project_dir = checklist_path.parent
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    project_checks = get_check_commands(project_dir)

    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    _checkpoint(project_dir, verbose=True)
    _push_or_die(project_dir)

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
    current_model = model  # may switch to fallback_model on rate limit

    while True:
        tasks = parse(checklist_path)
        task = find_next(tasks)
        if task is None:
            break

        # If this is a parent with all children done, just check it off
        if task.children and all(c.checked for c in task.children):
            check_off(checklist_path, task)
            continue

        label = _task_label(tasks, task)

        # Handle [AUTO] tasks: automated observation
        if is_auto_task(task):
            has_subtasks = "." in label
            ctx.update_group(label, has_subtasks)
            action, args = parse_auto_task(task)
            response = _handle_auto_task(label, action, args)
            check_off(checklist_path, task)
            completed.append(f"{label}) {task.text}")
            ctx.add(label, task.text, "0s", response)
            notify(f"[AUTO:{action}] {args[:60]}")
            continue

        # Handle [USER] tasks: pause for human observation
        if is_user_task(task):
            has_subtasks = "." in label
            ctx.update_group(label, has_subtasks)
            instructions = user_task_instructions(task)
            response = _handle_user_task(label, instructions)
            check_off(checklist_path, task)
            elapsed = _format_elapsed(time.monotonic() - run_start)
            completed.append(f"{label}) {task.text}")
            ctx.add(label, task.text, "0s", response)
            notify(f"[USER] {instructions[:80]}")
            continue

        cli = get_available_cli(rate_state, enabled_clis=enabled_clis)
        if cli is None:
            cli = wait_for_reset(rate_state, notify, enabled_clis=enabled_clis)

        has_subtasks = "." in label
        ctx.update_group(label, has_subtasks)

        # Pick up any text the user typed while the last task ran
        user_input = _check_user_input()
        if user_input:
            ctx.add_user_input(user_input)
            print(
                formatting.system_msg(f"User input received ({len(user_input)} chars)"),
                flush=True,
            )

        _checkpoint(
            project_dir,
            next_task=f"{label}) {task.text}",
        )
        print(formatting.task_header(label, task.text, cli), flush=True)

        task_start = time.monotonic()
        success = False
        models_to_try = [current_model]
        if fallback_model and fallback_model != current_model:
            models_to_try.append(fallback_model)
        for model_idx, task_model in enumerate(models_to_try):
            if model_idx > 0:
                print(
                    formatting.system_msg(f"Primary model failed, retrying with {task_model}"),
                    flush=True,
                )
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
                    model=task_model,
                    prior_errors=last_error,
                    session_context=ctx.text(),
                    check_commands=project_checks,
                    allowed_tools=allowed_tools,
                )

                if is_session_limited(
                    result.output,
                    result.exit_code,
                ):
                    _checkpoint(project_dir)
                    notify(
                        "Session limit reached. Polling every 10m.",
                        level="warning",
                    )
                    print(
                        formatting.system_msg(
                            "Session limit reached."
                            f" Polling every {SESSION_LIMIT_POLL // 60}m."
                            " Press Ctrl-C to exit."
                        ),
                        flush=True,
                    )
                    try:
                        time.sleep(SESSION_LIMIT_POLL)
                    except KeyboardInterrupt:
                        total = time.monotonic() - run_start
                        _print_summary(
                            completed,
                            None,
                            "",
                            parse(checklist_path),
                            total,
                            project_dir,
                            notes_snapshot,
                        )
                        print("\nExiting.", flush=True)
                        return [task.text]
                    # Don't count as a real attempt
                    attempt -= 1
                    continue

                if is_rate_limited(result.output, result.exit_code):
                    rate_state.mark_limited(cli)
                    notify(
                        f"Rate-limited on {cli}.",
                        level="warning",
                    )
                    if fallback_model and current_model != fallback_model:
                        current_model = fallback_model
                        task_model = fallback_model
                        print(
                            formatting.system_msg(
                                f"Switching to fallback model: {fallback_model}"
                            ),
                            flush=True,
                        )
                    cli = get_available_cli(
                        rate_state,
                        enabled_clis=enabled_clis,
                    )
                    if cli is None:
                        cli = wait_for_reset(
                            rate_state,
                            notify,
                            enabled_clis=enabled_clis,
                        )
                        # Reset to primary model after cooldown
                        if fallback_model and current_model == fallback_model:
                            current_model = model
                            task_model = model
                            print(
                                formatting.system_msg(
                                    f"Rate limit cleared, back to model: {model}"
                                ),
                                flush=True,
                            )
                    # Don't count rate-limit as a real attempt
                    attempt -= 1
                    continue

                if not result.success:
                    last_error = _tail(result.output, 50)
                    print(
                        formatting.error_msg(f"Task failed (attempt {attempt}/{max_retries})"),
                        flush=True,
                    )
                    print(
                        f"    Exit code: {result.exit_code}",
                        flush=True,
                    )
                    _print_error_tail(result.output)
                    continue

                if not _has_meaningful_changes(project_dir):
                    last_error = "Task produced no file changes"
                    print(
                        formatting.error_msg(
                            f"No-op task (attempt {attempt}/{max_retries}): {task.text}"
                        ),
                        flush=True,
                    )
                    continue

                changed_files = _changed_files(project_dir)
                check_result = run_checks(
                    project_dir,
                    changed_files=changed_files,
                )
                if check_result.passed:
                    try:
                        _commit(project_dir, task.text)
                    except RuntimeError as exc:
                        print(
                            formatting.error_msg(str(exc)),
                            flush=True,
                        )
                        total = time.monotonic() - run_start
                        _print_summary(
                            completed,
                            f"{label}) {task.text}",
                            str(exc),
                            parse(checklist_path),
                            total,
                            project_dir,
                            notes_snapshot,
                        )
                        sys.exit(1)
                    check_off(checklist_path, task)
                    elapsed = _format_elapsed(
                        time.monotonic() - task_start,
                    )
                    completed.append(f"{label}) {task.text}")
                    print(
                        formatting.task_complete(label, elapsed),
                        flush=True,
                    )
                    ctx.add(
                        label,
                        task.text,
                        elapsed,
                        result.output,
                        changed_files=changed_files,
                    )
                    success = True
                    break
                else:
                    last_error = f"Command: {check_result.command}\n" + _tail(
                        check_result.output, 50
                    )
                    print(
                        formatting.error_msg(
                            f"Checks failed (attempt"
                            f" {attempt}/{max_retries}):"
                            f" {check_result.command}"
                        ),
                        flush=True,
                    )
                    _print_error_tail(check_result.output)

            if success:
                break

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
        print(formatting.system_msg("Running full test suite (stage boundary)..."), flush=True)
        full_check = run_checks(project_dir)
        if not full_check.passed:
            print(
                formatting.error_msg(f"Full suite failed at stage boundary: {full_check.command}"),
                flush=True,
            )
            _print_error_tail(full_check.output)
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

    # Full test suite at end of run
    print(formatting.system_msg("Running full test suite (end of run)..."), flush=True)
    full_check = run_checks(project_dir)
    if not full_check.passed:
        print(
            formatting.error_msg(f"Full suite failed at end of run: {full_check.command}"),
            flush=True,
        )
        _print_error_tail(full_check.output)

    # Only audit if every task in every stage is complete
    final_for_audit = parse(checklist_path)
    has_unchecked = False

    def _any_unchecked(task_list: list[Task]) -> bool:
        for t in task_list:
            if not t.checked and not t.failed:
                return True
            if _any_unchecked(t.children):
                return True
        return False

    has_unchecked = _any_unchecked(final_for_audit)
    if has_unchecked:
        print(
            formatting.system_msg("Audit skipped (unchecked tasks remain)"),
            flush=True,
        )
    elif not no_audit:
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
        "--fallback-model",
        default=None,
        help="Model to use when the primary model is rate-limited",
    )
    parser.add_argument(
        "--no-audit", action="store_true", help="Skip the post-completion bug audit cycle"
    )
    parser.add_argument(
        "--allow-web-tools",
        action="store_true",
        help="Enable WebFetch and WebSearch tools for sessions",
    )
    subparsers = parser.add_subparsers(dest="command")
    sync_parser = subparsers.add_parser("sync", help="Sync PLAN.md with the codebase")
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without modifying PLAN.md"
    )
    subparsers.add_parser("audit", help="Audit the codebase and write BUGS.md")
    inv_parser = subparsers.add_parser("investigate", help="Investigate a bug in a worktree")
    inv_parser.add_argument(
        "description", nargs="?", default=None, help="Short description of the bug"
    )
    inv_parser.add_argument("--log", default=None, help="Path to a log file with error output")
    return parser.parse_args()


def _cmd_audit(checklist_path: Path) -> None:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = checklist_path.parent
    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    log_dir = project_dir / "logs"
    bugs_path = project_dir / "BUGS.md"
    existing = bugs_path.read_text() if bugs_path.exists() else ""
    result = run_audit(project_dir, log_dir, existing_bugs=existing)
    if not result.success:
        print(f"audit: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    bugs_path = project_dir / "BUGS.md"
    if bugs_path.exists():
        print(bugs_path.read_text())
    else:
        print("audit: BUGS.md was not written", file=sys.stderr)


def _copy_project_settings(src: Path, dst: Path) -> None:
    """Copy mcloop.json and .claude/ settings from src to dst."""
    mcloop_json = src / "mcloop.json"
    if mcloop_json.is_file():
        shutil.copy2(mcloop_json, dst / "mcloop.json")
        print("  copied mcloop.json", file=sys.stderr)

    claude_dir = src / ".claude"
    if claude_dir.is_dir():
        dst_claude = dst / ".claude"
        if dst_claude.exists():
            shutil.rmtree(dst_claude)
        shutil.copytree(claude_dir, dst_claude)
        print("  copied .claude/", file=sys.stderr)


def _cmd_sync(checklist_path: Path, *, dry_run: bool = False) -> None:
    """Launch a Claude Code session with full project context for sync analysis."""
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


def _handle_user_task(label: str, instructions: str) -> str:
    """Pause for a [USER] task and collect the user's observation.

    Prints clearly formatted instructions and waits for the user
    to type their observation. Returns the user's response text.
    """
    print(formatting.user_banner(label, instructions), flush=True)
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == "" and lines:
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass
    response = "\n".join(lines).strip()
    if response:
        print(formatting.system_msg(f"User observation recorded ({len(response)} chars)"))
    else:
        print(formatting.system_msg("No observation provided, continuing."))
    return response


def _handle_auto_task(label: str, action: str, args: str) -> str:
    """Execute an [AUTO] task and return the observation result.

    Dispatches to process_monitor, app_interact, or web_interact
    based on the action keyword. Returns a formatted string
    describing the observation result.

    Supported actions:
        run_cli      - Run a CLI command and capture output/crash/hang
        run_gui      - Launch a GUI app and check for crash/hang
                       (args: "command | process_name")
        window_exists - Check if an app has a window open
        screenshot   - Capture a screenshot of an app window
        list_elements - List UI elements of an app window
        click_button - Click a button in an app window
                       (args: "app_name | button_label")
        navigate     - Navigate a browser to a URL
        page_text    - Read visible text from the current browser page
    """
    print(formatting.auto_banner(label, action, args), flush=True)

    try:
        result = _dispatch_auto_action(action, args)
    except Exception as exc:
        result = f"ERROR: {action} failed: {exc}"
        print(f"  {result}", flush=True)
        return result

    # Truncate very long results for display
    display = result[:500] + "..." if len(result) > 500 else result
    print(f"  Result: {display}", flush=True)
    print(formatting.system_msg(f"Auto observation complete ({len(result)} chars)"))
    return result


def _dispatch_auto_action(action: str, args: str) -> str:
    """Dispatch an auto task action to the appropriate module.

    Returns the observation result as a string.
    """
    from mcloop import app_interact, process_monitor

    if action == "run_cli":
        cli_result = process_monitor.run_cli(args)
        parts = [f"exit_code: {cli_result.exit_code}"]
        if cli_result.hung:
            parts.append("STATUS: HUNG (killed)")
        elif cli_result.exit_code != 0:
            parts.append("STATUS: CRASHED")
        else:
            parts.append("STATUS: OK")
        if cli_result.output:
            parts.append(f"output:\n{cli_result.output}")
        if cli_result.sample_output:
            parts.append(f"sample:\n{cli_result.sample_output}")
        return "\n".join(parts)

    if action == "run_gui":
        # Format: "command | process_name"
        if "|" not in args:
            return f"ERROR: run_gui requires 'command | process_name', got: {args}"
        command, process_name = args.split("|", 1)
        gui_result = process_monitor.run_gui(
            command.strip(),
            process_name.strip(),
        )
        parts = []
        if gui_result.crashed:
            parts.append("STATUS: CRASHED")
        elif gui_result.hung:
            parts.append("STATUS: HUNG")
        else:
            parts.append("STATUS: OK")
        parts.append(f"duration: {gui_result.duration:.1f}s")
        if gui_result.crash_report:
            parts.append(f"crash_report:\n{gui_result.crash_report}")
        if gui_result.sample_output:
            parts.append(f"sample:\n{gui_result.sample_output}")
        return "\n".join(parts)

    if action == "window_exists":
        exists = app_interact.window_exists(args.strip())
        return f"window_exists({args.strip()}): {exists}"

    if action == "screenshot":
        app_name = args.strip()
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", app_name)
        path = f"/tmp/auto_screenshot_{safe_name}.png"
        app_interact.screenshot_window(app_name, path)
        return f"screenshot saved to {path}"

    if action == "list_elements":
        elements = app_interact.list_elements(args.strip())
        return f"UI elements:\n{elements}"

    if action == "click_button":
        if "|" not in args:
            return f"ERROR: click_button requires 'app_name | button_label', got: {args}"
        app_name, button_label = args.split("|", 1)
        app_interact.click_button(app_name.strip(), button_label.strip())
        return f"clicked button '{button_label.strip()}' in {app_name.strip()}"

    if action == "navigate":
        from mcloop import web_interact

        if not web_interact.is_playwright_available():
            return "ERROR: Playwright is not installed"
        browser = web_interact.launch_browser()
        try:
            browser.navigate(args.strip())
            text = browser.text()
            return f"navigated to {args.strip()}\npage text:\n{text}"
        finally:
            browser.close()

    if action == "page_text":
        from mcloop import web_interact

        if not web_interact.is_playwright_available():
            return "ERROR: Playwright is not installed"
        browser = web_interact.launch_browser()
        try:
            if args.strip():
                browser.navigate(args.strip())
            return f"page text:\n{browser.text()}"
        finally:
            browser.close()

    return f"ERROR: unknown auto action: {action}"


def _check_user_input() -> str:
    """Non-blocking check for user input typed between tasks.

    Reads any lines the user typed while a task was running.
    Returns the collected text, or empty string if nothing was typed.
    """
    if not sys.stdin.isatty():
        return ""
    lines: list[str] = []
    try:
        while select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
    except (OSError, ValueError):
        return ""
    return "\n".join(lines).strip()


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
    print(formatting.summary_header(), flush=True)
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
            formatting.system_msg(
                f"{completed_stage} complete. Run mcloop again for the next stage."
            ),
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

    print(formatting.summary_footer(), flush=True)


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
        formatting.system_msg(f"Building: {build_cmd}"),
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
            print(formatting.system_msg("Build succeeded"), flush=True)
        else:
            print(
                formatting.error_msg(f"Build failed (exit {result.returncode})"),
                flush=True,
            )
            _print_error_tail(result.stdout + result.stderr)
    except Exception as e:
        print(formatting.error_msg(f"Build error: {e}"), flush=True)


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
        changed_files: list[str] | None = None,
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
        if changed_files:
            entry += f"\n  Files: {', '.join(changed_files)}"
        self._entries.append(entry)

    def add_user_input(self, text: str) -> None:
        """Append free-form user input to context."""
        self._entries.append(f"[user] {text}")

    def text(self) -> str:
        """Return context string for inclusion in prompts."""
        return "\n".join(self._entries)


AUDIT_HASH_FILE = ".mcloop-last-audit"


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


def _should_skip_audit(project_dir: Path) -> bool:
    """Skip audit if no source files changed since last audit."""
    if not (project_dir / ".git").exists():
        return False
    hash_file = project_dir / AUDIT_HASH_FILE
    if not hash_file.exists():
        return False
    last_hash = hash_file.read_text().strip()
    if not last_hash:
        return False
    result = _git(
        ["git", "diff", "--name-only", last_hash, "HEAD"],
        cwd=project_dir,
        label="audit diff check",
    )
    if result.returncode != 0:
        return False
    changed = [
        f
        for f in result.stdout.strip().splitlines()
        if f and not f.startswith("logs/") and f != "PLAN.md" and f != AUDIT_HASH_FILE
    ]
    return len(changed) == 0


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
    """Run two rounds of audit/verify/fix to catch bugs introduced by fixes."""
    if _should_skip_audit(project_dir):
        print(
            formatting.system_msg("Audit skipped (no changes since last audit)"),
            flush=True,
        )
        return

    max_rounds = 2
    for round_num in range(1, max_rounds + 1):
        print(
            formatting.system_msg(f"Audit round {round_num}/{max_rounds}"),
            flush=True,
        )
        fixed = _run_single_audit_round(
            project_dir,
            log_dir,
            model=model,
        )
        if not fixed:
            # No bugs found or fixed — no need for another round
            if round_num == 1:
                notify("Audit complete: no bugs found.")
            else:
                notify("Audit complete: fixes verified, no new bugs.")
            break
        if round_num == max_rounds:
            notify("Audit complete: bugs fixed.")

    _save_audit_hash(project_dir)


def _run_single_audit_round(
    project_dir: Path,
    log_dir: Path,
    model: str | None = None,
) -> bool:
    """Run one audit/verify/fix cycle. Returns True if bugs were fixed."""
    bugs_path = project_dir / "BUGS.md"

    # Resume from existing BUGS.md if present
    if bugs_path.exists():
        bugs_content = bugs_path.read_text()
        if bugs_md_has_bugs(bugs_content):
            print(
                formatting.system_msg("Found existing BUGS.md, resuming fix cycle..."),
                flush=True,
            )
        else:
            print(
                formatting.system_msg("Existing BUGS.md has no bugs"),
                flush=True,
            )
            bugs_path.unlink()
            return False
    else:
        print(formatting.system_msg("Running bug audit..."), flush=True)
        audit_result = run_audit(
            project_dir,
            log_dir,
            model=model,
            existing_bugs="",
        )
        if not audit_result.success:
            print(
                f"audit: session exited with code {audit_result.exit_code}, skipping fix",
                flush=True,
            )
            return False

        if not bugs_path.exists():
            print(
                "audit: BUGS.md not written, skipping fix",
                flush=True,
            )
            return False

        bugs_content = bugs_path.read_text()
        if not bugs_md_has_bugs(bugs_content):
            print("audit: no bugs found", flush=True)
            bugs_path.unlink()
            return False

    # Pre-fix verification: check each bug against source code
    bugs_content = bugs_path.read_text()
    parsed_bugs = parse_bugs_md(bugs_content)
    if parsed_bugs:
        print(
            formatting.system_msg(f"Verifying {len(parsed_bugs)} bugs..."),
            flush=True,
        )
        verify_result = run_bug_verify(
            project_dir,
            log_dir,
            bugs_content,
            model=model,
        )
        if verify_result.success:
            verdicts = parse_verification_output(
                verify_result.output,
            )
            for status, header, reason in verdicts:
                if status == "CONFIRMED":
                    print(
                        f"  CONFIRMED: {header}",
                        flush=True,
                    )
                else:
                    suffix = f" ({reason})" if reason else ""
                    print(
                        f"  REMOVED: {header}{suffix}",
                        flush=True,
                    )

            if verdicts:
                removed_headers = {h for s, h, _ in verdicts if s == "REMOVED"}
                # A bug is removed if any REMOVED verdict
                # matches its title (substring match).
                confirmed_bugs = [
                    b
                    for b in parsed_bugs
                    if not any(b["title"] in rh or rh in b["title"] for rh in removed_headers)
                ]
                if not confirmed_bugs:
                    print(
                        formatting.system_msg("All reported bugs were false positives."),
                        flush=True,
                    )
                    bugs_path.unlink(missing_ok=True)
                    return False
                if len(confirmed_bugs) < len(parsed_bugs):
                    new_content = "# Bugs\n\n"
                    for bug in confirmed_bugs:
                        new_content += bug["body"] + "\n\n"
                    bugs_path.write_text(new_content)
                    bugs_content = new_content

    max_fix_attempts = 3
    for attempt in range(1, max_fix_attempts + 1):
        print(
            formatting.system_msg(f"Fixing bugs (attempt {attempt}/{max_fix_attempts})..."),
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
            # Post-fix review: verify changes don't introduce new bugs
            diff = _get_diff(project_dir)
            if diff:
                print(
                    formatting.system_msg("Post-fix review..."),
                    flush=True,
                )
                review_result = run_post_fix_review(
                    project_dir,
                    log_dir,
                    bugs_content,
                    diff,
                    model=model,
                )
                if review_result.success:
                    found, desc = review_found_problems(
                        review_result.output,
                    )
                    if found:
                        print(
                            formatting.error_msg("Post-fix review found problems"),
                            flush=True,
                        )
                        for line in desc.splitlines()[:10]:
                            print(f"    {line}", flush=True)
                        bugs_content = bugs_content + "\n\n## Post-fix review problems\n" + desc
                        bugs_path.write_text(bugs_content)
                        continue
                    print(
                        formatting.system_msg("Post-fix review: no new bugs introduced"),
                        flush=True,
                    )

            try:
                _commit(project_dir, "Fix bugs from audit")
            except RuntimeError as exc:
                print(
                    formatting.error_msg(str(exc)),
                    flush=True,
                )
                sys.exit(1)
            bugs_path.unlink(missing_ok=True)
            return True

        error_ctx = f"Command: {check_result.command}\n" + _tail(check_result.output, 50)
        print(
            formatting.error_msg(f"Bug fix checks failed (attempt {attempt}/{max_fix_attempts})"),
            flush=True,
        )
        _print_error_tail(check_result.output)

        # Append error to BUGS.md so next attempt sees it
        bugs_path.write_text(
            bugs_content + "\n\n## Post-fix check failure\n" + error_ctx,
        )

    return False


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
        result = _git(
            ["git", "remote"],
            cwd=project_dir,
            label="commit remote recheck",
        )
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
