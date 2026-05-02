"""Tests for mcloop.config module."""

from __future__ import annotations

import json

from mcloop.config import (
    REVIEWER_BACKENDS,
    format_reviewer_status,
    load_reviewer_config,
)


class TestLoadReviewerConfig:
    def test_returns_config_with_api_key(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "model": "gpt-4o",
                        "base_url": "https://api.example.com/v1",
                        "enabled": True,
                    }
                }
            )
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
        result = load_reviewer_config(str(tmp_path))
        assert result is not None
        assert result["model"] == "gpt-4o"
        assert result["base_url"] == "https://api.example.com/v1"
        assert result["api_key"] == "sk-test-123"

    def test_returns_none_when_no_api_key(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps({"reviewer": {"model": "gpt-4o"}})
        )
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert load_reviewer_config(str(tmp_path)) is None

    def test_returns_none_when_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_returns_none_when_no_reviewer_section(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(json.dumps({"other": "stuff"}))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_returns_none_on_invalid_json(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text("not json{{{")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_returns_none_when_reviewer_not_dict(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(json.dumps({"reviewer": "not a dict"}))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_returns_none_when_top_level_not_dict(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(json.dumps([1, 2, 3]))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_default_backend_is_rest(self, tmp_path, monkeypatch):
        """A reviewer config with no backend field defaults to rest and
        the loader still requires OPENROUTER_API_KEY (rest behavior)."""
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "model": "gpt-4o",
                        "base_url": "https://api.example.com/v1",
                        "enabled": True,
                    }
                }
            )
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        result = load_reviewer_config(str(tmp_path))
        assert result is not None
        assert result["backend"] == "rest"

    def test_backend_codex_skips_api_key_requirement(
        self, tmp_path, monkeypatch
    ):
        """Subscription backends must not require OPENROUTER_API_KEY in
        the environment. The returned dict must not carry an api_key."""
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "backend": "codex",
                        "model": "gpt-5.5",
                        "enabled": True,
                    }
                }
            )
        )
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = load_reviewer_config(str(tmp_path))
        assert result is not None
        assert result["backend"] == "codex"
        assert result["model"] == "gpt-5.5"
        assert "api_key" not in result

    def test_backend_claude_code_skips_api_key_requirement(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "backend": "claude_code",
                        "model": "claude-opus-4-7",
                        "enabled": True,
                    }
                }
            )
        )
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = load_reviewer_config(str(tmp_path))
        assert result is not None
        assert result["backend"] == "claude_code"
        assert "api_key" not in result

    def test_unknown_backend_returns_none(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "backend": "made-up-thing",
                        "model": "x",
                        "enabled": True,
                    }
                }
            )
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert load_reviewer_config(str(tmp_path)) is None

    def test_backend_set_constant_pinning(self):
        """Pin the supported backend set so a future addition is a
        deliberate edit reviewers see, not a silent drift."""
        assert REVIEWER_BACKENDS == frozenset({"rest", "claude_code", "codex"})

    def test_subscription_backend_force_flag_works(
        self, tmp_path, monkeypatch
    ):
        """force=True (the --reviewer flag) should enable a subscription
        backend even when enabled is not set in the config."""
        config_dir = tmp_path / ".mcloop"
        config_dir.mkdir()
        config_dir.joinpath("config.json").write_text(
            json.dumps(
                {
                    "reviewer": {
                        "backend": "codex",
                        "model": "gpt-5.5",
                    }
                }
            )
        )
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        # Without force, "enabled": false (the default) skips the config.
        assert load_reviewer_config(str(tmp_path)) is None
        # With force, the same config loads.
        assert load_reviewer_config(str(tmp_path), force=True) is not None


class TestFormatReviewerStatus:
    def test_full_config(self):
        config = {
            "model": "gpt-4o",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-test",
        }
        assert format_reviewer_status(config) == "gpt-4o via openrouter.ai (API key set)"

    def test_no_api_key(self):
        config = {"model": "gpt-4o", "base_url": "https://openrouter.ai/api/v1"}
        result = format_reviewer_status(config)
        assert result == "configured but OPENROUTER_API_KEY not set (disabled)"

    def test_none_config(self):
        assert format_reviewer_status(None) == ""

    def test_empty_api_key(self):
        config = {"model": "gpt-4o", "base_url": "https://example.com", "api_key": ""}
        result = format_reviewer_status(config)
        assert result == "configured but OPENROUTER_API_KEY not set (disabled)"

    def test_codex_backend_status(self):
        config = {"backend": "codex", "model": "gpt-5.5"}
        assert format_reviewer_status(config) == "gpt-5.5 via Codex (subscription)"

    def test_claude_code_backend_status(self):
        config = {"backend": "claude_code", "model": "claude-opus-4-7"}
        assert (
            format_reviewer_status(config)
            == "claude-opus-4-7 via Claude Code (subscription)"
        )

    def test_subscription_backend_ignores_missing_api_key(self):
        """A subscription backend reports its model + provider regardless
        of whether OPENROUTER_API_KEY is in the environment, because the
        backend never reads that variable."""
        config = {"backend": "codex", "model": "gpt-5.5"}  # no api_key
        result = format_reviewer_status(config)
        assert "subscription" in result
        assert "OPENROUTER_API_KEY" not in result
