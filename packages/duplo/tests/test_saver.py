"""Tests for duplo.saver."""

from __future__ import annotations

import json

import pytest

from duplo.doc_examples import CodeExample
from duplo.doc_tables import (
    DocStructures,
    FeatureTable,
    FunctionRef,
    OperationList,
    UnitList,
)
from duplo.extractor import Feature
from duplo.fetcher import PageRecord
from duplo.questioner import BuildPreferences
from duplo.saver import (
    CLAUDE_MD,
    DUPLO_JSON,
    EXAMPLES_DIR,
    RAW_PAGES_DIR,
    append_phase_to_history,
    clear_in_progress,
    load_examples,
    save_examples,
    save_feedback,
    save_raw_content,
    save_reference_urls,
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
        assert path.name == "duplo.json"

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
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def test_code_examples_stored(self, tmp_path, sample_features, sample_prefs):
        examples = [
            CodeExample(
                input="print(1+1)",
                expected_output="2",
                source_url="https://docs.example.com",
                language="python",
            ),
        ]
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            code_examples=examples,
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["code_examples"]) == 1
        assert data["code_examples"][0]["input"] == "print(1+1)"
        assert data["code_examples"][0]["expected_output"] == "2"

    def test_code_examples_omitted_when_none(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "code_examples" not in data

    def test_doc_structures_stored(self, tmp_path, sample_features, sample_prefs):
        structures = DocStructures(
            feature_tables=[
                FeatureTable(heading="Ops", rows=[{"op": "add"}], source_url="https://docs.ex.com")
            ],
            operation_lists=[
                OperationList(
                    heading="Math", items=["add", "sub"], source_url="https://docs.ex.com"
                )
            ],
            unit_lists=[
                UnitList(heading="Units", items=["m", "kg"], source_url="https://docs.ex.com")
            ],
            function_refs=[
                FunctionRef(
                    name="sin",
                    signature="sin(x)",
                    description="Sine",
                    source_url="https://docs.ex.com",
                )
            ],
        )
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            doc_structures=structures,
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        ds = data["doc_structures"]
        assert len(ds["feature_tables"]) == 1
        assert ds["feature_tables"][0]["heading"] == "Ops"
        assert len(ds["operation_lists"]) == 1
        assert len(ds["unit_lists"]) == 1
        assert len(ds["function_refs"]) == 1
        assert ds["function_refs"][0]["name"] == "sin"

    def test_doc_structures_omitted_when_none(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "doc_structures" not in data

    def test_all_extracted_data_in_single_write(self, tmp_path, sample_features, sample_prefs):
        examples = [
            CodeExample(
                input="1+1",
                expected_output="2",
                source_url="https://docs.ex.com",
                language="python",
            ),
        ]
        structures = DocStructures(
            feature_tables=[],
            operation_lists=[],
            unit_lists=[UnitList(heading="U", items=["m"], source_url="https://docs.ex.com")],
            function_refs=[],
        )
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            code_examples=examples,
            doc_structures=structures,
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "source_url" in data
        assert "features" in data
        assert "code_examples" in data
        assert "doc_structures" in data
        assert len(data["code_examples"]) == 1
        assert len(data["doc_structures"]["unit_lists"]) == 1


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
        assert path.name == "duplo.json"

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


class TestSaveReferenceUrls:
    _RECORDS = [
        PageRecord(
            url="https://example.com",
            fetched_at="2026-03-06T12:00:00+00:00",
            content_hash="abc123",
        ),
        PageRecord(
            url="https://example.com/docs",
            fetched_at="2026-03-06T12:00:01+00:00",
            content_hash="def456",
        ),
    ]

    def test_creates_file(self, tmp_path):
        path = save_reference_urls(self._RECORDS, target_dir=tmp_path)
        assert path.exists()
        assert path.name == "duplo.json"

    def test_returns_correct_path(self, tmp_path):
        path = save_reference_urls(self._RECORDS, target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_records_stored(self, tmp_path):
        save_reference_urls(self._RECORDS, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["reference_urls"]) == 2
        assert data["reference_urls"][0]["url"] == "https://example.com"
        assert data["reference_urls"][0]["fetched_at"] == "2026-03-06T12:00:00+00:00"
        assert data["reference_urls"][0]["content_hash"] == "abc123"
        assert data["reference_urls"][1]["url"] == "https://example.com/docs"

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_reference_urls(self._RECORDS, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_empty_records(self, tmp_path):
        save_reference_urls([], target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["reference_urls"] == []

    def test_overwrites_existing_records(self, tmp_path):
        old = [
            PageRecord(
                url="https://old.com",
                fetched_at="2026-01-01T00:00:00+00:00",
                content_hash="old",
            )
        ]
        save_reference_urls(old, target_dir=tmp_path)
        save_reference_urls(self._RECORDS, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["reference_urls"]) == 2
        assert data["reference_urls"][0]["url"] == "https://example.com"

    def test_file_ends_with_newline(self, tmp_path):
        save_reference_urls(self._RECORDS, target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")


class TestSaveExamples:
    _EXAMPLES = [
        CodeExample(
            input="print(1+1)",
            expected_output="2",
            source_url="https://docs.example.com",
            language="python",
        ),
        CodeExample(
            input="echo hello",
            expected_output="hello",
            source_url="https://docs.example.com/shell",
            language="shell",
        ),
    ]

    def test_creates_examples_dir(self, tmp_path):
        result = save_examples(self._EXAMPLES, target_dir=tmp_path)
        assert result == tmp_path / EXAMPLES_DIR
        assert result.is_dir()

    def test_creates_one_file_per_example(self, tmp_path):
        save_examples(self._EXAMPLES, target_dir=tmp_path)
        files = sorted((tmp_path / EXAMPLES_DIR).glob("*.json"))
        assert len(files) == 2

    def test_filenames_include_index_and_slug(self, tmp_path):
        save_examples(self._EXAMPLES, target_dir=tmp_path)
        files = sorted(f.name for f in (tmp_path / EXAMPLES_DIR).glob("*.json"))
        assert files[0].startswith("000_")
        assert files[1].startswith("001_")

    def test_file_content_is_valid_json(self, tmp_path):
        save_examples(self._EXAMPLES, target_dir=tmp_path)
        files = sorted((tmp_path / EXAMPLES_DIR).glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["input"] == "print(1+1)"
        assert data["expected_output"] == "2"
        assert data["source_url"] == "https://docs.example.com"
        assert data["language"] == "python"

    def test_files_end_with_newline(self, tmp_path):
        save_examples(self._EXAMPLES, target_dir=tmp_path)
        for filepath in (tmp_path / EXAMPLES_DIR).glob("*.json"):
            assert filepath.read_text(encoding="utf-8").endswith("\n")

    def test_empty_examples_creates_empty_dir(self, tmp_path):
        save_examples([], target_dir=tmp_path)
        assert (tmp_path / EXAMPLES_DIR).is_dir()
        assert list((tmp_path / EXAMPLES_DIR).glob("*.json")) == []

    def test_clears_old_files_on_rewrite(self, tmp_path):
        save_examples(self._EXAMPLES, target_dir=tmp_path)
        save_examples(self._EXAMPLES[:1], target_dir=tmp_path)
        files = list((tmp_path / EXAMPLES_DIR).glob("*.json"))
        assert len(files) == 1


class TestLoadExamples:
    def test_loads_from_examples_dir(self, tmp_path):
        examples = [
            CodeExample(
                input="print(1)",
                expected_output="1",
                source_url="https://example.com",
                language="python",
            ),
        ]
        save_examples(examples, target_dir=tmp_path)
        loaded = load_examples(target_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].input == "print(1)"
        assert loaded[0].expected_output == "1"

    def test_falls_back_to_duplo_json(self, tmp_path):
        data = {
            "code_examples": [
                {
                    "input": "old",
                    "expected_output": "data",
                    "source_url": "",
                    "language": "",
                }
            ]
        }
        (tmp_path / ".duplo").mkdir(exist_ok=True)
        (tmp_path / ".duplo" / "duplo.json").write_text(json.dumps(data))
        loaded = load_examples(target_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].input == "old"

    def test_prefers_examples_dir_over_duplo_json(self, tmp_path):
        data = {"code_examples": [{"input": "old", "expected_output": "data"}]}
        (tmp_path / ".duplo").mkdir(exist_ok=True)
        (tmp_path / ".duplo" / "duplo.json").write_text(json.dumps(data))
        examples = [
            CodeExample(
                input="new",
                expected_output="data",
                source_url="",
                language="",
            ),
        ]
        save_examples(examples, target_dir=tmp_path)
        loaded = load_examples(target_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].input == "new"

    def test_returns_empty_when_nothing_exists(self, tmp_path):
        assert load_examples(target_dir=tmp_path) == []

    def test_sorted_by_filename(self, tmp_path):
        examples = [
            CodeExample(input="first", expected_output="1", source_url="", language=""),
            CodeExample(input="second", expected_output="2", source_url="", language=""),
        ]
        save_examples(examples, target_dir=tmp_path)
        loaded = load_examples(target_dir=tmp_path)
        assert loaded[0].input == "first"
        assert loaded[1].input == "second"


class TestSaveRawContent:
    _HTML_A = "<html><body><h1>Page A</h1></body></html>"
    _HTML_B = "<html><body><h1>Page B</h1></body></html>"
    _RECORDS = [
        PageRecord(
            url="https://example.com",
            fetched_at="2026-03-06T12:00:00+00:00",
            content_hash="aaa111",
        ),
        PageRecord(
            url="https://example.com/docs",
            fetched_at="2026-03-06T12:00:01+00:00",
            content_hash="bbb222",
        ),
    ]

    def _raw_pages(self):
        return {
            "https://example.com": self._HTML_A,
            "https://example.com/docs": self._HTML_B,
        }

    def test_creates_raw_pages_dir(self, tmp_path):
        result = save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        assert result == tmp_path / RAW_PAGES_DIR
        assert result.is_dir()

    def test_saves_html_files(self, tmp_path):
        save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert (pages_dir / "aaa111.html").read_text(encoding="utf-8") == self._HTML_A
        assert (pages_dir / "bbb222.html").read_text(encoding="utf-8") == self._HTML_B

    def test_skips_missing_urls(self, tmp_path):
        partial = {"https://example.com": self._HTML_A}
        save_raw_content(partial, self._RECORDS, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert (pages_dir / "aaa111.html").exists()
        assert not (pages_dir / "bbb222.html").exists()

    def test_empty_raw_pages(self, tmp_path):
        save_raw_content({}, self._RECORDS, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert pages_dir.is_dir()
        assert list(pages_dir.iterdir()) == []

    def test_empty_records(self, tmp_path):
        save_raw_content(self._raw_pages(), [], target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert list(pages_dir.iterdir()) == []

    def test_overwrites_existing_files(self, tmp_path):
        save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        new_html = "<html><body><h1>Updated</h1></body></html>"
        save_raw_content({"https://example.com": new_html}, self._RECORDS[:1], target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert (pages_dir / "aaa111.html").read_text(encoding="utf-8") == new_html
