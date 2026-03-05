"""Tests for loop.runner."""

import subprocess

import pytest

from mcloop.runner import _build_command, _slugify, _write_log, gather_sync_context


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
    log_path = _write_log(
        tmp_path, "My task", ["claude", "-p", "do stuff"], "output here\n", 0
    )
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
