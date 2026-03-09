"""Tests for mcloop.investigator."""

from mcloop.investigator import BugContext, build_plan_generation_prompt, generate_plan


def test_plan_contains_debugging_playbook():
    """Generated plan includes the full debugging playbook."""
    plan = generate_plan(BugContext())
    assert "Reproduce the problem" in plan
    assert "Instrument at stage boundaries" in plan
    assert "Isolate subsystems" in plan
    assert "Inspect live runtime behavior" in plan
    assert "patch production code" in plan
    assert "Clean up temporary scaffolding" in plan


def test_plan_contains_probe_instruction():
    """Generated plan instructs creating standalone probes."""
    plan = generate_plan(BugContext())
    assert "standalone probe" in plan.lower()


def test_plan_contains_web_search_instruction():
    """Generated plan instructs searching the web before coding."""
    plan = generate_plan(BugContext())
    assert "search the web" in plan.lower()


def test_plan_includes_user_description():
    """User description appears in the bug description section."""
    ctx = BugContext(user_description="App crashes on startup")
    plan = generate_plan(ctx)
    assert "App crashes on startup" in plan


def test_plan_includes_crash_report():
    """Crash report appears in a dedicated section."""
    ctx = BugContext(crash_report="EXC_BAD_ACCESS at 0x0")
    plan = generate_plan(ctx)
    assert "## Crash Report" in plan
    assert "EXC_BAD_ACCESS at 0x0" in plan


def test_plan_omits_crash_report_when_empty():
    """No crash report section when none provided."""
    plan = generate_plan(BugContext())
    assert "## Crash Report" not in plan


def test_plan_includes_source_summary():
    """Source summary appears when provided."""
    ctx = BugContext(source_summary="main.py handles argument parsing")
    plan = generate_plan(ctx)
    assert "## Source Summary" in plan
    assert "main.py handles argument parsing" in plan


def test_plan_includes_failure_history():
    """Failure history populates the What Has Been Tried section."""
    ctx = BugContext(failure_history="Tried adding null check, still crashes")
    plan = generate_plan(ctx)
    assert "## What Has Been Tried" in plan
    assert "Tried adding null check" in plan


def test_plan_says_nothing_tried_when_no_history():
    """What Has Been Tried says nothing when history is empty."""
    plan = generate_plan(BugContext())
    assert "Nothing yet." in plan


def test_plan_has_research_step():
    """Plan includes a web research step."""
    plan = generate_plan(BugContext())
    assert "Search the web for known issues" in plan


def test_plan_has_isolation_step():
    """Plan includes an isolation step with probe."""
    plan = generate_plan(BugContext())
    assert "standalone probe script" in plan


def test_plan_has_verification_step():
    """Plan includes a verification step after the fix."""
    plan = generate_plan(BugContext())
    assert "Verify the fix" in plan


def test_steps_are_checklist_items():
    """All steps are markdown checklist items."""
    plan = generate_plan(BugContext())
    steps_section = plan.split("## Steps\n\n")[1]
    for line in steps_section.strip().splitlines():
        assert line.startswith("- [ ] "), f"Not a checklist item: {line!r}"


def test_gui_app_type_references_process_monitor():
    """GUI app type references process_monitor.run_gui."""
    ctx = BugContext(app_type="gui")
    plan = generate_plan(ctx)
    assert "process_monitor.run_gui()" in plan
    assert "app_interact" in plan


def test_cli_app_type_references_process_monitor():
    """CLI app type references process_monitor.run_cli."""
    ctx = BugContext(app_type="cli")
    plan = generate_plan(ctx)
    assert "process_monitor.run_cli()" in plan


def test_web_app_type_references_web_interact():
    """Web app type references web_interact."""
    ctx = BugContext(app_type="web")
    plan = generate_plan(ctx)
    assert "web_interact" in plan
    assert "process_monitor" in plan


def test_generic_app_type_no_specific_tooling():
    """Unknown app type uses generic instructions."""
    ctx = BugContext(app_type="")
    plan = generate_plan(ctx)
    assert "re-run the failing scenario" in plan


# --- build_plan_generation_prompt ---


def test_prompt_includes_debugging_playbook():
    """Plan generation prompt includes the full debugging playbook."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "Reproduce the problem" in prompt
    assert "Instrument at stage boundaries" in prompt
    assert "Isolate subsystems" in prompt
    assert "Inspect live runtime behavior" in prompt
    assert "patch production code" in prompt
    assert "Clean up temporary scaffolding" in prompt


def test_prompt_includes_probe_instruction():
    """Plan generation prompt instructs creating standalone probes."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "standalone probe script" in prompt.lower() or "standalone probe" in prompt.lower()


