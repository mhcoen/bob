"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json as _json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from mcloop import formatting
from mcloop.audit import _run_audit_fix_cycle
from mcloop.checklist import (
    Task,
    check_off,
    current_stage,
    find_next,
    get_eliminated,
    get_stages,
    has_unchecked_bugs,
    is_auto_task,
    is_user_task,
    mark_failed,
    parse,
    parse_auto_task,
    parse_description,
    stage_status,
    user_task_instructions,
)
from mcloop.checks import detect_build, detect_run, get_check_commands, run_checks
from mcloop.errors import (
    _check_errors_json,
)
from mcloop.git_ops import (
    _changed_files,
    _checkpoint,
    _commit,
    _ensure_git,
    _git,
    _has_meaningful_changes,
    _push_or_die,
)
from mcloop.investigate_cmd import (
    _cmd_investigate,
    _handle_auto_task,
    _handle_user_task,
    _launch_app_verification,
)
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
    run_audit,
    run_task,
)
from mcloop.session_context import SessionContext
from mcloop.sync_cmd import _cmd_sync

# Phase tracking for interrupt state capture
_current_phase = ""  # task, checks, audit, user_prompt
_current_task_label = ""
_current_task_text = ""
_phase_start_time = 0.0
_project_dir: Path | None = None


def _save_interrupt_state() -> None:
    """Write .mcloop/interrupted.json with current state.

    Called from the signal handler. Uses only synchronous file
    I/O and module-level state. No API calls.
    """
    import mcloop.runner as _runner

    if _project_dir is None:
        return
    mcloop_dir = _project_dir / ".mcloop"
    mcloop_dir.mkdir(exist_ok=True)
    elapsed = time.monotonic() - _phase_start_time if _phase_start_time else 0
    last_lines = list(_runner._last_output_lines)
    state = {
        "task_label": _current_task_label,
        "task_text": _current_task_text,
        "phase": _current_phase,
        "elapsed_seconds": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_output": last_lines,
    }
    try:
        (mcloop_dir / "interrupted.json").write_text(_json.dumps(state, indent=2) + "\n")
    except OSError:
        pass


def _check_interrupted(
    project_dir: Path,
    checklist_path: Path,
) -> str | None:
    """Check for interrupted.json and prompt the user.

    Returns:
        "retry" to proceed normally
        "skip" to mark task [!] and move on
        "quit" to exit
        None if no interrupted state found
    """
    state_file = project_dir / ".mcloop" / "interrupted.json"
    if not state_file.exists():
        return None
    try:
        state = _json.loads(state_file.read_text())
    except (OSError, _json.JSONDecodeError):
        state_file.unlink(missing_ok=True)
        return None

    phase = state.get("phase", "task")
    label = state.get("task_label", "?")
    text = state.get("task_text", "unknown")
    elapsed = state.get("elapsed_seconds", 0)
    last_output = state.get("last_output", [])
    timestamp = state.get("timestamp", "")

    print(
        formatting.summary_header(),
        flush=True,
    )
    print(
        f"  Previous run was interrupted during {phase} phase ({timestamp})",
        flush=True,
    )
    print(
        f"  Task {label}: {text}",
        flush=True,
    )
    print(
        f"  Running for {_format_elapsed(elapsed)}",
        flush=True,
    )
    if last_output:
        print("  Last output:", flush=True)
        for line in last_output[-5:]:
            print(f"    {line}", flush=True)
    print(
        formatting.summary_footer(),
        flush=True,
    )

    if phase == "user_prompt":
        print(
            "  Resuming where you left off.",
            flush=True,
        )
        state_file.unlink(missing_ok=True)
        return "retry"

    if phase == "audit":
        print(
            "  (r)esume audit / (s)kip audit / (q)uit",
            flush=True,
        )
    else:
        print(
            "  (r)etry / (d)escribe what went wrong / (s)kip / (q)uit",
            flush=True,
        )

    try:
        choice = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "q"

    if choice == "q":
        state_file.unlink(missing_ok=True)
        print("Exiting.", flush=True)
        sys.exit(0)

    if choice == "s":
        # Mark task as failed
        tasks = parse(checklist_path)
        for t in _all_tasks(tasks):
            if t.text.strip() == text.strip() and not t.checked:
                mark_failed(checklist_path, t)
                break
        state_file.unlink(missing_ok=True)
        return "skip"

    if choice == "d" and phase != "audit":
        print(
            "  Describe what went wrong (press Enter twice to finish):",
            flush=True,
        )
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        description = " ".join(lines).strip()
        if description:
            _write_ruledout_to_plan(
                checklist_path,
                text,
                description,
            )
            _write_eliminated_json(
                project_dir,
                label,
                description,
            )
            print(
                f"  Recorded: [RULEDOUT] {description}",
                flush=True,
            )
        state_file.unlink(missing_ok=True)
        return "retry"

    # Default: retry
    state_file.unlink(missing_ok=True)
    return "retry"


