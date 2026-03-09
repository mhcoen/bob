"""Generate investigation plans from bug context."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class BugContext:
    """All available context about a bug to investigate."""

    crash_report: str = ""
    user_description: str = ""
    failure_history: str = ""
    source_summary: str = ""
    app_type: str = ""  # "gui", "cli", "web", or ""


def build_plan_generation_prompt(ctx: BugContext) -> str:
    """Build the prompt sent to a Claude Code session to generate an investigation plan.

    The prompt includes the debugging playbook, standalone-probe instruction,
    web-search instruction, and the "What has been tried" section populated
    from ctx.failure_history.
    """
    parts: list[str] = []

    parts.append(
        "You are generating an investigation plan for a bug."
        " Follow this debugging playbook strictly:\n\n" + DEBUGGING_PLAYBOOK
    )
    parts.append(PROBES_INSTRUCTION)
    parts.append(WEB_SEARCH_INSTRUCTION)

    if ctx.user_description:
        parts.append(f"Bug description: {ctx.user_description}")
    if ctx.crash_report:
        parts.append(f"Crash report:\n```\n{ctx.crash_report}\n```")
    if ctx.source_summary:
        parts.append(f"Source summary: {ctx.source_summary}")

    parts.append("## What has been tried\n")
    if ctx.failure_history:
        parts.append(ctx.failure_history)
    else:
        parts.append("Nothing yet.")

    app_guidance = ""
    if ctx.app_type == "gui":
        app_guidance = (
            "This is a GUI app. Use process_monitor.run_gui() to launch,"
            " app_interact for UI interaction, and screenshot_window()"
            " for visual verification."
        )
    elif ctx.app_type == "web":
        app_guidance = (
            "This is a web app. Use process_monitor.launch() to start"
            " the server and web_interact for browser interaction."
        )
    elif ctx.app_type == "cli":
        app_guidance = (
            "This is a CLI app. Use process_monitor.run_cli() for execution and output capture."
        )
    if app_guidance:
        parts.append(app_guidance)

    parts.append(
        "Generate a PLAN.md with markdown checklist items (- [ ])"
        " following the playbook order: research, reproduce,"
        " instrument, isolate, inspect, fix, verify, clean up."
    )

    return "\n\n".join(parts)


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

    # Investigation steps
    lines.append("## Steps")
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
