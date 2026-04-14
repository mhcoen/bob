"""Tests for duplo.orchestrator."""

from pathlib import Path

from duplo.orchestrator import (
    _accepted_frames_by_source,
    _collect_cross_origin_links,
    collect_design_input,
)
from duplo.spec_reader import ProductSpec, ReferenceEntry
from duplo.video_extractor import ExtractionResult


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

    def test_dual_role_behavioral_visual_ref_included(self, tmp_path):
        """A ref with BOTH behavioral-target and visual-target roles is
        included because it has visual-target."""
        img = tmp_path / "ref" / "dual.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(_PNG_BYTES)
        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/dual.png"),
                    roles=["behavioral-target", "visual-target"],
                ),
            ]
        )
        result = collect_design_input(spec, target_dir=tmp_path)
        assert len(result) == 1
        assert result[0] == tmp_path / "ref" / "dual.png"

    def test_dual_role_video_contributes_accepted_frames(self, tmp_path):
        """Accepted frames from a dual-role (behavioral + visual) video
        are included via the visual_target_frames parameter."""
        frame = tmp_path / "dual_video_frame.png"
        frame.write_bytes(_PNG_BYTES)
        result = collect_design_input(None, visual_target_frames=[frame])
        assert result == [frame]

    def test_behavioral_only_video_not_in_design(self, tmp_path):
        """Frames from a behavioral-only video are NOT passed as
        visual_target_frames, so they do not appear in design input.
        Only site_video_frames (scraped) and visual_target_frames
        contribute frames."""
        behavioral_frame = tmp_path / "behavioral_frame.png"
        behavioral_frame.write_bytes(_unique_bytes("beh"))
        visual_frame = tmp_path / "visual_frame.png"
        visual_frame.write_bytes(_unique_bytes("vis"))
        # behavioral_frame is intentionally NOT passed — the caller
        # filters it out because its source video lacks visual-target.
        result = collect_design_input(None, visual_target_frames=[visual_frame])
        assert len(result) == 1
        assert result[0] == visual_frame

    def test_non_product_reference_scraped_video_excluded(self, tmp_path):
        """Frames from non-product-reference scraped videos are NOT
        passed as site_video_frames. Only product-reference scraped
        frames contribute to design input."""
        product_frame = tmp_path / "product_frame.png"
        product_frame.write_bytes(_unique_bytes("prod"))
        non_product_frame = tmp_path / "non_product_frame.png"
        non_product_frame.write_bytes(_unique_bytes("nonp"))
        # Only product_frame is passed; non_product_frame is filtered
        # out by the caller because it's not from a product-reference.
        result = collect_design_input(None, site_video_frames=[product_frame])
        assert len(result) == 1
        assert result[0] == product_frame

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


class TestAcceptedFramesBySource:
    """Tests for _accepted_frames_by_source."""

    def test_lookup_returns_correct_frames_per_source(self, tmp_path):
        """Each source maps to its own list of frames."""
        v1 = tmp_path / "video1.mp4"
        v2 = tmp_path / "video2.mp4"
        f1a = tmp_path / "v1_frame_a.png"
        f1b = tmp_path / "v1_frame_b.png"
        f2a = tmp_path / "v2_frame_a.png"
        results = [
            ExtractionResult(source=v1, frames=[f1a, f1b]),
            ExtractionResult(source=v2, frames=[f2a]),
        ]
        lookup = _accepted_frames_by_source(results)
        assert lookup[v1] == [f1a, f1b]
        assert lookup[v2] == [f2a]
        assert len(lookup) == 2

    def test_unfiltered_results_expose_rejected_frames(self, tmp_path):
        """If called with unfiltered results, rejected frames appear
        in output — demonstrating the contract violation is detectable."""
        vid = tmp_path / "demo.mp4"
        kept = tmp_path / "good.png"
        rejected = tmp_path / "blurry.png"
        # Unfiltered: both frames still present.
        results = [
            ExtractionResult(source=vid, frames=[kept, rejected]),
        ]
        lookup = _accepted_frames_by_source(results)
        assert rejected in lookup[vid]

    def test_source_path_preservation(self):
        """Keys equal the input ExtractionResult.source values
        byte-for-byte — no path transformation."""
        # Use a relative path string to verify no resolve() happens.
        relative = Path("some/relative/path/video.mp4")
        frame = Path("some/relative/path/frame_001.png")
        results = [ExtractionResult(source=relative, frames=[frame])]
        lookup = _accepted_frames_by_source(results)
        # Key must be the exact same Path object, not a resolved variant.
        assert relative in lookup
        assert lookup[relative] == [frame]

    def test_empty_input_returns_empty_dict(self):
        lookup = _accepted_frames_by_source([])
        assert lookup == {}

    def test_source_with_empty_frames(self, tmp_path):
        """A source whose frames were all filtered out maps to []."""
        vid = tmp_path / "all_rejected.mp4"
        results = [ExtractionResult(source=vid, frames=[])]
        lookup = _accepted_frames_by_source(results)
        assert lookup[vid] == []
