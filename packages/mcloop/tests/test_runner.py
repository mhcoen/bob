"""Tests for loop.runner."""

import os
import subprocess
from unittest.mock import patch

import pytest

from mcloop.runner import (
    _SUPPRESS_ALL_TOOLS,
    _build_command,
    _extract_status,
    _print_stream_event,
    _reclaim_foreground,
    _slugify,
    _write_log,
    bugs_md_has_bugs,
    build_audit_prompt,
    build_bug_fix_prompt,
    build_bug_verify_prompt,
    build_post_fix_review_prompt,
    build_sync_prompt,
    gather_audit_context,
    gather_sync_context,
    parse_bugs_md,
    parse_verification_output,
    review_found_problems,
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


def test_build_sync_prompt_flags_unchecked_already_done():
    prompt = build_sync_prompt()
    assert "UNCHECKED BUT ALREADY DONE" in prompt
    assert "UNCHECKED ITEMS ALREADY DONE" in prompt


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
    assert "LGTM" in prompt
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


# --- _reclaim_foreground ---


def test_reclaim_foreground_calls_tcsetpgrp():
    """Verify _reclaim_foreground opens /dev/tty and calls tcsetpgrp."""
    fake_fd = 42
    with (
        patch("os.open", return_value=fake_fd) as mock_open,
        patch("os.tcsetpgrp") as mock_tcsetpgrp,
        patch("os.getpgrp", return_value=1234),
        patch("os.close") as mock_close,
    ):
        _reclaim_foreground()
        mock_open.assert_called_once_with("/dev/tty", os.O_RDWR)
        mock_tcsetpgrp.assert_called_once_with(fake_fd, 1234)
        mock_close.assert_called_once_with(fake_fd)


def test_reclaim_foreground_no_tty():
    """_reclaim_foreground silently returns when there is no tty."""
    with (
        patch("os.open", side_effect=OSError("no tty")),
        patch("os.tcsetpgrp") as mock_tcsetpgrp,
    ):
        _reclaim_foreground()  # should not raise
        mock_tcsetpgrp.assert_not_called()


def test_reclaim_foreground_tcsetpgrp_fails():
    """_reclaim_foreground silently handles tcsetpgrp OSError."""
    fake_fd = 42
    with (
        patch("os.open", return_value=fake_fd),
        patch("os.tcsetpgrp", side_effect=OSError("not a tty")),
        patch("os.getpgrp", return_value=1234),
        patch("os.close") as mock_close,
    ):
        _reclaim_foreground()  # should not raise
        mock_close.assert_called_once_with(fake_fd)


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
