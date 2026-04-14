"""Tests for duplo.build_prefs — LLM-based BuildPreferences parsing."""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.build_prefs import (
    _parse_response,
    _str_list,
    architecture_hash,
    parse_build_preferences,
)
from duplo.claude_cli import ClaudeCliError
from duplo.questioner import BuildPreferences


class TestParseBuildPreferences:
    """Tests for parse_build_preferences()."""

    def test_empty_prose_returns_defaults(self) -> None:
        result = parse_build_preferences("")
        assert result == BuildPreferences(platform="", language="", constraints=[], preferences=[])

    def test_whitespace_only_returns_defaults(self) -> None:
        result = parse_build_preferences("   \n  ")
        assert result == BuildPreferences(platform="", language="", constraints=[], preferences=[])

    @patch("duplo.build_prefs.query")
    def test_successful_parse(self, mock_query: object) -> None:
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "web",
                "language": "TypeScript/React",
                "constraints": ["must use PostgreSQL"],
                "preferences": ["prefer functional style"],
            }
        )
        result = parse_build_preferences("Web app using React and PostgreSQL")
        assert result.platform == "web"
        assert result.language == "TypeScript/React"
        assert result.constraints == ["must use PostgreSQL"]
        assert result.preferences == ["prefer functional style"]

    @patch("duplo.build_prefs.query")
    def test_llm_failure_returns_defaults(self, mock_query: object) -> None:
        mock_query.side_effect = ClaudeCliError("timeout")  # type: ignore[union-attr]
        result = parse_build_preferences("Some architecture prose")
        assert result == BuildPreferences(platform="", language="", constraints=[], preferences=[])

    @patch("duplo.build_prefs.query")
    def test_llm_failure_records_diagnostic(self, mock_query: object) -> None:
        mock_query.side_effect = ClaudeCliError("timeout")  # type: ignore[union-attr]
        with patch("duplo.build_prefs.record_failure") as mock_rec:
            parse_build_preferences("Some prose")
            mock_rec.assert_called_once()
            assert "LLM call failed" in mock_rec.call_args[0][2]

    @patch("duplo.build_prefs.query")
    def test_passes_system_prompt(self, mock_query: object) -> None:
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {"platform": "", "language": "", "constraints": [], "preferences": []}
        )
        parse_build_preferences("CLI tool in Rust")
        mock_query.assert_called_once()  # type: ignore[union-attr]
        _, kwargs = mock_query.call_args  # type: ignore[union-attr]
        assert "system" in kwargs
        assert kwargs["system"]  # non-empty system prompt


class TestParseResponse:
    """Tests for _parse_response()."""

    def test_valid_json(self) -> None:
        raw = json.dumps(
            {
                "platform": "cli",
                "language": "Rust",
                "constraints": ["no network"],
                "preferences": [],
            }
        )
        result = _parse_response(raw)
        assert result.platform == "cli"
        assert result.language == "Rust"
        assert result.constraints == ["no network"]
        assert result.preferences == []

    def test_json_with_fences(self) -> None:
        raw = '```json\n{"platform": "web", "language": "Python"}\n```'
        result = _parse_response(raw)
        assert result.platform == "web"
        assert result.language == "Python"

    def test_json_with_surrounding_text(self) -> None:
        raw = 'Here is the result:\n{"platform": "desktop"}\nDone.'
        result = _parse_response(raw)
        assert result.platform == "desktop"

    def test_invalid_json_returns_defaults(self) -> None:
        result = _parse_response("not json at all")
        assert result == BuildPreferences(platform="", language="", constraints=[], preferences=[])

    def test_invalid_json_records_diagnostic(self) -> None:
        with patch("duplo.build_prefs.record_failure") as mock_rec:
            _parse_response("not json")
            mock_rec.assert_called_once()
            assert "Failed to parse" in mock_rec.call_args[0][2]

    def test_non_dict_json_returns_defaults(self) -> None:
        result = _parse_response('["a", "b"]')
        assert result == BuildPreferences(platform="", language="", constraints=[], preferences=[])

    def test_missing_fields_use_defaults(self) -> None:
        result = _parse_response('{"platform": "api"}')
        assert result.platform == "api"
        assert result.language == ""
        assert result.constraints == []
        assert result.preferences == []

    def test_null_fields_coerced(self) -> None:
        raw = json.dumps({"platform": None, "language": None, "constraints": None})
        result = _parse_response(raw)
        assert result.platform == ""
        assert result.language == ""
        assert result.constraints == []


class TestArchitectureHash:
    """Tests for architecture_hash()."""

    def test_deterministic(self) -> None:
        h1 = architecture_hash("Web app using React")
        h2 = architecture_hash("Web app using React")
        assert h1 == h2

    def test_different_input_different_hash(self) -> None:
        h1 = architecture_hash("Web app using React")
        h2 = architecture_hash("CLI tool in Rust")
        assert h1 != h2

    def test_empty_string(self) -> None:
        h = architecture_hash("")
        assert len(h) == 64  # SHA-256 hex digest length

    def test_hex_digest_format(self) -> None:
        h = architecture_hash("some prose")
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64


class TestStrList:
    """Tests for _str_list()."""

    def test_list_of_strings(self) -> None:
        assert _str_list(["a", "b"]) == ["a", "b"]

    def test_list_with_non_strings(self) -> None:
        assert _str_list([1, "two", 3.0]) == ["1", "two", "3.0"]

    def test_empty_items_filtered(self) -> None:
        assert _str_list(["a", "", None, "b"]) == ["a", "b"]

    def test_non_list_returns_empty(self) -> None:
        assert _str_list("not a list") == []
        assert _str_list(42) == []
        assert _str_list(None) == []
