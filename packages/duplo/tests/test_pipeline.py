"""Tests for duplo.pipeline orchestration functions."""

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
from duplo.main import main
from duplo.pipeline import (
    ScrapeResult,
    UpdateSummary,
    _analyze_new_files,
    _detect_and_append_gaps,
    _download_site_media,
    _load_preferences,
    _persist_scrape_result,
    _print_summary,
    _rescrape_product_url,
    _scrape_declared_sources,
)
from duplo.pipeline import (
    _build_completion_history,
    _complete_phase,
    _investigation_context,
    _prefs_from_dict,
    _source_url_from_spec,
    _unimplemented_features,
    _visual_target_video_frames,
)
from duplo.questioner import BuildPreferences

_DUPLO_JSON = ".duplo/duplo.json"


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch):
    """Prevent argparse from seeing pytest's CLI arguments."""
    monkeypatch.setattr("sys.argv", ["duplo"])
    monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)


_STUB_SPEC = (
    "# Stub\n"
    "\n"
    "## Purpose\n"
    "Stub spec used by tests. Long enough to pass validate_for_run purpose check.\n"
    "\n"
    "## Architecture\n"
    "Web app using React.\n"
)


def _write_duplo_json(tmp_path: Path, data: dict) -> None:
    """Write duplo.json into the .duplo/ subdirectory of *tmp_path*.

    Also writes a stub SPEC.md so main()'s dispatch gate (7.2.3) routes
    to _subsequent_run. Tests that need specific SPEC.md content can
    overwrite the file afterwards.
    """
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir(exist_ok=True)
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
    spec_path = tmp_path / "SPEC.md"
    if not spec_path.exists():
        spec_path.write_text(_STUB_SPEC, encoding="utf-8")


def _read_duplo_json(tmp_path: Path) -> dict:
    """Read duplo.json from the .duplo/ subdirectory of *tmp_path*."""
    return json.loads((tmp_path / _DUPLO_JSON).read_text())


class TestPlanStartsWithPhaseHeading:
    """PLAN.md must start with a phase heading (# line).

    Visual design requirements are written to CLAUDE.md, not PLAN.md.
    mcloop extracts only the current phase content from PLAN.md, so any
    design block injected there never reaches the code generator.
    """

    def test_phase_zero_plan_md_starts_with_heading(self, tmp_path, monkeypatch):
        """End-to-end: PLAN.md's first non-blank line is the phase heading,
        and design requirements are NOT injected into PLAN.md."""
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {
                        "name": "Search",
                        "description": "Full-text search.",
                        "category": "core",
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
                        "goal": "Scaffold",
                        "features": ["Search"],
                        "test": "ok",
                    },
                ],
                "current_phase": 0,
                "design_requirements": {
                    "colors": {"primary": "#ff0000"},
                    "fonts": {"body": "Inter"},
                    "layout": {"grid": "12-col"},
                },
            },
        )
        monkeypatch.chdir(tmp_path)

        llm_output = "# Phase 0: Core\n\n- [ ] Scaffold project\n"
        with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
            with patch("duplo.pipeline.generate_phase_plan", return_value=llm_output):
                main()

        written = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        first_non_blank = next(line for line in written.splitlines() if line.strip())
        assert first_non_blank.startswith("# "), (
            f"PLAN.md must start with a phase heading, got: {first_non_blank!r}"
        )
        assert "Visual Design Requirements" not in written
        assert "### Colors" not in written
        assert "### Typography" not in written


class TestPlanProjectHeader:
    """PLAN.md must open with a top-level ``# {app_name}`` project header,
    not the first phase heading.

    The phase-generation loop in ``_subsequent_run`` previously wrote
    phase content directly to PLAN.md with no project header, so the
    file opened with ``# numi -- Phase 0: Scaffold`` on line 1. The
    correct structure -- mirroring duplo's own PLAN.md and mcloop's
    PLAN.md -- starts with ``# {app_name}``, a brief description, and
    a single platform/language/constraints line before any phases.
    """

    def test_plan_md_first_line_is_project_header(self, tmp_path, monkeypatch):
        """The very first line of PLAN.md is ``# {app_name}``, not a
        phase heading. The description block and platform line are
        present before any phase heading.
        """
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "app_name": "numi",
                "features": [],
                "preferences": {
                    "platform": "cli",
                    "language": "Python 3.11+",
                    "constraints": ["stdlib only"],
                    "preferences": [],
                },
                "roadmap": [
                    {
                        "phase": 0,
                        "title": "Scaffold",
                        "goal": "Scaffold",
                        "features": [],
                        "test": "ok",
                    },
                ],
                "current_phase": 0,
            },
        )
        # Override the stub SPEC.md with one that has an explicit Purpose.
        (tmp_path / "SPEC.md").write_text(
            "# numi\n"
            "\n"
            "## Purpose\n"
            "numi is a command-line calculator for quick arithmetic and unit "
            "conversions. It runs in the terminal with no GUI.\n"
            "\n"
            "## Architecture\n"
            "CLI written in Python 3.11+ using only the standard library.\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        llm_output = "# numi -- Phase 0: Scaffold\n\n- [ ] Scaffold project\n"
        with (
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value=llm_output),
        ):
            main()

        written = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        lines = written.splitlines()
        assert lines, "PLAN.md is empty"
        # First line must be the top-level project header, not a phase heading.
        assert lines[0] == "# numi", f"PLAN.md first line must be '# numi', got: {lines[0]!r}"
        # The project header must appear before any phase heading in the file.
        phase_idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("# ") and "Phase" in ln),
            None,
        )
        assert phase_idx is not None and phase_idx > 0, (
            "Phase heading must come after the project header"
        )
        # The description from SPEC.md Purpose is present.
        assert "command-line calculator" in written


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

        with patch("duplo.pipeline.generate_phase_plan") as mock_gen:
            with patch("duplo.pipeline.notify_phase_complete"):
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.notify_phase_complete"),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 1: Next\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "Completing Phase 0: Core" in out
        assert "Run mcloop to start building" in out
        # _BASE_DATA has two phases in the roadmap, so after completing the
        # first phase the pipeline generates plans for both roadmap phases.
        assert mock_gen.call_count == len(self._BASE_DATA["roadmap"])

    def test_feedback_collected_during_completion(self, tmp_path, monkeypatch):
        """Feedback is collected and saved when a phase completes."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.collect_feedback", return_value="great work"),
            patch("duplo.pipeline.save_feedback") as mock_save_fb,
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 1\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 0: Core\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        # On a first run (no completed phases) the loop starts from the
        # roadmap's own Phase 0 rather than 1-indexed history numbering.
        assert "Generating Phase 0: Core PLAN.md" in out
        assert "Run mcloop to start building" in out
        # The pipeline now generates a plan for every phase in the roadmap.
        assert mock_gen.call_count == len(self._BASE_DATA["roadmap"])

    def test_phase_number_from_history(self, tmp_path, monkeypatch):
        """Phase number passed to generate_phase_plan = len(phases) + idx + 1."""
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
                "duplo.pipeline.generate_phase_plan",
                return_value="# Core\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        # Two completed phases + two roadmap phases => phase_number 3 then 4.
        phase_numbers = [c.kwargs["phase_number"] for c in mock_gen.call_args_list]
        assert phase_numbers == [3, 4]

    def test_loops_over_every_roadmap_phase(self, capsys, tmp_path, monkeypatch):
        """Pipeline must call save_plan once per roadmap phase and print
        ``Plan ready for all N phases.`` after the loop."""
        roadmap = [
            {"phase": 0, "title": "Scaffold", "goal": "g", "features": [], "test": "ok"},
            {"phase": 1, "title": "Core", "goal": "g", "features": [], "test": "ok"},
            {"phase": 2, "title": "Polish", "goal": "g", "features": [], "test": "ok"},
        ]
        data = {
            **self._BASE_DATA,
            "roadmap": roadmap,
            "current_phase": 0,
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.generate_roadmap", return_value=roadmap) as mock_roadmap,
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md") as mock_save,
        ):
            main()

        # save_plan is called once for the top-level project header plus
        # once per phase; generate_phase_plan is called once per phase.
        assert mock_save.call_count == len(roadmap) + 1
        assert mock_gen.call_count == len(roadmap)
        # generate_roadmap is not re-invoked when the stored roadmap is valid.
        mock_roadmap.assert_not_called()
        out = capsys.readouterr().out
        assert f"Plan ready for all {len(roadmap)} phases." in out

    def test_first_run_fresh_roadmap_generates_all_phases(self, capsys, tmp_path, monkeypatch):
        """First run with no stored roadmap: generate_roadmap() is invoked
        and the pipeline then loops over every returned phase, calling
        generate_phase_plan() and save_plan() once per phase. The final
        summary line must be ``Plan ready for all N phases.`` and the
        loop must start from the roadmap's Phase 0 scaffold."""
        fresh_roadmap = [
            {"phase": 0, "title": "Scaffold", "goal": "g", "features": [], "test": "ok"},
            {"phase": 1, "title": "Core", "goal": "g", "features": ["Search"], "test": "ok"},
            {"phase": 2, "title": "Polish", "goal": "g", "features": [], "test": "ok"},
        ]
        # Base data has features but NO "roadmap" key and NO "phases" key,
        # so this exercises the fresh-roadmap first-run branch.
        data = {
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
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.generate_roadmap", return_value=fresh_roadmap) as mock_roadmap,
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md") as mock_save,
        ):
            main()

        # Roadmap is freshly generated exactly once.
        mock_roadmap.assert_called_once()
        # generate_phase_plan runs once per fresh-roadmap phase; save_plan
        # runs once per phase plus one extra call for the top-level
        # project header block.
        assert mock_gen.call_count == len(fresh_roadmap)
        assert mock_save.call_count == len(fresh_roadmap) + 1
        # Phase numbers passed to generate_phase_plan start at 0 and cover
        # every phase in the fresh roadmap.
        phase_numbers = [c.kwargs["phase_number"] for c in mock_gen.call_args_list]
        assert phase_numbers == [0, 1, 2]
        out = capsys.readouterr().out
        assert f"Plan ready for all {len(fresh_roadmap)} phases." in out
        assert "Run mcloop to start building" in out

    def test_first_run_persists_roadmap_and_passes_each_phase_dict(self, tmp_path, monkeypatch):
        """First-run regression test: after generate_roadmap() returns a
        fresh roadmap, the pipeline must (a) persist the full roadmap
        plus ``current_phase = 0`` to duplo.json, and (b) invoke
        generate_phase_plan() with each roadmap entry in order -- not
        just the first one. This guards against a revert to the old
        single-phase first-run behavior described in BUGS.md.
        """
        fresh_roadmap = [
            {"phase": 0, "title": "Scaffold", "goal": "s", "features": [], "test": "ok"},
            {"phase": 1, "title": "Core", "goal": "c", "features": ["Search"], "test": "ok"},
            {"phase": 2, "title": "Polish", "goal": "p", "features": [], "test": "ok"},
        ]
        data = {
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
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.generate_roadmap", return_value=fresh_roadmap),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md") as mock_save,
        ):
            main()

        # The fresh roadmap and current_phase=0 are persisted to disk.
        saved = _read_duplo_json(tmp_path)
        assert saved["roadmap"] == fresh_roadmap
        assert saved["current_phase"] == 0

        # Every roadmap entry is passed to generate_phase_plan in order.
        phases_passed = [c.kwargs["phase"] for c in mock_gen.call_args_list]
        assert phases_passed == fresh_roadmap

        # save_plan fires once per phase so each plan is appended, plus
        # one extra call for the top-level project header block.
        assert mock_save.call_count == len(fresh_roadmap) + 1


