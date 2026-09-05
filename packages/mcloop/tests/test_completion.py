"""Real Git and durable receipt boundaries, with model execution mocked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from bob_tools.json_state import StateError, read_json_object
from plan_fixtures import canonical_plan_text
from test_args import _make_batch_args, _run_loop_with_patches

from mcloop._planfile_compat import check_off, parse
from mcloop.completion import (
    Completion,
    RecoveryRequired,
    acknowledge,
    pending_receipts,
    project_owner,
    recovery_command,
    recovery_report,
    require_reconciled,
)
from mcloop.main import run_loop


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def project(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    git(tmp_path, "config", "user.name", "Completion test")
    plan = tmp_path / "PLAN.md"
    plan.write_text(canonical_plan_text("# Plan\n\n- [ ] Implement result\n"))
    (tmp_path / "result.txt").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def begin(root):
    plan = root / "PLAN.md"
    return Completion.begin(
        root,
        plan,
        parse(plan),
        command="oracle",
        output="passed",
        baseline=git(root, "rev-parse", "HEAD"),
    )


def test_success_preserves_receipt_without_dirtying_git(project):
    with project_owner(project):
        receipt = begin(project)
        (project / "result.txt").write_text("after\n")
        git(project, "add", ".")
        git(project, "commit", "-qm", "implementation")
        sha = git(project, "rev-parse", "HEAD")
        receipt.advance("commit_returned", commit_hash=sha)
        plan = project / "PLAN.md"
        check_off(plan, parse(plan)[0])
        receipt.advance("plan_updated", plan_after=plan.read_text())
        receipt.advance("settled", ledger="disabled")
    require_reconciled(project)
    assert pending_receipts(project) == []
    data = read_json_object(receipt.path)
    assert data["observations"][1]["commit_hash"] == sha
    assert data["plan_before"] != plan.read_text()
    assert ".mcloop" not in git(project, "ls-files")
    assert ".mcloop" not in git(project, "status", "--porcelain")


@pytest.mark.parametrize(
    "boundary", ["before_commit", "after_commit", "after_plan", "after_ledger"]
)
@pytest.mark.parametrize("declared", [False, True])
def test_real_loop_interruption_blocks_restart_without_replay(project, boundary, declared):
    """Every uncertain return window retains evidence; restart never calls a model."""
    plan = project / "PLAN.md"
    if declared:
        plan.write_text(
            canonical_plan_text(
                "# Plan\n\n- [ ] Implement result [accept: command-exit: oracle]\n"
            )
        )
    ledger = project / ".duplo" / "ledger"
    ledger.mkdir(parents=True)
    from bob_tools.ledger import Storage

    from mcloop.checks import CheckResult
    from mcloop.ledger_emit import emit_task_lifecycle_events

    storage = Storage(ledger, writer_id="test")
    original_head = git(project, "rev-parse", "HEAD")

    def commit(*args, **kwargs):
        if boundary == "before_commit":
            raise KeyboardInterrupt("before Git")
        (project / "result.txt").write_text("after\n")
        git(project, "add", "result.txt")
        git(project, "commit", "-qm", "implementation")
        if boundary == "after_commit":
            raise KeyboardInterrupt("Git landed before return")
        return git(project, "rev-parse", "HEAD")

    def emit(**kwargs):
        if boundary == "after_plan":
            raise KeyboardInterrupt("before append")
        events = emit_task_lifecycle_events(**kwargs)
        assert events
        raise KeyboardInterrupt("append landed before return")

    with (
        patch("mcloop.ledger_emit.open_mcloop_storage", return_value=storage),
        patch("mcloop.ledger_emit.emit_task_lifecycle_events", side_effect=emit),
        patch("mcloop.ledger_pause.evaluate_and_maybe_pause", return_value=None),
        patch(
            "mcloop.main.run_command_acceptance",
            return_value=CheckResult(True, "passed", "oracle"),
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        _run_loop_with_patches(
            plan,
            extra_patches={
                "mcloop.main._commit": commit,
                "mcloop.main._maybe_auto_wrap": lambda *_: None,
            },
            no_audit=True,
            stop_after_one=True,
        )

    receipt_path, receipt = pending_receipts(project)[0]
    original_bytes = receipt_path.read_bytes()
    report = recovery_report(project)
    assert report["pending"][0]["receipt"]["tasks"][0]["id"] == "T-000001"
    assert report["current_git"]["head"]["stdout"].strip() == git(project, "rev-parse", "HEAD")
    assert (git(project, "rev-parse", "HEAD") == original_head) == (boundary == "before_commit")
    assert parse(plan)[0].checked == (boundary in ("after_plan", "after_ledger"))
    assert len(storage.read_all()) == (1 if boundary == "after_ledger" else 0)
    with (
        patch("mcloop.main._preflight_chain") as preflight,
        patch("mcloop.main.run_task") as model,
    ):
        with pytest.raises(RecoveryRequired, match="mcloop recover"):
            run_loop(plan, retry=True)
        model.assert_not_called()
        preflight.assert_not_called()
    assert receipt_path.read_bytes() == original_bytes
    assert len(storage.read_all()) == (1 if boundary == "after_ledger" else 0)


@pytest.mark.parametrize("stage", ["verified", "commit_returned", "plan_updated"])
def test_sigkill_style_exit_retains_receipt_and_releases_owner(project, stage):
    """os._exit skips Python cleanup, as an unhandled process death would."""
    script = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
from mcloop.completion import Completion, project_owner
root=Path(sys.argv[1])
with project_owner(root):
    tasks=[SimpleNamespace(task_id='T-000001', text='task')]
    c=Completion.begin(root, root/'PLAN.md', tasks, command='check', output='passed')
    if sys.argv[2] != 'verified': c.advance('commit_returned', commit_hash='unknown')
    if sys.argv[2] == 'plan_updated': c.advance('plan_updated')
    os._exit(71)
"""
    result = subprocess.run([sys.executable, "-c", script, str(project), stage], timeout=15)
    assert result.returncode == 71
    with project_owner(project):
        with pytest.raises(RecoveryRequired):
            require_reconciled(project)
    assert pending_receipts(project)[0][1]["stage"] == stage


