"""Tests for duplo.build_prefs — LLM-based BuildPreferences parsing."""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.build_prefs import (
    _parse_response,
    _str_list,
    architecture_hash,
    is_all_defaults,
    parse_build_preferences,
    validate_build_preferences,
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
                "language": "TypeScript",
                "framework": "React",
                "dependencies": ["PostgreSQL"],
                "other_constraints": ["prefer functional style"],
            }
        )
        result = parse_build_preferences("Web app using React and PostgreSQL")
        assert result.platform == "web"
        assert result.language == "TypeScript/React"
        assert result.constraints == ["PostgreSQL"]
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
                "framework": "",
                "dependencies": ["no network"],
                "other_constraints": [],
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
        raw = json.dumps(
            {
                "platform": None,
                "language": None,
                "framework": None,
                "dependencies": None,
            }
        )
        result = _parse_response(raw)
        assert result.platform == ""
        assert result.language == ""
        assert result.constraints == []

    def test_language_and_framework_combined(self) -> None:
        raw = json.dumps({"language": "TypeScript", "framework": "React"})
        result = _parse_response(raw)
        assert result.language == "TypeScript/React"

    def test_framework_only_becomes_language(self) -> None:
        raw = json.dumps({"framework": "SwiftUI"})
        result = _parse_response(raw)
        assert result.language == "SwiftUI"

    def test_language_only_no_framework(self) -> None:
        raw = json.dumps({"language": "Go", "framework": ""})
        result = _parse_response(raw)
        assert result.language == "Go"

    def test_dependencies_map_to_constraints(self) -> None:
        raw = json.dumps({"dependencies": ["PostgreSQL", "Redis"]})
        result = _parse_response(raw)
        assert result.constraints == ["PostgreSQL", "Redis"]

    def test_other_constraints_map_to_preferences(self) -> None:
        raw = json.dumps({"other_constraints": ["macOS only", "minimal deps"]})
        result = _parse_response(raw)
        assert result.preferences == ["macOS only", "minimal deps"]


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


class TestIsAllDefaults:
    """Tests for is_all_defaults()."""

    def test_all_empty_is_defaults(self) -> None:
        prefs = BuildPreferences(platform="", language="", constraints=[], preferences=[])
        assert is_all_defaults(prefs) is True

    def test_platform_set_not_defaults(self) -> None:
        prefs = BuildPreferences(platform="web", language="", constraints=[], preferences=[])
        assert is_all_defaults(prefs) is False

    def test_language_set_not_defaults(self) -> None:
        prefs = BuildPreferences(platform="", language="Python", constraints=[], preferences=[])
        assert is_all_defaults(prefs) is False

    def test_constraints_set_not_defaults(self) -> None:
        prefs = BuildPreferences(
            platform="", language="", constraints=["PostgreSQL"], preferences=[]
        )
        assert is_all_defaults(prefs) is False

    def test_preferences_set_not_defaults(self) -> None:
        prefs = BuildPreferences(
            platform="", language="", constraints=[], preferences=["minimal deps"]
        )
        assert is_all_defaults(prefs) is False

    def test_all_populated_not_defaults(self) -> None:
        prefs = BuildPreferences(
            platform="web",
            language="TypeScript/React",
            constraints=["PostgreSQL"],
            preferences=["functional style"],
        )
        assert is_all_defaults(prefs) is False


class TestValidateBuildPreferences:
    """Tests for validate_build_preferences()."""

    def test_all_defaults_emits_warning(self) -> None:
        prefs = BuildPreferences(platform="", language="", constraints=[], preferences=[])
        warnings = validate_build_preferences(prefs)
        assert len(warnings) == 1
        assert "all defaults" in warnings[0]

    def test_populated_prefs_no_warning(self) -> None:
        prefs = BuildPreferences(platform="cli", language="Rust", constraints=[], preferences=[])
        warnings = validate_build_preferences(prefs)
        assert warnings == []

    def test_only_constraints_no_warning(self) -> None:
        prefs = BuildPreferences(platform="", language="", constraints=["Redis"], preferences=[])
        warnings = validate_build_preferences(prefs)
        assert warnings == []


