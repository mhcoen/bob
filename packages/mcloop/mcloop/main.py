"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import dataclasses
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

import mcloop.lifecycle as _lifecycle
from mcloop import formatting
from mcloop.audit import _run_audit_fix_cycle
from mcloop.checklist import (
    Task,
    check_off,
    current_stage,
    find_next,
    find_parent,
    get_batch_children,
    get_eliminated,
    has_unchecked_bugs,
    is_auto_task,
    is_batch_task,
    is_user_task,
    mark_failed,
    parse,
    parse_auto_task,
    parse_description,
    purge_completed_bugs,
    stage_status,
    user_task_instructions,
)
from mcloop.checklist import (
    task_label as _task_label,
)
from mcloop.checks import detect_build, detect_run, get_check_commands, run_checks
from mcloop.claude_md_check import (
    auto_update_claude_md,
    check_claude_md_freshness,
)
from mcloop.config import format_reviewer_status, load_reviewer_config
from mcloop.errors import (
    _check_errors_json,
)
from mcloop.formatting import format_elapsed as _format_elapsed
from mcloop.git_ops import (
    _changed_files,
    _checkpoint,
    _commit,
    _ensure_git,
    _git,
    _has_meaningful_changes,
    _push_or_die,
    _snapshot_worktree,
)
from mcloop.install_cmd import (
    _cmd_install,
    _cmd_uninstall,
    _load_mcloop_config,
)
from mcloop.investigate_cmd import (
    _cmd_investigate,
    _handle_auto_task,
    _handle_user_task,
    _launch_app_verification,
)
from mcloop.lifecycle import (
    _all_tasks,  # noqa: F401 — re-exported for tests
    _check_interrupted,
    _graceful_kill_active_process,  # noqa: F401 — re-exported for tests
    _kill_active_process,
    _kill_orphan_sessions,
    _save_interrupt_state,  # noqa: F401 — re-exported for tests
    _write_eliminated_json,  # noqa: F401 — re-exported for tests
    _write_ruledout_to_plan,  # noqa: F401 — re-exported for tests
    register_signal_handlers,
)
from mcloop.notify import notify
from mcloop.output import (
    _dry_run,
    _print_error_tail,
    _print_summary,
    _snapshot_notes,
    _tail,
)
from mcloop.ratelimit import (
    SESSION_LIMIT_POLL,
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    is_session_limited,
    wait_for_reset,
)
from mcloop.review_integration import (
    _cleanup_stale_reviews,
    _collect_review_findings,
    _spawn_reviewer,
    _terminate_reviewers,
)
from mcloop.runner import (
    INVESTIGATION_TOOLS,
    run_audit,
    run_task,
    warn_unknown_model,
)
from mcloop.session_context import SessionContext
from mcloop.sync_cmd import _cmd_sync


@dataclasses.dataclass(frozen=True)
class RunStatus:
    """Structured result from run_loop().

    status: "success" when all tasks completed and checks passed,
            "failure" when a task failed or checks failed,
            "interrupted" when the user interrupted the run.
    stuck:  list of task texts that could not be completed.
    detail: optional description of why the run ended.
    """

    status: str  # "success", "failure", or "interrupted"
    stuck: list[str] = dataclasses.field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "success"


def main() -> None:
    import atexit

    import mcloop.runner as _runner

    atexit.register(_kill_active_process)
    atexit.register(_terminate_reviewers)

    register_signal_handlers(_runner, cleanup_callback=_terminate_reviewers)
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
        _cmd_audit(checklist_path, model=args.model)
        return

    if args.command == "investigate":
        _cmd_investigate(args, checklist_path)
        return

    if args.dry_run:
        _dry_run(parse(checklist_path))
        return

    # Resolve CLI: command line overrides config, default is claude
    cli = args.cli or _load_mcloop_config().get("cli", "claude")

    result = run_loop(
        checklist_path,
        max_retries=args.max_retries,
        cli=cli,
        model=args.model,
        fallback_model=args.fallback_model,
        no_audit=args.no_audit,
        allowed_tools=INVESTIGATION_TOOLS if args.allow_web_tools else None,
        enable_reviewer=args.reviewer,
    )
    if not result.ok:
        sys.exit(1)


