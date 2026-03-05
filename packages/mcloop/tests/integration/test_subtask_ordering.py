"""Integration test: subtask ordering — depth-first execution and parent auto-checking."""

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


def _make_fake_run_task(execution_order: list[str]) -> object:
    """Return a fake run_task that records execution order and creates a unique file per task."""
    counter = [0]

    def fake_run_task(task_text, cli, project_dir, log_dir, description="", **kwargs):
        project_dir = Path(project_dir)
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        execution_order.append(task_text)

        counter[0] += 1
        output_file = project_dir / f"task_{counter[0]}.txt"
        output_file.write_text(f"done: {task_text}\n")
        subprocess.run(
            ["git", "add", output_file.name],
            cwd=project_dir,
            capture_output=True,
        )

        log_path = log_dir / f"task_{counter[0]}.log"
        log_path.write_text(f"task: {task_text}\n")
        return RunResult(success=True, output="done", exit_code=0, log_path=log_path)

    return fake_run_task


@pytest.mark.integration
def test_children_execute_before_parent(tmp_path):
    """Children are executed depth-first; parent is auto-checked without calling run_task."""
    plan_content = (
        "- [ ] Parent task\n"
        "  - [ ] Child A\n"
        "  - [ ] Child B\n"
    )
    plan_md = _setup_repo(tmp_path, plan_content)

    execution_order: list[str] = []
    fake_run_task = _make_fake_run_task(execution_order)

    with patch("mcloop.main.run_task", fake_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == [], f"Expected no stuck tasks, got: {stuck}"

    # Children must run; parent must NOT be passed to run_task (it's auto-checked)
    assert execution_order == ["Child A", "Child B"], (
        f"Expected depth-first child execution, got: {execution_order}"
    )

    content = plan_md.read_text()
    assert "- [x] Parent task" in content
    assert "- [x] Child A" in content
    assert "- [x] Child B" in content


@pytest.mark.integration
def test_parent_auto_checked_after_all_children_complete(tmp_path):
    """Parent is auto-checked on disk when all its children are done, without being run."""
    plan_content = (
        "- [ ] Feature\n"
        "  - [ ] Step 1\n"
        "  - [ ] Step 2\n"
        "  - [ ] Step 3\n"
    )
    plan_md = _setup_repo(tmp_path, plan_content)

    execution_order: list[str] = []
    fake_run_task = _make_fake_run_task(execution_order)

    with patch("mcloop.main.run_task", fake_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    assert "Feature" not in execution_order, "Parent should not be passed to run_task"
    assert execution_order == ["Step 1", "Step 2", "Step 3"]

    content = plan_md.read_text()
    assert content.count("- [x]") == 4  # all three children + parent


@pytest.mark.integration
def test_deep_nesting_runs_depth_first(tmp_path):
    """Grandchildren execute before children, children before parents."""
    plan_content = (
        "- [ ] Grandparent\n"
        "  - [ ] Parent\n"
        "    - [ ] Grandchild\n"
    )
    plan_md = _setup_repo(tmp_path, plan_content)

    execution_order: list[str] = []
    fake_run_task = _make_fake_run_task(execution_order)

    with patch("mcloop.main.run_task", fake_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    assert execution_order == ["Grandchild"], (
        f"Only the leaf should be executed; got: {execution_order}"
    )

    content = plan_md.read_text()
    assert "- [x] Grandparent" in content
    assert "- [x] Parent" in content
    assert "- [x] Grandchild" in content


@pytest.mark.integration
def test_mixed_parent_and_leaf_tasks_ordered_correctly(tmp_path):
    """A top-level leaf before a parent-with-children runs before its children."""
    plan_content = (
        "- [ ] Standalone task\n"
        "- [ ] Group\n"
        "  - [ ] Sub A\n"
        "  - [ ] Sub B\n"
    )
    plan_md = _setup_repo(tmp_path, plan_content)

    execution_order: list[str] = []
    fake_run_task = _make_fake_run_task(execution_order)

    with patch("mcloop.main.run_task", fake_run_task):
        with patch("mcloop.main.notify"):
            stuck = run_loop(plan_md, max_retries=1, no_audit=True)

    assert stuck == []
    assert execution_order == ["Standalone task", "Sub A", "Sub B"], (
        f"Expected standalone then children, got: {execution_order}"
    )

    content = plan_md.read_text()
    assert "- [x] Standalone task" in content
    assert "- [x] Group" in content
    assert "- [x] Sub A" in content
    assert "- [x] Sub B" in content
