"""Phase 5 end-to-end integration tests.

Each test constructs a fixture project in a tmpdir, runs duplo's pipeline
programmatically, and asserts on the output state.  Tests must NOT make
real HTTP requests — fetch_site is always mocked.  Vision/LLM calls are
also mocked so tests don't depend on network or claude -p availability.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from duplo.design_extractor import DesignRequirements
from duplo.doc_tables import DocStructures
from duplo.fetcher import PageRecord

# ---------------------------------------------------------------------------
# Shared fixtures for the URL-only spec integration test
# ---------------------------------------------------------------------------

# Canonical URL matching the SPEC.md entry in TestMinimalUrlOnlySpec
_CANONICAL_URL = "https://notesapp.example.com"

# Small scraped text — enough to be non-empty but deterministic
_SCRAPED_TEXT = (
    "NotesApp is a cross-platform note-taking application. "
    "Features include real-time collaboration and end-to-end encryption."
)

# HTML fixture with one same-origin link and one cross-origin link
_RAW_HTML = (
    "<html><head><title>NotesApp</title></head><body>"
    "<h1>NotesApp</h1>"
    "<p>A note-taking app with real-time collaboration.</p>"
    '<a href="/docs/getting-started">Getting Started</a>'
    '<a href="https://other-domain.example.org/blog">Blog</a>'
    "</body></html>"
)

_CONTENT_HASH = hashlib.sha256(_RAW_HTML.encode()).hexdigest()

_PAGE_RECORD = PageRecord(
    url=_CANONICAL_URL,
    fetched_at="2026-04-14T00:00:00Z",
    content_hash=_CONTENT_HASH,
)

# The 5-tuple that fetch_site returns
_FETCH_SITE_RESULT = (
    _SCRAPED_TEXT,
    [],  # empty code_examples
    DocStructures(),  # empty doc_structures
    [_PAGE_RECORD],  # one PageRecord
    {_CANONICAL_URL: _RAW_HTML},  # raw_pages keyed by canonical URL
)


class TestFetchSiteFixture:
    """Verify the fetch_site fixture 5-tuple is well-formed."""

    def test_tuple_length(self):
        assert len(_FETCH_SITE_RESULT) == 5

    def test_scraped_text_non_empty(self):
        assert len(_FETCH_SITE_RESULT[0]) > 0

    def test_code_examples_empty(self):
        assert _FETCH_SITE_RESULT[1] == []

    def test_doc_structures_empty(self):
        ds = _FETCH_SITE_RESULT[2]
        assert isinstance(ds, DocStructures)
        assert not ds  # falsy when all lists are empty

    def test_single_page_record(self):
        records = _FETCH_SITE_RESULT[3]
        assert len(records) == 1
        assert records[0].url == _CANONICAL_URL

    def test_page_record_content_hash(self):
        record = _FETCH_SITE_RESULT[3][0]
        assert record.content_hash == _CONTENT_HASH

    def test_raw_pages_keyed_by_canonical_url(self):
        raw_pages = _FETCH_SITE_RESULT[4]
        assert _CANONICAL_URL in raw_pages
        assert len(raw_pages) == 1

    def test_raw_html_has_same_origin_link(self):
        html = _FETCH_SITE_RESULT[4][_CANONICAL_URL]
        assert 'href="/docs/getting-started"' in html

    def test_raw_html_has_cross_origin_link(self):
        html = _FETCH_SITE_RESULT[4][_CANONICAL_URL]
        assert 'href="https://other-domain.example.org/blog"' in html

    def test_record_and_raw_pages_in_sync(self):
        """Every PageRecord URL has a corresponding raw_pages entry."""
        records = _FETCH_SITE_RESULT[3]
        raw_pages = _FETCH_SITE_RESULT[4]
        for rec in records:
            assert rec.url in raw_pages


# ---------------------------------------------------------------------------
# Design extractor fixture
# ---------------------------------------------------------------------------

_DESIGN_REQUIREMENTS = DesignRequirements(
    colors={
        "primary": "#1a73e8",
        "secondary": "#5f6368",
        "background": "#ffffff",
        "text": "#202124",
        "accent": "#fbbc04",
    },
    fonts={
        "headings": "Inter, sans-serif, ~20px",
        "body": "Inter, sans-serif, ~14px",
        "mono": "Roboto Mono, monospace, ~13px",
    },
    spacing={
        "content_padding": "24px",
        "section_gap": "32px",
        "element_gap": "12px",
    },
    layout={
        "navigation": "top",
        "sidebar": "left",
        "content_width": "medium",
        "grid": "single-column content area with sidebar",
    },
    components=[
        {"name": "card", "style": "8px border-radius, subtle shadow"},
        {"name": "button", "style": "4px border-radius, solid fill"},
        {"name": "input field", "style": "1px border, 4px radius"},
    ],
    source_images=["screenshot1.png", "screenshot2.png"],
)


class TestDesignFixture:
    """Verify the DesignRequirements fixture is well-formed."""

    def test_colors_has_expected_keys(self):
        expected = {"primary", "secondary", "background", "text", "accent"}
        assert set(_DESIGN_REQUIREMENTS.colors.keys()) == expected

    def test_colors_are_hex_strings(self):
        for val in _DESIGN_REQUIREMENTS.colors.values():
            assert val.startswith("#")
            assert len(val) == 7

    def test_fonts_has_expected_keys(self):
        assert set(_DESIGN_REQUIREMENTS.fonts.keys()) == {
            "headings",
            "body",
            "mono",
        }

    def test_spacing_has_expected_keys(self):
        assert set(_DESIGN_REQUIREMENTS.spacing.keys()) == {
            "content_padding",
            "section_gap",
            "element_gap",
        }

    def test_layout_has_expected_keys(self):
        expected = {"navigation", "sidebar", "content_width", "grid"}
        assert set(_DESIGN_REQUIREMENTS.layout.keys()) == expected

    def test_components_non_empty(self):
        assert len(_DESIGN_REQUIREMENTS.components) == 3

    def test_components_have_name_and_style(self):
        for comp in _DESIGN_REQUIREMENTS.components:
            assert "name" in comp
            assert "style" in comp

    def test_source_images_non_empty(self):
        assert len(_DESIGN_REQUIREMENTS.source_images) == 2


class TestDesignMockWiring:
    """Verify the fixture can be used as a mock return value."""

    def test_mock_returns_fixture(self):
        with patch(
            "duplo.design_extractor.extract_design",
            return_value=_DESIGN_REQUIREMENTS,
        ) as mock_design:
            from duplo.design_extractor import extract_design

            result = extract_design([])

        mock_design.assert_called_once()
        assert isinstance(result, DesignRequirements)
        assert result.colors["primary"] == "#1a73e8"
        assert len(result.components) == 3
        assert result.source_images == [
            "screenshot1.png",
            "screenshot2.png",
        ]


class TestFetchSiteMockWiring:
    """Verify the fixture can be used as a mock return value."""

    def test_mock_returns_fixture(self):
        """Patching fetch_site with the fixture works end-to-end."""
        with patch(
            "duplo.fetcher.fetch_site",
            return_value=_FETCH_SITE_RESULT,
        ) as mock_fetch:
            from duplo.fetcher import fetch_site

            result = fetch_site("https://notesapp.example.com")

        mock_fetch.assert_called_once()
        text, examples, doc_structs, records, raw_pages = result
        assert text == _SCRAPED_TEXT
        assert examples == []
        assert isinstance(doc_structs, DocStructures)
        assert len(records) == 1
        assert _CANONICAL_URL in raw_pages
