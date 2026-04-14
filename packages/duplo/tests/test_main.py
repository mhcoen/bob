"""Tests for duplo.main CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from duplo.design_extractor import DesignRequirements
from duplo.extractor import Feature
from duplo.fetcher import PageRecord
from duplo.gap_detector import detect_design_gaps
from duplo.main import (
    ScrapeResult,
    UpdateSummary,
    _analyze_new_files,
    _build_completion_history,
    _complete_phase,
    _current_phase_content,
    _detect_and_append_gaps,
    _download_site_media,
    _init_project,
    _investigation_context,
    _load_preferences,
    _partition_features,
    _persist_scrape_result,
    _plan_has_unchecked_tasks,
    _plan_is_complete,
    _prefs_from_dict,
    _print_feature_status,
    _print_status,
    _print_summary,
    _rescrape_product_url,
    _scrape_declared_sources,
    _unimplemented_features,
    _visual_target_video_frames,
    main,
)
from duplo.questioner import BuildPreferences

_DUPLO_JSON = ".duplo/duplo.json"


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


def _read_duplo_json(tmp_path: Path) -> dict:
    """Read duplo.json from the .duplo/ subdirectory of *tmp_path*."""
    return json.loads((tmp_path / _DUPLO_JSON).read_text())


class TestMainFirstRun:
    """First run: no .duplo/duplo.json exists."""

    def test_exits_when_no_reference_materials(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_scans_and_fetches_url(self, tmp_path, monkeypatch):
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "links.txt").write_text("https://example.com")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._validate_url", return_value=("https://example.com", "Example")):
            with patch("duplo.main._confirm_product", return_value="Example"):
                with patch("duplo.main.fetch_site", return_value=("page text", [], None, [], {})):
                    with patch("duplo.main.extract_features", return_value=[]):
                        with patch(
                            "duplo.main.ask_preferences",
                            return_value=BuildPreferences(platform="web", language="Python"),
                        ):
                            with patch("builtins.input", return_value=""):
                                with patch(
                                    "duplo.main.save_selections",
                                    return_value=tmp_path / _DUPLO_JSON,
                                ):
                                    with patch(
                                        "duplo.main.write_claude_md",
                                        return_value=tmp_path / "CLAUDE.md",
                                    ):
                                        with patch(
                                            "duplo.main.generate_roadmap",
                                            return_value=None,
                                        ):
                                            main()

    def test_uses_first_url_as_source(self, tmp_path, monkeypatch, capsys):
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "urls.txt").write_text("https://first.com\nhttps://second.com")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._validate_url", return_value=("https://first.com", "First")):
            with patch("duplo.main._confirm_product", return_value="First"):
                with patch(
                    "duplo.main.fetch_site", return_value=("text", [], None, [], {})
                ) as mock_fetch:
                    with patch("duplo.main.extract_features", return_value=[]):
                        with patch(
                            "duplo.main.ask_preferences",
                            return_value=BuildPreferences(platform="web", language="Python"),
                        ):
                            with patch("builtins.input", return_value=""):
                                with patch(
                                    "duplo.main.save_selections",
                                    return_value=tmp_path / _DUPLO_JSON,
                                ):
                                    with patch(
                                        "duplo.main.write_claude_md",
                                        return_value=tmp_path / "CLAUDE.md",
                                    ):
                                        with patch(
                                            "duplo.main.generate_roadmap", return_value=None
                                        ):
                                            main()

        mock_fetch.assert_called_once_with("https://first.com")

    def test_first_run_with_images_only(self, tmp_path, monkeypatch):
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "screenshot.png").write_bytes(b"PNG")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._confirm_product", return_value="SomeApp"):
            with patch("duplo.main.extract_features", return_value=[]):
                with patch(
                    "duplo.main.ask_preferences",
                    return_value=BuildPreferences(platform="web", language="Python"),
                ):
                    with patch("builtins.input", return_value=""):
                        with patch(
                            "duplo.main.save_selections",
                            return_value=tmp_path / _DUPLO_JSON,
                        ):
                            with patch(
                                "duplo.main.write_claude_md",
                                return_value=tmp_path / "CLAUDE.md",
                            ):
                                with patch("duplo.main.generate_roadmap", return_value=None):
                                    main()

    def test_generates_roadmap_and_executes_phase(self, tmp_path, monkeypatch):
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "notes.txt").write_text("https://example.com")
        monkeypatch.chdir(tmp_path)

        roadmap = [
            {"phase": 0, "title": "Core", "goal": "MVP", "test": "it works"},
        ]
        with patch("duplo.main._validate_url", return_value=("https://example.com", "Example")):
            with patch("duplo.main._confirm_product", return_value="Example"):
                with patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})):
                    with patch("duplo.main.extract_features", return_value=[]):
                        with patch(
                            "duplo.main.ask_preferences",
                            return_value=BuildPreferences(platform="web", language="Python"),
                        ):
                            with patch("builtins.input", return_value=""):
                                with patch(
                                    "duplo.main.save_selections",
                                    return_value=tmp_path / _DUPLO_JSON,
                                ):
                                    with patch(
                                        "duplo.main.write_claude_md",
                                        return_value=tmp_path / "CLAUDE.md",
                                    ):
                                        with patch(
                                            "duplo.main.generate_roadmap",
                                            return_value=roadmap,
                                        ):
                                            with patch("duplo.main.save_roadmap"):
                                                with patch(
                                                    "duplo.main.get_current_phase",
                                                    return_value=(0, {"title": "Core"}),
                                                ):
                                                    with patch(
                                                        "duplo.main.generate_phase_plan",
                                                        return_value="# Phase 0: Core\n",
                                                    ):
                                                        with patch(
                                                            "duplo.main.save_plan",
                                                            return_value=tmp_path / "PLAN.md",
                                                        ):
                                                            with patch(
                                                                "duplo.main.capture_appshot",
                                                                return_value=0,
                                                            ):
                                                                with patch(
                                                                    "duplo.main.notify_phase_complete"
                                                                ):
                                                                    main()

    def test_skips_validation_when_product_json_exists(self, tmp_path, monkeypatch):
        """When .duplo/product.json exists, skip URL validation and product confirmation."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "links.txt").write_text("https://example.com")
        (tmp_path / ".duplo").mkdir()
        (tmp_path / ".duplo" / "product.json").write_text(
            json.dumps({"product_name": "Saved Product", "source_url": "https://saved.com"})
        )
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._validate_url") as mock_validate:
            with patch("duplo.main._confirm_product") as mock_confirm:
                with patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})):
                    with patch("duplo.main.extract_features", return_value=[]):
                        with patch(
                            "duplo.main.ask_preferences",
                            return_value=BuildPreferences(platform="web", language="Python"),
                        ):
                            with patch("builtins.input", return_value=""):
                                with patch(
                                    "duplo.main.save_selections",
                                    return_value=tmp_path / _DUPLO_JSON,
                                ):
                                    with patch(
                                        "duplo.main.write_claude_md",
                                        return_value=tmp_path / "CLAUDE.md",
                                    ):
                                        with patch(
                                            "duplo.main.generate_roadmap",
                                            return_value=None,
                                        ):
                                            main()

        mock_validate.assert_not_called()
        mock_confirm.assert_not_called()

    def test_saves_product_json_after_confirmation(self, tmp_path, monkeypatch):
        """First run without product.json saves it after confirmation."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "links.txt").write_text("https://example.com")
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main._validate_url",
            return_value=("https://example.com", "Example"),
        ):
            with patch("duplo.main._confirm_product", return_value="Example"):
                with patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})):
                    with patch("duplo.main.extract_features", return_value=[]):
                        with patch(
                            "duplo.main.ask_preferences",
                            return_value=BuildPreferences(platform="web", language="Python"),
                        ):
                            with patch("builtins.input", return_value=""):
                                with patch(
                                    "duplo.main.save_selections",
                                    return_value=tmp_path / _DUPLO_JSON,
                                ):
                                    with patch(
                                        "duplo.main.write_claude_md",
                                        return_value=tmp_path / "CLAUDE.md",
                                    ):
                                        with patch(
                                            "duplo.main.generate_roadmap",
                                            return_value=None,
                                        ):
                                            main()

        product_path = tmp_path / ".duplo" / "product.json"
        assert product_path.exists()
        data = json.loads(product_path.read_text())
        assert data["product_name"] == "Example"
        assert data["source_url"] == "https://example.com"


class TestMainSubsequentRun:
    """Subsequent runs: .duplo/duplo.json exists."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [{"name": "Search", "description": "Full-text search.", "category": "core"}],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Search",
                "features": ["Search"],
                "test": "ok",
            },
        ],
        "current_phase": 0,
    }

    def test_generates_and_runs_phase(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md") as mock_save:
                    main()

        mock_save.assert_called_once()

    def test_prints_plan_ready(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    main()

        out = capsys.readouterr().out
        assert "Run mcloop to start building" in out


class TestSubsequentRunResume:
    """Tests for resuming an interrupted phase."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {"phase": 0, "title": "Core", "goal": "Core", "features": [], "test": "ok"},
        ],
        "current_phase": 0,
    }

    def test_skips_plan_generation_when_plan_exists(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan") as mock_gen:
            with patch("duplo.main.notify_phase_complete"):
                main()

        mock_gen.assert_not_called()

    def test_incomplete_plan_prints_run_mcloop(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Task\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        main()

        out = capsys.readouterr().out
        assert "uncompleted tasks" in out
        assert "Run mcloop" in out


class TestPhaseCompletionFlow:
    """Tests for the phase-completion-then-advance flow."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {"phase": 0, "title": "Core", "goal": "Core", "features": [], "test": "ok"},
            {"phase": 1, "title": "Next", "goal": "Next", "features": [], "test": "ok"},
        ],
        "current_phase": 0,
    }

    def test_complete_plan_advances_and_generates_next(self, capsys, tmp_path, monkeypatch):
        """When PLAN.md is all checked, complete phase and generate next plan."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.notify_phase_complete"),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 1: Next\n- [ ] task",
            ) as mock_gen,
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "Completing Phase 0: Core" in out
        assert "Run mcloop to start building" in out
        mock_gen.assert_called_once()

    def test_feedback_collected_during_completion(self, tmp_path, monkeypatch):
        """Feedback is collected and saved when a phase completes."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.collect_feedback", return_value="great work"),
            patch("duplo.main.save_feedback") as mock_save_fb,
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 1\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_save_fb.assert_called_once()
        assert mock_save_fb.call_args.kwargs["after_phase"] == "Phase 0: Core"

    def test_no_plan_generates_current_phase(self, capsys, tmp_path, monkeypatch):
        """When no PLAN.md exists, generate plan for current phase."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 0: Core\n- [ ] task",
            ) as mock_gen,
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "Generating Phase 1: Core PLAN.md" in out
        assert "Run mcloop to start building" in out
        mock_gen.assert_called_once()

    def test_phase_number_from_history(self, tmp_path, monkeypatch):
        """Phase number passed to generate_phase_plan = len(phases) + 1."""
        data = {
            **self._BASE_DATA,
            "phases": [
                {"phase": "Phase 1", "plan": "done", "completed_at": "t1"},
                {"phase": "Phase 2", "plan": "done", "completed_at": "t2"},
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Core\n- [ ] task",
            ) as mock_gen,
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        assert mock_gen.call_args.kwargs["phase_number"] == 3


class TestSubsequentRunFileChanges:
    """Tests for file change detection on subsequent runs."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
    }

    def test_detects_added_file(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        # Save initial hashes with no files.
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        # Add a new file under ref/ that will be detected.
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir(exist_ok=True)
        (ref_dir / "new_ref.png").write_bytes(b"PNG image data")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        out = capsys.readouterr().out
        assert "File changes detected" in out
        assert "+ ref/new_ref.png" in out

    def test_detects_changed_file(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "notes.txt").write_text("original")
        monkeypatch.chdir(tmp_path)

        # Compute and save hashes with original content.
        from duplo.hasher import compute_hashes, save_hashes

        hashes = compute_hashes(tmp_path)
        save_hashes(hashes, directory=tmp_path)

        # Modify the file.
        (tmp_path / "notes.txt").write_text("modified content")

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.extract_features", return_value=[]):
                    main()

        out = capsys.readouterr().out
        assert "File changes detected" in out
        assert "~ notes.txt" in out

    def test_detects_removed_file(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "old.txt").write_text("data")
        monkeypatch.chdir(tmp_path)

        from duplo.hasher import compute_hashes, save_hashes

        hashes = compute_hashes(tmp_path)
        save_hashes(hashes, directory=tmp_path)

        # Remove the file.
        (tmp_path / "old.txt").unlink()

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        out = capsys.readouterr().out
        assert "File changes detected" in out
        assert "- old.txt" in out

    def test_top_level_file_not_analyzed(self, capsys, tmp_path, monkeypatch):
        """Top-level files outside ref/ are detected but not analyzed."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        # Add a new image at project root (NOT in ref/).
        (tmp_path / "stray.png").write_bytes(b"PNG image data")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._analyze_new_files") as mock_analyze:
            mock_analyze.return_value = UpdateSummary()
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    main()

        # File change detected and printed.
        out = capsys.readouterr().out
        assert "File changes detected" in out
        assert "+ stray.png" in out
        # But _analyze_new_files NOT called (file is outside ref/).
        mock_analyze.assert_not_called()

    def test_no_changes_no_message(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        from duplo.hasher import compute_hashes, save_hashes

        hashes = compute_hashes(tmp_path)
        save_hashes(hashes, directory=tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        out = capsys.readouterr().out
        assert "File changes detected" not in out

    def test_updates_hashes_after_detection(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir(exist_ok=True)
        (ref_dir / "new.txt").write_text("hello")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.extract_features", return_value=[]):
                    main()

        # Verify hashes reflect post-move state (ref/new.txt moved to .duplo/references/).
        from duplo.hasher import load_hashes

        saved = load_hashes(tmp_path)
        assert "ref/new.txt" not in saved


class TestInitProject:
    """Test _init_project directly."""

    _FEATURES = [Feature(name="Search", description="Full-text search.", category="core")]
    _PREFS = BuildPreferences(platform="web", language="Python")

    def test_saves_selections(self, tmp_path):
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON) as m:
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                with patch("duplo.main.generate_roadmap", return_value=None):
                    _init_project(
                        url="https://example.com",
                        project_name="example",
                        project_dir=tmp_path,
                        features=self._FEATURES,
                        prefs=self._PREFS,
                        app_name="Example",
                        text="page text",
                        code_examples=None,
                        doc_structures=None,
                    )
        m.assert_called_once()
        assert m.call_args.args[0] == "https://example.com"

    def test_returns_roadmap(self, tmp_path):
        roadmap = [{"phase": 1, "title": "Core"}]
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                with patch("duplo.main.generate_roadmap", return_value=roadmap):
                    result = _init_project(
                        url="https://example.com",
                        project_name="example",
                        project_dir=tmp_path,
                        features=self._FEATURES,
                        prefs=self._PREFS,
                        app_name="Example",
                        text="page text",
                        code_examples=None,
                        doc_structures=None,
                    )
        assert result == roadmap

    def test_returns_none_when_no_roadmap(self, tmp_path):
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                with patch("duplo.main.generate_roadmap", return_value=None):
                    result = _init_project(
                        url="https://example.com",
                        project_name="example",
                        project_dir=tmp_path,
                        features=[],
                        prefs=self._PREFS,
                        app_name="Example",
                        text="page text",
                        code_examples=None,
                        doc_structures=None,
                    )
        assert result is None

    def test_generates_tests_when_code_examples_present(self, tmp_path):
        examples = [{"input": "1+1", "expected_output": "2"}]
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.save_examples"):
                with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                    with patch("duplo.main.generate_roadmap", return_value=None):
                        with patch(
                            "duplo.main.detect_target_language",
                            return_value="Python",
                        ):
                            with patch(
                                "duplo.main.generate_test_source",
                                return_value="test code",
                            ) as m_gen:
                                with patch(
                                    "duplo.main.save_test_file",
                                    return_value=tmp_path / "tests" / "test_doc.py",
                                ) as m_save:
                                    _init_project(
                                        url="https://example.com",
                                        project_name="example",
                                        project_dir=tmp_path,
                                        features=[],
                                        prefs=self._PREFS,
                                        app_name="Example",
                                        text="page text",
                                        code_examples=examples,
                                        doc_structures=None,
                                    )
        m_gen.assert_called_once_with(examples, project_name="example")
        m_save.assert_called_once()

    def test_skips_test_generation_for_swift_project(self, capsys, tmp_path):
        """Package.swift in target dir → no test file generated."""
        (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9")
        examples = [{"input": "1+1", "expected_output": "2"}]
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.save_examples"):
                with patch(
                    "duplo.main.write_claude_md",
                    return_value=tmp_path / "CLAUDE.md",
                ):
                    with patch("duplo.main.generate_roadmap", return_value=None):
                        with patch(
                            "duplo.main.generate_test_source",
                        ) as m_gen:
                            with patch(
                                "duplo.main.save_test_file",
                            ) as m_save:
                                _init_project(
                                    url="https://example.com",
                                    project_name="example",
                                    project_dir=tmp_path,
                                    features=[],
                                    prefs=self._PREFS,
                                    app_name="Example",
                                    text="page text",
                                    code_examples=examples,
                                    doc_structures=None,
                                )
        m_gen.assert_not_called()
        m_save.assert_not_called()
        captured = capsys.readouterr()
        assert "Test generation skipped" in captured.out
        assert "Swift" in captured.out

    def test_writes_claude_md(self, tmp_path):
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md") as m:
                with patch("duplo.main.generate_roadmap", return_value=None):
                    _init_project(
                        url="https://example.com",
                        project_name="example",
                        project_dir=tmp_path,
                        features=[],
                        prefs=self._PREFS,
                        app_name="Example",
                        text="page text",
                        code_examples=None,
                        doc_structures=None,
                    )
        m.assert_called_once_with(target_dir=tmp_path)

    def test_captures_screenshots_from_section_urls(self, tmp_path):
        text = "=== https://example.com/page ===\nContent here."
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                with patch("duplo.main.generate_roadmap", return_value=None):
                    with patch(
                        "duplo.main.save_reference_screenshots",
                        return_value=["shot.png"],
                    ) as m_shot:
                        with patch("duplo.main.map_screenshots_to_features", return_value={}):
                            _init_project(
                                url="https://example.com",
                                project_name="example",
                                project_dir=tmp_path,
                                features=[],
                                prefs=self._PREFS,
                                app_name="Example",
                                text=text,
                                code_examples=None,
                                doc_structures=None,
                            )
        m_shot.assert_called_once()
        assert m_shot.call_args.args[0] == ["https://example.com/page"]

    def test_skips_screenshots_when_no_section_urls(self, tmp_path):
        with patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON):
            with patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"):
                with patch("duplo.main.generate_roadmap", return_value=None):
                    with patch("duplo.main.save_reference_screenshots") as m_shot:
                        _init_project(
                            url="https://example.com",
                            project_name="example",
                            project_dir=tmp_path,
                            features=[],
                            prefs=self._PREFS,
                            app_name="Example",
                            text="no section urls here",
                            code_examples=None,
                            doc_structures=None,
                        )
        m_shot.assert_not_called()


class TestAnalyzeNewFiles:
    """Tests for _analyze_new_files()."""

    def test_extracts_design_from_new_images(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create duplo.json so save_design_requirements can update it.
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "new_screen.png"
        img.write_bytes(b"PNG" * 500)

        design = DesignRequirements(
            colors={"primary": "#fff"},
            fonts={"body": "Arial"},
            source_images=["new_screen.png"],
        )
        with patch("duplo.main.extract_design", return_value=design) as mock_design:
            _analyze_new_files(["new_screen.png"])

        mock_design.assert_called_once()
        data = _read_duplo_json(tmp_path)
        assert data["design_requirements"]["colors"] == {"primary": "#fff"}

    def test_extracts_text_from_new_pdfs(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        pdf = tmp_path / "spec.pdf"
        pdf.write_bytes(b"%PDF" * 100)

        with patch("duplo.main.extract_pdf_text", return_value="PDF content") as mock_pdf:
            _analyze_new_files(["spec.pdf"])

        mock_pdf.assert_called_once()
        out = capsys.readouterr().out
        assert "PDF" in out

    def test_fetches_new_urls(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        txt = tmp_path / "urls.txt"
        txt.write_text("Check https://newsite.com for the product details")

        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, [], {}),
        ) as mock_fetch:
            with patch("duplo.main._load_existing_urls", return_value=set()):
                _analyze_new_files(["urls.txt"])

        mock_fetch.assert_called_once_with("https://newsite.com")
        out = capsys.readouterr().out
        assert "Fetching" in out

    def test_skips_already_fetched_urls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        txt = tmp_path / "urls.txt"
        txt.write_text("Check https://already.com for the product details")

        with patch("duplo.main.fetch_site") as mock_fetch:
            with patch(
                "duplo.main._load_existing_urls",
                return_value={"https://already.com"},
            ):
                _analyze_new_files(["urls.txt"])

        mock_fetch.assert_not_called()

    def test_skips_nonexistent_files(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _analyze_new_files(["nonexistent.png"])
        out = capsys.readouterr().out
        assert out == ""

    def test_moves_references(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "ref.png"
        img.write_bytes(b"PNG" * 500)

        design = DesignRequirements(
            colors={"primary": "#000"},
            source_images=["ref.png"],
        )
        with patch("duplo.main.extract_design", return_value=design):
            _analyze_new_files(["ref.png"])

        assert not img.exists()
        assert (tmp_path / ".duplo" / "references" / "ref.png").exists()

    def test_subsequent_run_analyzes_new_images(self, capsys, tmp_path, monkeypatch):
        """Integration: _subsequent_run triggers analysis of new image files."""
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
            },
        )
        # Save empty hash manifest.
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        # Add a new image under ref/.
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir(exist_ok=True)
        (ref_dir / "new.png").write_bytes(b"PNG" * 500)
        monkeypatch.chdir(tmp_path)

        design = DesignRequirements(colors={"primary": "#abc"}, source_images=["ref/new.png"])
        with patch("duplo.main.extract_design", return_value=design):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    main()

        out = capsys.readouterr().out
        assert "new image" in out.lower() or "Vision" in out
        data = _read_duplo_json(tmp_path)
        assert data["design_requirements"]["colors"] == {"primary": "#abc"}


class TestRescrapeProductUrl:
    """Tests for _rescrape_product_url()."""

    def test_rescrapes_source_url(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        records = [PageRecord("https://example.com", "t", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ) as mock_fetch:
            with patch("duplo.main.save_reference_urls") as mock_save_urls:
                with patch("duplo.main.save_raw_content") as mock_save_raw:
                    _rescrape_product_url()

        mock_fetch.assert_called_once_with("https://example.com")
        mock_save_urls.assert_called_once()
        mock_save_raw.assert_called_once()
        out = capsys.readouterr().out
        assert "Re-scraping" in out
        assert "1 page record" in out

    def test_skips_when_no_source_url(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        with patch("duplo.main.fetch_site") as mock_fetch:
            _rescrape_product_url()

        mock_fetch.assert_not_called()
        assert capsys.readouterr().out == ""

    def test_skips_when_no_duplo_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.fetch_site") as mock_fetch:
            _rescrape_product_url()

        mock_fetch.assert_not_called()

    def test_handles_fetch_failure(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        with patch("duplo.main.fetch_site", side_effect=Exception("network error")):
            _rescrape_product_url()

        out = capsys.readouterr().out
        assert "Failed to re-scrape" in out
        assert "network error" in out

    def test_saves_code_examples(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        examples = [{"input": "1+1", "expected_output": "2", "source_url": "", "language": "py"}]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", examples, None, [], {}),
        ):
            with patch("duplo.main.save_examples") as mock_save_ex:
                _rescrape_product_url()

        mock_save_ex.assert_called_once_with(examples)
        out = capsys.readouterr().out
        assert "1 code example" in out

    def test_skips_when_content_unchanged(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "reference_urls": [
                    {"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"},
                    {"url": "https://example.com/docs", "fetched_at": "t", "content_hash": "def"},
                ],
            },
        )

        records = [
            PageRecord("https://example.com", "t2", "abc"),
            PageRecord("https://example.com/docs", "t2", "def"),
        ]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {}),
        ):
            with patch("duplo.main.save_reference_urls") as mock_save:
                pages, ex, text = _rescrape_product_url()

        mock_save.assert_not_called()
        assert pages == 0
        assert ex == 0
        assert text == ""
        out = capsys.readouterr().out
        assert "Site content unchanged, skipping feature re-extraction." in out

    def test_proceeds_when_content_changed(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "reference_urls": [
                    {"url": "https://example.com", "fetched_at": "t", "content_hash": "old"},
                ],
            },
        )

        records = [PageRecord("https://example.com", "t2", "new")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.main.save_reference_urls") as mock_save:
                with patch("duplo.main.save_raw_content"):
                    pages, ex, text = _rescrape_product_url()

        mock_save.assert_called_once()
        assert pages == 1
        assert text == "text"
        out = capsys.readouterr().out
        assert "unchanged" not in out

    def test_proceeds_when_no_stored_hashes(self, capsys, tmp_path, monkeypatch):
        """First scrape with hashes — no stored reference_urls yet."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"source_url": "https://example.com", "features": []},
        )

        records = [PageRecord("https://example.com", "t", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.main.save_reference_urls") as mock_save:
                with patch("duplo.main.save_raw_content"):
                    pages, ex, text = _rescrape_product_url()

        mock_save.assert_called_once()
        assert pages == 1
        assert text == "text"

    def test_skips_when_recent_scrape(self, capsys, tmp_path, monkeypatch):
        """Skip re-scrape if last_scrape_timestamp is less than 10 minutes old."""
        import time

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "last_scrape_timestamp": time.time() - 120,  # 2 minutes ago
            },
        )

        with patch("duplo.main.fetch_site") as mock_fetch:
            pages, ex, text = _rescrape_product_url()

        mock_fetch.assert_not_called()
        assert pages == 0
        assert ex == 0
        assert text == ""
        out = capsys.readouterr().out
        assert "Using recent scrape data (2 minutes ago)" in out

    def test_rescrapes_when_timestamp_old(self, capsys, tmp_path, monkeypatch):
        """Proceed with re-scrape if last_scrape_timestamp is over 10 minutes."""
        import time

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "last_scrape_timestamp": time.time() - 700,  # ~11.7 minutes ago
            },
        )

        records = [PageRecord("https://example.com", "t", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.main.save_reference_urls"):
                with patch("duplo.main.save_raw_content"):
                    pages, ex, text = _rescrape_product_url()

        assert pages == 1
        out = capsys.readouterr().out
        assert "Re-scraping" in out

    def test_saves_timestamp_after_scrape(self, tmp_path, monkeypatch):
        """Successful scrape writes last_scrape_timestamp to duplo.json."""
        import time

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"source_url": "https://example.com", "features": []},
        )

        records = [PageRecord("https://example.com", "t", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.main.save_reference_urls"):
                with patch("duplo.main.save_raw_content"):
                    _rescrape_product_url()

        data = _read_duplo_json(tmp_path)
        assert "last_scrape_timestamp" in data
        assert time.time() - data["last_scrape_timestamp"] < 5

    def test_saves_timestamp_when_content_unchanged(self, tmp_path, monkeypatch):
        """Timestamp is saved even when content didn't change."""
        import time

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "reference_urls": [
                    {"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"},
                ],
            },
        )

        records = [PageRecord("https://example.com", "t2", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, records, {}),
        ):
            _rescrape_product_url()

        data = _read_duplo_json(tmp_path)
        assert "last_scrape_timestamp" in data
        assert time.time() - data["last_scrape_timestamp"] < 5

    def test_unchanged_content_skips_extract_features(self, capsys, tmp_path, monkeypatch):
        """Integration: when fetch_site returns identical hashes, extract_features is skipped."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "F1", "description": "d", "category": "c"}],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "reference_urls": [
                    {"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"},
                ],
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        # Create PLAN.md with unchecked tasks so _subsequent_run hits State 2 and returns.
        (tmp_path / "PLAN.md").write_text("# Phase 1\n- [ ] Build UI\n", encoding="utf-8")

        records = [PageRecord("https://example.com", "t2", "abc")]
        with (
            patch(
                "duplo.main.fetch_site",
                return_value=("text", [], None, records, {}),
            ),
            patch("duplo.main.extract_features") as mock_extract,
        ):
            main()

        mock_extract.assert_not_called()
        out = capsys.readouterr().out
        assert "Site content unchanged" in out

    def test_recent_timestamp_skips_fetch_site(self, capsys, tmp_path, monkeypatch):
        """Integration: when last_scrape_timestamp < 10 min old, fetch_site is skipped."""
        import time

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "F1", "description": "d", "category": "c"}],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "last_scrape_timestamp": time.time() - 180,  # 3 minutes ago
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        # Create PLAN.md with unchecked tasks so _subsequent_run hits State 2 and returns.
        (tmp_path / "PLAN.md").write_text("# Phase 1\n- [ ] Build UI\n", encoding="utf-8")

        with patch("duplo.main.fetch_site") as mock_fetch:
            main()

        mock_fetch.assert_not_called()
        out = capsys.readouterr().out
        assert "Using recent scrape data (3 minutes ago)" in out

    def test_subsequent_run_calls_rescrape(self, tmp_path, monkeypatch):
        """Integration: _subsequent_run calls _rescrape_product_url."""
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")) as mock_rescrape:
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    main()

        mock_rescrape.assert_called_once()


class TestDetectAndAppendGaps:
    """Tests for _detect_and_append_gaps()."""

    def test_appends_missing_features(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Search", "description": "Full-text search.", "category": "core"}
                ],
            },
        )
        plan = "# Phase 0: Core\n- [ ] Build UI\n"
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[MissingFeature(name="Search", reason="Not in plan")],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "- [ ] Implement Search" in updated
        out = capsys.readouterr().out
        assert "1 gap task" in out

    def test_appends_design_refinements(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "UI", "description": "User interface.", "category": "core"}],
                "design_requirements": {
                    "colors": {"primary": "#ff0000"},
                    "fonts": {},
                    "components": [],
                },
            },
        )
        plan = "# Phase 0: Core\n- [ ] Build UI\n"
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Update color palette: primary: #ff0000" in updated
        out = capsys.readouterr().out
        assert "1 gap task" in out

    def test_no_gaps_prints_covered(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "Search", "description": "search", "category": "core"}],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        out = capsys.readouterr().out
        assert "covered" in out.lower()

    def test_skips_when_no_features(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        (tmp_path / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

        with patch("duplo.main.detect_gaps") as mock_detect:
            _detect_and_append_gaps()

        mock_detect.assert_not_called()

    def test_skips_when_no_plan(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"source_url": "", "features": [{"name": "X", "description": "x", "category": "c"}]},
        )

        with patch("duplo.main.detect_gaps") as mock_detect:
            _detect_and_append_gaps()

        mock_detect.assert_not_called()

    def test_preserves_existing_checked_tasks(self, tmp_path, monkeypatch):
        """Existing checked tasks must survive gap appending unchanged."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "Auth", "description": "Login.", "category": "core"}],
            },
        )
        plan = (
            "# Phase 0: Core\n"
            "- [x] Set up project scaffold\n"
            "- [x] Implement basic routing\n"
            "- [ ] Add error handling\n"
        )
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[MissingFeature(name="Auth", reason="Not covered")],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # Every original line must appear in the updated content.
        for line in plan.strip().splitlines():
            assert line in updated, f"Original line lost: {line!r}"
        # New task appended.
        assert "- [ ] Implement Auth" in updated

    def test_preserves_existing_unchecked_tasks(self, tmp_path, monkeypatch):
        """Existing unchecked tasks must not be removed or altered."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "Export", "description": "CSV export.", "category": "core"}],
            },
        )
        plan = (
            "# Phase 1: Features\n"
            "- [ ] Build search bar\n"
            "- [ ] Implement pagination\n"
            "- [ ] Add dark mode toggle\n"
        )
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[MissingFeature(name="Export", reason="Missing")],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        for line in plan.strip().splitlines():
            assert line in updated, f"Original line lost: {line!r}"
        assert "- [ ] Implement Export" in updated

    def test_preserves_mixed_checked_and_unchecked(self, tmp_path, monkeypatch):
        """A plan with both checked and unchecked tasks must be fully preserved."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Websocket", "description": "Real-time.", "category": "core"}
                ],
                "design_requirements": {
                    "colors": {"accent": "#00ff00"},
                    "fonts": {},
                    "components": [],
                },
            },
        )
        plan = (
            "# Phase 2: Polish\n"
            "- [x] Set up CI pipeline\n"
            "- [ ] Write integration tests\n"
            "- [x] Deploy staging environment\n"
            "- [ ] Performance audit\n"
        )
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[MissingFeature(name="Websocket", reason="Not covered")],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # The original plan content must appear as a prefix (modulo trailing ws).
        assert updated.startswith(plan.rstrip())
        # New tasks appended after existing content.
        assert "- [ ] Implement Websocket" in updated
        assert "Update color palette: accent: #00ff00" in updated

    def test_original_content_is_prefix_of_updated(self, tmp_path, monkeypatch):
        """Updated PLAN.md must start with the original content (append-only)."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "API", "description": "REST API.", "category": "core"}],
            },
        )
        plan = "# Phase 0\n- [x] Done task\n- [ ] Pending task\n"
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[MissingFeature(name="API", reason="Missing")],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        updated = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # The original content (stripped) must be a prefix of the updated file.
        assert updated.startswith(plan.rstrip("\n"))

    def test_scope_exclude_filters_features_before_detect(self, tmp_path, monkeypatch, capsys):
        """Features matching scope_exclude are removed before detect_gaps."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Search", "description": "Full-text search.", "category": "core"},
                    {
                        "name": "Plugin API",
                        "description": "Extension system.",
                        "category": "advanced",
                    },
                    {"name": "Export", "description": "CSV export.", "category": "core"},
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result) as mock_detect:
            _detect_and_append_gaps(scope_exclude=["Plugin API"])

        # detect_gaps should have been called with only 2 features
        # (Plugin API excluded).
        call_features = mock_detect.call_args[0][1]
        names = [f.name for f in call_features]
        assert "Search" in names
        assert "Export" in names
        assert "Plugin API" not in names

    def test_scope_exclude_none_passes_all_features(self, tmp_path, monkeypatch):
        """When scope_exclude is None, all features reach detect_gaps."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Search", "description": "search", "category": "core"},
                    {"name": "Export", "description": "export", "category": "core"},
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n", encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result) as mock_detect:
            _detect_and_append_gaps(scope_exclude=None)

        call_features = mock_detect.call_args[0][1]
        assert len(call_features) == 2

    def test_scope_exclude_filters_by_description(self, tmp_path, monkeypatch):
        """scope_exclude matches against description, not just name."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {
                        "name": "Core Editing",
                        "description": "Rich text editing with plugin API support.",
                        "category": "core",
                    },
                    {
                        "name": "Export",
                        "description": "CSV export.",
                        "category": "core",
                    },
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result) as mock_detect:
            _detect_and_append_gaps(scope_exclude=["plugin API"])

        # "Core Editing" has "plugin API" in its description so it
        # should be excluded even though the name doesn't match.
        call_features = mock_detect.call_args[0][1]
        names = [f.name for f in call_features]
        assert "Export" in names
        assert "Core Editing" not in names

    def test_scope_exclude_all_features_skips_detect(self, tmp_path, monkeypatch):
        """When scope_exclude removes all features, detect_gaps is not called."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Plugin API", "description": "Extensions.", "category": "core"},
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n", encoding="utf-8")

        with patch("duplo.main.detect_gaps") as mock_detect:
            _detect_and_append_gaps(scope_exclude=["Plugin API"])

        mock_detect.assert_not_called()

    def test_subsequent_run_calls_detect_gaps(self, tmp_path, monkeypatch):
        """Integration: _subsequent_run calls _detect_and_append_gaps."""
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [{"name": "Search", "description": "search", "category": "core"}],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {
                        "phase": 0,
                        "title": "Core",
                        "goal": "Core",
                        "features": ["Search"],
                        "test": "ok",
                    },
                ],
                "current_phase": 0,
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)) as mock_gaps:
            with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
                with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        main()

        mock_gaps.assert_called_once()


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


class TestAnalyzeNewFilesReturnsSummary:
    """Tests that _analyze_new_files returns an UpdateSummary."""

    def test_returns_empty_for_nonexistent_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _analyze_new_files(["nonexistent.png"])
        assert isinstance(result, UpdateSummary)
        assert result.images_analyzed == 0

    def test_returns_image_count(self, tmp_path, monkeypatch):
        (tmp_path / "shot.png").write_bytes(b"PNG" * 500)
        monkeypatch.chdir(tmp_path)
        design = DesignRequirements(colors={"primary": "#abc"}, source_images=["shot.png"])
        with patch("duplo.main.extract_design", return_value=design):
            result = _analyze_new_files(["shot.png"])
        assert result.images_analyzed == 1

    def test_combines_images_and_video_frames(self, tmp_path, monkeypatch):
        """Video frames and user images go through a single extract_design call."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG" * 500)
        vid = tmp_path / "demo.mp4"
        vid.write_bytes(b"MP4" * 500)

        from duplo.frame_describer import FrameDescription
        from duplo.frame_filter import FilterDecision
        from duplo.video_extractor import ExtractionResult

        frame = tmp_path / ".duplo" / "video_frames" / "frame001.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"PNG" * 100)

        vid_result = ExtractionResult(source=vid, frames=[frame])
        decision = FilterDecision(path=frame, keep=True, reason="clear UI")
        desc = FrameDescription(path=frame, state="main view", detail="dashboard")

        design = DesignRequirements(
            colors={"primary": "#abc"},
            source_images=["shot.png", "frame001.png"],
        )

        with (
            patch("duplo.main.extract_all_videos", return_value=[vid_result]),
            patch("duplo.main.filter_frames", return_value=[decision]),
            patch("duplo.main.apply_filter", return_value=[frame]),
            patch("duplo.main.describe_frames", return_value=[desc]),
            patch("duplo.main.store_accepted_frames", return_value=["frame001.png"]),
            patch("duplo.main.extract_design", return_value=design) as mock_design,
        ):
            result = _analyze_new_files(["shot.png", "demo.mp4"])

        # Single call with both image and frame combined.
        mock_design.assert_called_once()
        call_args = mock_design.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0].name == "shot.png"
        assert call_args[1] == frame
        assert result.images_analyzed == 2
        assert result.video_frames_extracted == 1

    def test_video_only_goes_through_design_extraction(self, tmp_path, monkeypatch):
        """Video frames alone trigger design extraction (no user images)."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        vid = tmp_path / "demo.mp4"
        vid.write_bytes(b"MP4" * 500)

        from duplo.frame_describer import FrameDescription
        from duplo.frame_filter import FilterDecision
        from duplo.video_extractor import ExtractionResult

        frame = tmp_path / ".duplo" / "video_frames" / "frame001.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"PNG" * 100)

        vid_result = ExtractionResult(source=vid, frames=[frame])
        decision = FilterDecision(path=frame, keep=True, reason="clear UI")
        desc = FrameDescription(path=frame, state="settings", detail="panel")

        design = DesignRequirements(
            colors={"bg": "#fff"},
            source_images=["frame001.png"],
        )

        with (
            patch("duplo.main.extract_all_videos", return_value=[vid_result]),
            patch("duplo.main.filter_frames", return_value=[decision]),
            patch("duplo.main.apply_filter", return_value=[frame]),
            patch("duplo.main.describe_frames", return_value=[desc]),
            patch("duplo.main.store_accepted_frames", return_value=["frame001.png"]),
            patch("duplo.main.extract_design", return_value=design) as mock_design,
        ):
            result = _analyze_new_files(["demo.mp4"])

        mock_design.assert_called_once()
        call_args = mock_design.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0] == frame
        assert result.images_analyzed == 1
        assert result.videos_found == 1


class TestVisualTargetVideoFrames:
    """Tests for _visual_target_video_frames helper."""

    def test_returns_empty_when_no_spec(self, tmp_path):
        video = tmp_path / "ref" / "demo.mp4"
        frame = tmp_path / ".duplo" / "video_frames" / "demo_scene_0001.png"
        assert _visual_target_video_frames(None, [video], [frame]) == []

    def test_returns_empty_when_no_frames(self, tmp_path):
        from duplo.spec_reader import ProductSpec

        spec = ProductSpec(raw="")
        assert _visual_target_video_frames(spec, [], []) == []

    def test_returns_frames_from_visual_target_videos(self, tmp_path, monkeypatch):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        vt_video = ref_dir / "demo.mp4"
        vt_video.write_bytes(b"MP4")
        other_video = ref_dir / "intro.mp4"
        other_video.write_bytes(b"MP4")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(path=Path("ref/demo.mp4"), roles=["visual-target"]),
                ReferenceEntry(path=Path("ref/intro.mp4"), roles=["behavioral-target"]),
            ],
        )

        vt_frame = tmp_path / ".duplo" / "video_frames" / "demo_scene_0001.png"
        other_frame = tmp_path / ".duplo" / "video_frames" / "intro_scene_0001.png"
        vt_frame.parent.mkdir(parents=True, exist_ok=True)
        vt_frame.write_bytes(b"PNG")
        other_frame.write_bytes(b"PNG")

        result = _visual_target_video_frames(
            spec, [vt_video, other_video], [vt_frame, other_frame]
        )
        assert result == [vt_frame]

    def test_excludes_proposed_visual_target(self, tmp_path, monkeypatch):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        vid = ref_dir / "proposed.mp4"
        vid.write_bytes(b"MP4")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.mp4"),
                    roles=["visual-target"],
                    proposed=True,
                ),
            ],
        )

        frame = tmp_path / ".duplo" / "video_frames" / "proposed_scene_0001.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"PNG")

        result = _visual_target_video_frames(spec, [vid], [frame])
        assert result == []

    def test_non_video_visual_target_ignored(self, tmp_path, monkeypatch):
        """Non-video files (images) with visual-target role are not matched."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "shot.png"
        img.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"]),
            ],
        )

        frame = tmp_path / ".duplo" / "video_frames" / "other_scene_0001.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"PNG")

        result = _visual_target_video_frames(spec, [img], [frame])
        assert result == []