def _all_tasks(tasks: list[Task]) -> list[Task]:
    """Flatten the task tree into a list."""
    result: list[Task] = []
    for t in tasks:
        result.append(t)
        result.extend(_all_tasks(t.children))
    return result


def _write_ruledout_to_plan(
    checklist_path: Path,
    task_text: str,
    description: str,
) -> None:
    """Append a [RULEDOUT] line under a task in PLAN.md."""
    lines = checklist_path.read_text().splitlines()
    from mcloop.checklist import CHECKBOX_RE

    for i, line in enumerate(lines):
        m = CHECKBOX_RE.match(line)
        if m and m.group(3).strip() == task_text.strip():
            indent = len(m.group(1))
            ruledout_line = " " * (indent + 2) + f"[RULEDOUT] {description}"
            lines.insert(i + 1, ruledout_line)
            checklist_path.write_text("\n".join(lines) + "\n")
            return


def _write_eliminated_json(
    project_dir: Path,
    task_label: str,
    description: str,
) -> None:
    """Append an entry to .mcloop/eliminated.json."""
    elim_path = project_dir / ".mcloop" / "eliminated.json"
    try:
        data = _json.loads(elim_path.read_text())
    except (OSError, _json.JSONDecodeError):
        data = {}
    if task_label not in data:
        data[task_label] = []
    data[task_label].append(
        {
            "approach": description,
            "timestamp": time.strftime("%Y-%m-%d"),
        }
    )
    elim_path.write_text(_json.dumps(data, indent=2) + "\n")


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
    """Kill any active claude subprocess and its process group with SIGKILL.

    Used by the atexit handler where graceful shutdown is not possible.
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


def _graceful_kill_active_process() -> None:
    """Send SIGTERM to the child process group, escalate to SIGKILL after 2s.

    Called by the signal handler. Sends SIGTERM first to give the child
    process group a chance to clean up. If the group does not exit within
    2 seconds, escalates to SIGKILL.
    """
    import mcloop.runner as _runner

    proc = _runner._active_process
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        pgid = proc.pid
    # Send SIGTERM to the entire process group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    # Wait up to 2 seconds for graceful exit
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Escalate to SIGKILL
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    _runner._active_process = None


def main() -> None:
    import atexit

    atexit.register(_kill_active_process)

    def _handle_sigint(sig, frame):
        import mcloop.runner as _runner

        _runner._interrupted = True
        print("\nInterrupted. Saving state...", flush=True)
        _save_interrupt_state()
        _graceful_kill_active_process()
        print("State saved. Exiting.", flush=True)
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTSTP, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    signal.signal(signal.SIGHUP, _handle_sigint)
    _main()


def _main() -> None:
    args = _parse_args()
    checklist_path = Path(args.file).resolve()

    # The wrap subcommand works on any project directory — it does not
    # need a checklist file because it detects the language from file
    # extensions and build system files.
    if args.command == "wrap":
        _cmd_wrap(checklist_path.parent)
        return

    if args.command == "install":
        _cmd_install(checklist_path.parent, dry_run=args.dry_run)
        return

    if args.command == "uninstall":
        _cmd_uninstall(checklist_path.parent, dry_run=args.dry_run)
        return

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
        _cmd_investigate(args, checklist_path)
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
    global _project_dir, _current_phase, _current_task_label
    global _current_task_text, _phase_start_time
    project_dir = checklist_path.parent
    _project_dir = project_dir
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Check for interrupted state from a previous Ctrl-C
    interrupt_action = _check_interrupted(project_dir, checklist_path)
    if interrupt_action == "quit":
        return []

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    project_checks = get_check_commands(project_dir)

    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    _checkpoint(project_dir, verbose=True)
    _push_or_die(project_dir)

    # Check for crash errors from previous runs
    if not _check_errors_json(project_dir, model=model):
        return []

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

    # Bug-only mode: when ## Bugs has unchecked items, work only those
    # tasks. Do not fall through to feature tasks, do not start the
    # next stage, do not run the audit cycle.
    initial_tasks = parse(checklist_path)
    bug_only = has_unchecked_bugs(initial_tasks)
    if bug_only:
        print(
            formatting.system_msg("Bug-only mode: fixing bugs before continuing"),
            flush=True,
        )

    while True:
        tasks = parse(checklist_path)
        task = find_next(tasks)
        if task is None:
            break

        # In bug-only mode, stop when no more bug tasks remain
        if bug_only and task.stage != "Bugs":
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
            _current_phase = "user_prompt"
            _current_task_label = label
            _current_task_text = task.text
            _phase_start_time = time.monotonic()
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

        _current_phase = "task"
        _current_task_label = label
        _current_task_text = task.text
        _phase_start_time = time.monotonic()

        eliminated = get_eliminated(tasks, task)
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
                    eliminated=eliminated,
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
                    # No file changes — but maybe the work was already done.
                    # Run checks: if they pass, auto-check the task.
                    noop_check = run_checks(project_dir)
                    if noop_check.passed:
                        check_off(checklist_path, task)
                        elapsed = _format_elapsed(
                            time.monotonic() - task_start,
                        )
                        completed.append(f"{label}) {task.text}")
                        print(
                            "Task already satisfied (no changes needed)",
                            flush=True,
                        )
                        print(
                            formatting.task_complete(label, elapsed),
                            flush=True,
                        )
                        ctx.add(label, task.text, elapsed, result.output)
                        success = True
                        break
                    last_error = "Task produced no file changes"
                    print(
                        formatting.error_msg(
                            f"No-op task (attempt {attempt}/{max_retries}): {task.text}"
                        ),
                        flush=True,
                    )
                    continue

                _current_phase = "checks"
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
                    _maybe_auto_wrap(project_dir)
                    _reinject_wrappers(project_dir)
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

    # Bug-only mode: verify the fix by launching the app, then exit.
    # Skip stage transitions, audit cycle, and build.
    if bug_only:
        remaining_bugs = has_unchecked_bugs(parse(checklist_path))
        if remaining_bugs:
            print(
                formatting.error_msg("Bug-only mode: some bugs could not be fixed"),
                flush=True,
            )
        else:
            print(
                formatting.system_msg("Bug-only mode: all bugs fixed"),
                flush=True,
            )
            # Verify the fix by launching the app
            failure = _launch_app_verification(project_dir)
            if failure:
                print(
                    formatting.error_msg(f"Bug verification failed: {failure}"),
                    flush=True,
                )
            else:
                print(
                    formatting.system_msg("Bug verification passed"),
                    flush=True,
                )
                # Clear errors.json now that all bugs are fixed and verified
                errors_path = project_dir / ".mcloop" / "errors.json"
                if errors_path.is_file():
                    errors_path.unlink()
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
        stuck = [
            t.text
            for t in parse(checklist_path)
            if t.stage == "Bugs" and not t.checked and not t.failed
        ]
        return stuck

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
        _current_phase = "audit"
        _phase_start_time = time.monotonic()
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
    subparsers.add_parser("wrap", help="Instrument source files with error-catching hooks")
    install_parser = subparsers.add_parser("install", help="Install mcloop into the project")
    install_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be installed without doing it"
    )
    uninstall_parser = subparsers.add_parser("uninstall", help="Remove mcloop from the project")
    uninstall_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be removed without doing it"
    )
    inv_parser = subparsers.add_parser("investigate", help="Investigate a bug in a worktree")
    inv_parser.add_argument(
        "description", nargs="?", default=None, help="Short description of the bug"
    )
    inv_parser.add_argument("--log", default=None, help="Path to a log file with error output")
    return parser.parse_args()


def _cmd_wrap(project_dir: Path) -> None:
    """Instrument the project's source files with error-catching hooks."""
    from mcloop.wrap import wrap_project

    try:
        language, entry = wrap_project(project_dir)
    except ValueError as exc:
        print(f"wrap: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Instrumented {entry.relative_to(project_dir)} ({language})")
    print("Canonical wrappers saved to .mcloop/wrap/")


