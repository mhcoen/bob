"""Tests for duplo.status display and phase helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duplo.pipeline import _partition_features
from duplo.status import (
    UpdateSummary,
    _current_phase_content,
    _plan_has_unchecked_tasks,
    _plan_is_complete,
    _print_feature_status,
    _print_status,
    _print_summary,
)

_DUPLO_JSON = ".duplo/duplo.json"

_STUB_SPEC = (
    "# Stub\n"
    "\n"
    "## Purpose\n"
    "Stub spec used by tests. Long enough to pass validate_for_run purpose check.\n"
    "\n"
    "## Architecture\n"
    "Web app using React.\n"
)


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    """Prevent argparse from seeing pytest's CLI arguments."""
    monkeypatch.setattr("sys.argv", ["duplo"])
    monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)


def _write_duplo_json(tmp_path: Path, data: dict) -> None:
    """Write duplo.json into the .duplo/ subdirectory of *tmp_path*."""
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir(exist_ok=True)
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
    spec_path = tmp_path / "SPEC.md"
    if not spec_path.exists():
        spec_path.write_text(_STUB_SPEC, encoding="utf-8")


def _read_duplo_json(tmp_path: Path) -> dict:
    """Read duplo.json from the .duplo/ subdirectory of *tmp_path*."""
    return json.loads((tmp_path / _DUPLO_JSON).read_text())


class TestPrintSummary:
    """Tests for _print_summary()."""

    def test_no_changes(self, capsys):
        _print_summary(UpdateSummary())
        out = capsys.readouterr().out
        assert "No changes detected" in out

    def test_files_only(self, capsys):
        summary = UpdateSummary(files_added=2, files_changed=1, files_removed=0)
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "Update summary" in out
        assert "2 added" in out
        assert "1 changed" in out
        assert "No new tasks added" in out

    def test_full_summary(self, capsys):
        summary = UpdateSummary(
            files_added=1,
            images_analyzed=1,
            pages_rescraped=5,
            examples_rescraped=3,
            missing_features=2,
            design_refinements=1,
            tasks_appended=3,
        )
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "Update summary" in out
        assert "Images analyzed: 1" in out
        assert "Pages re-scraped: 5" in out
        assert "Code examples updated: 3" in out
        assert "Missing features: 2" in out
        assert "Design refinements: 1" in out
        assert "Tasks appended to PLAN.md: 3" in out

    def test_rescrape_only(self, capsys):
        summary = UpdateSummary(pages_rescraped=10)
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "Pages re-scraped: 10" in out
        assert "No new tasks added" in out

    def test_gaps_without_files(self, capsys):
        summary = UpdateSummary(
            missing_features=1,
            missing_examples=2,
            tasks_appended=3,
        )
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "Missing features: 1" in out
        assert "Missing examples: 2" in out
        assert "Tasks appended to PLAN.md: 3" in out

    def test_removed_files_shown(self, capsys):
        summary = UpdateSummary(files_removed=3)
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "3 removed" in out


class TestPartitionFeatures:
    """Tests for _partition_features helper."""

    def test_splits_by_status(self):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "Export",
                    "description": "Export",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Filter",
                    "description": "Filter",
                    "category": "core",
                    "status": "partial",
                    "implemented_in": "Phase 2",
                },
            ],
        }
        implemented, remaining = _partition_features(data)
        assert [f.name for f in implemented] == ["Search"]
        assert [f.name for f in remaining] == ["Export", "Filter"]

    def test_all_implemented(self):
        data = {
            "features": [
                {
                    "name": "A",
                    "description": "A",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        }
        implemented, remaining = _partition_features(data)
        assert len(implemented) == 1
        assert remaining == []

    def test_no_features(self):
        implemented, remaining = _partition_features({})
        assert implemented == []
        assert remaining == []

    def test_missing_status_defaults_to_remaining(self):
        data = {
            "features": [
                {"name": "A", "description": "A", "category": "core"},
            ],
        }
        implemented, remaining = _partition_features(data)
        assert implemented == []
        assert len(remaining) == 1

    def test_preserves_feature_fields(self):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Full-text search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        }
        implemented, _ = _partition_features(data)
        feat = implemented[0]
        assert feat.name == "Search"
        assert feat.description == "Full-text search"
        assert feat.category == "core"
        assert feat.status == "implemented"
        assert feat.implemented_in == "Phase 1"


class TestPrintFeatureStatus:
    """Tests for _print_feature_status display."""

    def test_prints_implemented_and_remaining(self, capsys):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "Export",
                    "description": "Export",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        }
        _print_feature_status(data)
        out = capsys.readouterr().out
        assert "1/2 implemented" in out
        assert "Search" in out
        assert "(Phase 1)" in out
        assert "Export" in out

    def test_no_output_when_no_features(self, capsys):
        _print_feature_status({})
        assert capsys.readouterr().out == ""

    def test_partial_shown_with_label(self, capsys):
        data = {
            "features": [
                {
                    "name": "Filter",
                    "description": "Filter",
                    "category": "core",
                    "status": "partial",
                    "implemented_in": "Phase 2",
                },
            ],
        }
        _print_feature_status(data)
        out = capsys.readouterr().out
        assert "0/1 implemented" in out
        assert "[partial]" in out

    def test_all_implemented(self, capsys):
        data = {
            "features": [
                {
                    "name": "A",
                    "description": "A",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "B",
                    "description": "B",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 2",
                },
            ],
        }
        _print_feature_status(data)
        out = capsys.readouterr().out
        assert "2/2 implemented" in out
        assert "Remaining" not in out


