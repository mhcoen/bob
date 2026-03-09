"""Tests for mcloop.investigator."""

from mcloop.investigator import BugContext, generate_plan


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
