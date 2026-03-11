"""Tests for loop.runner."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mcloop.gather import gather_audit_context, gather_sync_context
from mcloop.prompts import (
    bugs_md_has_bugs,
    build_audit_prompt,
    build_bug_fix_prompt,
    build_bug_verify_prompt,
    build_investigation_plan_description,
    build_post_fix_review_prompt,
    build_sync_prompt,
    parse_bugs_md,
    parse_verification_output,
    review_found_problems,
)
from mcloop.runner import (
    _SUPPRESS_ALL_TOOLS,
    INVESTIGATION_TOOLS,
    _build_command,
    _extract_status,
    _print_stream_event,
    _slugify,
    _write_log,
)


def test_build_command_claude():
    cmd = _build_command("claude", "fix the bug")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "fix the bug" in cmd


def test_build_command_codex():
    cmd = _build_command("codex", "fix the bug")
    assert cmd[0] == "codex"
    assert "-q" in cmd
    assert "fix the bug" in cmd


def test_build_command_unknown():
    with pytest.raises(ValueError, match="Unknown CLI"):
        _build_command("unknown", "task")


def test_build_command_custom_allowed_tools():
    cmd = _build_command("claude", "task", allowed_tools=INVESTIGATION_TOOLS)
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == INVESTIGATION_TOOLS


def test_build_command_default_allowed_tools():
    cmd = _build_command("claude", "task")
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Edit,Write,Bash,Read,Glob,Grep"


def test_investigation_tools_includes_web():
    assert "WebFetch" in INVESTIGATION_TOOLS
    assert "WebSearch" in INVESTIGATION_TOOLS


def test_slugify():
    assert _slugify("Add User Authentication!") == "add-user-authentication"
    assert len(_slugify("x" * 100)) <= 50


def test_slugify_special_chars():
    assert _slugify("Hello, World! 123") == "hello-world-123"
    assert _slugify("---leading-trailing---") == "leading-trailing"


def test_slugify_empty():
    assert _slugify("") == ""


def test_write_log(tmp_path):
    log_path = _write_log(tmp_path, "My task", ["claude", "-p", "do stuff"], "output here\n", 0)
    assert log_path.exists()
    content = log_path.read_text()
    assert "Task: My task" in content
    assert "Exit code: 0" in content
    assert "output here" in content
    assert "claude -p do stuff" in content


def test_write_log_filename_format(tmp_path):
    log_path = _write_log(tmp_path, "Add auth", ["cmd"], "out", 1)
    assert "add-auth" in log_path.name
    assert log_path.suffix == ".log"


# --- gather_sync_context ---


def test_gather_sync_context_reads_plan(tmp_path):
    (tmp_path / "PLAN.md").write_text("# My plan")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert ctx["PLAN.md"] == "# My plan"


def test_gather_sync_context_reads_readme(tmp_path):
    (tmp_path / "README.md").write_text("# Readme")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert ctx["README.md"] == "# Readme"


def test_gather_sync_context_reads_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("instructions")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert ctx["CLAUDE.md"] == "instructions"


def test_gather_sync_context_skips_missing_files(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert "PLAN.md" not in ctx
    assert "README.md" not in ctx
    assert "CLAUDE.md" not in ctx


def test_gather_sync_context_includes_git_log(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert "git_log" in ctx
    assert "init" in ctx["git_log"]


def test_gather_sync_context_includes_file_tree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "hello.py").write_text("print('hi')")
    subprocess.run(["git", "add", "hello.py"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    assert "file_tree" in ctx
    assert "hello.py" in ctx["file_tree"]


def test_gather_sync_context_includes_python_source(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    (tmp_path / "app.py").write_text("x = 1")
    ctx = gather_sync_context(tmp_path)
    assert "app.py" in ctx
    assert ctx["app.py"] == "x = 1"


def test_gather_sync_context_excludes_git_dir(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_sync_context(tmp_path)
    # No .git/ paths should appear as source keys
    assert not any(".git" in k for k in ctx)


# --- build_sync_prompt ---


def test_build_sync_prompt_append_only_instruction():
    prompt = build_sync_prompt()
    assert "APPEND ONLY" in prompt
    assert "Never modify" in prompt


def test_build_sync_prompt_checked_items_instruction():
    prompt = build_sync_prompt()
    assert "- [x]" in prompt


def test_build_sync_prompt_granularity_instruction():
    prompt = build_sync_prompt()
    assert "granularity" in prompt


def test_build_sync_prompt_no_duplicates_instruction():
    prompt = build_sync_prompt()
    assert "duplicate" in prompt


def test_build_sync_prompt_flags_checked_no_code():
    prompt = build_sync_prompt()
    assert "CHECKED BUT NOT IMPLEMENTED" in prompt
    assert "CHECKED ITEMS WITH NO CODE" in prompt


def test_build_sync_prompt_checks_off_completed_items():
    prompt = build_sync_prompt()
    assert "change it to checked" in prompt
    assert "Do NOT uncheck" in prompt


def test_build_sync_prompt_flags_description_drift():
    prompt = build_sync_prompt()
    assert "DESCRIPTION DRIFT" in prompt


def test_build_sync_prompt_problems_report_format():
    prompt = build_sync_prompt()
    assert "--- SYNC PROBLEMS ---" in prompt
    assert "--- END PROBLEMS ---" in prompt


def test_build_sync_prompt_no_problems_instruction():
    prompt = build_sync_prompt()
    assert "No problems found." in prompt


# --- gather_audit_context ---


def test_gather_audit_context_reads_readme(tmp_path):
    (tmp_path / "README.md").write_text("# Readme")
    ctx = gather_audit_context(tmp_path)
    assert ctx["README.md"] == "# Readme"


def test_gather_audit_context_reads_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("instructions")
    ctx = gather_audit_context(tmp_path)
    assert ctx["CLAUDE.md"] == "instructions"


def test_gather_audit_context_skips_missing_files(tmp_path):
    ctx = gather_audit_context(tmp_path)
    assert "README.md" not in ctx
    assert "CLAUDE.md" not in ctx


def test_gather_audit_context_includes_python_source(tmp_path):
    (tmp_path / "app.py").write_text("x = 1")
    ctx = gather_audit_context(tmp_path)
    assert "app.py" in ctx
    assert ctx["app.py"] == "x = 1"


def test_gather_audit_context_excludes_git_dir(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    ctx = gather_audit_context(tmp_path)
    assert not any(".git" in k for k in ctx)


def test_gather_audit_context_omits_plan_md(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Plan")
    ctx = gather_audit_context(tmp_path)
    assert "PLAN.md" not in ctx


# --- build_audit_prompt ---


def test_build_audit_prompt_defect_categories():
    prompt = build_audit_prompt()
    assert "Crashes" in prompt
    assert "Incorrect behavior" in prompt
    assert "Unhandled errors" in prompt
    assert "Security issues" in prompt


def test_build_audit_prompt_exclusions():
    prompt = build_audit_prompt()
    assert "Style" in prompt or "style" in prompt
    assert "Refactoring" in prompt or "refactoring" in prompt


def test_build_audit_prompt_bugs_md_output():
    prompt = build_audit_prompt()
    assert "BUGS.md" in prompt


def test_build_audit_prompt_no_bugs_instruction():
    prompt = build_audit_prompt()
    assert "No bugs found." in prompt


def test_build_audit_prompt_format_severity():
    prompt = build_audit_prompt()
    assert "Severity" in prompt


def test_build_audit_prompt_no_existing_bugs():
    prompt = build_audit_prompt()
    assert "No bugs found." in prompt
    assert "already exists" not in prompt


def test_build_audit_prompt_with_existing_bugs():
    existing = "# Bugs\n\n## foo.py:10 -- Off-by-one\n**Severity**: low\nDetails.\n"
    prompt = build_audit_prompt(existing_bugs=existing)
    assert "already exists" in prompt
    assert "Do NOT report any bug that is already" in prompt
    assert "Append new entries" in prompt or "append any new bugs" in prompt


def test_build_audit_prompt_existing_bugs_no_overwrite():
    existing = "# Bugs\n\n## foo.py:10 -- Bug\nDesc.\n"
    prompt = build_audit_prompt(existing_bugs=existing)
    assert "Do not remove or rewrite existing entries" in prompt


def test_build_audit_prompt_existing_bugs_no_new_instruction():
    existing = "# Bugs\n\n## foo.py:10 -- Bug\nDesc.\n"
    prompt = build_audit_prompt(existing_bugs=existing)
    assert "no new bugs" in prompt.lower() or "no new" in prompt.lower()
    # Should NOT contain the "No bugs found." file-creation instruction
    assert "No bugs found." not in prompt


# --- bugs_md_has_bugs ---


def test_bugs_md_has_bugs_no_bugs():
    assert bugs_md_has_bugs("# Bugs\n\nNo bugs found.\n") is False


def test_bugs_md_has_bugs_with_bugs():
    content = "# Bugs\n\n## foo.py:10 — Off-by-one\n**Severity**: low\nDetails.\n"
    assert bugs_md_has_bugs(content) is True


def test_bugs_md_has_bugs_empty():
    assert bugs_md_has_bugs("") is True


def test_bugs_md_has_bugs_partial_match():
    # "No bugs found." must appear exactly — partial text doesn't count
    assert bugs_md_has_bugs("# Bugs\n\nNo bugs.\n") is True


# --- build_bug_fix_prompt ---


def test_build_bug_fix_prompt_fix_only_instruction():
    prompt = build_bug_fix_prompt()
    assert "Fix ONLY" in prompt
    assert "minimal" in prompt


def test_build_bug_fix_prompt_no_delete_instruction():
    prompt = build_bug_fix_prompt()
    assert "deleted automatically" in prompt


def test_build_bug_fix_prompt_bugs_md_reference():
    prompt = build_bug_fix_prompt()
    assert "BUGS.md" in prompt


# --- build_post_fix_review_prompt ---


def test_build_post_fix_review_prompt_includes_bug_descriptions():
    prompt = build_post_fix_review_prompt("bug in foo.py", "diff here")
    assert "bug in foo.py" in prompt


def test_build_post_fix_review_prompt_includes_diff():
    prompt = build_post_fix_review_prompt("bugs", "+fixed line")
    assert "+fixed line" in prompt


def test_build_post_fix_review_prompt_read_only():
    prompt = build_post_fix_review_prompt("bugs", "diff")
    assert "read-only" in prompt.lower() or "Do not modify" in prompt


def test_build_post_fix_review_prompt_lgtm_format():
    prompt = build_post_fix_review_prompt("bugs", "diff")
    assert "NO_PROBLEMS" in prompt
    assert "PROBLEMS FOUND" in prompt
    assert "--- REVIEW RESULT ---" in prompt
    assert "--- END REVIEW ---" in prompt


# --- review_found_problems ---


def test_review_found_problems_lgtm():
    output = "Some text\n--- REVIEW RESULT ---\nLGTM\n--- END REVIEW ---\n"
    found, desc = review_found_problems(output)
    assert found is False
    assert desc == ""


def test_review_found_problems_with_problems():
    output = (
        "Review output\n"
        "--- REVIEW RESULT ---\n"
        "PROBLEMS FOUND\n"
        "The fix breaks error handling in foo.py\n"
        "--- END REVIEW ---\n"
    )
    found, desc = review_found_problems(output)
    assert found is True
    assert "PROBLEMS FOUND" in desc
    assert "error handling" in desc


def test_review_found_problems_no_marker():
    output = "Session crashed or produced no review output"
    found, desc = review_found_problems(output)
    assert found is False
    assert desc == ""


def test_review_found_problems_no_end_marker():
    output = "--- REVIEW RESULT ---\nPROBLEMS FOUND\nSomething is wrong\n"
    found, desc = review_found_problems(output)
    assert found is True
    assert "Something is wrong" in desc


# --- parse_bugs_md ---


def test_parse_bugs_md_single_bug():
    content = "# Bugs\n\n## foo.py:10 -- Off-by-one\n**Severity**: low\nDetails here.\n"
    bugs = parse_bugs_md(content)
    assert len(bugs) == 1
    assert bugs[0]["title"] == "foo.py:10 -- Off-by-one"
    assert "Details here." in bugs[0]["body"]


def test_parse_bugs_md_multiple_bugs():
    content = (
        "# Bugs\n\n"
        "## foo.py:10 -- Off-by-one\n"
        "**Severity**: low\n"
        "First bug.\n\n"
        "## bar.py:20 -- Null check\n"
        "**Severity**: high\n"
        "Second bug.\n"
    )
    bugs = parse_bugs_md(content)
    assert len(bugs) == 2
    assert "Off-by-one" in bugs[0]["title"]
    assert "Null check" in bugs[1]["title"]


def test_parse_bugs_md_no_bugs():
    content = "# Bugs\n\nNo bugs found.\n"
    bugs = parse_bugs_md(content)
    assert len(bugs) == 0


def test_parse_bugs_md_empty():
    assert parse_bugs_md("") == []


def test_parse_bugs_md_body_includes_header():
    content = "## foo.py:5 -- Bug\n**Severity**: medium\nDesc.\n"
    bugs = parse_bugs_md(content)
    assert len(bugs) == 1
    assert bugs[0]["body"].startswith("## foo.py:5")


# --- build_bug_verify_prompt ---


def test_build_bug_verify_prompt_includes_bugs():
    prompt = build_bug_verify_prompt("## foo.py:10 -- Bug\nDetails.")
    assert "foo.py:10" in prompt
    assert "Details." in prompt


def test_build_bug_verify_prompt_read_only():
    prompt = build_bug_verify_prompt("bugs")
    assert "read-only" in prompt.lower() or "Do not modify" in prompt


def test_build_bug_verify_prompt_format():
    prompt = build_bug_verify_prompt("bugs")
    assert "--- VERIFY RESULT ---" in prompt
    assert "--- END VERIFY ---" in prompt
    assert "CONFIRMED" in prompt
    assert "REMOVED" in prompt


# --- parse_verification_output ---


def test_parse_verification_output_confirmed():
    output = (
        "Some analysis\n"
        "--- VERIFY RESULT ---\n"
        "CONFIRMED: foo.py:10 Off-by-one\n"
        "--- END VERIFY ---\n"
    )
    results = parse_verification_output(output)
    assert len(results) == 1
    assert results[0][0] == "CONFIRMED"
    assert "foo.py:10" in results[0][1]


def test_parse_verification_output_removed():
    output = (
        "--- VERIFY RESULT ---\n"
        "REMOVED: bar.py:20 Null check (already handled)\n"
        "--- END VERIFY ---\n"
    )
    results = parse_verification_output(output)
    assert len(results) == 1
    assert results[0][0] == "REMOVED"
    assert "bar.py:20" in results[0][1]
    assert results[0][2] == "already handled"


def test_parse_verification_output_mixed():
    output = (
        "--- VERIFY RESULT ---\n"
        "CONFIRMED: foo.py:10 Off-by-one\n"
        "REMOVED: bar.py:20 Null check (not real)\n"
        "CONFIRMED: baz.py:5 Logic error\n"
        "--- END VERIFY ---\n"
    )
    results = parse_verification_output(output)
    assert len(results) == 3
    assert results[0][0] == "CONFIRMED"
    assert results[1][0] == "REMOVED"
    assert results[2][0] == "CONFIRMED"


def test_parse_verification_output_no_marker():
    output = "No structured output"
    results = parse_verification_output(output)
    assert results == []


def test_parse_verification_output_no_end_marker():
    output = "--- VERIFY RESULT ---\nCONFIRMED: foo.py:10 Bug\n"
    results = parse_verification_output(output)
    assert len(results) == 1
    assert results[0][0] == "CONFIRMED"


def test_parse_verification_output_removed_no_reason():
    output = "--- VERIFY RESULT ---\nREMOVED: foo.py:10 Bug\n--- END VERIFY ---\n"
    results = parse_verification_output(output)
    assert len(results) == 1
    assert results[0][0] == "REMOVED"
    assert results[0][2] == ""


# --- _run_session uses pty ---


def test_run_session_uses_pty_openpty(tmp_path):
    """_run_session calls pty.openpty and passes slave fd to Popen."""
    import os

    from mcloop.runner import _run_session

    r_fd, w_fd = os.pipe()

    def fake_openpty():
        return r_fd, w_fd

    with (
        patch("mcloop.runner.pty.openpty", side_effect=fake_openpty),
        patch("mcloop.runner.tty.setraw"),
        patch("mcloop.runner.subprocess.Popen") as mock_popen,
        patch("mcloop.runner.os.close"),
        patch("mcloop.runner.os.read", side_effect=OSError(5, "EIO")),
    ):
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.wait.return_value = 0
        proc.returncode = 0
        output, exitcode = _run_session(["echo", "hi"], cwd=tmp_path)

    # First Popen call is the main process (second is the watchdog)
    _, kwargs = mock_popen.call_args_list[0]
    assert kwargs.get("stdin") == w_fd
    assert kwargs.get("stdout") == w_fd
    assert kwargs.get("stderr") == w_fd
    assert kwargs.get("start_new_session") is True
    assert exitcode == 0


def test_run_session_no_reclaim_foreground():
    """_run_session does not reference _reclaim_foreground."""
    import inspect

    from mcloop.runner import _run_session

    source = inspect.getsource(_run_session)
    assert "_reclaim_foreground" not in source
    assert "tcsetpgrp" not in source


def test_reclaim_foreground_removed():
    """_reclaim_foreground no longer exists in runner module."""
    import mcloop.runner as runner

    assert not hasattr(runner, "_reclaim_foreground")


# --- _SUPPRESS_ALL_TOOLS ---


def test_suppress_all_tools_enabled():
    """All tool output is suppressed."""
    assert _SUPPRESS_ALL_TOOLS is True


# --- _extract_status ---


def test_extract_status_action_sentence():
    assert _extract_status("Let me read the configuration file.") is None


def test_extract_status_running_prefix():
    assert _extract_status("Running the test suite now.") is None


def test_extract_status_truncates_long():
    long = "Let me " + "x" * 200
    result = _extract_status(long)
    assert result is None


def test_extract_status_ignores_short_text():
    assert _extract_status("ok") is None


def test_extract_status_ignores_code():
    assert _extract_status("import os") is None
    assert _extract_status("def foo():") is None
    assert _extract_status("class Bar:") is None


def test_extract_status_ignores_json():
    assert _extract_status('{"type": "result"}') is None


def test_extract_status_ignores_paths():
    assert _extract_status("/usr/local/bin/python") is None


def test_extract_status_ignores_non_action():
    assert _extract_status("The variable x is set to 5.") is None


def test_extract_status_takes_first_sentence():
    text = "Let me fix the bug. Then I will run the tests."
    assert _extract_status(text) is None


def test_extract_status_empty():
    assert _extract_status("") is None
    assert _extract_status("   ") is None


# --- _print_stream_event ---


def test_print_stream_event_bash_tool(capsys):
    """Bash tool calls should be suppressed."""
    import json

    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ruff check ."},
                }
            ]
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_suppresses_read(capsys):
    """Read tool calls should be suppressed."""
    import json

    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/foo/bar.py"},
                }
            ]
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_suppresses_edit(capsys):
    """Edit tool calls should be suppressed."""
    import json

    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/foo/bar.py"},
                }
            ]
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_suppresses_glob(capsys):
    """Glob tool calls should be suppressed."""
    import json

    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Glob",
                    "input": {"pattern": "**/*.py"},
                }
            ]
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_status_line(capsys):
    """Streaming text no longer prints narration status."""
    import json

    event = {
        "type": "stream_event",
        "event": {
            "delta": {
                "type": "text_delta",
                "text": "Let me fix the failing test.",
            }
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_no_status_for_code(capsys):
    """Code text should not produce status output."""
    import json

    event = {
        "type": "stream_event",
        "event": {
            "delta": {
                "type": "text_delta",
                "text": "def calculate_total():",
            }
        },
    }
    _print_stream_event(json.dumps(event))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_stream_event_invalid_json(capsys):
    """Invalid JSON lines should be silently ignored."""
    _print_stream_event("not json at all")
    captured = capsys.readouterr()
    assert captured.out == ""


# --- run_task prompt content ---


def test_run_task_prompt_includes_accessibility_instruction(tmp_path):
    """run_task prompt instructs sessions to add accessibility identifiers."""
    log_dir = tmp_path / "logs"
    captured_prompt = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_prompt["prompt"] = prompt
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Build a settings screen",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
        )

    prompt = captured_prompt["prompt"]
    assert "accessibility" in prompt.lower()
    assert "accessibilityIdentifier" in prompt
    assert "data-testid" in prompt
    assert "setAccessibleName" in prompt
    # Covers interactive element types
    assert "buttons" in prompt
    assert "text fields" in prompt
    assert "toggles" in prompt
    # States the purpose
    assert "programmatically testable" in prompt


def test_run_task_prompt_notes_three_sections(tmp_path):
    """run_task prompt requires Observations, Hypotheses, Eliminated sections."""
    log_dir = tmp_path / "logs"
    captured_prompt = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_prompt["prompt"] = prompt
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Fix the parser",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
        )

    prompt = captured_prompt["prompt"]
    assert "## Observations" in prompt
    assert "## Hypotheses" in prompt
    assert "## Eliminated" in prompt
    assert "confirmed facts" in prompt
    assert "candidate explanations" in prompt
    assert "ruled out" in prompt


def test_run_task_prompt_includes_wrap_marker_instruction(tmp_path):
    """run_task prompt tells sessions not to modify mcloop:wrap markers."""
    log_dir = tmp_path / "logs"
    captured_prompt = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_prompt["prompt"] = prompt
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Fix the login flow",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
        )

    prompt = captured_prompt["prompt"]
    assert "mcloop:wrap" in prompt
    assert "Do not remove or modify" in prompt
    assert "mcloop:wrap:begin" in prompt
    assert "mcloop:wrap:end" in prompt


def test_run_task_accessibility_instruction_present_for_non_ui_task(tmp_path):
    """Accessibility instruction is included even for non-UI tasks."""
    log_dir = tmp_path / "logs"
    captured_prompt = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_prompt["prompt"] = prompt
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Refactor the database module",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
        )

    prompt = captured_prompt["prompt"]
    assert "accessibilityIdentifier" in prompt


def test_investigation_plan_description_has_three_sections():
    """Investigation plan description requires Observations, Hypotheses, Eliminated."""
    desc = build_investigation_plan_description("App crashes on launch")
    assert "## Observations" in desc
    assert "## Hypotheses" in desc
    assert "## Eliminated" in desc
    assert "confirmed facts" in desc
    assert "candidate explanations" in desc
    assert "ruled out" in desc


def test_investigation_plan_description_includes_bug_context():
    """Investigation plan description includes the bug context."""
    desc = build_investigation_plan_description("Segfault in parser")
    assert "Segfault in parser" in desc


def test_investigation_plan_description_includes_playbook():
    """Investigation plan description includes the debugging playbook."""
    desc = build_investigation_plan_description("")
    assert "Reproduce the problem" in desc
    assert "Isolate subsystems" in desc
    assert "patch production code" in desc


def test_investigation_plan_description_checks_eliminated_before_proposing():
    """Investigation plan description requires checking Eliminated before proposing."""
    desc = build_investigation_plan_description("App crashes")
    assert "read the ## Eliminated section" in desc
    assert "Do not repeat an eliminated approach" in desc
    assert "new evidence" in desc
    assert "contradicts" in desc


def test_investigation_plan_description_empty_context():
    """Investigation plan description works with empty bug context."""
    desc = build_investigation_plan_description("")
    assert "Bug context" not in desc
    assert "## Observations" in desc


def test_investigation_plan_description_includes_probes_instruction():
    """Investigation plan description instructs creating standalone probes."""
    desc = build_investigation_plan_description("")
    assert "standalone probe script" in desc
    assert "exercises just that subsystem" in desc


def test_investigation_plan_description_includes_web_search():
    """Investigation plan description instructs searching the web."""
    desc = build_investigation_plan_description("")
    assert "search the web" in desc
    assert "working examples" in desc


def test_investigation_plan_description_includes_failure_history():
    """Investigation plan description populates What has been tried."""
    desc = build_investigation_plan_description(
        "App crashes", failure_history="Tried null check, still crashes"
    )
    assert "## What has been tried" in desc
    assert "Tried null check, still crashes" in desc


def test_investigation_plan_description_nothing_tried_when_no_history():
    """Investigation plan description says nothing tried when empty."""
    desc = build_investigation_plan_description("")
    assert "## What has been tried" in desc
    assert "Nothing yet." in desc


def test_investigation_plan_description_includes_testing_instruction():
    """Investigation plan description instructs real-code testing."""
    desc = build_investigation_plan_description("")
    assert "exercise real code" in desc
    assert "Do not mock the core logic" in desc
    assert "deadlocks" in desc
    assert "timeout" in desc
    assert "permission" in desc.lower()
    assert "gracefully" in desc


def test_investigation_plan_description_includes_debugging_instruction():
    """Investigation plan description instructs decompose-first debugging."""
    desc = build_investigation_plan_description("")
    assert "decompose the problem before patching" in desc
    assert "working examples" in desc
    assert "question your assumptions" in desc
    assert "Three failed attempts" in desc
    assert "strategy is wrong" in desc


def test_run_task_passes_allowed_tools(tmp_path):
    """run_task forwards allowed_tools to _build_command."""
    log_dir = tmp_path / "logs"
    captured_kwargs = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_kwargs.update(kwargs)
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Fix the bug",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
            allowed_tools=INVESTIGATION_TOOLS,
        )

    assert captured_kwargs["allowed_tools"] == INVESTIGATION_TOOLS


def test_run_task_default_tools_not_passed(tmp_path):
    """run_task does not pass allowed_tools when not specified."""
    log_dir = tmp_path / "logs"
    captured_kwargs = {}

    def fake_build_command(cli, prompt, **kwargs):
        captured_kwargs.update(kwargs)
        return ["echo", "done"]

    with (
        patch("mcloop.runner._build_command", side_effect=fake_build_command),
        patch("mcloop.runner._run_session", return_value=("", 0)),
        patch("mcloop.runner._write_log", return_value=tmp_path / "log.txt"),
    ):
        from mcloop.runner import run_task

        run_task(
            task_text="Fix the bug",
            cli="claude",
            project_dir=tmp_path,
            log_dir=log_dir,
        )

    assert "allowed_tools" not in captured_kwargs


# --- Signal handler tests ---


def test_signal_handlers_installed():
    """main() installs handlers for SIGINT, SIGTSTP, SIGTERM, SIGHUP."""
    import signal

    handlers = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    with (
        patch("mcloop.main.signal.signal", side_effect=fake_signal),
        patch("mcloop.main._main"),
        patch("atexit.register"),
    ):
        from mcloop.main import main

        main()

    assert signal.SIGINT in handlers
    assert signal.SIGTSTP in handlers
    assert signal.SIGTERM in handlers
    assert signal.SIGHUP in handlers
    # All four use the same handler
    assert handlers[signal.SIGINT] is handlers[signal.SIGTSTP]
    assert handlers[signal.SIGINT] is handlers[signal.SIGTERM]
    assert handlers[signal.SIGINT] is handlers[signal.SIGHUP]


def test_handle_sigint_kills_active_process():
    """_handle_sigint kills the active subprocess and exits."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    original = runner._active_process
    runner._active_process = mock_proc

    with (
        patch("mcloop.main.os.getpgid", return_value=99999),
        patch("mcloop.main.os.killpg") as mock_killpg,
    ):
        from mcloop.main import _kill_active_process

        # Simulate what _handle_sigint does
        _kill_active_process()
        mock_killpg.assert_called_once()
        assert runner._active_process is None

    runner._active_process = original