def _run_batch(
    batch_children: list[Task],
    tasks: list[Task],
    checklist_path: Path,
    project_dir: Path,
    log_dir: Path,
    description: str,
    first_label: str,
    ctx: SessionContext,
    rate_state: RateLimitState,
    cli: str,
    current_model: str | None,
    fallback_model: str | None,
    max_retries: int,
    project_checks: list[str],
    allowed_tools: str | None,
    run_start: float,
    completed: list[str],
    notes_snapshot: tuple[str, int] | None,
    reviewer_config: dict | None = None,
) -> str:
    """Run multiple subtasks in a single session.

    Combines the text of all batch_children into a single prompt
    with numbered steps. On success (checks pass), checks off all
    children. On failure, returns "failed" so the caller can fall
    back to individual execution.

    Returns "success" or "failed".
    """
    n = len(batch_children)
    labels = []
    for child in batch_children:
        labels.append(_task_label(tasks, child))

    label_range = f"{labels[0]}-{labels[-1]}"
    print(
        formatting.system_msg(f"Batching {n} subtasks ({label_range})"),
        flush=True,
    )

    # Build combined prompt
    steps = []
    for i, child in enumerate(batch_children, 1):
        steps.append(f"{i}. {child.text}")
    combined_text = "Do all of the following in order:\n" + "\n".join(steps)

    active_cli = get_available_cli(rate_state, enabled_clis=(cli,))
    if active_cli is None:
        active_cli = wait_for_reset(rate_state, notify, enabled_clis=(cli,))

    parent_label = first_label.rsplit(".", 1)[0] if "." in first_label else first_label
    ctx.update_group(parent_label, True)

    _checkpoint(
        project_dir,
        next_task=f"{label_range}) [BATCH] {n} subtasks",
    )
    pre_batch_modified, pre_batch_untracked = _snapshot_worktree(project_dir)
    print(
        formatting.task_header(
            label_range,
            f"[BATCH] {n} subtasks",
            active_cli,
        ),
        flush=True,
    )
    for step in steps:
        print(f"  {step}", flush=True)

    _lifecycle._current_phase = "task"
    _lifecycle._current_task_label = label_range
    _lifecycle._current_task_text = f"[BATCH] {n} subtasks"
    _lifecycle._phase_start_time = time.monotonic()

    # Collect eliminated from all children and parent
    all_eliminated: list[str] = []
    for child in batch_children:
        all_eliminated.extend(get_eliminated(tasks, child))
    # Deduplicate while preserving order
    seen: set[str] = set()
    eliminated: list[str] = []
    for e in all_eliminated:
        if e not in seen:
            seen.add(e)
            eliminated.append(e)

    task_start = time.monotonic()
    result = run_task(
        combined_text,
        active_cli,
        project_dir,
        log_dir,
        description,
        task_label=label_range,
        model=current_model,
        session_context=ctx.text(),
        check_commands=project_checks,
        allowed_tools=allowed_tools,
        eliminated=eliminated,
    )

    if not result.success:
        elapsed = _format_elapsed(time.monotonic() - task_start)
        print(
            formatting.error_msg(f"Batch failed ({elapsed}), will retry individually"),
            flush=True,
        )
        return "failed"

    if not _has_meaningful_changes(project_dir):
        # No changes, check if work was already done
        noop_check = run_checks(project_dir)
        if noop_check.passed:
            for child in batch_children:
                check_off(checklist_path, child)
                lbl = _task_label(tasks, child)
                completed.append(f"{lbl}) {child.text}")
            elapsed = _format_elapsed(time.monotonic() - task_start)
            print(
                f"Batch already satisfied (no changes needed) [{elapsed}]",
                flush=True,
            )
            ctx.add(
                label_range,
                combined_text,
                elapsed,
                result.output,
            )
            return "success"
        print(
            formatting.error_msg("Batch produced no changes and checks failed"),
            flush=True,
        )
        return "failed"

    _lifecycle._current_phase = "checks"
    changed_files = _changed_files(project_dir)
    check_result = run_checks(
        project_dir,
        changed_files=changed_files,
    )
    if check_result.passed:
        if not check_claude_md_freshness(changed_files, project_dir):
            if auto_update_claude_md(project_dir):
                changed_files = _changed_files(project_dir)
            else:
                print(
                    formatting.error_msg("Batch: CLAUDE.md not updated alongside source changes"),
                    flush=True,
                )
                return "failed"
        try:
            _commit(
                project_dir,
                f"[BATCH] {label_range}: {n} subtasks",
            )
        except RuntimeError as exc:
            print(
                formatting.error_msg(str(exc)),
                flush=True,
            )
            return "failed"
        if reviewer_config:
            _spawn_reviewer(project_dir)
        _maybe_auto_wrap(project_dir)
        _reinject_wrappers(project_dir)
        for child in batch_children:
            check_off(checklist_path, child)
            lbl = _task_label(tasks, child)
            completed.append(f"{lbl}) {child.text}")
        elapsed = _format_elapsed(time.monotonic() - task_start)
        print(
            formatting.task_complete(label_range, elapsed),
            flush=True,
        )
        ctx.add(
            label_range,
            combined_text,
            elapsed,
            result.output,
            changed_files=changed_files,
        )
        return "success"

    print(
        formatting.error_msg(f"Batch checks failed: {check_result.command}"),
        flush=True,
    )
    # Discard uncommitted changes from the failed batch,
    # preserving files that were dirty before the batch started.
    # Selective rollback: only revert files the batch actually touched.
    current_modified = _git(
        ["git", "diff", "--name-only"],
        cwd=project_dir,
        label="batch rollback diff",
    )
    pre_mod_set = set(pre_batch_modified)
    for f in current_modified.stdout.strip().splitlines():
        f = f.strip()
        if f and f not in pre_mod_set:
            _git(
                ["git", "checkout", "--", f],
                cwd=project_dir,
                label=f"batch rollback {f}",
            )
    # Remove only new untracked files created by the batch.
    current_untracked = _git(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir,
        label="batch rollback untracked",
    )
    pre_untracked_set = set(pre_batch_untracked)
    for f in current_untracked.stdout.strip().splitlines():
        f = f.strip()
        if f and f not in pre_untracked_set:
            fpath = project_dir / f
            if fpath.is_file():
                fpath.unlink()
            elif fpath.is_dir():
                import shutil

                shutil.rmtree(fpath)
    return "failed"


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    cli: str = "claude",
    model: str | None = None,
    fallback_model: str | None = None,
    no_audit: bool = False,
    allowed_tools: str | None = None,
    enable_reviewer: bool = False,
) -> RunStatus:
    """Run the main loop. Returns a RunStatus indicating outcome."""
    import mcloop.runner as _runner

    register_signal_handlers(_runner, cleanup_callback=_terminate_reviewers)

    project_dir = checklist_path.parent
    _lifecycle._project_dir = project_dir
    log_dir = project_dir / "logs"
    description = parse_description(checklist_path)

    # Check for interrupted state from a previous Ctrl-C
    interrupt_action = _check_interrupted(project_dir, checklist_path)
    if interrupt_action == "quit":
        return RunStatus("interrupted", detail="User quit at interrupt prompt")

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    project_checks = get_check_commands(project_dir)

    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    _checkpoint(project_dir, verbose=True)
    _push_or_die(project_dir)

    # Check for crash errors from previous runs
    if not _check_errors_json(project_dir, model=model):
        return RunStatus("failure", detail="Unresolved errors from previous run")

    # Reviewer integration: enabled by "enabled": true in config
    # OR by --reviewer flag on the command line
    reviewer_config = load_reviewer_config(str(project_dir), force=enable_reviewer)
    reviewer_status = format_reviewer_status(reviewer_config)
    if reviewer_status:
        print(
            formatting.system_msg(f"Reviewer: {reviewer_status}"),
            flush=True,
        )

    # Clean up stale review files from previous runs
    _cleanup_stale_reviews(project_dir)

    # Clean up stale pending files from previous runs
    pending_dir = project_dir / ".mcloop" / "pending"
    if pending_dir.exists():
        for f in pending_dir.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)

    notes_snapshot = _snapshot_notes(project_dir)
    ctx = SessionContext()
    run_start = time.monotonic()
    completed: list[str] = []
    failed_task: str | None = None
    failed_reason: str = ""
    batch_exhausted: set[str] = set()
    current_model = model or _load_mcloop_config().get("model")
    primary_model = current_model

    if current_model:
        warn_unknown_model(cli, current_model)

    # Bug-only mode: when ## Bugs has unchecked items, work only those
    # tasks. Do not fall through to feature tasks, do not start the
    # next stage, do not run the audit cycle.
    def _has_any_failed(task_list: list[Task]) -> Task | None:
        for t in task_list:
            if t.failed:
                return t
            found = _has_any_failed(t.children)
            if found:
                return found
        return None

    initial_tasks = parse(checklist_path)
    bug_only = has_unchecked_bugs(initial_tasks)
    if bug_only:
        print(
            formatting.system_msg("Bug-only mode: fixing bugs before continuing"),
            flush=True,
        )

    active_stage_at_start = current_stage(parse(checklist_path))

    # Count remaining tasks for the startup notification
    def _count_unchecked(task_list: list[Task]) -> int:
        n = 0
        for t in task_list:
            if not t.checked and not t.failed:
                n += 1
            n += _count_unchecked(t.children)
        return n

    remaining_count = _count_unchecked(initial_tasks)
    start_msg = f"Starting: {remaining_count} task(s) remaining"
    if active_stage_at_start:
        start_msg += f" in {active_stage_at_start}"
    if bug_only:
        start_msg = f"Starting: fixing bugs ({remaining_count} remaining)"
    notify(start_msg)
    print(
        formatting.system_msg(
            "Do not edit PLAN.md while mcloop is running."
            " Kill mcloop first, make edits, then restart."
        ),
        flush=True,
    )

    while True:
        # Check for completed reviews from background reviewer processes
        _collect_review_findings(project_dir, checklist_path, ctx)

        tasks = parse(checklist_path)

        # If any task has failed, stop the entire run.
        # A failed task is fatal: do not attempt other tasks.
        failed = _has_any_failed(tasks)
        if failed:
            notify(
                f"Fatal: previously failed task: {failed.text}",
                level="error",
            )
            total = time.monotonic() - run_start
            _print_summary(
                completed,
                f"{failed.text}",
                "Task failed in a prior attempt",
                tasks,
                total,
                project_dir,
                notes_snapshot,
            )
            return RunStatus("failure", detail=f"Previously failed task: {failed.text}")

        if active_stage_at_start is not None:
            now_stage = current_stage(tasks)
            if now_stage != active_stage_at_start:
                break

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
            has_subtasks = find_parent(tasks, task) is not None
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
            _lifecycle._current_phase = "user_prompt"
            _lifecycle._current_task_label = label
            _lifecycle._current_task_text = task.text
            _lifecycle._phase_start_time = time.monotonic()
            has_subtasks = find_parent(tasks, task) is not None
            ctx.update_group(label, has_subtasks)
            instructions = user_task_instructions(task)
            response = _handle_user_task(label, instructions)
            check_off(checklist_path, task)
            completed.append(f"{label}) {task.text}")
            ctx.add(label, task.text, "0s", response)
            notify(f"[USER] {instructions[:80]}")
            continue

        # Check for [BATCH] on the parent task.
        # If the parent is marked [BATCH], collect all batchable
        # siblings and combine them into a single session.
        # Skipped when config has "batch": false.
        parent = find_parent(tasks, task)
        batch_enabled = _load_mcloop_config().get("batch", True)
        parent_label = _task_label(tasks, parent) if parent else ""
        if (
            batch_enabled
            and parent is not None
            and is_batch_task(parent)
            and parent_label not in batch_exhausted
        ):
            batch_children = get_batch_children(parent)
            if len(batch_children) > 1:
                batch_handled = _run_batch(
                    batch_children,
                    tasks,
                    checklist_path,
                    project_dir,
                    log_dir,
                    description,
                    label,
                    ctx,
                    rate_state,
                    cli,
                    current_model,
                    fallback_model,
                    max_retries,
                    project_checks,
                    allowed_tools,
                    run_start,
                    completed,
                    notes_snapshot,
                    reviewer_config=reviewer_config,
                )
                if batch_handled == "success":
                    continue
                elif batch_handled == "failed":
                    # Fall through to individual execution
                    print(
                        formatting.system_msg("Batch failed, falling back to individual tasks"),
                        flush=True,
                    )
                    # Re-parse and re-find since batch may have
                    # partially modified state
                    batch_exhausted.add(parent_label)
                    continue

        active_cli = get_available_cli(rate_state, enabled_clis=(cli,))
        if active_cli is None:
            active_cli = wait_for_reset(rate_state, notify, enabled_clis=(cli,))

        has_subtasks = find_parent(tasks, task) is not None
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
        print(formatting.task_header(label, task.text, active_cli), flush=True)

        _lifecycle._current_phase = "task"
        _lifecycle._current_task_label = label
        _lifecycle._current_task_text = task.text
        _lifecycle._phase_start_time = time.monotonic()

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
                    active_cli,
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
                        return RunStatus(
                            "interrupted",
                            stuck=[task.text],
                            detail="User interrupted during session limit wait",
                        )
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
                    active_cli = get_available_cli(
                        rate_state,
                        enabled_clis=(cli,),
                    )
                    if active_cli is None:
                        active_cli = wait_for_reset(
                            rate_state,
                            notify,
                            enabled_clis=(cli,),
                        )
                        # Reset to primary model after cooldown
                        if fallback_model and current_model == fallback_model:
                            current_model = primary_model
                            task_model = primary_model
                            print(
                                formatting.system_msg(
                                    f"Rate limit cleared, back to model: {primary_model}"
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
                    print(
                        formatting.error_msg("No-op task, checks failing on existing code"),
                        flush=True,
                    )
                    break

                _lifecycle._current_phase = "checks"
                changed_files = _changed_files(project_dir)
                check_result = run_checks(
                    project_dir,
                    changed_files=changed_files,
                )
                if check_result.passed:
                    if not check_claude_md_freshness(changed_files, project_dir):
                        if auto_update_claude_md(project_dir):
                            changed_files = _changed_files(project_dir)
                        else:
                            last_error = "CLAUDE.md was not updated alongside source file changes"
                            print(
                                formatting.error_msg(
                                    f"CLAUDE.md not updated (attempt {attempt}/{max_retries})"
                                ),
                                flush=True,
                            )
                            continue
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
                        return RunStatus(
                            "failure",
                            detail=f"Commit failed: {exc}",
                        )
                    if reviewer_config:
                        _spawn_reviewer(project_dir)
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
            return RunStatus("failure", detail=f"Task failed: {task.text}")

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
            purge_completed_bugs(checklist_path)
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
        if stuck:
            notify(f"Bug-only mode: {len(stuck)} bug(s) could not be fixed", level="error")
            return RunStatus("failure", stuck=stuck, detail="Bug-only mode: unfixed bugs remain")
        else:
            notify("Bug-only mode: all bugs fixed")
            return RunStatus("success")

    # Check if we stopped at a stage boundary
    final_tasks = parse(checklist_path)
    status = stage_status(final_tasks)

    if status.startswith("stage_complete:"):
        done_stage = status.split(":", 1)[1]
        next_stg = current_stage(parse(checklist_path))
        print(formatting.system_msg("Running full test suite (stage boundary)..."), flush=True)
        _full_suite_start = time.monotonic()
        full_check = run_checks(project_dir)
        _full_suite_elapsed = _format_elapsed(time.monotonic() - _full_suite_start)
        if not full_check.passed:
            print(
                formatting.error_msg(
                    f"Full suite failed at stage boundary: "
                    f"{full_check.command} [{_full_suite_elapsed}]"
                ),
                flush=True,
            )
            _print_error_tail(full_check.output)
            total = time.monotonic() - run_start
            _print_summary(
                completed,
                None,
                "",
                final_tasks,
                total,
                project_dir,
                notes_snapshot,
            )
            notify(
                "Run ended with red repo: full suite failed"
                f" at stage boundary ({full_check.command})",
                level="error",
            )
            return RunStatus(
                "failure",
                detail=f"Full suite failed at stage boundary: {full_check.command}",
            )
        else:
            print(
                formatting.system_msg(f"Full test suite passed [{_full_suite_elapsed}]"),
                flush=True,
            )
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
        return RunStatus("success")

    # Full test suite at end of run
    print(formatting.system_msg("Running full test suite (end of run)..."), flush=True)
    _full_suite_start = time.monotonic()
    full_check = run_checks(project_dir)
    _full_suite_elapsed = _format_elapsed(time.monotonic() - _full_suite_start)
    if not full_check.passed:
        print(
            formatting.error_msg(
                f"Full suite failed at end of run: {full_check.command} [{_full_suite_elapsed}]"
            ),
            flush=True,
        )
        _print_error_tail(full_check.output)
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
        notify(
            f"Run ended with red repo: full suite failed at end of run ({full_check.command})",
            level="error",
        )
        return RunStatus(
            "failure",
            detail=f"Full suite failed at end of run: {full_check.command}",
        )

    print(formatting.system_msg(f"Full test suite passed [{_full_suite_elapsed}]"), flush=True)

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
        _lifecycle._current_phase = "audit"
        _lifecycle._phase_start_time = time.monotonic()
        _audit_start = time.monotonic()
        _run_audit_fix_cycle(
            project_dir,
            log_dir,
            model=model,
        )
        _audit_elapsed = _format_elapsed(time.monotonic() - _audit_start)
        print(formatting.system_msg(f"Audit completed [{_audit_elapsed}]"), flush=True)

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
    return RunStatus("success")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loop: grind through a markdown checklist")
    parser.add_argument("--file", default="PLAN.md", help="Checklist file (default: PLAN.md)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would run")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    parser.add_argument("--model", default=None, help="Model to use (e.g., opus, sonnet, gpt-5.4)")
    parser.add_argument(
        "--cli",
        default=None,
        choices=["claude", "codex"],
        help="CLI backend (default: claude, or set in ~/.mcloop/config.json)",
    )
    parser.add_argument(
        "--fallback-model",
        default=None,
        help="Model to use when the primary model is rate-limited",
    )
    parser.add_argument(
        "--no-audit", action="store_true", help="Skip the post-completion bug audit cycle"
    )
    parser.add_argument(
        "--reviewer",
        action="store_true",
        help="Enable background code reviewer (requires OPENROUTER_API_KEY)",
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


def _cmd_audit(checklist_path: Path, model: str | None = None) -> None:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = checklist_path.parent
    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    log_dir = project_dir / "logs"
    bugs_path = project_dir / "BUGS.md"
    existing = bugs_path.read_text() if bugs_path.exists() else ""
    result = run_audit(project_dir, log_dir, model=model, existing_bugs=existing)
    if not result.success:
        print(f"audit: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    bugs_path = project_dir / "BUGS.md"
    if bugs_path.exists():
        print(bugs_path.read_text())
    else:
        print("audit: BUGS.md was not written", file=sys.stderr)


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
    remote_result = _git(
        ["git", "remote"],
        cwd=project_dir,
        label="auto-wrap remote check",
    )
    if remote_result.stdout.strip():
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
    remote_result = _git(
        ["git", "remote"],
        cwd=project_dir,
        label="reinject remote check",
    )
    if remote_result.stdout.strip():
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