def test_prompt_includes_web_search_instruction():
    """Plan generation prompt instructs searching the web."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "search the web" in prompt.lower()


def test_prompt_includes_failure_history():
    """Plan generation prompt populates What has been tried."""
    ctx = BugContext(failure_history="Tried null check, still crashes")
    prompt = build_plan_generation_prompt(ctx)
    assert "## What has been tried" in prompt
    assert "Tried null check, still crashes" in prompt


def test_prompt_says_nothing_tried_when_no_history():
    """Plan generation prompt says nothing tried when history is empty."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "## What has been tried" in prompt
    assert "Nothing yet." in prompt


def test_prompt_includes_bug_description():
    """Plan generation prompt includes the user's bug description."""
    ctx = BugContext(user_description="App crashes on startup")
    prompt = build_plan_generation_prompt(ctx)
    assert "App crashes on startup" in prompt


def test_prompt_includes_crash_report():
    """Plan generation prompt includes the crash report."""
    ctx = BugContext(crash_report="EXC_BAD_ACCESS at 0x0")
    prompt = build_plan_generation_prompt(ctx)
    assert "EXC_BAD_ACCESS at 0x0" in prompt


def test_prompt_gui_app_type():
    """Plan generation prompt includes GUI-specific guidance."""
    ctx = BugContext(app_type="gui")
    prompt = build_plan_generation_prompt(ctx)
    assert "process_monitor.run_gui()" in prompt
    assert "app_interact" in prompt


def test_prompt_gui_lists_available_tools():
    """GUI prompt enumerates specific app_interact and process_monitor functions."""
    ctx = BugContext(app_type="gui")
    prompt = build_plan_generation_prompt(ctx)
    assert "app_interact.window_exists(" in prompt
    assert "app_interact.list_elements(" in prompt
    assert "app_interact.click_button(" in prompt
    assert "app_interact.screenshot_window(" in prompt
    assert "process_monitor.read_crash_report(" in prompt


def test_prompt_cli_app_type():
    """Plan generation prompt includes CLI-specific guidance."""
    ctx = BugContext(app_type="cli")
    prompt = build_plan_generation_prompt(ctx)
    assert "process_monitor.run_cli(" in prompt


def test_prompt_cli_lists_available_tools():
    """CLI prompt enumerates specific process_monitor functions."""
    ctx = BugContext(app_type="cli")
    prompt = build_plan_generation_prompt(ctx)
    assert "process_monitor.launch(" in prompt
    assert "process_monitor.read_output(" in prompt
    assert "process_monitor.send_input(" in prompt
    assert "process_monitor.is_hung(" in prompt


def test_prompt_web_app_type():
    """Plan generation prompt includes web-specific guidance."""
    ctx = BugContext(app_type="web")
    prompt = build_plan_generation_prompt(ctx)
    assert "web_interact" in prompt
    assert "process_monitor" in prompt


def test_prompt_web_lists_available_tools():
    """Web prompt enumerates specific web_interact and process_monitor functions."""
    ctx = BugContext(app_type="web")
    prompt = build_plan_generation_prompt(ctx)
    assert "browser.navigate(" in prompt
    assert "browser.click(" in prompt
    assert "browser.text()" in prompt
    assert "browser.screenshot(" in prompt
    assert "process_monitor.launch(" in prompt


def test_prompt_includes_programmatic_instruction_for_app_types():
    """Prompt includes programmatic steps instruction when app_type is set."""
    for app_type in ("gui", "cli", "web"):
        ctx = BugContext(app_type=app_type)
        prompt = build_plan_generation_prompt(ctx)
        assert "programmatic tools" in prompt.lower(), (
            f"Missing programmatic instruction for {app_type}"
        )


def test_prompt_includes_repro_steps_instruction_for_app_types():
    """Prompt includes repro-steps.json instruction when app_type is set."""
    for app_type in ("gui", "cli", "web"):
        ctx = BugContext(app_type=app_type)
        prompt = build_plan_generation_prompt(ctx)
        assert "repro-steps.json" in prompt, f"Missing repro-steps instruction for {app_type}"