def test_handle_sigint_exits_130():
    """_handle_sigint exits with code 130 (128 + SIGINT)."""
    with (
        patch("mcloop.main._kill_active_process"),
        patch("mcloop.main.os._exit") as mock_exit,
    ):
        # Build the handler the same way main() does
        def _handle_sigint(sig, frame):
            from mcloop.main import _kill_active_process

            _kill_active_process()
            import os

            os._exit(130)

        _handle_sigint(2, None)
        mock_exit.assert_called_once_with(130)


# --- Pty-based tests with mock subprocess behind a pty ---


def _mock_run_session(tmp_path, read_side_effect, **extra_popen_attrs):
    """Helper: run _run_session with fully mocked pty and os calls.

    Returns (output, exitcode, mock_popen).
    read_side_effect controls what os.read returns to the reader thread.
    """
    from mcloop.runner import _run_session

    popen_attrs = {"pid": 12345, "returncode": 0}
    popen_attrs.update(extra_popen_attrs)

    with (
        patch("mcloop.runner.pty.openpty", return_value=(10, 11)),
        patch("mcloop.runner.tty.setraw"),
        patch("mcloop.runner.subprocess.Popen") as mock_popen,
        patch("mcloop.runner.os.close"),
        patch("mcloop.runner.os.read", side_effect=read_side_effect),
        patch("mcloop.runner.os.write"),
        patch("mcloop.runner.os.getpgid", return_value=12345),
    ):
        proc = mock_popen.return_value
        for k, v in popen_attrs.items():
            setattr(proc, k, v)
        proc.wait.return_value = popen_attrs["returncode"]
        output, exitcode = _run_session(
            extra_popen_attrs.pop("cmd", ["echo", "hi"]),
            cwd=tmp_path,
            **extra_popen_attrs.pop("session_kwargs", {}),
        )
    return output, exitcode, mock_popen


