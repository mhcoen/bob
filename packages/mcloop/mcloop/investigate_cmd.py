"""Investigation subcommand and related helpers."""

from __future__ import annotations

import json as _json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mcloop import formatting, worktree
from mcloop.checklist import Task, parse
from mcloop.checks import detect_app_type, detect_run
from mcloop.investigator import gather_bug_context, generate_plan
from mcloop.notify import notify

MAX_VERIFICATION_ROUNDS = 3


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


def _cmd_investigate(args, checklist_path: Path) -> None:
    """Handle the 'investigate' subcommand."""
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
    model = getattr(args, "model", None)
    if model:
        cmd.extend(["--model", model])
    fallback_model = getattr(args, "fallback_model", None)
    if fallback_model:
        cmd.extend(["--fallback-model", fallback_model])

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