class TestSubsequentRunPhaseLoopClaudeCliError:
    """Plan-generation loop must tolerate ClaudeCliError mid-loop.

    Retry logic inside query() can still exhaust all attempts. When that
    happens, earlier phases already appended to PLAN.md via save_plan()
    must survive: the exception must be swallowed, the loop must break,
    and the summary must reflect the partial save.
    """

    def test_claude_cli_error_on_third_phase_preserves_first_two(
        self, capsys, tmp_path, monkeypatch
    ):
        from duplo.claude_cli import ClaudeCliError

        roadmap = [
            {"phase": 0, "title": "Scaffold", "goal": "g", "features": [], "test": "ok"},
            {"phase": 1, "title": "Core", "goal": "g", "features": [], "test": "ok"},
            {"phase": 2, "title": "Polish", "goal": "g", "features": [], "test": "ok"},
        ]
        data = {
            "source_url": "https://example.com",
            "features": [],
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "roadmap": roadmap,
            "current_phase": 0,
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        call_count = {"n": 0}

        def fake_generate(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise ClaudeCliError("simulated retry exhaustion")
            return f"# Phase {kwargs.get('phase_number', call_count['n'])}\n- [ ] task\n"

        with (
            patch("duplo.pipeline.generate_phase_plan", side_effect=fake_generate) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md") as mock_save,
        ):
            # Must not propagate the ClaudeCliError out of main().
            main()

        # Third call raised, so generate_phase_plan ran three times total.
        assert mock_gen.call_count == 3
        # Only the first two phases were saved -- the third failed before
        # save_plan could be called. One additional save_plan call is made
        # before the loop to write the top-level project header block.
        assert mock_save.call_count == 2 + 1

        out = capsys.readouterr().out
        # Failure message reports the partial save count.
        assert "plan generation failed after retries" in out
        assert "2 of 3 phases saved to PLAN.md" in out
        # Closing summary reports the actual saved count, not the total.
        assert "Plan ready for 2 of 3 phases." in out


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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.pipeline.extract_features", return_value=[]):
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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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

        with patch("duplo.pipeline._analyze_new_files") as mock_analyze:
            mock_analyze.return_value = UpdateSummary()
            with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.pipeline.extract_features", return_value=[]):
                    main()

        # ref/ files are durable user inputs and stay in place; the hash
        # for ref/new.txt is recorded at its original path so future runs
        # can detect changes.
        from duplo.hasher import load_hashes

        saved = load_hashes(tmp_path)
        assert "ref/new.txt" in saved
        assert (tmp_path / "ref" / "new.txt").exists()


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
        with patch("duplo.pipeline.extract_design", return_value=design) as mock_design:
            _analyze_new_files(["new_screen.png"])

        mock_design.assert_called_once()
        data = _read_duplo_json(tmp_path)
        assert data["design_requirements"]["colors"] == {"primary": "#fff"}

    def test_extracts_text_from_new_pdfs(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        pdf = tmp_path / "spec.pdf"
        pdf.write_bytes(b"%PDF" * 100)

        with patch("duplo.pipeline.extract_pdf_text", return_value="PDF content") as mock_pdf:
            _analyze_new_files(["spec.pdf"])

        mock_pdf.assert_called_once()
        out = capsys.readouterr().out
        assert "PDF" in out

    def test_text_file_with_url_does_not_trigger_fetch(self, tmp_path, monkeypatch):
        """URLs in text files under ref/ must NOT be scraped — sources
        come exclusively from SPEC.md ## Sources."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        txt = tmp_path / "urls.txt"
        txt.write_text("Check https://newsite.com for the product details")

        with patch("duplo.pipeline.fetch_site") as mock_fetch:
            summary = _analyze_new_files(["urls.txt"])

        mock_fetch.assert_not_called()
        assert not hasattr(summary, "urls_fetched")

    def test_skips_nonexistent_files(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _analyze_new_files(["nonexistent.png"])
        out = capsys.readouterr().out
        assert out == ""

    def test_gates_pdf_and_text_on_spec_role(self, tmp_path, monkeypatch):
        """PDFs/text with role counter-example, ignore, or proposed=true
        must not be read into collected_text; files absent from References
        fall through (backward compat)."""
        from duplo.spec_reader import ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        docs_pdf = ref_dir / "docs.pdf"
        docs_pdf.write_bytes(b"%PDF-docs")
        bad_pdf = ref_dir / "bad.pdf"
        bad_pdf.write_bytes(b"%PDF-bad")
        ignored_pdf = ref_dir / "ignored.pdf"
        ignored_pdf.write_bytes(b"%PDF-ignored")
        proposed_pdf = ref_dir / "proposed.pdf"
        proposed_pdf.write_bytes(b"%PDF-proposed")
        stray_txt = ref_dir / "stray.txt"
        stray_txt.write_text("stray-content")
        bad_txt = ref_dir / "bad.txt"
        bad_txt.write_text("bad-content")

        spec = ProductSpec(
            raw="",
            references=[
                ReferenceEntry(path=Path("ref/docs.pdf"), roles=["docs"]),
                ReferenceEntry(path=Path("ref/bad.pdf"), roles=["counter-example"]),
                ReferenceEntry(path=Path("ref/ignored.pdf"), roles=["ignore"]),
                ReferenceEntry(path=Path("ref/proposed.pdf"), roles=["docs"], proposed=True),
                ReferenceEntry(path=Path("ref/bad.txt"), roles=["counter-example"]),
            ],
        )

        with patch("duplo.pipeline.extract_pdf_text", return_value="DOCS-PDF") as mock_pdf:
            summary = _analyze_new_files(
                [
                    "ref/docs.pdf",
                    "ref/bad.pdf",
                    "ref/ignored.pdf",
                    "ref/proposed.pdf",
                    "ref/stray.txt",
                    "ref/bad.txt",
                ],
                spec=spec,
            )

        passed_pdfs = mock_pdf.call_args[0][0]
        assert [p.name for p in passed_pdfs] == ["docs.pdf"]
        assert "DOCS-PDF" in summary.collected_text
        assert "bad-content" not in summary.collected_text
        assert "stray-content" in summary.collected_text
        assert summary.pdfs_extracted == 1
        assert summary.text_files_read == 1

    def test_does_not_move_references(self, tmp_path, monkeypatch):
        """ref/ files are durable user inputs and must stay in place;
        SPEC.md paths point at ref/ and break if files are relocated."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "ref.png"
        img.write_bytes(b"PNG" * 500)

        design = DesignRequirements(
            colors={"primary": "#000"},
            source_images=["ref.png"],
        )
        with patch("duplo.pipeline.extract_design", return_value=design):
            _analyze_new_files(["ref.png"])

        assert img.exists()
        assert not (tmp_path / ".duplo" / "references" / "ref.png").exists()

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
        # Overwrite stub SPEC.md to declare the ref image so the
        # spec-driven path picks it up.
        (tmp_path / "SPEC.md").write_text(
            "# Stub\n\n## Purpose\n"
            "Stub spec used by tests. Long enough to pass validate_for_run check.\n"
            "\n## Architecture\nWeb app using React.\n"
            "\n## References\n- ref/new.png\n  role: visual-target\n",
            encoding="utf-8",
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
        with patch("duplo.pipeline.extract_design", return_value=design):
            with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ) as mock_fetch:
            with patch("duplo.pipeline.save_reference_urls") as mock_save_urls:
                with patch("duplo.pipeline.save_raw_content") as mock_save_raw:
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

        with patch("duplo.pipeline.fetch_site") as mock_fetch:
            _rescrape_product_url()

        mock_fetch.assert_not_called()
        assert capsys.readouterr().out == ""

    def test_skips_when_no_duplo_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("duplo.pipeline.fetch_site") as mock_fetch:
            _rescrape_product_url()

        mock_fetch.assert_not_called()

    def test_handles_fetch_failure(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        with patch("duplo.pipeline.fetch_site", side_effect=Exception("network error")):
            _rescrape_product_url()

        out = capsys.readouterr().out
        assert "Failed to re-scrape" in out
        assert "network error" in out

    def test_saves_code_examples(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        examples = [{"input": "1+1", "expected_output": "2", "source_url": "", "language": "py"}]
        with patch(
            "duplo.pipeline.fetch_site",
            return_value=("text", examples, None, [], {}),
        ):
            with patch("duplo.pipeline.save_examples") as mock_save_ex:
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {}),
        ):
            with patch("duplo.pipeline.save_reference_urls") as mock_save:
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.pipeline.save_reference_urls") as mock_save:
                with patch("duplo.pipeline.save_raw_content"):
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.pipeline.save_reference_urls") as mock_save:
                with patch("duplo.pipeline.save_raw_content"):
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

        with patch("duplo.pipeline.fetch_site") as mock_fetch:
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.pipeline.save_reference_urls"):
                with patch("duplo.pipeline.save_raw_content"):
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.pipeline.save_reference_urls"):
                with patch("duplo.pipeline.save_raw_content"):
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
            "duplo.pipeline.fetch_site",
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
                "duplo.pipeline.fetch_site",
                return_value=("text", [], None, records, {}),
            ),
            patch("duplo.pipeline.extract_features") as mock_extract,
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

        with patch("duplo.pipeline.fetch_site") as mock_fetch:
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

        with patch(
            "duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")
        ) as mock_rescrape:
            with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
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
            },
        )
        plan = "# Phase 0: Core\n- [ ] Build UI\n"
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")

        from duplo.gap_detector import GapResult
        from duplo.spec_reader import DesignBlock, ProductSpec

        spec = ProductSpec(
            raw="",
            design=DesignBlock(
                auto_generated="### Colors\n- **primary**: `#ff0000`",
            ),
        )
        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps(spec=spec)

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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps()

        out = capsys.readouterr().out
        assert "covered" in out.lower()

    def test_skips_when_no_features(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        (tmp_path / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

        with patch("duplo.pipeline.detect_gaps") as mock_detect:
            _detect_and_append_gaps()

        mock_detect.assert_not_called()

    def test_skips_when_no_plan(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {"source_url": "", "features": [{"name": "X", "description": "x", "category": "c"}]},
        )

        with patch("duplo.pipeline.detect_gaps") as mock_detect:
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
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
        from duplo.spec_reader import DesignBlock, ProductSpec

        spec = ProductSpec(
            raw="",
            design=DesignBlock(
                auto_generated="### Colors\n- **accent**: `#00ff00`",
            ),
        )
        gap_result = GapResult(
            missing_features=[MissingFeature(name="Websocket", reason="Not covered")],
            missing_examples=[],
        )
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            _detect_and_append_gaps(spec=spec)

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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result) as mock_detect:
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result) as mock_detect:
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result) as mock_detect:
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

        with patch("duplo.pipeline.detect_gaps") as mock_detect:
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

        with patch(
            "duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)
        ) as mock_gaps:
            with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
                with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                    with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
                        main()

        mock_gaps.assert_called_once()


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
        with patch("duplo.pipeline.extract_design", return_value=design):
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
            patch("duplo.pipeline.extract_all_videos", return_value=[vid_result]),
            patch("duplo.pipeline.filter_frames", return_value=[decision]),
            patch("duplo.pipeline.apply_filter", return_value=[frame]),
            patch("duplo.pipeline.describe_frames", return_value=[desc]),
            patch("duplo.pipeline.store_accepted_frames", return_value=["frame001.png"]),
            patch("duplo.pipeline.extract_design", return_value=design) as mock_design,
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
            patch("duplo.pipeline.extract_all_videos", return_value=[vid_result]),
            patch("duplo.pipeline.filter_frames", return_value=[decision]),
            patch("duplo.pipeline.apply_filter", return_value=[frame]),
            patch("duplo.pipeline.describe_frames", return_value=[desc]),
            patch("duplo.pipeline.store_accepted_frames", return_value=["frame001.png"]),
            patch("duplo.pipeline.extract_design", return_value=design) as mock_design,
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
                "duplo.pipeline.collect_design_input",
                return_value=[img],
            ) as mock_cdi,
            patch("duplo.pipeline.extract_design", return_value=design),
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
        with patch("duplo.pipeline.extract_design", return_value=design) as mock_design:
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
                "duplo.pipeline.extract_all_videos",
                return_value=[],
            ) as mock_extract,
            patch("duplo.pipeline.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[beh_vid, vis_vid],
                pdfs=[],
                text_files=[],
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
                "duplo.pipeline.extract_all_videos",
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
                "duplo.pipeline.extract_all_videos",
            ) as mock_extract,
            patch("duplo.pipeline.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vis_vid],
                pdfs=[],
                text_files=[],
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
                "duplo.pipeline.extract_all_videos",
            ) as mock_extract,
            patch("duplo.pipeline.scan_files") as mock_scan,
        ):
            from duplo.scanner import ScanResult

            mock_scan.return_value = ScanResult(
                images=[],
                videos=[vid],
                pdfs=[],
                text_files=[],
            )
            _analyze_new_files(
                ["ref/proposed.mp4"],
                spec=spec,
            )

        mock_extract.assert_not_called()


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
            "duplo.pipeline.fetch_site",
            return_value=("text", examples, None, records, {"https://example.com": "<html/>"}),
        ):
            with patch("duplo.pipeline.save_reference_urls"):
                with patch("duplo.pipeline.save_raw_content"):
                    with patch("duplo.pipeline.save_examples"):
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            mf, me, dr, ta = _detect_and_append_gaps()
        assert mf == 2
        assert me == 0
        assert dr == 0
        assert ta == 2

    def test_spec_auto_generated_design_used(self, tmp_path, monkeypatch):
        """Design gaps read from SPEC.md AUTO-GENERATED block only."""
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
                auto_generated=(
                    "### Colors\n- **accent**: `#0000ff`\n\n### Typography\n- **body**: Roboto"
                ),
            ),
        )

        gap_result = GapResult(missing_features=[], missing_examples=[])
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            with patch("duplo.pipeline.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=spec)

        # detect_design_gaps should have been called with spec design only.
        call_design = mock_ddg.call_args[0][1]
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            with patch("duplo.pipeline.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=spec)

        call_design = mock_ddg.call_args[0][1]
        assert call_design["colors"]["primary"] == "#123456"

    def test_no_spec_produces_no_design_gaps(self, tmp_path, monkeypatch):
        """Without spec, no design gaps are detected."""
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
        with patch("duplo.pipeline.detect_gaps", return_value=gap_result):
            with patch("duplo.pipeline.detect_design_gaps", wraps=detect_design_gaps) as mock_ddg:
                _detect_and_append_gaps(spec=None)

        # detect_design_gaps should not have been called (no spec = no design data).
        mock_ddg.assert_not_called()


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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
                with patch("duplo.pipeline.extract_features", return_value=[]):
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

        with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
            with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
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
            "duplo.pipeline.fetch_site",
            return_value=("product content", [], None, records, {}),
        ):
            with patch("duplo.pipeline.save_reference_urls"):
                pages, ex, text = _rescrape_product_url()
        assert text == "product content"

    def test_returns_empty_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})
        with patch("duplo.pipeline.fetch_site", side_effect=Exception("fail")):
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[new_feature]) as mock_extract,
            patch("duplo.pipeline.save_features") as mock_save,
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        assert call_args.args == ("product text",)
        assert call_args.kwargs["existing_names"] == ["Auth"]
        mock_save.assert_called_once_with([new_feature])

    def test_skips_extraction_when_no_scraped_text(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline.extract_features") as mock_extract,
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch(
                "duplo.pipeline.collect_issues",
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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

        with patch("duplo.pipeline.generate_roadmap") as mock_gen:
            main()

        mock_gen.assert_not_called()
        out = capsys.readouterr().out
        assert "All features implemented" in out

    def test_stops_when_roadmap_generation_fails(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.generate_roadmap", return_value=[]),
            patch("duplo.pipeline.generate_phase_plan") as mock_plan,
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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap) as mock_gen,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        saved = _read_duplo_json(tmp_path)
        assert saved["current_phase"] == 0
        assert saved["roadmap"] == fake_roadmap

    def test_first_run_loops_over_all_generated_phases(self, capsys, tmp_path, monkeypatch):
        """First-run path must generate a plan for EVERY phase in the
        freshly generated roadmap, not just the first one.

        Reproduces the BUGS.md ticket: when duplo.json has no roadmap
        yet, ``generate_roadmap()`` is called, and the pipeline must
        then loop over the returned roadmap (starting from Phase 0 /
        scaffold) calling ``generate_phase_plan()`` for each.
        """
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        fake_roadmap = [
            {
                "phase": 0,
                "title": "Scaffold",
                "goal": "Scaffold project",
                "features": [],
                "test": "scaffold ok",
            },
            {
                "phase": 1,
                "title": "Core",
                "goal": "Build core",
                "features": ["Export"],
                "test": "core ok",
            },
            {
                "phase": 2,
                "title": "Polish",
                "goal": "Polish it",
                "features": [],
                "test": "polish ok",
            },
        ]
        with (
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n- [ ] task",
            ) as mock_gen,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md") as mock_save,
        ):
            main()

        # generate_phase_plan must fire once per roadmap phase, proving
        # the loop is not short-circuited. save_plan fires once per phase
        # plus one extra call for the top-level project header block.
        assert mock_gen.call_count == len(fake_roadmap)
        assert mock_save.call_count == len(fake_roadmap) + 1

        # Phase numbers must start at 0 (scaffold) and walk through
        # every entry in the roadmap.
        phase_numbers = [c.kwargs["phase_number"] for c in mock_gen.call_args_list]
        assert phase_numbers == [0, 1, 2]

        # The phase dicts passed in must be the roadmap entries in
        # order, not a single ``phase_info``.
        phases_passed = [c.kwargs["phase"] for c in mock_gen.call_args_list]
        assert phases_passed == fake_roadmap

        out = capsys.readouterr().out
        assert f"Plan ready for all {len(fake_roadmap)} phases." in out
        assert "Generating Phase 0: Scaffold PLAN.md" in out
        assert "Generating Phase 2: Polish PLAN.md" in out

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
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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

        with patch("duplo.pipeline.generate_roadmap") as mock_gen:
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], doc_structs, [], {}),
        ):
            with patch("duplo.pipeline.save_doc_structures") as mock_save_docs:
                _rescrape_product_url()

        mock_save_docs.assert_called_once_with(doc_structs)

    def test_skips_when_no_doc_structures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "https://example.com", "features": []})

        with patch(
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, [], {}),
        ):
            with patch("duplo.pipeline.save_doc_structures") as mock_save_docs:
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
            "duplo.pipeline.fetch_site",
            return_value=(
                "text",
                [],
                None,
                [],
                product_ref_raw_pages,
            ),
        ):
            with patch(
                "duplo.pipeline._download_site_media",
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
            "duplo.pipeline.fetch_site",
            return_value=("text", [], None, [], {}),
        ):
            with patch("duplo.pipeline._download_site_media") as mock_dl:
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[new_feat]),
            # Let save_features actually run so counting logic works
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[dup_feat]),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", side_effect=corrupt_json),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[]),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        out = capsys.readouterr().out
        assert "No features extracted" in out


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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
            ) as mock_match,
        ):
            _complete_phase("- [x] task\n", "", "Phase 1")

        mock_match.assert_not_called()

    def test_all_tasks_annotated_skips_unannotated_matching(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": self._FEATURES})

        plan_content = '- [x] Add search [feat: "Search"]\n'

        with (
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
            ) as mock_match,
        ):
            _complete_phase(plan_content, "", "Phase 1")

        mock_match.assert_not_called()

    def test_no_matches_prints_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"features": self._FEATURES})

        with (
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
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
            patch("duplo.pipeline.append_phase_to_history") as mock_append,
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch(
                "duplo.pipeline.match_unannotated_tasks",
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
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
            patch("duplo.pipeline.append_phase_to_history"),
            patch("duplo.pipeline.advance_phase"),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.capture_appshot", return_value=-2),
        ):
            _complete_phase("- [x] task\n", "MyApp", "Phase 1")

        out = capsys.readouterr().out
        assert "Screenshot capture timed out (skipping)" in out


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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch(
                "duplo.pipeline._detect_and_append_gaps",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch(
                "duplo.pipeline._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ) as mock_gaps,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        with patch("duplo.pipeline.investigate", return_value=result):
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

        mock_inv = patch("duplo.pipeline.investigate", return_value=result)
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

        mock_inv = patch("duplo.pipeline.investigate", return_value=result)
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


class TestFixModeSpecContext:
    """Tests that _fix_mode passes spec context (counter-examples,
    behavior contracts) through to investigate()."""

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

    def test_counter_examples_reach_investigator(self, capsys, tmp_path, monkeypatch):
        """Counter-example references from SPEC.md reach investigate() kwargs."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")

        # Create a counter-example reference file in ref/.
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        ce_file = ref_dir / "bad_design.png"
        ce_file.write_bytes(b"PNG" * 10)

        # Write a SPEC.md declaring the counter-example reference.
        spec_text = (
            "# TestApp\n\n"
            "## Purpose\nA test app.\n\n"
            "## References\n"
            "- ref/bad_design.png\n"
            "  role: counter-example\n"
            "  notes: Avoid this layout\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_text, encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "layout is wrong"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="layout mismatch",
                    expected="correct layout",
                    severity="major",
                    area="UI",
                ),
            ],
            summary="One bug.",
        )

        with patch("duplo.pipeline.investigate", return_value=result) as mock_inv:
            main()

        # investigate() was called with counter_examples kwarg.
        assert mock_inv.call_count == 1
        kwargs = mock_inv.call_args[1]
        assert "counter_examples" in kwargs
        assert len(kwargs["counter_examples"]) == 1
        assert kwargs["counter_examples"][0].path == Path("ref/bad_design.png")

    def test_behavior_contracts_reach_investigator(self, capsys, tmp_path, monkeypatch):
        """Behavior contracts from SPEC.md reach investigate() kwargs."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")

        # Write SPEC.md with behavior contracts.
        spec_text = (
            "# TestApp\n\n"
            "## Purpose\nA test app.\n\n"
            "## Behavior\n"
            "`2+3` \u2192 `5`\n"
            "`10/2` \u2192 `5`\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_text, encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "wrong calculation"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="calc error",
                    expected="correct result",
                    severity="major",
                    area="math",
                ),
            ],
            summary="One bug.",
        )

        with patch("duplo.pipeline.investigate", return_value=result) as mock_inv:
            main()

        # investigate() was called with behavior_contracts kwarg.
        assert mock_inv.call_count == 1
        kwargs = mock_inv.call_args[1]
        assert "behavior_contracts" in kwargs
        assert len(kwargs["behavior_contracts"]) == 2
        assert kwargs["behavior_contracts"][0].input == "2+3"
        assert kwargs["behavior_contracts"][0].expected == "5"
        assert kwargs["behavior_contracts"][1].input == "10/2"
        assert kwargs["behavior_contracts"][1].expected == "5"

    def test_counter_example_sources_reach_investigator(self, capsys, tmp_path, monkeypatch):
        """Counter-example source URLs from SPEC.md reach investigate() kwargs."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")

        # Write SPEC.md with a counter-example source.
        spec_text = (
            "# TestApp\n\n"
            "## Purpose\nA test app.\n\n"
            "## Sources\n"
            "- https://bad-example.com\n"
            "  role: counter-example\n"
            "  scrape: none\n"
            "  notes: Anti-pattern site\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_text, encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "fix", "design is wrong"])

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="design issue",
                    expected="better design",
                    severity="minor",
                    area="UI",
                ),
            ],
            summary="One bug.",
        )

        with patch("duplo.pipeline.investigate", return_value=result) as mock_inv:
            main()

        assert mock_inv.call_count == 1
        kwargs = mock_inv.call_args[1]
        assert "counter_example_sources" in kwargs
        assert len(kwargs["counter_example_sources"]) == 1
        assert kwargs["counter_example_sources"][0].url == "https://bad-example.com"

    def test_investigate_flag_also_passes_spec_context(self, capsys, tmp_path, monkeypatch):
        """The --investigate path also passes counter-examples and contracts."""
        from duplo.investigator import Diagnosis, InvestigationResult

        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text("- [x] done\n", encoding="utf-8")

        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        (ref_dir / "avoid.png").write_bytes(b"PNG" * 10)

        spec_text = (
            "# TestApp\n\n"
            "## Purpose\nA test app.\n\n"
            "## Behavior\n"
            "`hello` \u2192 `world`\n\n"
            "## References\n"
            "- ref/avoid.png\n"
            "  role: counter-example\n"
        )
        (tmp_path / "SPEC.md").write_text(spec_text, encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["duplo", "fix", "--investigate", "greeting broken"],
        )

        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="greeting broken",
                    expected="correct greeting",
                    severity="major",
                    area="greet",
                ),
            ],
            summary="One bug.",
        )

        with patch("duplo.pipeline.investigate", return_value=result) as mock_inv:
            main()

        assert mock_inv.call_count == 1
        kwargs = mock_inv.call_args[1]
        assert "counter_examples" in kwargs
        assert "behavior_contracts" in kwargs
        assert kwargs["behavior_contracts"][0].input == "hello"
        assert kwargs["counter_examples"][0].path == Path("ref/avoid.png")


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
        from duplo.pipeline import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.pipeline.compare_screenshots", return_value=result) as mock_cmp:
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
        from duplo.pipeline import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.pipeline.compare_screenshots", return_value=result) as mock_cmp:
            _compare_with_references(current)

        refs = mock_cmp.call_args[0][1]
        assert len(refs) == 1
        assert "screenshots" in str(refs[0])

    def test_skips_when_no_references(self, tmp_path, monkeypatch, capsys):
        """Prints skip message when no reference images found anywhere."""
        monkeypatch.chdir(tmp_path)
        current = tmp_path / "main.png"
        current.write_bytes(b"PNG" * 100)

        from duplo.pipeline import _compare_with_references

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
        from duplo.pipeline import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.pipeline.compare_screenshots", return_value=result) as mock_cmp:
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
        from duplo.pipeline import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.pipeline.compare_screenshots", return_value=result) as mock_cmp:
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
        from duplo.pipeline import _compare_with_references

        result = ComparisonResult(similar=True, summary="ok", details=[])
        with patch("duplo.pipeline.compare_screenshots", return_value=result) as mock_cmp:
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.generate_phase_plan") as mock_gen,
        ):
            main()

        mock_gen.assert_not_called()

    def test_does_not_complete_phase(self, tmp_path, monkeypatch):
        """Should NOT call _complete_phase when tasks remain unchecked."""
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "PLAN.md").write_text(self._PHASE2_PLAN, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.append_phase_to_history") as mock_append,
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.pipeline.match_unannotated_tasks") as mock_matcher,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=["Search is slow"]) as mock_issues,
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch("duplo.pipeline.generate_phase_plan", return_value=phase3_plan) as mock_plan,
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.collect_feedback", return_value=""),
            patch("duplo.pipeline.collect_issues", return_value=[]),
            patch("duplo.pipeline.notify_phase_complete"),
            patch("duplo.pipeline.capture_appshot", return_value=-1),
            patch("duplo.pipeline.match_unannotated_tasks", return_value=([], [])) as mock_matcher,
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 3\n- [ ] task",
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch("duplo.pipeline.load_frame_descriptions", return_value=[]),
            patch(
                "duplo.pipeline.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ) as mock_fmt,
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.pipeline.save_plan", return_value="PLAN.md") as mock_save,
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch("duplo.pipeline.extract_verification_cases", return_value=[]),
            patch(
                "duplo.pipeline.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.pipeline.save_plan", return_value="PLAN.md") as mock_save,
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
            patch("duplo.pipeline.read_spec", return_value=None),
            patch(
                "duplo.pipeline.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch(
                "duplo.pipeline.extract_verification_cases",
                return_value=vcases,
            ),
            patch(
                "duplo.pipeline.format_verification_tasks",
                return_value="\n- [ ] Verify: type `1+1`, expect `2`\n",
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.pipeline.save_plan", return_value="PLAN.md") as mock_save,
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.load_frame_descriptions",
                return_value=[{"state": "home"}],
            ),
            patch(
                "duplo.pipeline.extract_verification_cases",
                return_value=vcases,
            ),
            patch(
                "duplo.pipeline.format_verification_tasks",
                return_value="\n- [ ] Verify: type `1+1`, expect `2`\n",
            ),
            patch(
                "duplo.pipeline.format_contracts_as_verification",
                return_value=self._SPEC_VTASKS,
            ),
            patch("duplo.main.select_features", return_value=[feat]),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase 1\n- [ ] task\n",
            ),
            patch("duplo.pipeline.save_plan", return_value="PLAN.md") as mock_save,
        ):
            main()

        saved_content = mock_save.call_args[0][0]
        assert "Verify: type `1+1`" in saved_content
        assert "Functional verification from product spec" in saved_content


class TestValidateForRunWiring:
    """validate_for_run is called after read_spec in _subsequent_run."""

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
            patch("duplo.pipeline.read_spec", return_value=mock_spec),
            patch("duplo.pipeline.validate_for_run", return_value=vr),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "## Architecture still contains <FILL IN>" in captured.err

    def test_no_validation_when_no_spec_subsequent_run(self, tmp_path, monkeypatch):
        """When read_spec returns None on a subsequent run, validate_for_run is not called."""
        _write_duplo_json(tmp_path, {"features": []})
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.read_spec", return_value=None),
            patch("duplo.pipeline.validate_for_run") as mock_validate,
            patch("duplo.pipeline.load_hashes", return_value={}),
            patch("duplo.pipeline.compute_hashes", return_value={}),
            patch("duplo.pipeline.diff_hashes") as mock_diff,
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline.save_hashes"),
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

    def test_subsequent_run_blocked_by_fill_in_purpose(self, tmp_path, monkeypatch, capsys):
        """Subsequent run (.duplo/ exists) exits 1 when Purpose has <FILL IN>."""
        _write_duplo_json(tmp_path, {"features": []})
        (tmp_path / "SPEC.md").write_text(self.SPEC_WITH_FILL_IN)
        monkeypatch.chdir(tmp_path)

        with (
            patch("duplo.pipeline.compute_hashes") as mock_hash,
            patch("duplo.pipeline.fetch_site") as mock_fetch,
            patch("duplo.pipeline.extract_features") as mock_extract,
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


class TestDocsTextInFeatureExtraction:
    """Docs-role text feeds into extract_features via docs_text_extractor."""

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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=type("V", (), {"warnings": [], "errors": []})(),
            ),
            patch(
                "duplo.pipeline.compute_hashes",
                return_value={"a.txt": "abc"},
            ),
            patch("duplo.pipeline.load_hashes", return_value={"a.txt": "abc"}),
            patch(
                "duplo.pipeline.diff_hashes",
                return_value=type(
                    "D",
                    (),
                    {"added": [], "changed": [], "removed": []},
                )(),
            ),
            patch("duplo.pipeline.save_hashes"),
            patch(
                "duplo.pipeline._rescrape_product_url",
                return_value=(0, [], "rescraped"),
            ),
            patch(
                "duplo.pipeline.docs_text_extractor",
                return_value="docs text from md",
            ) as mock_docs,
            patch(
                "duplo.pipeline.extract_features",
                return_value=[Feature(name="F1", description="d", category="c")],
            ) as mock_ef,
            patch("duplo.pipeline.save_features"),
            patch("duplo.pipeline._detect_and_append_gaps"),
            patch("duplo.pipeline._print_summary"),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[kept, excluded]),
            patch("duplo.pipeline.save_features") as mock_save,
            patch(
                "duplo.pipeline._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[feat_a, feat_b]),
            patch("duplo.pipeline.save_features") as mock_save,
            patch(
                "duplo.pipeline._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
                "duplo.pipeline._rescrape_product_url",
                return_value=(1, 0, "product text"),
            ),
            patch("duplo.pipeline.extract_features", return_value=[excluded]),
            patch("duplo.pipeline.save_features") as mock_save,
            patch(
                "duplo.pipeline._detect_and_append_gaps",
                return_value=(0, 0, 0, 0),
            ),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch("duplo.pipeline.investigate", return_value=result) as mock_inv,
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
        assert len(result) == 1
        assert result[0].platform == "web"
        assert result[0].language == "Go"

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
        assert len(result) == 1
        assert result[0].platform == "cli"

    def test_returns_cached_when_hash_matches(self):
        from duplo.build_prefs import architecture_hash

        arch = "Web app in Python"
        h = architecture_hash(arch)
        spec = MagicMock()
        spec.architecture = arch
        spec.platform_entries = []
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": h,
        }
        with patch("duplo.pipeline.parse_build_preferences") as mock_parse:
            result = _load_preferences(data, spec)
            mock_parse.assert_not_called()
        assert len(result) == 1
        assert result[0].platform == "web"

    def test_reparses_when_hash_differs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "CLI tool in Rust"
        spec.platform_entries = []
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": "stale_hash",
        }
        new_prefs = [
            BuildPreferences(
                platform="cli",
                language="Rust",
                constraints=[],
                preferences=[],
            )
        ]
        with (
            patch(
                "duplo.pipeline.parse_build_preferences",
                return_value=new_prefs,
            ) as mock_parse,
            patch("duplo.pipeline.save_build_preferences") as mock_save,
        ):
            result = _load_preferences(data, spec)
            mock_parse.assert_called_once_with("CLI tool in Rust", structured_entries=[])
            mock_save.assert_called_once()
        assert len(result) == 1
        assert result[0].platform == "cli"
        assert result[0].language == "Rust"

    def test_reparses_when_no_stored_hash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "Desktop app in Swift"
        spec.platform_entries = []
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
        }
        new_prefs = [
            BuildPreferences(
                platform="desktop",
                language="Swift",
                constraints=[],
                preferences=[],
            )
        ]
        with (
            patch(
                "duplo.pipeline.parse_build_preferences",
                return_value=new_prefs,
            ) as mock_parse,
            patch("duplo.pipeline.save_build_preferences"),
        ):
            result = _load_preferences(data, spec)
            mock_parse.assert_called_once()
        assert len(result) == 1
        assert result[0].platform == "desktop"

    def test_updates_in_memory_data(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "API in Go"
        spec.platform_entries = []
        data = {
            "preferences": {
                "platform": "web",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": "old",
        }
        new_prefs = [
            BuildPreferences(
                platform="api",
                language="Go",
                constraints=[],
                preferences=[],
            )
        ]
        with (
            patch(
                "duplo.pipeline.parse_build_preferences",
                return_value=new_prefs,
            ),
            patch("duplo.pipeline.save_build_preferences"),
        ):
            _load_preferences(data, spec)
        assert isinstance(data["preferences"], list)
        assert data["preferences"][0]["platform"] == "api"
        assert data["architecture_hash"] != "old"

    def test_structured_entries_passed_through(self, tmp_path, monkeypatch):
        """When spec.platform_entries is set, they flow to parse_build_preferences."""
        from duplo.spec_reader import PlatformEntry

        monkeypatch.chdir(tmp_path)
        spec = MagicMock()
        spec.architecture = "anything"
        spec.platform_entries = [PlatformEntry(platform="macos", language="swift", build="spm")]
        data: dict = {"preferences": [], "architecture_hash": "old"}
        returned = [
            BuildPreferences(
                platform="macos", language="swift", constraints=[], preferences=["build: spm"]
            )
        ]
        with (
            patch(
                "duplo.pipeline.parse_build_preferences",
                return_value=returned,
            ) as mock_parse,
            patch("duplo.pipeline.save_build_preferences"),
        ):
            result = _load_preferences(data, spec)
        _, kwargs = mock_parse.call_args
        assert kwargs["structured_entries"] == spec.platform_entries
        assert len(result) == 1
        assert result[0].platform == "macos"

    def test_reads_list_format_preferences(self):
        """Storage can be a JSON list; loader returns a matching list."""
        data = {
            "preferences": [
                {
                    "platform": "web",
                    "language": "TypeScript",
                    "constraints": [],
                    "preferences": [],
                },
                {
                    "platform": "linux",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
            ],
        }
        result = _load_preferences(data, None)
        assert len(result) == 2
        assert result[0].platform == "web"
        assert result[1].platform == "linux"


class TestResolvePlatformProfiles:
    """Tests for _resolve_platform_profiles and pipeline wiring."""

    def _profile(self, pid: str, display: str | None = None):
        from duplo.platforms.schema import PlatformProfile

        return PlatformProfile(id=pid, display_name=display or pid)

    def test_calls_resolver_once_per_preference(self):
        from duplo.pipeline import _resolve_platform_profiles

        prefs = [
            BuildPreferences(platform="macos", language="Swift"),
            BuildPreferences(platform="linux", language="Python"),
        ]
        a = self._profile("macos-swiftui-spm", "SwiftUI")
        b = self._profile("linux-python-cli", "Python CLI")

        with patch(
            "duplo.pipeline.resolve_profiles",
            side_effect=[[a], [b]],
        ) as mock_resolve:
            result = _resolve_platform_profiles(prefs)

        assert mock_resolve.call_count == 2
        assert [c.args[0] for c in mock_resolve.call_args_list] == prefs
        assert [p.id for p in result] == ["macos-swiftui-spm", "linux-python-cli"]

    def test_union_dedupes_by_id(self):
        from duplo.pipeline import _resolve_platform_profiles

        prefs = [
            BuildPreferences(platform="macos", language="Swift"),
            BuildPreferences(platform="macos", language="Swift"),
        ]
        shared = self._profile("macos-swiftui-spm", "SwiftUI")

        with patch("duplo.pipeline.resolve_profiles", return_value=[shared]):
            result = _resolve_platform_profiles(prefs)

        assert [p.id for p in result] == ["macos-swiftui-spm"]

    def test_empty_preferences_returns_empty(self):
        from duplo.pipeline import _resolve_platform_profiles

        with patch("duplo.pipeline.resolve_profiles") as mock_resolve:
            result = _resolve_platform_profiles([])

        assert result == []
        mock_resolve.assert_not_called()

    def test_no_matches_returns_empty(self):
        from duplo.pipeline import _resolve_platform_profiles

        prefs = [BuildPreferences(platform="unknown", language="unknown")]
        with patch("duplo.pipeline.resolve_profiles", return_value=[]):
            result = _resolve_platform_profiles(prefs)

        assert result == []

    def test_main_pipeline_threads_profiles_through(self, capsys, tmp_path, monkeypatch):
        """main() calls resolve_profiles and announces matched profiles."""
        from duplo.build_prefs import architecture_hash
        from duplo.spec_reader import PlatformEntry, ProductSpec

        data = {
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
            "preferences": [
                {
                    "platform": "macos",
                    "language": "Swift",
                    "constraints": [],
                    "preferences": ["build: spm"],
                },
            ],
        }
        entries = [PlatformEntry(platform="macos", language="Swift", build="spm")]
        arch_prose = "Native macOS app using SwiftUI."
        data["architecture_hash"] = architecture_hash(arch_prose, structured_entries=entries)

        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        spec = ProductSpec(
            purpose="stub",
            scope="stub",
            behavior_contracts=[],
            architecture=arch_prose,
            design=MagicMock(auto_generated=""),
            sources=[],
            platform_entries=entries,
        )

        profile = self._profile("macos-swiftui-spm", "macOS SwiftUI")
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(errors=[], warnings=[]),
            ),
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._analyze_new_files", return_value=UpdateSummary()),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.resolve_profiles", return_value=[profile]) as mock_resolve,
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        assert mock_resolve.call_count >= 1
        prefs_arg = mock_resolve.call_args_list[0].args[0]
        assert prefs_arg.platform == "macos"
        assert prefs_arg.language == "Swift"
        out = capsys.readouterr().out
        assert "Platform profiles: macOS SwiftUI" in out

    def test_main_pipeline_announces_when_no_profiles_match(self, capsys, tmp_path, monkeypatch):
        """Empty resolver output produces a 'no profiles' status message."""
        from duplo.build_prefs import architecture_hash
        from duplo.spec_reader import ProductSpec

        arch_prose = "Unknown stack, details TBD."
        data = {
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
            "preferences": [
                {
                    "platform": "unknown",
                    "language": "unknown",
                    "constraints": [],
                    "preferences": [],
                },
            ],
            "architecture_hash": architecture_hash(arch_prose),
        }
        _write_duplo_json(tmp_path, data)
        monkeypatch.chdir(tmp_path)

        spec = ProductSpec(
            purpose="stub",
            scope="stub",
            behavior_contracts=[],
            architecture=arch_prose,
            design=MagicMock(auto_generated=""),
            sources=[],
            platform_entries=[],
        )

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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(errors=[], warnings=[]),
            ),
            patch("duplo.pipeline._rescrape_product_url", return_value=(0, 0, "")),
            patch("duplo.pipeline._analyze_new_files", return_value=UpdateSummary()),
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.resolve_profiles", return_value=[]) as mock_resolve,
            patch("duplo.pipeline.generate_roadmap", return_value=fake_roadmap),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"),
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()

        assert mock_resolve.call_count >= 1
        out = capsys.readouterr().out
        assert "No platform profiles matched" in out


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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
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
        with patch("duplo.pipeline.scrapeable_sources", return_value=[]):
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
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert "b_text" in result.combined_text
        # Failed source does NOT produce a source_record.
        assert len(result.source_records) == 1
        assert result.source_records[0]["url"] == "https://b.com"

    def test_source_records_populated(self):
        """Each successfully scraped source produces a source_record."""
        src_a = self._make_source("https://a.com", scrape="deep")
        src_b = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_a, src_b])

        def fake_fetch(url, *, scrape_depth="deep"):
            return (f"text-{url}", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.source_records) == 2
        rec_a = result.source_records[0]
        assert rec_a["url"] == "https://a.com"
        assert rec_a["scrape_depth_used"] == "deep"
        assert "last_scraped" in rec_a
        assert "content_hash" in rec_a
        rec_b = result.source_records[1]
        assert rec_b["url"] == "https://b.com"
        assert rec_b["scrape_depth_used"] == "shallow"

    def test_source_record_content_hash(self):
        """content_hash is SHA-256 of the scraped text."""
        import hashlib

        src = self._make_source("https://a.com", scrape="deep")
        spec = self._make_spec([src])

        def fake_fetch(url, *, scrape_depth="deep"):
            return ("hello world", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src],
            ),
        ):
            result = _scrape_declared_sources(spec)

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert result.source_records[0]["content_hash"] == expected

    def test_empty_sources_no_source_records(self):
        """No scrapeable sources produces no source_records."""
        spec = self._make_spec([])
        with patch("duplo.pipeline.scrapeable_sources", return_value=[]):
            result = _scrape_declared_sources(spec)
        assert result.source_records == []


class TestPersistScrapeResult:
    """Tests for _persist_scrape_result saving to .duplo/."""

    def test_saves_examples(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = ScrapeResult(all_code_examples=["ex1"])
        with (
            patch("duplo.pipeline.save_examples") as mock_save,
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls") as mock_urls,
            patch("duplo.pipeline.save_raw_content") as mock_raw,
            patch("duplo.pipeline.save_doc_structures"),
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
            patch(
                "duplo.pipeline.append_sources",
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
            patch("duplo.pipeline.append_sources") as mock_append,
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
            patch("duplo.pipeline.append_sources", return_value=original),
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
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
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
        ):
            _persist_scrape_result(result)
        # Content unchanged — dedup prevented addition.
        assert spec_path.read_text(encoding="utf-8") == original

    def test_saves_source_records(self, tmp_path, monkeypatch):
        """source_records persisted via save_sources."""
        monkeypatch.chdir(tmp_path)
        records = [
            {
                "url": "https://example.com",
                "last_scraped": "2026-04-14T10:00:00+00:00",
                "content_hash": "abc",
                "scrape_depth_used": "deep",
            }
        ]
        result = ScrapeResult(source_records=records)
        with (
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
            patch("duplo.pipeline.save_sources") as mock_save,
        ):
            _persist_scrape_result(result)
        mock_save.assert_called_once_with(records)

    def test_skips_save_sources_when_empty(self, tmp_path, monkeypatch):
        """save_sources not called when source_records is empty."""
        monkeypatch.chdir(tmp_path)
        result = ScrapeResult()
        with (
            patch("duplo.pipeline.save_examples"),
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.save_doc_structures"),
            patch("duplo.pipeline.save_sources") as mock_save,
        ):
            _persist_scrape_result(result)
        mock_save.assert_not_called()


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
                "duplo.pipeline.extract_all_videos",
                return_value=[
                    ExtractionResult(
                        source=vid_a,
                        frames=[frame_a1, frame_a2],
                    ),
                    ExtractionResult(source=vid_b, frames=[frame_b1]),
                ],
            ),
            patch("duplo.pipeline.filter_frames", return_value=[]),
            patch(
                "duplo.pipeline.apply_filter",
                return_value=[frame_a1, frame_b1],
            ),
            patch("duplo.pipeline.describe_frames", return_value=[]),
            patch("duplo.pipeline.store_accepted_frames"),
        ):
            from duplo.pipeline import _run_video_frame_pipeline

            frames, lookup = _run_video_frame_pipeline([vid_a, vid_b])

        assert set(frames) == {frame_a1, frame_b1}
        # Per-source lookup: vid_a kept frame_a1 (not frame_a2),
        # vid_b kept frame_b1.
        assert lookup[vid_a] == [frame_a1]
        assert lookup[vid_b] == [frame_b1]

    def test_empty_videos_returns_empty_lookup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".duplo" / "video_frames").mkdir(parents=True, exist_ok=True)

        with patch("duplo.pipeline.extract_all_videos", return_value=[]):
            from duplo.pipeline import _run_video_frame_pipeline

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
                "duplo.pipeline.extract_all_videos",
                return_value=[
                    ExtractionResult(source=vid, frames=[frame1]),
                ],
            ),
            patch("duplo.pipeline.filter_frames", return_value=[]),
            patch(
                "duplo.pipeline.apply_filter",
                return_value=[],  # all rejected
            ),
        ):
            from duplo.pipeline import _run_video_frame_pipeline

            frames, lookup = _run_video_frame_pipeline([vid])

        assert frames == []
        # Source present in lookup with empty frame list.
        assert vid in lookup
        assert lookup[vid] == []


class TestAutogenBlockSkipsVision:
    """Check autogen block FIRST via the in-memory dataclass."""

    def test_autogen_design_block_present_skips_vision(self, tmp_path, monkeypatch):
        """When extract_design succeeds and writes autogen block to SPEC.md,
        a subsequent _analyze_new_files call skips Vision extraction because
        the autogen block is now present in-memory."""
        from duplo.spec_reader import DesignBlock, ProductSpec, ReferenceEntry

        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "ref" / "shot.png"
        img.parent.mkdir()
        img.write_bytes(b"PNG")

        # First call: no autogen, extraction writes the block.
        design = DesignRequirements(
            colors={"primary": "#abc"},
            source_images=["shot.png"],
        )
        spec_no_autogen = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated=""),
            references=[
                ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"]),
            ],
        )
        with (
            patch("duplo.pipeline.extract_design", return_value=design),
            patch("duplo.pipeline.collect_design_input", return_value=[img]),
        ):
            _analyze_new_files(["ref/shot.png"], spec=spec_no_autogen)

        # SPEC.md should now have an autogen block.
        spec_path = tmp_path / "SPEC.md"
        assert spec_path.exists()
        assert "AUTO-GENERATED" in spec_path.read_text(encoding="utf-8")

        # Second call: autogen present in-memory, Vision should be skipped.
        spec_with_autogen = ProductSpec(
            raw="test",
            design=DesignBlock(auto_generated="colors:\n  primary: #abc"),
        )
        with (
            patch("duplo.pipeline.extract_design") as mock_design2,
            patch("duplo.pipeline.collect_design_input", return_value=[img]),
        ):
            _analyze_new_files(["ref/shot.png"], spec=spec_with_autogen)

        mock_design2.assert_not_called()

    def test_writes_autogen_when_spec_absent(self, tmp_path, monkeypatch):
        """Design write-back creates SPEC.md when the file does not exist."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(tmp_path, {"source_url": "", "features": []})
        img = tmp_path / "ref" / "shot.png"
        img.parent.mkdir()
        img.write_bytes(b"PNG")

        design = DesignRequirements(
            colors={"bg": "#000"},
            source_images=["shot.png"],
        )
        with (
            patch("duplo.pipeline.extract_design", return_value=design),
        ):
            _analyze_new_files(["ref/shot.png"])

        spec_path = tmp_path / "SPEC.md"
        assert spec_path.exists(), "SPEC.md should be created when absent"
        content = spec_path.read_text(encoding="utf-8")
        assert "AUTO-GENERATED" in content
        assert "## Design" in content

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
            patch("duplo.pipeline.extract_design") as mock_design,
            patch(
                "duplo.pipeline.save_design_requirements",
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
                "duplo.pipeline.fetch_site",
                return_value=(
                    "text",
                    [],
                    None,
                    [PageRecord("https://a.com", "2024-01-01", "abc")],
                    {"https://a.com": raw_html},
                ),
            ),
            patch(
                "duplo.pipeline._download_site_media",
                return_value=([tmp_path / "img.png"], []),
            ),
            patch(
                "duplo.pipeline.collect_design_input",
                return_value=[
                    tmp_path / "img.png",
                ],
            ),
            patch("duplo.pipeline.extract_design") as mock_design,
            patch(
                "duplo.pipeline.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
        ):
            _rescrape_product_url(spec=spec)

        mock_design.assert_not_called()
        # Cache invariant: save_design_requirements also skipped
        mock_save_dr.assert_not_called()

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
                "duplo.pipeline.extract_design",
                return_value=DesignRequirements(),
            ) as mock_design,
            patch("duplo.pipeline.save_design_requirements"),
        ):
            _analyze_new_files(["ref/new_shot.png"], spec=spec)

        # Empty/whitespace autogen should NOT block Vision
        mock_design.assert_called_once()

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
            patch("duplo.pipeline.extract_design") as mock_design,
            patch(
                "duplo.pipeline.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.pipeline.collect_design_input", return_value=[img]),
            patch("duplo.pipeline.record_failure") as mock_rf,
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
                "duplo.pipeline.fetch_site",
                return_value=(
                    "text",
                    [],
                    None,
                    [PageRecord("https://a.com", "2024-01-01", "abc")],
                    {"https://a.com": raw_html},
                ),
            ),
            patch(
                "duplo.pipeline._download_site_media",
                return_value=([tmp_path / "img.png"], []),
            ),
            patch(
                "duplo.pipeline.collect_design_input",
                return_value=[
                    tmp_path / "img.png",
                ],
            ),
            patch("duplo.pipeline.extract_design") as mock_design,
            patch(
                "duplo.pipeline.save_design_requirements",
            ) as mock_save_dr,
            patch("duplo.pipeline.save_reference_urls"),
            patch("duplo.pipeline.save_raw_content"),
            patch("duplo.pipeline.record_failure") as mock_rf,
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
            patch("duplo.pipeline.extract_design") as mock_design,
            patch(
                "duplo.pipeline.save_design_requirements",
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.pipeline._persist_scrape_result",
            ),
            patch(
                "duplo.pipeline._download_site_media",
                return_value=(
                    [Path("img.png")],
                    [Path("vid.mp4")],
                ),
            ) as mock_dl,
            patch(
                "duplo.pipeline.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.pipeline.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.pipeline._persist_scrape_result",
            ),
            patch(
                "duplo.pipeline._download_site_media",
                return_value=([], [site_vid]),
            ),
            patch(
                "duplo.pipeline.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.pipeline._run_video_frame_pipeline",
                return_value=([], {}),
            ) as mock_pipeline,
            patch(
                "duplo.pipeline.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
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
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch(
                "duplo.pipeline._persist_scrape_result",
            ),
            patch(
                "duplo.pipeline.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.pipeline.collect_design_input",
                return_value=[Path("img.png")],
            ),
            patch(
                "duplo.pipeline.extract_design",
            ) as mock_extract,
            patch(
                "duplo.pipeline.save_design_requirements",
            ) as mock_save_dr,
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
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
            patch("duplo.pipeline.read_spec", return_value=None),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[],
            ),
            patch(
                "duplo.pipeline._rescrape_product_url",
                return_value=(0, 0, ""),
            ) as mock_rescrape,
            patch(
                "duplo.pipeline._download_site_media",
            ) as mock_dl,
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        mock_rescrape.assert_called_once()
        # _download_site_media is NOT called directly in
        # _subsequent_run for the non-spec path (it's called
        # inside _rescrape_product_url).
        mock_dl.assert_not_called()


