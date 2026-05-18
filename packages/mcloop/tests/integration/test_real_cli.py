"""Integration tests using real CLI backends (claude, codex).

These tests make actual API calls and require working CLI authentication.
They are skipped unless both MCLOOP_INTEGRATION and MCLOOP_REAL_CLI are set.

Run with:
  MCLOOP_INTEGRATION=1 MCLOOP_REAL_CLI=1 pytest -m integration tests/integration/test_real_cli.py
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop

_SKIP_REASON = "Set MCLOOP_INTEGRATION=1 and MCLOOP_REAL_CLI=1 to run real CLI integration tests"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(
    tmp_path: Path,
    plan_content: str,
    mcloop_json: str | None = None,
) -> Path:
    """Create a temp git repo with PLAN.md and a check command.

    Returns the path to PLAN.md.
    """
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text(plan_content)

    if mcloop_json is None:
        mcloop_json = '{"checks": ["true"]}\n'
    (tmp_path / "mcloop.json").write_text(mcloop_json)

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md


@pytest.mark.integration
@unittest.skipUnless(
    os.environ.get("MCLOOP_INTEGRATION") and os.environ.get("MCLOOP_REAL_CLI"),
    _SKIP_REASON,
)
def test_real_claude_creates_file_and_commits(tmp_path):
    """Real Claude Code: create hello.txt containing 'hello', check off, commit."""
    plan_md = _setup_repo(
        tmp_path,
        "- [ ] Create a file called hello.txt containing hello\n",
    )

    with patch("mcloop.main.notify"):
        result = run_loop(plan_md, max_retries=2, no_audit=True)

    assert result.ok, f"Expected success, got: {result}"

    # File was created with expected content
    hello = tmp_path / "hello.txt"
    assert hello.exists(), "hello.txt was not created"
    content = hello.read_text()
    assert "hello" in content.lower(), f"Unexpected content: {content!r}"

    # Task was checked off
    plan_content = plan_md.read_text()
    assert "- [x]" in plan_content, f"Task not checked off:\n{plan_content}"

    # A commit was made (beyond the initial one)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    commit_lines = [line for line in log.stdout.strip().splitlines() if line]
    assert len(commit_lines) >= 2, f"Expected at least 2 commits, got:\n{log.stdout}"


@pytest.mark.integration
@unittest.skipUnless(
    os.environ.get("MCLOOP_INTEGRATION") and os.environ.get("MCLOOP_REAL_CLI"),
    _SKIP_REASON,
)
def test_real_codex_creates_file_and_commits(tmp_path):
    """Real Codex: create hello.txt containing 'hello', check off, commit."""
    plan_md = _setup_repo(
        tmp_path,
        "- [ ] Create a file called hello.txt containing hello\n",
    )

    with patch("mcloop.main.notify"):
        result = run_loop(plan_md, max_retries=2, no_audit=True, cli="codex")

    assert result.ok, f"Expected success, got: {result}"

    # File was created with expected content
    hello = tmp_path / "hello.txt"
    assert hello.exists(), "hello.txt was not created"
    content = hello.read_text()
    assert "hello" in content.lower(), f"Unexpected content: {content!r}"

    # Task was checked off
    plan_content = plan_md.read_text()
    assert "- [x]" in plan_content, f"Task not checked off:\n{plan_content}"

    # A commit was made (beyond the initial one)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    commit_lines = [line for line in log.stdout.strip().splitlines() if line]
    assert len(commit_lines) >= 2, f"Expected at least 2 commits, got:\n{log.stdout}"


@pytest.mark.integration
@unittest.skipUnless(
    os.environ.get("MCLOOP_INTEGRATION") and os.environ.get("MCLOOP_REAL_CLI"),
    _SKIP_REASON,
)
def test_real_claude_check_failure_retries_and_fails(tmp_path):
    """Real Claude Code: task succeeds but check always fails → retries, marked [!]."""
    plan_md = _setup_repo(
        tmp_path,
        "- [ ] Create a file called hello.txt containing hello\n",
        mcloop_json='{"checks": ["test -f goodbye.txt"]}\n',
    )

    with patch("mcloop.main.notify"):
        result = run_loop(plan_md, max_retries=2, no_audit=True)

    # Task failed (exhausted retries) → failure status
    assert not result.ok, f"Expected failure, got: {result}"

    # hello.txt was created by Claude (task itself succeeds)
    hello = tmp_path / "hello.txt"
    assert hello.exists(), "hello.txt was not created"

    # goodbye.txt was never created (nothing asks for it)
    assert not (tmp_path / "goodbye.txt").exists()

    # Task was marked failed, not checked off
    plan_content = plan_md.read_text()
    assert "- [!]" in plan_content, f"Task not marked failed:\n{plan_content}"
    assert "- [x]" not in plan_content, f"Task should not be checked off:\n{plan_content}"

    # No commits beyond the initial one (checks never passed)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    commit_lines = [line for line in log.stdout.strip().splitlines() if line]
    assert len(commit_lines) == 1, f"Expected only initial commit, got:\n{log.stdout}"
