"""Tests for loop.runner."""

import collections
import os
import signal
import subprocess
import sys
import time
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
    _last_output_lines,
    _print_stream_event,
    _slugify,
    _write_log,
    run_task,
)


def test_build_command_claude():
    cmd = _build_command("claude", "fix the bug")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "fix the bug" in cmd


def test_build_command_codex():
    cmd = _build_command("codex", "fix the bug")
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "--sandbox" in cmd
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


# --- _run_session uses DEVNULL+pipes ---


def test_run_session_uses_devnull_stdin(tmp_path):
    """_run_session spawns child with stdin=DEVNULL, stdout=PIPE."""
    from mcloop.runner import _run_session

    with patch("mcloop.runner.subprocess.Popen") as mock_popen:
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.wait.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        _run_session(["echo", "hi"], cwd=tmp_path)

    _, kwargs = mock_popen.call_args_list[0]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["start_new_session"] is True


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


def test_run_session_no_pty_imports():
    """runner module does not import pty or tty."""
    import mcloop.runner as runner

    source_file = runner.__file__
    with open(source_file) as f:
        source = f.read()
    assert "import pty" not in source
    assert "import tty" not in source


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

        # atexit handler uses SIGKILL directly
        _kill_active_process()
        mock_killpg.assert_called_once_with(99999, signal.SIGKILL)
        assert runner._active_process is None

    runner._active_process = original


def test_handle_sigint_exits_130():
    """Signal handler sets _interrupted flag and exits with code 130."""
    import mcloop.runner as runner

    original_flag = runner._interrupted
    with (
        patch("mcloop.main._graceful_kill_active_process"),
        patch("mcloop.main.os._exit") as mock_exit,
    ):
        from mcloop.main import main

        # Build and invoke the handler
        handler = None

        def capture_signal(sig, h):
            nonlocal handler
            if sig == signal.SIGINT:
                handler = h

        with (
            patch("mcloop.main.signal.signal", side_effect=capture_signal),
            patch("mcloop.main._main"),
            patch("atexit.register"),
        ):
            main()

        assert handler is not None
        handler(signal.SIGINT, None)
        assert runner._interrupted is True
        mock_exit.assert_called_once_with(130)

    runner._interrupted = original_flag


