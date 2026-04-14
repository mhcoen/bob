"""Tests for duplo.orchestrator."""

from duplo.orchestrator import _collect_cross_origin_links


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