def _print_file_diff(
    path: Path,
    old_content: str,
    new_content: str,
) -> None:
    """Print a unified diff of what a file operation would produce."""
    diff = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )
    for line in diff:
        print(f"    {line.rstrip()}")


def _cmd_install(project_dir: Path, *, dry_run: bool = False) -> None:
    """Install mcloop into the project directory."""
    claude_path = shutil.which("claude")
    if not claude_path:
        print(
            "Error: 'claude' not found on PATH.\n"
            "\n"
            "Install Claude Code:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "\n"
            "Then re-run: mcloop install",
            file=sys.stderr,
        )
        sys.exit(1)

    result = subprocess.run(
        [claude_path, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        print(
            f"Error: 'claude --version' failed (exit {result.returncode}).\n"
            "Check your Claude Code installation and re-run: mcloop install",
            file=sys.stderr,
        )
        sys.exit(1)

    version = result.stdout.strip()
    print(f"Found claude: {version}")

    summary: list[tuple[str, str]] = []
    summary.extend(_install_hooks(dry_run=dry_run))
    summary.extend(_merge_settings(dry_run=dry_run))
    summary.append(_setup_telegram(dry_run=dry_run))
    summary.append(_setup_api_key(dry_run=dry_run))
    summary.append(_setup_sandbox(dry_run=dry_run))
    summary.append(_install_recommended_permissions(dry_run=dry_run))
    rtk_status = _check_rtk()
    if rtk_status:
        summary.append(rtk_status)

    _print_install_summary(summary, dry_run=dry_run)


def _print_install_summary(summary: list[tuple[str, str]], *, dry_run: bool = False) -> None:
    """Print a summary table of everything configured, skipped, or pending."""
    prefix = "(dry run) " if dry_run else ""
    print(f"\n{prefix}Install summary:")
    print("  " + "-" * 50)
    for component, status in summary:
        print(f"  {component:<28} {status}")
    print("  " + "-" * 50)

    manual = [(c, s) for c, s in summary if "manual" in s.lower()]
    if manual:
        print("\n  Action needed:")
        for component, status in manual:
            print(f"    - {component}: {status}")
        print()


def _check_rtk() -> tuple[str, str] | None:
    """Print a note if rtk is on PATH."""
    if shutil.which("rtk"):
        print(
            "\n"
            "  Note: RTK detected on PATH.\n"
            "  RTK hooks should be configured separately via: rtk init\n"
        )
        return ("RTK", "detected — configure manually via rtk init")
    return None


_TELEGRAM_ENV_FILE = Path.home() / ".claude" / "telegram-hook.env"

_TELEGRAM_DESKTOP_MSG = (
    "\n"
    "  Tip: install the Telegram Desktop app alongside the mobile app\n"
    "  so you can approve tool calls from your computer.\n"
)


def _setup_telegram(*, dry_run: bool = False) -> tuple[str, str]:
    """Check for Telegram credentials or prompt interactively."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if token and chat_id:
        print("Telegram: using credentials from environment variables.")
        content = f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n"
        if dry_run:
            old = ""
            if _TELEGRAM_ENV_FILE.exists():
                old = _TELEGRAM_ENV_FILE.read_text()
            _print_file_diff(_TELEGRAM_ENV_FILE, old, content)
        else:
            _TELEGRAM_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TELEGRAM_ENV_FILE.write_text(content)
        print(_TELEGRAM_DESKTOP_MSG)
        return ("Telegram", "configured (env vars)")

    if _TELEGRAM_ENV_FILE.exists():
        print(f"Telegram: using existing credentials from {_TELEGRAM_ENV_FILE}")
        print(_TELEGRAM_DESKTOP_MSG)
        return ("Telegram", "skipped (already configured)")

    print("\nTelegram setup (for remote approval notifications):")
    print("  1. Message @BotFather on Telegram to create a bot")
    print("  2. Copy the bot token")
    print("  3. Send a message to your bot, then get your chat ID\n")

    if dry_run:
        print("  (dry run: skipping interactive prompt)")
        return ("Telegram", "skipped (dry run)")

    try:
        bot_token = input("  Bot token: ").strip()
        if not bot_token:
            print("Skipped: no bot token entered.", file=sys.stderr)
            return ("Telegram", "skipped (no token entered)")
        chat_id_input = input("  Chat ID: ").strip()
        if not chat_id_input:
            print("Skipped: no chat ID entered.", file=sys.stderr)
            return ("Telegram", "skipped (no chat ID entered)")
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped: Telegram setup cancelled.")
        return ("Telegram", "skipped (cancelled)")

    _TELEGRAM_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TELEGRAM_ENV_FILE.write_text(
        f"TELEGRAM_BOT_TOKEN={bot_token}\nTELEGRAM_CHAT_ID={chat_id_input}\n"
    )
    print(f"  Saved credentials to {_TELEGRAM_ENV_FILE}")
    print(_TELEGRAM_DESKTOP_MSG)
    return ("Telegram", "configured")


_MCLOOP_CONFIG = Path.home() / ".mcloop" / "config.json"


def _load_mcloop_config() -> dict:
    """Load ~/.mcloop/config.json, returning {} if missing or invalid."""
    if not _MCLOOP_CONFIG.exists():
        return {}
    try:
        return _json.loads(_MCLOOP_CONFIG.read_text())
    except (_json.JSONDecodeError, OSError):
        return {}


def _setup_api_key(*, dry_run: bool = False) -> tuple[str, str]:
    """Ask whether to keep ANTHROPIC_API_KEY. Default is no."""
    config = _load_mcloop_config()
    if "keep_anthropic_api_key" in config:
        choice = config["keep_anthropic_api_key"]
        label = "keep (use API credits)" if choice else "strip (use subscription)"
        print(f"ANTHROPIC_API_KEY: {label} (from {_MCLOOP_CONFIG})")
        return ("API key", f"skipped ({label})")

    print(
        "\nANTHROPIC_API_KEY handling:"
        "\n  By default, mcloop strips ANTHROPIC_API_KEY from the environment"
        "\n  so Claude Code uses your subscription instead of billing API credits."
        "\n  If you want to use API credits instead, answer yes.\n"
    )

    if dry_run:
        new_config = dict(config)
        new_config["keep_anthropic_api_key"] = False
        new_content = _json.dumps(new_config, indent=2) + "\n"
        old = _MCLOOP_CONFIG.read_text() if _MCLOOP_CONFIG.exists() else ""
        _print_file_diff(_MCLOOP_CONFIG, old, new_content)
        print("  (dry run: would use default — strip key)")
        return ("API key", "would configure (dry run)")

    try:
        answer = input("  Keep ANTHROPIC_API_KEY? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped: using default (strip key).")
        answer = ""

    keep = answer in ("y", "yes")
    label = "keep (use API credits)" if keep else "strip (use subscription)"
    print(f"  ANTHROPIC_API_KEY: {label}")

    config["keep_anthropic_api_key"] = keep
    _MCLOOP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _MCLOOP_CONFIG.write_text(_json.dumps(config, indent=2) + "\n")
    print(f"  Saved to {_MCLOOP_CONFIG}")
    return ("API key", f"configured ({label})")


_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

_SANDBOX_DEFAULTS = {
    "enabled": True,
    "autoAllowBashIfSandboxed": True,
    "allowUnsandboxedCommands": False,
}


def _setup_sandbox(*, dry_run: bool = False) -> tuple[str, str]:
    """Ask whether to enable Claude Code sandbox. Will enable, never disable."""
    settings_path = _CLAUDE_SETTINGS

    original_content = ""
    settings: dict = {}
    if settings_path.exists():
        original_content = settings_path.read_text()
        try:
            settings = _json.loads(original_content)
        except _json.JSONDecodeError:
            print(
                f"Error: {settings_path} contains invalid JSON.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not isinstance(settings, dict):
        print(
            f"Error: {settings_path} is not a JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    sandbox = settings.get("sandbox", {})
    if isinstance(sandbox, dict) and sandbox.get("enabled") is True:
        print("Sandbox: already enabled (skipping).")
        return ("Sandbox", "skipped (already enabled)")

    print(
        "\nSandbox mode:"
        "\n  The sandbox restricts file system and network access for"
        "\n  Claude Code sessions, adding a layer of protection when"
        "\n  running unattended.\n"
    )

    if dry_run:
        sandbox_cfg = dict(_SANDBOX_DEFAULTS)
        if isinstance(sandbox, dict):
            new_sandbox = dict(sandbox)
            new_sandbox.update(sandbox_cfg)
        else:
            new_sandbox = sandbox_cfg
        settings["sandbox"] = new_sandbox
        new_content = _json.dumps(settings, indent=2) + "\n"
        _print_file_diff(settings_path, original_content, new_content)
        print("  (dry run: would enable sandbox by default)")
        return ("Sandbox", "would enable (dry run)")

    try:
        answer = input("  Enable sandbox? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped: sandbox not enabled.")
        return ("Sandbox", "skipped (cancelled)")

    if answer in ("n", "no"):
        print("  Sandbox: not enabled.")
        return ("Sandbox", "not enabled")

    sandbox_cfg = dict(_SANDBOX_DEFAULTS)
    if isinstance(sandbox, dict):
        sandbox.update(sandbox_cfg)
    else:
        sandbox = sandbox_cfg
    settings["sandbox"] = sandbox

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(_json.dumps(settings, indent=2) + "\n")
    print("  Sandbox: enabled.")
    print(f"  Saved to {settings_path}")
    return ("Sandbox", "configured (enabled)")


_RECOMMENDED_PERMS_DEST = Path.home() / ".mcloop" / "recommended-permissions.json"


def _install_recommended_permissions(
    *,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Install recommended permissions baseline for manual merging."""
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "settings.example.json"

    if not src.exists():
        print(
            f"Warning: settings.example.json not found: {src}",
            file=sys.stderr,
        )
        return ("Permissions", "warning (settings.example.json not found)")

    raw = src.read_text()
    try:
        example = _json.loads(raw)
    except _json.JSONDecodeError:
        print(
            f"Warning: settings.example.json contains invalid JSON: {src}",
            file=sys.stderr,
        )
        return ("Permissions", "warning (invalid JSON)")

    perms = example.get("permissions", {})
    if not isinstance(perms, dict):
        perms = {}

    allow = perms.get("allow", [])
    recommended = {"permissions": {"allow": allow}}

    dest = _RECOMMENDED_PERMS_DEST
    new_content = _json.dumps(recommended, indent=2) + "\n"
    if dry_run:
        old = dest.read_text() if dest.exists() else ""
        _print_file_diff(dest, old, new_content)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content)
        print(f"  installed: {dest}")

    print(
        "\n  McLoop does not modify runtime permissions."
        "\n  Recommended permission settings are provided in:"
        f"\n    {dest}"
        "\n  Merge them into ~/.claude/settings.json manually"
        "\n  if desired.\n"
    )
    return ("Permissions", "installed — merge manually")


# Hook scripts to copy: (source filename in repo root, dest filename)
_HOOK_SCRIPTS = [
    "telegram-permission-hook.py",
    "session-start-hook.py",
]


def _install_hooks(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Copy hook scripts to ~/.mcloop/hooks/. Skip if already present."""
    repo_root = Path(__file__).resolve().parent.parent
    hooks_dir = Path.home() / ".mcloop" / "hooks"

    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, str]] = []
    for script_name in _HOOK_SCRIPTS:
        src = repo_root / script_name
        dest = hooks_dir / script_name
        label = f"Hook ({script_name})"

        if not src.exists():
            print(
                f"Warning: hook source not found: {src}",
                file=sys.stderr,
            )
            results.append((label, "warning (source not found)"))
            continue

        if dest.exists():
            print(f"  skip (exists): {dest}")
            results.append((label, "skipped (already installed)"))
            continue

        if dry_run:
            print(f"  would copy: {src} -> {dest}")
            results.append((label, "would install (dry run)"))
        else:
            shutil.copy2(src, dest)
            print(f"  copied: {dest}")
            results.append((label, "installed"))
    return results


# Hook entries to merge into ~/.claude/settings.json
_HOOK_ENTRIES = {
    "hooks": {
        "PreToolUse": [
            {
                "type": "command",
                "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
            },
        ],
        "SessionStart": [
            {
                "type": "command",
                "command": "python3 ~/.mcloop/hooks/session-start-hook.py",
            },
        ],
    },
}


def _merge_settings(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Merge mcloop hook entries into ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    original_content = ""

    if settings_path.exists():
        original_content = settings_path.read_text()
        try:
            settings = _json.loads(original_content)
        except _json.JSONDecodeError:
            print(
                f"Error: {settings_path} contains invalid JSON.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        settings = {}

    if not isinstance(settings, dict):
        print(
            f"Error: {settings_path} is not a JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    hooks = settings.setdefault("hooks", {})
    changed = False
    results: list[tuple[str, str]] = []

    for event_name, entries in _HOOK_ENTRIES["hooks"].items():
        existing = hooks.setdefault(event_name, [])
        existing_commands = {e.get("command") for e in existing if isinstance(e, dict)}
        for entry in entries:
            label = f"Settings ({event_name})"
            if entry["command"] in existing_commands:
                print(f"  skip (exists): hooks.{event_name}: {entry['command']}")
                results.append((label, "skipped (already configured)"))
            else:
                existing.append(entry)
                changed = True
                if dry_run:
                    print(f"  would add: hooks.{event_name}: {entry['command']}")
                    results.append((label, "would add (dry run)"))
                else:
                    print(f"  added: hooks.{event_name}: {entry['command']}")
                    results.append((label, "configured"))

    if changed:
        new_content = _json.dumps(settings, indent=2) + "\n"
        if dry_run:
            _print_file_diff(settings_path, original_content, new_content)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(new_content)
    return results


def _cmd_uninstall(project_dir: Path, *, dry_run: bool = False) -> None:
    """Remove mcloop from the project directory."""
    print("uninstall: not yet implemented", file=sys.stderr)
    sys.exit(1)


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
        parts = shlex.split(build_cmd)
    except ValueError:
        print(formatting.error_msg(f"Malformed build command: {build_cmd}"), flush=True)
        return
    try:
        result = subprocess.run(
            parts,
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


def _maybe_auto_wrap(project_dir: Path) -> None:
    """Auto-inject crash handlers after first task producing a runnable app.

    Triggered once: when detect_run() returns a command and no canonical
    wrappers exist yet in .mcloop/wrap/.
    """
    from mcloop.wrap import wrap_project

    # Already wrapped — canonical wrappers exist
    wrap_dir = project_dir / ".mcloop" / "wrap"
    if wrap_dir.is_dir() and any(wrap_dir.iterdir()):
        return

    # Not a runnable app (yet)
    run_cmd = detect_run(project_dir)
    if not run_cmd:
        return

    try:
        wrap_project(project_dir)
    except ValueError:
        return

    print("Injected crash handlers.", flush=True)

    _git(["git", "add", "-A"], cwd=project_dir, label="auto-wrap add")
    _git(
        ["git", "commit", "-m", "Inject mcloop crash handlers"],
        cwd=project_dir,
        label="auto-wrap commit",
    )
    push_result = _git(
        ["git", "push"],
        cwd=project_dir,
        label="auto-wrap push",
        silent=True,
    )
    if push_result.returncode != 0:
        print(
            formatting.error_msg("Push after auto-wrap failed"),
            flush=True,
        )


def _reinject_wrappers(project_dir: Path) -> None:
    """Re-inject crash handler wrappers if markers were stripped.

    Called after each task commit. Checks whether .mcloop/wrap/
    canonical wrappers exist and the entry point still has intact
    markers. If markers are missing or damaged, re-injects from
    the canonical source and commits the fix.
    """
    from mcloop.wrap import (
        find_entry_point,
        has_markers,
        inject,
    )

    wrap_dir = project_dir / ".mcloop" / "wrap"
    if not wrap_dir.is_dir():
        return

    # Determine language from which canonical wrapper exists
    if (wrap_dir / "swift_wrapper.swift").exists():
        language = "swift"
    elif (wrap_dir / "python_wrapper.py").exists():
        language = "python"
    else:
        return

    entry = find_entry_point(project_dir, language)
    if entry is None:
        return

    try:
        content = entry.read_text()
    except OSError:
        return

    if has_markers(content, language):
        return

    # Markers missing — re-inject
    print(
        formatting.system_msg("Re-injecting crash handler wrappers"),
        flush=True,
    )
    restored = inject(content, language, str(project_dir))
    entry.write_text(restored)
    _git(["git", "add", "-A"], cwd=project_dir, label="reinject add")
    _git(
        ["git", "commit", "-m", "Re-inject mcloop crash handlers"],
        cwd=project_dir,
        label="reinject commit",
    )
    push_result = _git(
        ["git", "push"],
        cwd=project_dir,
        label="reinject push",
        silent=True,
    )
    if push_result.returncode != 0:
        print(
            formatting.error_msg("Push after re-injection failed"),
            flush=True,
        )
