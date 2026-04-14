"""Tests for duplo.spec_writer."""

from duplo.spec_reader import SourceEntry
from duplo.spec_writer import append_sources


class TestAppendSources:
    """Tests for append_sources."""

    def test_append_single_entry(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://other.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert "- https://other.com" in result
        assert "  role: docs" in result
        assert "  scrape: shallow" in result
        # Original entry still present.
        assert "- https://example.com" in result

    def test_append_multiple_entries(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entries = [
            SourceEntry(url="https://a.com", role="docs", scrape="shallow"),
            SourceEntry(url="https://b.com", role="docs", scrape="none"),
        ]
        result = append_sources(spec, entries)
        assert "- https://a.com" in result
        assert "- https://b.com" in result

    def test_dedup_existing_canonical_url(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_dedup_trailing_slash(self):
        """Canonicalization strips trailing slash — dedup catches it."""
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://example.com/",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_dedup_case_insensitive_host(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://EXAMPLE.COM",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_idempotent_double_call(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(url="https://new.com", role="docs", scrape="shallow")
        first = append_sources(spec, [entry])
        second = append_sources(first, [entry])
        assert first == second

    def test_empty_new_entries_returns_unchanged(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        result = append_sources(spec, [])
        assert result == spec

    def test_missing_sources_section_created(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        assert "## Sources" in result
        assert "- https://example.com" in result
        assert "  role: product-reference" in result

    def test_missing_sources_placed_after_architecture(self):
        spec = "## Purpose\n\nBuild a calculator.\n\n## Architecture\n\nSwiftUI app.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        # Sources should come after Architecture.
        arch_pos = result.index("## Architecture")
        sources_pos = result.index("## Sources")
        assert sources_pos > arch_pos

    def test_flags_discovered_true(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://found.com",
            role="docs",
            scrape="shallow",
            discovered=True,
        )
        result = append_sources(spec, [entry])
        assert "  discovered: true" in result

    def test_flags_proposed_true(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://suggested.com",
            role="product-reference",
            scrape="deep",
            proposed=True,
        )
        result = append_sources(spec, [entry])
        assert "  proposed: true" in result

    def test_flags_both_proposed_and_discovered(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://both.com",
            role="docs",
            scrape="none",
            proposed=True,
            discovered=True,
        )
        result = append_sources(spec, [entry])
        assert "  proposed: true" in result
        assert "  discovered: true" in result

    def test_no_flags_when_false(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://clean.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert "proposed" not in result
        assert "discovered" not in result

    def test_notes_included(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://noted.com",
            role="docs",
            scrape="shallow",
            notes="main docs site",
        )
        result = append_sources(spec, [entry])
        assert "  notes: main docs site" in result

    def test_dedup_against_proposed_existing(self):
        """Dedup ignores flags — even a proposed entry blocks duplicates."""
        spec = (
            "## Sources\n\n"
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "  proposed: true\n"
        )
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_missing_sources_at_end_without_architecture(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        # Purpose content should come before Sources.
        purpose_pos = result.index("## Purpose")
        sources_pos = result.index("## Sources")
        assert sources_pos > purpose_pos

    def test_sources_after_architecture_before_next_section(self):
        spec = (
            "## Purpose\n\nBuild a calculator.\n\n"
            "## Architecture\n\nSwiftUI app.\n\n"
            "## Design\n\nMinimal.\n"
        )
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        arch_pos = result.index("## Architecture")
        sources_pos = result.index("## Sources")
        design_pos = result.index("## Design")
        assert arch_pos < sources_pos < design_pos
