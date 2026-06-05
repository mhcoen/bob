"""Declared task acceptance dispatch tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from plan_fixtures import canonical_plan_text

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