class TestSpecSourceOfTruth:
    """The in-memory spec from read_spec() at the top of _subsequent_run
    is the source of truth for ALL decisions.  SPEC.md
    is re-read from disk ONLY to stage writes (step 6: discovered URLs,
    step 13: design autogen).  It is NEVER re-read to drive extraction
    or filtering decisions."""

    def test_subsequent_run_read_spec_called_once(self, tmp_path, monkeypatch):
        """read_spec is called exactly once in _subsequent_run — not
        re-read after _persist_scrape_result or design write-back."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://a.com",
                "features": [
                    {"name": "F1", "description": "d", "category": "c"},
                ],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {
                        "phase": 1,
                        "title": "Core",
                        "goal": "g",
                        "features": ["F1"],
                        "test_criteria": [],
                    }
                ],
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        spec = ProductSpec(
            raw="test",
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(combined_text="text")

        with (
            patch("duplo.pipeline.read_spec", return_value=spec) as mock_rs,
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[spec.sources[0]],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch("duplo.pipeline._persist_scrape_result"),
            patch("duplo.pipeline.extract_features", return_value=[]),
            patch("duplo.pipeline._download_site_media", return_value=([], [])),
            patch(
                "duplo.main.select_features",
                side_effect=lambda feats, **kw: feats,
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        assert mock_rs.call_count == 1

    def test_subsequent_run_scope_exclude_uses_in_memory_spec(self, tmp_path, monkeypatch):
        """_subsequent_run applies scope_exclude from in-memory spec,
        not from a re-read of SPEC.md on disk."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://a.com",
                "features": [],
                "preferences": {
                    "platform": "web",
                    "language": "Python",
                    "constraints": [],
                    "preferences": [],
                },
                "roadmap": [
                    {
                        "phase": 1,
                        "title": "Core",
                        "goal": "g",
                        "features": [],
                        "test_criteria": [],
                    }
                ],
            },
        )
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        # In-memory spec excludes "analytics"
        spec = ProductSpec(
            raw="test",
            scope_exclude=["analytics"],
            sources=[
                SourceEntry(
                    url="https://a.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
            design=DesignBlock(),
        )
        analytics_feature = Feature(
            name="Analytics",
            description="Track analytics",
            category="core",
        )
        scrape_result = ScrapeResult(combined_text="scraped")

        with (
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[spec.sources[0]],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch("duplo.pipeline._persist_scrape_result"),
            patch(
                "duplo.pipeline.extract_features",
                return_value=[analytics_feature],
            ),
            patch(
                "duplo.pipeline.save_features",
            ) as mock_save,
            patch("duplo.pipeline._download_site_media", return_value=([], [])),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        # save_features should NOT have been called because the only
        # extracted feature matched scope_exclude and was filtered out.
        mock_save.assert_not_called()

    def test_persist_scrape_result_reads_spec_only_for_write(self, tmp_path, monkeypatch):
        """_persist_scrape_result reads SPEC.md from disk ONLY to stage
        the discovered-URLs write-back (step 6).  It does NOT call
        read_spec() — it reads the raw file text."""
        monkeypatch.chdir(tmp_path)
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(
            "## Sources\n- https://a.com\n  role: product-reference\n  scrape: deep\n",
            encoding="utf-8",
        )

        result = ScrapeResult(
            combined_text="text",
            discovered_urls=["https://new-link.com"],
        )

        with patch("duplo.pipeline.read_spec") as mock_rs:
            _persist_scrape_result(result)

        # _persist_scrape_result must NOT call read_spec.
        mock_rs.assert_not_called()
        # But SPEC.md should have the discovered URL appended.
        content = spec_path.read_text(encoding="utf-8")
        assert "https://new-link.com" in content


# ------------------------------------------------------------------
# Integration tests per PIPELINE-design.md § "Test plan"
# ------------------------------------------------------------------


def _setup_subsequent_run(tmp_path, monkeypatch, *, features=None, with_plan=False):
    """Set up .duplo/ state for a _subsequent_run test.

    Creates duplo.json, file_hashes.json, and chdir into tmp_path.
    When *with_plan* is True, creates a PLAN.md with unchecked tasks
    so _subsequent_run hits State 2 (return early after scraping).
    """
    monkeypatch.chdir(tmp_path)
    _write_duplo_json(
        tmp_path,
        {
            "source_url": "https://example.com",
            "features": features or [{"name": "F1", "description": "d", "category": "c"}],
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
    if with_plan:
        (tmp_path / "PLAN.md").write_text("# Phase 1\n- [ ] Pending task\n", encoding="utf-8")


class TestIntegrationUrlOnlySpec:
    """URL-only spec produces correct PLAN.md without consulting ref/.

    Uses _subsequent_run (duplo.json exists) since the new spec-sources
    pipeline is fully wired there.
    """

    def test_url_only_spec_scrapes_sources(self, tmp_path, monkeypatch):
        """A SPEC.md with only ## Sources (URL entries) scrapes via
        _scrape_declared_sources without direct fetch_site calls.
        Uses State 2 (PLAN.md with unchecked tasks) to return early
        after the scraping pipeline runs."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)

        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            SourceEntry,
        )

        src = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="## Sources\n- https://example.com\n",
            purpose="A calculator app",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="Calculator features",
            product_ref_raw_pages={"https://example.com": "<html>calc</html>"},
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(
            m,
            "validate_for_run",
            lambda s: MagicMock(warnings=[], errors=[]),
        )
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        mock_scrape = MagicMock(return_value=scrape_result)
        monkeypatch.setattr(m, "_scrape_declared_sources", mock_scrape)
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])
        mock_fetch = MagicMock()
        monkeypatch.setattr(m, "fetch_site", mock_fetch)

        main()

        # _scrape_declared_sources was used (the spec-sources path).
        mock_scrape.assert_called_once()
        # fetch_site was NOT called directly (no scanner fallback).
        mock_fetch.assert_not_called()

    def test_url_only_scan_finds_nothing_in_ref(self, tmp_path, monkeypatch):
        """When ref/ is empty, scan_directory returns an empty
        ScanResult and no ref/-based analysis runs."""
        monkeypatch.chdir(tmp_path)
        ref = tmp_path / "ref"
        ref.mkdir()

        from duplo.scanner import scan_directory

        result = scan_directory(ref)
        assert result.images == []
        assert result.videos == []
        assert result.pdfs == []
        assert result.text_files == []


class TestIntegrationRefOnlySpec:
    """ref/-only spec produces correct PLAN.md without HTTP requests."""

    def test_ref_only_spec_no_http(self, tmp_path, monkeypatch):
        """A SPEC.md with only ## References (no ## Sources) does
        NOT call _scrape_declared_sources or fetch_site. Falls
        back to _rescrape_product_url which is also mocked."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)

        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
        )

        ref_entry = ReferenceEntry(
            path=Path("ref/screenshot.png"),
            roles=["visual-target"],
        )
        spec = ProductSpec(
            raw="## References\n- ref/screenshot.png\n",
            purpose="A calculator clone",
            references=[ref_entry],
            sources=[],
            design=DesignBlock(),
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(
            m,
            "validate_for_run",
            lambda s: MagicMock(warnings=[], errors=[]),
        )
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [])
        mock_scrape = MagicMock()
        monkeypatch.setattr(m, "_scrape_declared_sources", mock_scrape)
        mock_fetch = MagicMock()
        monkeypatch.setattr(m, "fetch_site", mock_fetch)
        monkeypatch.setattr(
            m,
            "_rescrape_product_url",
            lambda **kw: (0, 0, ""),
        )
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )

        main()

        mock_fetch.assert_not_called()
        mock_scrape.assert_not_called()


class TestIntegrationBothSourcesAndRefs:
    """Both URL sources and ref/ files contribute to the plan."""

    def test_both_contribute(self, tmp_path, monkeypatch):
        """When SPEC.md has both ## Sources and ## References,
        features are extracted from scraped text AND ref/ docs
        text, and both feed into feature extraction."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)

        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        src = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        ref_entry = ReferenceEntry(
            path=Path("ref/guide.txt"),
            roles=["docs"],
        )
        spec = ProductSpec(
            raw="## Sources\n## References\n",
            purpose="A tool",
            sources=[src],
            references=[ref_entry],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="Scraped product info",
            product_ref_raw_pages={},
        )

        extract_calls = []

        def fake_extract(text, **kwargs):
            extract_calls.append(text)
            return [Feature("F1", "feat", "Core")]

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(
            m,
            "validate_for_run",
            lambda s: MagicMock(warnings=[], errors=[]),
        )
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(
            m,
            "_scrape_declared_sources",
            lambda s: scrape_result,
        )
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [ref_entry])
        mock_docs = MagicMock(return_value="Guide doc text")
        monkeypatch.setattr(m, "docs_text_extractor", mock_docs)
        monkeypatch.setattr(m, "extract_features", fake_extract)
        monkeypatch.setattr(m, "save_features", lambda *a: None)
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )
        monkeypatch.setattr(
            m,
            "format_behavioral_references",
            lambda s: [],
        )
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        main()

        mock_docs.assert_called_once_with([ref_entry])
        assert len(extract_calls) == 1
        assert "Guide doc text" in extract_calls[0]
        assert "Scraped product info" in extract_calls[0]


class TestIntegrationProposedExcluded:
    """proposed: true entries in SPEC.md are excluded from all
    pipeline stages."""

    def test_proposed_refs_excluded_from_pipeline(self):
        """References with proposed: true are excluded from
        format_visual_references, format_behavioral_references,
        and format_doc_references."""
        from duplo.spec_reader import (
            ProductSpec,
            ReferenceEntry,
            format_behavioral_references,
            format_doc_references,
            format_visual_references,
        )

        active_ref = ReferenceEntry(
            path=Path("ref/active.png"),
            roles=["visual-target"],
            proposed=False,
        )
        proposed_ref = ReferenceEntry(
            path=Path("ref/proposed.png"),
            roles=["visual-target"],
            proposed=True,
        )
        spec = ProductSpec(
            references=[active_ref, proposed_ref],
        )

        visual = format_visual_references(spec)
        assert len(visual) == 1
        assert visual[0].path == Path("ref/active.png")

        # Behavioral
        active_beh = ReferenceEntry(
            path=Path("ref/demo.mp4"),
            roles=["behavioral-target"],
            proposed=False,
        )
        proposed_beh = ReferenceEntry(
            path=Path("ref/new_demo.mp4"),
            roles=["behavioral-target"],
            proposed=True,
        )
        spec_beh = ProductSpec(
            references=[active_beh, proposed_beh],
        )
        behavioral = format_behavioral_references(spec_beh)
        assert len(behavioral) == 1
        assert behavioral[0].path == Path("ref/demo.mp4")

        # Docs
        active_doc = ReferenceEntry(
            path=Path("ref/manual.pdf"),
            roles=["docs"],
            proposed=False,
        )
        proposed_doc = ReferenceEntry(
            path=Path("ref/new_manual.pdf"),
            roles=["docs"],
            proposed=True,
        )
        spec_doc = ProductSpec(
            references=[active_doc, proposed_doc],
        )
        docs = format_doc_references(spec_doc)
        assert len(docs) == 1
        assert docs[0].path == Path("ref/manual.pdf")

    def test_proposed_sources_excluded_from_scraping(self):
        """Sources with proposed: true are excluded from
        scrapeable_sources."""
        from duplo.spec_reader import (
            ProductSpec,
            SourceEntry,
            scrapeable_sources,
        )

        active_src = SourceEntry(
            url="https://active.com",
            role="product-reference",
            scrape="deep",
            proposed=False,
        )
        proposed_src = SourceEntry(
            url="https://proposed.com",
            role="product-reference",
            scrape="deep",
            proposed=True,
        )
        spec = ProductSpec(sources=[active_src, proposed_src])

        result = scrapeable_sources(spec)
        assert len(result) == 1
        assert result[0].url == "https://active.com"

    def test_proposed_refs_not_in_design_input(self, tmp_path, monkeypatch):
        """collect_design_input excludes proposed: true visual refs."""
        monkeypatch.chdir(tmp_path)
        ref = tmp_path / "ref"
        ref.mkdir()
        active_img = ref / "active.png"
        active_img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import (
            ProductSpec,
            ReferenceEntry,
        )

        active = ReferenceEntry(
            path=Path("ref/active.png"),
            roles=["visual-target"],
            proposed=False,
        )
        proposed = ReferenceEntry(
            path=Path("ref/proposed.png"),
            roles=["visual-target"],
            proposed=True,
        )
        spec = ProductSpec(references=[active, proposed])

        result = collect_design_input(spec)
        paths = [p.name for p in result]
        assert "active.png" in paths
        assert "proposed.png" not in paths

    def test_subsequent_run_proposed_refs_not_processed(self, tmp_path, monkeypatch):
        """In _subsequent_run, proposed: true refs are not
        processed by the pipeline."""
        monkeypatch.chdir(tmp_path)
        _write_duplo_json(
            tmp_path,
            {
                "source_url": "https://example.com",
                "features": [
                    {
                        "name": "F1",
                        "description": "d",
                        "category": "c",
                    },
                ],
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
        (tmp_path / "PLAN.md").write_text("# Phase 1\n- [ ] Do something\n", encoding="utf-8")

        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        proposed_ref = ReferenceEntry(
            path=Path("ref/new_screenshot.png"),
            roles=["visual-target"],
            proposed=True,
        )
        src = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="test",
            sources=[src],
            references=[proposed_ref],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="text",
            product_ref_raw_pages={},
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(
            m,
            "validate_for_run",
            lambda s: MagicMock(warnings=[], errors=[]),
        )
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(
            m,
            "_scrape_declared_sources",
            lambda s: scrape_result,
        )
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        mock_di = MagicMock(return_value=[])
        monkeypatch.setattr(m, "collect_design_input", mock_di)
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )

        main()

        mock_di.assert_called_once()


class TestIntegrationProposedRemoved:
    """After user removes proposed: true, next run includes
    the files."""

    def test_refs_included_after_proposed_removed(self):
        """When proposed: true is removed from a ReferenceEntry,
        it appears in the formatter results."""
        from duplo.spec_reader import (
            ProductSpec,
            ReferenceEntry,
            format_visual_references,
        )

        proposed = ReferenceEntry(
            path=Path("ref/screenshot.png"),
            roles=["visual-target"],
            proposed=True,
        )
        spec = ProductSpec(references=[proposed])
        assert format_visual_references(spec) == []

        accepted = ReferenceEntry(
            path=Path("ref/screenshot.png"),
            roles=["visual-target"],
            proposed=False,
        )
        spec_accepted = ProductSpec(references=[accepted])
        result = format_visual_references(spec_accepted)
        assert len(result) == 1
        assert result[0].path == Path("ref/screenshot.png")

    def test_sources_included_after_proposed_removed(self):
        """When proposed: true is removed from a SourceEntry,
        it appears in scrapeable_sources."""
        from duplo.spec_reader import (
            ProductSpec,
            SourceEntry,
            scrapeable_sources,
        )

        proposed = SourceEntry(
            url="https://newsite.com",
            role="docs",
            scrape="deep",
            proposed=True,
        )
        spec = ProductSpec(sources=[proposed])
        assert scrapeable_sources(spec) == []

        accepted = SourceEntry(
            url="https://newsite.com",
            role="docs",
            scrape="deep",
            proposed=False,
        )
        spec_accepted = ProductSpec(sources=[accepted])
        result = scrapeable_sources(spec_accepted)
        assert len(result) == 1
        assert result[0].url == "https://newsite.com"

    def test_accepted_ref_included_in_design_input(self, tmp_path, monkeypatch):
        """After removing proposed: true from a visual-target
        ref, collect_design_input includes it."""
        monkeypatch.chdir(tmp_path)
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "accepted.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        from duplo.orchestrator import collect_design_input
        from duplo.spec_reader import (
            ProductSpec,
            ReferenceEntry,
        )

        accepted_ref = ReferenceEntry(
            path=Path("ref/accepted.png"),
            roles=["visual-target"],
            proposed=False,
        )
        spec = ProductSpec(references=[accepted_ref])

        result = collect_design_input(spec)
        paths = [p.name for p in result]
        assert "accepted.png" in paths


class TestSourceUrlFromSpec:
    """Tests for _source_url_from_spec helper."""

    def test_returns_first_product_reference(self):
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src1 = SourceEntry(url="https://first.com", role="product-reference", scrape="deep")
        src2 = SourceEntry(url="https://second.com", role="product-reference", scrape="deep")
        spec = ProductSpec(raw="", sources=[src1, src2], design=DesignBlock())
        assert _source_url_from_spec(spec) == "https://first.com"

    def test_skips_proposed(self):
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(
            url="https://proposed.com",
            role="product-reference",
            scrape="deep",
            proposed=True,
        )
        spec = ProductSpec(raw="", sources=[src], design=DesignBlock())
        assert _source_url_from_spec(spec) == ""

    def test_skips_discovered(self):
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(
            url="https://discovered.com",
            role="product-reference",
            scrape="deep",
            discovered=True,
        )
        spec = ProductSpec(raw="", sources=[src], design=DesignBlock())
        assert _source_url_from_spec(spec) == ""

    def test_skips_non_product_reference(self):
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(url="https://docs.com", role="docs", scrape="deep")
        spec = ProductSpec(raw="", sources=[src], design=DesignBlock())
        assert _source_url_from_spec(spec) == ""

    def test_none_spec(self):
        assert _source_url_from_spec(None) == ""

    def test_empty_sources(self):
        from duplo.spec_reader import DesignBlock, ProductSpec

        spec = ProductSpec(raw="", sources=[], design=DesignBlock())
        assert _source_url_from_spec(spec) == ""

    def test_first_product_ref_after_docs(self):
        """product-reference is returned even when preceded by docs entries."""
        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        docs = SourceEntry(url="https://docs.com", role="docs", scrape="deep")
        prod = SourceEntry(url="https://prod.com", role="product-reference", scrape="deep")
        spec = ProductSpec(raw="", sources=[docs, prod], design=DesignBlock())
        assert _source_url_from_spec(spec) == "https://prod.com"


class TestProductJsonBackwardCompat:
    """product.json source_url stays in sync with spec's product-reference."""

    def test_subsequent_run_syncs_product_json(self, tmp_path, monkeypatch):
        """_subsequent_run updates product.json source_url from spec."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)
        duplo_dir = tmp_path / ".duplo"

        # Product.json has old URL.
        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "Prod", "source_url": "https://old.com"}),
            encoding="utf-8",
        )

        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(url="https://new-spec.com", role="product-reference", scrape="deep")
        spec = ProductSpec(
            raw="## Sources\n- https://new-spec.com\n",
            purpose="A tool",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="features",
            product_ref_raw_pages={},
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(m, "validate_for_run", lambda s: MagicMock(warnings=[], errors=[]))
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(m, "_scrape_declared_sources", MagicMock(return_value=scrape_result))
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m, "diff_hashes", lambda *a: MagicMock(added=[], changed=[], removed=[])
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        main()

        pdata = json.loads((duplo_dir / "product.json").read_text(encoding="utf-8"))
        assert pdata["source_url"] == "https://new-spec.com"

    def test_subsequent_run_source_url_from_spec_for_roadmap(self, tmp_path, monkeypatch):
        """_subsequent_run uses spec's source_url (not duplo.json) when
        generating a roadmap."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=False)
        duplo_dir = tmp_path / ".duplo"

        # duplo.json has old URL, spec has new URL.
        data = json.loads((duplo_dir / "duplo.json").read_text(encoding="utf-8"))
        data["source_url"] = "https://old-duplo.com"
        (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")

        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(url="https://spec-url.com", role="product-reference", scrape="deep")
        spec = ProductSpec(
            raw="## Sources\n- https://spec-url.com\n",
            purpose="A tool",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(combined_text="features")

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(m, "validate_for_run", lambda s: MagicMock(warnings=[], errors=[]))
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(m, "_scrape_declared_sources", MagicMock(return_value=scrape_result))
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m, "diff_hashes", lambda *a: MagicMock(added=[], changed=[], removed=[])
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        # Capture the source_url passed to generate_roadmap.
        captured = {}

        def fake_generate(url, *a, **kw):
            captured["url"] = url
            return [{"phase": 1, "title": "Core", "goal": "g", "features": ["F1"]}]

        monkeypatch.setattr(m, "generate_roadmap", fake_generate)
        monkeypatch.setattr(m, "save_roadmap", lambda *a, **kw: None)
        monkeypatch.setattr(m, "format_roadmap", lambda r: "roadmap")
        monkeypatch.setattr(m, "get_current_phase", lambda: (1, None))
        monkeypatch.setattr(m, "_print_feature_status", lambda d: None)

        main()

        assert captured.get("url") == "https://spec-url.com"

    def test_no_spec_falls_back_to_duplo_json(self, tmp_path, monkeypatch):
        """Without a spec, source_url falls back to duplo.json."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=False)
        duplo_dir = tmp_path / ".duplo"

        data = json.loads((duplo_dir / "duplo.json").read_text(encoding="utf-8"))
        data["source_url"] = "https://legacy.com"
        (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: None)
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [])
        monkeypatch.setattr(m, "_rescrape_product_url", lambda **kw: (0, 0, ""))
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m, "diff_hashes", lambda *a: MagicMock(added=[], changed=[], removed=[])
        )

        captured = {}

        def fake_generate(url, *a, **kw):
            captured["url"] = url
            return [{"phase": 1, "title": "Core", "goal": "g", "features": ["F1"]}]

        monkeypatch.setattr(m, "generate_roadmap", fake_generate)
        monkeypatch.setattr(m, "save_roadmap", lambda *a, **kw: None)
        monkeypatch.setattr(m, "format_roadmap", lambda r: "roadmap")
        monkeypatch.setattr(m, "get_current_phase", lambda: (1, None))
        monkeypatch.setattr(m, "_print_feature_status", lambda d: None)

        main()

        assert captured.get("url") == "https://legacy.com"


class TestRemovedSourceIdempotent:
    """When a URL is removed from ## Sources, its entry stays in
    duplo.json but the pipeline doesn't re-scrape or use cached
    content from that source."""

    def _make_source(self, url, role="product-reference", scrape="deep"):
        from duplo.spec_reader import SourceEntry

        return SourceEntry(url=url, role=role, scrape=scrape)

    def _make_spec(self, sources):
        from duplo.spec_reader import ProductSpec

        return ProductSpec(sources=sources)

    def test_removed_source_not_rescraped(self):
        """A URL removed from ## Sources is not passed to fetch_site."""
        src_a = self._make_source("https://a.com")
        # Source B was previously scraped but is now removed from spec.
        spec = self._make_spec([src_a])

        calls = []

        def fake_fetch(url, *, scrape_depth="deep"):
            calls.append(url)
            return ("text_a", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a],
            ),
        ):
            _scrape_declared_sources(spec)

        assert calls == ["https://a.com"]

    def test_removed_source_cached_text_not_in_result(self):
        """Scraped text from a removed source is not in the result."""
        src_a = self._make_source("https://a.com")
        spec = self._make_spec([src_a])

        def fake_fetch(url, *, scrape_depth="deep"):
            return ("text from A only", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert "text from A only" in result.combined_text
        assert "text from B" not in result.combined_text

    def test_save_sources_preserves_removed_entry(self, tmp_path):
        """save_sources merges: removed URL entry stays in duplo.json."""
        from duplo.saver import load_sources, save_sources

        # First scrape: A and B both present.
        first_run = [
            {
                "url": "https://a.com",
                "last_scraped": "2026-04-14T10:00:00+00:00",
                "content_hash": "hash_a",
                "scrape_depth_used": "deep",
            },
            {
                "url": "https://b.com",
                "last_scraped": "2026-04-14T10:01:00+00:00",
                "content_hash": "hash_b",
                "scrape_depth_used": "shallow",
            },
        ]
        save_sources(first_run, target_dir=tmp_path)

        # Second scrape: only A in spec, B removed.
        second_run = [
            {
                "url": "https://a.com",
                "last_scraped": "2026-04-14T11:00:00+00:00",
                "content_hash": "hash_a_v2",
                "scrape_depth_used": "deep",
            },
        ]
        save_sources(second_run, target_dir=tmp_path)

        sources = load_sources(target_dir=tmp_path)
        by_url = {s["url"]: s for s in sources}

        # A was updated.
        assert by_url["https://a.com"]["content_hash"] == "hash_a_v2"
        assert by_url["https://a.com"]["last_scraped"] == "2026-04-14T11:00:00+00:00"
        # B was preserved with original metadata.
        assert "https://b.com" in by_url
        assert by_url["https://b.com"]["content_hash"] == "hash_b"
        assert by_url["https://b.com"]["last_scraped"] == "2026-04-14T10:01:00+00:00"

    def test_removed_source_no_source_record_emitted(self):
        """source_records only contains actually-scraped sources."""
        src_a = self._make_source("https://a.com")
        spec = self._make_spec([src_a])

        def fake_fetch(url, *, scrape_depth="deep"):
            return ("text", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a],
            ),
        ):
            result = _scrape_declared_sources(spec)

        urls = [r["url"] for r in result.source_records]
        assert urls == ["https://a.com"]


class TestSourcesFieldPopulated:
    """source_records from _scrape_declared_sources contain all required
    fields; product.json source_url populated from first product-reference."""

    def _make_source(self, url, role="product-reference", scrape="deep"):
        from duplo.spec_reader import SourceEntry

        return SourceEntry(url=url, role=role, scrape=scrape)

    def _make_spec(self, sources):
        from duplo.spec_reader import ProductSpec

        return ProductSpec(sources=sources)

    def test_source_records_have_all_fields(self):
        """Each source_record has url, last_scraped, content_hash,
        and scrape_depth_used."""
        src = self._make_source("https://example.com", scrape="deep")
        spec = self._make_spec([src])

        def fake_fetch(url, *, scrape_depth="deep"):
            return ("page text", [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch("duplo.pipeline.scrapeable_sources", return_value=[src]),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.source_records) == 1
        rec = result.source_records[0]
        assert rec["url"] == "https://example.com"
        assert "last_scraped" in rec
        assert isinstance(rec["last_scraped"], str)
        assert len(rec["last_scraped"]) > 0
        assert "content_hash" in rec
        assert isinstance(rec["content_hash"], str)
        assert len(rec["content_hash"]) == 64  # SHA-256 hex
        assert rec["scrape_depth_used"] == "deep"

    def test_content_hash_is_sha256_of_scraped_text(self):
        """content_hash is the SHA-256 of the scraped text."""
        import hashlib

        src = self._make_source("https://example.com")
        spec = self._make_spec([src])
        scraped = "hello world"

        def fake_fetch(url, *, scrape_depth="deep"):
            return (scraped, [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch("duplo.pipeline.scrapeable_sources", return_value=[src]),
        ):
            result = _scrape_declared_sources(spec)

        expected = hashlib.sha256(scraped.encode("utf-8")).hexdigest()
        assert result.source_records[0]["content_hash"] == expected

    def test_multiple_sources_produce_independent_records(self):
        """Each source gets its own source_record with independent metadata."""
        src_a = self._make_source("https://a.com", scrape="deep")
        src_b = self._make_source("https://b.com", role="docs", scrape="shallow")
        spec = self._make_spec([src_a, src_b])

        def fake_fetch(url, *, scrape_depth="deep"):
            text = f"text from {url}"
            return (text, [], None, [], {})

        with (
            patch("duplo.pipeline.fetch_site", side_effect=fake_fetch),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src_a, src_b],
            ),
        ):
            result = _scrape_declared_sources(spec)

        assert len(result.source_records) == 2
        by_url = {r["url"]: r for r in result.source_records}
        assert by_url["https://a.com"]["scrape_depth_used"] == "deep"
        assert by_url["https://b.com"]["scrape_depth_used"] == "shallow"
        # Each has its own content_hash.
        assert by_url["https://a.com"]["content_hash"] != by_url["https://b.com"]["content_hash"]

    def test_product_json_source_url_from_first_product_reference(self, tmp_path, monkeypatch):
        """product.json source_url is populated from the first
        product-reference entry in ## Sources."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)
        duplo_dir = tmp_path / ".duplo"

        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "App"}),
            encoding="utf-8",
        )

        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        docs = SourceEntry(url="https://docs.example.com", role="docs", scrape="deep")
        prod = SourceEntry(
            url="https://product.example.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="## Sources\n- https://product.example.com\n",
            purpose="A tool",
            sources=[docs, prod],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="features",
            product_ref_raw_pages={},
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(m, "validate_for_run", lambda s: MagicMock(warnings=[], errors=[]))
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [docs, prod])
        monkeypatch.setattr(m, "_scrape_declared_sources", MagicMock(return_value=scrape_result))
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        main()

        pdata = json.loads((duplo_dir / "product.json").read_text(encoding="utf-8"))
        # First product-reference is "https://product.example.com",
        # not the docs entry.
        assert pdata["source_url"] == "https://product.example.com"

    def test_save_sources_idempotent_across_runs(self, tmp_path):
        """Calling save_sources with same records twice yields identical
        duplo.json sources entries."""
        from duplo.saver import load_sources, save_sources

        records = [
            {
                "url": "https://a.com",
                "last_scraped": "2026-04-14T10:00:00+00:00",
                "content_hash": "hash_a",
                "scrape_depth_used": "deep",
            },
            {
                "url": "https://b.com",
                "last_scraped": "2026-04-14T10:05:00+00:00",
                "content_hash": "hash_b",
                "scrape_depth_used": "shallow",
            },
        ]
        save_sources(records, target_dir=tmp_path)
        first = load_sources(target_dir=tmp_path)
        save_sources(records, target_dir=tmp_path)
        second = load_sources(target_dir=tmp_path)
        assert first == second


class TestSubsequentRunCounterExampleExcluded:
    """_subsequent_run excludes counter-example refs from design input
    and feature extraction."""

    def test_design_input_excludes_counter_example(self, tmp_path, monkeypatch):
        """extract_design receives the visual-target path but NOT the
        counter-example path.  extract_features input does NOT contain
        counter-example content."""
        from duplo.spec_reader import (
            DesignBlock,
            ProductSpec,
            ReferenceEntry,
            SourceEntry,
        )

        monkeypatch.chdir(tmp_path)

        # Create ref/ directory with both image types.
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        vt_img = ref_dir / "good_design.png"
        vt_img.write_bytes(b"VISUAL_TARGET_BYTES")
        ce_img = ref_dir / "bad_design.png"
        ce_img.write_bytes(b"COUNTER_EXAMPLE_BYTES")

        # duplo.json and file_hashes.json for subsequent-run detection.
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

        src = SourceEntry(
            url="https://prod.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="test spec",
            sources=[src],
            design=DesignBlock(),
            references=[
                ReferenceEntry(
                    path=Path("ref/good_design.png"),
                    roles=["visual-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/bad_design.png"),
                    roles=["counter-example"],
                ),
            ],
        )
        scrape_result = ScrapeResult(
            combined_text="product features text",
            product_ref_raw_pages={},
        )

        captured_design_input: list[Path] = []
        captured_features_text: list[str] = []

        def fake_extract_design(images):
            captured_design_input.extend(images)
            return DesignRequirements()

        def fake_extract_features(text, **kwargs):
            captured_features_text.append(text)
            return []

        with (
            patch("duplo.pipeline.read_spec", return_value=spec),
            patch(
                "duplo.pipeline.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.pipeline.scrapeable_sources",
                return_value=[src],
            ),
            patch(
                "duplo.pipeline._scrape_declared_sources",
                return_value=scrape_result,
            ),
            patch("duplo.pipeline._persist_scrape_result"),
            patch(
                "duplo.pipeline.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.pipeline.extract_design",
                side_effect=fake_extract_design,
            ),
            patch(
                "duplo.pipeline.extract_features",
                side_effect=fake_extract_features,
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value="# Phase\n",
            ),
            patch(
                "duplo.pipeline.save_plan",
                return_value=tmp_path / "PLAN.md",
            ),
        ):
            main()

        # Design input should contain the visual-target image.
        design_names = [p.name for p in captured_design_input]
        assert "good_design.png" in design_names
        # Design input must NOT contain the counter-example image.
        assert "bad_design.png" not in design_names

        # Feature extraction text must not contain counter-example
        # file content (counter-example is an image, so it wouldn't
        # contribute text; and counter-example sources are excluded
        # from scraping by scrapeable_sources).
        combined = " ".join(captured_features_text)
        assert "COUNTER_EXAMPLE" not in combined


class TestSubsequentRunProductNameSync:
    """product.json:product_name matches the plan heading after _subsequent_run."""

    def test_product_name_matches_plan_heading(self, tmp_path, monkeypatch):
        """After _subsequent_run generates a plan, product.json:product_name
        matches the project_name passed to generate_phase_plan (which becomes
        the app name in the PLAN.md H1 heading)."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=False)
        duplo_dir = tmp_path / ".duplo"

        # Add roadmap so _subsequent_run hits State 3 (generate plan) directly.
        data = json.loads((duplo_dir / "duplo.json").read_text(encoding="utf-8"))
        data["roadmap"] = [
            {"phase": 0, "title": "Scaffold", "goal": "g", "features": [], "test": "ok"},
        ]
        data["current_phase"] = 0
        (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")

        # Simulate a first run that set app_name but left product_name empty
        # (the bug scenario fixed in 5.37.3).
        (duplo_dir / "product.json").write_text(
            json.dumps(
                {
                    "product_name": "",
                    "source_url": "https://numi.app",
                    "app_name": "Numi",
                }
            ),
            encoding="utf-8",
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: None)
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [])
        monkeypatch.setattr(m, "_rescrape_product_url", lambda **kw: (0, 0, ""))
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m, "diff_hashes", lambda *a: MagicMock(added=[], changed=[], removed=[])
        )
        monkeypatch.setattr(m, "select_features", lambda feats, **kw: feats)
        monkeypatch.setattr(m, "load_frame_descriptions", lambda: [])

        # Capture what project_name is passed to generate_phase_plan.
        captured = {}

        def fake_generate(*args, **kwargs):
            captured["project_name"] = kwargs.get("project_name", "")
            return "# Numi — Phase 1: Scaffold\n- [ ] task"

        monkeypatch.setattr(m, "generate_phase_plan", fake_generate)
        monkeypatch.setattr(m, "save_plan", lambda c: tmp_path / "PLAN.md")
        monkeypatch.setattr(m, "notify_phase_complete", lambda *a, **kw: None)

        main()

        # product.json:product_name must match the project_name used for the heading.
        pdata = json.loads((duplo_dir / "product.json").read_text(encoding="utf-8"))
        assert captured["project_name"] == pdata["product_name"]
        assert pdata["product_name"] == "Numi"

    def test_user_edited_product_name_survives_subsequent_run(self, tmp_path, monkeypatch):
        """A user-edited product_name in product.json is not overwritten
        by _subsequent_run."""
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)
        duplo_dir = tmp_path / ".duplo"

        # Simulate user editing product.json with a custom name.
        (duplo_dir / "product.json").write_text(
            json.dumps(
                {
                    "product_name": "My Custom Calculator",
                    "source_url": "https://numi.app",
                    "app_name": "My Custom Calculator",
                }
            ),
            encoding="utf-8",
        )

        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(url="https://numi.app", role="product-reference", scrape="deep")
        spec = ProductSpec(
            raw="## Sources\n- https://numi.app\n",
            purpose="A calculator",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="features",
            product_ref_raw_pages={},
        )

        import duplo.pipeline as m

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(m, "validate_for_run", lambda s: MagicMock(warnings=[], errors=[]))
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(m, "_scrape_declared_sources", MagicMock(return_value=scrape_result))
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m, "diff_hashes", lambda *a: MagicMock(added=[], changed=[], removed=[])
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        main()

        # User-edited product_name must survive.
        pdata = json.loads((duplo_dir / "product.json").read_text(encoding="utf-8"))
        assert pdata["product_name"] == "My Custom Calculator"
        assert pdata["app_name"] == "My Custom Calculator"