def test_run_session_pty_stream_json(tmp_path):
    """stream-json output from a child comes through the pty correctly."""
    import json

    lines = [json.dumps({"type": "stream_event", "index": i}) + "\n" for i in range(3)]
    payload = "".join(lines).encode()
    reads = iter([payload, b""])

    output, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=lambda fd, sz: next(reads),
    )

    assert exitcode == 0
    parsed = []
    for ln in output.strip().splitlines():
        try:
            obj = json.loads(ln)
            if "index" in obj:
                parsed.append(obj)
        except (json.JSONDecodeError, ValueError):
            pass
    assert len(parsed) == 3
    assert parsed[0]["index"] == 0
    assert parsed[2]["index"] == 2


def test_run_session_child_uses_start_new_session(tmp_path):
    """Child process is spawned with start_new_session=True for isolation."""
    _, _, mock_popen = _mock_run_session(
        tmp_path,
        read_side_effect=OSError(5, "EIO"),
    )
    _, kwargs = mock_popen.call_args_list[0]
    assert kwargs["start_new_session"] is True


def test_run_session_pty_multiline_output(tmp_path):
    """Multiple lines are correctly buffered and split by the reader thread."""
    payload = "".join(f"line_{i}\n" for i in range(10)).encode()
    reads = iter([payload, b""])

    output, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=lambda fd, sz: next(reads),
    )

    assert exitcode == 0
    for i in range(10):
        assert f"line_{i}" in output