class TestAnalyzeNewFilesWithSpec:
    """Tests for _analyze_new_files with spec-based design input."""

    def test_uses_collect_design_input_when_spec_provided(self, tmp_path, monkeypatch):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "design.png"
        img.write_bytes(b"PNG" * 500)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(path=Path("ref/design.png"), roles=["visual-target"]),
            ],
        )

        design = DesignRequirements(
            colors={"primary": "#abc"},
            source_images=["design.png"],
        )
        with (
            patch(
                "duplo.main.collect_design_input",
                return_value=[img],
            ) as mock_cdi,
            patch("duplo.main.extract_design", return_value=design),
        ):
            _analyze_new_files(["ref/design.png"], spec=spec)

        mock_cdi.assert_called_once()

    def test_falls_back_to_legacy_without_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG" * 500)

        design = DesignRequirements(
            colors={"primary": "#fff"},
            source_images=["shot.png"],
        )
        with patch("duplo.main.extract_design", return_value=design) as mock_design:
            _analyze_new_files(["shot.png"])

        mock_design.assert_called_once()
        call_args = mock_design.call_args[0][0]
        assert any(p.name == "shot.png" for p in call_args)


class TestBehavioralVideoFiltering:
    """Tests that extract_all_videos receives behavioral-target paths only."""

    def test_analyze_new_files_behavioral_only_with_spec(
        self,
        tmp_path,
        monkeypatch,
    ):
        """When spec is present, only behavioral-target videos are extracted."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        beh_vid = ref_dir / "demo.mp4"
        beh_vid.write_bytes(b"MP4" * 500)
        vis_vid = ref_dir / "design.mp4"
        vis_vid.write_bytes(b"MP4" * 500)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/design.mp4"),
                    roles=["visual-target"],
                ),
            ],
        )

        with (
            patch(
                "duplo.main.extract_all_videos",
                return_value=[],
            ) as mock_extract,
            patch("duplo.main.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid, vis_vid],
                pdfs=[],
                text_files=[],
                urls=[],
            )
            _analyze_new_files(
                ["ref/demo.mp4", "ref/design.mp4"],
                spec=spec,
            )

        # Only the behavioral-target video should be passed.
        mock_extract.assert_called_once()
        video_paths = mock_extract.call_args[0][0]
        assert len(video_paths) == 1
        assert video_paths[0].name == "demo.mp4"

    def test_analyze_new_files_all_videos_without_spec(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Without spec, all scanned videos are extracted (fallback)."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        vid = tmp_path / "demo.mp4"
        vid.write_bytes(b"MP4" * 500)

        with (
            patch(
                "duplo.main.extract_all_videos",
                return_value=[],
            ) as mock_extract,
        ):
            _analyze_new_files(["demo.mp4"])

        mock_extract.assert_called_once()
        video_paths = mock_extract.call_args[0][0]
        assert len(video_paths) == 1
        assert video_paths[0].name == "demo.mp4"

    def test_analyze_new_files_no_behavioral_videos_skips_extraction(
        self,
        tmp_path,
        monkeypatch,
    ):
        """When spec has no behavioral-target videos, extraction is skipped."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        vis_vid = ref_dir / "design.mp4"
        vis_vid.write_bytes(b"MP4" * 500)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/design.mp4"),
                    roles=["visual-target"],
                ),
            ],
        )

        with (
            patch(
                "duplo.main.extract_all_videos",
            ) as mock_extract,
            patch("duplo.main.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vis_vid],
                pdfs=[],
                text_files=[],
                urls=[],
            )
            _analyze_new_files(
                ["ref/design.mp4"],
                spec=spec,
            )

        mock_extract.assert_not_called()

    def test_analyze_new_files_proposed_behavioral_excluded(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Proposed behavioral-target videos are excluded by the formatter."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        vid = ref_dir / "proposed.mp4"
        vid.write_bytes(b"MP4" * 500)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.mp4"),
                    roles=["behavioral-target"],
                    proposed=True,
                ),
            ],
        )

        with (
            patch(
                "duplo.main.extract_all_videos",
            ) as mock_extract,
            patch("duplo.main.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vid],
                pdfs=[],
                text_files=[],
                urls=[],
            )
            _analyze_new_files(
                ["ref/proposed.mp4"],
                spec=spec,
            )

        mock_extract.assert_not_called()


class TestFirstRunSiteVideosBehavioral:
    """Site videos from _download_site_media are merged into behavioral input."""

    def test_site_videos_included_in_behavioral_with_spec(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        """When spec is present, site_videos are appended to behavioral refs."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        beh_vid = ref_dir / "demo.mp4"
        beh_vid.write_bytes(b"MP4" * 100)

        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch("duplo.main.load_product", return_value=("App", "https://a.com")),
            patch("duplo.main.fetch_site", return_value=("t", [], None, [], {"u": "<h>"})),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch("duplo.main.extract_all_videos", return_value=[]) as mock_ev,
            patch("duplo.main.extract_design"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type("V", (), {"warnings": [], "errors": []})()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_ev.assert_called_once()
        paths = mock_ev.call_args[0][0]
        names = [p.name for p in paths]
        assert "demo.mp4" in names
        assert "promo.mp4" in names
        assert len(paths) == 2

    def test_site_videos_included_without_spec(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Without spec, site_videos are appended to all scanned videos."""
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        scan_vid = ref_dir / "demo.mp4"
        scan_vid.write_bytes(b"MP4" * 100)
        (ref_dir / "urls.txt").write_text("https://a.com")

        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch("duplo.main._validate_url", return_value=("https://a.com", "App")),
            patch("duplo.main._confirm_product", return_value="App"),
            patch("duplo.main.fetch_site", return_value=("t", [], None, [], {"u": "<h>"})),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch("duplo.main.extract_all_videos", return_value=[]) as mock_ev,
            patch("duplo.main.extract_design"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            main()

        mock_ev.assert_called_once()
        paths = mock_ev.call_args[0][0]
        names = [p.name for p in paths]
        assert "demo.mp4" in names
        assert "promo.mp4" in names

    def test_site_video_frames_extracted_for_design_input(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Frames from site videos are identified for design input source (4)."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        beh_vid = ref_dir / "demo.mp4"
        beh_vid.write_bytes(b"MP4" * 100)

        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        beh_frame = frames_dir / "demo_scene_0001.png"
        beh_frame.write_bytes(b"PNG")
        site_frame = frames_dir / "promo_scene_0001.png"
        site_frame.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            from duplo.design_extractor import DesignRequirements

            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch("duplo.main.load_product", return_value=("App", "https://a.com")),
            patch("duplo.main.fetch_site", return_value=("t", [], None, [], {"u": "<h>"})),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(source=beh_vid, frames=[beh_frame]),
                    ExtractionResult(source=site_vid, frames=[site_frame]),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch("duplo.main.apply_filter", return_value=[beh_frame, site_frame]),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch("duplo.main.extract_design", side_effect=_capture_design),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch("duplo.main.save_selections", return_value=tmp_path / _DUPLO_JSON),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type("V", (), {"warnings": [], "errors": []})()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        # collect_design_input should receive site_video_frames
        mock_cdi.assert_called_once()
        svf = mock_cdi.call_args[1].get(
            "site_video_frames",
            mock_cdi.call_args[0][3] if len(mock_cdi.call_args[0]) > 3 else None,
        )
        if svf is not None:
            site_names = [f.name for f in svf]
            assert "promo_scene_0001.png" in site_names


class TestRescrapeReturnsCounts:
    """Tests that _rescrape_product_url returns page/example counts."""

    def test_returns_zeros_when_no_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        pages, examples, text = _rescrape_product_url()
        assert pages == 0
        assert examples == 0
        assert text == ""

    def test_returns_counts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})
        records = [PageRecord("https://example.com", "t", "abc")]
        examples = [{"input": "1+1", "expected_output": "2", "source_url": "", "language": "py"}]
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", examples, None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.main.save_reference_urls"):
                with patch("duplo.main.save_raw_content"):
                    with patch("duplo.main.save_examples"):
                        pages, ex, text = _rescrape_product_url()
        assert pages == 1
        assert ex == 1
        assert text == "text"


class TestDetectGapsReturnsCounts:
    """Tests that _detect_and_append_gaps returns counts."""

    def test_returns_zeros_when_no_plan(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"features": [{"name": "X", "description": "x", "category": "c"}]},
        )
        result = _detect_and_append_gaps()
        assert result == (0, 0, 0, 0)

    def test_returns_gap_counts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {"name": "Auth", "description": "Login.", "category": "core"},
                    {"name": "Search", "description": "Find.", "category": "core"},
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult, MissingFeature

        gap_result = GapResult(
            missing_features=[
                MissingFeature(name="Auth", reason="Not covered"),
                MissingFeature(name="Search", reason="Not covered"),
            ],
            missing_examples=[],
        )
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            mf, me, dr, ta = _detect_and_append_gaps()
        assert mf == 2
        assert me == 0
        assert dr == 0
        assert ta == 2

    def test_spec_auto_generated_design_merged(self, tmp_path, monkeypatch):
        """Design gaps read from SPEC.md AUTO-GENERATED block AND duplo.json."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": [
                    {"name": "Auth", "description": "Login.", "category": "core"},
                ],
                "design_requirements": {
                    "colors": {"primary": "#ff0000"},
                },
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult
        from duplo.spec_reader import DesignBlock, ProductSpec

        spec = ProductSpec(
            raw="",
            design=DesignBlock(
                auto_generated=(
                    "### Colors\n- **accent**: `#0000ff`\n\n### Typography\n- **body**: Roboto"
                ),
            ),
        )

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            with patch("duplo.main.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=spec)

        # detect_design_gaps should have been called with the merged dict.
        call_design = mock_ddg.call_args[0][1]
        # duplo.json's primary wins, spec's accent is added.
        assert call_design["colors"]["primary"] == "#ff0000"
        assert call_design["colors"]["accent"] == "#0000ff"
        assert call_design["fonts"]["body"] == "Roboto"

    def test_spec_design_only_no_duplo_json_design(self, tmp_path, monkeypatch):
        """Design gaps work with spec-only design (no duplo.json design_requirements)."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": [
                    {"name": "Auth", "description": "Login.", "category": "core"},
                ],
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult
        from duplo.spec_reader import DesignBlock, ProductSpec

        spec = ProductSpec(
            raw="",
            design=DesignBlock(
                auto_generated="### Colors\n- **primary**: `#123456`",
            ),
        )

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            with patch("duplo.main.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=spec)

        call_design = mock_ddg.call_args[0][1]
        assert call_design["colors"]["primary"] == "#123456"

    def test_no_spec_falls_back_to_duplo_json_only(self, tmp_path, monkeypatch):
        """Without spec, design gaps use duplo.json's design_requirements only."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": [
                    {"name": "Auth", "description": "Login.", "category": "core"},
                ],
                "design_requirements": {
                    "colors": {"primary": "#ff0000"},
                },
            },
        )
        (tmp_path / "PLAN.md").write_text("# Phase 0\n- [ ] Build UI\n", encoding="utf-8")

        from duplo.gap_detector import GapResult

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.main.detect_gaps", return_value=gap_result):
            with patch("duplo.main.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=None)

        call_design = mock_ddg.call_args[0][1]
        assert call_design == {"colors": {"primary": "#ff0000"}}


class TestSubsequentRunSummary:
    """Integration: _subsequent_run prints a summary at the end."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
    }

    def test_prints_summary_with_file_changes(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("some notes")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.extract_features", return_value=[]):
                    main()

        out = capsys.readouterr().out
        assert "Update summary" in out

    def test_prints_no_changes_summary(self, capsys, tmp_path, monkeypatch):
        data = {**self._BASE_DATA, "source_url": ""}
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        from duplo.hasher import compute_hashes, save_hashes

        hashes = compute_hashes(tmp_path)
        save_hashes(hashes, directory=tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        out = capsys.readouterr().out
        assert "No changes detected" in out


class TestRescrapeReturnsText:
    """Tests that _rescrape_product_url returns scraped text."""

    def test_returns_scraped_text(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})
        records = [PageRecord("https://example.com", "t", "abc")]
        with patch(
            "duplo.main.fetch_site",
            return_value=("product content", [], None, records, {}),
        ):
            with patch("duplo.main.save_reference_urls"):
                pages, ex, text = _rescrape_product_url()
        assert text == "product content"

    def test_returns_empty_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})
        with patch("duplo.main.fetch_site", side_effect=Exception("fail")):
            pages, ex, text = _rescrape_product_url()
        assert text == ""

    def test_returns_empty_when_no_source(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        pages, ex, text = _rescrape_product_url()
        assert text == ""


class TestSubsequentRunReextractsFeatures:
    """Tests that _subsequent_run re-extracts features after re-scraping."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [{"name": "Auth", "description": "Login.", "category": "core"}],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {"phase": 0, "title": "Core", "goal": "Core", "features": ["Auth"], "test": "ok"},
        ],
        "current_phase": 0,
    }

    def test_reextracts_and_merges_features(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        new_feature = Feature(name="Search", description="Find things.", category="core")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[new_feature]) as mock_extract,
            patch("duplo.main.save_features") as mock_save,
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_extract.assert_called_once_with(
            "product text",
            existing_names=["Auth"],
            spec_text="",
            scope_include=None,
            scope_exclude=None,
        )
        mock_save.assert_called_once_with([new_feature])

    def test_skips_extraction_when_no_scraped_text(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main.extract_features") as mock_extract,
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_extract.assert_not_called()

    def test_summary_shows_new_features(self, capsys):
        summary = UpdateSummary(new_features=3)
        _print_summary(summary)
        out = capsys.readouterr().out
        assert "New features extracted: 3" in out


class TestCompletePhaseIssues:
    """Issues collected at phase completion are saved to duplo.json."""

    def test_issues_saved_with_user_source_and_phase(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"current_phase": 1, "roadmap": [{"phase": 1}]})
        (tmp_path / "PLAN.md").write_text("- [x] task\n")

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch(
                "duplo.main.collect_issues",
                return_value=[
                    "Button misaligned",
                    "Color wrong",
                ],
            ),
        ):
            _complete_phase("- [x] task\n", "", "Phase 1")

        data = _read_duplo_json(tmp_path)
        issues = data["issues"]
        assert len(issues) == 2
        assert issues[0]["description"] == "Button misaligned"
        assert issues[0]["source"] == "user"
        assert issues[0]["phase"] == "Phase 1"
        assert issues[0]["status"] == "open"
        assert issues[1]["description"] == "Color wrong"
        assert issues[1]["source"] == "user"
        assert issues[1]["phase"] == "Phase 1"

    def test_no_issues_saves_nothing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"current_phase": 1})
        (tmp_path / "PLAN.md").write_text("- [x] task\n")

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
        ):
            _complete_phase("- [x] task\n", "", "Phase 2")

        data = _read_duplo_json(tmp_path)
        assert data.get("issues", []) == []
        assert "No issues reported." in capsys.readouterr().out


class TestUnimplementedFeatures:
    """Tests for _unimplemented_features helper."""

    def test_returns_only_non_implemented(self):
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
        result = _unimplemented_features(data)
        names = [f.name for f in result]
        assert names == ["Export", "Filter"]

    def test_returns_empty_when_all_implemented(self):
        data = {
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        }
        assert _unimplemented_features(data) == []

    def test_returns_empty_when_no_features(self):
        assert _unimplemented_features({}) == []

    def test_defaults_missing_status_to_pending(self):
        data = {
            "features": [
                {"name": "A", "description": "A", "category": "core"},
            ],
        }
        result = _unimplemented_features(data)
        assert len(result) == 1
        assert result[0].name == "A"


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


class TestRoadmapRegeneration:
    """Tests for roadmap regeneration when roadmap is missing or consumed."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [
            {
                "name": "Export",
                "description": "Export data",
                "category": "core",
                "status": "pending",
                "implemented_in": "",
            },
        ],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
    }

    def test_regenerates_when_no_roadmap(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Export",
                "goal": "Add export",
                "features": ["Export"],
                "test": "Export works",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_gen.assert_called_once()
        out = capsys.readouterr().out
        assert "remaining feature" in out

    def test_regenerates_when_roadmap_consumed(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "roadmap": [
                {"phase": 0, "title": "Done", "goal": "Done", "features": [], "test": "ok"},
            ],
            "current_phase": 1,
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Export",
                "goal": "Add export",
                "features": ["Export"],
                "test": "ok",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_gen.assert_called_once()
        # Only pending features should be passed
        features_arg = mock_gen.call_args[0][1]
        assert all(f.status != "implemented" for f in features_arg)

    def test_stops_when_all_features_implemented(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_roadmap") as mock_gen:
            main()

        mock_gen.assert_not_called()
        out = capsys.readouterr().out
        assert "All features implemented" in out

    def test_stops_when_roadmap_generation_fails(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.generate_roadmap", return_value=[]),
            patch("duplo.main.generate_phase_plan") as mock_plan,
        ):
            main()

        mock_plan.assert_not_called()
        out = capsys.readouterr().out
        assert "failed to generate roadmap" in out

    def test_excludes_implemented_features_from_roadmap(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "features": [
                {
                    "name": "Search",
                    "description": "Search items",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 0: Scaffold",
                },
                {
                    "name": "Export",
                    "description": "Export data",
                    "category": "extra",
                    "status": "pending",
                    "implemented_in": "",
                },
                {
                    "name": "Import",
                    "description": "Import data",
                    "category": "extra",
                    "status": "implemented",
                    "implemented_in": "Phase 1: Core",
                },
                {
                    "name": "Settings",
                    "description": "User settings",
                    "category": "ui",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Remaining",
                "goal": "Build remaining",
                "features": ["Export", "Settings"],
                "test": "ok",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_gen.assert_called_once()
        features_arg = mock_gen.call_args[0][1]
        feature_names = [f.name for f in features_arg]
        assert feature_names == ["Export", "Settings"]
        assert "Search" not in feature_names
        assert "Import" not in feature_names

    def test_passes_completion_history_when_regenerating(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 0: Scaffold",
                },
                {
                    "name": "Export",
                    "description": "Export data",
                    "category": "extra",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Export",
                "goal": "Add export",
                "features": ["Export"],
                "test": "ok",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_gen.assert_called_once()
        kwargs = mock_gen.call_args[1]
        assert "completion_history" in kwargs
        history = kwargs["completion_history"]
        assert len(history) == 1
        assert history[0]["phase"] == "Phase 0: Scaffold"
        assert history[0]["features"] == ["Search"]

    def test_saves_new_roadmap_and_resets_current_phase(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "roadmap": [
                {
                    "phase": 0,
                    "title": "Done",
                    "goal": "Done",
                    "features": [],
                    "test": "ok",
                },
            ],
            "current_phase": 1,
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Export",
                "goal": "Add export",
                "features": ["Export"],
                "test": "ok",
            },
            {
                "phase": 1,
                "title": "Polish",
                "goal": "Polish export",
                "features": [],
                "test": "ok",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        saved = _read_duplo_json(tmp_path)
        assert saved["current_phase"] == 0
        assert saved["roadmap"] == fake_roadmap

    def test_prints_feature_status_before_regenerating(self, capsys, tmp_path, monkeypatch):
        data = {
            "source_url": "https://example.com",
            "features": [
                {
                    "name": "Search",
                    "description": "Search items",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 0",
                },
                {
                    "name": "Export",
                    "description": "Export data",
                    "category": "extra",
                    "status": "pending",
                    "implemented_in": "",
                },
            ],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Export",
                "goal": "Add export",
                "features": ["Export"],
                "test": "ok",
            },
        ]
        with (
            patch("duplo.main.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "1/2 implemented" in out
        assert "Search" in out
        assert "Export" in out

    def test_prints_feature_status_when_all_implemented(self, capsys, tmp_path, monkeypatch):
        data = {
            "source_url": "https://example.com",
            "features": [
                {
                    "name": "Search",
                    "description": "Search",
                    "category": "core",
                    "status": "implemented",
                    "implemented_in": "Phase 0",
                },
            ],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_roadmap") as mock_gen:
            main()

        mock_gen.assert_not_called()
        out = capsys.readouterr().out
        assert "1/1 implemented" in out
        assert "All features implemented" in out


class TestBuildCompletionHistory:
    """Tests for _build_completion_history."""

    def test_empty_features(self):
        assert _build_completion_history({}) == []
        assert _build_completion_history({"features": []}) == []

    def test_no_implemented_features(self):
        data = {
            "features": [
                {"name": "A", "status": "pending", "implemented_in": ""},
            ]
        }
        assert _build_completion_history(data) == []

    def test_groups_by_phase(self):
        data = {
            "features": [
                {
                    "name": "A",
                    "status": "implemented",
                    "implemented_in": "Phase 0",
                },
                {
                    "name": "B",
                    "status": "implemented",
                    "implemented_in": "Phase 1",
                },
                {
                    "name": "C",
                    "status": "implemented",
                    "implemented_in": "Phase 0",
                },
                {
                    "name": "D",
                    "status": "pending",
                    "implemented_in": "",
                },
            ]
        }
        result = _build_completion_history(data)
        assert len(result) == 2
        assert result[0] == {"phase": "Phase 0", "features": ["A", "C"]}
        assert result[1] == {"phase": "Phase 1", "features": ["B"]}

    def test_skips_missing_implemented_in(self):
        data = {
            "features": [
                {"name": "A", "status": "implemented"},
            ]
        }
        assert _build_completion_history(data) == []


class TestRescrapeDocStructures:
    """_rescrape_product_url saves doc_structures when returned."""

    def test_saves_doc_structures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        doc_structs = {"feature_tables": [{"heading": "API", "rows": []}]}
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], doc_structs, [], {}),
        ):
            with patch("duplo.main.save_doc_structures") as mock_save_docs:
                _rescrape_product_url()

        mock_save_docs.assert_called_once_with(doc_structs)

    def test_skips_when_no_doc_structures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, [], {}),
        ):
            with patch("duplo.main.save_doc_structures") as mock_save_docs:
                _rescrape_product_url()

        mock_save_docs.assert_not_called()


class TestRescrapeDownloadsSiteMedia:
    """_rescrape_product_url downloads media from product-reference pages."""

    def test_downloads_media_from_product_ref_raw_pages(self, capsys, tmp_path, monkeypatch):
        """Caller passes product-reference raw pages to _download_site_media."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"source_url": "https://example.com", "features": []},
        )

        product_ref_raw_pages = {
            "https://example.com": "<html><img src='pic.png'/></html>",
        }
        with patch(
            "duplo.main.fetch_site",
            return_value=(
                "text",
                [],
                None,
                [],
                product_ref_raw_pages,
            ),
        ):
            with patch(
                "duplo.main._download_site_media",
                return_value=([Path("a.png")], []),
            ) as mock_dl:
                _rescrape_product_url()

        mock_dl.assert_called_once_with(product_ref_raw_pages)
        out = capsys.readouterr().out
        assert "1 image" in out

    def test_skips_media_when_no_raw_pages(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, [], {}),
        ):
            with patch("duplo.main._download_site_media") as mock_dl:
                _rescrape_product_url()

        mock_dl.assert_not_called()


class TestDownloadSiteMediaCachedVsNew:
    """_download_site_media returns ALL local paths — cached and new."""

    _HTML = (
        "<html><body>"
        '<img src="https://cdn.example.com/hero.png"/>'
        '<video src="https://cdn.example.com/demo.mp4"></video>'
        "</body></html>"
    )

    def test_returns_newly_downloaded_paths(self, tmp_path, monkeypatch):
        """First call downloads and returns new paths."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        def fake_stream(method, url, **kw):
            from unittest.mock import MagicMock

            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        raw_pages = {"https://example.com": self._HTML}
        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, vids = _download_site_media(raw_pages)

        assert len(imgs) == 1
        assert len(vids) == 1
        assert all(p.exists() for p in imgs + vids)

        # Files stored under <url-hash> subdirectory.
        import hashlib

        url_hash = hashlib.sha256("https://example.com".encode()).hexdigest()[:16]
        for p in imgs + vids:
            assert p.parent.name == url_hash

    def test_returns_cached_paths_on_second_call(self, tmp_path, monkeypatch):
        """Second call returns cached paths without HTTP requests."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        # Pre-populate the cache directory with files matching the
        # URL-hash/filename layout that _download_site_media creates.
        import hashlib

        url_hash = hashlib.sha256("https://example.com".encode()).hexdigest()[:16]
        media_dir = tmp_path / ".duplo" / "site_media" / url_hash
        media_dir.mkdir(parents=True)
        cached_img = media_dir / "cdn_example_com_hero.png"
        cached_img.write_bytes(b"x" * 20_000)
        cached_vid = media_dir / "cdn_example_com_demo.mp4"
        cached_vid.write_bytes(b"video-data")

        raw_pages = {"https://example.com": self._HTML}
        with patch("duplo.fetcher.httpx.stream") as mock_stream:
            imgs, vids = _download_site_media(raw_pages)

        mock_stream.assert_not_called()
        assert len(imgs) == 1
        assert imgs[0].resolve() == cached_img.resolve()
        assert len(vids) == 1
        assert vids[0].resolve() == cached_vid.resolve()

    def test_mixed_cached_and_new(self, tmp_path, monkeypatch):
        """Cached files and new downloads both appear in results."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        import hashlib

        url_hash = hashlib.sha256("https://example.com".encode()).hexdigest()[:16]
        media_dir = tmp_path / ".duplo" / "site_media" / url_hash
        media_dir.mkdir(parents=True)
        # Cache only the image, not the video.
        cached_img = media_dir / "cdn_example_com_hero.png"
        cached_img.write_bytes(b"x" * 20_000)

        def fake_stream(method, url, **kw):
            from unittest.mock import MagicMock

            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"video-bytes"])
            return cm

        raw_pages = {"https://example.com": self._HTML}
        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, vids = _download_site_media(raw_pages)

        assert len(imgs) == 1
        assert imgs[0].resolve() == cached_img.resolve()
        assert len(vids) == 1
        assert vids[0].exists()

    def test_empty_raw_pages(self, tmp_path, monkeypatch):
        """Empty raw_pages returns empty lists."""
        monkeypatch.chdir(tmp_path)
        imgs, vids = _download_site_media({})
        assert imgs == []
        assert vids == []

    def test_multiple_pages_all_media_returned(self, tmp_path, monkeypatch):
        """Media from multiple pages stored in separate url-hash dirs."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        page_a = '<html><img src="https://a.com/1.png"/></html>'
        page_b = '<html><img src="https://b.com/2.png"/></html>'

        def fake_stream(method, url, **kw):
            from unittest.mock import MagicMock

            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        raw_pages = {
            "https://a.com/page": page_a,
            "https://b.com/page": page_b,
        }
        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, vids = _download_site_media(raw_pages)

        assert len(imgs) == 2
        assert vids == []

        # Each page's media lives in a different url-hash directory.
        import hashlib

        hash_a = hashlib.sha256("https://a.com/page".encode()).hexdigest()[:16]
        hash_b = hashlib.sha256("https://b.com/page".encode()).hexdigest()[:16]
        parent_names = {p.parent.name for p in imgs}
        assert parent_names == {hash_a, hash_b}

    def test_cross_origin_media_downloaded(self, tmp_path, monkeypatch):
        """Embedded media is downloaded regardless of origin.

        Per design § 'Same-origin and embedded media': the user
        authorized the page; its embedded media (CDN images, third-party
        video hosts) is page content, not a navigation target.  Origin
        does not restrict download.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        html = (
            "<html><body>"
            '<img src="https://cdn.other.com/hero.png"/>'
            '<img src="https://static.third-party.io/banner.jpg"/>'
            '<video src="https://media.vimeo.com/demo.mp4"></video>'
            "</body></html>"
        )
        raw_pages = {"https://example.com": html}

        def fake_stream(method, url, **kw):
            from unittest.mock import MagicMock

            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, vids = _download_site_media(raw_pages)

        # All three cross-origin resources downloaded.
        assert len(imgs) == 2
        assert len(vids) == 1
        assert all(p.exists() for p in imgs + vids)

        # Filenames include the cross-origin domain prefix.
        img_names = {p.name for p in imgs}
        assert "cdn_other_com_hero.png" in img_names
        assert "static_third-party_io_banner.jpg" in img_names
        vid_names = {p.name for p in vids}
        assert "media_vimeo_com_demo.mp4" in vid_names

    def test_http_failure_records_diagnostic_and_continues(self, tmp_path, monkeypatch):
        """HTTP failure on one embed skips it but returns the rest."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo").mkdir()

        html = (
            "<html><body>"
            '<img src="https://cdn.example.com/good.png"/>'
            '<img src="https://cdn.example.com/broken.png"/>'
            '<video src="https://cdn.example.com/ok.mp4"></video>'
            "</body></html>"
        )
        raw_pages = {"https://example.com": html}

        call_count = 0

        def fake_stream(method, url, **kw):
            from unittest.mock import MagicMock

            nonlocal call_count
            call_count += 1
            if "broken.png" in url:
                raise httpx.HTTPStatusError(
                    "404",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(404),
                )
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        with (
            patch("duplo.fetcher.httpx.stream", side_effect=fake_stream),
            patch("duplo.fetcher.record_failure") as mock_rf,
        ):
            imgs, vids = _download_site_media(raw_pages)

        # The good image and video are returned; the broken one is skipped.
        assert len(imgs) == 1
        assert len(vids) == 1
        assert imgs[0].name == "cdn_example_com_good.png"
        assert vids[0].name == "cdn_example_com_ok.mp4"

        # Diagnostic recorded for the failed download.
        mock_rf.assert_called_once()
        assert "broken.png" in mock_rf.call_args[0][2]


class TestSubsequentRunFeatureCountingIntegration:
    """Tests the old_count/new_count diff logic during feature re-extraction."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [{"name": "Auth", "description": "Login.", "category": "core"}],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Core",
                "features": ["Auth"],
                "test": "ok",
            },
        ],
        "current_phase": 0,
    }

    def test_counts_newly_merged_features(self, capsys, tmp_path, monkeypatch):
        """When save_features adds genuinely new features, the count is correct."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        new_feat = Feature(name="Search", description="Find.", category="core")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[new_feat]),
            # Let save_features actually run so counting logic works
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "1 new feature(s) merged" in out
        data = _read_duplo_json(tmp_path)
        assert len(data["features"]) == 2

    def test_reports_no_new_when_all_duplicates(self, capsys, tmp_path, monkeypatch):
        """When all re-extracted features already exist, reports no new."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        dup_feat = Feature(name="Auth", description="Login again.", category="core")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[dup_feat]),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "No new features found" in out
        data = _read_duplo_json(tmp_path)
        assert len(data["features"]) == 1

    def test_handles_invalid_duplo_json_during_reextraction(self, capsys, tmp_path, monkeypatch):
        """When duplo.json is corrupted between rescrape and re-extract, exits."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        new_feat = Feature(name="Search", description="Find.", category="core")

        def corrupt_json(*_a, **_k):
            (duplo_dir / "duplo.json").write_text("NOT JSON", encoding="utf-8")
            return [new_feat]

        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", side_effect=corrupt_json),
        ):
            main()

        out = capsys.readouterr().out
        assert "invalid JSON" in out

    def test_reports_no_features_when_extraction_returns_empty(
        self, capsys, tmp_path, monkeypatch
    ):
        """When extract_features returns empty list, reports accordingly."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[]),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "No features extracted" in out


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


