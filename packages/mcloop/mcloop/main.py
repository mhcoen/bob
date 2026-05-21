"""Entry point for the main loop."""

from __future__ import annotations

import argparse
import dataclasses
import re
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

import mcloop.lifecycle as _lifecycle
from mcloop import formatting
from mcloop._planfile_precondition import (
    PlanNotCanonicalError,
    enforce_canonical,
)
from mcloop.audit import _run_audit_fix_cycle
from mcloop.checklist import (
    PlanCorruptionError,
    Task,
    check_off,
    clear_failed_markers,
    count_unchecked,
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
    user_task_instructions,
)
from mcloop.checklist import (
    task_label as _task_label,
)
from mcloop.checks import (
    detect_build,
    detect_run,
    get_check_commands,
    run_autofix,
    run_checks,
    try_salvage_style_failures,
)
from mcloop.claude_md_sync import handle_sync, reconcile_pending
from mcloop.config import format_reviewer_status, load_reviewer_config
from mcloop.conftest_guard import ensure_conftest_guard
from mcloop.dep_validator import (
    MissingDependenciesError,
    validate_project_dependencies,
)
from mcloop.errors import (
    _check_errors_json,
    _insert_bugs_section,
)
from mcloop.formatting import format_elapsed as _format_elapsed
from mcloop.formatting import format_task_id
from mcloop.git_ops import (
    _changed_files,
    _checkpoint,
    _commit,
    _ensure_git,
    _get_git_hash,
    _git,
    _has_meaningful_changes,
    _has_uncommitted_changes,
    _push_or_die,
    _snapshot_worktree,
    _stage_safe,
    _worktree_status,
)
from mcloop.idea_cmd import _cmd_idea
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
    _kill_active_process,  # noqa: F401 — re-exported for tests
    _kill_orphan_sessions,
    _save_interrupt_state,  # noqa: F401 — re-exported for tests
    _write_eliminated_json,  # noqa: F401 — re-exported for tests
    _write_ruledout_to_plan,  # noqa: F401 — re-exported for tests
    register_atexit_cleanup,
    register_signal_handlers,
    shutdown_lifecycle,
)
from mcloop.maintain import run_maintain
from mcloop.notify import notify
from mcloop.output import (
    _dry_run,
    _print_error_tail,
    _print_summary,
    _snapshot_notes,
    _tail,
)
from mcloop.plan_split import (
    BUGS_FILE,
    CURRENT_PLAN,
    ensure_bugs_file,
    ensure_current_plan,
    get_current_phase_name,
    transition_phase,
)
from mcloop.pytest_optimizations import ensure_pytest_optimizations
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
    _purge_all_reviews,
    _spawn_reviewer,
    _terminate_reviewers,
)
from mcloop.run_summary import (
    CheckEntry,
    RunSummary,
    TaskEntry,
    _iso_now,
    write_run_summary,
)
from mcloop.runner import (
    DEFAULT_TASK_TIMEOUT,
    INVESTIGATION_TOOLS,
    RunResult,
    SubscriptionPreflightError,
    run_audit,
    run_task,
    warn_unknown_model,
)
from mcloop.session_context import SessionContext
from mcloop.sync_cmd import _cmd_sync


@dataclasses.dataclass(frozen=True)
class BuildResult:
    """Structured result from _run_build()."""

    ran: bool  # True if a build command was found and executed
    passed: bool  # True if build succeeded or no build command exists
    command: str = ""
    output: str = ""


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


# Phrases the synthesizer commonly uses to author tasks that are
# read-only by design (capture a baseline, verify a property, record
# state without changing files). Such tasks legitimately produce no
# file changes and shouldn't be penalized when post-task checks fail
# for reasons orthogonal to the task itself (e.g., a Phase 0 task
# running before any tests exist). Match is case-insensitive substring;
# false positives are mostly benign because the post-task check has
# already failed, so the alternative path was a terminal failure
# anyway.
_READONLY_TASK_PHRASES: tuple[str, ...] = (
    "do not modify",
    "do not change",
    "do not edit",
    "without modifying",
    "without changing",
    "without editing",
    "read-only",
    "read only",
    "capture baseline",
    "capture pre-edit",
    "record exit code",
    "record stdout",
    "verify and record",
    "no file changes",
    "without making any changes",
)


def _enforce_canonical_inputs(
    master_path: Path,
    current_plan_path: Path,
    bugs_path: Path,
) -> None:
    """B3 increment-3 wire-in: canonical-plan precondition gate.

    Runs at ``run_loop`` entry, before any of the three pre-loop mutation
    sites enumerated in ``.scratch/B3_INCREMENT2_PATH_ENUM.md``:

      * the ``--retry`` ``clear_failed_markers`` calls (main.py ~700)
      * the ``_check_interrupted`` skip / describe mutation
        (lifecycle.py ~178, 217-261)
      * the ``ensure_current_plan`` split-plan write
        (plan_split.py ~204)

    Validates each plan-bearing file that exists on disk at the moment
    of the gate. ``current_plan_path`` is checked only if it already
    exists; the master is the source of truth for split-plan
    extraction, so a missing CURRENT_PLAN.md is not a precondition
    failure here — it is handled normally by
    ``ensure_current_plan`` later in the loop. ``bugs_path`` is checked
    only if it exists (``ensure_bugs_file`` creates it later with a
    canonical ``## Bugs`` header on first run).

    Raises ``PlanNotCanonicalError`` on the first non-canonical input,
    which the top-level ``main()`` handler catches and translates to
    exit code 3 (distinct from 1 = terminal failure, 2 = parser
    corruption, 5 = Plan-Ledger pause).
    """
    from bob_tools.planfile import parse_plan as _bt_parse_plan

    for path in (master_path, bugs_path, current_plan_path):
        if not path.exists():
            continue
        text = path.read_text()
        plan = _bt_parse_plan(text, source_path=path)
        enforce_canonical(text, plan, source_path=path)


def _is_readonly_task(task_text: str) -> bool:
    """Return True iff the task description signals it's a deliberate
    no-op. Used by the no-op-with-failing-checks branch in the task
    loop to distinguish "deliberate read-only task" (accept) from
    "editor was supposed to make changes but didn't" (terminal
    failure)."""
    text = task_text.lower()
    return any(phrase in text for phrase in _READONLY_TASK_PHRASES)


_PASS_EVIDENCE_TERMS = (
    "already formatted",
    "all checks pass",
    "all four checks pass",
    "clean",
    "green",
    "no failures",
    "no issues",
    "pass",
    "passed",
)

_CHECK_EVIDENCE_TERMS = (
    "cargo test",
    "go test",
    "mypy",
    "npm test",
    "pnpm test",
    "pyright",
    "pytest",
    "ruff",
    "swift test",
    "xcodebuild",
)

_VERIFICATION_TASK_TERMS = (
    "all green",
    "gate",
    "verify",
    "verification",
)


def _stage_anchor(task_text: str) -> str:
    match = re.search(r"\bstage\s+(\d+)\b", task_text, flags=re.IGNORECASE)
    if not match:
        return ""
    return f"stage {match.group(1)}"


def _has_task_specific_acceptance_evidence(
    task_text: str,
    output: str,
    *,
    task_id: str = "",
) -> bool:
    """Return True when a no-change session proves the requested task."""
    normalized_output = output.lower()
    if not normalized_output.strip():
        return False

    anchors = [anchor for anchor in (task_id.lower(), _stage_anchor(task_text)) if anchor]
    if not anchors or not any(anchor in normalized_output for anchor in anchors):
        return False

    check_terms_seen = {
        term for term in _CHECK_EVIDENCE_TERMS if term in normalized_output
    }
    has_check_evidence = len(check_terms_seen) >= 2
    has_pass_evidence = any(term in normalized_output for term in _PASS_EVIDENCE_TERMS)
    return has_check_evidence and has_pass_evidence


def _has_checked_acceptance_task(tasks: list[Task]) -> bool:
    """Return True if a checked [AUTO]/[USER] task can evidence acceptance."""
    for task in tasks:
        if task.checked and (is_auto_task(task) or is_user_task(task)):
            return True
        if _has_checked_acceptance_task(task.children):
            return True
    return False


def _auto_response_failed(response: str) -> bool:
    """Return True when an [AUTO] observation reports an execution failure."""
    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line.startswith("ERROR:"):
            return True
        if line.startswith("STATUS:"):
            status = line.removeprefix("STATUS:").strip().upper()
            if status.startswith("CRASHED") or status.startswith("HUNG"):
                return True
        if line.startswith("exit_code:"):
            code = line.removeprefix("exit_code:").strip()
            if code not in {"0"}:
                return True
    return False


