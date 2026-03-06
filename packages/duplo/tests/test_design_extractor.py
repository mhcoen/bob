"""Tests for duplo.design_extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from duplo.design_extractor import (
    DesignRequirements,
    _parse_design,
    extract_design,
    format_design_section,
)


# Minimal valid 1x1 PNG bytes for test images.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_image(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(_PNG_BYTES)
    return path


def _sample_response() -> dict:
    return {
        "colors": {"primary": "#1a73e8", "background": "#ffffff", "text": "#333333"},
        "fonts": {"headings": "Inter Bold, ~24px", "body": "Inter, ~16px"},
        "spacing": {"content_padding": "24px", "section_gap": "32px"},
        "layout": {
            "navigation": "top",
            "sidebar": "none",
            "content_width": "wide",
        },
        "components": [
            {"name": "button", "style": "rounded corners 8px, blue fill, white text"},
            {"name": "card", "style": "white bg, 1px border #e0e0e0, 4px shadow"},
        ],
    }


class TestParseDesign:
    def test_parses_valid_json(self):
        raw = json.dumps(_sample_response())
        result = _parse_design(raw)
        assert result.colors["primary"] == "#1a73e8"
        assert result.fonts["body"] == "Inter, ~16px"
        assert result.layout["navigation"] == "top"
        assert len(result.components) == 2

    def test_strips_json_code_fence(self):
        raw = "```json\n" + json.dumps(_sample_response()) + "\n```"
        result = _parse_design(raw)
        assert result.colors["primary"] == "#1a73e8"

    def test_strips_plain_code_fence(self):
        raw = "```\n" + json.dumps({"colors": {"bg": "#fff"}}) + "\n```"
        result = _parse_design(raw)
        assert result.colors["bg"] == "#fff"

    def test_returns_empty_on_invalid_json(self):
        result = _parse_design("not json at all")
        assert result.colors == {}
        assert result.fonts == {}

    def test_returns_empty_on_json_array(self):
        result = _parse_design("[]")
        assert result.colors == {}

    def test_handles_missing_keys(self):
        raw = json.dumps({"colors": {"primary": "#000"}})
        result = _parse_design(raw)
        assert result.colors == {"primary": "#000"}
        assert result.fonts == {}
        assert result.components == []


class TestExtractDesign:
    def _make_client(self, response_text: str) -> MagicMock:
        content_block = MagicMock()
        content_block.text = response_text
        message = MagicMock()
        message.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = message
        return client

    def test_returns_empty_for_no_images(self):
        result = extract_design([])
        assert result.colors == {}
        assert result.source_images == []

    def test_extracts_design_from_images(self, tmp_path):
        img = _make_image(tmp_path, "screenshot.png")
        response = json.dumps(_sample_response())
        client = self._make_client(response)
        result = extract_design([img], client=client)
        assert result.colors["primary"] == "#1a73e8"
        assert result.source_images == ["screenshot.png"]
        client.messages.create.assert_called_once()

    def test_sends_images_as_base64(self, tmp_path):
        img1 = _make_image(tmp_path, "a.png")
        img2 = _make_image(tmp_path, "b.jpg")
        client = self._make_client(json.dumps({"colors": {}}))
        extract_design([img1, img2], client=client)

        call_kwargs = client.messages.create.call_args
        user_content = call_kwargs.kwargs["messages"][0]["content"]
        image_blocks = [b for b in user_content if b.get("type") == "image"]
        assert len(image_blocks) == 2
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        assert image_blocks[1]["source"]["media_type"] == "image/jpeg"

    def test_limits_to_max_images(self, tmp_path):
        images = [_make_image(tmp_path, f"img{i}.png") for i in range(15)]
        client = self._make_client(json.dumps({"colors": {}}))
        result = extract_design(images, client=client)
        assert len(result.source_images) == 10

    def test_creates_default_client_when_none(self, tmp_path):
        img = _make_image(tmp_path, "test.png")
        response = json.dumps({"colors": {"bg": "#fff"}})
        mock_client = self._make_client(response)
        with patch(
            "duplo.design_extractor.anthropic.Anthropic",
            return_value=mock_client,
        ):
            result = extract_design([img])
        assert result.colors["bg"] == "#fff"

    def test_returns_empty_on_bad_response(self, tmp_path):
        img = _make_image(tmp_path, "test.png")
        client = self._make_client("I cannot analyse this image.")
        result = extract_design([img], client=client)
        assert result.colors == {}

    def test_uses_expected_model(self, tmp_path):
        img = _make_image(tmp_path, "test.png")
        client = self._make_client(json.dumps({"colors": {}}))
        extract_design([img], client=client)
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"


class TestFormatDesignSection:
    def test_formats_full_design(self):
        design = DesignRequirements(
            colors={"primary": "#1a73e8", "background": "#ffffff"},
            fonts={"headings": "Inter Bold, ~24px"},
            spacing={"content_padding": "24px"},
            layout={"navigation": "top"},
            components=[{"name": "button", "style": "rounded 8px"}],
        )
        result = format_design_section(design)
        assert "## Visual Design Requirements" in result
        assert "`#1a73e8`" in result
        assert "Inter Bold" in result
        assert "content_padding" in result
        assert "navigation" in result
        assert "button" in result

    def test_returns_empty_for_empty_design(self):
        design = DesignRequirements()
        assert format_design_section(design) == ""

    def test_includes_only_available_sections(self):
        design = DesignRequirements(colors={"primary": "#000"})
        result = format_design_section(design)
        assert "### Colors" in result
        assert "### Typography" not in result
        assert "### Spacing" not in result

    def test_formats_components_correctly(self):
        design = DesignRequirements(
            colors={"primary": "#000"},
            components=[
                {"name": "card", "style": "white bg, shadow"},
                {"name": "input", "style": "border 1px gray"},
            ],
        )
        result = format_design_section(design)
        assert "**card**" in result
        assert "**input**" in result