class TestCompletePhaseTaskMatching:
    """Phase completion marks features via annotated and unannotated tasks."""

    _FEATURES = [
        {
            "name": "Search",
            "description": "Full-text search",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
        {
            "name": "Export",
            "description": "Export data",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
    ]

    def _plan_with_annotations(self):
        return (
            '- [x] Add search bar [feat: "Search"]\n'
            "- [x] Scaffold project\n"
            '- [x] Fix crash on export [fix: "Export button crash"]\n'
        )

    def test_annotated_features_marked(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": self._FEATURES,
                "issues": [
                    {
                        "description": "Export button crash",
                        "source": "user",
                        "phase": "Phase 1",
                        "status": "open",
                    },
                ],
            },
        )

        plan_content = self._plan_with_annotations()

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
                return_value=(["Export"], []),
            ) as mock_match,
        ):
            _complete_phase(plan_content, "", "Phase 1")

        # Check that Search was marked via annotation.
        data = _read_duplo_json(tmp_path)
        search = next(f for f in data["features"] if f["name"] == "Search")
        assert search["status"] == "implemented"
        assert search["implemented_in"] == "Phase 1"

        # Check that fix annotation resolved the issue.
        issue = data["issues"][0]
        assert issue["status"] == "resolved"

        # Unannotated matcher was called with the scaffold task.
        mock_match.assert_called_once()
        out = capsys.readouterr().out
        assert "1 annotated feature" in out
        assert "1 annotated fix" in out
        assert "Matched 1 existing feature" in out

    def test_unannotated_tasks_matched_via_claude(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": self._FEATURES})

        plan_content = "- [x] Build search UI\n- [x] Wire export button\n"

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
                return_value=(["Search", "Export"], ["Custom theme"]),
            ),
        ):
            _complete_phase(plan_content, "", "Phase 1")

        out = capsys.readouterr().out
        assert "2 unannotated task(s)" in out
        assert "Matched 2 existing feature" in out
        assert "Discovered 1 new feature" in out
        assert "Custom theme" in out

    def test_no_features_skips_unannotated_matching(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": []})

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
            ) as mock_match,
        ):
            _complete_phase("- [x] task\n", "", "Phase 1")

        mock_match.assert_not_called()

    def test_all_tasks_annotated_skips_unannotated_matching(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": self._FEATURES})

        plan_content = '- [x] Add search [feat: "Search"]\n'

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
            ) as mock_match,
        ):
            _complete_phase(plan_content, "", "Phase 1")

        mock_match.assert_not_called()

    def test_no_matches_prints_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": self._FEATURES})

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
                return_value=([], []),
            ),
        ):
            _complete_phase("- [x] setup\n", "", "Phase 1")

        out = capsys.readouterr().out
        assert "No feature matches found" in out


