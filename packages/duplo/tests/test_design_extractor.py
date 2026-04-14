"""Tests for duplo.design_extractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from duplo.claude_cli import ClaudeCliError
from duplo.design_extractor import (
    DesignRequirements,
    _parse_design,
    collect_design_input,
    extract_design,
    format_design_block,
    format_design_section,
)
from duplo.spec_reader import ProductSpec, ReferenceEntry


# Minimal valid 1x1 PNG bytes for test images.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _unique_bytes(tag: str) -> bytes:
    """Return bytes unique to *tag* for content-hash dedup testing."""
    return _PNG_BYTES + tag.encode()


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


class TestCollectDesignInput:
    def test_visual_references_from_spec(self, tmp_path):
        img = tmp_path / "ref" / "screenshot.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(_PNG_BYTES)
        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/screenshot.png"), roles=["visual-target"]),
            ]
        )
        result = collect_design_input(spec, target_dir=tmp_path)
        assert len(result) == 1
        assert result[0] == tmp_path / "ref" / "screenshot.png"

    def test_excludes_proposed_visual_references(self, tmp_path):
        img = tmp_path / "ref" / "proposed.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(_PNG_BYTES)
        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.png"),
                    roles=["visual-target"],
                    proposed=True,
                ),
            ]
        )
        result = collect_design_input(spec, target_dir=tmp_path)
        assert result == []

    def test_visual_target_frames_included(self, tmp_path):
        frame = tmp_path / "frame1.png"
        frame.write_bytes(_PNG_BYTES)
        result = collect_design_input(None, visual_target_frames=[frame])
        assert result == [frame]

    def test_site_images_included(self, tmp_path):
        img = tmp_path / "site_img.png"
        img.write_bytes(_PNG_BYTES)
        result = collect_design_input(None, site_images=[img])
        assert result == [img]

    def test_site_video_frames_included(self, tmp_path):
        frame = tmp_path / "site_frame.png"
        frame.write_bytes(_PNG_BYTES)
        result = collect_design_input(None, site_video_frames=[frame])
        assert result == [frame]

    def test_union_of_all_four_sources(self, tmp_path):
        ref_img = tmp_path / "ref" / "target.png"
        ref_img.parent.mkdir(parents=True)
        ref_img.write_bytes(_unique_bytes("target"))
        vt_frame = tmp_path / "vt_frame.png"
        vt_frame.write_bytes(_unique_bytes("vt"))
        site_img = tmp_path / "site.png"
        site_img.write_bytes(_unique_bytes("site"))
        sv_frame = tmp_path / "sv_frame.png"
        sv_frame.write_bytes(_unique_bytes("sv"))

        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/target.png"), roles=["visual-target"]),
            ]
        )
        result = collect_design_input(
            spec,
            visual_target_frames=[vt_frame],
            site_images=[site_img],
            site_video_frames=[sv_frame],
            target_dir=tmp_path,
        )
        assert len(result) == 4
        assert result[0] == tmp_path / "ref" / "target.png"
        assert result[1] == vt_frame
        assert result[2] == site_img
        assert result[3] == sv_frame

    def test_deduplicates_by_resolved_path(self, tmp_path):
        img = tmp_path / "ref" / "same.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(_PNG_BYTES)
        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/same.png"), roles=["visual-target"]),
            ]
        )
        # Pass the same image as both a visual reference and a site image.
        result = collect_design_input(
            spec,
            site_images=[tmp_path / "ref" / "same.png"],
            target_dir=tmp_path,
        )
        assert len(result) == 1

    def test_no_spec_returns_other_sources(self, tmp_path):
        frame = tmp_path / "frame.png"
        frame.write_bytes(_PNG_BYTES)
        result = collect_design_input(None, visual_target_frames=[frame])
        assert result == [frame]

    def test_empty_inputs_returns_empty(self):
        result = collect_design_input(None)
        assert result == []

    def test_proposed_excluded_from_union_with_other_sources(self, tmp_path):
        """Proposed visual-target refs are excluded even when other
        (non-ref) sources are present — only source (1) is filtered."""
        proposed_img = tmp_path / "ref" / "proposed.png"
        proposed_img.parent.mkdir(parents=True)
        proposed_img.write_bytes(_unique_bytes("proposed"))
        real_img = tmp_path / "ref" / "real.png"
        real_img.write_bytes(_unique_bytes("real"))
        vt_frame = tmp_path / "vt_frame.png"
        vt_frame.write_bytes(_unique_bytes("vt"))
        site_img = tmp_path / "site.png"
        site_img.write_bytes(_unique_bytes("site"))
        sv_frame = tmp_path / "sv_frame.png"
        sv_frame.write_bytes(_unique_bytes("sv"))

        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.png"),
                    roles=["visual-target"],
                    proposed=True,
                ),
                ReferenceEntry(
                    path=Path("ref/real.png"),
                    roles=["visual-target"],
                ),
            ]
        )
        result = collect_design_input(
            spec,
            visual_target_frames=[vt_frame],
            site_images=[site_img],
            site_video_frames=[sv_frame],
            target_dir=tmp_path,
        )
        # proposed.png excluded, real.png + 3 other sources included.
        assert len(result) == 4
        names = [p.name for p in result]
        assert "proposed.png" not in names
        assert "real.png" in names
        assert "vt_frame.png" in names
        assert "site.png" in names
        assert "sv_frame.png" in names

    def test_all_proposed_refs_yields_only_other_sources(self, tmp_path):
        """When ALL visual-target refs are proposed, only sources (2)-(4)
        contribute."""
        proposed = tmp_path / "ref" / "p.png"
        proposed.parent.mkdir(parents=True)
        proposed.write_bytes(_PNG_BYTES)
        vt_frame = tmp_path / "frame.png"
        vt_frame.write_bytes(_PNG_BYTES)

        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/p.png"),
                    roles=["visual-target"],
                    proposed=True,
                ),
            ]
        )
        result = collect_design_input(
            spec,
            visual_target_frames=[vt_frame],
            target_dir=tmp_path,
        )
        assert len(result) == 1
        assert result[0] == vt_frame

    def test_non_visual_target_refs_excluded(self, tmp_path):
        img = tmp_path / "ref" / "behavioral.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(_PNG_BYTES)
        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/behavioral.png"),
                    roles=["behavioral-target"],
                ),
            ]
        )
        result = collect_design_input(spec, target_dir=tmp_path)
        assert result == []

    def test_content_hash_dedup_ref_wins(self, tmp_path):
        """When a ref-declared frame and a site video frame have identical
        content but different paths, the ref-declared frame wins (added
        first)."""
        ref_img = tmp_path / "ref" / "demo.png"
        ref_img.parent.mkdir(parents=True)
        ref_img.write_bytes(_PNG_BYTES)
        # Same content at a different path (scraped copy).
        scraped_frame = tmp_path / "scraped_frame.png"
        scraped_frame.write_bytes(_PNG_BYTES)

        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/demo.png"), roles=["visual-target"]),
            ]
        )
        result = collect_design_input(
            spec,
            site_video_frames=[scraped_frame],
            target_dir=tmp_path,
        )
        assert len(result) == 1
        assert result[0] == tmp_path / "ref" / "demo.png"

    def test_content_hash_dedup_across_sources(self, tmp_path):
        """Identical content across sources (2) and (4) is deduplicated."""
        frame_a = tmp_path / "vt_frame.png"
        frame_a.write_bytes(_PNG_BYTES)
        frame_b = tmp_path / "sv_frame.png"
        frame_b.write_bytes(_PNG_BYTES)

        result = collect_design_input(
            None,
            visual_target_frames=[frame_a],
            site_video_frames=[frame_b],
        )
        assert len(result) == 1
        assert result[0] == frame_a

    def test_content_hash_dedup_different_content_kept(self, tmp_path):
        """Frames with different content at different paths are all kept."""
        frame_a = tmp_path / "a.png"
        frame_a.write_bytes(_PNG_BYTES)
        frame_b = tmp_path / "b.png"
        # Different content.
        frame_b.write_bytes(_PNG_BYTES + b"\x00")

        result = collect_design_input(
            None,
            visual_target_frames=[frame_a],
            site_video_frames=[frame_b],
        )
        assert len(result) == 2

    def test_content_hash_dedup_unreadable_file_still_added(self, tmp_path):
        """If a file can't be read (OSError), it's still added (no hash
        dedup, only path dedup)."""
        frame = tmp_path / "ghost.png"
        frame.write_bytes(_PNG_BYTES)
        # A second file that doesn't exist on disk.
        missing = tmp_path / "missing.png"

        result = collect_design_input(
            None,
            visual_target_frames=[frame],
            site_video_frames=[missing],
        )
        # frame is added; missing is added too (OSError skips hash check).
        assert len(result) == 2

    def test_order_is_deterministic(self, tmp_path):
        """Sources are appended in order: (1) refs, (2) vt frames,
        (3) site images, (4) site video frames."""
        ref_img = tmp_path / "ref" / "a.png"
        ref_img.parent.mkdir(parents=True)
        ref_img.write_bytes(_unique_bytes("a"))
        vt = tmp_path / "b.png"
        vt.write_bytes(_unique_bytes("b"))
        si = tmp_path / "c.png"
        si.write_bytes(_unique_bytes("c"))
        sv = tmp_path / "d.png"
        sv.write_bytes(_unique_bytes("d"))

        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"]),
            ]
        )
        result = collect_design_input(
            spec,
            visual_target_frames=[vt],
            site_images=[si],
            site_video_frames=[sv],
            target_dir=tmp_path,
        )
        names = [p.name for p in result]
        assert names == ["a.png", "b.png", "c.png", "d.png"]


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
