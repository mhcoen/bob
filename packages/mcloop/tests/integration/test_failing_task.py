"""Integration test: failing task — retry behavior and [!] marking after max retries."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.main import run_loop
from mcloop.runner import RunResult


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _setup_repo(tmp_path: Path, plan_content: str) -> Path:
    """Create a minimal git repo with the given PLAN.md content."""
    _git(["git", "init"], tmp_path)
    _git(["git", "config", "user.email", "test@mcloop.test"], tmp_path)
    _git(["git", "config", "user.name", "McLoop Test"], tmp_path)

    plan_md = tmp_path / "PLAN.md"
    plan_md.write_text(plan_content)
    (tmp_path / "mcloop.json").write_text('{"checks": ["python -c \\"print(\'ok\')\\""]}\n')

    _git(["git", "add", "."], tmp_path)
    _git(["git", "commit", "-m", "initial"], tmp_path)

    return plan_md


def _fail_result() -> RunResult:
    return RunResult(
        success=False, output="error: something broke", exit_code=1, log_path=Path("/dev/null")
    )


def _ok_result_with_file(
    task_text: str, project_dir: Path, log_dir: Path, filename: str
) -> RunResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / filename).write_text(f"done: {task_text}\n")
    subprocess.run(["git", "add", filename], cwd=project_dir, capture_output=True)
    log_path = log_dir / f"{filename}.log"
    log_path.write_text(f"task: {task_text}\n")
    return RunResult(success=True, output="done", exit_code=0, log_path=log_path)


@pytest.mark.integration
def test_task_marked_failed_after_max_retries(tmp_path):
    """A task that always fails is marked [!] after exhausting all retries."""
    plan_md = _setup_repo(tmp_path, "- [ ] Impossible task\n")

    attempt_count = [0]

    def always_fail(task_text, cli, project_dir, log_dir, description="", **kwargs):
        attempt_count[0] += 1
        return _fail_result()

    with patch("mcloop.main.run_task", always_fail):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=3, no_audit=True)

    assert stuck == ["Impossible task"]
    assert attempt_count[0] == 3, f"Expected 3 attempts, got {attempt_count[0]}"

    content = plan_md.read_text()
    assert "- [!] Impossible task" in content, (
        f"Task should be marked [!] after max retries, got:\n{content}"
    )

    # No commit should have been made for the failing task
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Impossible task" not in log.stdout, (
        f"No commit should exist for a failed task, git log:\n{log.stdout}"
    )


@pytest.mark.integration
def test_loop_stops_after_failed_task_leaving_subsequent_tasks_unchecked(tmp_path):
    """Loop halts when a task exceeds max retries; subsequent tasks remain unchecked."""
    plan_md = _setup_repo(
        tmp_path,
        "- [ ] Failing task\n- [ ] Should not run\n",
    )

    tasks_attempted: list[str] = []

    def selective_fail(task_text, cli, project_dir, log_dir, description="", **kwargs):
        tasks_attempted.append(task_text)
        return _fail_result()

    with patch("mcloop.main.run_task", selective_fail):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=2, no_audit=True)

    assert stuck == ["Failing task"]
    assert "Should not run" not in tasks_attempted, (
        f"Loop should stop after first failed task; tasks run: {tasks_attempted}"
    )

    content = plan_md.read_text()
    assert "- [!] Failing task" in content
    assert "- [ ] Should not run" in content


@pytest.mark.integration
def test_task_retried_correct_number_of_times(tmp_path):
    """Task is attempted exactly max_retries times before being marked failed."""
    plan_md = _setup_repo(tmp_path, "- [ ] Hard task\n")

    attempt_count = [0]

    def count_attempts(task_text, cli, project_dir, log_dir, description="", **kwargs):
        attempt_count[0] += 1
        return _fail_result()

    with patch("mcloop.main.run_task", count_attempts):
        with patch("mcloop.main.notify"):
            run_loop(plan_md, max_retries=5, no_audit=True)

    assert attempt_count[0] == 5, (
        f"Expected exactly 5 attempts (max_retries=5), got {attempt_count[0]}"
    )


@pytest.mark.integration
def test_task_succeeds_on_final_retry(tmp_path):
    """Task fails on first two attempts, succeeds on third (the last allowed)."""
    plan_md = _setup_repo(tmp_path, "- [ ] Eventually works\n")

    attempt_count = [0]

    def fail_twice_then_succeed(task_text, cli, project_dir, log_dir, description="", **kwargs):
        attempt_count[0] += 1
        if attempt_count[0] < 3:
            return _fail_result()
        return _ok_result_with_file(task_text, Path(project_dir), Path(log_dir), "success.txt")

    with patch("mcloop.main.run_task", fail_twice_then_succeed):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=3, no_audit=True)

    assert stuck == [], f"Expected task to succeed on third attempt, got stuck: {stuck}"
    assert attempt_count[0] == 3

    content = plan_md.read_text()
    assert "- [x] Eventually works" in content, (
        f"Task should be checked off after succeeding on retry:\n{content}"
    )
    assert (tmp_path / "success.txt").exists()

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "Eventually works" in log.stdout