class TestCompletePhaseScoping:
    """_complete_phase must only process tasks from the current phase section."""

    _FEATURES = [
        {
            "name": "Search",
            "description": "Full-text search",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
        {
            "name": "Export",
            "description": "Export data",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
    ]

    def test_only_processes_current_phase_tasks(self, tmp_path, monkeypatch, capsys):
        """Phase 2 completion must not re-process Phase 1 tasks."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": self._FEATURES,
                "current_phase": 2,
                "roadmap": [
                    {"phase": 1, "title": "Core"},
                    {"phase": 2, "title": "Extras"},
                ],
            },
        )

        # Multi-phase PLAN.md: Phase 1 has a Search annotation,
        # Phase 2 has an Export annotation.
        plan_content = (
            "# App — Phase 1: Core\n"
            '- [x] Build search [feat: "Search"]\n'
            "# App — Phase 2: Extras\n"
            '- [x] Add export [feat: "Export"]\n'
        )

        with (
            patch("duplo.main.append_phase_to_history") as mock_append,
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
                return_value=([], []),
            ),
        ):
            _complete_phase(plan_content, "", "Phase 2: Extras")

        # Only Export should be marked (Phase 2 task), not Search (Phase 1).
        data = _read_duplo_json(tmp_path)
        search = next(f for f in data["features"] if f["name"] == "Search")
        assert search["status"] == "pending", "Phase 1 task was re-processed"

        export = next(f for f in data["features"] if f["name"] == "Export")
        assert export["status"] == "implemented"

        # append_phase_to_history should receive only the Phase 2 section.
        recorded = mock_append.call_args[0][0]
        assert "Phase 2" in recorded
        assert "Phase 1" not in recorded

    def test_unannotated_tasks_scoped_to_current_phase(self, tmp_path, monkeypatch, capsys):
        """match_unannotated_tasks receives only Phase 2 unannotated tasks."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": self._FEATURES,
                "current_phase": 2,
                "roadmap": [
                    {"phase": 1, "title": "Core"},
                    {"phase": 2, "title": "Extras"},
                ],
            },
        )

        # Phase 1 has an unannotated task; Phase 2 has one annotated + one not.
        plan_content = (
            "# App — Phase 1: Core\n"
            "- [x] Set up CI\n"
            "# App — Phase 2: Extras\n"
            '- [x] Add export [feat: "Export"]\n'
            "- [x] Write docs\n"
        )

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch(
                "duplo.main.match_unannotated_tasks",
                return_value=([], []),
            ) as mock_match,
        ):
            _complete_phase(plan_content, "", "Phase 2: Extras")

        # match_unannotated_tasks should be called with tasks from Phase 2 only.
        call_tasks = mock_match.call_args[0][0]
        task_texts = [t.text for t in call_tasks]
        assert "Write docs" in task_texts
        assert "Set up CI" not in task_texts

    def test_fix_annotations_scoped_to_current_phase(self, tmp_path, monkeypatch):
        """Fix annotations in Phase 1 are not resolved when completing Phase 2."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "features": [],
                "current_phase": 2,
                "roadmap": [
                    {"phase": 1, "title": "Core"},
                    {"phase": 2, "title": "Extras"},
                ],
                "issues": [
                    {
                        "description": "Login broken",
                        "source": "user",
                        "phase": "Phase 1",
                        "status": "open",
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "description": "Search crash",
                        "source": "user",
                        "phase": "Phase 2",
                        "status": "open",
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                ],
            },
        )

        plan_content = (
            "# App — Phase 1: Core\n"
            '- [x] Fix login page [fix: "Login broken"]\n'
            "# App — Phase 2: Extras\n"
            '- [x] Fix search [fix: "Search crash"]\n'
        )

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
        ):
            _complete_phase(plan_content, "", "Phase 2: Extras")

        data = _read_duplo_json(tmp_path)
        login_issue = next(i for i in data["issues"] if i["description"] == "Login broken")
        assert login_issue["status"] == "open", "Phase 1 fix was re-processed"

        search_issue = next(i for i in data["issues"] if i["description"] == "Search crash")
        assert search_issue["status"] == "resolved"


class TestCompletePhaseAppshotTimeout:
    """_complete_phase handles capture_appshot timeout exit code."""

    def test_timeout_prints_message_and_continues(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"current_phase": 1})
        (tmp_path / "PLAN.md").write_text("- [x] task\n")

        with (
            patch("duplo.main.append_phase_to_history"),
            patch("duplo.main.advance_phase"),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.capture_appshot", return_value=-2),
        ):
            _complete_phase("- [x] task\n", "MyApp", "Phase 1")

        out = capsys.readouterr().out
        assert "Screenshot capture timed out (skipping)" in out


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


class TestGapDetectorSkipsWithUncheckedTasks:
    """Gap detection must be skipped when PLAN.md has unchecked tasks."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [
            {"name": "Search", "description": "search", "category": "core"},
        ],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Core",
                "features": ["Search"],
                "test": "ok",
            },
        ],
        "current_phase": 0,
    }

    def test_gap_detector_skipped_when_plan_has_unchecked_tasks(
        self, capsys, tmp_path, monkeypatch
    ):
        """When PLAN.md has unchecked tasks, gap detection must not run."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        plan = "# Phase 0\n- [x] Done task\n- [ ] User-added task\n"
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch(
                "duplo.main._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ) as mock_gaps,
        ):
            main()

        mock_gaps.assert_not_called()

    def test_gap_detector_runs_when_no_plan(self, capsys, tmp_path, monkeypatch):
        """When there is no PLAN.md, gap detection can run."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch(
                "duplo.main._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ) as mock_gaps,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_gaps.assert_called_once()


class TestFixMode:
    """Tests for duplo fix subcommand."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "app_name": "TestApp",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {"phase": 0, "title": "Core", "goal": "Core", "features": [], "test": "ok"},
        ],
        "current_phase": 0,
    }

    def test_fix_from_cli_args(self, capsys, tmp_path, monkeypatch):
        """duplo fix 'bug one' 'bug two' appends tasks to PLAN.md."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "bug one", "bug two"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="bug one",
                    expected="",
                    severity="major",
                    area="",
                ),
                Diagnosis(
                    symptom="bug two",
                    expected="",
                    severity="major",
                    area="",
                ),
            ],
            summary="Two bugs.",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert '[fix: "bug one"]' in plan
        assert '[fix: "bug two"]' in plan
        data = _read_duplo_json(tmp_path)
        issues = data.get("issues", [])
        assert len(issues) == 2
        assert issues[0]["description"] == "bug one"
        assert issues[0]["source"] == "user"
        out = capsys.readouterr().out
        assert "2 diagnosed fix task" in out

    def test_fix_from_file(self, capsys, tmp_path, monkeypatch):
        """duplo fix --file reads paragraphs as bugs."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [ ] task\n", encoding="utf-8")
        bugs_file = tmp_path / "BUGS.md"
        bugs_file.write_text(
            "First bug description\n\nSecond bug\nwith details\n\nThird bug\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "--file", str(bugs_file)])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(symptom="First bug", expected="", severity="major", area=""),
                Diagnosis(symptom="Second bug", expected="", severity="major", area=""),
                Diagnosis(symptom="Third bug", expected="", severity="minor", area=""),
            ],
            summary="Three bugs.",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Fix: First bug" in plan
        assert "Fix: Second bug" in plan
        assert "Fix: Third bug" in plan
        out = capsys.readouterr().out
        assert "3 bug(s) from" in out
        assert "3 diagnosed fix task" in out

    def test_fix_preserves_existing_plan(self, tmp_path, monkeypatch):
        """Fix tasks are appended; existing plan content is preserved."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        original = "# Phase 0\n- [x] done task\n- [ ] pending task\n"
        (tmp_path / "PLAN.md").write_text(original, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "new bug"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="new bug diagnosed",
                    expected="",
                    severity="major",
                    area="",
                ),
            ],
            summary="One bug.",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # Existing content is preserved (may be reordered by ## Bugs insertion).
        assert "- [x] done task" in plan
        assert "- [ ] pending task" in plan
        assert "Fix: new bug diagnosed" in plan
        # Bug task is in the ## Bugs section.
        assert "## Bugs" in plan

    def test_fix_no_plan_saves_issues_only(self, capsys, tmp_path, monkeypatch):
        """Without PLAN.md, issues are saved but no tasks appended."""
        from duplo.investigator import InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "a bug"])

        result = InvestigationResult(diagnoses=[], summary="No diagnoses")

        with patch("duplo.main.investigate", return_value=result):
            main()

        data = _read_duplo_json(tmp_path)
        assert len(data.get("issues", [])) == 1
        out = capsys.readouterr().out
        assert "No PLAN.md found" in out

    def test_fix_no_bugs_does_nothing(self, capsys, tmp_path, monkeypatch):
        """duplo fix with no args and empty interactive input does nothing."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix"])
        monkeypatch.setattr("builtins.input", lambda _="": "")

        main()

        out = capsys.readouterr().out
        assert "No bugs reported" in out

    def test_fix_exits_without_duplo_json(self, tmp_path, monkeypatch):
        """duplo fix without a project exits with error."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "a bug"])

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestFixModeDiagnosis:
    """Tests for duplo fix routing through investigator.investigate()."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "app_name": "TestApp",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {"phase": 0, "title": "Core", "goal": "Core", "features": [], "test": "ok"},
        ],
        "current_phase": 0,
    }

    def test_single_bug_structured_fix(self, capsys, tmp_path, monkeypatch):
        """A single bug produces a structured diagnosed fix task via investigator."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "button is broken"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Submit button does not respond to clicks",
                    expected="Should submit the form",
                    severity="critical",
                    area="Form handler",
                    evidence_sources=["ref_frame.png"],
                ),
            ],
            summary="One bug found.",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Submit button does not respond to clicks" in plan
        assert "Expected: Should submit the form" in plan
        assert "Area: Form handler" in plan
        assert '[fix: "Submit button does not respond to clicks"]' in plan
        out = capsys.readouterr().out
        assert "1 diagnosed fix task" in out
        # Issues still saved.
        data = _read_duplo_json(tmp_path)
        assert len(data.get("issues", [])) == 1

    def test_multiple_bugs_structured_fixes(self, capsys, tmp_path, monkeypatch):
        """Multiple bugs each produce a structured diagnosed fix task."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "bug A", "bug B"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Color mismatch on header",
                    expected="Should be #333",
                    severity="minor",
                    area="CSS styles",
                ),
                Diagnosis(
                    symptom="Missing footer link",
                    expected="Footer should have a privacy link",
                    severity="major",
                    area="Footer component",
                ),
            ],
            summary="Two bugs found.",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Color mismatch on header" in plan
        assert "Missing footer link" in plan
        out = capsys.readouterr().out
        assert "2 diagnosed fix task" in out
        data = _read_duplo_json(tmp_path)
        assert len(data.get("issues", [])) == 2

    def test_investigator_failure_fallback(self, capsys, tmp_path, monkeypatch):
        """When investigator returns no diagnoses, fallback tasks surface the error."""
        from duplo.investigator import InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "something broke"])

        result = InvestigationResult(
            diagnoses=[],
            summary="Investigation failed: connection timeout",
        )

        with patch("duplo.main.investigate", return_value=result):
            main()

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # Fallback: raw bug text used as fix task in ## Bugs section.
        assert '- [ ] Fix: something broke [fix: "something broke"]' in plan
        assert "## Bugs" in plan
        out = capsys.readouterr().out
        # Error reason surfaced in stdout output.
        assert "Diagnosis incomplete" in out
        assert "Investigation failed: connection timeout" in out
        assert "1 fix task" in out

    def test_fix_without_investigate_calls_investigate_once(self, capsys, tmp_path, monkeypatch):
        """duplo fix (no --investigate) calls investigate() exactly once
        and appends diagnosed fix tasks when result.diagnoses is non-empty."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "login page crashes"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Login form throws TypeError on submit",
                    expected="Should authenticate and redirect",
                    severity="critical",
                    area="Auth module",
                    evidence_sources=["current.png"],
                ),
            ],
            summary="One diagnosis.",
        )

        mock_inv = patch("duplo.main.investigate", return_value=result)
        with mock_inv as inv:
            main()

        # investigate() called exactly once.
        assert inv.call_count == 1

        # Diagnosed fix task appended to PLAN.md.
        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Login form throws TypeError on submit" in plan
        assert "Expected: Should authenticate and redirect" in plan
        assert "Area: Auth module" in plan
        assert '[fix: "Login form throws TypeError on submit"]' in plan

        out = capsys.readouterr().out
        assert "1 diagnosed fix task" in out

    def test_fix_without_investigate_empty_diagnoses_fallback(self, capsys, tmp_path, monkeypatch):
        """duplo fix (no --investigate) falls back to raw fix tasks
        when investigate() returns empty diagnoses."""
        from duplo.investigator import InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "sidebar missing", "font too small"])

        result = InvestigationResult(
            diagnoses=[],
            summary="Could not determine root cause",
        )

        mock_inv = patch("duplo.main.investigate", return_value=result)
        with mock_inv as inv:
            main()

        # investigate() still called exactly once.
        assert inv.call_count == 1

        plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        # Raw fix tasks for each bug in ## Bugs section.
        assert "## Bugs" in plan
        assert '- [ ] Fix: sidebar missing [fix: "sidebar missing"]' in plan
        assert '- [ ] Fix: font too small [fix: "font too small"]' in plan

        out = capsys.readouterr().out
        # Fallback reason surfaced.
        assert "Diagnosis incomplete" in out
        assert "Could not determine root cause" in out
        # Count reflects undiagnosed tasks.
        assert "2 fix task" in out
        assert "(undiagnosed)" in out


