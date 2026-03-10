"""Tests for duplo.main CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.design_extractor import DesignRequirements
from duplo.extractor import Feature
from duplo.main import (
    UpdateSummary,
    _analyze_new_files,
    _build_completion_history,
    _complete_phase,
    _detect_and_append_gaps,
    _init_project,
    _partition_features,
    _print_feature_status,
    _print_status,
    _print_summary,
    _rescrape_product_url,
    _unimplemented_features,
    main,
)
from duplo.questioner import BuildPreferences

_DUPLO_JSON = ".duplo/duplo.json"


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    """Prevent argparse from seeing pytest's CLI arguments."""
    monkeypatch.setattr("sys.argv", ["duplo"])


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
        (tmp_path / "links.txt").write_text("https://example.com")
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
        (tmp_path / "urls.txt").write_text("https://first.com\nhttps://second.com")
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
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
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
        (tmp_path / "notes.txt").write_text("https://example.com")
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
        (tmp_path / "links.txt").write_text("https://example.com")
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
        (tmp_path / "links.txt").write_text("https://example.com")
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

    def test_appends_test_tasks_to_generated_plan(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "code_examples": [
                {
                    "input": "1+1",
                    "expected_output": "2",
                    "source_url": "",
                    "language": "python",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch(
                "duplo.main.generate_phase_plan",
                return_value="# Phase 0\n- [ ] Task",
            ),
            patch(
                "duplo.main.save_plan",
                return_value=tmp_path / "PLAN.md",
            ) as mock_save,
        ):
            main()

        saved_content = mock_save.call_args.args[0]
        assert "Wire up" in saved_content
        assert "run_example()" in saved_content


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
        # Add a new file that will be detected.
        (tmp_path / "new_ref.png").write_bytes(b"PNG image data")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        out = capsys.readouterr().out
        assert "File changes detected" in out
        assert "+ new_ref.png" in out

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
        (tmp_path / "new.txt").write_text("hello")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                main()

        # Verify hashes reflect post-move state (new.txt moved to .duplo/references/).
        from duplo.hasher import load_hashes

        saved = load_hashes(tmp_path)
        assert "new.txt" not in saved


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
                            "duplo.main.generate_test_source", return_value="test code"
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
        # Add a new image.
        (tmp_path / "new.png").write_bytes(b"PNG" * 500)
        monkeypatch.chdir(tmp_path)

        design = DesignRequirements(colors={"primary": "#abc"}, source_images=["new.png"])
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

        records = [{"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"}]
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
        assert "Update design: primary: #ff0000" in updated
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
        assert "Update design: accent: #00ff00" in updated

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
        records = [{"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"}]
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
        records = [{"url": "https://example.com", "fetched_at": "t", "content_hash": "abc"}]
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

        mock_extract.assert_called_once_with("product text")
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
    """_rescrape_product_url downloads media from re-scraped pages."""

    def test_downloads_media_from_raw_pages(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        raw_pages = {"https://example.com": "<html><img src='pic.png'/></html>"}
        with patch(
            "duplo.main.fetch_site",
            return_value=("text", [], None, [], raw_pages),
        ):
            with patch(
                "duplo.main._download_site_media",
                return_value=([Path("a.png")], []),
            ) as mock_dl:
                _rescrape_product_url()

        mock_dl.assert_called_once_with(raw_pages)
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
