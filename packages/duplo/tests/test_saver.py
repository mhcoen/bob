"""Tests for duplo.saver."""

from __future__ import annotations

import json

import pytest

from duplo.extractor import Feature
from duplo.questioner import BuildPreferences
from duplo.saver import (
    CLAUDE_MD,
    DUPLO_JSON,
    append_phase_to_history,
    clear_in_progress,
    save_feedback,
    save_screenshot_feature_map,
    save_selections,
    set_in_progress,
    write_claude_md,
)


@pytest.fixture()
def sample_features() -> list[Feature]:
    return [
        Feature(name="Search", description="Full-text search.", category="core"),
        Feature(name="REST API", description="CRUD via JSON API.", category="api"),
    ]


@pytest.fixture()
def sample_prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="web",
        language="Python/FastAPI",
        constraints=["Postgres only"],
        preferences=["pytest for tests"],
    )


class TestSaveSelections:
    def test_creates_file(self, tmp_path, sample_features, sample_prefs):
        path = save_selections(
            "https://example.com", sample_features, sample_prefs, target_dir=tmp_path
        )
        assert path.exists()
        assert path.name == DUPLO_JSON

    def test_returns_correct_path(self, tmp_path, sample_features, sample_prefs):
        path = save_selections(
            "https://example.com", sample_features, sample_prefs, target_dir=tmp_path
        )
        assert path == tmp_path / DUPLO_JSON

    def test_json_is_valid(self, tmp_path, sample_features, sample_prefs):
        path = save_selections(
            "https://example.com", sample_features, sample_prefs, target_dir=tmp_path
        )
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_source_url_stored(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_features_stored(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["features"]) == 2
        assert data["features"][0]["name"] == "Search"
        assert data["features"][1]["category"] == "api"

    def test_preferences_stored(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        prefs = data["preferences"]
        assert prefs["platform"] == "web"
        assert prefs["language"] == "Python/FastAPI"
        assert prefs["constraints"] == ["Postgres only"]
        assert prefs["preferences"] == ["pytest for tests"]

    def test_empty_features(self, tmp_path, sample_prefs):
        save_selections("https://example.com", [], sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["features"] == []

    def test_overwrites_existing_file(self, tmp_path, sample_features, sample_prefs):
        path = tmp_path / DUPLO_JSON
        path.write_text('{"old": "data"}')
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads(path.read_text())
        assert "source_url" in data
        assert "old" not in data

    def test_default_target_dir_is_cwd(self, monkeypatch, tmp_path, sample_features, sample_prefs):
        monkeypatch.chdir(tmp_path)
        path = save_selections("https://example.com", sample_features, sample_prefs)
        assert path == tmp_path / DUPLO_JSON

    def test_file_ends_with_newline(self, tmp_path, sample_features, sample_prefs):
        path = save_selections(
            "https://example.com", sample_features, sample_prefs, target_dir=tmp_path
        )
        assert path.read_text().endswith("\n")


class TestAppendPhaseToHistory:
    _PLAN = "# Phase 1: Core Scaffolding\n\n## Objective\nBuild the core.\n"

    def test_creates_phases_key(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "phases" in data

    def test_appends_one_entry(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["phases"]) == 1

    def test_entry_contains_phase_title(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["phases"][0]
        assert entry["phase"] == "Phase 1: Core Scaffolding"

    def test_entry_contains_plan(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["phases"][0]
        assert entry["plan"] == self._PLAN

    def test_entry_contains_completed_at(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["phases"][0]
        assert "completed_at" in entry
        assert entry["completed_at"].endswith("+00:00")

    def test_multiple_calls_accumulate(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        append_phase_to_history("# Phase 2: Auth\n", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["phases"]) == 2
        assert data["phases"][1]["phase"] == "Phase 2: Auth"

    def test_existing_fields_preserved(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history(self._PLAN, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_unknown_phase_when_no_heading(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        append_phase_to_history("No heading here.", target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["phases"][0]
        assert entry["phase"] == "Unknown phase"

    def test_returns_path(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        path = append_phase_to_history(self._PLAN, target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_file_ends_with_newline(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        path = append_phase_to_history(self._PLAN, target_dir=tmp_path)
        assert path.read_text().endswith("\n")


class TestSetInProgress:
    def test_creates_in_progress_key(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "in_progress" in data

    def test_stores_label(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["in_progress"]["label"] == "Phase 2"

    def test_mcloop_done_defaults_to_false(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["in_progress"]["mcloop_done"] is False

    def test_mcloop_done_can_be_set_true(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", mcloop_done=True, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["in_progress"]["mcloop_done"] is True

    def test_creates_file_when_absent(self, tmp_path):
        set_in_progress("Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["in_progress"]["label"] == "Phase 1"

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_overwrites_existing_in_progress(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        set_in_progress("Phase 2", mcloop_done=True, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["in_progress"]["label"] == "Phase 2"
        assert data["in_progress"]["mcloop_done"] is True

    def test_file_ends_with_newline(self, tmp_path):
        set_in_progress("Phase 1", target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")


class TestClearInProgress:
    def test_removes_in_progress_key(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        clear_in_progress(target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "in_progress" not in data

    def test_noop_when_key_absent(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        clear_in_progress(target_dir=tmp_path)  # should not raise
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "in_progress" not in data

    def test_noop_when_file_absent(self, tmp_path):
        clear_in_progress(target_dir=tmp_path)  # should not raise

    def test_preserves_other_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        clear_in_progress(target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_file_ends_with_newline(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        set_in_progress("Phase 1", target_dir=tmp_path)
        clear_in_progress(target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")


class TestSaveFeedback:
    def test_creates_feedback_key(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Looks good!", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "feedback" in data

    def test_appends_one_entry(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Looks good!", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["feedback"]) == 1

    def test_entry_contains_text(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Fix the login button.", target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["feedback"][0]
        assert entry["text"] == "Fix the login button."

    def test_entry_contains_after_phase(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Looks good!", after_phase="Phase 1: Core", target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["feedback"][0]
        assert entry["after_phase"] == "Phase 1: Core"

    def test_after_phase_defaults_to_empty(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Some feedback.", target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["feedback"][0]
        assert entry["after_phase"] == ""

    def test_entry_contains_recorded_at(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Looks good!", target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["feedback"][0]
        assert "recorded_at" in entry
        assert entry["recorded_at"].endswith("+00:00")

    def test_multiple_calls_accumulate(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Phase 1 feedback.", after_phase="Phase 1", target_dir=tmp_path)
        save_feedback("Phase 2 feedback.", after_phase="Phase 2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["feedback"]) == 2
        assert data["feedback"][1]["after_phase"] == "Phase 2"

    def test_creates_file_when_absent(self, tmp_path):
        save_feedback("Some feedback.", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["feedback"][0]["text"] == "Some feedback."

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_feedback("Looks good!", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_returns_path(self, tmp_path):
        path = save_feedback("Some feedback.", target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_file_ends_with_newline(self, tmp_path):
        save_feedback("Some feedback.", target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")


class TestWriteClaudeMd:
    def test_creates_file(self, tmp_path):
        path = write_claude_md(target_dir=tmp_path)
        assert path.exists()
        assert path.name == CLAUDE_MD

    def test_returns_correct_path(self, tmp_path):
        path = write_claude_md(target_dir=tmp_path)
        assert path == tmp_path / CLAUDE_MD

    def test_contains_appshot(self, tmp_path):
        path = write_claude_md(target_dir=tmp_path)
        content = path.read_text()
        assert "appshot" in content

    def test_contains_screenshots_current(self, tmp_path):
        path = write_claude_md(target_dir=tmp_path)
        assert "screenshots/current/" in path.read_text()

    def test_overwrites_existing_file(self, tmp_path):
        existing = tmp_path / CLAUDE_MD
        existing.write_text("old content")
        path = write_claude_md(target_dir=tmp_path)
        assert "appshot" in path.read_text()
        assert "old content" not in path.read_text()

    def test_default_target_dir_is_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = write_claude_md()
        assert path == tmp_path / CLAUDE_MD


class TestSaveScreenshotFeatureMap:
    _MAPPING = {
        "example_com_index.png": ["Search", "REST API"],
        "example_com_docs.png": ["REST API"],
    }

    def test_creates_file(self, tmp_path):
        path = save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        assert path.exists()
        assert path.name == DUPLO_JSON

    def test_returns_correct_path(self, tmp_path):
        path = save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_mapping_stored(self, tmp_path):
        save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["screenshot_features"] == self._MAPPING

    def test_overwrites_existing_mapping(self, tmp_path):
        save_screenshot_feature_map({"old.png": ["Old Feature"]}, target_dir=tmp_path)
        save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["screenshot_features"] == self._MAPPING
        assert "old.png" not in data["screenshot_features"]

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_empty_mapping_stored(self, tmp_path):
        save_screenshot_feature_map({}, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["screenshot_features"] == {}

    def test_creates_file_when_absent(self, tmp_path):
        save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "screenshot_features" in data

    def test_file_ends_with_newline(self, tmp_path):
        save_screenshot_feature_map(self._MAPPING, target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")