class TestCompareWithReferences:
    """Tests for _compare_with_references reference image lookup."""

    def test_uses_duplo_references_over_screenshots(self, tmp_path, monkeypatch, capsys):
        """Video frames in .duplo/references/ take priority over screenshots/."""
        monkeypatch.chdir(tmp_path)
        duplo_refs = tmp_path / ".duplo" / "references"
        duplo_refs.mkdir(parents=True)
        (duplo_refs / "frame001.png").write_bytes(b"PNG" * 100)
        (duplo_refs / "frame002.png").write_bytes(b"PNG" * 100)

        current = tmp_path / "screenshots" / "current" / "main.png"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"PNG" * 100)

        from duplo.comparator import ComparisonResult
        from duplo.main import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.main.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        # Should have used the 2 duplo reference images.
        call_args = mock_cmp.call_args
        refs = call_args[0][1]
        assert len(refs) == 2
        assert all(".duplo" in str(r) for r in refs)

    def test_falls_back_to_screenshots_dir(self, tmp_path, monkeypatch, capsys):
        """Falls back to screenshots/ when .duplo/references/ has no PNGs."""
        monkeypatch.chdir(tmp_path)
        shot_dir = tmp_path / "screenshots"
        shot_dir.mkdir()
        (shot_dir / "page.png").write_bytes(b"PNG" * 100)

        current = tmp_path / "screenshots" / "current" / "main.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"PNG" * 100)

        from duplo.comparator import ComparisonResult
        from duplo.main import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.main.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        refs = mock_cmp.call_args[0][1]
        assert len(refs) == 1
        assert "screenshots" in str(refs[0])

    def test_skips_when_no_references(self, tmp_path, monkeypatch, capsys):
        """Prints skip message when no reference images found anywhere."""
        monkeypatch.chdir(tmp_path)
        current = tmp_path / "main.png"
        current.write_bytes(b"PNG" * 100)

        from duplo.main import _compare_with_references

        _compare_with_references(current)
        out = capsys.readouterr().out
        assert "No reference screenshots found" in out

    def test_empty_duplo_refs_falls_back(self, tmp_path, monkeypatch, capsys):
        """.duplo/references/ exists but is empty — falls back to screenshots/."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo" / "references").mkdir(parents=True)
        shot_dir = tmp_path / "screenshots"
        shot_dir.mkdir()
        (shot_dir / "capture.png").write_bytes(b"PNG" * 100)

        current = tmp_path / "screenshots" / "current" / "main.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"PNG" * 100)

        from duplo.comparator import ComparisonResult
        from duplo.main import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.main.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        refs = mock_cmp.call_args[0][1]
        assert len(refs) == 1
        assert "screenshots" in str(refs[0])
        assert "capture.png" in str(refs[0])

    def test_both_dirs_uses_only_duplo_refs(self, tmp_path, monkeypatch, capsys):
        """When both dirs have PNGs, only .duplo/references/ is used."""
        monkeypatch.chdir(tmp_path)
        duplo_refs = tmp_path / ".duplo" / "references"
        duplo_refs.mkdir(parents=True)
        (duplo_refs / "ref.png").write_bytes(b"PNG" * 100)

        shot_dir = tmp_path / "screenshots"
        shot_dir.mkdir()
        (shot_dir / "legacy.png").write_bytes(b"PNG" * 100)

        current = tmp_path / "current.png"
        current.write_bytes(b"PNG" * 100)

        from duplo.comparator import ComparisonResult
        from duplo.main import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.main.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        refs = mock_cmp.call_args[0][1]
        assert len(refs) == 1
        assert ".duplo" in str(refs[0])
        assert "legacy.png" not in str(refs[0])

    def test_duplo_refs_no_dir_falls_back(self, tmp_path, monkeypatch, capsys):
        """.duplo/references/ doesn't exist at all — falls back to screenshots/."""
        monkeypatch.chdir(tmp_path)
        shot_dir = tmp_path / "screenshots"
        shot_dir.mkdir()
        (shot_dir / "web.png").write_bytes(b"PNG" * 100)

        current = tmp_path / "current.png"
        current.write_bytes(b"PNG" * 100)

        from duplo.comparator import ComparisonResult
        from duplo.main import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.main.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        refs = mock_cmp.call_args[0][1]
        assert len(refs) == 1
        assert "web.png" in str(refs[0])


class TestPhase2NotStartedRunDuplo:
    """Run duplo when Phase 1 is complete and Phase 2 PLAN.md has unchecked tasks.

    Confirms duplo prints a status summary and tells the user to run mcloop.
    """

    _BASE_DATA = {
        "source_url": "https://example.com",
        "app_name": "TestApp",
        "features": [
            {
                "name": "Auth",
                "description": "Login",
                "category": "core",
                "status": "implemented",
                "implemented_in": "Phase 1: Core",
            },
            {
                "name": "Search",
                "description": "Find things",
                "category": "core",
                "status": "pending",
                "implemented_in": "",
            },
            {
                "name": "Export",
                "description": "Export data",
                "category": "core",
                "status": "pending",
                "implemented_in": "",
            },
        ],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Core features",
                "features": ["Auth"],
                "test": "ok",
            },
            {
                "phase": 1,
                "title": "Search & Export",
                "goal": "Add search and export",
                "features": ["Search", "Export"],
                "test": "ok",
            },
        ],
        "current_phase": 1,
        "phases": [
            {
                "phase": "Phase 1: Core",
                "plan": "- [x] Set up auth",
                "completed_at": "2026-03-01T00:00:00",
            },
        ],
    }

    _PHASE2_PLAN = (
        "# TestApp — Phase 2: Search & Export\n\n"
        '- [ ] Implement search [feat: "Search"]\n'
        '- [ ] Implement export [feat: "Export"]\n'
    )

    def _run_main(self, tmp_path, monkeypatch, data=None):
        """Set up duplo.json and PLAN.md, then run main() with mocks."""
        _write_duplo_json(tmp_path, data or self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
        ):
            main()

    def test_prints_status_and_run_mcloop(self, capsys, tmp_path, monkeypatch):
        """Phase 2 PLAN.md with unchecked tasks → status summary + run mcloop."""
        self._run_main(tmp_path, monkeypatch)

        out = capsys.readouterr().out
        assert "Phase 1 complete" in out
        assert "1/3 features implemented" in out
        assert "uncompleted tasks" in out
        assert "Run mcloop to continue building" in out

    def test_status_includes_app_name(self, capsys, tmp_path, monkeypatch):
        """Status line includes the app name prefix."""
        self._run_main(tmp_path, monkeypatch)

        out = capsys.readouterr().out
        assert "TestApp: Phase 1 complete" in out

    def test_phase_label_from_roadmap(self, capsys, tmp_path, monkeypatch):
        """Phase label includes the title from the roadmap."""
        self._run_main(tmp_path, monkeypatch)

        out = capsys.readouterr().out
        assert "Phase 1: Search & Export" in out

    def test_does_not_generate_plan(self, tmp_path, monkeypatch):
        """Should NOT call generate_phase_plan when PLAN.md has unchecked tasks."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.generate_phase_plan") as mock_gen,
        ):
            main()

        mock_gen.assert_not_called()

    def test_does_not_complete_phase(self, tmp_path, monkeypatch):
        """Should NOT call _complete_phase when tasks remain unchecked."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.append_phase_to_history") as mock_append,
        ):
            main()

        mock_append.assert_not_called()

    def test_returns_without_advancing(self, tmp_path, monkeypatch):
        """current_phase and phases should remain unchanged after the run."""
        self._run_main(tmp_path, monkeypatch)

        data = _read_duplo_json(tmp_path)
        assert data["current_phase"] == 1
        assert len(data["phases"]) == 1

    def test_status_with_open_issues(self, capsys, tmp_path, monkeypatch):
        """Status line includes open issue count when issues exist."""
        data = {
            **self._BASE_DATA,
            "issues": [
                {"description": "crash on load", "status": "open"},
                {"description": "typo fixed", "status": "resolved"},
            ],
        }
        self._run_main(tmp_path, monkeypatch, data=data)

        out = capsys.readouterr().out
        assert "1 open issues" in out


class TestPhase2CompleteRunDuplo:
    """After mcloop completes Phase 2 (all tasks checked), run duplo again.

    Confirms:
    1. Annotated tasks are tracked deterministically (no Claude call needed).
    2. Issues prompt appears.
    3. Roadmap is regenerated if consumed.
    4. Phase 3 is ready.
    """

    _FEATURES = [
        {
            "name": "Auth",
            "description": "Login",
            "category": "core",
            "status": "implemented",
            "implemented_in": "Phase 1: Core",
        },
        {
            "name": "Search",
            "description": "Find things",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
        {
            "name": "Export",
            "description": "Export data",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
        {
            "name": "Notifications",
            "description": "Alerts",
            "category": "core",
            "status": "pending",
            "implemented_in": "",
        },
    ]

    _PHASE2_PLAN_COMPLETE = (
        "# TestApp — Phase 2: Search & Export\n\n"
        '- [x] Implement full-text search [feat: "Search"]\n'
        '- [x] Add export button [feat: "Export"]\n'
        '- [x] Fix crash on empty query [fix: "empty query crash"]\n'
    )

    _BASE_DATA = {
        "source_url": "https://example.com",
        "app_name": "TestApp",
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Core features",
                "features": ["Auth"],
                "test": "ok",
            },
            {
                "phase": 1,
                "title": "Search & Export",
                "goal": "Add search and export",
                "features": ["Search", "Export"],
                "test": "ok",
            },
        ],
        "current_phase": 1,
        "phases": [
            {
                "phase": "Phase 1: Core",
                "plan": "- [x] Set up auth",
                "completed_at": "2026-03-01T00:00:00",
            },
        ],
        "issues": [
            {
                "description": "empty query crash",
                "source": "user",
                "phase": "Phase 1: Core",
                "status": "open",
            },
        ],
    }

    def _make_data(self, **overrides):
        data = {**self._BASE_DATA, "features": list(self._FEATURES)}
        data.update(overrides)
        return data

    def _new_roadmap(self):
        return [
            {
                "phase": 0,
                "title": "Alerts",
                "goal": "Notify users",
                "features": ["Notifications"],
                "test": "ok",
            },
        ]

    def test_annotated_features_tracked_without_claude(self, tmp_path, monkeypatch, capsys):
        """All tasks have [feat:]/[fix:] annotations → no Claude call."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.match_unannotated_tasks") as mock_matcher,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        # Claude matcher should NOT be called — all tasks were annotated.
        mock_matcher.assert_not_called()

        # Features should be marked as implemented.
        result = _read_duplo_json(tmp_path)
        search = next(f for f in result["features"] if f["name"] == "Search")
        assert search["status"] == "implemented"
        export = next(f for f in result["features"] if f["name"] == "Export")
        assert export["status"] == "implemented"

        out = capsys.readouterr().out
        assert "2 annotated feature" in out

    def test_fix_annotations_resolve_issues(self, tmp_path, monkeypatch, capsys):
        """[fix:] annotations resolve matching issues in duplo.json."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        result = _read_duplo_json(tmp_path)
        issue = next(i for i in result["issues"] if i["description"] == "empty query crash")
        assert issue["status"] == "resolved"

        out = capsys.readouterr().out
        assert "1 annotated fix" in out

    def test_issues_prompt_appears(self, tmp_path, monkeypatch, capsys):
        """collect_issues is called during phase completion."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=["Search is slow"]) as mock_issues,
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        mock_issues.assert_called_once()

        result = _read_duplo_json(tmp_path)
        user_issues = [i for i in result["issues"] if i.get("source") == "user"]
        descs = [i["description"] for i in user_issues]
        assert "Search is slow" in descs

    def test_roadmap_regenerated_when_consumed(self, tmp_path, monkeypatch, capsys):
        """When roadmap is fully consumed, generate_roadmap is called."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ) as mock_roadmap,
        ):
            main()

        mock_roadmap.assert_called_once()

        out = capsys.readouterr().out
        assert "remaining feature" in out.lower()

    def test_phase3_plan_generated(self, tmp_path, monkeypatch, capsys):
        """After completion + roadmap regen, Phase 3 PLAN.md is generated."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        phase3_plan = (
            '# TestApp — Phase 3: Alerts\n\n- [ ] Add notifications [feat: "Notifications"]\n'
        )

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch("duplo.main.generate_phase_plan", return_value=phase3_plan) as mock_plan,
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args
        assert call_kwargs.kwargs.get("phase_number") == 3

        plan_text = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "Phase 3" in plan_text

        out = capsys.readouterr().out
        assert "Phase 3" in out
        assert "Run mcloop to start building" in out

    def test_plan_deleted_after_completion(self, tmp_path, monkeypatch):
        """PLAN.md is deleted during phase completion, then recreated."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN_COMPLETE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        result = _read_duplo_json(tmp_path)
        assert len(result["phases"]) == 2
        assert "Phase 2" in result["phases"][1]["phase"]

    def test_mixed_annotated_and_unannotated(self, tmp_path, monkeypatch, capsys):
        """Annotated tracked deterministically, unannotated sent to Claude."""
        data = self._make_data()
        _write_duplo_json(tmp_path, data)
        mixed_plan = (
            "# TestApp — Phase 2: Search & Export\n\n"
            '- [x] Implement search [feat: "Search"]\n'
            "- [x] Set up CI pipeline\n"
            '- [x] Add export [feat: "Export"]\n'
        )
        (tmp_path / "PLAN.md").write_text(mixed_plan, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.collect_feedback", return_value=""),
            patch("duplo.main.collect_issues", return_value=[]),
            patch("duplo.main.notify_phase_complete"),
            patch("duplo.main.capture_appshot", return_value=-1),
            patch("duplo.main.match_unannotated_tasks", return_value=([], [])) as mock_matcher,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.main.generate_roadmap",
                return_value=self._new_roadmap(),
            ),
        ):
            main()

        result = _read_duplo_json(tmp_path)
        search = next(f for f in result["features"] if f["name"] == "Search")
        assert search["status"] == "implemented"
        export = next(f for f in result["features"] if f["name"] == "Export")
        assert export["status"] == "implemented"

        mock_matcher.assert_called_once()

        out = capsys.readouterr().out
        assert "2 annotated feature" in out
        assert "1 unannotated task" in out


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


class TestSubsequentRunSpecVerificationIndependent:
    """SPEC verification tasks must append independently of video frames.

    Covers four modes:
    (a) frame_descs empty, SPEC present → spec verification appended.
    (b) frame_descs non-empty, vcases empty, SPEC present → no crash.
    (c) frame_descs non-empty, vcases non-empty, SPEC absent → no crash.
    (d) Happy path: frame_descs non-empty, vcases non-empty, SPEC present.
    """

    _BASE_DATA = {
        "source_url": "",
        "app_name": "TestApp",
        "current_phase": 1,
        "roadmap": [
            {"phase": 1, "title": "Core", "goal": "Build core", "features": ["Search"]},
        ],
        "features": [
            {"name": "Search", "description": "Search stuff", "category": "core"},
        ],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
    }

    _SPEC_VTASKS = (
        "\n## Functional verification from product spec\n\n"
        "- [ ] Verify: type `1+1`, expect result `2`\n"
    )

    def _setup(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        from duplo.hasher import compute_hashes, save_hashes

        hashes = compute_hashes(tmp_path)
        save_hashes(hashes, directory=tmp_path)
        monkeypatch.chdir(tmp_path)

    def _make_spec(self):
        """Return a mock spec object with one behavior contract."""
        contract = type("C", (), {"input": "1+1", "expected": "2"})()
        design = type("D", (), {"user_prose": "", "auto_generated": ""})()
        return type(
            "Spec",
            (),
            {
                "raw": "x",
                "behavior_contracts": [contract],
                "scope_include": None,
                "scope_exclude": None,
                "purpose": "A" * 50,
                "scope": "",
                "behavior": "",
                "architecture": "",
                "design": design,
                "sources": [],
                "references": [],
                "notes": "",
                "fill_in_purpose": False,
                "fill_in_architecture": False,
                "fill_in_design": False,
                "dropped_sources": [],
                "dropped_references": [],
            },
        )()

    def test_no_frames_with_spec(self, capsys, tmp_path, monkeypatch):
        """(a) No frame_descs, SPEC present → spec vtasks appended."""
        self._setup(tmp_path, monkeypatch)
        from duplo.extractor import Feature as F

        feat = F(name="Search", description="Search stuff", category="core")
        spec = self._make_spec()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.load_frame_descriptions", return_value=[]),
            patch(
                "duplo.main.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ) as mock_fmt,
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.main.save_plan", return_value="PLAN.md") as mock_save,
        ):
            main()

        mock_fmt.assert_called_once()
        saved_content = mock_save.call_args[0][0]
        assert "Functional verification from product spec" in saved_content

    def test_frames_no_vcases_with_spec(self, capsys, tmp_path, monkeypatch):
        """(b) frame_descs non-empty, vcases empty, SPEC present → no crash."""
        self._setup(tmp_path, monkeypatch)
        from duplo.extractor import Feature as F

        feat = F(name="Search", description="Search stuff", category="core")
        spec = self._make_spec()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch("duplo.main.extract_verification_cases", return_value=[]),
            patch(
                "duplo.main.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.main.save_plan", return_value="PLAN.md") as mock_save,
        ):
            main()

        saved_content = mock_save.call_args[0][0]
        assert "Functional verification from product spec" in saved_content

    def test_frames_with_vcases_no_spec(self, capsys, tmp_path, monkeypatch):
        """(c) frame_descs non-empty, vcases non-empty, SPEC absent → no crash."""
        self._setup(tmp_path, monkeypatch)
        from duplo.extractor import Feature as F
        from duplo.verification_extractor import VerificationCase

        feat = F(name="Search", description="Search stuff", category="core")
        vcases = [VerificationCase(input="1+1", expected="2", frame="f.png")]

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch(
                "duplo.main.extract_verification_cases",
                return_value=vcases,
            ),
            patch(
                "duplo.main.format_verification_tasks",
                return_value="\n- [ ] Verify: type `1+1`, expect `2`\n",
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.main.save_plan", return_value="PLAN.md") as mock_save,
        ):
            main()

        saved_content = mock_save.call_args[0][0]
        assert "Verify: type `1+1`" in saved_content
        assert "Functional verification from product spec" not in saved_content

    def test_happy_path_frames_vcases_and_spec(self, capsys, tmp_path, monkeypatch):
        """(d) frame_descs + vcases + SPEC → both appended."""
        self._setup(tmp_path, monkeypatch)
        from duplo.extractor import Feature as F
        from duplo.verification_extractor import VerificationCase

        feat = F(name="Search", description="Search stuff", category="core")
        vcases = [VerificationCase(input="1+1", expected="2", frame="f.png")]
        spec = self._make_spec()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch(
                "duplo.main.extract_verification_cases",
                return_value=vcases,
            ),
            patch(
                "duplo.main.format_verification_tasks",
                return_value="\n- [ ] Verify: type `1+1`, expect `2`\n",
            ),
            patch(
                "duplo.main.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.main.save_plan", return_value="PLAN.md") as mock_save,
        ):
            main()

        saved_content = mock_save.call_args[0][0]
        assert "Verify: type `1+1`" in saved_content
        assert "Functional verification from product spec" in saved_content