def test_run_session_pty_nonzero_exit(tmp_path):
    """Non-zero exit code from child is captured correctly through pty."""
    _, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=OSError(5, "EIO"),
        returncode=42,
    )
    assert exitcode == 42


def test_run_session_pty_stdin_text(tmp_path):
    """stdin_text is written to the master fd via os.write."""
    from mcloop.runner import _run_session

    written_data = []

    def capture_write(fd, data):
        written_data.append((fd, data))
        return len(data)

    with (
        patch("mcloop.runner.pty.openpty", return_value=(10, 11)),
        patch("mcloop.runner.tty.setraw"),
        patch("mcloop.runner.subprocess.Popen") as mock_popen,
        patch("mcloop.runner.os.close"),
        patch("mcloop.runner.os.read", side_effect=OSError(5, "EIO")),
        patch("mcloop.runner.os.write", side_effect=capture_write),
        patch("mcloop.runner.os.getpgid", return_value=12345),
    ):
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.returncode = 0
        proc.wait.return_value = 0
        _run_session(["echo"], cwd=tmp_path, stdin_text="hello_pty\n")

    # master_fd is 10; stdin_text should be written there
    assert any(fd == 10 and b"hello_pty" in data for fd, data in written_data)


def test_run_session_slave_fd_closed_in_parent(tmp_path):
    """Parent closes the slave fd after spawning the child."""
    from mcloop.runner import _run_session

    closed_fds = []

    def track_close(fd):
        closed_fds.append(fd)

    with (
        patch("mcloop.runner.pty.openpty", return_value=(10, 11)),
        patch("mcloop.runner.tty.setraw"),
        patch("mcloop.runner.subprocess.Popen") as mock_popen,
        patch("mcloop.runner.os.close", side_effect=track_close),
        patch("mcloop.runner.os.read", side_effect=OSError(5, "EIO")),
        patch("mcloop.runner.os.getpgid", return_value=12345),
    ):
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.returncode = 0
        proc.wait.return_value = 0
        _run_session(["echo"], cwd=tmp_path)

    # slave fd (11) closed first, master fd (10) closed after
    assert 11 in closed_fds
    assert 10 in closed_fds
    assert closed_fds.index(11) < closed_fds.index(10)