class TestPrintStatus:
    """Tests for _print_status display at start of subsequent runs."""

    def test_prints_phase_features_and_issues(self, capsys):
        data = {
            "app_name": "McWhisper",
            "phases": [{"phase": "Phase 1", "plan": "", "completed_at": ""}],
            "features": [
                {
                    "name": "Auth",
                    "description": "Login",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "Search",
                    "description": "Find",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Export",
                    "description": "Export",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
            "issues": [
                {"description": "crash on load", "status": "open"},
                {"description": "fixed typo", "status": "resolved"},
                {"description": "slow render", "status": "open"},
            ],
        }
        _print_status(data)
        out = capsys.readouterr().out
        assert "McWhisper: Phase 1 complete. 1/3 features implemented, 2 open issues." in out

    def test_empty_data(self, capsys):
        _print_status({})
        out = capsys.readouterr().out
        assert "Ready to generate Phase 1. 0/0 features implemented." in out

    def test_no_phases_plan_exists(self, capsys):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Find",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        }
        _print_status(data, plan_exists=True)
        out = capsys.readouterr().out
        assert "Phase 1 in progress. 0/1 features implemented." in out

    def test_no_phases_no_plan(self, capsys):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Find",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        }
        _print_status(data)
        out = capsys.readouterr().out
        assert "Ready to generate Phase 1. 0/1 features implemented." in out

    def test_issues_without_status_field_count_as_open(self, capsys):
        data = {
            "issues": [
                {"description": "old bug"},
            ],
        }
        _print_status(data)
        out = capsys.readouterr().out
        assert "1 open issues" in out

    def test_no_app_name_omits_prefix(self, capsys):
        data = {
            "phases": [{"phase": "Phase 1", "plan": "", "completed_at": ""}],
            "features": [
                {
                    "name": "Auth",
                    "description": "Login",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        }
        _print_status(data)
        out = capsys.readouterr().out
        assert out.strip().startswith("Phase 1 complete")
        assert "McWhisper" not in out


class TestPlanHasUncheckedTasks:
    """Tests for _plan_has_unchecked_tasks helper."""

    def test_returns_false_when_no_plan(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _plan_has_unchecked_tasks() is False

    def test_returns_true_with_unchecked_tasks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "PLAN.md").write_text("- [ ] Task\n")
        assert _plan_has_unchecked_tasks() is True

    def test_returns_false_when_all_checked(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "PLAN.md").write_text("- [x] Done\n")
        assert _plan_has_unchecked_tasks() is False

    def test_returns_true_with_mixed_tasks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "PLAN.md").write_text("- [x] Done\n- [ ] Pending\n")
        assert _plan_has_unchecked_tasks() is True

    def test_returns_true_with_failed_task(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "PLAN.md").write_text("- [!] Failed\n")
        assert _plan_has_unchecked_tasks() is True

    def test_returns_false_with_empty_plan(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "PLAN.md").write_text("# Plan\nNo tasks here.\n")
        assert _plan_has_unchecked_tasks() is False


# -- Multi-phase helpers ---------------------------------------------------


_MULTI_PHASE_PLAN = """\
# MyApp — Phase 1: Setup

- [x] Create project structure
- [x] Add build system

# MyApp — Phase 2: Features

- [ ] Add search
- [ ] Add filters
"""


def _write_phase_data(tmp_path: Path, current_phase: int) -> None:
    """Write a minimal duplo.json with the given current_phase."""
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir(exist_ok=True)
    data = {
        "current_phase": current_phase,
        "roadmap": [
            {"phase": 1, "title": "Setup"},
            {"phase": 2, "title": "Features"},
        ],
    }
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")


class TestCurrentPhaseContent:
    """Tests for _current_phase_content helper."""

    def test_extracts_phase_1_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 1)
        result = _current_phase_content(_MULTI_PHASE_PLAN)
        assert "Phase 1: Setup" in result
        assert "Create project structure" in result
        assert "Phase 2" not in result
        assert "Add search" not in result

    def test_extracts_phase_2_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 2)
        result = _current_phase_content(_MULTI_PHASE_PLAN)
        assert "Phase 2: Features" in result
        assert "Add search" in result
        assert "Phase 1" not in result
        assert "Create project structure" not in result

    def test_returns_full_content_when_no_duplo_json(self, tmp_path, monkeypatch):
        """phase_num == 0 when no duplo.json → returns full content."""
        monkeypatch.chdir(tmp_path)
        result = _current_phase_content(_MULTI_PHASE_PLAN)
        assert result == _MULTI_PHASE_PLAN

    def test_returns_full_content_when_heading_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 99)
        result = _current_phase_content(_MULTI_PHASE_PLAN)
        assert result == _MULTI_PHASE_PLAN

    def test_last_phase_extends_to_eof(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        content = "# App — Phase 3: Final\n\n- [ ] Deploy\n- [ ] Monitor\n"
        _write_phase_data(tmp_path, 3)
        # Override roadmap to include phase 3
        data = json.loads((tmp_path / ".duplo" / "duplo.json").read_text())
        data["roadmap"].append({"phase": 3, "title": "Final"})
        (tmp_path / ".duplo" / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        result = _current_phase_content(content)
        assert "Deploy" in result
        assert "Monitor" in result


class TestCurrentPhaseContentStage:
    """_current_phase_content accepts Stage headings."""

    def test_extracts_stage_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 1)
        content = (
            "# MyApp — Stage 1: Setup\n\n"
            "- [x] Create project\n\n"
            "# MyApp — Stage 2: Features\n\n"
            "- [ ] Add search\n"
        )
        result = _current_phase_content(content)
        assert "Stage 1: Setup" in result
        assert "Create project" in result
        assert "Stage 2" not in result

    def test_extracts_stage_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 2)
        content = (
            "# MyApp — Stage 1: Setup\n\n"
            "- [x] Create project\n\n"
            "# MyApp — Stage 2: Features\n\n"
            "- [ ] Add search\n"
        )
        result = _current_phase_content(content)
        assert "Stage 2: Features" in result
        assert "Add search" in result
        assert "Stage 1" not in result