def main() -> None:
    import atexit
    import traceback

    import mcloop.runner as _runner
    from mcloop.ledger_pause import HardStop

    register_atexit_cleanup()
    atexit.register(_terminate_reviewers)

    register_signal_handlers(_runner, cleanup_callback=_terminate_reviewers)
    try:
        try:
            _main()
        except HardStop as exc:
            # Plan Ledger Slice D: a threshold crossing that warrants
            # auto-reauthor either could not run (reauthor_unavailable),
            # produced an invalid plan (lineage_invalid), or otherwise
            # failed (reauthor_failed); OR auto_reauthor is disabled and
            # a manual pause is required (manual_pause). Exit code 5 is
            # reserved for this class of pause per the Slice D design.
            print(
                f"\nmcloop: Plan Ledger paused the run (reason={exc.reason})",
                file=sys.stderr,
            )
            print(f"  detail: {exc.detail}", file=sys.stderr)
            sys.exit(5)
        except PlanCorruptionError as exc:
            # The user shouldn't see a Python traceback for an expected
            # condition like a malformed PLAN.md. Print the error message
            # cleanly; write the full traceback to a log file for debugging.
            print(f"\nmcloop: {exc}\n", file=sys.stderr)
            try:
                log_dir = Path.cwd() / ".mcloop"
                log_dir.mkdir(exist_ok=True)
                log_path = log_dir / "last_error.log"
                log_path.write_text(
                    f"PlanCorruptionError\n\n{exc}\n\nTraceback:\n"
                    + "".join(traceback.format_exception(exc))
                )
                print(f"Full traceback logged to {log_path}", file=sys.stderr)
            except OSError:
                pass  # Logging is best-effort; never let it mask the real error.
            sys.exit(2)
        except PlanNotCanonicalError as exc:
            # B3 increment 3: canonical-plan precondition rejected the
            # input. Exit code 3 is reserved for this class so callers
            # (CI, scripts) can distinguish "PLAN.md not migrated yet"
            # from a generic run failure (1), a parser corruption (2),
            # or a Plan-Ledger pause (5).
            print(f"\n!!! {exc}\n", file=sys.stderr)
            sys.exit(3)
        except SubscriptionPreflightError as exc:
            print(f"\n!!! {exc}\n", file=sys.stderr)
            sys.exit(exc.exit_code)
    finally:
        try:
            shutdown_lifecycle()
        finally:
            _terminate_reviewers()
            atexit.unregister(_terminate_reviewers)


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

    if args.command == "idea":
        _cmd_idea(checklist_path.parent, args.text)
        return

    if args.command == "ack-orchestra-override":
        _cmd_ack_orchestra_override(checklist_path.parent)
        return

    if args.command == "maintain":
        _cmd_maintain(
            checklist_path.parent,
            cli=args.cli,
            model=args.model,
            stop_after_one=args.stop_after_one,
        )
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
        stop_after_stage=args.stop_after_stage,
        stop_after_one=args.stop_after_one,
        task_timeout=args.timeout,
        retry=args.retry,
        no_plan_ledger=getattr(args, "no_plan_ledger", False),
        no_auto_reauthor=getattr(args, "no_auto_reauthor", False),
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
    commit_hashes: list[str] | None = None,
    prior_errors: str = "",
    task_timeout: int | None = None,
) -> tuple[str, str]:
    """Run multiple subtasks in a single session.

    Combines the text of all batch_children into a single prompt
    with numbered steps. On success (checks pass), checks off all
    children. On failure, returns ("failed", error_tail) so the
    caller can retry the batch with the error as prior_errors.

    Returns ("success", "") or ("failed", error_tail).
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
        steps.append(f"{i}. {format_task_id(child)}{child.text}")
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
    # A batch covers multiple children, no single canonical task id;
    # leave the interrupt-state task_id empty for batches. The label_range
    # ("first-last") identifies the batch in the interrupt prompt.
    _lifecycle._current_task_id = ""
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
        prior_errors=prior_errors,
        session_context=ctx.text(),
        check_commands=project_checks,
        allowed_tools=allowed_tools,
        eliminated=eliminated,
        timeout=task_timeout or DEFAULT_TASK_TIMEOUT,
    )

    if not result.success:
        elapsed = _format_elapsed(time.monotonic() - task_start)
        print(
            formatting.error_msg(f"Batch session failed ({elapsed})"),
            flush=True,
        )
        return "failed", _tail(result.output, 50)

    if not _has_meaningful_changes(project_dir):
        # A batch is implementation work. A globally green suite is not
        # acceptance evidence that the batch's required work or tests
        # came into existence.
        print(
            formatting.error_msg("Batch produced no changes and no acceptance evidence"),
            flush=True,
        )
        return "failed", "Batch produced no file changes and no acceptance evidence"

    _lifecycle._current_phase = "checks"
    run_autofix(project_dir)
    changed_files = _changed_files(project_dir)
    if not changed_files and _has_uncommitted_changes(project_dir):
        print(
            formatting.error_msg("Batch: autofix modified metadata-only files"),
            flush=True,
        )
        return "failed", "Autofix modified metadata-only files"
    pre_check_status = _worktree_status(project_dir)
    check_result = run_checks(
        project_dir,
        changed_files=changed_files,
    )
    if not check_result.passed:
        # Try salvage: if ruff's only complaints are minor style codes
        # (E501 line too long, etc.), suppress those specific lines
        # via a per-line suppression comment and re-run once. This
        # prevents a 14-minute batch from being thrown away for one
        # unbreakable long string.
        salvaged, patched = try_salvage_style_failures(
            project_dir,
            check_result.output,
        )
        if salvaged:
            print(
                formatting.system_msg("Salvaged minor style failures in: " + ", ".join(patched)),
                flush=True,
            )
            # Add patched files to the pre-batch snapshot so the
            # rollback path below does NOT revert them. Salvage
            # patches are deliberate fixes that must survive retries.
            pre_batch_modified = list(pre_batch_modified) + list(patched)
            # Refresh changed_files since we just edited more files.
            changed_files = _changed_files(project_dir)
            pre_check_status = _worktree_status(project_dir)
            check_result = run_checks(
                project_dir,
                changed_files=changed_files,
            )
    if check_result.passed:
        post = set(_worktree_status(project_dir).splitlines())
        pre = set(pre_check_status.splitlines()) if pre_check_status else set()
        if post != pre:
            print(
                formatting.error_msg("Batch: checker introduced uncommitted changes"),
                flush=True,
            )
            return "failed", "Checker introduced uncommitted changes"
        try:
            batch_hash = _commit(
                project_dir,
                f"[BATCH] {label_range}: {n} subtasks",
            )
        except RuntimeError as exc:
            print(
                formatting.error_msg(str(exc)),
                flush=True,
            )
            return "failed", f"Commit failed: {exc}"
        if batch_hash and commit_hashes is not None:
            commit_hashes.append(batch_hash)
        handle_sync(project_dir, batch_hash or "", task_label=first_label)
        if reviewer_config:
            _spawn_reviewer(project_dir)
        _maybe_auto_wrap(project_dir)
        _reinject_wrappers(project_dir)
        for child in batch_children:
            check_off(checklist_path, child)
            lbl = _task_label(tasks, child)
            completed.append(f"{lbl}) {format_task_id(child)}{child.text}")
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
        return "success", ""

    print(
        formatting.error_msg(f"Batch checks failed: {check_result.command}"),
        flush=True,
    )
    _check_tail = _tail(check_result.output, 50)
    _batch_error = f"Command: {check_result.command}\n{_check_tail}"
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
    return "failed", _batch_error


def _build_and_write_summary(
    project_dir: Path,
    run_start_iso: str,
    elapsed_seconds: float,
    mode: str,
    task_entries: list[TaskEntry],
    check_entries: list[CheckEntry],
    commit_hashes: list[str],
    terminal_status: str,
    failure_detail: str = "",
    stop_reason: str = "",
    stuck: list[str] | None = None,
    full_suite_passed: bool | None = None,
    build_passed: bool | None = None,
    audit_result: str | None = None,
) -> Path | None:
    """Build a RunSummary and write it. Returns the file path, or None on error."""
    summary = RunSummary(
        run_start=run_start_iso,
        run_end=_iso_now(),
        elapsed_seconds=round(elapsed_seconds, 2),
        mode=mode,
        tasks=list(task_entries),
        checks=list(check_entries),
        full_suite_passed=full_suite_passed,
        build_passed=build_passed,
        audit_result=audit_result,
        terminal_status=terminal_status,
        failure_detail=failure_detail,
        stop_reason=stop_reason,
        stuck=stuck or [],
        commit_hashes=list(commit_hashes),
    )
    try:
        return write_run_summary(project_dir, summary)
    except Exception:
        return None


def run_loop(
    checklist_path: Path,
    max_retries: int = 3,
    cli: str = "claude",
    model: str | None = None,
    fallback_model: str | None = None,
    no_audit: bool = False,
    allowed_tools: str | None = None,
    enable_reviewer: bool = False,
    stop_after_stage: bool = False,
    stop_after_one: bool = False,
    task_timeout: int | None = None,
    retry: bool = False,
    no_plan_ledger: bool = False,
    no_auto_reauthor: bool = False,
) -> RunStatus:
    """Run the main loop. Returns a RunStatus indicating outcome."""
    import mcloop.runner as _runner

    register_signal_handlers(_runner, cleanup_callback=_terminate_reviewers)

    run_start_iso = _iso_now()
    run_start_mono = time.monotonic()

    project_dir = checklist_path.parent
    _lifecycle._project_dir = project_dir
    log_dir = project_dir / "logs"

    # Split-plan paths
    master_path = checklist_path  # PLAN.md (the full roadmap)
    current_plan_path = project_dir / CURRENT_PLAN
    bugs_path = project_dir / BUGS_FILE

    # B3 increment 3: canonical-plan precondition gate. Runs before
    # parse_description, --retry's clear_failed_markers, the interrupt
    # check, and ensure_current_plan — i.e. before every pre-loop
    # mutation site enumerated in
    # .scratch/B3_INCREMENT2_PATH_ENUM.md. Raises PlanNotCanonicalError
    # on non-canonical input; main()'s handler translates that to
    # exit 3.
    _enforce_canonical_inputs(master_path, current_plan_path, bugs_path)

    description = parse_description(master_path)

    # --retry: flip every [!] back to [ ] in the active files so
    # previously-failed tasks are eligible again this run. Runs before
    # ensure_current_plan so a freshly extracted CURRENT_PLAN.md is
    # already clean, and before the interrupt check so skipped tasks
    # from a prior Ctrl-C also get reset.
    if retry:
        cleared_current = clear_failed_markers(current_plan_path)
        cleared_bugs = clear_failed_markers(bugs_path)
        cleared_total = cleared_current + cleared_bugs
        if cleared_total:
            print(
                formatting.system_msg(
                    f"--retry: reset {cleared_total} failed task marker"
                    f"{'s' if cleared_total != 1 else ''}"
                ),
                flush=True,
            )

    # Check for interrupted state from a previous Ctrl-C. Pass the
    # split-plan files in priority order so skip/describe actions mark
    # the task in the file the loop actually reads from, not just the
    # master PLAN.md.
    _interrupt_active_paths = [
        p for p in (bugs_path, current_plan_path, master_path) if p.exists()
    ]
    interrupt_action = _check_interrupted(
        project_dir,
        checklist_path,
        active_paths=_interrupt_active_paths,
    )
    if interrupt_action == "quit":
        _build_and_write_summary(
            project_dir,
            run_start_iso,
            elapsed_seconds=time.monotonic() - run_start_mono,
            mode="plan",
            task_entries=[],
            check_entries=[],
            commit_hashes=[],
            terminal_status="interrupted",
            failure_detail="User quit at interrupt prompt",
        )
        return RunStatus("interrupted", detail="User quit at interrupt prompt")

    reconcile_pending(project_dir)

    # Codex fallover disabled until remote approval is sorted out
    rate_state = RateLimitState()

    project_checks = get_check_commands(project_dir)

    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    _checkpoint(project_dir, verbose=True)

    # Ensure the target project has a conftest.py guard that blocks
    # real claude/codex subprocess calls during pytest. Idempotent.
    if ensure_conftest_guard(project_dir):
        _stage_safe(project_dir)
        _checkpoint(project_dir)

    # Ensure pyproject.toml has pytest-xdist parallelism + a timeout so
    # the suite runs in seconds instead of minutes. Idempotent.
    if ensure_pytest_optimizations(project_dir):
        _stage_safe(project_dir)
        _checkpoint(project_dir)

    _push_or_die(project_dir)

    # Split-plan: ensure BUGS.md exists before anything tries to write to it
    ensure_bugs_file(bugs_path)

    # Check for crash errors from previous runs
    if not _check_errors_json(project_dir, model=model):
        _build_and_write_summary(
            project_dir,
            run_start_iso,
            elapsed_seconds=time.monotonic() - run_start_mono,
            mode="plan",
            task_entries=[],
            check_entries=[],
            commit_hashes=[],
            terminal_status="failure",
            failure_detail="Unresolved errors from previous run",
        )
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

    # Clean up review files from previous runs
    if reviewer_config:
        _cleanup_stale_reviews(project_dir)
    else:
        _purge_all_reviews(project_dir)

    # Clean up stale pending files from previous runs
    pending_dir = project_dir / ".mcloop" / "pending"
    if pending_dir.exists():
        for f in pending_dir.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)

    # ---- Plan Ledger Slice D: settings + storage + startup eval ----
    # Imported lazily so projects without bob_tools/duplo on the path
    # do not pay the import cost; the loader returns disabled
    # settings when ledger_dir is absent, so the runtime cost is
    # zero on Plan-Ledger-disabled projects.
    from mcloop.ledger_config import load_plan_ledger_settings
    from mcloop.ledger_emit import (
        TaskOutcome,
        emit_task_lifecycle_events,
        open_mcloop_storage,
        record_phase_id_fallback,
        resolve_phase_id,
    )
    from mcloop.ledger_pause import HardStop, auto_reauthor, evaluate_and_maybe_pause

    _pl_settings = load_plan_ledger_settings(
        project_dir=project_dir,
        plan_path=master_path,
        cli_no_plan_ledger=no_plan_ledger,
        cli_no_auto_reauthor=no_auto_reauthor,
    )
    _pl_storage = None
    _pl_run_id: str | None = None
    if _pl_settings.enabled:
        print(
            formatting.system_msg(
                "Plan Ledger: enabled "
                f"(auto_reauthor={'on' if _pl_settings.auto_reauthor else 'off'},"
                f" ledger_dir={_pl_settings.ledger_dir})"
            ),
            flush=True,
        )
        _pl_storage = open_mcloop_storage(_pl_settings.ledger_dir)
        _pl_run_id = f"mcloop-{int(time.time())}"
        # One-shot startup eval: catches crossings introduced by
        # actors other than McLoop (Duplo writes, manual edits)
        # since the last McLoop run.
        startup_pause = evaluate_and_maybe_pause(
            storage=_pl_storage,
            run_id=_pl_run_id,
        )
        if startup_pause is not None:
            if _pl_settings.auto_reauthor:
                auto_reauthor(
                    decision=startup_pause,
                    plan_path=_pl_settings.plan_path,
                    ledger_dir=_pl_settings.ledger_dir,
                    project_dir=project_dir,
                )
            else:
                raise HardStop(
                    reason="manual_pause",
                    detail=(
                        "startup ledger evaluation surfaced crossing "
                        f"{startup_pause.crossing_event_id} "
                        f"(rule={startup_pause.rule_id}); auto_reauthor "
                        "is disabled for this run"
                    ),
                )

    # ---- Pre-flight: project dependencies declared but not installed ----
    # ensure_pytest_optimizations() above mutates pyproject.toml to add
    # pytest-xdist + pytest-timeout; declaring them does not install
    # them. If the project venv was provisioned earlier or by a run.sh
    # that fell back to a non-dev install, the first pytest invocation
    # later in the loop fails with "unrecognized arguments: -n" and
    # burns retries that cannot succeed (the venv contents do not
    # change between retries). Catch the mismatch here and fail fast.
    try:
        validate_project_dependencies(project_dir)
    except MissingDependenciesError as exc:
        print(formatting.system_msg(f"Pre-flight: {exc}"), flush=True)
        _build_and_write_summary(
            project_dir,
            run_start_iso,
            elapsed_seconds=time.monotonic() - run_start_mono,
            mode="plan",
            task_entries=[],
            check_entries=[],
            commit_hashes=[],
            terminal_status="failure",
            failure_detail=f"Missing project dependencies: {exc}",
        )
        return RunStatus("failure", detail=f"Missing project dependencies: {exc}")

    def _ledger_settle(task_label: str, outcome: TaskOutcome) -> None:
        """Plan Ledger Slice D per-task settle hook.

        Emits lifecycle events, evaluates thresholds, and either
        auto-reauthors (refresh happens automatically at the next
        outer-loop iteration that re-parses PLAN.md) or raises
        HardStop with reason="manual_pause" when auto_reauthor is
        disabled.

        No-op on projects without Plan Ledger enabled.
        """
        if not _pl_settings.enabled or _pl_storage is None or _pl_run_id is None:
            return
        resolution = resolve_phase_id(
            plan_path=_pl_settings.plan_path,
            task_label=task_label,
        )
        if resolution.source == "ordinal":
            record_phase_id_fallback(
                storage=_pl_storage,
                task_label=task_label,
                resolution=resolution,
                run_id=_pl_run_id,
            )
        emit_task_lifecycle_events(
            storage=_pl_storage,
            task_label=task_label,
            phase_id=resolution.phase_id,
            outcome=outcome,
            project_dir=project_dir,
            run_id=_pl_run_id,
        )
        decision = evaluate_and_maybe_pause(
            storage=_pl_storage,
            run_id=_pl_run_id,
        )
        if decision is None:
            return
        if _pl_settings.auto_reauthor:
            auto_reauthor(
                decision=decision,
                plan_path=_pl_settings.plan_path,
                ledger_dir=_pl_settings.ledger_dir,
                project_dir=project_dir,
            )
            # Plan refreshed on disk by Slice C's reauthor. The
            # outer `while True:` re-parses bug_tasks and plan_tasks
            # at the top of every iteration, so the refreshed task
            # mapping is picked up automatically (Q3 mandatory
            # refresh contract).
            return
        raise HardStop(
            reason="manual_pause",
            detail=(
                f"crossing {decision.crossing_event_id} "
                f"(rule={decision.rule_id}, "
                f"action={decision.recommended_action}); "
                "auto_reauthor disabled for this run -- re-author "
                "manually with `duplo reauthor` or re-invoke without "
                "--no-auto-reauthor"
            ),
        )

    notes_snapshot = _snapshot_notes(project_dir)
    ctx = SessionContext()
    run_start = run_start_mono
    completed: list[str] = []
    task_entries: list[TaskEntry] = []
    check_entries: list[CheckEntry] = []
    commit_hashes: list[str] = []
    failed_task: str | None = None
    failed_reason: str = ""
    terminal_failure: str | None = None  # Set by any fatal failure; gates success path
    stopped_early: str = ""  # "stage" or "one" when a stop flag caused the exit
    completed_stage: str = ""  # Set by phase transition when a phase completes
    batch_exhausted: set[str] = set()
    acceptance_evidence_phases: set[str] = set()
    current_model = model or _load_mcloop_config().get("model")
    primary_model = current_model

    if current_model:
        warn_unknown_model(cli, current_model)

    # Split-plan: extract current phase from master if needed.
    # Bug check first: even if all phases are complete, unchecked
    # bugs must still be worked before the run is allowed to exit.
    _phases_exhausted = False
    if not ensure_current_plan(master_path, current_plan_path):
        _phases_exhausted = True

    # Parse bugs now so the bug-only check can gate the early exit.
    _early_bug_tasks = parse(bugs_path) if bugs_path.exists() else []
    _early_has_bugs = has_unchecked_bugs(_early_bug_tasks)

    if _phases_exhausted and not _early_has_bugs:
        # All phases already complete and no bugs to work.
        total = time.monotonic() - run_start_mono
        _build_and_write_summary(
            project_dir,
            run_start_iso,
            elapsed_seconds=total,
            mode="plan",
            task_entries=[],
            check_entries=[],
            commit_hashes=[],
            terminal_status="success",
        )
        notify("All phases already complete")
        return RunStatus("success", detail="All phases already complete")

    # Bug-only mode: when BUGS.md has unchecked items, work only those
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

    initial_bug_tasks = parse(bugs_path)
    initial_plan_tasks = parse(current_plan_path) if current_plan_path.exists() else []
    bug_only = has_unchecked_bugs(initial_bug_tasks)
    _run_mode = "bug-only" if bug_only else "plan"
    if bug_only:
        print(
            formatting.system_msg("Bug-only mode: fixing bugs before continuing"),
            flush=True,
        )
        if stop_after_stage:
            print(
                formatting.system_msg(
                    "Warning: --stop-after-stage ignored in bug-only mode (no stages)"
                ),
                flush=True,
            )
            stop_after_stage = False

    active_phase_name = (
        get_current_phase_name(current_plan_path) if current_plan_path.exists() else ""
    )

    # Count remaining tasks for the startup notification
    remaining_count = count_unchecked(initial_bug_tasks) + count_unchecked(initial_plan_tasks)
    start_msg = f"Starting: {remaining_count} task(s) remaining"
    if active_phase_name:
        start_msg += f" in {active_phase_name}"
    if bug_only:
        start_msg = f"Starting: fixing bugs ({remaining_count} remaining)"
    notify(start_msg)
    print(
        formatting.system_msg(
            "Do not edit CURRENT_PLAN.md or BUGS.md while mcloop is running."
            " Kill mcloop first, make edits, then restart."
            " PLAN.md (the master) is safe to edit during a run."
        ),
        flush=True,
    )

    while True:
        # Check for completed reviews from background reviewer processes
        if reviewer_config:
            _collect_review_findings(project_dir, bugs_path, ctx)

        # Parse both split-plan files
        bug_tasks = parse(bugs_path)
        plan_tasks = parse(current_plan_path) if current_plan_path.exists() else []

        # If any task has failed, stop the entire run.
        # A failed task is fatal: do not attempt other tasks.
        failed = _has_any_failed(bug_tasks) or _has_any_failed(plan_tasks)
        if failed:
            notify(
                f"Fatal: previously failed task: {format_task_id(failed)}{failed.text}",
                level="error",
            )
            total = time.monotonic() - run_start
            _print_summary(
                completed,
                f"{format_task_id(failed)}{failed.text}",
                "Task failed in a prior attempt",
                bug_tasks + plan_tasks,
                total,
                project_dir,
                notes_snapshot,
            )
            _detail = f"Previously failed task: {failed.text}"
            _build_and_write_summary(
                project_dir,
                run_start_iso,
                elapsed_seconds=total,
                mode=_run_mode,
                task_entries=task_entries,
                check_entries=check_entries,
                commit_hashes=commit_hashes,
                terminal_status="failure",
                failure_detail=_detail,
            )
            return RunStatus("failure", detail=_detail)

        # Find next task: bugs have priority over plan tasks
        task = find_next(bug_tasks)
        if task is not None:
            tasks = bug_tasks
            active_file = bugs_path
        else:
            task = find_next(plan_tasks)
            if task is not None:
                tasks = plan_tasks
                active_file = current_plan_path

        # Phase transition: current plan fully checked, try next phase
        if task is None and not bug_only:
            if (
                active_phase_name
                and active_phase_name not in acceptance_evidence_phases
                and not _has_checked_acceptance_task(plan_tasks)
            ):
                terminal_failure = (
                    f"Stage completion lacks executed acceptance evidence: {active_phase_name}"
                )
                print(
                    formatting.error_msg(terminal_failure),
                    flush=True,
                )
                notify(terminal_failure, level="error")
                break
            # Run full test suite at phase boundary
            print(
                formatting.system_msg("Running full test suite (phase boundary)..."),
                flush=True,
            )
            _full_suite_start = time.monotonic()
            full_check = run_checks(project_dir)
            _full_suite_elapsed = _format_elapsed(time.monotonic() - _full_suite_start)
            check_entries.append(
                CheckEntry(
                    command=full_check.command or "full-suite",
                    passed=full_check.passed,
                    elapsed=round(time.monotonic() - _full_suite_start, 2),
                )
            )
            if not full_check.passed:
                print(
                    formatting.error_msg(
                        f"Full suite failed at phase boundary: "
                        f"{full_check.command} [{_full_suite_elapsed}]"
                    ),
                    flush=True,
                )
                _print_error_tail(full_check.output)
                notify(
                    "Run ended with red repo: full suite failed"
                    f" at phase boundary ({full_check.command})",
                    level="error",
                )
                terminal_failure = f"Full suite failed at phase boundary: {full_check.command}"
                break
            print(
                formatting.system_msg(f"Full test suite passed [{_full_suite_elapsed}]"),
                flush=True,
            )
            build_result = _run_build(project_dir)
            if not build_result.passed:
                notify(
                    f"Build failed at phase boundary ({build_result.command})",
                    level="error",
                )
                terminal_failure = f"Build failed at phase boundary: {build_result.command}"
                break

            # Transition to next phase. Default behavior: advance and
            # continue the outer loop, which re-parses CURRENT_PLAN.md
            # at the top of the next iteration and picks up the
            # refreshed phase tasks. ``--stop-after-stage`` overrides
            # to break at the boundary; ``next_phase is None`` means
            # all phases are complete and the post-loop audit/summary
            # path runs.
            completed_phase = active_phase_name
            next_phase = transition_phase(master_path, current_plan_path)
            completed_stage = completed_phase
            if next_phase is None:
                break
            if stop_after_stage:
                stopped_early = "stage"
                active_phase_name = next_phase
                break
            active_phase_name = next_phase
            print(
                formatting.system_msg(f"Advancing to {next_phase}"),
                flush=True,
            )
            notify(f"Starting {next_phase}")
            continue

        if task is None:
            break

        # In bug-only mode, stop when no more bug tasks remain
        if bug_only and task.stage != "Bugs":
            break

        # If this is a parent with all children done, just check it off
        if task.children and all(c.checked for c in task.children):
            check_off(active_file, task)
            continue

        label = _task_label(tasks, task)

        # Handle [AUTO] tasks: automated observation
        if is_auto_task(task):
            has_subtasks = find_parent(tasks, task) is not None
            ctx.update_group(label, has_subtasks)
            action, args = parse_auto_task(task)
            response = _handle_auto_task(label, action, args)
            if _auto_response_failed(response):
                failed_task = f"{label}) {format_task_id(task)}{task.text}"
                failed_reason = response
                terminal_failure = f"AUTO task failed: {format_task_id(task)}{task.text}"
                print(
                    formatting.error_msg(terminal_failure),
                    flush=True,
                )
                ctx.add(label, task.text, "0s", response)
                notify(f"[AUTO:{action}] failed: {args[:60]}", level="error")
                break
            check_off(active_file, task)
            if active_file == current_plan_path and active_phase_name:
                acceptance_evidence_phases.add(active_phase_name)
            completed.append(f"{label}) {format_task_id(task)}{task.text}")
            ctx.add(label, task.text, "0s", response)
            notify(f"[AUTO:{action}] {args[:60]}")
            continue

        # Handle [USER] tasks: pause for human observation
        if is_user_task(task):
            _lifecycle._current_phase = "user_prompt"
            _lifecycle._current_task_label = label
            _lifecycle._current_task_text = task.text
            _lifecycle._current_task_id = task.task_id or ""
            _lifecycle._phase_start_time = time.monotonic()
            has_subtasks = find_parent(tasks, task) is not None
            ctx.update_group(label, has_subtasks)
            instructions = user_task_instructions(task)
            response = _handle_user_task(label, instructions)
            # Ask user whether the task passed
            if response:
                try:
                    verdict = input("Did this task pass? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    verdict = ""
                passed = verdict in ("y", "yes")
            else:
                passed = True  # No observation = user skipped
            if passed:
                check_off(active_file, task)
                if active_file == current_plan_path and active_phase_name:
                    acceptance_evidence_phases.add(active_phase_name)
                completed.append(f"{label}) {format_task_id(task)}{task.text}")
                ctx.add(label, task.text, "0s", response)
                notify(f"[USER] {instructions[:80]}")
            else:
                # User reported failure: file a bug from the observation
                # and leave the USER task unchecked for re-verification
                # after the bug is fixed.
                flat_obs = response.replace("\n", " | ")
                short_obs = flat_obs[:200]
                if len(flat_obs) > 200:
                    short_obs += "..."
                bug_desc = f"Fix issue reported during task {label}: {short_obs}"
                _insert_bugs_section(bugs_path, [f"- [ ] {bug_desc}"])
                print(
                    formatting.system_msg("Bug filed from user observation -> BUGS.md"),
                    flush=True,
                )
                failed_task = f"{label}) {format_task_id(task)}{task.text}"
                failed_reason = response
                terminal_failure = f"User verification failed: {format_task_id(task)}{task.text}"
                notify(
                    f"[USER] FAILED: {format_task_id(task)}{task.text[:60]}",
                    level="error",
                )
                break
            continue

        # Check for [BATCH] on the parent task.
        # If the parent is marked [BATCH], collect all batchable
        # siblings and combine them into a single session.
        # Skipped when config has "batch": false or --stop-after-one.
        parent = find_parent(tasks, task)
        batch_enabled = _load_mcloop_config().get("batch", True)
        parent_label = _task_label(tasks, parent) if parent else ""
        if (
            batch_enabled
            and not stop_after_one
            and parent is not None
            and is_batch_task(parent)
            and parent_label not in batch_exhausted
        ):
            batch_children = get_batch_children(parent)
            if len(batch_children) > 1:
                batch_handled = "failed"
                batch_prior_errors = ""
                for batch_attempt in range(1, max_retries + 1):
                    if batch_attempt > 1:
                        print(
                            formatting.system_msg(
                                f"Retrying batch with prior errors"
                                f" (attempt {batch_attempt}/{max_retries})"
                            ),
                            flush=True,
                        )
                    batch_handled, batch_prior_errors = _run_batch(
                        batch_children,
                        tasks,
                        active_file,
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
                        commit_hashes=commit_hashes,
                        prior_errors=batch_prior_errors,
                        task_timeout=task_timeout,
                    )
                    if batch_handled == "success":
                        break
                if batch_handled == "success":
                    if active_file == current_plan_path and active_phase_name:
                        acceptance_evidence_phases.add(active_phase_name)
                    continue
                # Batch exhausted its retries. Mark the parent and every
                # child as failed, record the failure, and stop the run.
                # Falling through to per-subtask execution is wrong: a
                # batch body is written as a coherent unit where some
                # subtasks may be context-only and cannot stand alone.
                print(
                    formatting.error_msg(f"Batch failed after {max_retries} attempts"),
                    flush=True,
                )
                batch_exhausted.add(parent_label)
                mark_failed(active_file, parent)
                for child in batch_children:
                    mark_failed(active_file, child)
                task_entries.append(
                    TaskEntry(
                        label=parent_label,
                        text=parent.text,
                        outcome="failed",
                        elapsed=round(time.monotonic() - run_start, 2),
                        model=current_model or "",
                        attempts=max_retries,
                        task_id=parent.task_id or "",
                    )
                )
                failed_task = f"{parent_label}) {format_task_id(parent)}{parent.text}"
                failed_reason = batch_prior_errors
                notify(
                    f"Giving up on batch: {format_task_id(parent)}{parent.text}",
                    level="error",
                )
                terminal_failure = f"Batch failed: {format_task_id(parent)}{parent.text}"
                break

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
            next_task=f"{label}) {format_task_id(task)}{task.text}",
        )
        print(
            formatting.task_header(
                label,
                f"{format_task_id(task)}{task.text}",
                active_cli,
            ),
            flush=True,
        )

        _lifecycle._current_phase = "task"
        _lifecycle._current_task_label = label
        _lifecycle._current_task_text = task.text
        _lifecycle._current_task_id = task.task_id or ""
        _lifecycle._phase_start_time = time.monotonic()

        eliminated = get_eliminated(tasks, task)
        task_start = time.monotonic()
        success = False
        # Reset per-task parity-field locals so the no-success summary
        # entry does not leak the previous task's result or
        # changed_files. ``run_task`` reassigns ``result`` on every
        # attempt; ``changed_files`` is set inside the success branch
        # only, so it must default to an empty list here.
        result: RunResult | None = None
        changed_files = []
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
                    timeout=task_timeout or DEFAULT_TASK_TIMEOUT,
                    is_bug_task=(active_file == bugs_path),
                    task_id=task.task_id or "",
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
                            parse(bugs_path)
                            + (parse(current_plan_path) if current_plan_path.exists() else []),
                            total,
                            project_dir,
                            notes_snapshot,
                        )
                        print("\nExiting.", flush=True)
                        _build_and_write_summary(
                            project_dir,
                            run_start_iso,
                            elapsed_seconds=total,
                            mode=_run_mode,
                            task_entries=task_entries,
                            check_entries=check_entries,
                            commit_hashes=commit_hashes,
                            terminal_status="interrupted",
                            failure_detail="User interrupted during session limit wait",
                            stuck=[f"{format_task_id(task)}{task.text}"],
                        )
                        return RunStatus(
                            "interrupted",
                            stuck=[f"{format_task_id(task)}{task.text}"],
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
                    if result.exit_code == -2:
                        timeout_m = (task_timeout or DEFAULT_TASK_TIMEOUT) // 60
                        notify(
                            f"Task {label} timed out after {timeout_m}m.",
                            level="warning",
                        )
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
                    if active_file == bugs_path:
                        last_error = (
                            "Bug task produced no file changes. A bug task"
                            " must actually modify code; treating this as"
                            " a failure rather than auto-checking."
                        )
                        print(
                            formatting.error_msg(
                                "Bug task produced no changes - treating as failure"
                            ),
                            flush=True,
                        )
                        break
                    if _is_readonly_task(task.text):
                        elapsed = _format_elapsed(
                            time.monotonic() - task_start,
                        )
                        check_off(active_file, task)
                        if active_file == current_plan_path and active_phase_name:
                            acceptance_evidence_phases.add(active_phase_name)
                        completed.append(f"{label}) {format_task_id(task)}{task.text}")
                        print(
                            formatting.system_msg(
                                "Task is a read-only no-op by design; treating as"
                                " executed acceptance evidence"
                            ),
                            flush=True,
                        )
                        print(
                            formatting.task_complete(label, elapsed),
                            flush=True,
                        )
                        ctx.add(label, task.text, elapsed, result.output)
                        success = True
                        break
                    if _has_task_specific_acceptance_evidence(
                        task.text,
                        result.output,
                        task_id=task.task_id or "",
                    ):
                        elapsed = _format_elapsed(
                            time.monotonic() - task_start,
                        )
                        check_off(active_file, task)
                        if active_file == current_plan_path and active_phase_name:
                            acceptance_evidence_phases.add(active_phase_name)
                        completed.append(f"{label}) {format_task_id(task)}{task.text}")
                        task_entries.append(
                            TaskEntry(
                                label=label,
                                text=task.text,
                                outcome="success",
                                elapsed=round(time.monotonic() - task_start, 2),
                                model=task_model or "",
                                attempts=attempt,
                                success=True,
                                exit_code=result.exit_code,
                                log_path=str(result.log_path) if result.log_path else "",
                                changed_files=[],
                                task_id=task.task_id or "",
                            )
                        )
                        print(
                            formatting.system_msg(
                                "Task produced no file changes but provided"
                                " task-specific acceptance evidence"
                            ),
                            flush=True,
                        )
                        print(
                            formatting.task_complete(label, elapsed),
                            flush=True,
                        )
                        ctx.add(label, task.text, elapsed, result.output)
                        _ledger_settle(
                            label,
                            TaskOutcome(
                                success=True,
                                abandoned=False,
                                summary=task.text[:200],
                                changed_files=(),
                            ),
                        )
                        success = True
                        break
                    # Non-read-only no-op: editor was supposed to make
                    # changes or produce a dedicated verification artifact,
                    # but didn't. A globally green suite is not acceptance
                    # evidence for this task.
                    last_error = (
                        "Session produced no file changes and no task-specific"
                        " acceptance evidence."
                    )
                    print(
                        formatting.error_msg("No-op task without acceptance evidence"),
                        flush=True,
                    )
                    break

                _lifecycle._current_phase = "checks"
                run_autofix(project_dir)
                changed_files = _changed_files(project_dir)
                if not changed_files and _has_uncommitted_changes(project_dir):
                    last_error = "Autofix modified metadata-only files"
                    print(
                        formatting.error_msg(
                            f"Autofix modified metadata-only files"
                            f" (attempt {attempt}/{max_retries})"
                        ),
                        flush=True,
                    )
                    continue
                pre_check_status = _worktree_status(project_dir)
                check_result = run_checks(
                    project_dir,
                    changed_files=changed_files,
                )
                if not check_result.passed:
                    salvaged, patched = try_salvage_style_failures(
                        project_dir,
                        check_result.output,
                    )
                    if salvaged:
                        print(
                            formatting.system_msg(
                                "Salvaged minor style failures in: " + ", ".join(patched)
                            ),
                            flush=True,
                        )
                        changed_files = _changed_files(project_dir)
                        pre_check_status = _worktree_status(project_dir)
                        check_result = run_checks(
                            project_dir,
                            changed_files=changed_files,
                        )
                if check_result.passed:
                    post = set(_worktree_status(project_dir).splitlines())
                    pre = set(pre_check_status.splitlines()) if pre_check_status else set()
                    if post != pre:
                        last_error = "Checker introduced uncommitted changes"
                        print(
                            formatting.error_msg(
                                f"Checker introduced uncommitted changes"
                                f" (attempt {attempt}/{max_retries})"
                            ),
                            flush=True,
                        )
                        continue
                    try:
                        task_hash = _commit(
                            project_dir,
                            f"{format_task_id(task)}{task.text}",
                        )
                    except RuntimeError as exc:
                        print(
                            formatting.error_msg(str(exc)),
                            flush=True,
                        )
                        task_entries.append(
                            TaskEntry(
                                label=label,
                                text=task.text,
                                outcome="failed",
                                elapsed=round(time.monotonic() - task_start, 2),
                                model=task_model or "",
                                attempts=attempt,
                                success=False,
                                exit_code=result.exit_code,
                                log_path=str(result.log_path) if result.log_path else "",
                                changed_files=list(changed_files or []),
                                task_id=task.task_id or "",
                            )
                        )
                        failed_task = f"{label}) {format_task_id(task)}{task.text}"
                        failed_reason = str(exc)
                        terminal_failure = f"Commit failed: {exc}"
                        _ledger_settle(
                            label,
                            TaskOutcome(
                                success=False,
                                abandoned=False,
                                summary=f"commit failed: {exc}",
                                changed_files=tuple(changed_files or ()),
                                failure_kind="commit_failed",
                            ),
                        )
                        break
                    if task_hash:
                        commit_hashes.append(task_hash)
                    handle_sync(project_dir, task_hash or "", task_label=label)
                    if reviewer_config:
                        _spawn_reviewer(project_dir)
                    _maybe_auto_wrap(project_dir)
                    _reinject_wrappers(project_dir)
                    check_off(active_file, task)
                    if active_file == current_plan_path and active_phase_name:
                        acceptance_evidence_phases.add(active_phase_name)
                    elapsed = _format_elapsed(
                        time.monotonic() - task_start,
                    )
                    task_entries.append(
                        TaskEntry(
                            label=label,
                            text=task.text,
                            outcome="success",
                            elapsed=round(time.monotonic() - task_start, 2),
                            model=task_model or "",
                            attempts=attempt,
                            commit_hash=task_hash,
                            success=True,
                            exit_code=result.exit_code,
                            log_path=str(result.log_path) if result.log_path else "",
                            changed_files=list(changed_files or []),
                            task_id=task.task_id or "",
                        )
                    )
                    completed.append(f"{label}) {format_task_id(task)}{task.text}")
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
                    _ledger_settle(
                        label,
                        TaskOutcome(
                            success=True,
                            abandoned=False,
                            summary=task.text[:200],
                            changed_files=tuple(changed_files or ()),
                        ),
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
            if terminal_failure:
                break

        if terminal_failure:
            break

        if not success:
            elapsed = _format_elapsed(time.monotonic() - task_start)
            # Use the per-iteration sentinels (result, changed_files)
            # set at the top of this task. They are reset at every
            # task boundary so the no-success entry never carries over
            # values from a prior task's run.
            task_entries.append(
                TaskEntry(
                    label=label,
                    text=task.text,
                    outcome="failed",
                    elapsed=round(time.monotonic() - task_start, 2),
                    model=current_model or "",
                    attempts=max_retries,
                    success=False,
                    exit_code=result.exit_code if result is not None else 0,
                    log_path=(
                        str(result.log_path) if result is not None and result.log_path else ""
                    ),
                    changed_files=list(changed_files),
                    task_id=task.task_id or "",
                )
            )
            mark_failed(active_file, task)
            failed_task = f"{label}) {format_task_id(task)}{task.text} [{elapsed}]"
            failed_reason = last_error
            notify(
                f"Giving up on: {format_task_id(task)}{task.text}",
                level="error",
            )
            terminal_failure = f"Task failed: {format_task_id(task)}{task.text}"
            _ledger_settle(
                label,
                TaskOutcome(
                    success=False,
                    abandoned=True,
                    summary=last_error or "max retries exceeded",
                    changed_files=tuple(changed_files or ()),
                    failure_kind="max_retries_exceeded",
                ),
            )
            break

        # --stop-after-one: exit after one successful task
        if stop_after_one and success:
            stopped_early = "one"
            break

    # Bug-only mode: verify the fix by launching the app, then exit.
    # Skip stage transitions, audit cycle, and build.
    if bug_only:
        if terminal_failure is None and stopped_early == "one":
            # --stop-after-one: exit cleanly after one bug fix
            _checkpoint(project_dir)
            total = time.monotonic() - run_start
            _stop_msg = "Stopped after one task as requested"
            notify(_stop_msg)
            _print_summary(
                completed,
                None,
                "",
                parse(bugs_path)
                + (parse(current_plan_path) if current_plan_path.exists() else []),
                total,
                project_dir,
                notes_snapshot,
                stop_reason=_stop_msg,
            )
            _build_and_write_summary(
                project_dir,
                run_start_iso,
                elapsed_seconds=total,
                mode=_run_mode,
                task_entries=task_entries,
                check_entries=check_entries,
                commit_hashes=commit_hashes,
                terminal_status="stopped",
                stop_reason="stop_after_one",
            )
            return RunStatus("success", detail=_stop_msg)
        if terminal_failure is None:
            remaining_bugs = has_unchecked_bugs(parse(bugs_path))
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
                purge_completed_bugs(bugs_path)
                # Verify the fix by launching the app
                failure = _launch_app_verification(project_dir)
                if failure:
                    print(
                        formatting.error_msg(f"Bug verification failed: {failure}"),
                        flush=True,
                    )
                    terminal_failure = f"Bug verification failed: {failure}"
                else:
                    print(
                        formatting.system_msg("Bug verification passed"),
                        flush=True,
                    )
                    # Clear errors.json now that all bugs are fixed and verified
                    errors_path = project_dir / ".mcloop" / "errors.json"
                    if errors_path.is_file():
                        errors_path.unlink()
        _checkpoint(project_dir)
        total = time.monotonic() - run_start
        _print_summary(
            completed,
            failed_task,
            failed_reason,
            parse(bugs_path) + (parse(current_plan_path) if current_plan_path.exists() else []),
            total,
            project_dir,
            notes_snapshot,
        )
        if terminal_failure:
            _build_and_write_summary(
                project_dir,
                run_start_iso,
                elapsed_seconds=total,
                mode=_run_mode,
                task_entries=task_entries,
                check_entries=check_entries,
                commit_hashes=commit_hashes,
                terminal_status="failure",
                failure_detail=terminal_failure,
            )
            return RunStatus("failure", detail=terminal_failure)
        stuck = [
            f"{format_task_id(t)}{t.text}"
            for t in parse(bugs_path)
            if t.stage == "Bugs" and not t.checked and not t.failed
        ]
        if stuck:
            notify(f"Bug-only mode: {len(stuck)} bug(s) could not be fixed", level="error")
            _build_and_write_summary(
                project_dir,
                run_start_iso,
                elapsed_seconds=total,
                mode=_run_mode,
                task_entries=task_entries,
                check_entries=check_entries,
                commit_hashes=commit_hashes,
                terminal_status="failure",
                failure_detail="Bug-only mode: unfixed bugs remain",
                stuck=stuck,
            )
            return RunStatus("failure", stuck=stuck, detail="Bug-only mode: unfixed bugs remain")
        else:
            notify("Bug-only mode: all bugs fixed")
            _build_and_write_summary(
                project_dir,
                run_start_iso,
                elapsed_seconds=total,
                mode=_run_mode,
                task_entries=task_entries,
                check_entries=check_entries,
                commit_hashes=commit_hashes,
                terminal_status="success",
            )
            return RunStatus("success")

    # --- Post-loop processing ---
    # terminal_failure may already be set from a task/commit failure above.
    # Phase-boundary full-suite and build checks are handled inside the
    # loop's phase transition block. Post-loop only needs: audit (when
    # all phases are done) and summary generation.

    # --stop-after-one: skip post-loop processing entirely
    if stopped_early == "one" and terminal_failure is None:
        _checkpoint(project_dir)
        total = time.monotonic() - run_start
        _stop_msg = "Stopped after one task as requested"
        notify(_stop_msg)
        _print_summary(
            completed,
            None,
            "",
            parse(bugs_path) + (parse(current_plan_path) if current_plan_path.exists() else []),
            total,
            project_dir,
            notes_snapshot,
            stop_reason=_stop_msg,
        )
        _build_and_write_summary(
            project_dir,
            run_start_iso,
            elapsed_seconds=total,
            mode=_run_mode,
            task_entries=task_entries,
            check_entries=check_entries,
            commit_hashes=commit_hashes,
            terminal_status="stopped",
            stop_reason="stop_after_one",
        )
        return RunStatus("success", detail=_stop_msg)

    summary_remaining_tasks: list[Task] = []
    success_msg: str | None = None
    _summary_full_suite: bool | None = None
    _summary_build: bool | None = None
    _summary_audit: str | None = None

    if terminal_failure is None:
        phase_done_more_remain = bool(completed_stage) and current_plan_path.exists()
        if stopped_early == "stage" or phase_done_more_remain:
            # Phase completed, full suite + build already ran in-loop.
            summary_remaining_tasks = parse(bugs_path) + (
                parse(current_plan_path) if current_plan_path.exists() else []
            )
            _summary_full_suite = True  # Passed in-loop (otherwise terminal_failure would be set)
            _summary_build = True
            msg = f"{completed_stage} complete."
            next_stg = (
                get_current_phase_name(current_plan_path) if current_plan_path.exists() else None
            )
            if next_stg:
                msg += f" Run mcloop again to start {next_stg}."
            success_msg = msg
        else:
            # All phases done. Full suite + build already ran at last
            # phase boundary. Run audit.
            summary_remaining_tasks = parse(bugs_path) + (
                parse(current_plan_path) if current_plan_path.exists() else []
            )
            _summary_full_suite = True
            _summary_build = True

            # Audit: run if enabled
            if not no_audit:
                from mcloop.audit import AuditResult

                _lifecycle._current_phase = "audit"
                _lifecycle._phase_start_time = time.monotonic()
                _audit_start = time.monotonic()
                audit_result = _run_audit_fix_cycle(
                    project_dir,
                    log_dir,
                    model=model,
                )
                _summary_audit = audit_result.value
                _audit_elapsed = _format_elapsed(time.monotonic() - _audit_start)
                if audit_result == AuditResult.fixed:
                    _audit_hash = _get_git_hash(project_dir)
                    if _audit_hash:
                        commit_hashes.append(_audit_hash)
                if audit_result == AuditResult.failed:
                    print(
                        formatting.error_msg(f"Audit failed [{_audit_elapsed}]"),
                        flush=True,
                    )
                    notify(
                        "Run ended: audit session failed (crashed, timed out,"
                        " or audit report not produced). Completion skipped.",
                        level="error",
                    )
                    terminal_failure = "Audit failed: session crashed or audit report not produced"
                else:
                    print(
                        formatting.system_msg(f"Audit completed [{_audit_elapsed}]"),
                        flush=True,
                    )

            if terminal_failure is None:
                success_msg = "All tasks completed!"
    else:
        # Task/commit failure: show remaining tasks in summary
        summary_remaining_tasks = parse(bugs_path) + (
            parse(current_plan_path) if current_plan_path.exists() else []
        )

    # --- Single exit point for all non-bug-only paths ---
    _checkpoint(project_dir)
    total = time.monotonic() - run_start
    _phase_done_more_remain = bool(completed_stage) and current_plan_path.exists()
    # Use success_msg as the summary's stop_reason whenever it is
    # set. Under the phase-transition contract, success_msg may be
    # "All tasks completed!" (loop advanced through every phase and
    # exited via next_phase is None) OR "<phase> complete. Run mcloop
    # again..." (--stop-after-stage / phase_done_more_remain). In all
    # cases we want the message to appear verbatim in the summary
    # rather than letting _print_summary fall back to the "Run mcloop
    # again for the next stage" template — which is wrong when the
    # run actually finished every stage.
    _stop_reason = success_msg or ""
    _print_summary(
        completed,
        failed_task,
        failed_reason,
        summary_remaining_tasks,
        total,
        project_dir,
        notes_snapshot,
        completed_stage=completed_stage or "",
        stop_reason=_stop_reason,
    )
    if terminal_failure:
        _terminal_status = "failure"
    elif stopped_early == "stage":
        _terminal_status = "stopped"
    else:
        _terminal_status = "success"
    _summary_stop_reason = (
        "stop_after_stage" if stopped_early == "stage" and not terminal_failure else ""
    )
    _stuck = [
        f"{format_task_id(t)}{t.text}"
        for t in summary_remaining_tasks
        if not t.checked and not t.failed
    ]
    _build_and_write_summary(
        project_dir,
        run_start_iso,
        elapsed_seconds=total,
        mode=_run_mode,
        task_entries=task_entries,
        check_entries=check_entries,
        commit_hashes=commit_hashes,
        terminal_status=_terminal_status,
        failure_detail=terminal_failure or "",
        stop_reason=_summary_stop_reason,
        stuck=_stuck if terminal_failure else [],
        full_suite_passed=_summary_full_suite,
        build_passed=_summary_build,
        audit_result=_summary_audit,
    )
    if terminal_failure:
        return RunStatus("failure", detail=terminal_failure)
    if success_msg:
        notify(success_msg)
    return RunStatus("success")


def _prescan_maintain_parent_flags(argv: list[str]) -> str | None:
    """Return a moved flag name if it appears before the literal
    ``maintain`` token in *argv*, else ``None``.

    Narrow scope by design: only the three flags moved onto the maintain
    subparser, only the long-option forms, only when they appear BEFORE
    the ``maintain`` subcommand. Stops at ``--`` (option separator).

    The post-parse loop-flag gate cannot detect this case for these
    flags because argparse's subparser-vs-parent scoping silently
    overwrites the parent value with the subparser's default. Surface
    the old pre-subcommand spelling here so the user gets a clear
    error instead of running maintain with config/default CLI.
    """
    if "maintain" not in argv:
        return None
    maintain_idx = argv.index("maintain")
    forbidden = {"--cli", "--model", "--stop-after-one"}
    for i in range(maintain_idx):
        tok = argv[i]
        if tok == "--":
            return None
        flag = tok.split("=", 1)[0]
        if flag in forbidden:
            return flag
    return None


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
    parser.add_argument(
        "--stop-after-stage",
        action="store_true",
        help="Stop after completing the current stage instead of advancing",
    )
    parser.add_argument(
        "--stop-after-one",
        action="store_true",
        help="Run exactly one task then exit (bypasses batching)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-task timeout in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help=(
            "Reset failed-task markers ([!] back to [ ]) in"
            " CURRENT_PLAN.md and BUGS.md before starting the loop"
        ),
    )
    parser.add_argument(
        "--no-plan-ledger",
        dest="no_plan_ledger",
        action="store_true",
        help=(
            "Disable Plan Ledger (Slice D) emission and threshold "
            "evaluation for this run. Mirrors MCLOOP_NO_PLAN_LEDGER=1."
        ),
    )
    parser.add_argument(
        "--no-auto-reauthor",
        dest="no_auto_reauthor",
        action="store_true",
        help=(
            "Emit Plan Ledger events and evaluate thresholds, but do "
            "NOT auto-invoke duplo's re-author when a crossing fires. "
            "Hard-stop instead. Mirrors MCLOOP_NO_AUTO_REAUTHOR=1."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    sync_parser = subparsers.add_parser("sync", help="Sync PLAN.md with the codebase")
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without modifying PLAN.md"
    )
    subparsers.add_parser("audit", help="Audit the codebase for bugs")
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
    idea_parser = subparsers.add_parser("idea", help="Append an idea to IDEAS.md")
    idea_parser.add_argument("text", help="The idea text to record")
    maintain_parser = subparsers.add_parser(
        "maintain", help="Check and enforce invariants from MAINTAIN.md"
    )
    maintain_parser.add_argument(
        "--cli",
        default=None,
        choices=["claude", "codex"],
        help="CLI backend (default: claude, or set in ~/.mcloop/config.json)",
    )
    maintain_parser.add_argument(
        "--model",
        default=None,
        help="Model to use (e.g., opus, sonnet, gpt-5.4)",
    )
    maintain_parser.add_argument(
        "--stop-after-one",
        action="store_true",
        help="Run exactly one maintain task then exit",
    )
    subparsers.add_parser(
        "ack-orchestra-override",
        help=(
            "Acknowledge the project-local .orchestra/config.json so the "
            "override banner is silenced until the file changes"
        ),
    )
    moved = _prescan_maintain_parent_flags(sys.argv[1:])
    if moved is not None:
        parser.error(
            f"{moved} belongs to the maintain subcommand. "
            f"Write `mcloop maintain {moved} ...` instead."
        )
    args = parser.parse_args()

    # Parent-level loop flags configure the bare-loop action. They
    # are ignored by every subcommand. Reject them when a subcommand
    # is present so users don't get silent acceptance of flags that
    # do nothing.
    _LOOP_ONLY_FLAGS: dict[str, tuple[str, object]] = {
        # attr_name: (cli_form, default_value)
        "dry_run": ("--dry-run", False),
        "max_retries": ("--max-retries", 3),
        "model": ("--model", None),
        "cli": ("--cli", None),
        "fallback_model": ("--fallback-model", None),
        "no_audit": ("--no-audit", False),
        "reviewer": ("--reviewer", False),
        "allow_web_tools": ("--allow-web-tools", False),
        "stop_after_stage": ("--stop-after-stage", False),
        "stop_after_one": ("--stop-after-one", False),
        "timeout": ("--timeout", None),
        "retry": ("--retry", False),
        "no_plan_ledger": ("--no-plan-ledger", False),
        "no_auto_reauthor": ("--no-auto-reauthor", False),
    }
    # `investigate` is excluded transitionally: _cmd_investigate
    # forwards parent-level --model / --fallback-model to the spawned
    # child mcloop process until those flags move onto the investigate
    # subparser in a follow-up.
    _TRANSITIONAL_PARENT_FLAG_CONSUMERS = {"investigate"}
    # Flags now declared on a subparser are owned by that subparser;
    # the pre-subcommand spelling is rejected upstream by the argv
    # prescan, so post-parse values for these attrs reflect the
    # canonical (subparser-level) form and must not trip the gate.
    _SUBPARSER_OWNED: dict[str, set[str]] = {
        "maintain": {"cli", "model", "stop_after_one"},
    }
    if args.command is not None and args.command not in _TRANSITIONAL_PARENT_FLAG_CONSUMERS:
        owned = _SUBPARSER_OWNED.get(args.command, set())
        bad: list[str] = []
        for attr, (cli_form, default) in _LOOP_ONLY_FLAGS.items():
            if attr in owned:
                continue
            if getattr(args, attr, default) != default:
                bad.append(cli_form)
        # The sync/install/uninstall subparsers declare their OWN
        # --dry-run, which is the canonical scope. argparse stores
        # the subcommand's value at args.dry_run after dispatch, so
        # presence of a subcommand-level --dry-run is indistinguishable
        # from a parent-level one. Drop --dry-run from `bad` when the
        # active subcommand legitimately supports it.
        if args.command in ("sync", "install", "uninstall") and "--dry-run" in bad:
            bad.remove("--dry-run")
        if bad:
            parser.error(
                f"the following flags only apply to the bare-loop "
                f"action and are not valid with subcommand "
                f"{args.command!r}: " + ", ".join(sorted(bad))
            )

    return args


def _cmd_ack_orchestra_override(project_dir: Path) -> None:
    """Acknowledge the project-local Orchestra config override.

    Computes the sha256 fingerprint of
    ``<project_dir>/.orchestra/config.json`` and writes it to
    ``<project_dir>/.mcloop/orchestra-override-ack``. Subsequent mcloop
    runs suppress the override banner as long as the local config
    bytes match the recorded fingerprint. An edit to the local config
    invalidates the ack and the banner returns until the user re-runs
    this subcommand.

    Exits non-zero if the project does not have a local override file
    (there is nothing to acknowledge).
    """
    from mcloop.orchestra_override import (
        fingerprint,
        project_orchestra_config_path,
        write_ack,
    )

    config_path = project_orchestra_config_path(project_dir)
    if not config_path.is_file():
        print(
            "ack-orchestra-override: no project-local "
            ".orchestra/config.json found; nothing to acknowledge",
            file=sys.stderr,
        )
        sys.exit(1)
    digest = fingerprint(config_path)
    written = write_ack(project_dir, digest)
    print(
        f"Acknowledged {config_path}. Banner silenced until the file changes. Ack file: {written}"
    )


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
    """Launch a Claude Code session to audit the codebase."""
    from mcloop.audit import AUDIT_REPORT_FILE

    project_dir = checklist_path.parent
    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    log_dir = project_dir / "logs"
    report_path = project_dir / AUDIT_REPORT_FILE
    report_path.parent.mkdir(parents=True, exist_ok=True)
    existing = report_path.read_text() if report_path.exists() else ""
    result = run_audit(project_dir, log_dir, model=model, existing_bugs=existing)
    if not result.success:
        print(f"audit: session exited with code {result.exit_code}", file=sys.stderr)
        sys.exit(result.exit_code)
    if report_path.exists():
        print(report_path.read_text())
    else:
        print("audit: audit report was not written", file=sys.stderr)


def _cmd_maintain(
    project_dir: Path,
    cli: str | None = None,
    model: str | None = None,
    stop_after_one: bool = False,
) -> None:
    """Run maintain mode: check and enforce MAINTAIN.md invariants."""
    from mcloop.install_cmd import _load_mcloop_config

    maintain_path = project_dir / "MAINTAIN.md"
    if not maintain_path.exists():
        print("MAINTAIN.md not found", file=sys.stderr)
        sys.exit(1)
    resolved_cli = cli or _load_mcloop_config().get("cli", "claude")
    summary = run_maintain(
        maintain_path, cli=resolved_cli, model=model, stop_after_one=stop_after_one
    )
    if summary.failed > 0:
        sys.exit(1)


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


def _run_build(project_dir: Path) -> BuildResult:
    """Run the auto-detected or configured build command."""
    build_cmd = detect_build(project_dir)
    if not build_cmd:
        return BuildResult(ran=False, passed=True)
    print(
        formatting.system_msg(f"Building: {build_cmd}"),
        flush=True,
    )
    try:
        parts = shlex.split(build_cmd)
    except ValueError:
        print(formatting.error_msg(f"Malformed build command: {build_cmd}"), flush=True)
        return BuildResult(ran=True, passed=False, command=build_cmd)
    try:
        result = subprocess.run(
            parts,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        combined_output = result.stdout + result.stderr
        if result.returncode == 0:
            print(formatting.system_msg("Build succeeded"), flush=True)
            return BuildResult(ran=True, passed=True, command=build_cmd)
        else:
            print(
                formatting.error_msg(f"Build failed (exit {result.returncode})"),
                flush=True,
            )
            _print_error_tail(combined_output)
            return BuildResult(ran=True, passed=False, command=build_cmd, output=combined_output)
    except Exception as e:
        print(formatting.error_msg(f"Build error: {e}"), flush=True)
        return BuildResult(ran=True, passed=False, command=build_cmd, output=str(e))


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

    _stage_safe(project_dir, label="auto-wrap")
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
    _stage_safe(project_dir, label="reinject")
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
