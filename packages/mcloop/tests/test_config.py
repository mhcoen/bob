"""Tests for mcloop.config module."""

from __future__ import annotations

import json

from mcloop.config import format_reviewer_status, load_reviewer_config


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
