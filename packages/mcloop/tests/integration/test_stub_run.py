"""Integration test: run_loop with stub_cli as the CLI backend.

Uses the stub_cli.py script instead of mocking run_task, so the full
run_task → _build_command → _run_session pipeline executes against a
real subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop

STUB_CLI = str(Path(__file__).resolve().parent.parent / "stubs" / "stub_cli.py")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(
    tmp_path: Path,
    plan_content: str,
    scenario: dict,
) -> tuple[Path, Path]:
    """Create a temp git repo with PLAN.md and a scenario file.

    Returns (plan_md, scenario_path).
    """
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text(plan_content)

    # Trivial check command so run_checks always passes
    (tmp_path / "mcloop.json").write_text(
        json.dumps({"checks": [f"{sys.executable} -c \"print('ok')\""]}) + "\n"
    )

    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md, scenario_path


def _make_stub_build_command(scenario_path: Path):
    """Return a _build_command replacement that invokes stub_cli.py."""

    def build_command(
        cli: str,
        prompt: str | None = None,
        model: str | None = None,
        allowed_tools: str = "Edit,Write,Bash,Read,Glob,Grep",
    ) -> list[str]:
        cmd = [sys.executable, STUB_CLI, "--scenario", str(scenario_path)]
        if cli == "claude":
            cmd.append("-p")
            if prompt:
                cmd.append(prompt)
            cmd.extend(["--output-format", "stream-json"])
        elif cli == "codex":
            cmd.append("exec")
            if prompt:
                cmd.append(prompt)
        return cmd

    return build_command


@pytest.mark.integration
def test_stub_creates_file_and_checks_off_task(tmp_path):
    """run_loop with stub_cli: task creates a file, gets checked off, commit made."""
    scenario = {
        "tasks": [
            {
                "match": "Create hello.txt",
                "files": {"hello.txt": "hello from stub\n"},
                "output": "Created hello.txt",
                "exit_code": 0,
            }
        ],
        "default": {"output": "unknown task", "exit_code": 1},
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create hello.txt\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    assert (tmp_path / "hello.txt").exists()
    assert (tmp_path / "hello.txt").read_text() == "hello from stub\n"

    content = plan_md.read_text()
    assert "- [x] Create hello.txt" in content

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Create hello.txt" in log.stdout


@pytest.mark.integration
def test_stub_multiple_tasks_sequential(tmp_path):
    """run_loop processes multiple tasks sequentially via stub_cli."""
    scenario = {
        "tasks": [
            {
                "match": "Create alpha",
                "files": {"alpha.txt": "alpha\n"},
                "output": "done alpha",
                "exit_code": 0,
            },
            {
                "match": "Create beta",
                "files": {"beta.txt": "beta\n"},
                "output": "done beta",
                "exit_code": 0,
            },
        ],
        "default": {"output": "no match", "exit_code": 1},
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Create alpha\n- [ ] Create beta\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    assert (tmp_path / "alpha.txt").read_text() == "alpha\n"
    assert (tmp_path / "beta.txt").read_text() == "beta\n"

    content = plan_md.read_text()
    assert "- [x] Create alpha" in content
    assert "- [x] Create beta" in content


@pytest.mark.integration
def test_stub_failing_task_retried_and_marked_failed(tmp_path):
    """Stub returns exit_code=1 → task retried, then marked [!]."""
    scenario = {
        "tasks": [
            {
                "match": "Impossible",
                "output": "error: cannot do this",
                "exit_code": 1,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Impossible task\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=2, no_audit=True)

    assert stuck == ["Impossible task"]
    content = plan_md.read_text()
    assert "- [!] Impossible task" in content


@pytest.mark.integration
def test_stub_no_file_changes_checks_pass_auto_checked(tmp_path):
    """Stub exits 0 but creates no files → task auto-checked if checks pass."""
    scenario = {
        "tasks": [
            {
                "match": "Verify",
                "output": "looks good",
                "exit_code": 0,
            }
        ],
    }
    plan_md, scenario_path = _setup_repo(
        tmp_path,
        "- [ ] Verify everything works\n",
        scenario,
    )

    with patch(
        "mcloop.runner._build_command",
        _make_stub_build_command(scenario_path),
    ):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    content = plan_md.read_text()
    assert "- [x] Verify everything works" in content
