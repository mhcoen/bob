"""Recover a whole execution, including work before verification/checkpoints."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from test_args import _run_loop_with_patches
from test_completion import git
from test_completion import project as project

from mcloop.completion import (
    RecoveryRequired,
    acknowledge,
    guarded_loop,
    pending_receipts,
    require_reconciled,
    run_owned_command,
    start_execution,
)
from mcloop.git_ops import _checkpoint
from mcloop.main import run_loop


@pytest.mark.parametrize("commits", [False, True])
def test_editor_interruption_retains_baseline_and_never_restarts(project, commits):
    before = git(project, "rev-parse", "HEAD")

    def editor(*args, **kwargs):
        (project / "result.txt").write_text("unfinished work\n")
        if commits:
            git(project, "add", "result.txt")
            git(project, "commit", "-qm", "editor checkpoint")
        raise KeyboardInterrupt("editor interrupted")

    with pytest.raises(KeyboardInterrupt):
        _run_loop_with_patches(project / "PLAN.md", extra_patches={"mcloop.main.run_task": editor})
    path, data = pending_receipts(project)[0]
    assert data["schema_version"] == 2
    assert data["observations"][0]["head"]["stdout"].strip() == before
    assert data["observations"][-1]["stage"] == "attempt_started"
    assert data["observations"][-1]["task_id"] == "T-000001"
    current = path.read_bytes()
    with patch("mcloop.main.run_task") as model, patch("mcloop.main._checkpoint") as checkpoint:
        with pytest.raises(RecoveryRequired):
            run_loop(project / "PLAN.md", retry=True)
        model.assert_not_called()
        checkpoint.assert_not_called()
    assert path.read_bytes() == current
    acknowledge(
        project, data["id"], "Inspected editor work and Git; retained task for explicit retry."
    )
    require_reconciled(project)
    assert (project / "result.txt").read_text() == "unfinished work\n"


@pytest.mark.parametrize("where", ["before", "after"])
def test_checkpoint_interruption_retains_execution_and_git(project, where):
    real_run = subprocess.run

    def interrupt_git(args, **kwargs):
        if args[:2] == ["git", "commit"]:
            if where == "before":
                raise KeyboardInterrupt("before checkpoint")
            result = real_run(args, **kwargs)
            assert result.returncode == 0
            raise KeyboardInterrupt("after checkpoint")
        return real_run(args, **kwargs)

    before = git(project, "rev-parse", "HEAD")
    (project / "result.txt").write_text("checkpoint work\n")

    @guarded_loop
    def operation(plan):
        start_execution(plan)
        _checkpoint(project)

    with (
        patch("mcloop.git_ops.subprocess.run", side_effect=interrupt_git),
        pytest.raises(KeyboardInterrupt),
    ):
        operation(project / "PLAN.md")
    data = pending_receipts(project)[0][1]
    assert data["observations"][-1]["stage"] == "checkpoint_started"
    assert (git(project, "rev-parse", "HEAD") == before) == (where == "before")
    with pytest.raises(RecoveryRequired):
        operation(project / "PLAN.md")


@pytest.mark.parametrize("kind", ["status", "staging", "commit"])
def test_checkpoint_errors_are_not_silently_ignored(project, kind):
    (project / "result.txt").write_text("changed\n")
    real_run = subprocess.run

    def fail(args, **kwargs):
        if args[:2] == ["git", {"status": "status", "staging": "add", "commit": "commit"}[kind]]:
            return subprocess.CompletedProcess(args, 1, "", "injected failure")
        return real_run(args, **kwargs)

    with patch("mcloop.git_ops.subprocess.run", side_effect=fail), patch("mcloop.git_ops.notify"):
        with pytest.raises(RuntimeError, match="failed"):
            _checkpoint(project)
    assert (project / "result.txt").read_text() == "changed\n"


def test_checkpoint_only_sensitive_untracked_files_is_valid_noop(project):
    (project / ".env").write_text("placeholder")
    before = git(project, "rev-parse", "HEAD")
    _checkpoint(project)
    assert git(project, "rev-parse", "HEAD") == before
    assert ".env" not in git(project, "ls-files")


def test_exit_without_cleanup_blocks_future_run(project):
    script = """
import os, sys
from pathlib import Path
from mcloop.completion import guarded_loop, start_execution
@guarded_loop
def run(plan):
    start_execution(plan)
    (plan.parent/'result.txt').write_text('interrupted')
    os._exit(73)
run(Path(sys.argv[1]))
"""
    proc = subprocess.run([sys.executable, "-c", script, str(project / "PLAN.md")], timeout=15)
    assert proc.returncode == 73
    with pytest.raises(RecoveryRequired):
        run_loop(project / "PLAN.md")


def test_normal_failure_records_return_without_certifying_completion(project):
    result, _ = _run_loop_with_patches(
        project / "PLAN.md",
        extra_patches={
            "mcloop.main.run_task": MagicMock(success=False, output="failed", exit_code=1),
        },
        max_retries=1,
    )
    assert not result.ok
    require_reconciled(project)
    records = list((project / ".mcloop" / "completions").glob("*.json"))
    data = json.loads(records[0].read_text())
    assert data["stage"] == "returned"
    assert data["observations"][-1]["outcome"] == "failure"


@pytest.mark.parametrize("command_name", ["audit", "maintain", "investigate"])
def test_command_execution_guard_preserves_interruption(project, command_name):
    def command():
        (project / "result.txt").write_text(command_name)
        raise KeyboardInterrupt()

    command.__name__ = command_name
    with pytest.raises(KeyboardInterrupt):
        run_owned_command(project / "PLAN.md", command)
    model = MagicMock()
    with pytest.raises(RecoveryRequired):
        run_owned_command(project / "PLAN.md", model)
    model.assert_not_called()
    assert pending_receipts(project)[0][1]["observations"][-1]["command"] == command_name


@pytest.mark.parametrize("command_name", ["audit", "maintain", "investigate"])
def test_cli_dispatch_checks_receipts_before_command(project, command_name):
    import mcloop.main as main

    @guarded_loop
    def interrupted(plan):
        start_execution(plan)
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        interrupted(project / "PLAN.md")
    with (
        patch("sys.argv", ["mcloop", "--file", str(project / "PLAN.md"), command_name]),
        patch.object(main, f"_cmd_{command_name}") as command,
        pytest.raises(RecoveryRequired),
    ):
        main._main()
    command.assert_not_called()
