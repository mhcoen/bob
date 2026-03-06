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
    _detect_and_append_gaps,
    _init_project,
    _print_summary,
    _rescrape_product_url,
    main,
)
from duplo.questioner import BuildPreferences

_DUPLO_JSON = ".duplo/duplo.json"


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
                                                                "duplo.main.run_mcloop",
                                                                return_value=0,
                                                            ):
                                                                with patch(
                                                                    "duplo.main.capture_appshot",
                                                                    return_value=0,
                                                                ):
                                                                    with patch(
                                                                        "duplo.main.notify_phase_complete"
                                                                    ):
                                                                        main()


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
    }

    def test_generates_and_runs_phase(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md") as mock_save:
                with patch("duplo.main.run_mcloop", return_value=0):
                    main()

        mock_save.assert_called_once()

    def test_captures_appshot_when_app_name_set(self, tmp_path, monkeypatch):
        data = {**self._BASE_DATA, "app_name": "MyApp"}
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.run_mcloop", return_value=0):
                    with patch("duplo.main.capture_appshot", return_value=0) as mock_shot:
                        main()

        mock_shot.assert_called_once()
        assert mock_shot.call_args.args[0] == "MyApp"

    def test_skips_appshot_when_no_app_name(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.run_mcloop", return_value=0):
                    with patch("duplo.main.capture_appshot") as mock_shot:
                        main()

        mock_shot.assert_not_called()

    def test_uses_run_sh_as_launch_when_present(self, tmp_path, monkeypatch):
        data = {**self._BASE_DATA, "app_name": "MyApp"}
        _write_duplo_json(tmp_path, data)
        (tmp_path / "run.sh").write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.run_mcloop", return_value=0):
                    with patch("duplo.main.capture_appshot", return_value=0) as mock_shot:
                        main()

        _, kwargs = mock_shot.call_args
        assert kwargs.get("launch") == "./run.sh"

    def test_appends_phase_history(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0: Core\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.run_mcloop", return_value=0):
                    main()

        data = _read_duplo_json(tmp_path)
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "Phase 0: Core"

    def test_exits_on_mcloop_failure(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.main.run_mcloop", return_value=1):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
        assert exc_info.value.code == 1


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
    }

    def test_skips_plan_generation_when_plan_exists(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.generate_phase_plan") as mock_gen:
            with patch("duplo.main.run_mcloop", return_value=0):
                with patch("duplo.main.notify_phase_complete"):
                    main()

        mock_gen.assert_not_called()

    def test_skips_mcloop_when_mcloop_done(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 0", "mcloop_done": True},
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.run_mcloop") as mock_run:
            with patch("duplo.main.notify_phase_complete"):
                main()

        mock_run.assert_not_called()

    def test_in_progress_cleared_after_success(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 0", "mcloop_done": False},
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.run_mcloop", return_value=0):
            with patch("duplo.main.notify_phase_complete"):
                main()

        result = _read_duplo_json(tmp_path)
        assert "in_progress" not in result

    def test_resumes_print_message(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.run_mcloop", return_value=0):
            with patch("duplo.main.notify_phase_complete"):
                main()

        out = capsys.readouterr().out
        assert "Resuming" in out


class TestAdvanceToNext:
    """Tests for advancing to the next phase (feedback collection)."""

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

    def test_collects_feedback_and_generates_next(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "phases": [
                {
                    "phase": "Phase 0: Core",
                    "plan": "# Phase 0: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main.get_current_phase",
            return_value=(0, {"title": "Core"}),
        ):
            with patch("duplo.main.collect_feedback", return_value="some feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan",
                    return_value="# Phase 1: Features\n",
                ) as mock_gen:
                    with patch(
                        "duplo.main.save_plan",
                        return_value=tmp_path / "PLAN.md",
                    ):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()

        out = capsys.readouterr().out
        assert "next phase" in out.lower()
        mock_gen.assert_called_once()

    def test_appends_phase_history(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "phases": [
                {
                    "phase": "Phase 0: Core",
                    "plan": "# Phase 0: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main.get_current_phase",
            return_value=(0, {"title": "Core"}),
        ):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan",
                    return_value="# Phase 1: Next\n",
                ):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()

        result = _read_duplo_json(tmp_path)
        assert len(result["phases"]) == 2
        assert result["phases"][1]["phase"] == "Phase 1: Next"

    def test_loads_issues_when_present(self, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "phases": [
                {
                    "phase": "Phase 0: Core",
                    "plan": "# Phase 0: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        (tmp_path / "ISSUES.md").write_text("# Visual Issues\n- Layout broken\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main.get_current_phase",
            return_value=(0, {"title": "Core"}),
        ):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan",
                    return_value="# Phase 1\n",
                ) as mock_gen:
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()

        args = mock_gen.call_args.args
        issues_text = (
            args[2] if len(args) > 2 else mock_gen.call_args.kwargs.get("issues_text", "")
        )
        assert "Layout broken" in issues_text

    def test_all_phases_complete_no_plan(self, capsys, tmp_path, monkeypatch):
        data = {
            **self._BASE_DATA,
            "phases": [
                {
                    "phase": "Phase 0: Core",
                    "plan": "# Phase 0: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main.get_current_phase",
            return_value=(0, {"title": "Core"}),
        ):
            with patch("duplo.main.run_mcloop") as mock_run:
                main()

        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "complete" in out.lower()

    def test_appends_test_tasks(self, tmp_path, monkeypatch):
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
            "phases": [
                {
                    "phase": "Phase 0: Core",
                    "plan": "# Phase 0: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        _write_duplo_json(tmp_path, data)
        (tmp_path / "PLAN.md").write_text("# Phase 0: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.main.get_current_phase",
            return_value=(0, {"title": "Core"}),
        ):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan",
                    return_value="# Phase 1\n- [ ] Task",
                ):
                    with patch(
                        "duplo.main.save_plan",
                        return_value=tmp_path / "PLAN.md",
                    ) as mock_save:
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
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
                with patch("duplo.main.run_mcloop", return_value=0):
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
                with patch("duplo.main.run_mcloop", return_value=0):
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
                with patch("duplo.main.run_mcloop", return_value=0):
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
                with patch("duplo.main.run_mcloop", return_value=0):
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
                with patch("duplo.main.run_mcloop", return_value=0):
                    main()

        # Verify hashes were updated.
        from duplo.hasher import load_hashes

        saved = load_hashes(tmp_path)
        assert "new.txt" in saved


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
                    with patch("duplo.main.run_mcloop", return_value=0):
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

        with patch("duplo.main._rescrape_product_url", return_value=(0, 0)) as mock_rescrape:
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
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
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main._detect_and_append_gaps", return_value=(0, 0, 0, 0)) as mock_gaps:
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
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
        pages, examples = _rescrape_product_url()
        assert pages == 0
        assert examples == 0

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
                        pages, ex = _rescrape_product_url()
        assert pages == 1
        assert ex == 1


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
                with patch("duplo.main.run_mcloop", return_value=0):
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
                with patch("duplo.main.run_mcloop", return_value=0):
                    main()

        out = capsys.readouterr().out
        assert "No changes detected" in out
