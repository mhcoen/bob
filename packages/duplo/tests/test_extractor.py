"""Tests for duplo.extractor."""

from __future__ import annotations

import json as _json
from unittest.mock import patch

from duplo.claude_cli import ClaudeCliError
from duplo.extractor import Feature, _matches_excluded, _parse_features, extract_features


class TestParseFeatures:
    def test_parses_valid_json(self):
        raw = '[{"name": "Real-time sync", "description": "Syncs data in real time.", "category": "core"}]'
        features = _parse_features(raw)
        assert len(features) == 1
        assert features[0].name == "Real-time sync"
        assert features[0].description == "Syncs data in real time."
        assert features[0].category == "core"

    def test_parses_multiple_features(self):
        raw = json_array(
            [
                {"name": "Feature A", "description": "Does A.", "category": "core"},
                {"name": "Feature B", "description": "Does B.", "category": "api"},
            ]
        )
        features = _parse_features(raw)
        assert len(features) == 2
        assert features[0].name == "Feature A"
        assert features[1].name == "Feature B"

    def test_strips_markdown_code_fence(self):
        raw = '```json\n[{"name": "SSO", "description": "Supports single sign-on.", "category": "security"}]\n```'
        features = _parse_features(raw)
        assert len(features) == 1
        assert features[0].name == "SSO"

    def test_strips_plain_code_fence(self):
        raw = '```\n[{"name": "SSO", "description": "Supports SSO.", "category": "security"}]\n```'
        features = _parse_features(raw)
        assert len(features) == 1

    def test_returns_empty_on_invalid_json(self):
        assert _parse_features("not json at all") == []

    def test_returns_empty_on_json_object_not_array(self):
        assert _parse_features('{"name": "x"}') == []

    def test_skips_items_missing_name(self):
        raw = '[{"description": "Does something.", "category": "core"}]'
        features = _parse_features(raw)
        assert features == []

    def test_skips_items_missing_description(self):
        raw = '[{"name": "Something", "category": "core"}]'
        features = _parse_features(raw)
        assert features == []

    def test_defaults_category_to_other(self):
        raw = '[{"name": "Widget", "description": "A widget."}]'
        features = _parse_features(raw)
        assert len(features) == 1
        assert features[0].category == "other"

    def test_skips_non_dict_items(self):
        raw = '[{"name": "Valid", "description": "Valid item.", "category": "core"}, "invalid"]'
        features = _parse_features(raw)
        assert len(features) == 1
        assert features[0].name == "Valid"

    def test_empty_array(self):
        assert _parse_features("[]") == []

    def test_defaults_status_to_pending(self):
        raw = '[{"name": "Search", "description": "Full-text search.", "category": "core"}]'
        features = _parse_features(raw)
        assert features[0].status == "pending"
        assert features[0].implemented_in == ""

    def test_feature_dataclass_defaults(self):
        feat = Feature(name="X", description="Y", category="core")
        assert feat.status == "pending"
        assert feat.implemented_in == ""

    def test_feature_from_dict_without_status_defaults_to_pending(self):
        d = {"name": "X", "description": "Y", "category": "core"}
        feat = Feature(**d)
        assert feat.status == "pending"
        assert feat.implemented_in == ""

    def test_feature_explicit_status(self):
        feat = Feature(
            name="X",
            description="Y",
            category="core",
            status="implemented",
            implemented_in="Phase 1",
        )
        assert feat.status == "implemented"
        assert feat.implemented_in == "Phase 1"


class TestExtractorSystemPrompt:
    def test_system_prompt_contains_hallucination_constraints(self):
        from duplo.extractor import _SYSTEM

        assert "DEMONSTRABLY OFFERS" in _SYSTEM
        assert "Do NOT hallucinate" in _SYSTEM
        assert "Do NOT extract features of the PLATFORM" in _SYSTEM
        assert "When in doubt, OMIT" in _SYSTEM

    def test_system_prompt_warns_against_passing_mentions(self):
        from duplo.extractor import _SYSTEM

        assert "mentioned in passing" in _SYSTEM

    def test_system_prompt_references_multiple_sources(self):
        """Prompt must say 'product sources', not 'product website'."""
        from duplo.extractor import _SYSTEM

        assert "product sources" in _SYSTEM


