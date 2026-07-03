"""Declared task acceptance dispatch tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from plan_fixtures import canonical_plan_text

from mcloop import git_ops
from mcloop.checks import CheckResult, acceptance_kind
from mcloop.main import ChainEntry, RunStatus, run_loop
from mcloop.runner import RunResult
from mcloop.waivers import load_waivers


def _scratch_project(name: str) -> Path:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    root = Path(".scratch") / "tests" / "declared-acceptance" / worker / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    return root


def _write_plan(root: Path, task_text: str) -> Path:
    plan = root / "PLAN.md"
    plan.write_text(canonical_plan_text(task_text))
    return plan


def _ok_result(root: Path) -> RunResult:
    log_path = root / "logs" / "agent.log"
    log_path.write_text("done\n")
    return RunResult(success=True, output="done", exit_code=0, log_path=log_path)


@contextmanager
def _loop_patches(root: Path, *, changed_files: list[str] | None = None) -> Iterator[None]:
    changed = changed_files or []
    with (
        patch("mcloop.main.notify"),
        patch("mcloop.main.validate_project_dependencies"),
        patch("mcloop.main.get_available_cli", return_value="test-cli"),
        # The startup chain preflight probes each tier via the real
        # runner._build_command, which rejects the synthetic "test-cli";
        # bypass the probe and accept the supplied chain verbatim.
        patch("mcloop.main._preflight_chain", side_effect=lambda chain, project_dir: chain),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._get_git_hash", return_value="base-sha"),
        patch("mcloop.main._has_meaningful_changes", return_value=bool(changed)),
        patch("mcloop.main._changed_files", return_value=list(changed)),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_task", return_value=_ok_result(root)),
    ):
        yield


def _run_one(plan: Path) -> RunStatus:
    return run_loop(
        plan,
        max_retries=1,
        no_audit=True,
        stop_after_one=True,
        chain=[ChainEntry(cli="test-cli", model=None)],
    )


def test_acceptance_kind_reads_runtime_annotations() -> None:
    from mcloop._planfile_compat import Task

    task = Task(
        text="Run proof [accept: command-exit: true]",
        checked=False,
        failed=False,
        line_number=0,
        indent_level=0,
        annotations=(("accept", "command-exit: true"),),
    )

    kind = acceptance_kind(task)

    assert kind is not None
    assert kind.kind == "command-exit"
    assert kind.command == "true"


def test_command_exit_acceptance_runs_without_shell_and_passes() -> None:
    root = _scratch_project("command-pass")
    plan = _write_plan(
        root,
        "- [ ] Narrative words are not a command [accept: command-exit: true]\n",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    real_run = subprocess.run

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return real_run(args, **kwargs)

    with (
        _loop_patches(root),
        patch("mcloop.main.run_autofix"),
        patch("mcloop.checks.subprocess.run", side_effect=fake_run),
        patch("mcloop.main.run_checks") as mock_checks,
        patch("mcloop.main.parse_auto_task") as mock_parse_auto,
    ):
        result = _run_one(plan)

    assert result.ok
    assert calls == [
        (
            ["true"],
            {
                "shell": False,
                "cwd": root,
                "capture_output": True,
                "text": True,
                "timeout": 300,
            },
        )
    ]
    mock_checks.assert_not_called()
    mock_parse_auto.assert_not_called()
    assert "Narrative words" not in calls[0][0]
    assert "- [x]" in plan.read_text()


def test_command_exit_acceptance_fails_on_nonzero_exit() -> None:
    root = _scratch_project("command-fail")
    plan = _write_plan(root, "- [ ] Check command [accept: command-exit: false]\n")
    calls: list[tuple[list[str], dict[str, object]]] = []
    real_run = subprocess.run

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return real_run(args, **kwargs)

    with (
        _loop_patches(root),
        patch("mcloop.main.run_autofix"),
        patch("mcloop.checks.subprocess.run", side_effect=fake_run),
    ):
        result = _run_one(plan)

    assert not result.ok
    assert calls[0][0] == ["false"]
    assert calls[0][1]["shell"] is False
    assert "- [!]" in plan.read_text()


def test_pytest_acceptance_routes_to_scoped_run_checks() -> None:
    root = _scratch_project("pytest")
    plan = _write_plan(root, "- [ ] Run scoped checks [accept: pytest]\n")

    with (
        _loop_patches(root, changed_files=["pkg/widget.py"]),
        patch("mcloop.main.run_autofix") as mock_autofix,
        patch(
            "mcloop.main.run_checks",
            return_value=CheckResult(passed=True, output="ok", command="pytest"),
        ) as mock_checks,
    ):
        result = _run_one(plan)

    assert result.ok
    mock_autofix.assert_called_once_with(root)
    mock_checks.assert_called_once_with(root, changed_files=["pkg/widget.py"])


def test_command_exit_acceptance_runs_autofix_before_the_check() -> None:
    """T-000034: format-on-exit is mcloop's job, not a model habit. A
    command-exit acceptance must run the auto-fixers before the
    acceptance command, like the pytest/coverage kinds already do,
    so the committed artifact does not depend on which model held
    the editor seat.
    """
    root = _scratch_project("command-autofix")
    plan = _write_plan(root, "- [ ] Run proof [accept: command-exit: true]\n")
    order: list[str] = []

    def fake_acceptance(*_args: object, **_kwargs: object) -> CheckResult:
        order.append("acceptance")
        return CheckResult(passed=True, output="ok", command="true")

    with (
        _loop_patches(root, changed_files=["pkg/widget.py"]),
        patch(
            "mcloop.main.run_autofix",
            side_effect=lambda _dir: order.append("autofix"),
        ),
        patch("mcloop.main.run_command_acceptance", side_effect=fake_acceptance),
    ):
        result = _run_one(plan)

    assert result.ok
    assert order == ["autofix", "acceptance"]


def test_waived_acceptance_runs_autofix() -> None:
    """T-000034: even a waived acceptance commits the editor's files,
    so the formatter must run before that commit too.
    """
    root = _scratch_project("waived-autofix")
    plan = _write_plan(
        root,
        "## Stage 1: Test\n"
        "<!-- phase_id: phase_001 -->\n\n"
        "- [ ] T-000001: Logical proof [accept: waived: reason; covered-by=T-000010]\n"
        "- [x] T-000010: Existing proof\n",
    )

    with (
        _loop_patches(root),
        patch("mcloop.main.run_autofix") as mock_autofix,
    ):
        result = _run_one(plan)

    assert result.ok
    mock_autofix.assert_called_once_with(root)


def _init_git_repo(root: Path) -> None:
    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "-A"],
        ["commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_misformatted_editor_output_still_commits_formatted(tmp_path: Path) -> None:
    """Regression for T-000034: a stubbed editor session that writes a
    deliberately misformatted file still yields a task commit that
    passes ``ruff format --check``, even when the task's acceptance
    (command-exit) never runs ruff itself.

    Uses tmp_path rather than _scratch_project: this test runs real git
    against the project directory, so it needs an absolute path outside
    the repo tree (the relative .scratch location shifts with cwd under
    xdist, and sandboxed environments can deny .git writes there).
    """
    if shutil.which("ruff") is None:
        pytest.skip("ruff not available on PATH")
    root = tmp_path / "format-on-exit"
    root.mkdir()
    (root / "logs").mkdir()
    plan = _write_plan(root, "- [ ] Write module [accept: command-exit: true]\n")
    (root / "pyproject.toml").write_text("[tool.ruff]\n")
    _init_git_repo(root)

    misformatted = "def add( a,b ):\n    return a+b\n"

    def messy_editor(*_args: object, **_kwargs: object) -> RunResult:
        log_path = root / "logs" / "agent.log"
        log_path.write_text("done\n")
        (root / "messy.py").write_text(misformatted)
        return RunResult(success=True, output="done", exit_code=0, log_path=log_path)

    with (
        _loop_patches(root, changed_files=["messy.py"]),
        patch("mcloop.main.run_task", side_effect=messy_editor),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        # conftest's autouse guard stubs _commit to "" for repo safety;
        # this project is a self-contained repo under tmp_path, so the
        # real commit is safe and is exactly what the regression pins.
        patch("mcloop.main._commit", git_ops._commit),
    ):
        result = _run_one(plan)

    assert result.ok
    committed = subprocess.run(
        ["git", "show", "HEAD:messy.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # The loop's formatter, not the editor model, produced the artifact.
    assert committed != misformatted
    fmt_check = subprocess.run(
        ["ruff", "format", "--check", "."],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert fmt_check.returncode == 0, fmt_check.stdout + fmt_check.stderr


def test_waived_acceptance_records_ledger_when_covered_by_resolves() -> None:
    root = _scratch_project("waived-resolves")
    plan = _write_plan(
        root,
        "## Stage 1: Test\n"
        "<!-- phase_id: phase_001 -->\n\n"
        "- [ ] T-000001: Logical proof [accept: waived: reason; covered-by=T-000010]\n"
        "- [x] T-000010: Existing proof\n",
    )

    with _loop_patches(root):
        result = _run_one(plan)

    assert result.ok
    records = load_waivers(root)
    assert len(records) == 1
    assert records[0]["task_label"] == "T-000001"
    assert records[0]["changed_input"] == "accept:T-000010"
    assert records[0]["baseline_sha"] == "base-sha"
    assert records[0]["reason"] == "reason"


def test_waived_acceptance_fails_closed_when_covered_by_is_missing() -> None:
    root = _scratch_project("waived-missing")
    plan = _write_plan(
        root,
        "- [ ] Logical proof [accept: waived: reason; covered-by=T-000010]\n",
    )

    with _loop_patches(root):
        result = _run_one(plan)

    assert not result.ok
    assert load_waivers(root) == []
    assert "- [!]" in plan.read_text()


def test_missing_acceptance_falls_back_to_inference_with_warning(capsys) -> None:
    root = _scratch_project("legacy-warning")
    plan = _write_plan(root, "- [ ] Capture baseline without modifying files.\n")

    with _loop_patches(root):
        result = _run_one(plan)

    assert result.ok
    assert "no declared acceptance" in capsys.readouterr().out
    assert "- [x]" in plan.read_text()
