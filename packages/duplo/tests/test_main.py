"""Tests for duplo.main CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.extractor import Feature
from duplo.main import _init_project, main
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

        with patch("duplo.main.fetch_site", return_value=("page text", [], None, [], {})):
            with patch("duplo.main.extract_features", return_value=[]):
                with patch("duplo.main.ask_preferences", return_value=BuildPreferences()):
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

        with patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})) as mock_fetch:
            with patch("duplo.main.extract_features", return_value=[]):
                with patch("duplo.main.ask_preferences", return_value=BuildPreferences()):
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

        mock_fetch.assert_called_once_with("https://first.com")

    def test_first_run_with_images_only(self, tmp_path, monkeypatch):
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.extract_features", return_value=[]):
            with patch("duplo.main.ask_preferences", return_value=BuildPreferences()):
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

        roadmap = [{"phase": 0, "title": "Core"}]
        with patch("duplo.main.fetch_site", return_value=("text", [], None, [], {})):
            with patch("duplo.main.extract_features", return_value=[]):
                with patch("duplo.main.ask_preferences", return_value=BuildPreferences()):
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