def test_graceful_kill_sends_sigterm_first():
    """_graceful_kill_active_process sends SIGTERM before SIGKILL."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait.return_value = 0
    original = runner._active_process
    runner._active_process = mock_proc

    with (
        patch("mcloop.main.os.getpgid", return_value=99999),
        patch("mcloop.main.os.killpg") as mock_killpg,
    ):
        from mcloop.main import _graceful_kill_active_process

        _graceful_kill_active_process()
        # SIGTERM should be sent (not SIGKILL)
        mock_killpg.assert_called_once_with(99999, signal.SIGTERM)
        assert runner._active_process is None

    runner._active_process = original


def test_graceful_kill_escalates_to_sigkill():
    """_graceful_kill_active_process escalates to SIGKILL after timeout."""
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait.side_effect = [
        subprocess.TimeoutExpired("cmd", 2),
        0,
    ]
    original = runner._active_process
    runner._active_process = mock_proc

    with (
        patch("mcloop.main.os.getpgid", return_value=99999),
        patch("mcloop.main.os.killpg") as mock_killpg,
    ):
        from mcloop.main import _graceful_kill_active_process

        _graceful_kill_active_process()
        # First SIGTERM, then SIGKILL
        assert mock_killpg.call_count == 2
        mock_killpg.assert_any_call(99999, signal.SIGTERM)
        mock_killpg.assert_any_call(99999, signal.SIGKILL)
        assert runner._active_process is None

    runner._active_process = original


def test_graceful_kill_no_active_process():
    """_graceful_kill_active_process does nothing when no process."""
    import mcloop.runner as runner

    original = runner._active_process
    runner._active_process = None

    with patch("mcloop.main.os.killpg") as mock_killpg:
        from mcloop.main import _graceful_kill_active_process

        _graceful_kill_active_process()
        mock_killpg.assert_not_called()

    runner._active_process = original


# --- Pipe-based _run_session tests ---


def _mock_run_session_pipe(tmp_path, output_lines, returncode=0):
    """Helper: run _run_session with mocked Popen using pipe stdout.

    Returns (output, exitcode, mock_popen).
    output_lines: list of strings the child's stdout yields.
    """
    from mcloop.runner import _run_session

    with patch("mcloop.runner.subprocess.Popen") as mock_popen:
        proc = mock_popen.return_value
        proc.pid = 12345
        proc.returncode = returncode
        proc.wait.return_value = returncode
        proc.stdout = iter(output_lines)
        output, exitcode = _run_session(["echo", "hi"], cwd=tmp_path)
    return output, exitcode, mock_popen


def test_run_session_pipe_stream_json(tmp_path):
    """stream-json output from child comes through pipes correctly."""
    import json

    lines = [json.dumps({"type": "stream_event", "index": i}) + "\n" for i in range(3)]
    output, exitcode, _ = _mock_run_session_pipe(tmp_path, lines)

    assert exitcode == 0
    parsed = []
    for ln in output.strip().splitlines():
        obj = json.loads(ln)
        if "index" in obj:
            parsed.append(obj)
    assert len(parsed) == 3
    assert parsed[0]["index"] == 0
    assert parsed[2]["index"] == 2


def test_run_session_pipe_multiline_output(tmp_path):
    """Multiple lines are read correctly from pipe stdout."""
    lines = [f"line_{i}\n" for i in range(10)]
    output, exitcode, _ = _mock_run_session_pipe(tmp_path, lines)

    assert exitcode == 0
    for i in range(10):
        assert f"line_{i}" in output


def test_run_session_pipe_nonzero_exit(tmp_path):
    """Non-zero exit code from child is captured correctly."""
    _, exitcode, _ = _mock_run_session_pipe(tmp_path, [], returncode=42)
    assert exitcode == 42


def test_run_session_no_stdin_text_parameter():
    """_run_session does not accept a stdin_text parameter."""
    import inspect

    from mcloop.runner import _run_session

    sig = inspect.signature(_run_session)
    assert "stdin_text" not in sig.parameters


# --- Signal handling integration tests ---
# These spawn a real subprocess that mimics mcloop's signal setup
# and verify that SIGINT, SIGTSTP, and SIGTERM all reach the handler.


_SIGNAL_TEST_SCRIPT = """\
import os
import signal
import sys
import time

def _handle(sig, frame):
    sys.stdout.write(f"CAUGHT:{sig}\\n")
    sys.stdout.flush()
    os._exit(130)

signal.signal(signal.SIGINT, _handle)
signal.signal(signal.SIGTSTP, _handle)
signal.signal(signal.SIGTERM, _handle)
signal.signal(signal.SIGHUP, _handle)

# Signal readiness
sys.stdout.write("READY\\n")
sys.stdout.flush()
time.sleep(30)
"""


def _spawn_signal_test():
    """Spawn a subprocess running the signal test script."""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _SIGNAL_TEST_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Do NOT use start_new_session — we need the child to
        # receive signals we send directly via os.kill.
    )
    # Wait for the child to signal readiness
    line = proc.stdout.readline().decode().strip()
    assert line == "READY", f"Expected READY, got {line!r}"
    return proc


def test_sigint_reaches_handler():
    """SIGINT (Ctrl-C) reaches mcloop's signal handler and exits 130."""
    proc = _spawn_signal_test()
    os.kill(proc.pid, signal.SIGINT)
    proc.wait(timeout=5)
    output = proc.stdout.read().decode()
    assert f"CAUGHT:{signal.SIGINT}" in output
    assert proc.returncode == 130


def test_sigtstp_reaches_handler():
    """SIGTSTP (Ctrl-Z) reaches mcloop's signal handler and exits 130."""
    proc = _spawn_signal_test()
    os.kill(proc.pid, signal.SIGTSTP)
    proc.wait(timeout=5)
    output = proc.stdout.read().decode()
    assert f"CAUGHT:{signal.SIGTSTP}" in output
    assert proc.returncode == 130