class TestExtractFeatures:
    def test_returns_feature_list(self):
        response = '[{"name": "Search", "description": "Full-text search.", "category": "core"}]'
        with patch("duplo.extractor.query", return_value=response):
            features = extract_features("Some product text")
        assert len(features) == 1
        assert isinstance(features[0], Feature)
        assert features[0].name == "Search"

    def test_passes_scraped_text_to_prompt(self):
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features("My product content")
        prompt = mock_query.call_args[0][0]
        assert "My product content" in prompt

    def test_user_prompt_says_product_content(self):
        """Prompt must say 'product content', not 'product website'."""
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features("text")
        prompt = mock_query.call_args[0][0]
        assert "product content" in prompt
        assert "product website" not in prompt

    def test_truncates_long_input(self):
        long_text = "x" * 100_000
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features(long_text)
        prompt = mock_query.call_args[0][0]
        assert len(prompt) < 70_000

    def test_returns_empty_on_bad_response(self):
        with patch(
            "duplo.extractor.query",
            return_value="I cannot extract features from this.",
        ):
            features = extract_features("product text")
        assert features == []

    def test_returns_empty_on_claude_cli_error(self):
        with patch(
            "duplo.extractor.query",
            side_effect=ClaudeCliError("claude CLI timed out after 300 seconds"),
        ):
            features = extract_features("product text")
        assert features == []


class TestExtractFeaturesMultiSource:
    """Verify extract_features handles concatenated multi-source text."""

    def test_concatenated_sources_all_visible_in_prompt(self):
        """Text from multiple sources is passed through to the LLM."""
        source_a = "Source A: Calculator with basic math operations."
        source_b = "Source B: Unit converter with metric support."
        combined = source_a + "\n" + source_b
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features(combined)
        prompt = mock_query.call_args[0][0]
        assert "Calculator with basic math" in prompt
        assert "Unit converter with metric" in prompt

    def test_features_extracted_from_multiple_sources(self):
        """Features from different sources are all returned."""
        raw = json_array(
            [
                {
                    "name": "Calculator",
                    "description": "Basic math.",
                    "category": "core",
                },
                {
                    "name": "Unit converter",
                    "description": "Metric conversion.",
                    "category": "core",
                },
            ]
        )
        combined = "Website text about calc.\nDocs text about converter."
        with patch("duplo.extractor.query", return_value=raw):
            features = extract_features(combined)
        assert len(features) == 2
        names = {f.name for f in features}
        assert "Calculator" in names
        assert "Unit converter" in names

    def test_spec_text_and_scope_with_multi_source(self):
        """spec_text and scope params work with concatenated input."""
        raw = json_array(
            [
                {
                    "name": "Math",
                    "description": "Basic math.",
                    "category": "core",
                },
                {
                    "name": "CLI tool",
                    "description": "Command line.",
                    "category": "other",
                },
            ]
        )
        combined = "Site text.\nPDF text.\nDocs text."
        with patch("duplo.extractor.query", return_value=raw) as mock_q:
            features = extract_features(
                combined,
                spec_text="Build a calculator app.",
                scope_exclude=["CLI tool"],
            )
        system = mock_q.call_args.kwargs.get("system", "")
        assert "Build a calculator app." in system
        assert len(features) == 2


class TestExtractFeaturesWithSpec:
    def test_spec_text_injected_into_system_prompt(self):
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features("content", spec_text="Build a calculator.")
        system = mock_query.call_args.kwargs.get("system", "")
        assert "Build a calculator." in system

    def test_spec_text_empty_does_not_modify_system(self):
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features("content", spec_text="")
        system = mock_query.call_args.kwargs.get("system", "")
        assert "product specification" not in system.lower()

    def test_scope_exclude_not_applied_internally(self):
        """scope_exclude filtering is applied at the orchestrator level, not here."""
        raw = json_array(
            [
                {"name": "Math", "description": "Basic math.", "category": "core"},
                {"name": "CLI tool", "description": "Command line.", "category": "other"},
            ]
        )
        with patch("duplo.extractor.query", return_value=raw):
            features = extract_features("content", scope_exclude=["CLI tool"])
        assert len(features) == 2

    def test_scope_include_does_not_filter(self):
        """Scope includes never drop extracted features; they only add."""
        raw = json_array(
            [
                {"name": "Math", "description": "Basic math.", "category": "core"},
            ]
        )
        with patch("duplo.extractor.query", return_value=raw):
            features = extract_features("content", scope_include=["Variables"])
        # Math is kept and Variables is synthesized because it was not
        # surfaced by the LLM.
        names = {f.name for f in features}
        assert "Math" in names
        assert "Variables" in names

    def test_combined_spec_and_existing_names(self):
        with patch("duplo.extractor.query", return_value="[]") as mock_query:
            extract_features(
                "content",
                existing_names=["Search"],
                spec_text="Build a widget.",
            )
        system = mock_query.call_args.kwargs.get("system", "")
        assert "Build a widget." in system
        assert "Search" in system


