"""Tests for duplo.design_extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from duplo.claude_cli import ClaudeCliError
from duplo.design_extractor import (
    DesignRequirements,
    _parse_design,
    extract_design,
    format_design_block,
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
    def test_returns_empty_for_no_images(self):
        result = extract_design([])
        assert result.colors == {}
        assert result.source_images == []

    def test_extracts_design_from_images(self, tmp_path):
        img = _make_image(tmp_path, "screenshot.png")
        response = json.dumps(_sample_response())
        with patch("duplo.design_extractor.query_with_images", return_value=response):
            result = extract_design([img])
        assert result.colors["primary"] == "#1a73e8"
        assert result.source_images == ["screenshot.png"]

    def test_passes_image_paths(self, tmp_path):
        img1 = _make_image(tmp_path, "a.png")
        img2 = _make_image(tmp_path, "b.jpg")
        with patch(
            "duplo.design_extractor.query_with_images",
            return_value=json.dumps({"colors": {}}),
        ) as mock_q:
            extract_design([img1, img2])
        image_paths = mock_q.call_args[0][1]
        assert len(image_paths) == 2
        assert image_paths[0] == img1
        assert image_paths[1] == img2

    def test_limits_to_max_images(self, tmp_path):
        images = [_make_image(tmp_path, f"img{i}.png") for i in range(15)]
        with patch(
            "duplo.design_extractor.query_with_images",
            return_value=json.dumps({"colors": {}}),
        ) as mock_q:
            result = extract_design(images)
        assert len(result.source_images) == 10
        image_paths = mock_q.call_args[0][1]
        assert len(image_paths) == 10

    def test_returns_empty_on_bad_response(self, tmp_path):
        img = _make_image(tmp_path, "test.png")
        with patch(
            "duplo.design_extractor.query_with_images",
            return_value="I cannot analyse this image.",
        ):
            result = extract_design([img])
        assert result.colors == {}

    def test_returns_empty_on_claude_cli_error(self, tmp_path):
        img = _make_image(tmp_path, "test.png")
        with patch(
            "duplo.design_extractor.query_with_images",
            side_effect=ClaudeCliError("claude CLI timed out"),
        ):
            result = extract_design([img])
        assert result.colors == {}
        assert result.fonts == {}
        assert result.source_images == ["test.png"]


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


class TestFormatDesignBlock:
    def test_returns_body_without_heading(self):
        design = DesignRequirements(
            colors={"primary": "#1a73e8"},
            fonts={"body": "Inter, ~16px"},
        )
        result = format_design_block(design)
        assert "## Visual Design Requirements" not in result
        assert "### Colors" in result
        assert "`#1a73e8`" in result
        assert "### Typography" in result

    def test_returns_empty_for_empty_design(self):
        design = DesignRequirements()
        assert format_design_block(design) == ""

    def test_matches_format_design_section_content(self):
        design = DesignRequirements(
            colors={"primary": "#000", "bg": "#fff"},
            layout={"navigation": "top"},
            components=[{"name": "btn", "style": "rounded"}],
        )
        section = format_design_section(design)
        block = format_design_block(design)
        # Block should contain all section content except the heading.
        for line in block.splitlines():
            assert line in section

    def test_no_leading_blank_lines(self):
        design = DesignRequirements(colors={"primary": "#000"})
        result = format_design_block(design)
        assert not result.startswith("\n")

    def test_roundtrip_through_update_design_autogen(self):
        """update_design_autogen + spec parser recovers design fields."""
        from duplo.spec_reader import _parse_design_block
        from duplo.spec_writer import update_design_autogen

        design = DesignRequirements(
            colors={"primary": "#1a73e8", "bg": "#ffffff"},
            fonts={"body": "Inter, ~16px"},
            spacing={"gap": "16px"},
            layout={"navigation": "top"},
            components=[{"name": "card", "style": "rounded"}],
        )
        spec_text = "# My App\n\n## Purpose\n\nDo stuff.\n"
        block = format_design_block(design)
        modified = update_design_autogen(spec_text, block)
        # Parse just the ## Design body out of the modified text.
        # Find section between ## Design and next ## heading (or EOF).
        import re

        m = re.search(
            r"^## Design\n(.*?)(?=^## |\Z)",
            modified,
            re.MULTILINE | re.DOTALL,
        )
        assert m, "## Design section not found in modified spec"
        parsed = _parse_design_block(m.group(1))
        assert "### Colors" in parsed.auto_generated
        assert "`#1a73e8`" in parsed.auto_generated
        assert "### Typography" in parsed.auto_generated
        assert "Inter, ~16px" in parsed.auto_generated
        assert "### Spacing" in parsed.auto_generated
        assert "### Layout" in parsed.auto_generated
        assert "### Component Styles" in parsed.auto_generated
        assert "card" in parsed.auto_generated