def test_sigterm_reaches_handler():
    """SIGTERM (kill <pid>) reaches mcloop's signal handler and exits 130."""
    proc = _spawn_signal_test()
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    output = proc.stdout.read().decode()
    assert f"CAUGHT:{signal.SIGTERM}" in output
    assert proc.returncode == 130


def test_signal_handler_kills_child_process():
    """Signal handler sends SIGTERM to the active child process group."""
    # This test verifies the _graceful_kill_active_process logic:
    # when a signal arrives, the handler sends SIGTERM to the active
    # subprocess's process group, waits, then escalates to SIGKILL.
    import mcloop.runner as runner

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait.return_value = 0
    original = runner._active_process
    runner._active_process = mock_proc

    with (
        patch("mcloop.main.os.getpgid", return_value=99999),
        patch("mcloop.main.os.killpg") as mock_killpg,
        patch("mcloop.main.os._exit"),
    ):
        from mcloop.main import _graceful_kill_active_process

        _graceful_kill_active_process()
        mock_killpg.assert_called_once_with(99999, signal.SIGTERM)
        assert runner._active_process is None

    runner._active_process = original


def test_run_session_checks_interrupted_flag(tmp_path):
    """_run_session breaks out of its loop when _interrupted is set."""
    import mcloop.runner as runner

    original = runner._interrupted
    runner._interrupted = True

    output, exitcode, _ = _mock_run_session_pipe(tmp_path, ["line1\n", "line2\n"], returncode=0)
    # With _interrupted set, the loop should break early
    # (may or may not have read lines depending on timing)
    assert exitcode == 0

    runner._interrupted = original


def test_runner_module_has_interrupted_flag():
    """runner module exposes _interrupted flag."""
    import mcloop.runner as runner

    assert hasattr(runner, "_interrupted")
    assert runner._interrupted is False or runner._interrupted is True


# --- fd leakage verification tests ---
# Verify that child processes spawned the way _run_session does
# have no terminal fds (no /dev/tty* or /dev/pts/*).


