"""Generate investigation plans and gather bug context."""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from mcloop.checks import detect_app_type, detect_run

DEBUGGING_PLAYBOOK = (
    "1. Reproduce the problem.\n"
    "2. Instrument at stage boundaries.\n"
    "3. Isolate subsystems with standalone probes.\n"
    "4. Inspect live runtime behavior.\n"
    "5. Only then patch production code.\n"
    "6. Clean up temporary scaffolding after the fix."
)

PROBES_INSTRUCTION = (
    "For any subsystem whose behavior is unclear, create a standalone"
    " probe script that exercises just that subsystem in isolation."
    " The probe should print or log enough to confirm or rule out"
    " the hypothesis. Delete probe scripts after the investigation."
)

WEB_SEARCH_INSTRUCTION = (
    "Before writing code to fix or work around the issue, search the"
    " web for known issues, working examples, and upstream fixes."
    " Prefer proven solutions over ad-hoc patches."
)


TESTING_INSTRUCTION = (
    "When writing tests during an investigation, exercise real code"
    " with real inputs. Do not mock the core logic under test — only"
    " mock external boundaries (network, filesystem, system APIs)."
    " For threaded or async code, write tests that detect deadlocks"
    " by running with a timeout. For system APIs that may fail due"
    " to permissions (e.g., macOS accessibility, screen recording),"
    " handle the permission-denied case gracefully in tests: skip"
    " or assert the expected error rather than letting the test crash."
)

DEBUGGING_INSTRUCTION = (
    "When debugging, decompose the problem before patching."
    " Break the symptom into smaller questions: what changed,"
    " what subsystem is involved, what are the inputs and outputs"
    " at the boundary. Answer each question with evidence (logs,"
    " probes, tests) before writing a fix."
    " Search the web for working examples of the API or pattern"
    " you are using — do not assume your mental model of the API"
    " is correct. When the same approach fails twice, stop and"
    " question your assumptions: re-read the documentation, check"
    " version differences, and verify that the environment matches"
    " what you expect. Three failed attempts at the same strategy"
    " means the strategy is wrong, not the execution."
)


@dataclass
class BugContext:
    """All available context about a bug to investigate."""

    crash_report: str = ""
    user_description: str = ""
    failure_history: str = ""
    source_summary: str = ""
    app_type: str = ""  # "gui", "cli", "web", or ""


def generate_plan(ctx: BugContext) -> str:
    """Produce an investigation PLAN.md from bug context.

    The plan follows the debugging playbook and includes steps
    for reproduction, instrumentation, isolation, inspection,
    fixing, and cleanup. When an app_type is known, plan steps
    reference the process monitor and app interaction layer.
    """
    lines: list[str] = []
    lines.append("# Investigation Plan")
    lines.append("")
    lines.append("## Debugging Playbook")
    lines.append("")
    lines.append(DEBUGGING_PLAYBOOK)
    lines.append("")
    lines.append(PROBES_INSTRUCTION)
    lines.append("")
    lines.append(WEB_SEARCH_INSTRUCTION)
    lines.append("")

    # Bug description section
    lines.append("## Bug Description")
    lines.append("")
    if ctx.user_description:
        lines.append(ctx.user_description)
    else:
        lines.append("No user description provided.")
    lines.append("")

    # Crash report section
    if ctx.crash_report:
        lines.append("## Crash Report")
        lines.append("")
        lines.append("```")
        lines.append(ctx.crash_report)
        lines.append("```")
        lines.append("")

    # Source summary section
    if ctx.source_summary:
        lines.append("## Source Summary")
        lines.append("")
        lines.append(ctx.source_summary)
        lines.append("")

    # What has been tried section
    lines.append("## What Has Been Tried")
    lines.append("")
    if ctx.failure_history:
        lines.append(ctx.failure_history)
    else:
        lines.append("Nothing yet.")
    lines.append("")

    # Investigation steps — Stage heading so planfile-backed parsers
    # recognize the task block as a stage rather than orphan content.
    lines.append("## Stage 1: Steps")
    lines.append("")
    _add_steps(lines, ctx)

    return "\n".join(lines)


def _add_steps(lines: list[str], ctx: BugContext) -> None:
    """Append checklist steps based on the bug context."""
    step = 1

    # Step 1: Research
    lines.append(
        f"- [ ] {step}. Search the web for known issues matching"
        " this bug's symptoms before writing any code"
    )
    step += 1

    # Step 2: Reproduce
    reproduce_detail = _reproduce_step(ctx.app_type)
    lines.append(f"- [ ] {step}. Reproduce the problem: {reproduce_detail}")
    step += 1

    # Step 3: Instrument
    lines.append(
        f"- [ ] {step}. Instrument at stage boundaries to narrow down where the failure occurs"
    )
    step += 1

    # Step 4: Isolate
    lines.append(f"- [ ] {step}. Isolate the failing subsystem with a standalone probe script")
    step += 1

    # Step 5: Inspect
    inspect_detail = _inspect_step(ctx.app_type)
    lines.append(f"- [ ] {step}. Inspect live runtime behavior: {inspect_detail}")
    step += 1

    # Step 6: Fix
    lines.append(f"- [ ] {step}. Apply the fix to production code")
    step += 1

    # Step 7: Verify
    verify_detail = _verify_step(ctx.app_type)
    lines.append(f"- [ ] {step}. Verify the fix: {verify_detail}")
    step += 1

    # Step 8: Clean up
    lines.append(
        f"- [ ] {step}. Clean up temporary scaffolding"
        " (probe scripts, debug logging, test fixtures)"
    )
    lines.append("")


