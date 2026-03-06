"""Tests for duplo.main CLI entry point."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from duplo.main import _parse_args, main


class TestParseArgs:
    def test_init_subcommand_with_url(self):
        with patch("sys.argv", ["duplo", "init", "https://example.com"]):
            args = _parse_args()
        assert args.command == "init"
        assert args.url == "https://example.com"

    def test_run_subcommand(self):
        with patch("sys.argv", ["duplo", "run"]):
            args = _parse_args()
        assert args.command == "run"

    def test_next_subcommand(self):
        with patch("sys.argv", ["duplo", "next"]):
            args = _parse_args()
        assert args.command == "next"

    def test_no_args(self):
        with patch("sys.argv", ["duplo"]):
            args = _parse_args()
        assert args.command is None


class TestMain:
    def test_run_command(self, capsys, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [
                {"name": "Search", "description": "Full-text search.", "category": "core"}
            ],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 1\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md") as mock_save:
                    with patch("duplo.main.run_mcloop", return_value=0):
                        main()

        out = capsys.readouterr().out
        assert "PLAN.md" in out
        mock_save.assert_called_once()

    def test_run_command_captures_appshot_when_app_name_set(self, capsys, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "app_name": "MyApp",
            "features": [],
            "preferences": {
                "platform": "desktop",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 1\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
                        with patch("duplo.main.capture_appshot", return_value=0) as mock_shot:
                            main()

        mock_shot.assert_called_once()
        args, kwargs = mock_shot.call_args
        assert args[0] == "MyApp"
        out = capsys.readouterr().out
        assert "MyApp" in out

    def test_run_command_skips_appshot_when_no_app_name(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 1\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
                        with patch("duplo.main.capture_appshot") as mock_shot:
                            main()

        mock_shot.assert_not_called()

    def test_run_command_uses_run_sh_as_launch_when_present(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "app_name": "MyApp",
            "features": [],
            "preferences": {
                "platform": "desktop",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "run.sh").write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 1\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
                        with patch("duplo.main.capture_appshot", return_value=0) as mock_shot:
                            main()

        _, kwargs = mock_shot.call_args
        assert kwargs.get("launch") == "./run.sh"

    def test_run_command_appends_phase_history(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan", return_value="# Phase 1: Core\n"):
                with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                    with patch("duplo.main.run_mcloop", return_value=0):
                        main()

        data = json.loads((tmp_path / "duplo.json").read_text())
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "Phase 1: Core"

    def test_run_command_missing_duplo_json(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "run"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_next_command(self, capsys, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="some feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan", return_value="# Phase 2: Features\n"
                ) as mock_gen:
                    with patch(
                        "duplo.main.save_plan", return_value=tmp_path / "PLAN.md"
                    ) as mock_save:
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()
        out = capsys.readouterr().out
        assert "next phase" in out.lower()
        mock_gen.assert_called_once()
        mock_save.assert_called_once()

    def test_next_command_runs_mcloop(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch("duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0) as mock_run:
                            with patch("duplo.main.notify_phase_complete"):
                                main()
        mock_run.assert_called_once_with(".")

    def test_next_command_appends_phase_history(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan", return_value="# Phase 2: Next\n"
                ):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()
        data = json.loads((tmp_path / "duplo.json").read_text())
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "Phase 2: Next"

    def test_next_command_captures_appshot_when_app_name_set(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "app_name": "MyApp",
            "features": [],
            "preferences": {
                "platform": "desktop",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch("duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.capture_appshot", return_value=0) as mock_shot:
                                with patch("duplo.main.notify_phase_complete"):
                                    main()
        mock_shot.assert_called_once()
        assert mock_shot.call_args.args[0] == "MyApp"

    def test_next_command_skips_appshot_when_no_app_name(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch("duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.capture_appshot") as mock_shot:
                                with patch("duplo.main.notify_phase_complete"):
                                    main()
        mock_shot.assert_not_called()

    def test_next_command_exits_on_mcloop_failure(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch("duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=1):
                            with pytest.raises(SystemExit) as exc_info:
                                main()
        assert exc_info.value.code == 1

    def test_next_command_works_without_duplo_json(self, tmp_path, monkeypatch):
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch("duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete"):
                                main()  # should not raise

    def test_next_command_notifies_with_phase_label(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan",
                    return_value="# Phase 2: Enhancements\n",
                ):
                    with patch("duplo.main.save_plan", return_value=tmp_path / "PLAN.md"):
                        with patch("duplo.main.run_mcloop", return_value=0):
                            with patch("duplo.main.notify_phase_complete") as mock_notify:
                                main()
        mock_notify.assert_called_once_with("Phase 2: Enhancements")

    def test_next_command_loads_issues_when_present(self, tmp_path, monkeypatch):
        import json

        duplo_data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        (tmp_path / "duplo.json").write_text(json.dumps(duplo_data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        (tmp_path / "ISSUES.md").write_text("# Visual Issues\n- Layout broken\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback", return_value="feedback"):
                with patch(
                    "duplo.main.generate_next_phase_plan", return_value="# Phase 2\n"
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

    def test_next_command_missing_plan_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("sys.argv", ["duplo", "next"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_init_command(self, capsys, tmp_path):
        from pathlib import Path

        from duplo.extractor import Feature

        features = [Feature(name="Search", description="Full-text search.", category="core")]
        with patch("sys.argv", ["duplo", "init", "https://example.com"]):
            with patch("builtins.input", return_value=""):
                with patch("duplo.main.create_project_dir", return_value=tmp_path):
                    with patch("duplo.main.fetch_site", return_value="product page text"):
                        with patch("duplo.main.extract_features", return_value=features):
                            with patch("duplo.main.select_features", return_value=features):
                                with patch("duplo.main.ask_preferences"):
                                    with patch(
                                        "duplo.main.save_selections",
                                        return_value=Path("duplo.json"),
                                    ):
                                        with patch(
                                            "duplo.main.write_claude_md",
                                            return_value=Path("CLAUDE.md"),
                                        ):
                                            with patch(
                                                "duplo.main.generate_roadmap",
                                                return_value=None,
                                            ):
                                                main()
        out = capsys.readouterr().out
        assert "https://example.com" in out
        assert "product page text" in out

    def test_init_command_writes_claude_md(self, tmp_path):
        from pathlib import Path

        from duplo.extractor import Feature

        features = [Feature(name="Search", description="Full-text search.", category="core")]
        with patch("sys.argv", ["duplo", "init", "https://example.com"]):
            with patch("builtins.input", return_value=""):
                with patch("duplo.main.create_project_dir", return_value=tmp_path):
                    with patch("duplo.main.fetch_site", return_value="text"):
                        with patch("duplo.main.extract_features", return_value=features):
                            with patch("duplo.main.select_features", return_value=features):
                                with patch("duplo.main.ask_preferences"):
                                    with patch(
                                        "duplo.main.save_selections",
                                        return_value=Path("duplo.json"),
                                    ):
                                        with patch(
                                            "duplo.main.write_claude_md",
                                            return_value=Path("CLAUDE.md"),
                                        ) as mock_write:
                                            with patch(
                                                "duplo.main.generate_roadmap",
                                                return_value=None,
                                            ):
                                                main()
        mock_write.assert_called_once_with(target_dir=tmp_path)

    def test_no_args_exits(self):
        with patch("sys.argv", ["duplo"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1


class TestCmdRunResume:
    """Tests for resuming an interrupted 'duplo run'."""

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
        import json

        (tmp_path / "duplo.json").write_text(json.dumps(self._BASE_DATA), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan") as mock_gen:
                with patch("duplo.main.run_mcloop", return_value=0):
                    with patch("duplo.main.notify_phase_complete"):
                        main()

        mock_gen.assert_not_called()

    def test_skips_mcloop_when_mcloop_done(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 1", "mcloop_done": True},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.run_mcloop") as mock_run:
                with patch("duplo.main.notify_phase_complete"):
                    main()

        mock_run.assert_not_called()

    def test_exits_gracefully_when_phase1_complete(self, capsys, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "phases": [
                {
                    "phase": "Phase 1: Core",
                    "plan": "# Phase 1: Core\n",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch(
                "duplo.main.get_current_phase",
                return_value=(1, {"title": "Core"}),
            ):
                with patch("duplo.main.generate_phase_plan") as mock_gen:
                    with patch("duplo.main.run_mcloop") as mock_run:
                        main()

        mock_gen.assert_not_called()
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "already complete" in out.lower()

    def test_in_progress_cleared_after_success(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 1", "mcloop_done": False},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.run_mcloop", return_value=0):
                with patch("duplo.main.notify_phase_complete"):
                    main()

        result = json.loads((tmp_path / "duplo.json").read_text())
        assert "in_progress" not in result

    def test_resumes_print_message(self, capsys, tmp_path, monkeypatch):
        import json

        (tmp_path / "duplo.json").write_text(json.dumps(self._BASE_DATA), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 1: Core\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "run"]):
            with patch("duplo.main.generate_phase_plan"):
                with patch("duplo.main.run_mcloop", return_value=0):
                    with patch("duplo.main.notify_phase_complete"):
                        main()

        out = capsys.readouterr().out
        assert "Resuming" in out


class TestCmdNextResume:
    """Tests for resuming an interrupted 'duplo next'."""

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

    def test_skips_feedback_and_plan_when_in_progress(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 2", "mcloop_done": False},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 2: Features\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.collect_feedback") as mock_fb:
                with patch("duplo.main.generate_next_phase_plan") as mock_gen:
                    with patch("duplo.main.run_mcloop", return_value=0):
                        with patch("duplo.main.notify_phase_complete"):
                            main()

        mock_fb.assert_not_called()
        mock_gen.assert_not_called()

    def test_skips_mcloop_when_mcloop_done(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 2", "mcloop_done": True},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 2: Features\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.run_mcloop") as mock_run:
                with patch("duplo.main.notify_phase_complete"):
                    main()

        mock_run.assert_not_called()

    def test_resumes_with_mcloop_when_not_done(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 2", "mcloop_done": False},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 2: Features\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.run_mcloop", return_value=0) as mock_run:
                with patch("duplo.main.notify_phase_complete"):
                    main()

        mock_run.assert_called_once_with(".")

    def test_in_progress_cleared_after_resume(self, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 2", "mcloop_done": False},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 2: Features\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.run_mcloop", return_value=0):
                with patch("duplo.main.notify_phase_complete"):
                    main()

        result = json.loads((tmp_path / "duplo.json").read_text())
        assert "in_progress" not in result

    def test_resume_print_message(self, capsys, tmp_path, monkeypatch):
        import json

        data = {
            **self._BASE_DATA,
            "in_progress": {"label": "Phase 2", "mcloop_done": False},
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "PLAN.md").write_text("# Phase 2: Features\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with patch("sys.argv", ["duplo", "next"]):
            with patch("duplo.main.run_mcloop", return_value=0):
                with patch("duplo.main.notify_phase_complete"):
                    main()

        out = capsys.readouterr().out
        assert "Resuming" in out