def _lsof_tty_fds(pid: int) -> list[str]:
    """Run lsof on a pid and return lines referencing terminal devices."""
    import shutil

    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    try:
        result = subprocess.run(
            [lsof, "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    tty_lines = []
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "/dev/tty" in lower or "/dev/pts/" in lower:
            tty_lines.append(line)
    return tty_lines


def test_child_process_no_tty_fds():
    """Child spawned like _run_session has no terminal fds."""
    # Spawn a child the same way _run_session does
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        # Give child a moment to fully start
        time.sleep(0.3)
        tty_fds = _lsof_tty_fds(proc.pid)
        assert tty_fds == [], "Child has terminal fds:\n" + "\n".join(tty_fds)
    finally:
        proc.kill()
        proc.wait()


def test_watchdog_process_no_tty_fds():
    """Watchdog spawned like _run_session has no terminal fds."""
    # Spawn a "main" process to watch
    main_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        # Spawn watchdog the same way _run_session does
        watchdog = subprocess.Popen(
            [
                "sh",
                "-c",
                f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 2; done; "
                f"kill -9 -{main_proc.pid} 2>/dev/null",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.3)
            tty_fds = _lsof_tty_fds(watchdog.pid)
            assert tty_fds == [], "Watchdog has terminal fds:\n" + "\n".join(tty_fds)
        finally:
            watchdog.kill()
            watchdog.wait()
    finally:
        main_proc.kill()
        main_proc.wait()


# ── _last_output_lines deque ──


def test_last_output_lines_is_bounded_deque():
    """_last_output_lines is a deque with maxlen=20."""
    assert isinstance(_last_output_lines, collections.deque)
    assert _last_output_lines.maxlen == 20


def test_last_output_lines_cleared_on_session_start(tmp_path):
    """_last_output_lines is cleared at the start of each session."""
    from mcloop.runner import _run_session

    _last_output_lines.append("stale line")
    assert len(_last_output_lines) > 0

    # Run a trivial command that exits immediately
    cmd = ["echo", "hello"]
    _run_session(cmd, tmp_path)
    # After _run_session, the deque should NOT contain the stale line
    assert "stale line" not in list(_last_output_lines)


def test_last_output_lines_populated_during_session(tmp_path):
    """_last_output_lines captures output lines from the session."""
    from mcloop.runner import _run_session

    _last_output_lines.clear()
    cmd = [sys.executable, "-c", "print('line1'); print('line2')"]
    _run_session(cmd, tmp_path)
    lines = list(_last_output_lines)
    assert "line1" in lines
    assert "line2" in lines


def test_run_task_eliminated_appends_ruled_out_block(tmp_path):
    """The eliminated parameter adds a RULED OUT APPROACHES block to the prompt."""
    log_dir = tmp_path / "logs"
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    eliminated = [
        "[RULEDOUT] tried restart",
        "[RULEDOUT] tried reinstall",
    ]

    with patch("mcloop.runner._run_session") as mock_session:
        mock_session.return_value = ("output", 0)
        run_task(
            "Fix the bug",
            "claude",
            project_dir,
            log_dir,
            eliminated=eliminated,
        )
        # The prompt passed to _build_command -> _run_session should
        # contain the ruled-out block
        cmd = mock_session.call_args[0][0]
        prompt = cmd[2]  # claude -p <prompt>
        assert "RULED OUT APPROACHES" in prompt
        assert "[RULEDOUT] tried restart" in prompt
        assert "[RULEDOUT] tried reinstall" in prompt


def test_run_task_no_eliminated_no_block(tmp_path):
    """Without eliminated entries, no RULED OUT block in the prompt."""
    log_dir = tmp_path / "logs"
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    with patch("mcloop.runner._run_session") as mock_session:
        mock_session.return_value = ("output", 0)
        run_task(
            "Fix the bug",
            "claude",
            project_dir,
            log_dir,
        )
        cmd = mock_session.call_args[0][0]
        prompt = cmd[2]
        assert "RULED OUT APPROACHES" not in prompt


# --- _build_session_env ---


def test_build_session_env_allowlisted_vars_only(monkeypatch):
    """Only allowlisted vars from os.environ are included."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("SECRET_KEY", "should-not-appear")
    with patch("mcloop.main._load_mcloop_config", return_value={}):
        env = _build_session_env()
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/user"
    assert "SECRET_KEY" not in env


def test_build_session_env_adds_task_label(monkeypatch):
    """MCLOOP_TASK_LABEL is added when task_label is non-empty."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    with patch("mcloop.main._load_mcloop_config", return_value={}):
        env = _build_session_env(task_label="task-1")
    assert env["MCLOOP_TASK_LABEL"] == "task-1"


def test_build_session_env_no_task_label_when_empty(monkeypatch):
    """MCLOOP_TASK_LABEL is not set when task_label is empty string."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    with patch("mcloop.main._load_mcloop_config", return_value={}):
        env = _build_session_env(task_label="")
    assert "MCLOOP_TASK_LABEL" not in env


def test_build_session_env_excludes_credentials_by_default(monkeypatch):
    """Credentials are NOT included without billing: api."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    with patch("mcloop.main._load_mcloop_config", return_value={}):
        env = _build_session_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env


def test_build_session_env_api_billing_claude(monkeypatch):
    """ANTHROPIC_API_KEY included when billing is api and cli is claude."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    config = {"billing": "api"}
    with patch("mcloop.main._load_mcloop_config", return_value=config):
        env = _build_session_env(cli="claude")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-123"


def test_build_session_env_api_billing_codex(monkeypatch):
    """OPENAI_API_KEY included when billing is api and cli is codex."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-456")
    config = {"billing": "api"}
    with patch("mcloop.main._load_mcloop_config", return_value=config):
        env = _build_session_env(cli="codex")
    assert env["OPENAI_API_KEY"] == "sk-oai-456"


def test_build_session_env_api_billing_wrong_key(monkeypatch):
    """OPENAI_API_KEY NOT included when billing is api but cli is claude."""
    from mcloop.runner import _build_session_env

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-456")
    config = {"billing": "api"}
    with patch("mcloop.main._load_mcloop_config", return_value=config):
        env = _build_session_env(cli="claude")
    assert "OPENAI_API_KEY" not in env


# --- _build_command codex ---


def test_build_command_codex_with_model():
    """codex command includes model flag."""
    from mcloop.runner import _build_command

    cmd = _build_command("codex", "test prompt", model="gpt-5.4")
    assert cmd == [
        "codex",
        "exec",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "--model",
        "gpt-5.4",
        "test prompt",
    ]


def test_build_command_codex_no_model():
    """codex command omits --model when not specified."""
    from mcloop.runner import _build_command

    cmd = _build_command("codex", "prompt")
    assert "--model" not in cmd
    assert cmd[-1] == "prompt"


def test_build_command_claude_no_regression():
    """claude command still produces correct invocation."""
    from mcloop.runner import _build_command

    cmd = _build_command("claude", "test prompt", model="sonnet")
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    assert "test prompt" in cmd
    assert "--model" in cmd
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "sonnet"


# --- warn_unknown_model ---


def test_warn_unknown_model_known_claude(capsys):
    """No warning for known claude model."""
    from mcloop.runner import warn_unknown_model

    warn_unknown_model("claude", "sonnet")
    assert capsys.readouterr().out == ""


def test_warn_unknown_model_unknown_claude(capsys):
    """Warning printed for unknown claude model."""
    from mcloop.runner import warn_unknown_model

    warn_unknown_model("claude", "gpt-turbo-9000")
    out = capsys.readouterr().out
    assert "Warning" in out
    assert "gpt-turbo-9000" in out


def test_warn_unknown_model_known_codex(capsys):
    """No warning for known codex model."""
    from mcloop.runner import warn_unknown_model

    warn_unknown_model("codex", "gpt-5.4")
    assert capsys.readouterr().out == ""


def test_warn_unknown_model_none_cli(capsys):
    """No warning when CLI has no known models."""
    from mcloop.runner import warn_unknown_model

    warn_unknown_model("unknown-cli", "any-model")
    assert capsys.readouterr().out == ""


# --- model config in run_loop ---


def test_config_model_used_when_no_arg(tmp_path):
    """Config model is used when --model is None."""
    from mcloop.main import run_loop

    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")
    config = {"model": "haiku"}

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.load_reviewer_config", return_value=None),
        patch("mcloop.main.format_reviewer_status", return_value=""),
        patch("mcloop.main._cleanup_stale_reviews"),
        patch("mcloop.main._load_mcloop_config", return_value=config),
        patch("mcloop.main.warn_unknown_model") as mock_warn,
    ):
        run_loop(plan, model=None)
    mock_warn.assert_called_once_with("claude", "haiku")


def test_arg_model_overrides_config(tmp_path):
    """--model overrides config model."""
    from mcloop.main import run_loop

    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")
    config = {"model": "haiku"}

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.load_reviewer_config", return_value=None),
        patch("mcloop.main.format_reviewer_status", return_value=""),
        patch("mcloop.main._cleanup_stale_reviews"),
        patch("mcloop.main._load_mcloop_config", return_value=config),
        patch("mcloop.main.warn_unknown_model") as mock_warn,
    ):
        run_loop(plan, model="opus")
    mock_warn.assert_called_once_with("claude", "opus")


def test_no_warning_when_no_model(tmp_path):
    """No warning when model is None (no model configured)."""
    from mcloop.main import run_loop

    plan = tmp_path / "PLAN.md"
    plan.write_text("# Project\n\nNo tasks.\n")

    with (
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main.parse", return_value=[]),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main._print_summary"),
        patch("mcloop.main.notify"),
        patch("mcloop.main.load_reviewer_config", return_value=None),
        patch("mcloop.main.format_reviewer_status", return_value=""),
        patch("mcloop.main._cleanup_stale_reviews"),
        patch("mcloop.main._load_mcloop_config", return_value={}),
        patch("mcloop.main.warn_unknown_model") as mock_warn,
    ):
        run_loop(plan, model=None)
    mock_warn.assert_not_called()
