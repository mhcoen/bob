"""Integration test: minimal end-to-end run through run_loop."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop
from mcloop.runner import RunResult


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with PLAN.md and trivial check config."""
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text("- [ ] Create hello.txt\n")

    # Use a trivial check so run_checks always passes
    (tmp_path / "mcloop.json").write_text('{"checks": ["python -c \\"print(\'ok\')\\""]}\n')

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md


@pytest.mark.integration
def test_minimal_run_file_created_task_checked_off_commit_made(tmp_path):
    """run_loop: task runs, file is created, task is checked off, commit is made."""
    plan_md = _setup_repo(tmp_path)
    output_file = tmp_path / "hello.txt"

    def fake_run_task(task_text, cli, project_dir, log_dir, description="", **kwargs):
        project_dir = Path(project_dir)
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Simulate the task: create hello.txt and stage it
        (project_dir / "hello.txt").write_text("hello from task\n")
        subprocess.run(
            ["git", "add", "hello.txt"],
            cwd=project_dir,
            capture_output=True,
        )

        log_path = log_dir / "fake.log"
        log_path.write_text(f"task: {task_text}\n")
        return RunResult(success=True, output="done", exit_code=0, log_path=log_path)

    with patch("mcloop.main.run_task", fake_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == [], f"Expected no stuck tasks, got: {stuck}"
    assert output_file.exists(), "hello.txt should have been created by the task"
    assert output_file.read_text() == "hello from task\n"

    plan_content = plan_md.read_text()
    assert "- [x] Create hello.txt" in plan_content, (
        f"Task should be checked off in PLAN.md, got:\n{plan_content}"
    )

    log_result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Create hello.txt" in log_result.stdout, (
        f"Expected a commit for the task, git log:\n{log_result.stdout}"
    )