def test_prompt_omits_repro_steps_instruction_without_app_type():
    """Prompt omits repro-steps.json instruction when no app_type."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "repro-steps.json" not in prompt


def test_prompt_omits_programmatic_instruction_without_app_type():
    """Prompt omits programmatic steps instruction when no app_type."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "programmatic tools" not in prompt.lower()


def test_prompt_requests_checklist_format():
    """Plan generation prompt asks for checklist items."""
    prompt = build_plan_generation_prompt(BugContext())
    assert "- [ ]" in prompt
    assert "PLAN.md" in prompt


def test_full_context_plan():
    """Plan with all context fields populated."""
    ctx = BugContext(
        crash_report="SIGSEGV in main thread",
        user_description="Window goes blank after resize",
        failure_history="Tried disabling animation, no change",
        source_summary="SwiftUI app with custom layout engine",
        app_type="gui",
    )
    plan = generate_plan(ctx)
    assert "## Crash Report" in plan
    assert "SIGSEGV" in plan
    assert "Window goes blank" in plan
    assert "Tried disabling animation" in plan
    assert "SwiftUI app" in plan
    assert "process_monitor.run_gui()" in plan


# --- Sample bug description scenarios ---


def test_crash_on_startup_has_research_step():
    """Startup crash bug plan includes a web research step."""
    ctx = BugContext(
        user_description="App crashes immediately on launch with SIGABRT",
        crash_report="SIGABRT in dyld: missing symbol _NSWindowDidBecomeKeyNotification",
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "SIGABRT" in plan


def test_crash_on_startup_has_isolation_step():
    """Startup crash bug plan includes isolation via standalone probe."""
    ctx = BugContext(
        user_description="App crashes immediately on launch with SIGABRT",
        crash_report="SIGABRT in dyld: missing symbol _NSWindowDidBecomeKeyNotification",
    )
    plan = generate_plan(ctx)
    assert "standalone probe script" in plan


def test_crash_on_startup_has_verification_step():
    """Startup crash bug plan includes post-fix verification."""
    ctx = BugContext(
        user_description="App crashes immediately on launch with SIGABRT",
        crash_report="SIGABRT in dyld: missing symbol _NSWindowDidBecomeKeyNotification",
    )
    plan = generate_plan(ctx)
    assert "Verify the fix" in plan


def test_gui_hang_plan_steps():
    """GUI hang bug plan has research, isolation, and verification steps."""
    ctx = BugContext(
        user_description="Menu bar app freezes after clicking Preferences",
        app_type="gui",
        source_summary="SwiftUI menu bar app with a settings window",
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "standalone probe script" in plan
    assert "Verify the fix" in plan
    assert "process_monitor.run_gui()" in plan
    assert "app_interact" in plan


def test_cli_segfault_plan_steps():
    """CLI segfault bug plan has research, isolation, and verification steps."""
    ctx = BugContext(
        user_description="CLI tool segfaults when given a file larger than 2GB",
        app_type="cli",
        failure_history="Tried increasing stack size with ulimit, no effect",
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "standalone probe script" in plan
    assert "Verify the fix" in plan
    assert "process_monitor.run_cli()" in plan
    assert "Tried increasing stack size" in plan


def test_web_500_error_plan_steps():
    """Web server 500 error bug plan has research, isolation, and verification."""
    ctx = BugContext(
        user_description="API returns 500 on POST /api/upload with multipart form",
        app_type="web",
        source_summary="Express.js server with multer for file uploads",
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "standalone probe script" in plan
    assert "Verify the fix" in plan
    assert "web_interact" in plan
    assert "process_monitor" in plan


def test_data_corruption_plan_steps():
    """Data corruption bug plan has research, isolation, and verification steps."""
    ctx = BugContext(
        user_description="Database entries contain garbled UTF-8 after import",
        source_summary="Python script reads CSV and writes to SQLite",
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "standalone probe script" in plan
    assert "Verify the fix" in plan
    assert "re-run the failing scenario" in plan


def test_intermittent_failure_with_history():
    """Intermittent failure bug plan includes failure history and all step types."""
    ctx = BugContext(
        user_description="Test suite fails randomly with 'connection reset by peer'",
        failure_history=(
            "Added retry logic, failures still happen.\nIncreased timeout to 30s, no improvement."
        ),
    )
    plan = generate_plan(ctx)
    assert "Search the web for known issues" in plan
    assert "standalone probe script" in plan
    assert "Verify the fix" in plan
    assert "Added retry logic" in plan
    assert "Increased timeout" in plan
