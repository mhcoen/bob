"""Tests for duplo.orchestrator."""

from pathlib import Path

from duplo.orchestrator import _collect_cross_origin_links, collect_design_input
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


class TestCollectCrossOriginLinks:
    """Tests for _collect_cross_origin_links."""

    def test_same_origin_excluded(self):
        html = '<a href="https://example.com/about">About</a>'
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == []

    def test_cross_origin_included(self):
        html = '<a href="https://other.com/page">Other</a>'
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == ["https://other.com/page"]

    def test_subdomain_is_cross_origin(self):
        html = '<a href="https://docs.numi.app/guide">Docs</a>'
        result = _collect_cross_origin_links("https://numi.app", {"https://numi.app": html})
        assert result == ["https://docs.numi.app/guide"]

    def test_only_a_href_collected(self):
        """<img src> to cross-origin CDN is NOT collected."""
        html = (
            '<img src="https://cdn.example.com/logo.png">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css">'
            '<script src="https://analytics.example.com/track.js"></script>'
            '<a href="https://partner.com">Partner</a>'
        )
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == ["https://partner.com"]

    def test_duplicates_within_page_collapsed(self):
        html = (
            '<a href="https://other.com/page">Link 1</a>'
            '<a href="https://other.com/page">Link 2</a>'
        )
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == ["https://other.com/page"]

    def test_duplicates_across_pages_collapsed(self):
        page1 = '<a href="https://other.com/page">Link</a>'
        page2 = '<a href="https://other.com/page">Link</a>'
        result = _collect_cross_origin_links(
            "https://example.com",
            {
                "https://example.com": page1,
                "https://example.com/about": page2,
            },
        )
        assert result == ["https://other.com/page"]

    def test_canonicalization_collapses_variants(self):
        """Uppercase host and trailing slash collapse to one entry."""
        html = (
            '<a href="https://OTHER.com/page/">Link 1</a>'
            '<a href="https://other.com/page">Link 2</a>'
        )
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == ["https://other.com/page"]

    def test_empty_raw_pages(self):
        result = _collect_cross_origin_links("https://example.com", {})
        assert result == []

    def test_relative_href_resolved_against_page_url(self):
        """href="docs" on /foo/page.html resolves to /foo/docs, not /docs."""
        html = '<a href="docs">Docs</a>'
        result = _collect_cross_origin_links(
            "https://example.com",
            {"https://other.com/foo/page.html": html},
        )
        assert "https://other.com/foo/docs" in result

    def test_relative_href_not_resolved_against_source_url(self):
        """Relative hrefs resolve against the page URL, not source_url."""
        html = '<a href="docs">Docs</a>'
        # Page is on other.com/foo/page.html, source is example.com.
        # The relative "docs" should resolve to other.com/foo/docs,
        # which is same-origin with the page but cross-origin to source.
        result = _collect_cross_origin_links(
            "https://example.com",
            {"https://other.com/foo/page.html": html},
        )
        assert "https://other.com/foo/docs" in result
        # Must NOT resolve to example.com/docs
        assert "https://example.com/docs" not in result

    def test_fragment_only_links_skipped(self):
        html = '<a href="#section">Jump</a>'
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == []

    def test_mailto_and_javascript_skipped(self):
        html = '<a href="mailto:test@example.com">Email</a><a href="javascript:void(0)">Click</a>'
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == []

    def test_fragment_stripped_from_cross_origin(self):
        """Fragment is stripped before canonicalization."""
        html = '<a href="https://other.com/page#section">Link</a>'
        result = _collect_cross_origin_links("https://example.com", {"https://example.com": html})
        assert result == ["https://other.com/page"]


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
        missing = tmp_path / "missing.png"

        result = collect_design_input(
            None,
            visual_target_frames=[frame],
            site_video_frames=[missing],
        )
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