class TestParseResponseAllDefaults:
    """Tests for _parse_response returning all-defaults."""

    def test_empty_json_object_returns_all_defaults(self) -> None:
        result = _parse_response("{}")
        assert is_all_defaults(result)

    def test_all_empty_fields_returns_all_defaults(self) -> None:
        raw = json.dumps(
            {
                "platform": "",
                "language": "",
                "framework": "",
                "dependencies": [],
                "other_constraints": [],
            }
        )
        result = _parse_response(raw)
        assert is_all_defaults(result)

    def test_all_null_fields_returns_all_defaults(self) -> None:
        raw = json.dumps(
            {
                "platform": None,
                "language": None,
                "framework": None,
                "dependencies": None,
                "other_constraints": None,
            }
        )
        result = _parse_response(raw)
        assert is_all_defaults(result)

    @patch("duplo.build_prefs.query")
    def test_llm_no_usable_fields_returns_defaults(self, mock_query: object) -> None:
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "",
                "language": "",
                "framework": "",
                "dependencies": [],
                "other_constraints": [],
            }
        )
        result = parse_build_preferences("Some vague prose with no tech details")
        assert is_all_defaults(result)


class TestTypicalArchitectureProse:
    """Parse with typical architecture prose — fields populated correctly."""

    @patch("duplo.build_prefs.query")
    def test_swift_macos_app(self, mock_query: object) -> None:
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "desktop",
                "language": "Swift",
                "framework": "SwiftUI",
                "dependencies": ["CoreData"],
                "other_constraints": ["macOS only", "minimum macOS 14"],
            }
        )
        result = parse_build_preferences(
            "Native macOS desktop app built with Swift and SwiftUI. "
            "Uses CoreData for persistence. Targets macOS 14+."
        )
        assert result.platform == "desktop"
        assert result.language == "Swift/SwiftUI"
        assert result.constraints == ["CoreData"]
        assert result.preferences == ["macOS only", "minimum macOS 14"]

    @patch("duplo.build_prefs.query")
    def test_missing_fields_default(self, mock_query: object) -> None:
        """LLM returns only platform and language; rest defaults."""
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "cli",
                "language": "Go",
            }
        )
        result = parse_build_preferences("Command-line tool written in Go.")
        assert result.platform == "cli"
        assert result.language == "Go"
        assert result.constraints == []
        assert result.preferences == []

    @patch("duplo.build_prefs.query")
    def test_web_fullstack(self, mock_query: object) -> None:
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "web",
                "language": "TypeScript",
                "framework": "Next.js",
                "dependencies": ["PostgreSQL", "Tailwind CSS", "Prisma"],
                "other_constraints": ["prefer server components"],
            }
        )
        result = parse_build_preferences(
            "Full-stack web app. TypeScript with Next.js. "
            "PostgreSQL via Prisma. Tailwind CSS for styling. "
            "Prefer server components where possible."
        )
        assert result.platform == "web"
        assert result.language == "TypeScript/Next.js"
        assert "PostgreSQL" in result.constraints
        assert "Tailwind CSS" in result.constraints
        assert result.preferences == ["prefer server components"]


class TestCommentStrippingAndHash:
    """Commented-out content in ## Architecture does NOT change hash."""

    def test_comments_do_not_affect_hash(self) -> None:
        """Adding/removing HTML comments does not change the hash."""
        prose_no_comment = "Swift macOS app with SwiftUI."
        prose_with_comment = "Swift macOS app with SwiftUI.<!-- TODO: consider AppKit fallback -->"
        from duplo.spec_reader import _parse_spec

        spec_clean = _parse_spec("## Architecture\n" + prose_no_comment)
        spec_commented = _parse_spec("## Architecture\n" + prose_with_comment)
        # Comments stripped before storage, so architecture text is identical.
        assert spec_clean.architecture == spec_commented.architecture
        # Therefore hashes are identical.
        h1 = architecture_hash(spec_clean.architecture)
        h2 = architecture_hash(spec_commented.architecture)
        assert h1 == h2

    def test_changing_real_content_changes_hash(self) -> None:
        """Non-comment changes DO produce a different hash."""
        from duplo.spec_reader import _parse_spec

        spec_a = _parse_spec("## Architecture\nSwift macOS app.")
        spec_b = _parse_spec("## Architecture\nRust CLI tool.")
        assert architecture_hash(spec_a.architecture) != architecture_hash(spec_b.architecture)

    def test_spec_architecture_has_comments_stripped(self) -> None:
        """spec.architecture contains no HTML comments after parsing."""
        from duplo.spec_reader import _parse_spec

        spec = _parse_spec("## Architecture\nSwiftUI app.\n<!-- internal note -->\nUses MVVM.")
        assert "<!--" not in spec.architecture
        assert "internal note" not in spec.architecture
        assert "SwiftUI app." in spec.architecture
        assert "Uses MVVM." in spec.architecture