def test_failed_receipt_publication_prevents_git(project, monkeypatch):
    plan = project / "PLAN.md"
    commit = MagicMock()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("mcloop.completion.atomic_write_json", fail)
    with pytest.raises(OSError, match="disk full"):
        _run_loop_with_patches(plan, extra_patches={"mcloop.main._commit": commit}, no_audit=True)
    commit.assert_not_called()
    assert not parse(plan)[0].checked


@pytest.mark.parametrize("stage", ["commit_returned", "plan_updated", "settled"])
def test_receipt_replace_failure_preserves_previous_stage(project, monkeypatch, stage):
    c = begin(project)
    stages = ["verified", "commit_returned", "plan_updated", "settled"]
    for step in stages[1 : stages.index(stage)]:
        c.advance(step)
    old = c.path.read_bytes()

    def fail(*args):
        raise OSError("replace failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="replace failure"):
        c.advance(stage)
    assert c.path.read_bytes() == old
    with pytest.raises(RecoveryRequired):
        require_reconciled(project)


@pytest.mark.parametrize("bad", ["{", '{"schema_version": 99}', '{"schema_version": true}', "[]"])
def test_corrupt_receipt_refused_and_preserved(project, bad):
    c = begin(project)
    c.path.write_text(bad)
    with pytest.raises(StateError):
        require_reconciled(project)
    with pytest.raises(StateError):
        acknowledge(project, c.path.stem, "reviewed")
    assert c.path.read_text() == bad


def test_acknowledgement_preserves_evidence_without_replaying(project):
    c = begin(project)
    before = read_json_object(c.path)
    plan_before = (project / "PLAN.md").read_bytes()
    head = git(project, "rev-parse", "HEAD")
    with pytest.raises(RecoveryRequired):
        acknowledge(project, "../elsewhere", "reviewed")
    with pytest.raises(RecoveryRequired):
        acknowledge(project, c.path.stem, " ")
    acknowledge(
        project,
        c.path.stem,
        "Compared HEAD, plan and ledger; retained pending task for explicit retry.",
    )
    require_reconciled(project)
    after = read_json_object(c.path)
    for key in ("id", "plan_before", "tasks", "observations"):
        assert after[key] == before[key]
    assert after["stage"] == "acknowledged"
    assert after["resolution"]["evidence"]["pending"][0]["receipt"] == before
    assert (project / "PLAN.md").read_bytes() == plan_before
    assert git(project, "rev-parse", "HEAD") == head
    with pytest.raises(RecoveryRequired):
        acknowledge(project, c.path.stem, "again")


def test_recovery_read_only_and_owner_excludes_acknowledgement(project, capsys):
    c = begin(project)
    old = c.path.read_bytes()
    with project_owner(project):
        recovery_command(project, identity=None, reason=None)
        report = json.loads(capsys.readouterr().out)
        assert report["pending"][0]["receipt"]["id"] == c.path.stem
        with pytest.raises(RecoveryRequired, match="Another McLoop"):
            acknowledge(project, c.path.stem, "reviewed")
        with pytest.raises(RecoveryRequired, match="Another McLoop"):
            run_loop(project / "PLAN.md")
    assert c.path.read_bytes() == old


def test_batch_commit_error_is_not_a_retryable_editor_failure(tmp_path):
    from mcloop.checks import CheckResult
    from mcloop.main import _run_batch

    args = _make_batch_args(tmp_path)
    with (
        patch("mcloop.main.get_available_cli", return_value="claude"),
        patch(
            "mcloop.main.run_task",
            return_value=SimpleNamespace(success=True, output="done", exit_code=0),
        ),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._changed_files", return_value=["code.py"]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main.run_checks", return_value=CheckResult(True, "passed", "oracle")),
        patch("mcloop.main._commit", side_effect=RuntimeError("push failed after commit")),
        pytest.raises(RecoveryRequired, match="Batch commit outcome"),
    ):
        _run_batch(**args)
    pending = pending_receipts(args["project_dir"])
    assert len(pending) == 1
    assert len(pending[0][1]["tasks"]) == 2
    assert all(not t.checked for t in args["batch_children"])


