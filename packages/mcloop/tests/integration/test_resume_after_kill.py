"""Integration test: resume after kill — restart picks up where it left off."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop
from mcloop.runner import RunResult


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a three-task PLAN.md."""
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text("- [ ] Create alpha.txt\n- [ ] Create beta.txt\n- [ ] Create gamma.txt\n")
    (tmp_path / "mcloop.json").write_text(
        f'{{"checks": ["{sys.executable} -c \\"print(\'ok\')\\""]}}\\n'
    )

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md


def _make_run_task(task_files: dict[str, str], kill_on: str | None = None):
    """Return a fake run_task.

    task_files maps task_text -> filename to create.
    If kill_on is set, raises KeyboardInterrupt when that task is called.
    """

    def fake_run_task(task_text, cli, project_dir, log_dir, description="", **kwargs):
        if task_text == kill_on:
            raise KeyboardInterrupt("simulated kill")

        project_dir = Path(project_dir)
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        filename = task_files[task_text]
        (project_dir / filename).write_text(f"done: {task_text}\n")
        subprocess.run(
            ["git", "add", filename],
            cwd=project_dir,
            capture_output=True,
        )

        log_path = log_dir / f"{filename}.log"
        log_path.write_text(f"task: {task_text}\n")
        return RunResult(success=True, output="done", exit_code=0, log_path=log_path)

    return fake_run_task


@pytest.mark.integration
def test_resume_after_kill_picks_up_where_it_left_off(tmp_path):
    """Kill mid-run; restart completes remaining tasks without re-running completed ones."""
    plan_md = _setup_repo(tmp_path)

    task_files = {
        "Create alpha.txt": "alpha.txt",
        "Create beta.txt": "beta.txt",
        "Create gamma.txt": "gamma.txt",
    }

    # First run: completes task 1, then gets "killed" at task 2
    with patch("mcloop.main.run_task", _make_run_task(task_files, kill_on="Create beta.txt")):
        with patch("mcloop.main.notify"):
            with pytest.raises(KeyboardInterrupt):
                run_loop(plan_md, max_retries=1, no_audit=True)

    # After kill: task 1 committed and checked off; tasks 2-3 still unchecked
    plan_content = plan_md.read_text()
    assert "- [x] Create alpha.txt" in plan_content, "task 1 should be checked off"
    assert "- [ ] Create beta.txt" in plan_content, "task 2 should still be unchecked"
    assert "- [ ] Create gamma.txt" in plan_content, "task 3 should still be unchecked"
    assert (tmp_path / "alpha.txt").exists(), "alpha.txt should exist after first run"
    assert not (tmp_path / "beta.txt").exists(), "beta.txt should not exist yet"

    # Track which tasks run on restart to ensure task 1 is not re-run
    second_run_tasks: list[str] = []

    def tracking_run_task(task_text, cli, project_dir, log_dir, description="", **kwargs):
        second_run_tasks.append(task_text)
        inner = _make_run_task(task_files)
        return inner(task_text, cli, project_dir, log_dir, description, **kwargs)

    # Second run: should complete tasks 2 and 3 only
    with patch("mcloop.main.run_task", tracking_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == [], f"Expected no stuck tasks on restart, got: {stuck}"
    assert second_run_tasks == ["Create beta.txt", "Create gamma.txt"], (
        f"Restart should only run unchecked tasks, got: {second_run_tasks}"
    )

    plan_content = plan_md.read_text()
    assert "- [x] Create alpha.txt" in plan_content
    assert "- [x] Create beta.txt" in plan_content
    assert "- [x] Create gamma.txt" in plan_content

    assert (tmp_path / "alpha.txt").exists()
    assert (tmp_path / "beta.txt").exists()
    assert (tmp_path / "gamma.txt").exists()

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Create alpha.txt" in log.stdout
    assert "Create beta.txt" in log.stdout
    assert "Create gamma.txt" in log.stdout
    assert log.stdout.count("Create alpha.txt") == 1, "task 1 should be committed exactly once"
