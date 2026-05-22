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
    steps_section = plan.split("## Stage 1: Steps\n\n")[1]
    for line in steps_section.strip().splitlines():
        assert line.startswith("- [ ] "), f"Not a checklist item: {line!r}"


def test_generated_plan_parses_through_planfile_compat_shim(tmp_path):
    """Pin: generated investigation plans must parse through the shim
    without losing structure. The `## Stage 1: Steps` heading is the
    sole task-bearing stage; the other `##` blocks (Debugging Playbook,
    Bug Description, etc.) are documentation and contribute no tasks.
    """
    from mcloop._planfile_compat import parse as shim_parse

    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(generate_plan(BugContext(app_type="cli")))
    tasks = shim_parse(plan_path)

    assert len(tasks) == 8, (
        f"expected 8 stage-1 tasks, got {len(tasks)}: "
        f"{[t.text[:40] for t in tasks]}"
    )
    assert all(t.stage == "Stage 1: Steps" for t in tasks)
    assert all(not t.checked for t in tasks)


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
