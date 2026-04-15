"""Phase 5 end-to-end integration tests.

Each test constructs a fixture project in a tmpdir, runs duplo's pipeline
programmatically, and asserts on the output state.  Tests must NOT make
real HTTP requests — fetch_site is always mocked.  Vision/LLM calls are
also mocked so tests don't depend on network or claude -p availability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from duplo.design_extractor import DesignRequirements
from duplo.doc_tables import DocStructures
from duplo.extractor import Feature
from duplo.fetcher import PageRecord
from duplo.main import ScrapeResult, main

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


# ---------------------------------------------------------------------------
# Feature extractor fixture
# ---------------------------------------------------------------------------

_FEATURES = [
    Feature(
        name="Real-time collaboration",
        description="Multiple users can edit the same note simultaneously "
        "with live cursor tracking and conflict resolution.",
        category="Collaboration",
    ),
    Feature(
        name="End-to-end encryption",
        description="All notes are encrypted on the client before syncing, "
        "ensuring only the note owner can read the content.",
        category="Security",
    ),
]


class TestFeatureFixture:
    """Verify the Feature fixture list is well-formed."""

    def test_fixture_length(self):
        assert len(_FEATURES) == 2

    def test_features_are_feature_instances(self):
        for feat in _FEATURES:
            assert isinstance(feat, Feature)

    def test_names_are_distinct(self):
        names = [f.name for f in _FEATURES]
        assert len(names) == len(set(names))

    def test_descriptions_non_empty(self):
        for feat in _FEATURES:
            assert len(feat.description) > 0

    def test_categories_non_empty(self):
        for feat in _FEATURES:
            assert len(feat.category) > 0

    def test_default_status_pending(self):
        for feat in _FEATURES:
            assert feat.status == "pending"

    def test_default_implemented_in_empty(self):
        for feat in _FEATURES:
            assert feat.implemented_in == ""


class TestFeatureMockWiring:
    """Verify the fixture can be used as a mock return value."""

    def test_mock_returns_fixture(self):
        with patch(
            "duplo.extractor.extract_features",
            return_value=_FEATURES,
        ) as mock_extract:
            from duplo.extractor import extract_features

            result = extract_features("some text")

        mock_extract.assert_called_once()
        assert len(result) == 2
        assert result[0].name == "Real-time collaboration"
        assert result[1].name == "End-to-end encryption"
        assert result[0].category == "Collaboration"
        assert result[1].category == "Security"


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


# ---------------------------------------------------------------------------
# Selector mock — auto-select all features without prompting
# ---------------------------------------------------------------------------


def _select_all_features(features: list[Feature], **kwargs: object) -> list[Feature]:
    """Mock replacement for select_features that returns all features."""
    return list(features)


class TestSelectorMockWiring:
    """Verify the selector mock auto-selects all features."""

    def test_mock_returns_all_features(self):
        with patch(
            "duplo.selector.select_features",
            side_effect=_select_all_features,
        ) as mock_sel:
            from duplo.selector import select_features

            result = select_features(_FEATURES)

        mock_sel.assert_called_once()
        assert len(result) == 2
        assert result[0].name == "Real-time collaboration"
        assert result[1].name == "End-to-end encryption"

    def test_mock_ignores_recommended_kwarg(self):
        """Mock works even when recommended/phase_label are passed."""
        with patch(
            "duplo.selector.select_features",
            side_effect=_select_all_features,
        ) as mock_sel:
            from duplo.selector import select_features

            result = select_features(
                _FEATURES,
                recommended=["End-to-end encryption"],
                phase_label="Phase 2: Security",
            )

        mock_sel.assert_called_once()
        assert len(result) == 2

    def test_mock_with_empty_list(self):
        with patch(
            "duplo.selector.select_features",
            side_effect=_select_all_features,
        ):
            from duplo.selector import select_features

            result = select_features([])

        assert result == []

    def test_no_interactive_input_required(self):
        """The mock must not call input() — patching input to raise
        ensures the mock bypasses all interactive prompts."""
        with (
            patch(
                "duplo.selector.select_features",
                side_effect=_select_all_features,
            ),
            patch("builtins.input", side_effect=RuntimeError("unexpected")),
        ):
            from duplo.selector import select_features

            result = select_features(_FEATURES)

        assert len(result) == 2


# ---------------------------------------------------------------------------
# Roadmap fixture for _subsequent_run State 3
# ---------------------------------------------------------------------------

_ROADMAP = [
    {
        "phase": 1,
        "title": "Core Notes",
        "goal": "Basic note CRUD with local storage",
        "features": [
            "Real-time collaboration",
            "End-to-end encryption",
        ],
        "test": "User can create, edit, and delete notes",
    },
]

_PLAN_CONTENT = (
    "# NotesApp — Phase 1: Core Notes\n\n"
    "## Bugs\n\n"
    "- [ ] Set up project scaffolding\n"
    '- [ ] Implement note CRUD [feat: "Real-time collaboration"]\n'
    '- [ ] Add encryption layer [feat: "End-to-end encryption"]\n'
)


# ---------------------------------------------------------------------------
# Integration: run _subsequent_run via main() against tmpdir
# ---------------------------------------------------------------------------


def _write_duplo_json(tmp_path: Path, data: dict) -> None:
    """Write duplo.json into the .duplo/ subdirectory of *tmp_path*."""
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir(exist_ok=True)
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")


class TestSubsequentRunUrlOnlySpec:
    """End-to-end: _subsequent_run with URL-only SPEC.md (State 3).

    Constructs a tmpdir with:
    - SPEC.md containing a product-reference source (scrape: deep)
    - .duplo/duplo.json with features from a prior first run
    - .duplo/file_hashes.json (empty — no file changes)
    - No PLAN.md (triggers State 3: generate roadmap + plan)

    All LLM/network calls are mocked.  Asserts that PLAN.md is
    generated at the end of the run.
    """

    _SPEC_TEXT = (
        "<!-- How the pieces fit together: -->\n"
        "\n"
        "## Purpose\n"
        "NotesApp is a cross-platform note-taking application "
        "with real-time collaboration and end-to-end encryption. "
        "It supports rich text editing and offline mode.\n"
        "\n"
        "## Architecture\n"
        "Web app using React + TypeScript. SQLite for local storage.\n"
        "\n"
        "## Sources\n"
        f"- {_CANONICAL_URL}\n"
        "  role: product-reference\n"
        "  scrape: deep\n"
    )

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: SPEC.md, duplo.json, file_hashes.json."""
        # Write SPEC.md.
        (tmp_path / "SPEC.md").write_text(self._SPEC_TEXT, encoding="utf-8")

        # Write duplo.json with features saved by a prior first run.
        _write_duplo_json(
            tmp_path,
            {
                "app_name": "NotesApp",
                "source_url": _CANONICAL_URL,
                "features": [
                    {
                        "name": f.name,
                        "description": f.description,
                        "category": f.category,
                        "status": "pending",
                        "implemented_in": "",
                    }
                    for f in _FEATURES
                ],
                "preferences": {
                    "platform": "web",
                    "language": "TypeScript",
                    "constraints": [],
                    "preferences": [],
                },
                "architecture_hash": hashlib.sha256(
                    b"Web app using React + TypeScript. SQLite for local storage."
                ).hexdigest(),
            },
        )

        # Empty file_hashes — no file changes to detect.
        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")

        # No ref/ directory (URL-only spec).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def _scrape_result(self):
        """Build a ScrapeResult from the shared fixtures."""
        content_hash = hashlib.sha256(_SCRAPED_TEXT.encode("utf-8")).hexdigest()
        return ScrapeResult(
            combined_text=_SCRAPED_TEXT,
            all_code_examples=[],
            all_page_records=[_PAGE_RECORD],
            all_raw_pages={_CANONICAL_URL: _RAW_HTML},
            product_ref_raw_pages={_CANONICAL_URL: _RAW_HTML},
            source_records=[
                {
                    "url": _CANONICAL_URL,
                    "last_scraped": "2026-04-14T00:00:00Z",
                    "content_hash": content_hash,
                    "scrape_depth_used": "deep",
                }
            ],
        )

    def test_generates_plan_md(self, tmp_path, monkeypatch):
        """_subsequent_run in State 3 generates PLAN.md."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            patch("duplo.main._persist_scrape_result"),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch("duplo.main.save_features"),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
        ):
            main()

        plan_path = tmp_path / "PLAN.md"
        assert plan_path.exists(), "PLAN.md should be generated"
        content = plan_path.read_text(encoding="utf-8")
        assert "NotesApp" in content
        assert "Phase 1" in content

    def test_scrape_declared_sources_called(self, tmp_path, monkeypatch):
        """_subsequent_run calls _scrape_declared_sources when spec
        has scrapeable sources."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ) as mock_scrape,
            patch("duplo.main._persist_scrape_result"),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch("duplo.main.save_features"),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
        ):
            main()

        mock_scrape.assert_called_once()

    def test_roadmap_saved_to_duplo_json(self, tmp_path, monkeypatch):
        """After generate_roadmap, the roadmap is persisted in
        duplo.json and current_phase is set."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            patch("duplo.main._persist_scrape_result"),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch("duplo.main.save_features"),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
        ):
            main()

        data = json.loads((tmp_path / ".duplo" / "duplo.json").read_text(encoding="utf-8"))
        assert "roadmap" in data
        assert len(data["roadmap"]) == 1
        assert data["roadmap"][0]["title"] == "Core Notes"
        assert data["current_phase"] == 1

    def test_no_network_requests(self, tmp_path, monkeypatch):
        """The entire run completes without any real HTTP requests.
        fetch_site is never called directly (only via mock)."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            patch("duplo.main._persist_scrape_result"),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch("duplo.main.save_features"),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError(
                    "fetch_site should not be called directly",
                ),
            ),
        ):
            main()

    def test_plan_has_bugs_section(self, tmp_path, monkeypatch):
        """PLAN.md should contain a ## Bugs section (injected by
        save_plan on first write)."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            patch("duplo.main._persist_scrape_result"),
            patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch("duplo.main.save_features"),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
        ):
            main()

        content = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "## Bugs" in content