class TestNoAskPreferencesInPipeline:
    """Phase 7.3.5: after removing ``_first_run``, the pipeline must no
    longer touch ``questioner.ask_preferences``. Two checks:

    1. ``ask_preferences`` is not imported into ``duplo.main`` or
       ``duplo.orchestrator`` module namespaces.
    2. A full ``main()`` run against a valid SPEC.md does not invoke
       ``questioner.ask_preferences``.
    """

    def test_main_module_has_no_ask_preferences(self):
        import duplo.main

        assert not hasattr(duplo.main, "ask_preferences")

    def test_orchestrator_module_has_no_ask_preferences(self):
        import duplo.orchestrator

        assert not hasattr(duplo.orchestrator, "ask_preferences")

    @pytest.mark.skip(
        reason="Phase 7.4.4: questioner.ask_preferences removed as dead code. "
        "The invariant is still pinned by test_main_module_has_no_ask_preferences "
        "and test_orchestrator_module_has_no_ask_preferences above."
    )
    def test_pipeline_does_not_call_ask_preferences(self, tmp_path, monkeypatch):
        """Running ``main()`` with a valid SPEC.md never reaches
        ``questioner.ask_preferences``. BuildPreferences come from
        ``spec.architecture`` via ``build_prefs.parse_build_preferences``.
        """
        _setup_subsequent_run(tmp_path, monkeypatch, with_plan=True)

        from duplo.spec_reader import DesignBlock, ProductSpec, SourceEntry

        src = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        spec = ProductSpec(
            raw="## Purpose\nA thing.\n## Architecture\nWeb.\n## Sources\n- https://example.com\n",
            purpose="A valid product purpose statement.",
            architecture="Web app using React.",
            sources=[src],
            design=DesignBlock(),
        )
        scrape_result = ScrapeResult(
            combined_text="features",
            product_ref_raw_pages={},
        )

        import duplo.pipeline as m
        import duplo.questioner as q

        monkeypatch.setattr(m, "read_spec", lambda: spec)
        monkeypatch.setattr(
            m,
            "validate_for_run",
            lambda s: MagicMock(warnings=[], errors=[]),
        )
        monkeypatch.setattr(m, "scrapeable_sources", lambda s: [src])
        monkeypatch.setattr(
            m,
            "_scrape_declared_sources",
            MagicMock(return_value=scrape_result),
        )
        monkeypatch.setattr(m, "_persist_scrape_result", lambda r: None)
        monkeypatch.setattr(m, "format_doc_references", lambda s: [])
        monkeypatch.setattr(m, "extract_features", lambda *a, **kw: [])
        monkeypatch.setattr(m, "compute_hashes", lambda *a: {})
        monkeypatch.setattr(m, "save_hashes", lambda *a: None)
        monkeypatch.setattr(m, "load_hashes", lambda *a: {})
        monkeypatch.setattr(
            m,
            "diff_hashes",
            lambda *a: MagicMock(added=[], changed=[], removed=[]),
        )
        monkeypatch.setattr(m, "_download_site_media", lambda rp: ([], []))
        monkeypatch.setattr(m, "format_behavioral_references", lambda s: [])
        monkeypatch.setattr(m, "collect_design_input", lambda *a, **kw: [])

        ap_mock = MagicMock(
            side_effect=AssertionError(
                "ask_preferences must not be called; prefs come from SPEC.md"
            )
        )
        monkeypatch.setattr(q, "ask_preferences", ap_mock)

        main()

        ap_mock.assert_not_called()