def test_terminal_stage_without_observation_cannot_bypass_gate(project):
    c = begin(project)
    data = read_json_object(c.path)
    data["stage"] = "settled"
    c.path.write_text(json.dumps(data))
    old = c.path.read_bytes()
    with pytest.raises(StateError, match="matching observation"):
        require_reconciled(project)
    assert c.path.read_bytes() == old


def test_unreadable_receipt_directory_is_not_empty(project, monkeypatch):
    c = begin(project)
    original = type(c.path).iterdir

    def denied(path):
        if path == c.path.parent:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(type(c.path), "iterdir", denied)
    with pytest.raises(PermissionError, match="denied"):
        require_reconciled(project)


def test_successful_loop_links_ledger_event_to_receipt(project):
    from bob_tools.ledger import Storage

    ledger = project / ".duplo" / "ledger"
    ledger.mkdir(parents=True)
    storage = Storage(ledger, writer_id="test")

    def commit(*args):
        (project / "result.txt").write_text("after\n")
        git(project, "add", "result.txt")
        git(project, "commit", "-qm", "implementation")
        return git(project, "rev-parse", "HEAD")

    with (
        patch("mcloop.ledger_emit.open_mcloop_storage", return_value=storage),
        patch("mcloop.ledger_pause.evaluate_and_maybe_pause", return_value=None),
    ):
        status, _ = _run_loop_with_patches(
            project / "PLAN.md",
            extra_patches={
                "mcloop.main._commit": commit,
                "mcloop.main._maybe_auto_wrap": lambda *_: None,
            },
            no_audit=True,
            stop_after_one=True,
        )
    assert status.ok
    require_reconciled(project)
    receipts = list((project / ".mcloop" / "completions").glob("*.json"))
    data = read_json_object(receipts[0])
    events = storage.read_all()
    assert data["observations"][-1]["ledger_event_ids"] == [str(events[0].event_id)]
    assert data["observations"][-1]["ledger_run_id"] == events[0].run_id


def test_recover_cli_reports_without_provider_or_plan_changes(project):
    c = begin(project)
    old = c.path.read_bytes()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcloop",
            "--file",
            str(project / "PLAN.md"),
            "recover",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["pending"][0]["receipt"]["id"] == c.path.stem
    assert c.path.read_bytes() == old


def test_push_error_does_not_emit_false_failure_or_reauthor(project):
    from bob_tools.ledger import Storage

    ledger = project / ".duplo" / "ledger"
    ledger.mkdir(parents=True)
    storage = Storage(ledger, writer_id="test")

    def commit(*args):
        (project / "result.txt").write_text("after\n")
        git(project, "add", "result.txt")
        git(project, "commit", "-qm", "implementation")
        raise RuntimeError("push failed")

    with (
        patch("mcloop.ledger_emit.open_mcloop_storage", return_value=storage),
        patch("mcloop.ledger_pause.evaluate_and_maybe_pause", return_value=None) as evaluate,
        patch("mcloop.ledger_pause.auto_reauthor") as reauthor,
    ):
        status, _ = _run_loop_with_patches(
            project / "PLAN.md",
            extra_patches={
                "mcloop.main._commit": commit,
            },
            no_audit=True,
        )
    assert not status.ok
    assert evaluate.call_count == 1  # Startup only, never ambiguous failure settlement.
    reauthor.assert_not_called()
    assert storage.read_all() == []
    assert pending_receipts(project)[0][1]["error"] == "push failed"
    assert not parse(project / "PLAN.md")[0].checked
