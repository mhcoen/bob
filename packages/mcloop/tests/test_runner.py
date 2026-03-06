"""Tests for loop.runner."""

import subprocess

import pytest

from mcloop.runner import (
    _build_command,
    _slugify,
    _write_log,
    bugs_md_has_bugs,
    build_audit_prompt,
    build_bug_fix_prompt,
    build_sync_prompt,
    gather_audit_context,
    gather_sync_context,
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
