"""Integration tests using real CLI backends (claude, codex).

These tests make actual API calls and require a working CLI installation.
They are skipped unless the MCLOOP_INTEGRATION environment variable is set.

Run with: MCLOOP_INTEGRATION=1 pytest -m integration tests/integration/test_real_cli.py
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop

_SKIP_REASON = "Set MCLOOP_INTEGRATION=1 to run real CLI integration tests"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(tmp_path: Path, plan_content: str) -> Path:
    """Create a temp git repo with PLAN.md and a trivial check command.

    Returns the path to PLAN.md.
    """
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text(plan_content)

    # No checks — the task is trivial (create a text file)
    (tmp_path / "mcloop.json").write_text('{"checks": ["true"]}\n')

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md


@pytest.mark.integration
@unittest.skipUnless(os.environ.get("MCLOOP_INTEGRATION"), _SKIP_REASON)
def test_real_claude_creates_file_and_commits(tmp_path):
    """Real Claude Code: create hello.txt containing 'hello', check off, commit."""
    plan_md = _setup_repo(
        tmp_path,
        "- [ ] Create a file called hello.txt containing hello\n",
    )

    with patch("mcloop.main.notify"):
        stuck = run_loop(plan_md, max_retries=2, no_audit=True)

    assert stuck == [], f"Task got stuck: {stuck}"

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
    lines = [line for line in log.stdout.strip().splitlines() if line]
    assert len(lines) >= 2, f"Expected at least 2 commits, got:\n{log.stdout}"
