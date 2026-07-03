"""Tests for duplo.saver."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
from duplo.planner import CompletedTask
from duplo.questioner import BuildPreferences
from duplo.platforms.schema import PlatformProfile
from duplo.saver import (
    CLAUDE_MD_FILENAME,
    DUPLO_JSON,
    EXAMPLES_DIR,
    PRODUCT_JSON,
    RAW_PAGES_DIR,
    REFERENCES_DIR,
    _find_duplicate_groups,
    _merge_duplicate_group,
    _propagate_implemented_status,
    append_phase_to_history,
    resolve_issue,
    save_build_preferences,
    save_issue,
    load_examples,
    load_product,
    mark_implemented_features,
    move_references,
    resolve_completed_fixes,
    save_examples,
    save_feature_status,
    save_features,
    save_feedback,
    save_frame_descriptions,
    derive_app_name,
    save_product,
    save_raw_content,
    save_reference_urls,
    save_roadmap,
    save_sources,
    save_selections,
    store_accepted_frames,
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


class TestSaveSelectionsArchHash:
    """Tests for architecture_hash in save_selections."""

    def test_arch_hash_stored(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            arch_hash="abc123",
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["architecture_hash"] == "abc123"

    def test_arch_hash_omitted_when_empty(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "architecture_hash" not in data


class TestSaveBuildPreferences:
    """Tests for save_build_preferences."""

    def test_updates_preferences(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        new_prefs = [
            BuildPreferences(
                platform="cli",
                language="Rust",
                constraints=[],
                preferences=[],
            )
        ]
        save_build_preferences(new_prefs, "newhash", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert isinstance(data["preferences"], list)
        assert data["preferences"][0]["platform"] == "cli"
        assert data["preferences"][0]["language"] == "Rust"
        assert data["architecture_hash"] == "newhash"

    def test_stores_multi_stack_list(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        new_prefs = [
            BuildPreferences(
                platform="web", language="TypeScript", constraints=[], preferences=[]
            ),
            BuildPreferences(platform="linux", language="Python", constraints=[], preferences=[]),
        ]
        save_build_preferences(new_prefs, "newhash", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["preferences"]) == 2
        assert data["preferences"][0]["platform"] == "web"
        assert data["preferences"][1]["platform"] == "linux"

    def test_preserves_other_keys(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        new_prefs = [
            BuildPreferences(platform="cli", language="Go", constraints=[], preferences=[])
        ]
        save_build_preferences(new_prefs, "hash2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"
        assert len(data["features"]) == 2

    def test_empty_hash_omitted(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        new_prefs = [
            BuildPreferences(platform="cli", language="Go", constraints=[], preferences=[])
        ]
        save_build_preferences(new_prefs, "", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert "architecture_hash" not in data


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

    def test_prefixed_heading(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        plan = "# McWhisper — Phase 1: Core\n\n## Objective\nBuild it.\n"
        append_phase_to_history(plan, target_dir=tmp_path)
        entry = json.loads((tmp_path / DUPLO_JSON).read_text())["phases"][0]
        assert entry["phase"] == "Phase 1: Core"


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


class TestSaveSources:
    _SOURCES = [
        {
            "url": "https://example.com",
            "last_scraped": "2026-04-14T10:00:00+00:00",
            "content_hash": "abc123",
            "scrape_depth_used": "deep",
        },
        {
            "url": "https://docs.example.com",
            "last_scraped": "2026-04-14T10:01:00+00:00",
            "content_hash": "def456",
            "scrape_depth_used": "shallow",
        },
    ]

    def test_creates_file(self, tmp_path):
        path = save_sources(self._SOURCES, target_dir=tmp_path)
        assert path.exists()
        assert path.name == "duplo.json"

    def test_returns_correct_path(self, tmp_path):
        path = save_sources(self._SOURCES, target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_sources_stored(self, tmp_path):
        save_sources(self._SOURCES, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["sources"]) == 2
        assert data["sources"][0]["url"] == "https://example.com"
        assert data["sources"][0]["last_scraped"] == "2026-04-14T10:00:00+00:00"
        assert data["sources"][0]["content_hash"] == "abc123"
        assert data["sources"][0]["scrape_depth_used"] == "deep"
        assert data["sources"][1]["url"] == "https://docs.example.com"
        assert data["sources"][1]["scrape_depth_used"] == "shallow"

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        save_sources(self._SOURCES, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"
        assert len(data["sources"]) == 2

    def test_empty_sources(self, tmp_path):
        save_sources([], target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["sources"] == []

    def test_merges_preserving_removed_urls(self, tmp_path):
        """Entries for URLs no longer in the scrape list are preserved."""
        old = [
            {
                "url": "https://old.com",
                "last_scraped": "2026-01-01T00:00:00+00:00",
                "content_hash": "old",
                "scrape_depth_used": "deep",
            }
        ]
        save_sources(old, target_dir=tmp_path)
        save_sources(self._SOURCES, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        urls = {s["url"] for s in data["sources"]}
        assert "https://old.com" in urls
        assert "https://example.com" in urls
        assert "https://docs.example.com" in urls
        assert len(data["sources"]) == 3

    def test_updates_existing_entry_by_url(self, tmp_path):
        """An entry with the same URL is updated, not duplicated."""
        old = [
            {
                "url": "https://example.com",
                "last_scraped": "2026-01-01T00:00:00+00:00",
                "content_hash": "old_hash",
                "scrape_depth_used": "shallow",
            }
        ]
        save_sources(old, target_dir=tmp_path)
        save_sources(self._SOURCES, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        by_url = {s["url"]: s for s in data["sources"]}
        assert by_url["https://example.com"]["content_hash"] == "abc123"
        assert by_url["https://example.com"]["scrape_depth_used"] == "deep"

    def test_file_ends_with_newline(self, tmp_path):
        save_sources(self._SOURCES, target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")

    def test_idempotent_double_call(self, tmp_path):
        """Calling save_sources twice with the same data produces
        identical duplo.json content."""
        save_sources(self._SOURCES, target_dir=tmp_path)
        first = (tmp_path / DUPLO_JSON).read_text()
        save_sources(self._SOURCES, target_dir=tmp_path)
        second = (tmp_path / DUPLO_JSON).read_text()
        assert first == second

    def test_multiple_sources_tracked_independently(self, tmp_path):
        """Each source entry retains its own metadata independently."""
        sources = [
            {
                "url": "https://alpha.com",
                "last_scraped": "2026-04-14T10:00:00+00:00",
                "content_hash": "hash_alpha",
                "scrape_depth_used": "deep",
            },
            {
                "url": "https://beta.com",
                "last_scraped": "2026-04-14T10:05:00+00:00",
                "content_hash": "hash_beta",
                "scrape_depth_used": "shallow",
            },
            {
                "url": "https://gamma.com",
                "last_scraped": "2026-04-14T10:10:00+00:00",
                "content_hash": "hash_gamma",
                "scrape_depth_used": "deep",
            },
        ]
        save_sources(sources, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        by_url = {s["url"]: s for s in data["sources"]}
        assert len(by_url) == 3
        assert by_url["https://alpha.com"]["content_hash"] == "hash_alpha"
        assert by_url["https://alpha.com"]["scrape_depth_used"] == "deep"
        assert by_url["https://beta.com"]["content_hash"] == "hash_beta"
        assert by_url["https://beta.com"]["scrape_depth_used"] == "shallow"
        assert by_url["https://gamma.com"]["content_hash"] == "hash_gamma"
        assert by_url["https://gamma.com"]["last_scraped"] == "2026-04-14T10:10:00+00:00"

    def test_update_one_source_leaves_others_untouched(self, tmp_path):
        """Updating one source's metadata does not alter other entries."""
        save_sources(self._SOURCES, target_dir=tmp_path)
        updated = [
            {
                "url": "https://example.com",
                "last_scraped": "2026-04-15T12:00:00+00:00",
                "content_hash": "new_hash",
                "scrape_depth_used": "shallow",
            },
        ]
        save_sources(updated, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        by_url = {s["url"]: s for s in data["sources"]}
        # Updated entry changed.
        assert by_url["https://example.com"]["content_hash"] == "new_hash"
        # Other entry untouched.
        assert by_url["https://docs.example.com"]["content_hash"] == "def456"
        assert by_url["https://docs.example.com"]["scrape_depth_used"] == "shallow"


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

    @staticmethod
    def _url_hash(url: str) -> str:
        import hashlib

        return hashlib.sha256(url.encode()).hexdigest()

    def _raw_pages(self):
        return {
            "https://example.com": self._HTML_A,
            "https://example.com/docs": self._HTML_B,
        }

    def test_saves_html_to_url_hashed_filenames(self, tmp_path):
        save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        hash_a = self._url_hash("https://example.com")
        hash_b = self._url_hash("https://example.com/docs")
        assert (pages_dir / f"{hash_a}.html").read_text(encoding="utf-8") == self._HTML_A
        assert (pages_dir / f"{hash_b}.html").read_text(encoding="utf-8") == self._HTML_B

    def test_url_hash_matches_sha256(self, tmp_path):
        import hashlib

        save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        for record in self._RECORDS:
            expected = hashlib.sha256(record.url.encode()).hexdigest()
            assert (pages_dir / f"{expected}.html").exists()

    def test_overwrites_existing_file_at_same_hash(self, tmp_path):
        save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        new_html = "<html><body><h1>Updated</h1></body></html>"
        save_raw_content(
            {"https://example.com": new_html},
            self._RECORDS[:1],
            target_dir=tmp_path,
        )
        pages_dir = tmp_path / RAW_PAGES_DIR
        hash_a = self._url_hash("https://example.com")
        assert (pages_dir / f"{hash_a}.html").read_text(encoding="utf-8") == new_html

    def test_missing_key_skipped_with_diagnostic(self, tmp_path):
        partial = {"https://example.com": self._HTML_A}
        with patch("duplo.saver.record_failure") as mock_rf:
            save_raw_content(partial, self._RECORDS, target_dir=tmp_path)
        mock_rf.assert_called_once_with(
            "save_raw_content",
            "io",
            "no raw_pages entry for https://example.com/docs; record skipped",
        )
        pages_dir = tmp_path / RAW_PAGES_DIR
        hash_a = self._url_hash("https://example.com")
        hash_b = self._url_hash("https://example.com/docs")
        assert (pages_dir / f"{hash_a}.html").exists()
        assert not (pages_dir / f"{hash_b}.html").exists()

    def test_remaining_records_persisted_when_one_skipped(self, tmp_path):
        records = [
            PageRecord(
                url="https://example.com/missing",
                fetched_at="2026-03-06T12:00:00+00:00",
                content_hash="xxx",
            ),
            self._RECORDS[0],
        ]
        raw = {"https://example.com": self._HTML_A}
        with patch("duplo.saver.record_failure"):
            save_raw_content(raw, records, target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        hash_a = self._url_hash("https://example.com")
        assert (pages_dir / f"{hash_a}.html").exists()

    def test_empty_raw_pages_and_empty_records_noop(self, tmp_path):
        save_raw_content({}, [], target_dir=tmp_path)
        pages_dir = tmp_path / RAW_PAGES_DIR
        assert not pages_dir.exists()

    def test_returns_none(self, tmp_path):
        result = save_raw_content(self._raw_pages(), self._RECORDS, target_dir=tmp_path)
        assert result is None


class TestMoveReferences:
    def test_moves_files(self, tmp_path):
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"PNG" * 100)
        pdf = tmp_path / "spec.pdf"
        pdf.write_bytes(b"%PDF" * 100)

        moved = move_references([img, pdf], target_dir=tmp_path)

        assert len(moved) == 2
        refs_dir = tmp_path / REFERENCES_DIR
        assert (refs_dir / "screenshot.png").exists()
        assert (refs_dir / "spec.pdf").exists()
        assert not img.exists()
        assert not pdf.exists()

    def test_creates_references_dir(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG" * 100)

        move_references([img], target_dir=tmp_path)

        assert (tmp_path / REFERENCES_DIR).is_dir()

    def test_skips_missing_files(self, tmp_path):
        missing = tmp_path / "gone.png"
        existing = tmp_path / "here.png"
        existing.write_bytes(b"PNG" * 100)

        moved = move_references([missing, existing], target_dir=tmp_path)

        assert len(moved) == 1
        assert moved[0].name == "here.png"

    def test_empty_list(self, tmp_path):
        moved = move_references([], target_dir=tmp_path)
        assert moved == []

    def test_overwrites_existing_destination(self, tmp_path):
        refs_dir = tmp_path / REFERENCES_DIR
        refs_dir.mkdir(parents=True)
        (refs_dir / "dup.png").write_bytes(b"old")

        src = tmp_path / "dup.png"
        src.write_bytes(b"new")

        moved = move_references([src], target_dir=tmp_path)

        assert len(moved) == 1
        assert (refs_dir / "dup.png").read_bytes() == b"new"
        assert not src.exists()


class TestSaveFrameDescriptions:
    _DESCS = [
        {"filename": "frame_001.png", "state": "Settings panel", "detail": "Theme toggle"},
        {"filename": "frame_002.png", "state": "Main dashboard", "detail": "Grid layout"},
    ]

    def test_creates_file(self, tmp_path):
        path = save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        assert path.exists()
        assert path.name == "duplo.json"

    def test_returns_correct_path(self, tmp_path):
        path = save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        assert path == tmp_path / DUPLO_JSON

    def test_descriptions_stored(self, tmp_path):
        save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["frame_descriptions"]) == 2
        assert data["frame_descriptions"][0]["state"] == "Settings panel"
        assert data["frame_descriptions"][1]["filename"] == "frame_002.png"

    def test_preserves_existing_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"

    def test_empty_descriptions(self, tmp_path):
        save_frame_descriptions([], target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["frame_descriptions"] == []

    def test_overwrites_existing_descriptions(self, tmp_path):
        save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        new = [{"filename": "new.png", "state": "Login", "detail": "Form"}]
        save_frame_descriptions(new, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["frame_descriptions"]) == 1
        assert data["frame_descriptions"][0]["filename"] == "new.png"

    def test_file_ends_with_newline(self, tmp_path):
        save_frame_descriptions(self._DESCS, target_dir=tmp_path)
        assert (tmp_path / DUPLO_JSON).read_text().endswith("\n")


class TestStoreAcceptedFrames:
    def _make_frames(self, tmp_path):
        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True)
        f1 = frames_dir / "frame_001.png"
        f1.write_bytes(b"PNG1" * 100)
        f2 = frames_dir / "frame_002.png"
        f2.write_bytes(b"PNG2" * 100)
        return [
            {
                "path": f1,
                "filename": "frame_001.png",
                "state": "Settings panel",
                "detail": "Theme toggle",
            },
            {
                "path": f2,
                "filename": "frame_002.png",
                "state": "Main dashboard",
                "detail": "Grid layout",
            },
        ]

    def test_copies_frames_to_references(self, tmp_path):
        entries = self._make_frames(tmp_path)
        copied = store_accepted_frames(entries, target_dir=tmp_path)

        refs_dir = tmp_path / REFERENCES_DIR
        assert len(copied) == 2
        assert (refs_dir / "frame_001.png").exists()
        assert (refs_dir / "frame_002.png").exists()

    def test_source_frames_preserved(self, tmp_path):
        entries = self._make_frames(tmp_path)
        store_accepted_frames(entries, target_dir=tmp_path)

        # Source frames should still exist (copy, not move).
        for entry in entries:
            assert Path(entry["path"]).exists()

    def test_saves_descriptions_to_duplo_json(self, tmp_path):
        entries = self._make_frames(tmp_path)
        store_accepted_frames(entries, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["frame_descriptions"]) == 2
        assert data["frame_descriptions"][0]["state"] == "Settings panel"
        assert data["frame_descriptions"][1]["detail"] == "Grid layout"

    def test_skips_missing_frames(self, tmp_path):
        entries = self._make_frames(tmp_path)
        Path(entries[0]["path"]).unlink()
        copied = store_accepted_frames(entries, target_dir=tmp_path)

        assert len(copied) == 1
        assert copied[0].name == "frame_002.png"

    def test_empty_list(self, tmp_path):
        copied = store_accepted_frames([], target_dir=tmp_path)
        assert copied == []

    def test_creates_references_dir(self, tmp_path):
        entries = self._make_frames(tmp_path)
        store_accepted_frames(entries, target_dir=tmp_path)
        assert (tmp_path / REFERENCES_DIR).is_dir()

    def test_descriptions_exclude_missing_frames(self, tmp_path):
        entries = self._make_frames(tmp_path)
        Path(entries[0]["path"]).unlink()
        store_accepted_frames(entries, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["frame_descriptions"]) == 1
        assert data["frame_descriptions"][0]["filename"] == "frame_002.png"


class TestSaveProduct:
    def test_creates_file(self, tmp_path):
        path = save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        assert path.exists()
        assert path.name == "product.json"

    def test_returns_correct_path(self, tmp_path):
        path = save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        assert path == tmp_path / PRODUCT_JSON

    def test_stores_product_name(self, tmp_path):
        save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == "Acme App"

    def test_stores_source_url(self, tmp_path):
        save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["source_url"] == "https://acme.com"

    def test_empty_url(self, tmp_path):
        save_product("Acme App", "", target_dir=tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["source_url"] == ""

    def test_overwrites_existing(self, tmp_path):
        save_product("Old", "https://old.com", target_dir=tmp_path)
        save_product("New", "https://new.com", target_dir=tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == "New"

    def test_file_ends_with_newline(self, tmp_path):
        save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        assert (tmp_path / PRODUCT_JSON).read_text().endswith("\n")

    def test_creates_duplo_dir(self, tmp_path):
        save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        assert (tmp_path / ".duplo").is_dir()

    def test_preserves_existing_keys(self, tmp_path):
        """save_product does read-modify-write, preserving keys like app_name."""
        path = tmp_path / PRODUCT_JSON
        (tmp_path / ".duplo").mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"product_name": "Old", "source_url": "https://old.com", "app_name": "my-app"}
            )
            + "\n",
        )
        save_product("New", "https://new.com", target_dir=tmp_path)
        data = json.loads(path.read_text())
        assert data["product_name"] == "New"
        assert data["source_url"] == "https://new.com"
        assert data["app_name"] == "my-app"