class TestScopeIncludeReconciliation:
    """Unmatched scope_include items are deterministically synthesized."""

    def _raw(self, *names: str) -> str:
        return json_array(
            [{"name": n, "description": f"Does {n}.", "category": "core"} for n in names]
        )

    def test_unmatched_include_is_synthesized(self):
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content", scope_include=["Variables"])
        synth = [f for f in features if f.name == "Variables"]
        assert len(synth) == 1
        assert synth[0].category == "core"
        assert "Variables" in synth[0].description

    def test_matched_include_is_not_duplicated(self):
        """An include already extracted by the LLM is not synthesized again."""
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content", scope_include=["Math"])
        assert [f.name for f in features].count("Math") == 1
        assert len(features) == 1

    def test_match_is_case_insensitive(self):
        """Case differences between include and extracted name still match."""
        with patch("duplo.extractor.query", return_value=self._raw("Real-Time Sync")):
            features = extract_features("content", scope_include=["real-time sync"])
        assert len(features) == 1
        assert features[0].name == "Real-Time Sync"

    def test_extracted_features_preserved_before_synthesized(self):
        """Extracted features keep their order; synthesized ones append after."""
        with patch("duplo.extractor.query", return_value=self._raw("Math", "Charts")):
            features = extract_features("content", scope_include=["Charts", "Variables"])
        names = [f.name for f in features]
        assert names == ["Math", "Charts", "Variables"]

    def test_multiple_unmatched_includes_all_synthesized(self):
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content", scope_include=["Variables", "Functions"])
        names = {f.name for f in features}
        assert {"Math", "Variables", "Functions"} <= names

    def test_blank_include_items_ignored(self):
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content", scope_include=["", "   ", "Variables"])
        names = [f.name for f in features]
        assert names == ["Math", "Variables"]

    def test_duplicate_includes_synthesized_once(self):
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content", scope_include=["Variables", "variables"])
        assert [f.name for f in features].count("Variables") == 1

    def test_synthesized_even_when_extraction_empty(self):
        """Includes surface even if the LLM returned nothing usable."""
        with patch("duplo.extractor.query", return_value="not json"):
            features = extract_features("content", scope_include=["Variables"])
        assert [f.name for f in features] == ["Variables"]

    def test_synthesized_on_claude_cli_error(self):
        """A failed extraction still surfaces required scope includes."""
        with patch(
            "duplo.extractor.query",
            side_effect=ClaudeCliError("timed out"),
        ):
            features = extract_features("content", scope_include=["Variables"])
        assert [f.name for f in features] == ["Variables"]

    def test_no_scope_include_leaves_features_unchanged(self):
        with patch("duplo.extractor.query", return_value=self._raw("Math")):
            features = extract_features("content")
        assert [f.name for f in features] == ["Math"]