class TestValidateForRunWiring:
    """validate_for_run is called after read_spec in _first_run and _subsequent_run."""

    def test_first_run_exits_on_validation_errors(self, tmp_path, monkeypatch, capsys):
        """_first_run exits 1 when validate_for_run returns errors."""
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
        monkeypatch.chdir(tmp_path)

        from duplo.spec_reader import ValidationResult

        mock_spec = type("Spec", (), {"raw": "x" * 100})()
        vr = ValidationResult(
            errors=["## Purpose still contains <FILL IN>"],
            warnings=["some warning"],
        )

        with (
            patch("duplo.main.read_spec", return_value=mock_spec),
            patch("duplo.main.validate_for_run", return_value=vr),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "some warning" in captured.out
        assert "## Purpose still contains <FILL IN>" in captured.err

    def test_first_run_continues_on_warnings_only(self, tmp_path, monkeypatch, capsys):
        """_first_run prints warnings but does not exit when no errors."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "links.txt").write_text("https://example.com")
        monkeypatch.chdir(tmp_path)

        from duplo.spec_reader import DesignBlock, ValidationResult

        mock_spec = type(
            "Spec",
            (),
            {
                "raw": "x" * 100,
                "architecture": "",
                "behavior_contracts": [],
                "scope_include": None,
                "scope_exclude": None,
                "references": [],
                "sources": [],
                "design": DesignBlock(),
            },
        )()
        vr = ValidationResult(
            errors=[],
            warnings=["design will be inferred"],
        )

        with (
            patch("duplo.main.read_spec", return_value=mock_spec),
            patch("duplo.main.validate_for_run", return_value=vr),
            patch("duplo.main.format_spec_for_prompt", return_value=""),
            patch(
                "duplo.main._validate_url",
                return_value=("https://example.com", "Example"),
            ),
            patch("duplo.main._confirm_product", return_value="Example"),
            patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md", return_value=tmp_path / "CLAUDE.md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            main()

        captured = capsys.readouterr()
        assert "design will be inferred" in captured.out

    def test_subsequent_run_exits_on_validation_errors(self, tmp_path, monkeypatch, capsys):
        """_subsequent_run exits 1 when validate_for_run returns errors."""
        _write_duplo_json(tmp_path, {"features": []})
        monkeypatch.chdir(tmp_path)

        from duplo.spec_reader import ValidationResult

        mock_spec = type("Spec", (), {"raw": "x" * 100})()
        vr = ValidationResult(
            errors=["## Architecture still contains <FILL IN>"],
            warnings=[],
        )

        with (
            patch("duplo.main.read_spec", return_value=mock_spec),
            patch("duplo.main.validate_for_run", return_value=vr),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "## Architecture still contains <FILL IN>" in captured.err

    def test_no_validation_when_no_spec(self, tmp_path, monkeypatch):
        """When read_spec returns None, validate_for_run is not called."""
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch("duplo.main.validate_for_run") as mock_validate,
            patch("duplo.main.scan_directory") as mock_scan,
        ):
            mock_scan.return_value = type(
                "S",
                (),
                {
                    "images": [],
                    "videos": [],
                    "pdfs": [],
                    "text_files": [],
                    "urls": [],
                },
            )()
            with pytest.raises(SystemExit):
                main()

        mock_validate.assert_not_called()

    def test_no_validation_when_no_spec_subsequent_run(self, tmp_path, monkeypatch):
        """When read_spec returns None on a subsequent run, validate_for_run is not called."""
        _write_duplo_json(tmp_path, {"features": []})
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch("duplo.main.validate_for_run") as mock_validate,
            patch("duplo.main.load_hashes", return_value={}),
            patch("duplo.main.compute_hashes", return_value={}),
            patch("duplo.main.diff_hashes") as mock_diff,
            patch("duplo.main._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.main.save_hashes"),
        ):
            mock_diff.return_value = type("D", (), {"added": [], "changed": [], "removed": []})()
            # _subsequent_run will proceed past validation into the
            # no-changes path. It will eventually try to generate a plan
            # or roadmap, so let it exit naturally or via SystemExit.
            try:
                main()
            except (SystemExit, Exception):
                pass

        mock_validate.assert_not_called()


class TestFillInPurposeBlocksRun:
    """End-to-end: a real SPEC.md with <FILL IN> in ## Purpose exits 1."""

    SPEC_WITH_FILL_IN = """\
# My App

## Purpose

<FILL IN>

## Sources

- https://example.com
  role: product-reference
  scrape: deep
"""

    def test_first_run_blocked_by_fill_in_purpose(self, tmp_path, monkeypatch, capsys):
        """First run (no .duplo/) exits 1 when Purpose has <FILL IN>."""
        (tmp_path / "SPEC.md").write_text(self.SPEC_WITH_FILL_IN)
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.scan_directory") as mock_scan,
            patch("duplo.main.fetch_site") as mock_fetch,
            patch("duplo.main.extract_features") as mock_extract,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Purpose" in captured.err
        assert "FILL IN" in captured.err

        # Must NOT have proceeded to scraping or extraction.
        mock_scan.assert_not_called()
        mock_fetch.assert_not_called()
        mock_extract.assert_not_called()

    def test_subsequent_run_blocked_by_fill_in_purpose(self, tmp_path, monkeypatch, capsys):
        """Subsequent run (.duplo/ exists) exits 1 when Purpose has <FILL IN>."""
        _write_duplo_json(tmp_path, {"features": []})
        (tmp_path / "SPEC.md").write_text(self.SPEC_WITH_FILL_IN)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.compute_hashes") as mock_hash,
            patch("duplo.main.fetch_site") as mock_fetch,
            patch("duplo.main.extract_features") as mock_extract,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Purpose" in captured.err
        assert "FILL IN" in captured.err

        # Must NOT have proceeded to hashing, scraping, or extraction.
        mock_hash.assert_not_called()
        mock_fetch.assert_not_called()
        mock_extract.assert_not_called()


class TestMigrationDispatchOrder:
    """Migration check runs for no-subcommand but not fix/investigate."""

    def test_no_subcommand_calls_check_migration(self, tmp_path, monkeypatch):
        """No-subcommand path calls _check_migration before proceeding."""
        monkeypatch.chdir(tmp_path)
        called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: called.append(target_dir),
        )
        # No .duplo/duplo.json → first run → exits due to no refs.
        with pytest.raises(SystemExit):
            main()
        assert len(called) == 1
        assert called[0] == Path.cwd()

    def test_fix_skips_check_migration(self, tmp_path, monkeypatch):
        """duplo fix dispatches without calling _check_migration."""
        from duplo.investigator import InvestigationResult

        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "app_name": "App",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {"phase": 0, "title": "Core", "goal": "g", "features": [], "test": "ok"},
                ],
                "current_phase": 0,
            },
        )
        (tmp_path / "PLAN.md").write_text("- [x] done\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "some bug"])

        called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: called.append(target_dir),
        )

        result = InvestigationResult(diagnoses=[], summary="", raw_response="")
        with patch("duplo.main.investigate", return_value=result):
            main()

        assert called == []

    def test_investigate_skips_check_migration(self, tmp_path, monkeypatch):
        """duplo investigate dispatches without calling _check_migration."""
        from duplo.investigator import InvestigationResult

        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "app_name": "App",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {"phase": 0, "title": "Core", "goal": "g", "features": [], "test": "ok"},
                ],
                "current_phase": 0,
            },
        )
        (tmp_path / "PLAN.md").write_text("- [x] done\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["duplo", "investigate", "some bug"],
        )

        called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: called.append(target_dir),
        )

        result = InvestigationResult(diagnoses=[], summary="", raw_response="")
        with patch("duplo.main.investigate", return_value=result):
            main()

        assert called == []

    def test_migration_blocks_no_subcommand(
        self,
        capsys,
        tmp_path,
        monkeypatch,
    ):
        """When migration is needed, no-subcommand path exits with message."""
        from duplo.migration import _check_migration as real_check

        monkeypatch.setattr("duplo.main._check_migration", real_check)

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        # No SPEC.md → needs_migration returns True.
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "SPEC.md" in captured.out

    def test_old_layout_prints_message_exits_skips_runs(
        self,
        capsys,
        tmp_path,
        monkeypatch,
    ):
        """duplo (no args) in old-layout dir: prints migration, exits 1,
        never calls _first_run or _subsequent_run."""
        from duplo.migration import _check_migration as real_check

        monkeypatch.setattr("duplo.main._check_migration", real_check)

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        # No SPEC.md → old layout → needs migration.
        monkeypatch.chdir(tmp_path)

        first_run_called = []
        subsequent_run_called = []
        monkeypatch.setattr(
            "duplo.main._first_run",
            lambda **kw: first_run_called.append(kw),
        )
        monkeypatch.setattr(
            "duplo.main._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SPEC.md" in captured.out
        assert first_run_called == []
        assert subsequent_run_called == []

    def test_new_format_dir_passes_migration_proceeds(
        self,
        capsys,
        tmp_path,
        monkeypatch,
    ):
        """duplo (no args) in new-format dir: migration passes silently,
        proceeds to existing dispatch (subsequent run, since duplo.json
        exists). May exit for other reasons but NOT the migration message."""
        from duplo.migration import _check_migration as real_check

        monkeypatch.setattr("duplo.main._check_migration", real_check)

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text('{"features": []}')
        # New-format SPEC.md with ## Sources heading.
        (tmp_path / "SPEC.md").write_text("# My App\n\n## Purpose\nA test app.\n\n## Sources\n")
        monkeypatch.chdir(tmp_path)

        subsequent_run_called = []
        first_run_called = []
        monkeypatch.setattr(
            "duplo.main._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )
        monkeypatch.setattr(
            "duplo.main._first_run",
            lambda **kw: first_run_called.append(kw),
        )

        main()

        # Migration did NOT fire — no migration message in output.
        captured = capsys.readouterr()
        assert "SPEC.md" not in captured.out
        assert "Migrate manually" not in captured.out
        # Proceeded to subsequent run (duplo.json exists).
        assert len(subsequent_run_called) == 1
        assert first_run_called == []

    def test_migration_pass_proceeds_to_first_run(self, tmp_path, monkeypatch):
        """When _check_migration returns, _first_run is called (no duplo.json)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        first_run_called = []
        monkeypatch.setattr(
            "duplo.main._first_run",
            lambda **kw: first_run_called.append(kw),
        )
        main()
        assert len(first_run_called) == 1

    def test_migration_pass_proceeds_to_subsequent_run(self, tmp_path, monkeypatch):
        """When _check_migration returns, _subsequent_run is called (duplo.json exists)."""
        _write_duplo_json(tmp_path, {"features": []})
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        subsequent_run_called = []
        monkeypatch.setattr(
            "duplo.main._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )
        main()
        assert len(subsequent_run_called) == 1

    def test_init_is_not_a_subcommand(self, tmp_path, monkeypatch):
        """'duplo init' is not a recognised subcommand (lands in Phase 4).

        Today it falls through to the no-subcommand path, so
        _check_migration is called and 'init' is parsed as the url arg.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init"])

        migration_called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: migration_called.append(target_dir),
        )
        first_run_called = []
        monkeypatch.setattr(
            "duplo.main._first_run",
            lambda **kw: first_run_called.append(kw),
        )
        main()
        # Went through no-subcommand path (migration check ran).
        assert len(migration_called) == 1
        # 'init' was treated as the url positional arg, not a subcommand.
        assert len(first_run_called) == 1
        assert first_run_called[0]["url"] == "init"

    def test_fix_old_layout_bypasses_migration_dispatches_fix(
        self,
        tmp_path,
        monkeypatch,
    ):
        """duplo fix in an old-layout dir: skips _check_migration,
        dispatches to _fix_mode."""
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        # No SPEC.md → old layout that would trigger migration on bare duplo.
        (tmp_path / "PLAN.md").write_text("- [x] done\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "some bug"])

        migration_called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: migration_called.append(target_dir),
        )

        fix_mode_called = []
        monkeypatch.setattr(
            "duplo.main._fix_mode",
            lambda args: fix_mode_called.append(args),
        )

        main()

        assert migration_called == []
        assert len(fix_mode_called) == 1
        assert fix_mode_called[0].command == "fix"

    def test_investigate_old_layout_bypasses_migration_dispatches_fix_mode(
        self,
        tmp_path,
        monkeypatch,
    ):
        """duplo investigate in an old-layout dir: skips _check_migration,
        dispatches to _fix_mode."""
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        # No SPEC.md → old layout that would trigger migration on bare duplo.
        (tmp_path / "PLAN.md").write_text("- [x] done\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "investigate", "some bug"])

        migration_called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: migration_called.append(target_dir),
        )

        fix_mode_called = []
        monkeypatch.setattr(
            "duplo.main._fix_mode",
            lambda args: fix_mode_called.append(args),
        )

        main()

        assert migration_called == []
        assert len(fix_mode_called) == 1
        assert fix_mode_called[0].command == "investigate"


class TestBehavioralPathsDuplicateAssertion:
    """Assert that duplicate paths in the behavioral video set are rejected."""

    def test_duplicate_path_raises_with_spec(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Same video path in both ref/ entries and site_videos triggers assertion."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        vid = ref_dir / "demo.mp4"
        vid.write_bytes(b"MP4" * 100)

        # Use the same relative path that format_behavioral_references returns
        dup_path = Path("ref/demo.mp4")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=dup_path,
                    roles=["behavioral-target"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [dup_path]),
            ),
            patch("duplo.main.extract_all_videos", return_value=[]),
            patch("duplo.main.extract_design"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
            pytest.raises(AssertionError, match="Duplicate source path"),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type("V", (), {"warnings": [], "errors": []})()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

    def test_no_assertion_when_paths_distinct(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Distinct ref/ and site_media/ paths pass the assertion."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        beh_vid = ref_dir / "demo.mp4"
        beh_vid.write_bytes(b"MP4" * 100)

        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch("duplo.main.extract_all_videos", return_value=[]) as mock_ev,
            patch("duplo.main.extract_design"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type("V", (), {"warnings": [], "errors": []})()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_ev.assert_called_once()
        paths = mock_ev.call_args[0][0]
        assert len(paths) == 2


class TestDocsTextInFeatureExtraction:
    """Docs-role text feeds into extract_features via docs_text_extractor."""

    def test_first_run_includes_docs_text(self, tmp_path, monkeypatch, capsys):
        """First run: docs-role text is combined into extract_features input."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        doc_file = ref_dir / "guide.txt"
        doc_file.write_text("Guide content here")
        monkeypatch.chdir(tmp_path)

        spec = ProductSpec(
            raw="test spec",
            references=[
                ReferenceEntry(path=Path("ref/guide.txt"), roles=["docs"]),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.validate_for_run",
                return_value=type("V", (), {"warnings": [], "errors": []})(),
            ),
            patch(
                "duplo.main.scan_directory",
                return_value=type(
                    "S",
                    (),
                    {
                        "images": [],
                        "videos": [],
                        "pdfs": [],
                        "text_files": [],
                        "urls": ["https://example.com"],
                        "roles": {},
                    },
                )(),
            ),
            patch(
                "duplo.main._validate_url",
                return_value=("https://example.com", "Example"),
            ),
            patch("duplo.main._confirm_product", return_value="Example"),
            patch(
                "duplo.main.fetch_site",
                return_value=("scraped text", [], None, [], {}),
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value="docs extracted text",
            ) as mock_docs,
            patch("duplo.main.extract_features", return_value=[]) as mock_ef,
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            main()

        mock_docs.assert_called_once()
        entries = mock_docs.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].path == Path("ref/guide.txt")

        mock_ef.assert_called_once()
        combined = mock_ef.call_args[0][0]
        assert "docs extracted text" in combined
        assert "scraped text" in combined

    def test_first_run_no_docs_without_spec(self, tmp_path, monkeypatch):
        """Without spec, docs_text_extractor is not called."""
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        (ref_dir / "links.txt").write_text("https://example.com")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch(
                "duplo.main._validate_url",
                return_value=("https://example.com", "Example"),
            ),
            patch("duplo.main._confirm_product", return_value="Ex"),
            patch(
                "duplo.main.fetch_site",
                return_value=("text", [], None, [], {}),
            ),
            patch("duplo.main.docs_text_extractor") as mock_docs,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(platform="web", language="Python"),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            main()

        mock_docs.assert_not_called()

    def test_subsequent_run_includes_docs_text(self, tmp_path, monkeypatch, capsys):
        """Subsequent run: docs text feeds into re-extraction."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        _write_duplo_json(
            tmp_path,
            {
                "features": [{"name": "F1", "description": "d", "category": "c"}],
                "source_url": "",
            },
        )
        (tmp_path / "SPEC.md").write_text("spec")
        monkeypatch.chdir(tmp_path)

        spec = ProductSpec(
            raw="spec",
            references=[
                ReferenceEntry(path=Path("ref/notes.md"), roles=["docs"]),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.validate_for_run",
                return_value=type("V", (), {"warnings": [], "errors": []})(),
            ),
            patch(
                "duplo.main.compute_hashes",
                return_value={"a.txt": "abc"},
            ),
            patch("duplo.main.load_hashes", return_value={"a.txt": "abc"}),
            patch(
                "duplo.main.diff_hashes",
                return_value=type(
                    "D",
                    (),
                    {"added": [], "changed": [], "removed": []},
                )(),
            ),
            patch("duplo.main.save_hashes"),
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(0, [], "rescraped"),
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value="docs text from md",
            ) as mock_docs,
            patch(
                "duplo.main.extract_features",
                return_value=[Feature(name="F1", description="d", category="c")],
            ) as mock_ef,
            patch("duplo.main.save_features"),
            patch("duplo.main._detect_and_append_gaps"),
            patch("duplo.main._print_summary"),
        ):
            # PLAN.md with unchecked tasks -> state 2 (tells user
            # to run mcloop).
            (tmp_path / "PLAN.md").write_text("- [ ] task\n")
            main()

        mock_docs.assert_called_once()
        entries = mock_docs.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].roles == ["docs"]

        mock_ef.assert_called_once()
        combined = mock_ef.call_args[0][0]
        assert "docs text from md" in combined


class TestScopeExcludeAtOrchestratorLevel:
    """scope_exclude filtering happens in main.py, not inside extract_features."""

    _BASE_DATA = {
        "source_url": "https://example.com",
        "features": [
            {"name": "Auth", "description": "Login.", "category": "core"},
        ],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Core",
                "features": ["Auth"],
                "test": "ok",
            },
        ],
        "current_phase": 0,
    }

    def test_subsequent_run_filters_excluded_features(self, tmp_path, monkeypatch):
        """Features matching scope_exclude are dropped before save_features."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")

        spec_md = (
            "## Purpose\n"
            "A full-featured scientific calculator application "
            "for desktop platforms.\n"
            "## Scope\n- exclude: CLI tool\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_md, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        kept = Feature(name="Math", description="Basic math.", category="core")
        excluded = Feature(name="CLI tool", description="Command line.", category="other")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[kept, excluded]),
            patch("duplo.main.save_features") as mock_save,
            patch(
                "duplo.main._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_save.assert_called_once_with([kept])

    def test_subsequent_run_no_spec_skips_filter(self, tmp_path, monkeypatch):
        """Without a spec, all extracted features pass through."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        feat_a = Feature(name="Math", description="Basic.", category="core")
        feat_b = Feature(name="CLI tool", description="Command line.", category="other")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[feat_a, feat_b]),
            patch("duplo.main.save_features") as mock_save,
            patch(
                "duplo.main._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_save.assert_called_once_with([feat_a, feat_b])

    def test_subsequent_run_all_excluded_skips_save(self, tmp_path, monkeypatch, capsys):
        """When all features are excluded, save_features is not called."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")

        spec_md = (
            "## Purpose\n"
            "A full-featured scientific calculator application "
            "for desktop platforms.\n"
            "## Scope\n- exclude: CLI tool\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_md, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        excluded = Feature(name="CLI tool", description="Command line.", category="other")
        with (
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.main.extract_features", return_value=[excluded]),
            patch("duplo.main.save_features") as mock_save,
            patch(
                "duplo.main._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_save.assert_not_called()
        out = capsys.readouterr().out
        assert "No features extracted" in out or "No new features" in out


class TestInvestigationContext:
    """Tests for _investigation_context role-filtered kwarg builder."""

    def test_none_spec_returns_empty(self):
        result = _investigation_context(None)
        assert result == {}

    def test_counter_examples_included(self):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        ce = ReferenceEntry(
            path=Path("ref/bad.png"),
            roles=["counter-example"],
            notes="Avoid",
        )
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[],
            architecture="",
            references=[ce],
        )
        result = _investigation_context(spec)
        assert "counter_examples" in result
        assert len(result["counter_examples"]) == 1
        assert result["counter_examples"][0].path == Path("ref/bad.png")

    def test_counter_example_sources_included(self):
        from duplo.spec_reader import ProductSpec, SourceEntry

        ces = SourceEntry(
            url="https://bad.com",
            role="counter-example",
            scrape="none",
            notes="Don't do this",
        )
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[],
            architecture="",
            references=[],
            sources=[ces],
        )
        result = _investigation_context(spec)
        assert "counter_example_sources" in result
        assert result["counter_example_sources"][0].url == "https://bad.com"

    def test_behavior_contracts_included(self):
        from duplo.spec_reader import BehaviorContract, ProductSpec

        bc = BehaviorContract(input="2+3", expected="5")
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[bc],
            architecture="",
            references=[],
            sources=[],
        )
        result = _investigation_context(spec)
        assert "behavior_contracts" in result
        assert len(result["behavior_contracts"]) == 1
        assert result["behavior_contracts"][0].input == "2+3"

    def test_docs_text_included(self, tmp_path):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        doc_file = tmp_path / "ref" / "guide.txt"
        doc_file.parent.mkdir(parents=True, exist_ok=True)
        doc_file.write_text("API usage guide", encoding="utf-8")

        doc_ref = ReferenceEntry(
            path=doc_file,
            roles=["docs"],
        )
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[],
            architecture="",
            references=[doc_ref],
            sources=[],
        )
        result = _investigation_context(spec)
        assert "docs_text" in result
        assert "API usage guide" in result["docs_text"]

    def test_empty_spec_returns_empty(self):
        from duplo.spec_reader import ProductSpec

        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[],
            architecture="",
            references=[],
            sources=[],
        )
        result = _investigation_context(spec)
        assert result == {}

    def test_proposed_counter_examples_excluded(self):
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        ce = ReferenceEntry(
            path=Path("ref/bad.png"),
            roles=["counter-example"],
            proposed=True,
        )
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[],
            architecture="",
            references=[ce],
            sources=[],
        )
        result = _investigation_context(spec)
        assert "counter_examples" not in result

    def test_fix_mode_passes_context_to_investigate(self, capsys, tmp_path, monkeypatch):
        """duplo fix passes role-filtered context through to investigate()."""
        from duplo.investigator import Diagnosis, InvestigationResult
        from duplo.spec_reader import (
            BehaviorContract,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "app_name": "TestApp",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {
                        "phase": 0,
                        "title": "Core",
                        "goal": "Core",
                        "features": [],
                        "test": "ok",
                    },
                ],
                "current_phase": 0,
            },
        )
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "broken"])

        ce = ReferenceEntry(
            path=Path("ref/avoid.png"),
            roles=["counter-example"],
        )
        ces = SourceEntry(
            url="https://bad.com",
            role="counter-example",
            scrape="none",
        )
        bc = BehaviorContract(input="2+3", expected="5")
        spec = ProductSpec(
            raw="",
            purpose="",
            scope="",
            scope_include=[],
            scope_exclude=[],
            behavior="",
            behavior_contracts=[bc],
            architecture="",
            references=[ce],
            sources=[ces],
        )

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="broken thing",
                    expected="should work",
                    severity="major",
                    area="core",
                ),
            ],
            summary="One bug.",
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.investigate", return_value=result) as mock_inv,
        ):
            main()

        # Verify the call included role-filtered context.
        call_kwargs = mock_inv.call_args[1]
        assert "counter_examples" in call_kwargs
        assert len(call_kwargs["counter_examples"]) == 1
        assert "counter_example_sources" in call_kwargs
        assert len(call_kwargs["counter_example_sources"]) == 1
        assert "behavior_contracts" in call_kwargs
        assert len(call_kwargs["behavior_contracts"]) == 1


class TestPrefsFromDict:
    """Tests for _prefs_from_dict helper."""

    def test_full_dict(self):
        d = {
            "platform": "web",
            "language": "Python",
            "constraints": ["pg"],
            "preferences": ["pytest"],
        }
        p = _prefs_from_dict(d)
        assert p.platform == "web"
        assert p.language == "Python"
        assert p.constraints == ["pg"]
        assert p.preferences == ["pytest"]

    def test_empty_dict(self):
        p = _prefs_from_dict({})
        assert p.platform == ""
        assert p.language == ""
        assert p.constraints == []
        assert p.preferences == []


class TestLoadPreferences:
    """Tests for _load_preferences with architecture-hash invalidation."""

    def test_returns_cached_when_no_spec(self):
        data = {
            "preferences": {
                "platform": "web",
                "language": "Go",
                "constraints": [],
                "preferences": [],
            },
        }
        result = _load_preferences(data, None)
        assert result.platform == "web"
        assert result.language == "Go"

    def test_returns_cached_when_spec_has_no_architecture(self):
        spec = MagicMock()
        spec.architecture = ""
        data = {
            "preferences": {
                "platform": "cli",
                "language": "Rust",
                "constraints": [],
                "preferences": [],
            },
        }
        result = _load_preferences(data, spec)
        assert result.platform == "cli"

    def test_returns_cached_when_hash_matches(self):
        from duplo.build_prefs import architecture_hash

        arch = "Web app in Python"
        h = architecture_hash(arch)
        spec = MagicMock()
        spec.architecture = arch
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": h,
        }
        with patch("duplo.main.parse_build_preferences") as mock_parse:
            result = _load_preferences(data, spec)
            mock_parse.assert_not_called()
        assert result.platform == "web"

    def test_reparses_when_hash_differs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "CLI tool in Rust"
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": "stale_hash",
        }
        new_prefs = BuildPreferences(
            platform="cli",
            language="Rust",
            constraints=[],
            preferences=[],
        )
        with (
            patch(
                "duplo.main.parse_build_preferences",
                return_value=new_prefs,
            ) as mock_parse,
            patch("duplo.main.save_build_preferences") as mock_save,
        ):
            result = _load_preferences(data, spec)
            mock_parse.assert_called_once_with("CLI tool in Rust")
            mock_save.assert_called_once()
        assert result.platform == "cli"
        assert result.language == "Rust"

    def test_reparses_when_no_stored_hash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "Desktop app in Swift"
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        new_prefs = BuildPreferences(
            platform="desktop",
            language="Swift",
            constraints=[],
            preferences=[],
        )
        with (
            patch(
                "duplo.main.parse_build_preferences",
                return_value=new_prefs,
            ) as mock_parse,
            patch("duplo.main.save_build_preferences"),
        ):
            result = _load_preferences(data, spec)
            mock_parse.assert_called_once()
        assert result.platform == "desktop"

    def test_updates_in_memory_data(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "API in Go"
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": "old",
        }
        new_prefs = BuildPreferences(
            platform="api",
            language="Go",
            constraints=[],
            preferences=[],
        )
        with (
            patch(
                "duplo.main.parse_build_preferences",
                return_value=new_prefs,
            ),
            patch("duplo.main.save_build_preferences"),
        ):
            _load_preferences(data, spec)
        assert data["preferences"]["platform"] == "api"
        assert data["architecture_hash"] != "old"


class TestScrapeDeclaredSources:
    """Tests for _scrape_declared_sources multi-source iteration."""

    def _make_spec(self, sources):
        from duplo.spec_reader import ProductSpec

        return ProductSpec(sources=sources)

    def _make_source(self, url, role="product-reference", scrape="deep"):
        from duplo.spec_reader import SourceEntry

        return SourceEntry(url=url, role=role, scrape=scrape)

    def test_calls_fetch_site_per_source(self):
        """fetch_site called once per scrapeable source."""
        src_a = self._make_source("https://a.com", scrape="deep")
        src_b = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_a, src_b])

        calls = []

        def fake_fetch(url, *, scrape_depth="deep"):
            calls.append((url, scrape_depth))
            return ("text", [], None, [], {})

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(calls) == 2
        assert calls[0] == ("https://a.com", "deep")
        assert calls[1] == ("https://b.com", "shallow")
        assert "text" in result.combined_text

    def test_first_source_wins_page_records(self):
        """Duplicate canonical URL keeps first source's record."""
        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs")
        spec = self._make_spec([src_a, src_b])

        record_a = PageRecord(
            url="https://shared.com/page",
            fetched_at="t1",
            content_hash="hash_a",
        )
        record_b = PageRecord(
            url="https://shared.com/page",
            fetched_at="t2",
            content_hash="hash_b",
        )

        fetch_results = [
            ("text_a", [], None, [record_a], {}),
            ("text_b", [], None, [record_b], {}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.all_page_records) == 1
        assert result.all_page_records[0].content_hash == "hash_a"

    def test_first_source_wins_raw_pages(self):
        """Duplicate canonical URL in raw_pages keeps first HTML."""
        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs")
        spec = self._make_spec([src_a, src_b])

        fetch_results = [
            ("", [], None, [], {"https://shared.com": "<html>A</html>"}),
            ("", [], None, [], {"https://shared.com": "<html>B</html>"}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert result.all_raw_pages["https://shared.com"] == "<html>A</html>"

    def test_discovered_urls_only_from_deep(self):
        """Cross-origin links collected only from deep sources."""
        src_deep = self._make_source("https://a.com", scrape="deep")
        src_shallow = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_deep, src_shallow])

        deep_raw = {"https://a.com": ('<html><a href="https://x.com">link</a></html>')}
        shallow_raw = {"https://b.com": ('<html><a href="https://y.com">link</a></html>')}

        fetch_results = [
            ("", [], None, [], deep_raw),
            ("", [], None, [], shallow_raw),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_deep, src_shallow],
            ),
        ):
            result = _scrape_declared_sources(spec)

        # Deep source's cross-origin link is collected.
        assert "https://x.com" in result.discovered_urls
        # Shallow source's cross-origin link is NOT collected.
        assert "https://y.com" not in result.discovered_urls

    def test_product_ref_raw_pages_only_from_product_ref(self):
        """Only product-reference sources contribute to product_ref."""
        src_prod = self._make_source("https://prod.com", role="product-reference")
        src_docs = self._make_source("https://docs.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_prod, src_docs])

        fetch_results = [
            (
                "",
                [],
                None,
                [],
                {"https://prod.com": "<html>prod</html>"},
            ),
            (
                "",
                [],
                None,
                [],
                {"https://docs.com": "<html>docs</html>"},
            ),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_prod, src_docs],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert "https://prod.com" in result.product_ref_raw_pages
        assert "https://docs.com" not in result.product_ref_raw_pages
        # Both present in all_raw_pages.
        assert "https://prod.com" in result.all_raw_pages
        assert "https://docs.com" in result.all_raw_pages

    def test_first_source_wins_product_ref_raw_pages(self):
        """Duplicate URL across product-reference sources keeps first HTML."""
        src_a = self._make_source("https://a.com", role="product-reference")
        src_b = self._make_source("https://b.com", role="product-reference")
        spec = self._make_spec([src_a, src_b])

        fetch_results = [
            ("", [], None, [], {"https://shared.com": "<html>A</html>"}),
            ("", [], None, [], {"https://shared.com": "<html>B</html>"}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert result.product_ref_raw_pages["https://shared.com"] == "<html>A</html>"

    def test_doc_structures_merged(self):
        """Doc structures accumulated across sources."""
        from duplo.doc_tables import DocStructures, FeatureTable

        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs")
        spec = self._make_spec([src_a, src_b])

        ds_a = DocStructures(
            feature_tables=[FeatureTable(heading="h", rows=[["r"]], source_url="a")]
        )
        ds_b = DocStructures(
            feature_tables=[FeatureTable(heading="h2", rows=[["r2"]], source_url="b")]
        )

        fetch_results = [
            ("", [], ds_a, [], {}),
            ("", [], ds_b, [], {}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.merged_doc_structures.feature_tables) == 2

    def test_combined_text_accumulated(self):
        """Scraped text from all sources concatenated into combined_text."""
        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_a, src_b])

        fetch_results = [
            ("alpha text", [], None, [], {}),
            ("beta text", [], None, [], {}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert "alpha text" in result.combined_text
        assert "beta text" in result.combined_text

    def test_code_examples_accumulated(self):
        """Code examples from all sources collected into all_code_examples."""
        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_a, src_b])

        ex_a = {"input": "1+1", "expected_output": "2"}
        ex_b = {"input": "2+2", "expected_output": "4"}

        fetch_results = [
            ("", [ex_a], None, [], {}),
            ("", [ex_b], None, [], {}),
        ]
        call_idx = [0]

        def fake_fetch(url, *, scrape_depth="deep"):
            idx = call_idx[0]
            call_idx[0] += 1
            return fetch_results[idx]

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.all_code_examples) == 2
        assert ex_a in result.all_code_examples
        assert ex_b in result.all_code_examples

    def test_empty_sources_returns_empty_result(self):
        """No scrapeable sources returns empty ScrapeResult."""
        spec = self._make_spec([])
        with patch("duplo.main.scrapeable_sources", return_value=[]):
            result = _scrape_declared_sources(spec)

        assert result.combined_text == ""
        assert result.all_code_examples == []
        assert result.all_page_records == []
        assert result.all_raw_pages == {}
        assert result.product_ref_raw_pages == {}
        assert result.merged_doc_structures.feature_tables == []

    def test_fetch_failure_continues(self):
        """Exception from fetch_site for one source skips it."""
        src_a = self._make_source("https://a.com")
        src_b = self._make_source("https://b.com", role="docs")
        spec = self._make_spec([src_a, src_b])

        def fake_fetch(url, *, scrape_depth="deep"):
            if url == "https://a.com":
                raise ConnectionError("timeout")
            return ("b_text", [], None, [], {})

        with (
            patch("duplo.main.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert "b_text" in result.combined_text


class TestPersistScrapeResult:
    """Tests for _persist_scrape_result saving to .duplo/."""

    def test_saves_examples(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = ScrapeResult(all_code_examples=["ex1"])
        with (
            patch("duplo.main.save_examples") as mock_save,
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
        ):
            _persist_scrape_result(result)
        mock_save.assert_called_once_with(["ex1"])

    def test_saves_page_records_and_raw(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = PageRecord(url="https://a.com", fetched_at="t", content_hash="h")
        result = ScrapeResult(
            all_page_records=[record],
            all_raw_pages={"https://a.com": "<html>A</html>"},
        )
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls") as mock_urls,
            patch("duplo.main.save_raw_content") as mock_raw,
            patch("duplo.main.save_doc_structures"),
        ):
            _persist_scrape_result(result)
        mock_urls.assert_called_once_with([record])
        mock_raw.assert_called_once()

    def test_discovered_urls_append_to_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("## Sources\n", encoding="utf-8")
        result = ScrapeResult(discovered_urls=["https://discovered.com"])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
            patch(
                "duplo.main.append_sources",
                return_value="## Sources\n- https://discovered.com\n",
            ) as mock_append,
        ):
            _persist_scrape_result(result)
        mock_append.assert_called_once()
        args = mock_append.call_args
        entries = args[0][1]
        assert len(entries) == 1
        assert entries[0].discovered is True
        assert entries[0].role == "docs"
        assert entries[0].scrape == "deep"
        # Verify SPEC.md was actually written with modified content.
        written = spec_path.read_text(encoding="utf-8")
        assert "https://discovered.com" in written

    def test_no_write_when_discovered_urls_empty(self, tmp_path, monkeypatch):
        """SPEC.md not touched when discovered_urls is empty."""
        monkeypatch.chdir(tmp_path)
        original = "## Sources\n"
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(original, encoding="utf-8")
        result = ScrapeResult(discovered_urls=[])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
            patch("duplo.main.append_sources") as mock_append,
        ):
            _persist_scrape_result(result)
        mock_append.assert_not_called()
        assert spec_path.read_text(encoding="utf-8") == original

    def test_no_write_when_spec_unchanged(self, tmp_path, monkeypatch):
        """SPEC.md not rewritten when append_sources returns same."""
        monkeypatch.chdir(tmp_path)
        original = "## Sources\n- https://discovered.com\n"
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(original, encoding="utf-8")
        result = ScrapeResult(discovered_urls=["https://discovered.com"])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
            patch("duplo.main.append_sources", return_value=original),
        ):
            _persist_scrape_result(result)
        # File content unchanged.
        assert spec_path.read_text(encoding="utf-8") == original

    def test_discovered_urls_write_flags_to_file(self, tmp_path, monkeypatch):
        """discovered: true and role: docs written to SPEC.md (no mock)."""
        monkeypatch.chdir(tmp_path)
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("## Sources\n", encoding="utf-8")
        result = ScrapeResult(discovered_urls=["https://new.example.com"])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
        ):
            _persist_scrape_result(result)
        written = spec_path.read_text(encoding="utf-8")
        assert "https://new.example.com" in written
        assert "discovered: true" in written
        assert "role: docs" in written
        assert "scrape: deep" in written

    def test_idempotent_through_real_dedup(self, tmp_path, monkeypatch):
        """Second persist with same discovered URL doesn't change SPEC.md."""
        monkeypatch.chdir(tmp_path)
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("## Sources\n", encoding="utf-8")
        result = ScrapeResult(discovered_urls=["https://dedup.example.com"])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
        ):
            _persist_scrape_result(result)
            after_first = spec_path.read_text(encoding="utf-8")
            # Second call with same URL — dedup in append_sources.
            _persist_scrape_result(result)
            after_second = spec_path.read_text(encoding="utf-8")
        assert after_first == after_second
        assert after_first.count("https://dedup.example.com") == 1

    def test_idempotent_existing_url_in_sources(self, tmp_path, monkeypatch):
        """URL already in ## Sources is not added again (no mock)."""
        monkeypatch.chdir(tmp_path)
        spec_path = tmp_path / "SPEC.md"
        original = "## Sources\n\n- https://already.example.com\n  role: docs\n  scrape: deep\n"
        spec_path.write_text(original, encoding="utf-8")
        result = ScrapeResult(discovered_urls=["https://already.example.com"])
        with (
            patch("duplo.main.save_examples"),
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.save_doc_structures"),
        ):
            _persist_scrape_result(result)
        # Content unchanged — dedup prevented addition.
        assert spec_path.read_text(encoding="utf-8") == original


