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

PROGRAMMATIC_STEPS_INSTRUCTION = (
    "Every plan step that involves launching, observing, or verifying"
    " the app MUST use the programmatic tools instead of manual"
    " actions. For example, 'Launch the app and verify the menu bar"
    " icon appears' becomes 'Launch the app with"
    " process_monitor.run_gui() and verify the window exists with"
    " app_interact.window_exists()'. Never write steps that assume a"
    " human will click, look, or type — use process_monitor,"
    " app_interact, or web_interact to do it programmatically."
)

REPRO_STEPS_INSTRUCTION = (
    "When you successfully reproduce the bug, save the reproduction"
    " steps to .mcloop/repro-steps.json as a JSON array. Each entry"
    " must have 'action' and 'args' keys matching the AUTO action"
    " format. Supported actions: run_cli, run_gui (args:"
    " 'command | process_name'), window_exists, screenshot,"
    " list_elements, click_button (args: 'app | label'), navigate,"
    " page_text. Example:\n"
    ' [{"action": "window_exists", "args": "MyApp"},'
    ' {"action": "click_button", "args": "MyApp | Start"}]\n'
    "This file is replayed automatically after the fix to verify"
    " the bug no longer occurs."
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

    if ctx.app_type:
        parts.append(PROGRAMMATIC_STEPS_INSTRUCTION)
        parts.append(REPRO_STEPS_INSTRUCTION)

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
            "This is a GUI app. Available programmatic tools:\n"
            "- process_monitor.run_gui(): launch and monitor for crash/hang\n"
            "- process_monitor.is_alive(pid): check process is running\n"
            "- process_monitor.sample(pid): capture call graph\n"
            "- process_monitor.read_crash_report(name): find crash logs\n"
            "- app_interact.window_exists(app): verify window appeared\n"
            "- app_interact.list_elements(app): inspect UI element tree\n"
            "- app_interact.click_button(app, label): click by label\n"
            "- app_interact.select_menu_item(app, path): navigate menus\n"
            "- app_interact.type_text(app, text): type into focused field\n"
            "- app_interact.read_value(app, type, label): read element value\n"
            "- app_interact.screenshot_window(app, path): capture screenshot"
        )
    elif ctx.app_type == "web":
        app_guidance = (
            "This is a web app. Available programmatic tools:\n"
            "- process_monitor.launch(cmd): start the server\n"
            "- process_monitor.is_alive(pid): check server is running\n"
            "- process_monitor.read_output(proc): read server logs\n"
            "- web_interact.launch_browser(): open headless browser\n"
            "- browser.navigate(url): go to a page\n"
            "- browser.click(selector): click an element\n"
            "- browser.text(): read visible page text\n"
            "- browser.content(): read page HTML\n"
            "- browser.screenshot(path): capture screenshot"
        )
    elif ctx.app_type == "cli":
        app_guidance = (
            "This is a CLI app. Available programmatic tools:\n"
            "- process_monitor.run_cli(cmd): run and capture output/exit code\n"
            "- process_monitor.launch(cmd): start long-running process\n"
            "- process_monitor.read_output(proc): read stdout\n"
            "- process_monitor.send_input(proc, text): write to stdin\n"
            "- process_monitor.is_hung(proc): detect stuck process\n"
            "- process_monitor.sample(pid): capture call graph"
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
