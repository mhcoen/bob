import json
import subprocess
from unittest.mock import patch

import pytest
from bob_tools.json_state import StateError
from bob_tools.planfile import migrate, parse_plan, save

from mcloop.completion import project_owner
from mcloop.plan_revision import apply_revision, enforce_milestones, prepare_revision


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    path = tmp_path / "PLAN.md"
    text = (
        "<!-- bob-plan-format: 1 -->\n## Phase 1: Build\n"
        "- [x] T-000001: Completed\n- [ ] T-000002: Implement\n"
    )
    save(path, migrate(parse_plan(text)))
    candidate = tmp_path / ".mcloop/candidate.md"
    candidate.parent.mkdir(exist_ok=True)
    extra = (
        "- [ ] T-000003: [AUTO:run_cli] Run the app "
        "[milestone: scaffold] [demonstrates: input reaches output] "
        "[simulated: runtime until phase_002] [replaces: none] "
        "[accept: command-exit: python3 smoke.py]\n"
    )
    save(candidate, migrate(parse_plan(path.read_text() + extra)))
    return path, candidate


def test_prepare_does_not_edit_and_apply_preserves_completed_work(project):
    path, candidate = project
    original = path.read_bytes()
    receipt = prepare_revision(path, candidate)
    assert path.read_bytes() == original
    assert (receipt.parent / "changes.diff").exists()
    apply_revision(path, receipt)
    assert "[x] T-000001:" in path.read_text()
    assert "milestone: scaffold" in path.read_text()
    assert json.loads(receipt.read_text())["status"] == "applied"
    apply_revision(path, receipt)


@pytest.mark.parametrize(
    "target", ["PLAN.md", "SPEC.md", ".mcloop/config.json", ".duplo/ledger/events.jsonl"]
)
def test_apply_refuses_stale_project(project, target):
    path, candidate = project
    receipt = prepare_revision(path, candidate)
    changed = path.parent / target
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("Changed after preparation")
    before = path.read_bytes()
    with pytest.raises(StateError, match="changed"):
        apply_revision(path, receipt)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("Completed", "Reinterpreted"),
        lambda s: s.replace("[x]", "[ ]"),
        lambda s: s.replace("T-000001:", "T-000009:"),
    ],
)
def test_revision_cannot_rewrite_or_remove_completed_work(project, change):
    path, candidate = project
    candidate.write_text(change(candidate.read_text()))
    with pytest.raises(StateError):
        prepare_revision(path, candidate)


def test_busy_project_and_unresolved_completion_are_refused(project):
    path, candidate = project
    with project_owner(path.parent), pytest.raises(StateError, match="owns"):
        prepare_revision(path, candidate)
    with patch("mcloop.plan_revision.pending_receipts", return_value=[object()]):
        with pytest.raises(StateError, match="outstanding"):
            prepare_revision(path, candidate)


def test_interrupted_apply_can_resume_after_atomic_plan_write(project):
    from mcloop import plan_revision as module

    path, candidate = project
    receipt = prepare_revision(path, candidate)
    real = module.atomic_write_json

    def interrupt(path, record):
        if record.get("status") == "applied":
            raise OSError("interrupted receipt write")
        return real(path, record)

    with patch.object(module, "atomic_write_json", side_effect=interrupt):
        with pytest.raises(OSError):
            apply_revision(path, receipt)
    with pytest.raises(StateError, match="Interrupted plan revision"):
        enforce_milestones(path)
    apply_revision(path, receipt)
    enforce_milestones(path)


def test_failed_milestone_stops_before_later_implementation():
    from test_declared_acceptance import _loop_patches, _scratch_project, _write_plan

    from mcloop.main import ChainEntry, run_loop

    root = _scratch_project("failed-milestone")
    path = _write_plan(
        root,
        "## Phase 1: Scaffold\n"
        "- [ ] [AUTO:run_cli] Exercise app [milestone: scaffold] "
        "[demonstrates: visible output] [simulated: runtime until phase_002] "
        "[replaces: none] [accept: command-exit: python3 smoke.py]\n"
        "## Phase 2: Extend\n"
        "- [ ] Implement runtime [accept: command-exit: python3 tests.py]\n"
        "- [ ] [AUTO:run_cli] Exercise runtime [milestone: integration] "
        "[demonstrates: real recognition] [simulated: none] [replaces: runtime] "
        "[accept: command-exit: python3 smoke.py]\n",
    )
    with (
        _loop_patches(root),
        patch("mcloop.main.run_task") as editor,
        patch(
            "mcloop.main._handle_auto_task", return_value="ERROR: fixture outcome absent"
        ) as command,
    ):
        result = run_loop(
            path, max_retries=1, no_audit=True, chain=[ChainEntry(cli="test-cli", model=None)]
        )
    assert not result.ok
    command.assert_called_once()
    assert command.call_args.args[-1] == "python3 smoke.py"
    editor.assert_not_called()
    assert "[ ] T-000002:" in path.read_text()


def test_malformed_receipt_and_missing_candidate_report_state_errors(project):
    from mcloop.plan_revision import revision_command

    path, candidate = project
    receipt = prepare_revision(path, candidate)
    receipt.write_text('{"schema_version": 1, "status": "prepared"}')
    with pytest.raises(StateError, match="Incomplete"):
        apply_revision(path, receipt)
    with pytest.raises(StateError):
        revision_command(path, path.parent / "missing.md", None)
