"""Tests for mcloop.ledger_emit (Plan Ledger Slice D emission)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

try:
    import bob_tools.ledger  # noqa: F401
    _BOB_TOOLS_AVAILABLE = True
except ImportError:
    _BOB_TOOLS_AVAILABLE = False

needs_bob_tools = pytest.mark.skipif(
    not _BOB_TOOLS_AVAILABLE,
    reason="ledger_emit tests require the 'bob_tools' package",
)

from mcloop.ledger_emit import (  # noqa: E402  (must follow try/except guard above)
    PhaseIdResolution,
    TaskOutcome,
    find_explicit_phase_id_for_task,
    parse_plan_phase_ids,
    resolve_phase_id,
)

# ---------------------------------------------------------------------
# parse_plan_phase_ids
# ---------------------------------------------------------------------


class TestParsePlanPhaseIds:
    def test_single_phase(self) -> None:
        text = "## Phase phase_001: Bring up scaffold\n"
        assert parse_plan_phase_ids(text) == ["phase_001"]

    def test_multiple_phases_in_order(self) -> None:
        text = (
            "## Phase phase_001: A\n\n"
            "## Phase phase_002: B\n\n"
            "## Phase phase_003: C\n"
        )
        assert parse_plan_phase_ids(text) == [
            "phase_001",
            "phase_002",
            "phase_003",
        ]

    def test_pre_slice_c_headers_ignored(self) -> None:
        text = "# Phase 1: Stopwatch\n\n## Other section\n"
        assert parse_plan_phase_ids(text) == []

    def test_empty_string(self) -> None:
        assert parse_plan_phase_ids("") == []


# ---------------------------------------------------------------------
# find_explicit_phase_id_for_task
# ---------------------------------------------------------------------


class TestFindExplicitPhaseIdForTask:
    def test_task_under_first_phase(self) -> None:
        text = (
            "## Phase phase_001: Setup\n\n"
            "- [ ] task-001: Bring up scaffold\n\n"
            "## Phase phase_002: Persistence\n\n"
            "- [ ] task-002: Add localStorage\n"
        )
        assert find_explicit_phase_id_for_task(text, "task-001") == "phase_001"

    def test_task_under_second_phase(self) -> None:
        text = (
            "## Phase phase_001: Setup\n\n"
            "- [ ] task-001: Bring up scaffold\n\n"
            "## Phase phase_002: Persistence\n\n"
            "- [ ] task-002: Add localStorage\n"
        )
        assert find_explicit_phase_id_for_task(text, "task-002") == "phase_002"

    def test_task_not_found(self) -> None:
        text = "## Phase phase_001: Setup\n\n- [ ] task-001\n"
        assert find_explicit_phase_id_for_task(text, "task-999") is None

    def test_no_phase_header_returns_none(self) -> None:
        text = "# Phase 1: Setup\n\n- [ ] task-001\n"
        assert find_explicit_phase_id_for_task(text, "task-001") is None

    def test_inline_phase_id_comment(self) -> None:
        # The synthesizer's primary mechanism is header-prefixed
        # phase_id; an inline comment is also accepted as the
        # current-phase signal.
        text = (
            "## Phase phase_001: Setup\n\n"
            "<!-- phase_id: phase_001b -->\n"
            "- [ ] task-001\n"
        )
        assert find_explicit_phase_id_for_task(text, "task-001") == "phase_001b"

    def test_empty_label_returns_none(self) -> None:
        text = "## Phase phase_001: Setup\n\n- [ ] task-001\n"
        assert find_explicit_phase_id_for_task(text, "") is None


# ---------------------------------------------------------------------
# resolve_phase_id
# ---------------------------------------------------------------------


class TestResolvePhaseId:
    def _write_plan(
        self, tmp_path: Path, phases: list[tuple[str, str]]
    ) -> Path:
        body_lines: list[str] = []
        for pid, label in phases:
            body_lines.append(f"## Phase {pid}: {pid} title")
            body_lines.append("")
            body_lines.append(f"- [ ] {label}: do the thing")
            body_lines.append("")
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("\n".join(body_lines), encoding="utf-8")
        return plan_path

    def test_explicit_resolution(self, tmp_path: Path) -> None:
        plan_path = self._write_plan(
            tmp_path, [("phase_001", "task-001"), ("phase_002", "task-002")]
        )
        result = resolve_phase_id(
            plan_path=plan_path, task_label="task-002", ordinal_index=99
        )
        assert result.phase_id == "phase_002"
        assert result.source == "explicit"
        assert result.plan_phase_count == 2

    def test_ordinal_fallback(self, tmp_path: Path) -> None:
        plan_path = self._write_plan(
            tmp_path, [("phase_001", "task-001"), ("phase_002", "task-002")]
        )
        result = resolve_phase_id(
            plan_path=plan_path, task_label="task-999", ordinal_index=1
        )
        assert result.phase_id == "phase_002"
        assert result.source == "ordinal"

    def test_ordinal_out_of_range(self, tmp_path: Path) -> None:
        plan_path = self._write_plan(tmp_path, [("phase_001", "task-001")])
        result = resolve_phase_id(
            plan_path=plan_path, task_label="task-999", ordinal_index=5
        )
        assert result.phase_id is None
        assert result.source == "none"

    def test_no_plan_file(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "DOES_NOT_EXIST.md"
        result = resolve_phase_id(
            plan_path=plan_path, task_label="task-001", ordinal_index=0
        )
        assert result.phase_id is None
        assert result.source == "none"
        assert result.plan_phase_count == 0

    def test_pre_slice_c_plan_falls_through_to_none(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# Old-style plan\n\n- [ ] task-001\n")
        result = resolve_phase_id(
            plan_path=plan_path, task_label="task-001", ordinal_index=0
        )
        assert result.phase_id is None
        assert result.source == "none"


# ---------------------------------------------------------------------
# Integration: full emit/storage round-trip
# ---------------------------------------------------------------------


@needs_bob_tools
class TestEmitTaskLifecycleEvents:
    def _git_init(self, project_dir: Path) -> None:
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(project_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(project_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(project_dir),
            check=True,
            capture_output=True,
        )

    def _seed_commit(self, project_dir: Path, paths: list[str]) -> None:
        for relpath in paths:
            full = project_dir / relpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("payload\n")
        subprocess.run(
            ["git", "add", *paths],
            cwd=str(project_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "seeded by test"],
            cwd=str(project_dir),
            check=True,
            capture_output=True,
        )

    def _open_storage(self, ledger_dir: Path) -> Any:
        from bob_tools.ledger import Storage

        return Storage(ledger_dir, writer_id="mcloop-test")

    def test_success_emits_commit_landed(self, tmp_path: Path) -> None:
        from bob_tools.ledger import EventType

        from mcloop.ledger_emit import emit_task_lifecycle_events

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        ledger_dir = tmp_path / "ledger"
        self._git_init(project_dir)
        self._seed_commit(project_dir, ["src/foo.py"])

        storage = self._open_storage(ledger_dir)
        outcome = TaskOutcome(
            success=True,
            abandoned=False,
            summary="implemented foo",
            changed_files=("src/foo.py",),
        )
        ids = emit_task_lifecycle_events(
            storage=storage,
            task_label="task-001",
            phase_id="phase_001",
            outcome=outcome,
            project_dir=project_dir,
            run_id="test-run-001",
        )
        assert len(ids) == 1
        events = storage.read_all()
        committed = [e for e in events if e.type is EventType.COMMIT_LANDED]
        assert len(committed) == 1
        payload = committed[0].payload
        assert payload["attributed_phase_id"] == "phase_001"
        assert payload["change_class"] == "code"
        assert payload["touched_paths"] == ["src/foo.py"]

    def test_failure_emits_test_failed(self, tmp_path: Path) -> None:
        from bob_tools.ledger import EventType

        from mcloop.ledger_emit import emit_task_lifecycle_events

        ledger_dir = tmp_path / "ledger"
        storage = self._open_storage(ledger_dir)
        outcome = TaskOutcome(
            success=False,
            abandoned=False,
            summary="pytest failed: 3 errors",
            changed_files=(),
            failure_kind="pytest",
        )
        ids = emit_task_lifecycle_events(
            storage=storage,
            task_label="task-007",
            phase_id="phase_002",
            outcome=outcome,
            project_dir=tmp_path,
            run_id="test-run-002",
        )
        assert len(ids) == 1
        events = storage.read_all()
        failed = [e for e in events if e.type is EventType.TEST_FAILED]
        assert len(failed) == 1
        assert failed[0].payload["test_id"] == "task-007"
        assert failed[0].payload["phase_id"] == "phase_002"
        assert failed[0].payload["failure_kind"] == "pytest"

    def test_abandoned_with_phase_id_emits_phase_abandoned(
        self, tmp_path: Path
    ) -> None:
        from bob_tools.ledger import EventType

        from mcloop.ledger_emit import emit_task_lifecycle_events

        ledger_dir = tmp_path / "ledger"
        storage = self._open_storage(ledger_dir)
        outcome = TaskOutcome(
            success=False,
            abandoned=True,
            summary="max retries exceeded",
            changed_files=(),
        )
        ids = emit_task_lifecycle_events(
            storage=storage,
            task_label="task-009",
            phase_id="phase_003",
            outcome=outcome,
            project_dir=tmp_path,
            run_id="test-run-003",
        )
        assert len(ids) == 1
        events = storage.read_all()
        abandoned = [e for e in events if e.type is EventType.PHASE_ABANDONED]
        assert len(abandoned) == 1
        assert abandoned[0].payload["phase_id"] == "phase_003"
        assert "max retries" in abandoned[0].payload["reason"]

    def test_abandoned_without_phase_id_emits_finding_observed(
        self, tmp_path: Path
    ) -> None:
        from bob_tools.ledger import EventType

        from mcloop.ledger_emit import emit_task_lifecycle_events

        ledger_dir = tmp_path / "ledger"
        storage = self._open_storage(ledger_dir)
        outcome = TaskOutcome(
            success=False,
            abandoned=True,
            summary="max retries exceeded",
            changed_files=(),
        )
        ids = emit_task_lifecycle_events(
            storage=storage,
            task_label="bug-foo",
            phase_id=None,
            outcome=outcome,
            project_dir=tmp_path,
            run_id="test-run-004",
        )
        assert len(ids) == 1
        events = storage.read_all()
        findings = [e for e in events if e.type is EventType.FINDING_OBSERVED]
        assert len(findings) == 1
        assert "task_abandoned" in findings[0].payload["tags"]


# ---------------------------------------------------------------------
# record_phase_id_fallback
# ---------------------------------------------------------------------


@needs_bob_tools
class TestRecordPhaseIdFallback:
    def test_records_finding_on_ordinal_resolution(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from bob_tools.ledger import EventType, Storage

        from mcloop.ledger_emit import record_phase_id_fallback

        ledger_dir = tmp_path / "ledger"
        storage = Storage(ledger_dir, writer_id="mcloop-test")
        resolution = PhaseIdResolution(
            phase_id="phase_002",
            source="ordinal",
            plan_phase_count=3,
        )
        ev_id = record_phase_id_fallback(
            storage=storage,
            task_label="task-007",
            resolution=resolution,
            run_id="test-run-fb",
        )
        assert ev_id is not None
        events = storage.read_all()
        findings = [e for e in events if e.type is EventType.FINDING_OBSERVED]
        assert len(findings) == 1
        assert "phase_id_fallback" in findings[0].payload["tags"]
        captured = capsys.readouterr()
        assert "fell back to ordinal" in captured.err

    def test_no_op_on_explicit_resolution(self, tmp_path: Path) -> None:
        from bob_tools.ledger import Storage

        from mcloop.ledger_emit import record_phase_id_fallback

        ledger_dir = tmp_path / "ledger"
        storage = Storage(ledger_dir, writer_id="mcloop-test")
        resolution = PhaseIdResolution(
            phase_id="phase_001", source="explicit", plan_phase_count=1
        )
        ev_id = record_phase_id_fallback(
            storage=storage,
            task_label="task-001",
            resolution=resolution,
            run_id="test-run-fb",
        )
        assert ev_id is None
        assert storage.read_all() == []