class TestNoInitializerImportsInPipeline:
    """Phase 7.5.5: after _first_run was deleted in 7.2.1, no production
    module on the ``duplo`` pipeline should import ``create_project_dir``
    or ``project_name_from_url`` from ``duplo.initializer``. The user now
    creates their own project directory before running ``duplo init``,
    and ``derive_app_name`` in ``duplo.saver`` resolves app names without
    hostname-derived naming.

    The functions still exist in ``duplo/initializer.py`` (retained per
    the project's no-delete rule), but this test pins the invariant that
    they are not wired into any live pipeline module.
    """

    def test_main_module_has_no_initializer_symbols(self):
        import duplo.main

        assert not hasattr(duplo.main, "create_project_dir")
        assert not hasattr(duplo.main, "project_name_from_url")

    def test_init_module_has_no_initializer_symbols(self):
        import duplo.init

        assert not hasattr(duplo.init, "create_project_dir")
        assert not hasattr(duplo.init, "project_name_from_url")

    def test_orchestrator_module_has_no_initializer_symbols(self):
        import duplo.orchestrator

        assert not hasattr(duplo.orchestrator, "create_project_dir")
        assert not hasattr(duplo.orchestrator, "project_name_from_url")

    def test_saver_module_has_no_initializer_symbols(self):
        import duplo.saver

        assert not hasattr(duplo.saver, "create_project_dir")
        assert not hasattr(duplo.saver, "project_name_from_url")