class TestLoadProduct:
    def test_loads_saved_product(self, tmp_path):
        save_product("Acme App", "https://acme.com", target_dir=tmp_path)
        result = load_product(target_dir=tmp_path)
        assert result == ("Acme App", "https://acme.com")

    def test_returns_none_when_missing(self, tmp_path):
        assert load_product(target_dir=tmp_path) is None

    def test_empty_fields(self, tmp_path):
        save_product("", "", target_dir=tmp_path)
        result = load_product(target_dir=tmp_path)
        assert result == ("", "")


class TestDeriveAppName:
    """Tests for derive_app_name()."""

    def test_url_based_derivation(self, tmp_path):
        """Product-reference URL uses product_name from product.json."""
        from duplo.spec_reader import ProductSpec, SourceEntry

        # Pre-populate product.json with a validated product name.
        save_product("Numi Calculator", "https://numi.app", target_dir=tmp_path)

        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://numi.app",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        result = derive_app_name(spec, tmp_path)
        assert result == "Numi Calculator"

    def test_no_url_fallback_uses_directory_name(self, tmp_path):
        """No product-reference URL falls back to directory name."""
        from duplo.spec_reader import ProductSpec

        spec = ProductSpec()
        result = derive_app_name(spec, tmp_path)
        assert result == tmp_path.resolve().name

    def test_product_json_written(self, tmp_path):
        """app_name is persisted to product.json."""
        from duplo.spec_reader import ProductSpec

        spec = ProductSpec()
        derive_app_name(spec, tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["app_name"] == tmp_path.resolve().name

    def test_user_edited_product_json_not_overwritten(self, tmp_path):
        """Existing app_name in product.json is preserved."""
        from duplo.spec_reader import ProductSpec, SourceEntry

        # Simulate user editing product.json.
        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps(
                {
                    "product_name": "Numi Calculator",
                    "source_url": "https://numi.app",
                    "app_name": "My Custom Name",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://numi.app",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        result = derive_app_name(spec, tmp_path)
        assert result == "My Custom Name"
        # Verify product.json was NOT rewritten.
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["app_name"] == "My Custom Name"

    def test_no_spec_falls_back_to_directory(self, tmp_path):
        """None spec uses directory name."""
        result = derive_app_name(None, tmp_path)
        assert result == tmp_path.resolve().name

    def test_non_product_reference_sources_ignored(self, tmp_path):
        """Sources with roles other than product-reference don't count."""
        from duplo.spec_reader import ProductSpec, SourceEntry

        save_product("Some Product", "https://example.com", target_dir=tmp_path)

        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://example.com",
                    role="docs",
                    scrape="deep",
                ),
            ],
        )
        result = derive_app_name(spec, tmp_path)
        # No product-reference source → falls back to dir name.
        assert result == tmp_path.resolve().name

    def test_product_ref_but_no_product_name_falls_back(self, tmp_path):
        """Product-reference URL but empty product_name → dir name."""
        from duplo.spec_reader import ProductSpec, SourceEntry

        # product.json exists but product_name is empty.
        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps({"product_name": "", "source_url": "https://numi.app"}) + "\n",
            encoding="utf-8",
        )

        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://numi.app",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        result = derive_app_name(spec, tmp_path)
        assert result == tmp_path.resolve().name

    def test_duplo_json_app_name_used_when_product_json_empty(self, tmp_path):
        """duplo.json app_name is used when product.json has no app_name."""
        from duplo.spec_reader import ProductSpec

        # product.json exists with empty product_name, no app_name.
        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps({"product_name": "", "source_url": "https://numi.app"}) + "\n",
            encoding="utf-8",
        )
        # duplo.json has app_name from save_selections.
        (tmp_path / DUPLO_JSON).write_text(
            json.dumps({"app_name": "Numi"}) + "\n",
            encoding="utf-8",
        )

        result = derive_app_name(ProductSpec(), tmp_path)
        assert result == "Numi"

    def test_product_name_synced_when_empty(self, tmp_path):
        """product_name in product.json is populated when empty."""
        from duplo.spec_reader import ProductSpec

        # product.json with empty product_name.
        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps({"product_name": "", "source_url": "https://numi.app"}) + "\n",
            encoding="utf-8",
        )
        # duplo.json has app_name.
        (tmp_path / DUPLO_JSON).write_text(
            json.dumps({"app_name": "Numi"}) + "\n",
            encoding="utf-8",
        )

        derive_app_name(ProductSpec(), tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == "Numi"

    def test_existing_product_name_not_overwritten(self, tmp_path):
        """Non-empty product_name in product.json is preserved."""
        from duplo.spec_reader import ProductSpec

        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps(
                {
                    "product_name": "User Custom Name",
                    "source_url": "https://numi.app",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / DUPLO_JSON).write_text(
            json.dumps({"app_name": "Numi"}) + "\n",
            encoding="utf-8",
        )

        derive_app_name(ProductSpec(), tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == "User Custom Name"

    def test_app_name_in_product_json_syncs_product_name(self, tmp_path):
        """When product.json has app_name but empty product_name, product_name is synced."""
        (tmp_path / ".duplo").mkdir(parents=True)
        (tmp_path / PRODUCT_JSON).write_text(
            json.dumps(
                {
                    "product_name": "",
                    "source_url": "https://numi.app",
                    "app_name": "My App",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = derive_app_name(None, tmp_path)
        assert result == "My App"
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == "My App"

    def test_directory_name_populates_product_name(self, tmp_path):
        """Directory-name fallback also populates product_name."""
        derive_app_name(None, tmp_path)
        data = json.loads((tmp_path / PRODUCT_JSON).read_text())
        assert data["product_name"] == tmp_path.resolve().name
        assert data["app_name"] == tmp_path.resolve().name


class TestSaveFeatures:
    """Tests for save_features() merge behaviour."""

    def test_adds_new_features(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        new = [Feature(name="Dark mode", description="Toggle dark theme.", category="ui")]
        save_features(new, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]
        assert "Dark mode" in names
        assert "Search" in names
        assert len(data["features"]) == 3

    def test_skips_duplicates(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        dup = [Feature(name="Search", description="Different desc.", category="core")]
        save_features(dup, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["features"]) == 2
        search_feat = [f for f in data["features"] if f["name"] == "Search"][0]
        assert search_feat["description"] == "Full-text search."

    def test_creates_features_key_when_absent(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True)
        (duplo_dir / "duplo.json").write_text("{}", encoding="utf-8")
        new = [Feature(name="Auth", description="User login.", category="core")]
        save_features(new, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["features"]) == 1
        assert data["features"][0]["name"] == "Auth"

    def test_preserves_existing_features(self, tmp_path, sample_features, sample_prefs):
        save_selections("https://example.com", sample_features, sample_prefs, target_dir=tmp_path)
        save_features([], target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["features"]) == 2

    def test_new_features_get_pending_status(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True)
        (duplo_dir / "duplo.json").write_text("{}", encoding="utf-8")
        new = [Feature(name="Auth", description="User login.", category="core")]
        save_features(new, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        feat = data["features"][0]
        assert feat["status"] == "pending"
        assert feat["implemented_in"] == ""

    def test_preserves_existing_status(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True)
        (duplo_dir / "duplo.json").write_text(
            json.dumps(
                {
                    "features": [
                        {
                            "name": "Auth",
                            "description": "User login.",
                            "category": "core",
                            "status": "implemented",
                            "implemented_in": "Phase 1",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        new = [Feature(name="Auth", description="Different.", category="core")]
        save_features(new, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        feat = data["features"][0]
        assert feat["status"] == "implemented"
        assert feat["implemented_in"] == "Phase 1"

    def test_legacy_features_without_status_treated_as_pending(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True)
        (duplo_dir / "duplo.json").write_text(
            json.dumps(
                {
                    "features": [
                        {
                            "name": "Auth",
                            "description": "User login.",
                            "category": "core",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        feat = Feature(**data["features"][0])
        assert feat.status == "pending"
        assert feat.implemented_in == ""

    def test_selections_include_status_fields(self, tmp_path, sample_features, sample_prefs):
        save_selections(
            "https://example.com",
            sample_features,
            sample_prefs,
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        for feat in data["features"]:
            assert feat["status"] == "pending"
            assert feat["implemented_in"] == ""


class TestFindDuplicateGroups:
    """Tests for _find_duplicate_groups()."""

    def test_returns_empty_for_single_name(self):
        assert _find_duplicate_groups(["Auth"]) == []

    def test_returns_empty_for_empty_list(self):
        assert _find_duplicate_groups([]) == []

    def test_parses_valid_groups(self, monkeypatch):
        response = json.dumps(
            [
                ["Custom vocabulary / glossary", "Custom vocabulary"],
            ]
        )
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda *a, **kw: response,
        )
        groups = _find_duplicate_groups(
            [
                "Custom vocabulary / glossary",
                "Custom vocabulary",
                "Search",
            ]
        )
        assert len(groups) == 1
        assert set(groups[0]) == {
            "Custom vocabulary / glossary",
            "Custom vocabulary",
        }

    def test_returns_empty_on_cli_error(self, monkeypatch):
        from duplo.claude_cli import ClaudeCliError

        def fail(*a, **kw):
            raise ClaudeCliError("fail", 1, "err")

        monkeypatch.setattr("duplo.claude_cli.query", fail)
        assert _find_duplicate_groups(["A", "B"]) == []

    def test_returns_empty_on_bad_json(self, monkeypatch):
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda *a, **kw: "not json",
        )
        assert _find_duplicate_groups(["A", "B"]) == []

    def test_ignores_singletons(self, monkeypatch):
        response = json.dumps([["OnlyOne"], ["A", "B"]])
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda *a, **kw: response,
        )
        groups = _find_duplicate_groups(["OnlyOne", "A", "B"])
        assert len(groups) == 1
        assert set(groups[0]) == {"A", "B"}


class TestMergeDuplicateGroup:
    """Tests for _merge_duplicate_group()."""

    def test_keeps_longest_name(self):
        features = [
            {"name": "Custom vocabulary", "description": "d", "status": "pending"},
            {
                "name": "Custom vocabulary / glossary",
                "description": "d",
                "status": "pending",
            },
            {"name": "Search", "description": "s", "status": "pending"},
        ]
        kept = _merge_duplicate_group(
            features,
            ["Custom vocabulary", "Custom vocabulary / glossary"],
        )
        assert kept == "Custom vocabulary / glossary"
        names = [f["name"] for f in features]
        assert "Custom vocabulary" not in names
        assert "Custom vocabulary / glossary" in names
        assert "Search" in names

    def test_preserves_implemented_status(self):
        features = [
            {
                "name": "API keys",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 1",
            },
            {
                "name": "Bring your own API keys",
                "description": "d",
                "status": "pending",
                "implemented_in": "",
            },
        ]
        kept = _merge_duplicate_group(
            features,
            ["API keys", "Bring your own API keys"],
        )
        assert kept == "Bring your own API keys"
        assert len(features) == 1
        assert features[0]["status"] == "implemented"
        assert features[0]["implemented_in"] == "Phase 1"

    def test_returns_none_when_fewer_than_two_members(self):
        features = [
            {"name": "Auth", "description": "d", "status": "pending"},
        ]
        result = _merge_duplicate_group(features, ["Auth", "Nonexistent"])
        assert result is None
        assert len(features) == 1


class TestSaveFeaturesSemanticDedup:
    """Tests for save_features() semantic dedup (post-merge pass)."""

    def _write_features(self, tmp_path, features_data):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"features": features_data}), encoding="utf-8")

    def test_merges_near_duplicates_across_runs(self, tmp_path, monkeypatch, capsys):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Custom vocabulary",
                    "description": "Add custom words.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        )

        # Stub the candidate-vs-existing dedup to let the new feature through.
        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {},
        )
        # Stub the post-merge dedup to find the near-duplicate pair.
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [["Custom vocabulary", "Custom vocabulary / glossary"]],
        )

        new = [
            Feature(
                name="Custom vocabulary / glossary",
                description="Add custom words and glossary.",
                category="core",
            ),
        ]
        save_features(new, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]
        assert len(names) == 1
        assert names[0] == "Custom vocabulary / glossary"
        # Implemented status preserved from the shorter-named original.
        assert data["features"][0]["status"] == "implemented"
        assert data["features"][0]["implemented_in"] == "Phase 1"

        captured = capsys.readouterr()
        assert "Merged 1 duplicate feature(s)." in captured.out

    def test_no_merge_message_when_no_duplicates(self, tmp_path, monkeypatch, capsys):
        self._write_features(tmp_path, [])
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [],
        )
        new = [Feature(name="Auth", description="Login.", category="core")]
        save_features(new, target_dir=tmp_path)

        captured = capsys.readouterr()
        assert "Merged" not in captured.out

    def test_skips_dedup_when_no_candidates(self, tmp_path, monkeypatch):
        """When all new features are exact matches, still run post-merge."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Authentication",
                    "description": "Login system.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [["Auth", "Authentication"]],
        )

        # Pass an exact duplicate — skipped, but post-merge still runs.
        save_features(
            [Feature(name="Auth", description="Login.", category="core")],
            target_dir=tmp_path,
        )
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["features"]) == 1
        assert data["features"][0]["name"] == "Authentication"


class TestPropagateImplementedStatus:
    """Tests for _propagate_implemented_status()."""

    def test_marks_pending_duplicate_as_implemented(self, monkeypatch):
        features = [
            {
                "name": "Local on-device transcription",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 2",
            },
            {
                "name": "Local offline transcription",
                "description": "d",
                "status": "pending",
                "implemented_in": "",
            },
        ]
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda prompt, system=None, model=None: json.dumps(
                {"Local offline transcription": "Local on-device transcription"}
            ),
        )
        marked = _propagate_implemented_status(features)
        assert marked == ["Local offline transcription"]
        assert features[1]["status"] == "implemented"
        assert features[1]["implemented_in"] == "Phase 2"

    def test_no_matches_returns_empty(self, monkeypatch):
        features = [
            {
                "name": "Auth",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 1",
            },
            {"name": "Search", "description": "d", "status": "pending", "implemented_in": ""},
        ]
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda prompt, system=None, model=None: "{}",
        )
        marked = _propagate_implemented_status(features)
        assert marked == []
        assert features[1]["status"] == "pending"

    def test_no_pending_features_skips_llm(self):
        features = [
            {
                "name": "Auth",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 1",
            },
        ]
        # No monkeypatch needed — should return early without calling LLM.
        marked = _propagate_implemented_status(features)
        assert marked == []

    def test_no_implemented_features_skips_llm(self):
        features = [
            {"name": "Auth", "description": "d", "status": "pending", "implemented_in": ""},
        ]
        marked = _propagate_implemented_status(features)
        assert marked == []

    def test_llm_failure_returns_empty(self, monkeypatch):
        from duplo.claude_cli import ClaudeCliError

        features = [
            {
                "name": "Auth",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 1",
            },
            {"name": "Login", "description": "d", "status": "pending", "implemented_in": ""},
        ]

        def fail(prompt, system=None, model=None):
            raise ClaudeCliError("fail", 1)

        monkeypatch.setattr("duplo.claude_cli.query", fail)
        marked = _propagate_implemented_status(features)
        assert marked == []
        assert features[1]["status"] == "pending"

    def test_ignores_invalid_impl_name(self, monkeypatch):
        """LLM returns a mapping to a non-existent implemented feature."""
        features = [
            {
                "name": "Auth",
                "description": "d",
                "status": "implemented",
                "implemented_in": "Phase 1",
            },
            {"name": "Login", "description": "d", "status": "pending", "implemented_in": ""},
        ]
        monkeypatch.setattr(
            "duplo.claude_cli.query",
            lambda prompt, system=None, model=None: json.dumps({"Login": "Nonexistent feature"}),
        )
        marked = _propagate_implemented_status(features)
        assert marked == []
        assert features[1]["status"] == "pending"


class TestSaveFeaturesFullDedupPipeline:
    """End-to-end tests for save_features() dedup pipeline.

    Provides realistic feature lists with known near-duplicates, mocks
    all LLM calls, and verifies the merged list has no duplicates with
    statuses preserved.
    """

    def _write_features(self, tmp_path, features_data):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"features": features_data}), encoding="utf-8")

    def test_multiple_duplicate_groups_merged(self, tmp_path, monkeypatch, capsys):
        """Three groups of near-duplicates collapse to one feature each."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Custom vocabulary",
                    "description": "Add custom words.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "Bring-your-own API keys",
                    "description": "User provides keys.",
                    "category": "integrations",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Dark mode",
                    "description": "Dark theme.",
                    "category": "ui",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )

        new_features = [
            Feature(
                name="Custom vocabulary / glossary",
                description="Add custom words and glossary terms.",
                category="core",
            ),
            Feature(
                name="Bring your own API keys (BYOK)",
                description="User provides their own keys.",
                category="integrations",
            ),
            Feature(
                name="Dark mode / night theme",
                description="Dark color scheme.",
                category="ui",
            ),
        ]

        # Let all candidates through the initial dedup pass.
        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {},
        )
        # Return three groups of duplicates.
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [
                ["Custom vocabulary", "Custom vocabulary / glossary"],
                ["Bring-your-own API keys", "Bring your own API keys (BYOK)"],
                ["Dark mode", "Dark mode / night theme"],
            ],
        )
        # No propagation needed (separate test covers that).
        monkeypatch.setattr(
            "duplo.saver._propagate_implemented_status",
            lambda feats: [],
        )

        save_features(new_features, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]
        assert len(names) == 3
        # Longest names kept.
        assert "Custom vocabulary / glossary" in names
        assert "Bring your own API keys (BYOK)" in names
        assert "Dark mode / night theme" in names
        # Short duplicates gone.
        assert "Custom vocabulary" not in names
        assert "Bring-your-own API keys" not in names
        assert "Dark mode" not in names

        # Implemented status preserved from "Custom vocabulary".
        vocab = next(f for f in data["features"] if "glossary" in f["name"])
        assert vocab["status"] == "implemented"
        assert vocab["implemented_in"] == "Phase 1"

        captured = capsys.readouterr()
        assert "Merged 3 duplicate feature(s)." in captured.out

    def test_candidate_dedup_blocks_duplicates(self, tmp_path, monkeypatch):
        """Candidate-vs-existing LLM dedup prevents adding a semantic dup."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "CLI tool",
                    "description": "Command-line interface.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        )

        new_features = [
            Feature(
                name="Command-line interface (CLI)",
                description="CLI for the tool.",
                category="core",
            ),
            Feature(
                name="Search",
                description="Full-text search.",
                category="core",
            ),
        ]

        # LLM says "Command-line interface (CLI)" duplicates "CLI tool".
        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {"Command-line interface (CLI)": "CLI tool"},
        )
        # No post-merge duplicates.
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [],
        )
        monkeypatch.setattr(
            "duplo.saver._propagate_implemented_status",
            lambda feats: [],
        )

        save_features(new_features, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]
        assert len(names) == 2
        assert "CLI tool" in names
        assert "Search" in names
        assert "Command-line interface (CLI)" not in names

    def test_propagation_marks_pending_after_merge(self, tmp_path, monkeypatch, capsys):
        """After merging, propagation marks a pending dup of an implemented one."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Local on-device transcription",
                    "description": "On-device speech-to-text.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 2",
                },
            ],
        )

        new_features = [
            Feature(
                name="Local offline transcription",
                description="Offline speech-to-text.",
                category="core",
            ),
        ]

        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {},
        )
        # No post-merge name duplicates — they have distinct names.
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [],
        )

        # Propagation recognizes the pending feature duplicates the implemented one.
        def fake_propagate(feats):
            for f in feats:
                if f["name"] == "Local offline transcription":
                    f["status"] = "implemented"
                    f["implemented_in"] = "Phase 2"
                    return ["Local offline transcription"]
            return []

        monkeypatch.setattr(
            "duplo.saver._propagate_implemented_status",
            fake_propagate,
        )

        save_features(new_features, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        offline = next(f for f in data["features"] if f["name"] == "Local offline transcription")
        assert offline["status"] == "implemented"
        assert offline["implemented_in"] == "Phase 2"

        captured = capsys.readouterr()
        assert "Marked 1 feature(s) as implemented" in captured.out

    def test_full_pipeline_merge_then_propagate(self, tmp_path, monkeypatch, capsys):
        """End-to-end: merge duplicates, then propagate implemented status."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Authentication.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "Export CSV",
                    "description": "CSV export.",
                    "category": "data",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )

        new_features = [
            Feature(
                name="Authentication / login",
                description="Login and auth.",
                category="core",
            ),
            Feature(
                name="CSV data export",
                description="Export data as CSV.",
                category="data",
            ),
            Feature(
                name="Search",
                description="Full-text search.",
                category="core",
            ),
        ]

        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {},
        )
        # "Auth" and "Authentication / login" are near-duplicates;
        # "Export CSV" and "CSV data export" are near-duplicates.
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [
                ["Auth", "Authentication / login"],
                ["Export CSV", "CSV data export"],
            ],
        )

        # After merge, "Authentication / login" is implemented (from "Auth").
        # "CSV data export" kept longer name; propagation not needed (both pending).
        # But let's say Search is semantically identical to nothing — no propagation.
        monkeypatch.setattr(
            "duplo.saver._propagate_implemented_status",
            lambda feats: [],
        )

        save_features(new_features, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]

        # 3 features after merging 2 groups from 5 total.
        assert len(names) == 3
        assert "Authentication / login" in names
        assert "CSV data export" in names
        assert "Search" in names
        assert "Auth" not in names
        assert "Export CSV" not in names

        # "Authentication / login" inherited implemented status from "Auth".
        auth = next(f for f in data["features"] if f["name"] == "Authentication / login")
        assert auth["status"] == "implemented"
        assert auth["implemented_in"] == "Phase 1"

        captured = capsys.readouterr()
        assert "Merged 2 duplicate feature(s)." in captured.out

    def test_no_duplicates_no_merge_no_propagation(self, tmp_path, monkeypatch, capsys):
        """Clean feature list with no duplicates passes through unchanged."""
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        )

        new_features = [
            Feature(name="Search", description="Full-text search.", category="core"),
            Feature(name="Export", description="Data export.", category="data"),
        ]

        monkeypatch.setattr(
            "duplo.saver._deduplicate_features_llm",
            lambda cands, exist: {},
        )
        monkeypatch.setattr(
            "duplo.saver._find_duplicate_groups",
            lambda names: [],
        )
        monkeypatch.setattr(
            "duplo.saver._propagate_implemented_status",
            lambda feats: [],
        )

        save_features(new_features, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        names = [f["name"] for f in data["features"]]
        assert len(names) == 3
        assert set(names) == {"Auth", "Search", "Export"}

        captured = capsys.readouterr()
        assert "Merged" not in captured.out
        assert "Marked" not in captured.out


class TestSaveFeatureStatus:
    """Tests for save_feature_status()."""

    def _write_features(self, tmp_path, features_data):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"features": features_data}), encoding="utf-8")

    def test_updates_existing_feature(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        save_feature_status("Auth", "implemented", "Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        feat = data["features"][0]
        assert feat["status"] == "implemented"
        assert feat["implemented_in"] == "Phase 1"

    def test_updates_only_matching_feature(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Search",
                    "description": "Find.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        save_feature_status("Search", "partial", "Phase 2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        auth = [f for f in data["features"] if f["name"] == "Auth"][0]
        search = [f for f in data["features"] if f["name"] == "Search"][0]
        assert auth["status"] == "pending"
        assert auth["implemented_in"] == ""
        assert search["status"] == "partial"
        assert search["implemented_in"] == "Phase 2"

    def test_updates_feature_without_status_field(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                },
            ],
        )
        save_feature_status("Auth", "implemented", "Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        feat = data["features"][0]
        assert feat["status"] == "implemented"
        assert feat["implemented_in"] == "Phase 1"

    def test_raises_on_unknown_feature(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        with pytest.raises(ValueError, match="No feature named"):
            save_feature_status("Nope", "implemented", "Phase 1", target_dir=tmp_path)

    def test_raises_on_invalid_status(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        with pytest.raises(ValueError, match="Invalid status"):
            save_feature_status("Auth", "done", "Phase 1", target_dir=tmp_path)

    def test_raises_on_empty_features_list(self, tmp_path):
        self._write_features(tmp_path, [])
        with pytest.raises(ValueError, match="No feature named"):
            save_feature_status("Auth", "implemented", "Phase 1", target_dir=tmp_path)

    def test_preserves_other_data(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(
            json.dumps(
                {
                    "source_url": "https://example.com",
                    "features": [
                        {
                            "name": "Auth",
                            "description": "Login.",
                            "category": "core",
                            "status": "pending",
                            "implemented_in": "",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        save_feature_status("Auth", "implemented", "Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["source_url"] == "https://example.com"


class TestSaveIssueAndResolve:
    """Tests for save_issue and resolve_issue."""

    def test_save_issue_creates_entry(self, tmp_path):
        save_issue("Crash on save", "test failure", "Phase 1", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["issues"]) == 1
        issue = data["issues"][0]
        assert issue["description"] == "Crash on save"
        assert issue["source"] == "test failure"
        assert issue["phase"] == "Phase 1"
        assert issue["status"] == "open"
        assert "added_at" in issue

    def test_save_issue_appends_multiple(self, tmp_path):
        save_issue("Bug A", "visual comparison", "Phase 1", target_dir=tmp_path)
        save_issue("Bug B", "test failure", "Phase 2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["issues"]) == 2
        assert data["issues"][0]["description"] == "Bug A"
        assert data["issues"][1]["description"] == "Bug B"

    def test_save_issue_skips_duplicate(self, tmp_path):
        save_issue("Same bug", "tests", "Phase 1", target_dir=tmp_path)
        save_issue("Same bug", "visual", "Phase 2", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert len(data["issues"]) == 1

    def test_save_issue_preserves_other_keys(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"source_url": "https://example.com"}), encoding="utf-8")
        save_issue("Bug", "tests", "Phase 1", target_dir=tmp_path)
        data = json.loads(path.read_text())
        assert data["source_url"] == "https://example.com"
        assert len(data["issues"]) == 1

    def test_resolve_issue_sets_resolved(self, tmp_path):
        save_issue("Broken layout", "visual comparison", "Phase 1", target_dir=tmp_path)
        resolve_issue("Broken layout", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        issue = data["issues"][0]
        assert issue["status"] == "resolved"
        assert "resolved_at" in issue

    def test_resolve_issue_raises_on_missing(self, tmp_path):
        save_issue("Existing bug", "tests", "Phase 1", target_dir=tmp_path)
        with pytest.raises(ValueError, match="No issue with description"):
            resolve_issue("Nonexistent bug", target_dir=tmp_path)

    def test_resolve_issue_raises_on_empty(self, tmp_path):
        (tmp_path / ".duplo").mkdir(parents=True, exist_ok=True)
        (tmp_path / DUPLO_JSON).write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="No issue with description"):
            resolve_issue("Bug", target_dir=tmp_path)

    def test_resolve_leaves_other_issues_unchanged(self, tmp_path):
        save_issue("Bug A", "tests", "Phase 1", target_dir=tmp_path)
        save_issue("Bug B", "visual", "Phase 1", target_dir=tmp_path)
        resolve_issue("Bug A", target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["issues"][0]["status"] == "resolved"
        assert data["issues"][1]["status"] == "open"


class TestMarkImplementedFeatures:
    """Tests for mark_implemented_features()."""

    def _write_features(self, tmp_path, features_data):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"features": features_data}), encoding="utf-8")

    def test_marks_single_feature(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                }
            ],
        )
        tasks = [CompletedTask(text="Add login", features=["Auth"])]
        marked = mark_implemented_features(tasks, "Phase 1", target_dir=tmp_path)
        assert marked == ["Auth"]
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["features"][0]["status"] == "implemented"
        assert data["features"][0]["implemented_in"] == "Phase 1"

    def test_marks_multiple_features(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Search",
                    "description": "Find things.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        tasks = [
            CompletedTask(text="Add login", features=["Auth"]),
            CompletedTask(text="Add search", features=["Search"]),
        ]
        marked = mark_implemented_features(tasks, "Phase 2", target_dir=tmp_path)
        assert set(marked) == {"Auth", "Search"}
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert all(f["status"] == "implemented" for f in data["features"])

    def test_deduplicates_feature_names(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                }
            ],
        )
        tasks = [
            CompletedTask(text="Add login form", features=["Auth"]),
            CompletedTask(text="Wire up auth backend", features=["Auth"]),
        ]
        marked = mark_implemented_features(tasks, "Phase 1", target_dir=tmp_path)
        assert marked == ["Auth"]

    def test_skips_unknown_features(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                }
            ],
        )
        tasks = [
            CompletedTask(text="Add login", features=["Auth"]),
            CompletedTask(text="Add magic", features=["Nonexistent"]),
        ]
        marked = mark_implemented_features(tasks, "Phase 1", target_dir=tmp_path)
        assert marked == ["Auth"]

    def test_empty_tasks(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                }
            ],
        )
        marked = mark_implemented_features([], "Phase 1", target_dir=tmp_path)
        assert marked == []

    def test_tasks_without_features(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Auth",
                    "description": "Login.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                }
            ],
        )
        tasks = [CompletedTask(text="Set up project")]
        marked = mark_implemented_features(tasks, "Phase 1", target_dir=tmp_path)
        assert marked == []

    def test_multi_feature_annotation(self, tmp_path):
        self._write_features(
            tmp_path,
            [
                {
                    "name": "Recording",
                    "description": "Record audio.",
                    "category": "core",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Shortcuts",
                    "description": "Key bindings.",
                    "category": "ui",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        )
        tasks = [
            CompletedTask(
                text="Add recording with hotkey",
                features=["Recording", "Shortcuts"],
            ),
        ]
        marked = mark_implemented_features(tasks, "Phase 3", target_dir=tmp_path)
        assert set(marked) == {"Recording", "Shortcuts"}

    def test_features_missing_status_field(self, tmp_path):
        """Features without a status field should still be updatable."""
        self._write_features(
            tmp_path,
            [{"name": "Auth", "description": "Login.", "category": "core"}],
        )
        tasks = [CompletedTask(text="Add login", features=["Auth"])]
        marked = mark_implemented_features(tasks, "Phase 1", target_dir=tmp_path)
        assert marked == ["Auth"]
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["features"][0]["status"] == "implemented"


class TestResolveCompletedFixes:
    """Tests for resolve_completed_fixes()."""

    def _write_issues(self, tmp_path, issues_data):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(parents=True, exist_ok=True)
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"issues": issues_data}), encoding="utf-8")

    def test_resolves_single_fix(self, tmp_path):
        self._write_issues(
            tmp_path,
            [{"description": "button misaligned", "status": "open"}],
        )
        tasks = [CompletedTask(text="Fix button", fixes=["button misaligned"])]
        resolved = resolve_completed_fixes(tasks, target_dir=tmp_path)
        assert resolved == ["button misaligned"]
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["issues"][0]["status"] == "resolved"
        assert "resolved_at" in data["issues"][0]

    def test_resolves_multiple_fixes(self, tmp_path):
        self._write_issues(
            tmp_path,
            [
                {"description": "button misaligned", "status": "open"},
                {"description": "color wrong", "status": "open"},
            ],
        )
        tasks = [
            CompletedTask(text="Fix button", fixes=["button misaligned"]),
            CompletedTask(text="Fix color", fixes=["color wrong"]),
        ]
        resolved = resolve_completed_fixes(tasks, target_dir=tmp_path)
        assert set(resolved) == {"button misaligned", "color wrong"}
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert all(i["status"] == "resolved" for i in data["issues"])

    def test_deduplicates_fix_descriptions(self, tmp_path):
        self._write_issues(
            tmp_path,
            [{"description": "button misaligned", "status": "open"}],
        )
        tasks = [
            CompletedTask(text="Fix button layout", fixes=["button misaligned"]),
            CompletedTask(text="Adjust button CSS", fixes=["button misaligned"]),
        ]
        resolved = resolve_completed_fixes(tasks, target_dir=tmp_path)
        assert resolved == ["button misaligned"]

    def test_skips_unknown_issues(self, tmp_path):
        self._write_issues(
            tmp_path,
            [{"description": "button misaligned", "status": "open"}],
        )
        tasks = [
            CompletedTask(text="Fix button", fixes=["button misaligned"]),
            CompletedTask(text="Fix ghost", fixes=["nonexistent issue"]),
        ]
        resolved = resolve_completed_fixes(tasks, target_dir=tmp_path)
        assert resolved == ["button misaligned"]

    def test_empty_tasks(self, tmp_path):
        self._write_issues(
            tmp_path,
            [{"description": "button misaligned", "status": "open"}],
        )
        resolved = resolve_completed_fixes([], target_dir=tmp_path)
        assert resolved == []

    def test_tasks_without_fixes(self, tmp_path):
        self._write_issues(
            tmp_path,
            [{"description": "button misaligned", "status": "open"}],
        )
        tasks = [CompletedTask(text="Add login", features=["Auth"])]
        resolved = resolve_completed_fixes(tasks, target_dir=tmp_path)
        assert resolved == []

    def test_leaves_other_issues_unchanged(self, tmp_path):
        self._write_issues(
            tmp_path,
            [
                {"description": "button misaligned", "status": "open"},
                {"description": "font too small", "status": "open"},
            ],
        )
        tasks = [CompletedTask(text="Fix button", fixes=["button misaligned"])]
        resolve_completed_fixes(tasks, target_dir=tmp_path)
        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["issues"][1]["status"] == "open"


class TestSaveRoadmap:
    """Tests for save_roadmap."""

    def test_saves_roadmap_and_resets_current_phase(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        path = duplo_dir / "duplo.json"
        path.write_text(json.dumps({"current_phase": 5, "features": []}), encoding="utf-8")

        roadmap = [
            {"phase": 0, "title": "A", "goal": "g", "features": [], "test": "t"},
            {"phase": 1, "title": "B", "goal": "g", "features": [], "test": "t"},
        ]
        save_roadmap(roadmap, target_dir=tmp_path)

        data = json.loads(path.read_text())
        assert data["roadmap"] == roadmap
        assert data["current_phase"] == 0
        assert data["features"] == []

    def test_creates_duplo_json_if_missing(self, tmp_path):
        roadmap = [{"phase": 0, "title": "X", "goal": "g", "features": [], "test": "t"}]
        save_roadmap(roadmap, target_dir=tmp_path)

        data = json.loads((tmp_path / DUPLO_JSON).read_text())
        assert data["roadmap"] == roadmap
        assert data["current_phase"] == 0


class TestAppendPhaseToHistoryStageRegex:
    """Tests that append_phase_to_history accepts Stage headings."""

    def test_stage_heading_extracted(self, tmp_path):
        plan_content = "# MyApp — Stage 1: Core\n\n- [x] Build it\n"
        result_path = append_phase_to_history(plan_content, target_dir=tmp_path)
        data = json.loads(result_path.read_text(encoding="utf-8"))
        phase_title = data["phases"][-1]["phase"]
        assert "Stage 1" in phase_title

    def test_phase_heading_still_works(self, tmp_path):
        plan_content = "# MyApp — Phase 3: Polish\n\n- [x] Fix bugs\n"
        result_path = append_phase_to_history(plan_content, target_dir=tmp_path)
        data = json.loads(result_path.read_text(encoding="utf-8"))
        phase_title = data["phases"][-1]["phase"]
        assert "Phase 3" in phase_title


class TestWriteClaudeMd:
    @pytest.fixture()
    def swift_profile(self) -> PlatformProfile:
        return PlatformProfile(
            id="macos-swiftui-spm",
            display_name="macOS SwiftUI (SPM)",
            claude_md_rules=[
                "Run ./run.sh to launch the app bundle.",
                "Do not invoke .build/debug/<binary> directly.",
            ],
            failure_modes=["Running binary directly produces no UI."],
        )

    @pytest.fixture()
    def swift_prefs(self) -> BuildPreferences:
        return BuildPreferences(
            platform="macos",
            language="Swift/SwiftUI",
            constraints=["macOS 14+"],
            preferences=["build: Swift Package Manager"],
        )

    def test_creates_file_at_project_root(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        assert path == (tmp_path / CLAUDE_MD_FILENAME).resolve()
        assert path.exists()

    def test_contains_project_name_heading(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# MyApp")

    def test_contains_stack_section_with_prefs(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "## Stack" in content
        assert "macos / Swift/SwiftUI" in content
        assert "build: Swift Package Manager" in content
        assert "constraint: macOS 14+" in content

    def test_contains_platform_rules_section(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "## Platform rules" in content
        assert "Run ./run.sh to launch the app bundle." in content
        assert "Running binary directly produces no UI." in content

    def test_omits_platform_rules_when_no_profiles(self, tmp_path, swift_prefs):
        path = write_claude_md([], [swift_prefs], "MyApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "## Platform rules" not in content
        assert "## Stack" in content

    def test_local_overrides_included_when_provided(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md(
            [swift_profile],
            [swift_prefs],
            "MyApp",
            local_md_content="Prefer 4-space indentation.",
            target_dir=tmp_path,
        )
        content = path.read_text(encoding="utf-8")
        assert "## Local overrides" in content
        assert "Prefer 4-space indentation." in content

    def test_local_overrides_absent_when_blank(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md(
            [swift_profile],
            [swift_prefs],
            "MyApp",
            local_md_content="   \n\n",
            target_dir=tmp_path,
        )
        content = path.read_text(encoding="utf-8")
        assert "## Local overrides" not in content

    def test_multiple_stacks_enumerated(self, tmp_path, swift_profile):
        prefs_list = [
            BuildPreferences(platform="macos", language="Swift", preferences=[]),
            BuildPreferences(platform="web", language="TypeScript", preferences=[]),
        ]
        path = write_claude_md([swift_profile], prefs_list, "MultiApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "Stack 1: macos / Swift" in content
        assert "Stack 2: web / TypeScript" in content

    def test_overwrites_existing_file(self, tmp_path, swift_profile, swift_prefs):
        existing = tmp_path / CLAUDE_MD_FILENAME
        existing.write_text("stale hand-edited content\n", encoding="utf-8")
        write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        content = existing.read_text(encoding="utf-8")
        assert "stale hand-edited content" not in content
        assert "# MyApp" in content

    def test_file_ends_with_single_newline(self, tmp_path, swift_profile, swift_prefs):
        path = write_claude_md([swift_profile], [swift_prefs], "MyApp", target_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_empty_preferences_list(self, tmp_path):
        path = write_claude_md([], [], "MyApp", target_dir=tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "# MyApp" in content
        assert "## Stack" in content


class TestProcessedVideosManifest:
    """load_processed_videos / record_processed_videos round-trips."""

    def test_load_missing_file_returns_empty(self, tmp_path):
        from duplo.saver import load_processed_videos

        assert load_processed_videos(target_dir=tmp_path) == {}

    def test_load_corrupted_file_returns_empty(self, tmp_path):
        from duplo.saver import PROCESSED_VIDEOS_JSON, load_processed_videos

        path = tmp_path / PROCESSED_VIDEOS_JSON
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_processed_videos(target_dir=tmp_path) == {}

    def test_record_then_load_round_trip(self, tmp_path):
        from duplo.saver import load_processed_videos, record_processed_videos

        record_processed_videos({"ref/a.mp4": "hash-a"}, target_dir=tmp_path)
        assert load_processed_videos(target_dir=tmp_path) == {"ref/a.mp4": "hash-a"}

    def test_record_merges_and_overwrites(self, tmp_path):
        from duplo.saver import load_processed_videos, record_processed_videos

        record_processed_videos(
            {"ref/a.mp4": "hash-a", "ref/b.mp4": "hash-b"},
            target_dir=tmp_path,
        )
        record_processed_videos({"ref/b.mp4": "hash-b2"}, target_dir=tmp_path)
        assert load_processed_videos(target_dir=tmp_path) == {
            "ref/a.mp4": "hash-a",
            "ref/b.mp4": "hash-b2",
        }

    def test_non_string_values_dropped_on_load(self, tmp_path):
        from duplo.saver import PROCESSED_VIDEOS_JSON, load_processed_videos

        path = tmp_path / PROCESSED_VIDEOS_JSON
        path.parent.mkdir(parents=True)
        path.write_text('{"ref/a.mp4": "h", "ref/bad.mp4": 42}', encoding="utf-8")
        assert load_processed_videos(target_dir=tmp_path) == {"ref/a.mp4": "h"}