class TestCacheHitAvoidsLlmCall:
    """Cache hit avoids the LLM call (integration with _load_preferences)."""

    def test_matching_hash_skips_parse(self) -> None:
        from duplo.main import _load_preferences

        arch = "Swift macOS app with SwiftUI and CoreData."
        h = architecture_hash(arch)
        spec = type("Spec", (), {"architecture": arch})()
        data = {
            "preferences": {
                "platform": "desktop",
                "language": "Swift/SwiftUI",
                "constraints": ["CoreData"],
                "preferences": ["macOS only"],
            },
            "architecture_hash": h,
        }
        with patch("duplo.main.parse_build_preferences") as mock_parse:
            result = _load_preferences(data, spec)
            mock_parse.assert_not_called()
        assert result.platform == "desktop"
        assert result.language == "Swift/SwiftUI"

    def test_changed_architecture_triggers_reparse(self, tmp_path, monkeypatch) -> None:
        from duplo.main import _load_preferences

        monkeypatch.chdir(tmp_path)
        old_arch = "Python CLI tool."
        new_arch = "Swift macOS app with SwiftUI."
        old_hash = architecture_hash(old_arch)
        spec = type("Spec", (), {"architecture": new_arch})()
        data = {
            "preferences": {
                "platform": "cli",
                "language": "Python",
                "constraints": [],
                "preferences": [],
            },
            "architecture_hash": old_hash,
        }
        new_prefs = BuildPreferences(
            platform="desktop",
            language="Swift/SwiftUI",
            constraints=[],
            preferences=["macOS only"],
        )
        with (
            patch(
                "duplo.main.parse_build_preferences",
                return_value=new_prefs,
            ) as mock_parse,
            patch("duplo.main.save_build_preferences"),
        ):
            result = _load_preferences(data, spec)
            mock_parse.assert_called_once_with(new_arch)
        assert result.platform == "desktop"
        assert result.language == "Swift/SwiftUI"


class TestAllDefaultsWarningViaValidate:
    """All-defaults BuildPreferences emits warning via validate_build_preferences."""

    def test_all_defaults_warning_message_content(self) -> None:
        prefs = BuildPreferences(platform="", language="", constraints=[], preferences=[])
        warnings = validate_build_preferences(prefs)
        assert len(warnings) == 1
        assert "all defaults" in warnings[0]
        assert "## Architecture" in warnings[0]
        assert "Plan generation will proceed" in warnings[0]

    @patch("duplo.build_prefs.query")
    def test_all_defaults_from_parse_triggers_warning(self, mock_query: object) -> None:
        """End-to-end: LLM returns empty -> validate emits warning."""
        mock_query.return_value = json.dumps(  # type: ignore[union-attr]
            {
                "platform": "",
                "language": "",
                "framework": "",
                "dependencies": [],
                "other_constraints": [],
            }
        )
        prefs = parse_build_preferences("vague text with no specifics")
        assert is_all_defaults(prefs)
        warnings = validate_build_preferences(prefs)
        assert len(warnings) == 1

    def test_populated_prefs_no_warning(self) -> None:
        prefs = BuildPreferences(
            platform="desktop",
            language="Swift/SwiftUI",
            constraints=["CoreData"],
            preferences=["macOS only"],
        )
        warnings = validate_build_preferences(prefs)
        assert warnings == []