class TestRunVideoFramePipelinePerSourceLookup:
    """_run_video_frame_pipeline returns per-source accepted-frame lookup."""

    def test_returns_per_source_lookup(self, tmp_path, monkeypatch):
        """The second return value maps each source to its accepted frames."""
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        vid_a = tmp_path / "a.mp4"
        vid_a.write_bytes(b"MP4")
        vid_b = tmp_path / "b.mp4"
        vid_b.write_bytes(b"MP4")

        frame_a1 = frames_dir / "a_scene_0001.png"
        frame_a1.write_bytes(b"PNG1")
        frame_a2 = frames_dir / "a_scene_0002.png"
        frame_a2.write_bytes(b"PNG2")
        frame_b1 = frames_dir / "b_scene_0001.png"
        frame_b1.write_bytes(b"PNG3")

        with (
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=vid_a,
                        frames=[frame_a1, frame_a2],
                    ),
                    ExtractionResult(source=vid_b, frames=[frame_b1]),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[frame_a1, frame_b1],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
        ):
            from duplo.main import _run_video_frame_pipeline

            frames, lookup = _run_video_frame_pipeline([vid_a, vid_b])

        assert set(frames) == {frame_a1, frame_b1}
        # Per-source lookup: vid_a kept frame_a1 (not frame_a2),
        # vid_b kept frame_b1.
        assert lookup[vid_a] == [frame_a1]
        assert lookup[vid_b] == [frame_b1]

    def test_empty_videos_returns_empty_lookup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo" / "video_frames").mkdir(parents=True, exist_ok=True)

        with patch("duplo.main.extract_all_videos", return_value=[]):
            from duplo.main import _run_video_frame_pipeline

            frames, lookup = _run_video_frame_pipeline([])

        assert frames == []
        assert lookup == {}

    def test_all_frames_rejected_source_has_empty_list(self, tmp_path, monkeypatch):
        """Source whose frames are all rejected maps to empty list."""
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        vid = tmp_path / "a.mp4"
        vid.write_bytes(b"MP4")
        frame1 = frames_dir / "a_scene_0001.png"
        frame1.write_bytes(b"PNG1")

        with (
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(source=vid, frames=[frame1]),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[],  # all rejected
            ),
        ):
            from duplo.main import _run_video_frame_pipeline

            frames, lookup = _run_video_frame_pipeline([vid])

        assert frames == []
        # Source present in lookup with empty frame list.
        assert vid in lookup
        assert lookup[vid] == []