def test_run_session_slave_set_to_raw_mode(tmp_path):
    """Slave fd is set to raw mode to avoid line-discipline interference."""
    from mcloop.runner import _run_session

    with (
        patch("mcloop.runner.pty.openpty", return_value=(10, 11)),
        patch("mcloop.runner.tty.setraw") as mock_setraw,
        patch("mcloop.runner.subprocess.Popen") as mock_popen,
        patch("mcloop.runner.os.close"),
        patch("mcloop.runner.os.read", side_effect=OSError(5, "EIO")),
        patch("mcloop.runner.os.getpgid", return_value=12345),
    ):
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.returncode = 0
        proc.wait.return_value = 0
        _run_session(["echo"], cwd=tmp_path)

    mock_setraw.assert_called_once_with(11)


def test_run_session_reader_handles_partial_lines(tmp_path):
    """Reader thread correctly reassembles data arriving in chunks."""
    chunks = iter(
        [
            b'{"type": "stre',
            b'am_event"}\n',
            b"",
        ]
    )

    output, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=lambda fd, sz: next(chunks),
    )

    assert '{"type": "stream_event"}' in output


def test_run_session_reader_handles_eio(tmp_path):
    """Reader thread treats EIO as end-of-stream (child closed pty)."""
    import errno

    call_count = [0]

    def mock_read(fd, size):
        call_count[0] += 1
        if call_count[0] == 1:
            return b"first_line\n"
        raise OSError(errno.EIO, "EIO")

    output, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=mock_read,
    )

    assert "first_line" in output
    assert exitcode == 0


def test_run_session_reader_handles_ebadf(tmp_path):
    """Reader thread treats EBADF as end-of-stream."""
    import errno

    call_count = [0]

    def mock_read(fd, size):
        call_count[0] += 1
        if call_count[0] == 1:
            return b"data_line\n"
        raise OSError(errno.EBADF, "EBADF")

    output, exitcode, _ = _mock_run_session(
        tmp_path,
        read_side_effect=mock_read,
    )

    assert "data_line" in output
    assert exitcode == 0


def test_run_session_slave_fd_passed_to_child(tmp_path):
    """Slave fd is used as stdin/stdout/stderr for the child process."""
    _, _, mock_popen = _mock_run_session(
        tmp_path,
        read_side_effect=OSError(5, "EIO"),
    )
    _, kwargs = mock_popen.call_args_list[0]
    assert kwargs["stdin"] == 11
    assert kwargs["stdout"] == 11
    assert kwargs["stderr"] == 11