class TestPlanIsCompleteMultiPhase:
    """_plan_is_complete must scope to the current phase only."""

    def test_complete_phase1_incomplete_phase2(self, tmp_path, monkeypatch):
        """Phase 1 all checked, Phase 2 has unchecked → complete for phase 1."""
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 1)
        (tmp_path / "PLAN.md").write_text(_MULTI_PHASE_PLAN)
        assert _plan_is_complete() is True

    def test_incomplete_phase2(self, tmp_path, monkeypatch):
        """Phase 2 has unchecked tasks → not complete for phase 2."""
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 2)
        (tmp_path / "PLAN.md").write_text(_MULTI_PHASE_PLAN)
        assert _plan_is_complete() is False

    def test_has_unchecked_scoped_to_phase1(self, tmp_path, monkeypatch):
        """Phase 1 all checked → no unchecked tasks for phase 1."""
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 1)
        (tmp_path / "PLAN.md").write_text(_MULTI_PHASE_PLAN)
        assert _plan_has_unchecked_tasks() is False

    def test_has_unchecked_scoped_to_phase2(self, tmp_path, monkeypatch):
        """Phase 2 has unchecked tasks → True for phase 2."""
        monkeypatch.chdir(tmp_path)
        _write_phase_data(tmp_path, 2)
        (tmp_path / "PLAN.md").write_text(_MULTI_PHASE_PLAN)
        assert _plan_has_unchecked_tasks() is True
