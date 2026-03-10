"""Tests for duplo.extractor."""

from __future__ import annotations

import json as _json
from unittest.mock import patch

from duplo.extractor import Feature, _parse_features, extract_features


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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def json_array(items: list) -> str:
    return _json.dumps(items)