class TestDesignInputPerSourceLookup:
    """_first_run uses accepted_frames_by_path lookup for design input composition."""

    def test_visual_target_frames_via_lookup(self, tmp_path, monkeypatch):
        """Frames from visual-target videos are selected via per-source lookup."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # A dual-role video: both behavioral-target and visual-target
        dual_vid = ref_dir / "demo.mp4"
        dual_vid.write_bytes(b"MP4" * 100)

        # A behavioral-only video
        beh_vid = ref_dir / "tutorial.mp4"
        beh_vid.write_bytes(b"MP4" * 100)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        dual_frame = frames_dir / "demo_scene_0001.png"
        dual_frame.write_bytes(b"DUAL")
        beh_frame = frames_dir / "tutorial_scene_0001.png"
        beh_frame.write_bytes(b"BEH")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target", "visual-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/tutorial.mp4"),
                    roles=["behavioral-target"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=Path("ref/demo.mp4"),
                        frames=[dual_frame],
                    ),
                    ExtractionResult(
                        source=Path("ref/tutorial.mp4"),
                        frames=[beh_frame],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[dual_frame, beh_frame],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[dual_vid, beh_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            mock_design.return_value = DesignRequirements()
            main()

        # collect_design_input should receive only dual-role video's
        # frames as visual_target_frames (arg index 1), not the
        # behavioral-only video's frames.
        mock_cdi.assert_called_once()
        vt_frames_arg = mock_cdi.call_args[0][1]
        vt_frame_names = [f.name for f in vt_frames_arg]
        assert "demo_scene_0001.png" in vt_frame_names
        assert "tutorial_scene_0001.png" not in vt_frame_names

    def test_site_images_passed_as_source_4(self, tmp_path, monkeypatch):
        """Site images from _download_site_media passed as source 4."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        site_img = tmp_path / ".duplo" / "site_media" / "hero.png"
        site_img.parent.mkdir(parents=True, exist_ok=True)
        site_img.write_bytes(b"SITEIMG")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/placeholder.txt"),
                    roles=["docs"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([site_img], []),
            ),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            mock_design.return_value = DesignRequirements()
            main()

        mock_cdi.assert_called_once()
        si_arg = mock_cdi.call_args[0][2]
        assert site_img in si_arg

    def test_all_four_sources_combined(self, tmp_path, monkeypatch):
        """All four design input sources flow through collect_design_input."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # Source 1: visual-target reference image in ref/
        vt_img = ref_dir / "screenshot.png"
        vt_img.write_bytes(b"VT_IMG")

        # Source 2: video with visual-target role
        vt_vid = ref_dir / "demo.mp4"
        vt_vid.write_bytes(b"MP4" * 100)

        # Source 3: scraped site video
        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        # Source 4: site image
        site_img = tmp_path / ".duplo" / "site_media" / "hero.png"
        site_img.write_bytes(b"SITEIMG")

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        vt_frame = frames_dir / "demo_scene_0001.png"
        vt_frame.write_bytes(b"VTFRAME")
        site_frame = frames_dir / "promo_scene_0001.png"
        site_frame.write_bytes(b"SITEFRAME")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/screenshot.png"),
                    roles=["visual-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target", "visual-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([site_img], [site_vid]),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=Path("ref/demo.mp4"),
                        frames=[vt_frame],
                    ),
                    ExtractionResult(
                        source=site_vid,
                        frames=[site_frame],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[vt_frame, site_frame],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch(
                "duplo.main.extract_design",
                side_effect=_capture_design,
            ),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vt_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_cdi.assert_called_once()
        args = mock_cdi.call_args[0]
        # arg 0: spec, arg 1: vt_frames, arg 2: site_images, arg 3: svf
        vt_frames_arg = args[1]
        site_images_arg = args[2]
        site_video_frames_arg = args[3]

        # Source 1 is handled inside collect_design_input (visual refs)
        # Source 2: visual-target video frame
        assert vt_frame in vt_frames_arg
        # Source 3: site video frames
        assert site_frame in site_video_frames_arg
        # Source 4: site images
        assert site_img in site_images_arg

        # All four sources appear in the final design input
        captured_names = [p.name for p in design_input_captured]
        assert "screenshot.png" in captured_names  # source 1
        assert "demo_scene_0001.png" in captured_names  # source 2
        assert "promo_scene_0001.png" in captured_names  # source 3
        assert "hero.png" in captured_names  # source 4

    def test_frame_content_hash_dedup_ref_wins(self, tmp_path, monkeypatch):
        """Ref-declared frame with same content as scraped frame wins; scraped dropped."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # Visual-target video in ref/
        vt_vid = ref_dir / "demo.mp4"
        vt_vid.write_bytes(b"MP4" * 100)

        # Scraped video (same demo appearing on the product page)
        site_vid = tmp_path / ".duplo" / "site_media" / "demo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Both videos produce a frame with IDENTICAL content
        identical_content = b"IDENTICAL_FRAME_CONTENT"
        vt_frame = frames_dir / "demo_scene_0001.png"
        vt_frame.write_bytes(identical_content)
        scraped_frame = frames_dir / "demo_site_scene_0001.png"
        scraped_frame.write_bytes(identical_content)

        # A unique scraped frame (different content) should survive
        unique_frame = frames_dir / "demo_site_scene_0002.png"
        unique_frame.write_bytes(b"UNIQUE_SCRAPED_FRAME")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo.mp4"),
                    roles=["behavioral-target", "visual-target"],
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=Path("ref/demo.mp4"),
                        frames=[vt_frame],
                    ),
                    ExtractionResult(
                        source=site_vid,
                        frames=[scraped_frame, unique_frame],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[vt_frame, scraped_frame, unique_frame],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vt_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            mock_design.return_value = DesignRequirements()
            main()

        mock_cdi.assert_called_once()
        args = mock_cdi.call_args[0]
        vt_frames_arg = args[1]  # visual-target frames
        svf_arg = args[3]  # site video frames

        # Ref-declared frame kept
        assert vt_frame in vt_frames_arg
        # Duplicate scraped frame dropped (same content as ref frame)
        assert scraped_frame not in svf_arg
        # Unique scraped frame kept
        assert unique_frame in svf_arg

    def test_frame_content_hash_dedup_no_spec(self, tmp_path, monkeypatch):
        """No-spec branch deduplicates video_frames against scraped frames."""
        monkeypatch.chdir(tmp_path)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        identical_content = b"SAME_FRAME"
        ref_frame = frames_dir / "ref_scene_0001.png"
        ref_frame.write_bytes(identical_content)
        scraped_frame = frames_dir / "scraped_scene_0001.png"
        scraped_frame.write_bytes(identical_content)
        unique_scraped = frames_dir / "scraped_scene_0002.png"
        unique_scraped.write_bytes(b"UNIQUE")

        ref_vid = tmp_path / "ref" / "demo.mp4"
        ref_vid.parent.mkdir(parents=True, exist_ok=True)
        ref_vid.write_bytes(b"MP4" * 100)

        site_vid = tmp_path / ".duplo" / "site_media" / "promo.mp4"
        site_vid.parent.mkdir(parents=True, exist_ok=True)
        site_vid.write_bytes(b"MP4" * 100)

        from duplo.video_extractor import ExtractionResult

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch("duplo.main.load_product", return_value=None),
            patch("duplo.main.validate_product_url") as mock_vprod,
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=ref_vid,
                        frames=[ref_frame],
                    ),
                    ExtractionResult(
                        source=site_vid,
                        frames=[scraped_frame, unique_scraped],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[ref_frame, scraped_frame, unique_scraped],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch("duplo.main.extract_design") as mock_design,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_vprod.return_value = type(
                "VR",
                (),
                {
                    "single_product": True,
                    "product_name": "App",
                    "products": [],
                    "reason": "",
                    "unclear_boundaries": False,
                },
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[ref_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            mock_design.return_value = DesignRequirements()
            main()

        # extract_design receives the deduped list
        mock_design.assert_called_once()
        design_input = mock_design.call_args[0][0]
        design_names = [p.name for p in design_input]

        # Ref frame kept
        assert "ref_scene_0001.png" in design_names
        # Duplicate scraped frame dropped
        assert "scraped_scene_0001.png" not in design_names
        # Unique scraped frame kept
        assert "scraped_scene_0002.png" in design_names

    def test_missing_source_gracefully_omitted(self, tmp_path, monkeypatch):
        """Design input works when some sources are absent (no videos, no site media)."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # Only source 1: visual-target reference image
        vt_img = ref_dir / "screenshot.png"
        vt_img.write_bytes(b"VT_IMG")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/screenshot.png"),
                    roles=["visual-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {"u": "<h>"}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),  # no site media at all
            ),
            # No videos scanned — pipeline skipped
            patch(
                "duplo.main.extract_design",
                side_effect=_capture_design,
            ),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[vt_img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_cdi.assert_called_once()
        # Sources 2, 3, 4 absent — only source 1 contributes
        captured_names = [p.name for p in design_input_captured]
        assert "screenshot.png" in captured_names
        assert len(design_input_captured) == 1

    def test_frame_content_hash_dedup_two_videos_identical_frames(self, tmp_path, monkeypatch):
        """Two separate videos at different paths producing identical frames dedup."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # Two visual-target videos in ref/
        vid_a = ref_dir / "demo_a.mp4"
        vid_a.write_bytes(b"MP4A" * 100)
        vid_b = ref_dir / "demo_b.mp4"
        vid_b.write_bytes(b"MP4B" * 100)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Both videos produce frames with identical content at different paths
        identical_content = b"SAME_FRAME_BYTES_HERE"
        frame_a = frames_dir / "demo_a_scene_0001.png"
        frame_a.write_bytes(identical_content)
        frame_b = frames_dir / "demo_b_scene_0001.png"
        frame_b.write_bytes(identical_content)

        # vid_a also has a unique frame
        unique_a = frames_dir / "demo_a_scene_0002.png"
        unique_a.write_bytes(b"UNIQUE_A")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/demo_a.mp4"),
                    roles=["behavioral-target", "visual-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/demo_b.mp4"),
                    roles=["behavioral-target", "visual-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=Path("ref/demo_a.mp4"),
                        frames=[frame_a, unique_a],
                    ),
                    ExtractionResult(
                        source=Path("ref/demo_b.mp4"),
                        frames=[frame_b],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[frame_a, unique_a, frame_b],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch(
                "duplo.main.extract_design",
                side_effect=_capture_design,
            ),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vid_a, vid_b],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_cdi.assert_called_once()
        captured_names = [p.name for p in design_input_captured]
        # frame_a kept (first video processed first)
        assert "demo_a_scene_0001.png" in captured_names
        # frame_b dropped (identical content hash as frame_a)
        assert "demo_b_scene_0001.png" not in captured_names
        # unique_a kept (different content)
        assert "demo_a_scene_0002.png" in captured_names

    def test_behavioral_only_video_excluded_from_design(self, tmp_path, monkeypatch):
        """Frames from behavioral-only video do NOT appear in design input."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry
        from duplo.video_extractor import ExtractionResult

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        beh_vid = ref_dir / "tutorial.mp4"
        beh_vid.write_bytes(b"MP4" * 100)

        frames_dir = tmp_path / ".duplo" / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        beh_frame = frames_dir / "tutorial_scene_0001.png"
        beh_frame.write_bytes(b"BEHFRAME")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/tutorial.mp4"),
                    roles=["behavioral-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=Path("ref/tutorial.mp4"),
                        frames=[beh_frame],
                    ),
                ],
            ),
            patch("duplo.main.filter_frames", return_value=[]),
            patch(
                "duplo.main.apply_filter",
                return_value=[beh_frame],
            ),
            patch("duplo.main.describe_frames", return_value=[]),
            patch("duplo.main.store_accepted_frames"),
            patch(
                "duplo.main.extract_design",
                side_effect=_capture_design,
            ),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_cdi.assert_called_once()
        vt_frames_arg = mock_cdi.call_args[0][1]
        # Behavioral-only video's frames NOT passed as visual-target frames
        assert beh_frame not in vt_frames_arg
        assert len(vt_frames_arg) == 0
        # No design input at all (only behavioral video, no visual sources)
        assert len(design_input_captured) == 0

    def test_proposed_visual_ref_excluded_from_design(self, tmp_path, monkeypatch):
        """proposed: true visual-target ref does NOT appear in design input."""
        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()

        # A proposed visual-target (should be excluded)
        proposed_img = ref_dir / "proposed.png"
        proposed_img.write_bytes(b"PROPOSED_IMG")

        # A confirmed visual-target (should be included)
        confirmed_img = ref_dir / "confirmed.png"
        confirmed_img.write_bytes(b"CONFIRMED_IMG")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.png"),
                    roles=["visual-target"],
                    proposed=True,
                ),
                ReferenceEntry(
                    path=Path("ref/confirmed.png"),
                    roles=["visual-target"],
                ),
            ],
        )

        design_input_captured = []

        def _capture_design(images):
            design_input_captured.extend(images)
            return DesignRequirements()

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.extract_design",
                side_effect=_capture_design,
            ),
            patch(
                "duplo.main.collect_design_input",
                wraps=collect_design_input,
            ) as mock_cdi,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[confirmed_img, proposed_img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_cdi.assert_called_once()
        captured_names = [p.name for p in design_input_captured]
        # Confirmed visual-target included
        assert "confirmed.png" in captured_names
        # Proposed visual-target excluded
        assert "proposed.png" not in captured_names


class TestAutogenBlockSkipsVision:
    """Check autogen block FIRST via the in-memory dataclass."""

    def test_first_run_skips_vision_when_autogen_present(self, tmp_path, monkeypatch):
        """_first_run skips extract_design when spec.design.auto_generated
        has content."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  primary: #fff"),
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        # Vision should NOT be called since autogen block already exists
        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()

    def test_first_run_writes_autogen_block(self, tmp_path, monkeypatch):
        """_first_run writes autogen block to SPEC.md when absent."""
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(
            "## Purpose\nTest\n\n## Design\nUser prose here.\n",
            encoding="utf-8",
        )

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(
                user_prose="User prose here.",
                auto_generated="",
            ),
            references=[
                ReferenceEntry(
                    path=Path("ref/screen.png"),
                    roles=["visual-target"],
                ),
            ],
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        design_result = DesignRequirements(
            colors={"primary": "#ff0000"},
            fonts=[],
            spacing={},
            layout="",
            components=[],
            source_images=["screen.png"],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main.extract_design",
                return_value=design_result,
            ) as mock_design,
            patch("duplo.main.save_design_requirements"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_design.assert_called_once()
        # SPEC.md should now contain AUTO-GENERATED markers
        updated = spec_path.read_text(encoding="utf-8")
        assert "AUTO-GENERATED" in updated

    def test_analyze_new_files_skips_vision_when_autogen_present(self, tmp_path, monkeypatch):
        """_analyze_new_files skips extract_design when autogen exists."""
        from duplo.spec_reader import DesignBlock, ProductSpec

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "new_shot.png"
        img.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  bg: #000"),
        )

        with (
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
        ):
            _analyze_new_files(
                ["ref/new_shot.png"],
                spec=spec,
            )

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()

    def test_rescrape_skips_vision_when_autogen_present(self, tmp_path, monkeypatch):
        """_rescrape_product_url skips extract_design when autogen exists."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        monkeypatch.chdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        data = {
            "source_url": "https://a.com",
            "features": [],
            "last_scrape_timestamp": 0,
        }
        (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")

        site_media_dir = duplo_dir / "site_media"
        site_media_dir.mkdir()

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="fonts:\n  body: Inter"),
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )

        raw_html = "<html><body><img src='https://a.com/img.png'></body></html>"
        with (
            patch(
                "duplo.main.fetch_site",
                return_value=(
                    "text",
                    [],
                    None,
                    [PageRecord("https://a.com", "2024-01-01", "abc")],
                    {"https://a.com": raw_html},
                ),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([tmp_path / "img.png"], []),
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[
                    tmp_path / "img.png",
                ],
            ),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
        ):
            _rescrape_product_url(spec=spec)

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()

    def test_no_spec_does_not_skip_vision(self, tmp_path, monkeypatch):
        """When spec is None, autogen check is False and Vision proceeds."""

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main.extract_design",
                return_value=DesignRequirements(),
            ) as mock_design,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        # Without spec, Vision should proceed
        mock_design.assert_called_once()

    def test_empty_autogen_does_not_skip_vision(self, tmp_path, monkeypatch):
        """An empty autogen block (whitespace only) does not skip Vision."""
        from duplo.spec_reader import DesignBlock, ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "new_shot.png"
        img.write_bytes(b"PNG")
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("## Design\n", encoding="utf-8")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="   \n  "),
            references=[
                ReferenceEntry(
                    path=Path("ref/new_shot.png"),
                    roles=["visual-target"],
                ),
            ],
        )

        with (
            patch(
                "duplo.main.extract_design",
                return_value=DesignRequirements(),
            ) as mock_design,
            patch("duplo.main.save_design_requirements"),
        ):
            _analyze_new_files(["ref/new_shot.png"], spec=spec)

        # Empty/whitespace autogen should NOT block Vision
        mock_design.assert_called_once()

    def test_first_run_emits_diagnostic_when_autogen_present(self, tmp_path, monkeypatch):
        """_first_run emits record_failure when skipping Vision due to
        existing autogen block."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  primary: #fff"),
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.main.collect_design_input", return_value=[img]),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
            patch("duplo.main.record_failure") as mock_rf,
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()
        mock_rf.assert_called_once()
        args = mock_rf.call_args
        assert args[0][0] == "orchestrator:design_extraction"
        assert args[0][1] == "io"
        assert "Autogen design block exists" in args[0][2]
        assert "1 input image(s)" in args[0][2]

    def test_analyze_new_files_emits_diagnostic_when_autogen_present(self, tmp_path, monkeypatch):
        """_analyze_new_files emits record_failure when skipping Vision."""
        from duplo.spec_reader import DesignBlock, ProductSpec

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "new_shot.png"
        img.write_bytes(b"PNG")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  bg: #000"),
        )

        with (
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.main.collect_design_input", return_value=[img]),
            patch("duplo.main.record_failure") as mock_rf,
        ):
            _analyze_new_files(
                ["ref/new_shot.png"],
                spec=spec,
            )

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()
        mock_rf.assert_called_once()
        args = mock_rf.call_args
        assert args[0][0] == "orchestrator:design_extraction"
        assert args[0][1] == "io"
        assert "1 input image(s)" in args[0][2]

    def test_rescrape_emits_diagnostic_when_autogen_present(self, tmp_path, monkeypatch):
        """_rescrape_product_url emits record_failure when skipping Vision."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        monkeypatch.chdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        data = {
            "source_url": "https://a.com",
            "features": [],
            "last_scrape_timestamp": 0,
        }
        (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")

        site_media_dir = duplo_dir / "site_media"
        site_media_dir.mkdir()

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="fonts:\n  body: Inter"),
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )

        raw_html = "<html><body><img src='https://a.com/img.png'></body></html>"
        with (
            patch(
                "duplo.main.fetch_site",
                return_value=(
                    "text",
                    [],
                    None,
                    [PageRecord("https://a.com", "2024-01-01", "abc")],
                    {"https://a.com": raw_html},
                ),
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([tmp_path / "img.png"], []),
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[
                    tmp_path / "img.png",
                ],
            ),
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.main.save_reference_urls"),
            patch("duplo.main.save_raw_content"),
            patch("duplo.main.record_failure") as mock_rf,
        ):
            _rescrape_product_url(spec=spec)

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()
        # May have multiple record_failure calls; find the design one.
        design_calls = [
            c for c in mock_rf.call_args_list if c[0][0] == "orchestrator:design_extraction"
        ]
        assert len(design_calls) == 1
        assert "1 input image(s)" in design_calls[0][0][2]

    def test_first_run_spec_write_idempotent(self, tmp_path, monkeypatch):
        """SPEC.md write only happens when content actually changes."""
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")
        # Pre-populate SPEC.md with the exact content that
        # update_design_autogen would produce (already has autogen
        # markers with matching body).
        spec_content = (
            "## Purpose\nTest\n\n## Design\nUser prose.\n\n"
            "<!-- BEGIN AUTO-GENERATED -->\ncolors:\n"
            "  primary: #ff0000\n<!-- END AUTO-GENERATED -->\n"
        )
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(spec_content, encoding="utf-8")
        original_mtime = spec_path.stat().st_mtime

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(
                user_prose="User prose.",
                auto_generated="",
            ),
            references=[
                ReferenceEntry(
                    path=Path("ref/screen.png"),
                    roles=["visual-target"],
                ),
            ],
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        design_result = DesignRequirements(
            colors={"primary": "#ff0000"},
            fonts=[],
            spacing={},
            layout={},
            components=[],
            source_images=["screen.png"],
        )

        # Mock update_design_autogen to return UNCHANGED text (simulates
        # the case where the existing autogen block already matches).
        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main.extract_design",
                return_value=design_result,
            ),
            patch("duplo.main.save_design_requirements"),
            patch(
                "duplo.main.update_design_autogen",
                return_value=spec_content,
            ) as mock_autogen,
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        mock_autogen.assert_called_once()
        # Since update_design_autogen returned unchanged text, SPEC.md
        # should NOT have been rewritten — mtime stays the same.
        assert spec_path.stat().st_mtime == original_mtime

    def test_in_memory_spec_consulted_not_disk(self, tmp_path, monkeypatch):
        """The autogen check uses spec.design.auto_generated (in-memory),
        NOT a re-read of SPEC.md from disk. Prove by having SPEC.md on
        disk contain an autogen block while the in-memory spec has an
        empty auto_generated field — Vision should proceed."""
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "screen.png"
        img.write_bytes(b"PNG")
        spec_path = tmp_path / "SPEC.md"
        # SPEC.md on disk has an autogen block
        spec_path.write_text(
            "## Design\n\n<!-- BEGIN AUTO-GENERATED -->\n"
            "colors:\n  primary: #000\n"
            "<!-- END AUTO-GENERATED -->\n",
            encoding="utf-8",
        )

        # But the in-memory spec has EMPTY auto_generated
        spec = ProductSpec(
            raw="test",
            design=DesignBlock(
                user_prose="",
                auto_generated="",
            ),
            references=[
                ReferenceEntry(
                    path=Path("ref/screen.png"),
                    roles=["visual-target"],
                ),
            ],
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        design_result = DesignRequirements(
            colors={"primary": "#ff0000"},
            fonts=[],
            spacing={},
            layout={},
            components=[],
            source_images=["screen.png"],
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch("duplo.main.validate_for_run") as mock_val,
            patch("duplo.main.scan_directory") as mock_scan,
            patch(
                "duplo.main.load_product",
                return_value=("App", "https://a.com"),
            ),
            patch(
                "duplo.main.fetch_site",
                return_value=("t", [], None, [], {}),
            ),
            patch(
                "duplo.main.extract_design",
                return_value=design_result,
            ) as mock_design,
            patch("duplo.main.save_design_requirements"),
            patch("duplo.main.extract_features", return_value=[]),
            patch(
                "duplo.main.ask_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="Python",
                ),
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.save_selections",
                return_value=tmp_path / _DUPLO_JSON,
            ),
            patch("duplo.main.write_claude_md"),
            patch("duplo.main.generate_roadmap", return_value=None),
        ):
            from duplo.scanner import ScanResult

            mock_val.return_value = type(
                "V",
                (),
                {"warnings": [], "errors": []},
            )()
            mock_scan.return_value = ScanResult(
                images=[img],
                videos=[],
                pdfs=[],
                text_files=[],
                urls=["https://a.com"],
            )
            main()

        # In-memory spec had empty auto_generated, so Vision should
        # proceed even though SPEC.md on disk has autogen content.
        mock_design.assert_called_once()

    def test_in_memory_autogen_present_overrides_disk(self, tmp_path, monkeypatch):
        """When in-memory spec.design.auto_generated has content but
        SPEC.md on disk does NOT have an autogen block, Vision is
        still skipped — proving the in-memory dataclass is the
        source of truth."""
        from duplo.spec_reader import DesignBlock, ProductSpec

        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        img = ref_dir / "new_shot.png"
        img.write_bytes(b"PNG")
        spec_path = tmp_path / "SPEC.md"
        # SPEC.md on disk has NO autogen block
        spec_path.write_text("## Design\nJust prose.\n", encoding="utf-8")

        spec = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  bg: #123"),
        )

        with (
            patch("duplo.main.extract_design") as mock_design,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
        ):
            _analyze_new_files(["ref/new_shot.png"], spec=spec)

        # In-memory spec says autogen exists, so Vision is skipped
        # even though disk SPEC.md has no autogen block.
        mock_design.assert_not_called()
        mock_save_dr.assert_not_called()


class TestSubsequentRunSpecSourcesIntegration:
    """Integration: _subsequent_run with spec sources downloads
    site media and processes behavioral videos / design input."""

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: duplo.json, file hashes, SPEC.md."""
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

    def test_downloads_site_media_from_spec_sources(self, tmp_path, monkeypatch):
        """When spec declares sources, site media is downloaded
        from product_ref_raw_pages."""
        self._setup(tmp_path, monkeypatch)
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            SourceEntry,
        )

        src = SourceEntry(
            url="https://prod.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="test",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="text",
            product_ref_raw_pages={"https://prod.com": "<html></html>"},
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.main._persist_scrape_result",
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=(
                    [Path("img.png")],
                    [Path("vid.mp4")],
                ),
            ) as mock_dl,
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.main.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        mock_dl.assert_called_once_with({"https://prod.com": "<html></html>"})

    def test_processes_behavioral_videos_with_spec_sources(self, tmp_path, monkeypatch):
        """Behavioral video pipeline runs when spec sources
        provide site_videos."""
        self._setup(tmp_path, monkeypatch)
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            SourceEntry,
        )

        src = SourceEntry(
            url="https://prod.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="test",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="text",
            product_ref_raw_pages={"https://prod.com": "<html></html>"},
        )
        site_vid = Path(".duplo/site_media/abc/demo.mp4")

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.main._persist_scrape_result",
            ),
            patch(
                "duplo.main._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main._run_video_frame_pipeline",
                return_value=([], {}),
            ) as mock_pipeline,
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.main.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        mock_pipeline.assert_called_once_with([site_vid])

    def test_skips_design_when_autogen_present(self, tmp_path, monkeypatch):
        """Design extraction is skipped when autogen block
        already has content."""
        self._setup(tmp_path, monkeypatch)
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            SourceEntry,
        )

        src = SourceEntry(
            url="https://prod.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="test",
            sources=[src],
            design=DesignBlock(auto_generated="colors:\n  bg: #fff"),
        )
        scrape_result = ScrapeResult(
            combined_text="text",
            product_ref_raw_pages={},
        )

        with (
            patch("duplo.main.read_spec", return_value=spec),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.main._persist_scrape_result",
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[Path("img.png")],
            ),
            patch(
                "duplo.main.extract_design",
            ) as mock_extract,
            patch(
                "duplo.main.save_design_requirements",
            ) as mock_save_dr,
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.main.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        mock_extract.assert_not_called()
        mock_save_dr.assert_not_called()

    def test_no_media_pipeline_without_spec_sources(self, tmp_path, monkeypatch):
        """Without spec sources, _rescrape_product_url is used
        instead and the spec-sources media pipeline does not
        run."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch("duplo.main.read_spec", return_value=None),
            patch(
                "duplo.main.scrapeable_sources",
                return_value=[],
            ),
            patch(
                "duplo.main._rescrape_product_url",
                return_value=(0, 0, ""),
            ) as mock_rescrape,
            patch(
                "duplo.main._download_site_media",
            ) as mock_dl,
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.main.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        mock_rescrape.assert_called_once()
        # _download_site_media is NOT called directly in
        # _subsequent_run for the non-spec path (it's called
        # inside _rescrape_product_url).
        mock_dl.assert_not_called()
