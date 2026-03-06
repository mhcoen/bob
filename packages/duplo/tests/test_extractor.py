"""Tests for duplo.extractor."""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock, patch

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


class TestExtractFeatures:
    def _make_client(self, json_response: str) -> MagicMock:
        content_block = MagicMock()
        content_block.text = json_response
        message = MagicMock()
        message.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = message
        return client

    def test_returns_feature_list(self):
        response = '[{"name": "Search", "description": "Full-text search.", "category": "core"}]'
        client = self._make_client(response)
        features = extract_features("Some product text", client=client)
        assert len(features) == 1
        assert isinstance(features[0], Feature)
        assert features[0].name == "Search"

    def test_passes_scraped_text_to_api(self):
        client = self._make_client("[]")
        extract_features("My product content", client=client)
        call_args = client.messages.create.call_args
        user_message = call_args.kwargs["messages"][0]["content"]
        assert "My product content" in user_message

    def test_truncates_long_input(self):
        long_text = "x" * 100_000
        client = self._make_client("[]")
        extract_features(long_text, client=client)
        call_args = client.messages.create.call_args
        user_message = call_args.kwargs["messages"][0]["content"]
        # Should be truncated to _MAX_CONTENT_CHARS (60_000) + prompt overhead
        assert len(user_message) < 70_000

    def test_returns_empty_on_bad_response(self):
        client = self._make_client("I cannot extract features from this.")
        features = extract_features("product text", client=client)
        assert features == []

    def test_creates_default_client_when_none(self):
        response = (
            '[{"name": "Auth", "description": "User authentication.", "category": "security"}]'
        )
        mock_client = self._make_client(response)
        with patch("duplo.extractor.anthropic.Anthropic", return_value=mock_client):
            features = extract_features("some text")
        assert len(features) == 1
        assert features[0].name == "Auth"

    def test_uses_expected_model(self):
        client = self._make_client("[]")
        extract_features("text", client=client)
        call_args = client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def json_array(items: list) -> str:
    return _json.dumps(items)