class TestReadLocalMd:
    """Unit tests for _read_local_md helper."""

    def test_returns_content_when_file_exists(self, tmp_path):
        from duplo.pipeline import _read_local_md

        (tmp_path / "local.md").write_text("project override\n", encoding="utf-8")
        assert _read_local_md(tmp_path) == "project override\n"

    def test_returns_empty_when_file_absent(self, tmp_path):
        from duplo.pipeline import _read_local_md

        assert _read_local_md(tmp_path) == ""


class TestLocalMdWiring:
    """Phase 8.8: local.md flows into planner prompt and CLAUDE.md."""

    _BASE_DATA = {
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
    }

    def _run_main(self, tmp_path):
        from duplo.platforms.schema import PlatformProfile

        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        profile = PlatformProfile(id="web-python", display_name="Web Python")
        with (
            patch("duplo.pipeline._detect_and_append_gaps", return_value=(0, 0, 0, 0)),
            patch("duplo.pipeline.resolve_profiles", return_value=[profile]),
            patch("duplo.pipeline.write_scaffold", return_value=[]),
            patch("duplo.pipeline.format_scaffold_notice", return_value=""),
            patch("duplo.main.select_features", side_effect=lambda f, **kw: f),
            patch("duplo.main.select_issues", return_value=[]),
            patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n") as mock_plan,
            patch(
                "duplo.pipeline.write_claude_md",
                return_value=tmp_path / "CLAUDE.md",
            ) as mock_claude,
            patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"),
        ):
            main()
        return mock_plan, mock_claude

    def test_local_overrides_present_when_local_md_exists(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        (tmp_path / "local.md").write_text(
            "Prefer tabs over spaces in this project.\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        mock_plan, mock_claude = self._run_main(tmp_path)

        addendum = mock_plan.call_args.kwargs["platform_addendum"]
        assert "Local overrides" in addendum
        assert "Prefer tabs over spaces" in addendum

        assert (
            mock_claude.call_args.kwargs["local_md_content"]
            == "Prefer tabs over spaces in this project.\n"
        )

    def test_local_overrides_absent_when_local_md_missing(self, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        mock_plan, mock_claude = self._run_main(tmp_path)

        addendum = mock_plan.call_args.kwargs["platform_addendum"]
        assert "Local overrides" not in addendum

        assert mock_claude.call_args.kwargs["local_md_content"] == ""


class TestInitializerLocalMdGitignore:
    """Phase 8.8: create_project_dir ignores local.md."""

    def test_gitignore_includes_local_md(self, tmp_path):
        from duplo.initializer import create_project_dir

        target = tmp_path / "proj"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            create_project_dir(target)
        gitignore = (target / ".gitignore").read_text(encoding="utf-8")
        assert "local.md" in gitignore.splitlines()