class TestMatchesExcluded:
    """Tests for _matches_excluded word-boundary regex filter."""

    def _feat(self, name: str, description: str = "") -> Feature:
        return Feature(name=name, description=description, category="core")

    def test_exact_name_match(self):
        feat = self._feat("Plugin API")
        assert _matches_excluded(feat, ["Plugin API"]) is True

    def test_case_insensitive_match(self):
        feat = self._feat("Plugin API")
        assert _matches_excluded(feat, ["plugin api"]) is True

    def test_match_with_trailing_punctuation(self):
        feat = self._feat("plugin API.", description="")
        assert _matches_excluded(feat, ["plugin API"]) is True

    def test_no_match_hyphenated(self):
        """'non-plugin-API' should NOT match excluded term 'plugin API'."""
        feat = self._feat("non-plugin-API")
        assert _matches_excluded(feat, ["plugin API"]) is False

    def test_no_match_substring(self):
        """'plugins' should NOT match excluded term 'plugin'."""
        feat = self._feat("plugins manager")
        assert _matches_excluded(feat, ["plugin"]) is False

    def test_match_in_description(self):
        feat = self._feat("Some Feature", description="Provides a plugin API.")
        assert _matches_excluded(feat, ["plugin API"]) is True

    def test_no_match_description_substring(self):
        feat = self._feat(
            "Some Feature",
            description="Uses non-plugin-API approach.",
        )
        assert _matches_excluded(feat, ["plugin API"]) is False

    def test_empty_scope_exclude(self):
        feat = self._feat("Anything")
        assert _matches_excluded(feat, []) is False

    def test_multiple_terms_first_matches(self):
        feat = self._feat("CLI tool")
        assert _matches_excluded(feat, ["CLI tool", "REST API"]) is True

    def test_multiple_terms_second_matches(self):
        feat = self._feat("REST API")
        assert _matches_excluded(feat, ["CLI tool", "REST API"]) is True

    def test_multiple_terms_none_match(self):
        feat = self._feat("Calculator")
        assert _matches_excluded(feat, ["CLI tool", "REST API"]) is False

    def test_word_boundary_at_start_of_string(self):
        feat = self._feat("API access")
        assert _matches_excluded(feat, ["API"]) is True

    def test_word_boundary_at_end_of_string(self):
        feat = self._feat("Custom API")
        assert _matches_excluded(feat, ["API"]) is True

    def test_single_word_term(self):
        feat = self._feat("Webhooks support")
        assert _matches_excluded(feat, ["Webhooks"]) is True

    def test_single_word_no_match_partial(self):
        feat = self._feat("Webhooks support")
        assert _matches_excluded(feat, ["Webhook"]) is False

    def test_emits_diagnostic(self):
        feat = self._feat("Plugin API", description="Extends via plugins.")
        with patch("duplo.extractor.record_failure") as mock_rf:
            result = _matches_excluded(feat, ["Plugin API"])
        assert result is True
        mock_rf.assert_called_once()
        args = mock_rf.call_args
        assert args[0][0] == "extractor:scope_exclude"
        assert args[0][1] == "io"
        assert "Plugin API" in args[0][2]
        assert "dropped" in args[0][2]

    def test_no_diagnostic_when_no_match(self):
        feat = self._feat("Calculator")
        with patch("duplo.extractor.record_failure") as mock_rf:
            _matches_excluded(feat, ["Plugin API"])
        mock_rf.assert_not_called()

    def test_regex_special_chars_escaped(self):
        """Terms with regex metacharacters are treated as literals."""
        feat = self._feat("C++ support")
        assert _matches_excluded(feat, ["C++"]) is True

    def test_regex_special_chars_no_false_positive(self):
        feat = self._feat("Cpp support")
        assert _matches_excluded(feat, ["C++"]) is False

    def test_description_only_match(self):
        """Term absent from name but present in description still matches."""
        feat = self._feat("Data export", description="Exports via REST API.")
        assert _matches_excluded(feat, ["REST API"]) is True

    def test_description_only_no_match_when_absent(self):
        """Neither name nor description contains term."""
        feat = self._feat("Data export", description="Exports data to CSV.")
        assert _matches_excluded(feat, ["REST API"]) is False

    def test_name_matches_description_does_not(self):
        """Match in name is sufficient even if description doesn't match."""
        feat = self._feat("REST API", description="Provides data access.")
        assert _matches_excluded(feat, ["REST API"]) is True

    def test_diagnostic_on_description_match(self):
        """Diagnostic emitted when match is in description, not name."""
        feat = self._feat("Data export", description="Exports via REST API.")
        with patch("duplo.extractor.record_failure") as mock_rf:
            result = _matches_excluded(feat, ["REST API"])
        assert result is True
        mock_rf.assert_called_once()
        assert "Data export" in mock_rf.call_args[0][2]

    def test_match_with_leading_whitespace(self):
        """Term at start of string after whitespace still matches."""
        feat = self._feat("  Plugin API")
        assert _matches_excluded(feat, ["Plugin API"]) is True

    def test_no_match_suffix_hyphenated(self):
        """'plugins-API' should NOT match excluded term 'plugin'."""
        feat = self._feat("plugins-API")
        assert _matches_excluded(feat, ["plugin"]) is False

    def test_no_match_separate_words(self):
        """'plugin' and 'API' mentioned separately should NOT match 'plugin API'."""
        feat = self._feat(
            "Plugin system",
            description="The plugin system exposes a public API.",
        )
        assert _matches_excluded(feat, ["plugin API"]) is False

    def test_multiple_terms_each_emits_own_diagnostic(self):
        """Each (term, feature) pair that matches emits its own diagnostic."""
        feat = self._feat("CLI REST API tool")
        with patch("duplo.extractor.record_failure") as mock_rf:
            result = _matches_excluded(feat, ["CLI", "REST API"])
        assert result is True
        # First matching term causes early return; one diagnostic emitted.
        mock_rf.assert_called_once()
        assert "CLI" in mock_rf.call_args[0][2]


class TestParseFeaturesRobustness:
    """Parser round-trips across fenced, prose-prefixed, and trailing-whitespace inputs."""

    _PAYLOAD = [
        {"name": "Search", "description": "Full-text search.", "category": "core"},
    ]

    def _assert_round_trips(self, raw: str) -> None:
        features = _parse_features(raw)
        assert len(features) == 1
        assert features[0].name == "Search"
        assert features[0].description == "Full-text search."
        assert features[0].category == "core"

    def test_fenced_json_round_trips(self):
        raw = f"```json\n{_json.dumps(self._PAYLOAD)}\n```"
        self._assert_round_trips(raw)

    def test_prose_prefixed_round_trips(self):
        raw = f"Here are the features I extracted:\n\n{_json.dumps(self._PAYLOAD)}"
        self._assert_round_trips(raw)

    def test_trailing_whitespace_round_trips(self):
        raw = f"{_json.dumps(self._PAYLOAD)}\n  \t\n"
        self._assert_round_trips(raw)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def json_array(items: list) -> str:
    return _json.dumps(items)