def _reproduce_step(app_type: str) -> str:
    """Return reproduction instructions appropriate to the app type."""
    if app_type == "gui":
        return (
            "launch the app with process_monitor.run_gui(),"
            " use app_interact to trigger the failing action,"
            " and confirm the crash or hang is observed"
        )
    if app_type == "web":
        return (
            "launch the app with process_monitor.launch(),"
            " use web_interact to navigate to the failing page,"
            " and confirm the error is observed"
        )
    if app_type == "cli":
        return "run the command with process_monitor.run_cli() and confirm the failure is observed"
    return "run the failing scenario and confirm the bug is observed"


def _inspect_step(app_type: str) -> str:
    """Return inspection instructions appropriate to the app type."""
    if app_type == "gui":
        return (
            "use app_interact.list_elements() to inspect window"
            " state, screenshot_window() to capture visual state"
        )
    if app_type == "web":
        return "use web_interact to read page content and take a screenshot of the current state"
    if app_type == "cli":
        return "use process_monitor.read_output() to capture and examine program output"
    return "examine logs, tracebacks, and runtime output"


def _verify_step(app_type: str) -> str:
    """Return verification instructions appropriate to the app type."""
    if app_type == "gui":
        return (
            "re-run with process_monitor.run_gui() and"
            " app_interact to confirm the bug no longer occurs"
        )
    if app_type == "web":
        return "re-run with process_monitor and web_interact to confirm the bug no longer occurs"
    if app_type == "cli":
        return "re-run with process_monitor.run_cli() to confirm the bug no longer occurs"
    return "re-run the failing scenario to confirm the fix"


def _find_recent_crash_report(
    max_age_seconds: int = 3600,
    process_name: str | None = None,
) -> str:
    """Find the most recent .ips crash report from DiagnosticReports.

    Returns the contents of the newest .ips file modified within
    max_age_seconds, or an empty string if none found. When
    ``process_name`` is provided, only .ips files whose filename
    starts with that name are considered.
    """
    reports_dir = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    if not reports_dir.is_dir():
        return ""
    now = time.time()
    candidates: list[Path] = []
    for entry in reports_dir.iterdir():
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if entry.suffix != ".ips" or (now - mtime) >= max_age_seconds:
            continue
        if process_name and not entry.name.startswith(process_name):
            continue
        candidates.append(entry)
    if not candidates:
        return ""
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        return newest.read_text()
    except OSError:
        return ""


def _derive_process_name(project_dir: Path) -> str:
    """Derive a process name to filter crash reports by.

    For GUI apps launched via ``open X.app``, returns ``X``.
    Otherwise falls back to the project directory name.
    """
    run_cmd = detect_run(project_dir)
    if run_cmd:
        try:
            parts = shlex.split(run_cmd)
        except ValueError:
            parts = []
        if parts and parts[0] == "open":
            for p in parts[1:]:
                if p.endswith(".app"):
                    return Path(p).stem
    return project_dir.name


def gather_bug_context(
    project_dir: Path,
    *,
    description: str | None = None,
    log_path: str | None = None,
    stdin_text: str = "",
) -> BugContext:
    """Collect bug context from all available sources.

    Sources (in order of priority):
    - description: user-provided bug description from CLI argument
    - log_path: path to a log file specified via --log
    - stdin_text: text piped via stdin
    - .mcloop/last-run.log: log from the most recent mcloop run
    - ~/Library/Logs/DiagnosticReports/: most recent macOS crash report
    - detect_app_type: classify the project as gui/cli/web
    """
    crash_report = _find_recent_crash_report(process_name=_derive_process_name(project_dir))

    # Collect failure history from log sources
    failure_parts: list[str] = []

    # --log file
    if log_path:
        log_file = Path(log_path)
        if log_file.is_file():
            try:
                content = log_file.read_text().strip()
                if content:
                    failure_parts.append(f"From {log_path}:\n{content}")
            except OSError:
                pass

    # Piped stdin
    if stdin_text.strip():
        failure_parts.append(f"From stdin:\n{stdin_text.strip()}")

    # .mcloop/last-run.log
    last_run = project_dir / ".mcloop" / "last-run.log"
    if last_run.is_file():
        try:
            content = last_run.read_text().strip()
            if content:
                failure_parts.append(f"From last-run.log:\n{content}")
        except OSError:
            pass

    failure_history = "\n\n".join(failure_parts)
    app_type = detect_app_type(project_dir)

    return BugContext(
        crash_report=crash_report,
        user_description=description or "",
        failure_history=failure_history,
        app_type=app_type,
    )
