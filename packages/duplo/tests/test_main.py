"""Tests for duplo.main CLI argument parsing and dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.main import main

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


class TestMainFirstRun:
    """First run: no .duplo/duplo.json exists."""

    def test_exits_when_no_reference_materials(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        assert "duplo init" in capsys.readouterr().out


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
            with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                with patch(
                    "duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"
                ) as mock_save:
                    main()

        # One call for the top-level project header block, plus one call
        # for the generated phase plan.
        assert mock_save.call_count == 2

    def test_prints_plan_ready(self, capsys, tmp_path, monkeypatch):
        _write_duplo_json(tmp_path, self._BASE_DATA)
        monkeypatch.chdir(tmp_path)

        with patch("duplo.main.select_features", side_effect=lambda f, **kw: f):
            with patch("duplo.pipeline.generate_phase_plan", return_value="# Phase 0\n"):
                with patch("duplo.pipeline.save_plan", return_value=tmp_path / "PLAN.md"):
                    main()

        out = capsys.readouterr().out
        assert "Run mcloop to start building" in out


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
        with patch("duplo.pipeline.investigate", return_value=result):
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
        with patch("duplo.pipeline.investigate", return_value=result):
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
        never calls _subsequent_run."""
        from duplo.migration import _check_migration as real_check

        monkeypatch.setattr("duplo.main._check_migration", real_check)

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        # No SPEC.md → old layout → needs migration.
        monkeypatch.chdir(tmp_path)

        subsequent_run_called = []
        monkeypatch.setattr(
            "duplo.pipeline._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SPEC.md" in captured.out
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
        monkeypatch.setattr(
            "duplo.pipeline._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )

        main()

        # Migration did NOT fire — no migration message in output.
        captured = capsys.readouterr()
        assert "SPEC.md" not in captured.out
        assert "Migrate manually" not in captured.out
        # Proceeded to subsequent run (duplo.json exists).
        assert len(subsequent_run_called) == 1

    def test_migration_pass_without_duplo_json_prints_init_message(
        self, tmp_path, monkeypatch, capsys
    ):
        """Fresh directory (no SPEC.md, no duplo.json) prints init message
        and exits 0 (Phase 7.2.3 dispatch refinement)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "duplo init" in captured.out

    def test_migration_pass_proceeds_to_subsequent_run(self, tmp_path, monkeypatch):
        """When _check_migration returns, _subsequent_run is called (duplo.json exists)."""
        _write_duplo_json(tmp_path, {"features": []})
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        subsequent_run_called = []
        monkeypatch.setattr(
            "duplo.pipeline._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )
        main()
        assert len(subsequent_run_called) == 1

    def test_spec_only_proceeds_to_subsequent_run(self, tmp_path, monkeypatch):
        """SPEC.md alone (no duplo.json yet) routes to _subsequent_run per 7.2.3."""
        (tmp_path / "SPEC.md").write_text(_STUB_SPEC, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        subsequent_run_called = []
        monkeypatch.setattr(
            "duplo.pipeline._subsequent_run",
            lambda: subsequent_run_called.append(True),
        )
        main()
        assert len(subsequent_run_called) == 1

    def test_init_skips_check_migration(self, tmp_path, monkeypatch):
        """'duplo init' dispatches to run_init without the migration check."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init"])

        migration_called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: migration_called.append(target_dir),
        )
        with patch("duplo.init.run_init"):
            main()
        # init bypasses migration.
        assert migration_called == []

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
            "duplo.pipeline._fix_mode",
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
            "duplo.pipeline._fix_mode",
            lambda args: fix_mode_called.append(args),
        )

        main()

        assert migration_called == []
        assert len(fix_mode_called) == 1
        assert fix_mode_called[0].command == "investigate"


class TestInitSubcommand:
    """Tests for the ``duplo init`` subcommand dispatch."""

    def test_init_no_args_dispatches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init"])
        with patch("duplo.init.run_init") as mock_run:
            main()
        mock_run.assert_called_once()
        ns = mock_run.call_args.args[0]
        assert ns.url is None
        assert ns.from_description is None
        assert ns.deep is False
        assert ns.force is False
        assert ns.command == "init"

    def test_init_with_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init", "https://numi.app"])
        with patch("duplo.init.run_init") as mock_run:
            main()
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0].url == "https://numi.app"

    def test_init_with_http_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init", "http://example.com"])
        with patch("duplo.init.run_init") as mock_run:
            main()
        assert mock_run.call_args.args[0].url == "http://example.com"

    def test_init_with_from_description(self, tmp_path, monkeypatch):
        desc = tmp_path / "description.txt"
        desc.write_text("a calculator app", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init", "--from-description", str(desc)])
        with patch("duplo.init.run_init") as mock_run:
            main()
        ns = mock_run.call_args.args[0]
        assert ns.url is None
        assert ns.from_description == str(desc)

    def test_init_with_from_description_stdin(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init", "--from-description", "-"])
        with patch("duplo.init.run_init") as mock_run:
            main()
        assert mock_run.call_args.args[0].from_description == "-"

    def test_init_with_url_and_from_description(self, tmp_path, monkeypatch):
        desc = tmp_path / "description.txt"
        desc.write_text("notes", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["duplo", "init", "https://numi.app", "--from-description", str(desc)],
        )
        with patch("duplo.init.run_init") as mock_run:
            main()
        ns = mock_run.call_args.args[0]
        assert ns.url == "https://numi.app"
        assert ns.from_description == str(desc)

    def test_init_deep_and_force_flags(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["duplo", "init", "https://numi.app", "--deep", "--force"],
        )
        with patch("duplo.init.run_init") as mock_run:
            main()
        ns = mock_run.call_args.args[0]
        assert ns.deep is True
        assert ns.force is True

    def test_init_rejects_invalid_url(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init", "not-a-url"])
        with patch("duplo.init.run_init") as mock_run:
            with pytest.raises(SystemExit):
                main()
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "not a valid URL" in out
        assert "http://" in out

    def test_init_skips_check_migration(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo", "init"])
        called = []
        monkeypatch.setattr(
            "duplo.main._check_migration",
            lambda target_dir: called.append(target_dir),
        )
        with patch("duplo.init.run_init"):
            main()
        assert called == []
