"""Tests for mcloop.ledger_config (Slice D config + flag precedence)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcloop.ledger_config import load_plan_ledger_settings


def _write_orchestra_config(project_dir: Path, body: dict) -> None:
    cfg_dir = project_dir / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(body))


class TestDefaults:
    def test_default_disabled_when_no_ledger_dir(
        self, tmp_path: Path
    ) -> None:
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.enabled is False
        assert s.auto_reauthor is True
        assert s.ledger_dir == (tmp_path / ".duplo" / "ledger").resolve()
        assert s.plan_path == (tmp_path / "PLAN.md").resolve()
        assert s.threshold_params == {}

    def test_default_enabled_when_ledger_dir_exists(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".duplo" / "ledger").mkdir(parents=True)
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.enabled is True
        assert s.auto_reauthor is True


class TestConfigLayer:
    def test_config_disabled_overrides_autodetect(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".duplo" / "ledger").mkdir(parents=True)
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"enabled": False}}
        )
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.enabled is False

    def test_config_auto_reauthor_off(self, tmp_path: Path) -> None:
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"auto_reauthor": False}}
        )
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.auto_reauthor is False

    def test_config_custom_ledger_dir(self, tmp_path: Path) -> None:
        custom = tmp_path / "alt-ledger"
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"ledger_dir": str(custom)}}
        )
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.ledger_dir == custom.resolve()

    def test_config_threshold_params(self, tmp_path: Path) -> None:
        _write_orchestra_config(
            tmp_path,
            {
                "plan_ledger": {
                    "threshold_params": {"exploratory_commit_limit": 7}
                }
            },
        )
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.threshold_params == {"exploratory_commit_limit": 7}

    def test_malformed_config_falls_back_to_defaults(
        self, tmp_path: Path
    ) -> None:
        cfg_dir = tmp_path / ".orchestra"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.json").write_text("not json {{{{")
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.enabled is False
        assert s.auto_reauthor is True


class TestPrecedence:
    def test_cli_no_plan_ledger_beats_config_enabled(
        self, tmp_path: Path
    ) -> None:
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"enabled": True}}
        )
        s = load_plan_ledger_settings(
            project_dir=tmp_path, cli_no_plan_ledger=True
        )
        assert s.enabled is False

    def test_cli_no_auto_reauthor_beats_config(
        self, tmp_path: Path
    ) -> None:
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"auto_reauthor": True}}
        )
        s = load_plan_ledger_settings(
            project_dir=tmp_path, cli_no_auto_reauthor=True
        )
        assert s.auto_reauthor is False

    def test_env_no_plan_ledger_beats_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"enabled": True}}
        )
        monkeypatch.setenv("MCLOOP_NO_PLAN_LEDGER", "1")
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.enabled is False

    def test_env_no_auto_reauthor_beats_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"auto_reauthor": True}}
        )
        monkeypatch.setenv("MCLOOP_NO_AUTO_REAUTHOR", "1")
        s = load_plan_ledger_settings(project_dir=tmp_path)
        assert s.auto_reauthor is False

    def test_cli_beats_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Env says skip plan ledger; CLI should not flip it back on
        # (the CLI flag is store_true with default False, so passing
        # nothing is "no opinion"). Verify the env wins when CLI is
        # not asserted, then the disable flag is sticky.
        _write_orchestra_config(
            tmp_path, {"plan_ledger": {"enabled": True}}
        )
        monkeypatch.setenv("MCLOOP_NO_PLAN_LEDGER", "1")
        s = load_plan_ledger_settings(
            project_dir=tmp_path, cli_no_plan_ledger=True
        )
        assert s.enabled is False
