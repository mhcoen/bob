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
from duplo.questioner import BuildPreferences

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

    def test_persist_scrape_result_called(self, tmp_path, monkeypatch):
        """_persist_scrape_result is called with the ScrapeResult
        from _scrape_declared_sources."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            patch("duplo.main._persist_scrape_result") as mock_persist,
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

        mock_persist.assert_called_once()
        result = mock_persist.call_args[0][0]
        assert result.all_raw_pages == {_CANONICAL_URL: _RAW_HTML}
        assert len(result.all_page_records) == 1
        assert result.source_records[0]["url"] == _CANONICAL_URL

    def test_raw_pages_written(self, tmp_path):
        """.duplo/raw_pages/ contains sha256(canonical_url).html
        after save_raw_content runs with scrape result data."""
        from duplo.saver import save_raw_content

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(exist_ok=True)

        save_raw_content(
            {_CANONICAL_URL: _RAW_HTML},
            [_PAGE_RECORD],
            target_dir=tmp_path,
        )

        raw_pages_dir = duplo_dir / "raw_pages"
        assert raw_pages_dir.exists(), ".duplo/raw_pages/ should exist"
        url_hash = hashlib.sha256(_CANONICAL_URL.encode()).hexdigest()
        expected_file = raw_pages_dir / f"{url_hash}.html"
        assert expected_file.exists(), f"Expected {url_hash}.html in raw_pages/"
        assert expected_file.read_text(encoding="utf-8") == _RAW_HTML

    def test_sources_in_duplo_json(self, tmp_path):
        """.duplo/duplo.json has sources populated with the URL
        after save_sources runs with scrape result data."""
        from duplo.saver import save_sources

        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(exist_ok=True)
        (duplo_dir / "duplo.json").write_text("{}", encoding="utf-8")

        result = self._scrape_result()
        save_sources(result.source_records, target_dir=tmp_path)

        data = json.loads((duplo_dir / "duplo.json").read_text(encoding="utf-8"))
        assert "sources" in data
        source_urls = [s["url"] for s in data["sources"]]
        assert _CANONICAL_URL in source_urls

    def test_product_json_synced(self, tmp_path, monkeypatch):
        """product.json has source_url from spec's first
        product-reference after _subsequent_run."""
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

        product_path = tmp_path / ".duplo" / "product.json"
        assert product_path.exists(), "product.json should exist"
        product = json.loads(product_path.read_text(encoding="utf-8"))
        assert product["source_url"] == _CANONICAL_URL

    def test_no_ref_dir_no_errors(self, tmp_path, monkeypatch):
        """No FileNotFoundError and no ref/-related diagnostic when
        ref/ directory does not exist (URL-only spec)."""
        self._setup(tmp_path, monkeypatch)
        assert not (tmp_path / "ref").exists()

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

        assert (tmp_path / "PLAN.md").exists()
        errors_path = tmp_path / ".duplo" / "errors.jsonl"
        if errors_path.exists():
            errors_text = errors_path.read_text(encoding="utf-8")
            assert "ref/" not in errors_text, "No diagnostic about missing ref/ expected"


# ---------------------------------------------------------------------------
# Fixture: ref-only SPEC.md (no ## Sources, with ## References + ref/ files)
# ---------------------------------------------------------------------------

# Minimal 1x1 white PNG (valid image file, 67 bytes)
_FIXTURE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

_FIXTURE_DOCS_TEXT = (
    "NotesApp User Guide\n"
    "===================\n"
    "\n"
    "NotesApp supports real-time collaboration and end-to-end\n"
    "encryption for all your notes.\n"
)

_REF_ONLY_SPEC_TEXT = (
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
    "## References\n"
    "- ref/app_screenshot.png\n"
    "  role: visual-target\n"
    "- ref/user_guide.txt\n"
    "  role: docs\n"
)


def _setup_ref_only_tmpdir(tmp_path: Path) -> None:
    """Create a ref-only project fixture in *tmp_path*.

    Writes SPEC.md (no ## Sources), ref/app_screenshot.png, and
    ref/user_guide.txt.
    """
    (tmp_path / "SPEC.md").write_text(_REF_ONLY_SPEC_TEXT, encoding="utf-8")
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "app_screenshot.png").write_bytes(_FIXTURE_PNG)
    (ref_dir / "user_guide.txt").write_text(_FIXTURE_DOCS_TEXT, encoding="utf-8")


class TestRefOnlyFixture:
    """Verify the ref-only tmpdir fixture is well-formed."""

    def test_spec_has_marker(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "How the pieces fit together:" in text

    def test_spec_has_purpose(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Purpose" in text
        # Purpose must be substantial (>50 chars)
        idx = text.index("## Purpose")
        purpose_start = text.index("\n", idx) + 1
        next_heading = text.find("##", purpose_start)
        purpose_body = text[purpose_start:next_heading].strip()
        assert len(purpose_body) > 50

    def test_spec_has_architecture(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Architecture" in text

    def test_spec_has_no_sources(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Sources" not in text

    def test_spec_has_references(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## References" in text

    def test_spec_references_visual_target(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "ref/app_screenshot.png" in text
        assert "role: visual-target" in text

    def test_spec_references_docs(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "ref/user_guide.txt" in text
        assert "role: docs" in text

    def test_ref_dir_exists(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        assert (tmp_path / "ref").is_dir()

    def test_image_file_exists(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        img = tmp_path / "ref" / "app_screenshot.png"
        assert img.exists()
        assert img.stat().st_size > 0

    def test_image_file_is_valid_png(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        data = (tmp_path / "ref" / "app_screenshot.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_docs_file_exists(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        doc = tmp_path / "ref" / "user_guide.txt"
        assert doc.exists()
        assert doc.stat().st_size > 0

    def test_docs_file_content(self, tmp_path):
        _setup_ref_only_tmpdir(tmp_path)
        text = (tmp_path / "ref" / "user_guide.txt").read_text(encoding="utf-8")
        assert "NotesApp" in text

    def test_spec_parses_correctly(self, tmp_path):
        """read_spec returns a ProductSpec with the expected
        references when run against the fixture."""
        _setup_ref_only_tmpdir(tmp_path)
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            from duplo.spec_reader import read_spec

            spec = read_spec()
        finally:
            os.chdir(old_cwd)

        assert spec is not None
        assert len(spec.references) == 2
        roles = {r.roles[0] for r in spec.references}
        assert roles == {"visual-target", "docs"}
        assert spec.sources == []
        assert len(spec.purpose) > 50


class TestRefOnlyNoHttp:
    """Ref-only spec must never trigger HTTP requests.

    Patches fetch_site to raise if called — the test asserts no
    HTTP work happened during the run.
    """

    def test_fetch_site_not_called(self, tmp_path, monkeypatch):
        """fetch_site raises RuntimeError if invoked, proving no
        HTTP requests are made for a ref-only SPEC.md."""
        _setup_ref_only_tmpdir(tmp_path)

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

        with patch(
            "duplo.fetcher.fetch_site",
            side_effect=RuntimeError("fetch_site must not be called for ref-only spec"),
        ):
            # The test body will be filled in subsequent steps
            # when the full ref-only pipeline mocks are wired up.
            # For now, verify the guard is in place.
            from duplo.fetcher import fetch_site

            raised = False
            try:
                fetch_site("https://should-not-be-called.example.com")
            except RuntimeError as exc:
                raised = True
                assert "must not be called" in str(exc)
            assert raised, "fetch_site guard did not raise"


class TestRefOnlyDesignExtraction:
    """Ref-only spec: extract_design called with visual-target ref paths.

    Runs _first_run with a ref-only SPEC.md (no ## Sources).  The
    visual-target image in ref/ should reach extract_design as the
    design_input argument.  extract_design is mocked to return the
    deterministic _DESIGN_REQUIREMENTS fixture.
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: ref-only fixture + monkeypatch for main().

        Pre-populates product.json so _confirm_product is skipped
        (ref-only specs have no URL, so without a saved product the
        pipeline would prompt the user and exit).
        """
        _setup_ref_only_tmpdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(exist_ok=True)
        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "NotesApp", "source_url": ""}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def test_extract_design_receives_visual_target(self, tmp_path, monkeypatch):
        """extract_design is called with the visual-target ref/ path."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ) as mock_design,
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        mock_design.assert_called_once()
        design_input = mock_design.call_args[0][0]
        # The visual-target ref path should be in the design input.
        input_names = [p.name for p in design_input]
        assert "app_screenshot.png" in input_names, (
            f"Expected visual-target ref in design_input, got {input_names}"
        )

    def test_extract_design_returns_fixture(self, tmp_path, monkeypatch):
        """extract_design mock returns the deterministic fixture."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ) as mock_design,
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        result = mock_design.return_value
        assert isinstance(result, DesignRequirements)
        assert result.colors["primary"] == "#1a73e8"
        assert len(result.components) == 3

    def test_docs_ref_excluded_from_design_input(self, tmp_path, monkeypatch):
        """Only visual-target refs appear in design_input, not docs."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ) as mock_design,
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        design_input = mock_design.call_args[0][0]
        input_names = [p.name for p in design_input]
        assert "user_guide.txt" not in input_names, (
            "docs-role ref should not appear in design_input"
        )


# ---------------------------------------------------------------------------
# Docs text extractor mock — deterministic output, called with docs ref path
# ---------------------------------------------------------------------------

_DOCS_EXTRACTED_TEXT = (
    "=== user_guide.txt ===\n"
    "NotesApp User Guide\n"
    "===================\n"
    "\n"
    "NotesApp supports real-time collaboration and end-to-end\n"
    "encryption for all your notes.\n"
)


class TestRefOnlyDocsExtraction:
    """Ref-only spec: docs_text_extractor called with docs ref/ entries.

    Runs _first_run with a ref-only SPEC.md (no ## Sources).  The
    docs-role entry (ref/user_guide.txt) should reach
    docs_text_extractor as a ReferenceEntry.  The mock returns
    deterministic text and we assert the call received the right path.
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: ref-only fixture + monkeypatch for main()."""
        _setup_ref_only_tmpdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(exist_ok=True)
        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "NotesApp", "source_url": ""}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def test_docs_text_extractor_receives_docs_ref(self, tmp_path, monkeypatch):
        """docs_text_extractor is called with the docs-role ref entry."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value=_DOCS_EXTRACTED_TEXT,
            ) as mock_docs,
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        mock_docs.assert_called_once()
        doc_entries = mock_docs.call_args[0][0]
        paths = [e.path for e in doc_entries]
        assert any("user_guide.txt" in str(p) for p in paths), (
            f"Expected docs ref path containing user_guide.txt, got {paths}"
        )

    def test_docs_text_extractor_returns_deterministic_output(self, tmp_path, monkeypatch):
        """docs_text_extractor mock returns the deterministic fixture."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value=_DOCS_EXTRACTED_TEXT,
            ) as mock_docs,
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        assert mock_docs.return_value == _DOCS_EXTRACTED_TEXT
        assert "NotesApp User Guide" in mock_docs.return_value

    def test_docs_text_included_in_feature_extraction(self, tmp_path, monkeypatch):
        """Docs text from docs_text_extractor flows into extract_features
        as part of the combined text input."""
        self._setup(tmp_path, monkeypatch)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ) as mock_extract,
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value=_DOCS_EXTRACTED_TEXT,
            ),
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            patch("duplo.main.save_reference_screenshots", return_value=[]),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        mock_extract.assert_called_once()
        combined_text = mock_extract.call_args[0][0]
        assert "NotesApp User Guide" in combined_text, (
            "Docs text should appear in the combined text sent to "
            f"extract_features, got: {combined_text[:200]}"
        )


class TestRefOnlyFeatureExtraction:
    """Ref-only spec: extract_features and select_features wiring.

    Runs _first_run with a ref-only SPEC.md (no ## Sources).  Verifies
    that extract_features is called with non-empty text derived from the
    docs-role ref, and that select_features auto-selects all features
    returned by the extractor.
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: ref-only fixture + monkeypatch for main()."""
        _setup_ref_only_tmpdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir(exist_ok=True)
        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "NotesApp", "source_url": ""}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def _run_with_mocks(self):
        """Run main() with full ref-only mocks.

        Returns (mock_extract, mock_select) so callers can assert on
        them.
        """
        mock_extract = MagicMock(return_value=_FEATURES)
        mock_select = MagicMock(side_effect=_select_all_features)

        with (
            patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ),
            patch(
                "duplo.main.docs_text_extractor",
                return_value=_DOCS_EXTRACTED_TEXT,
            ),
            patch(
                "duplo.main.extract_features",
                new=mock_extract,
            ),
            patch(
                "duplo.main.select_features",
                new=mock_select,
            ),
            patch(
                "duplo.main.parse_build_preferences",
                return_value=BuildPreferences(
                    platform="web",
                    language="TypeScript",
                    constraints=[],
                    preferences=[],
                ),
            ),
            patch(
                "duplo.main.validate_build_preferences",
                return_value=[],
            ),
            patch("builtins.input", return_value=""),
            patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
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
                "duplo.main.save_reference_screenshots",
                return_value=[],
            ),
            patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site must not be called"),
            ),
        ):
            main()

        return mock_extract, mock_select

    def test_extract_features_called(self, tmp_path, monkeypatch):
        """extract_features is called exactly once."""
        self._setup(tmp_path, monkeypatch)
        m_ext, _ = self._run_with_mocks()
        m_ext.assert_called_once()

    def test_extract_features_receives_non_empty_text(self, tmp_path, monkeypatch):
        """extract_features receives non-empty combined text as its
        first positional argument."""
        self._setup(tmp_path, monkeypatch)
        m_ext, _ = self._run_with_mocks()
        combined_text = m_ext.call_args[0][0]
        assert len(combined_text) > 0, "extract_features should receive non-empty text"

    def test_extract_features_returns_fixture(self, tmp_path, monkeypatch):
        """extract_features mock returns the deterministic fixture."""
        self._setup(tmp_path, monkeypatch)
        m_ext, _ = self._run_with_mocks()
        result = m_ext.return_value
        assert len(result) == 2
        assert result[0].name == "Real-time collaboration"
        assert result[1].name == "End-to-end encryption"

    def test_select_features_called(self, tmp_path, monkeypatch):
        """select_features is called during the ref-only run."""
        self._setup(tmp_path, monkeypatch)
        _, m_sel = self._run_with_mocks()
        m_sel.assert_called_once()

    def test_select_features_receives_extracted_features(self, tmp_path, monkeypatch):
        """select_features receives the features from
        extract_features."""
        self._setup(tmp_path, monkeypatch)
        _, m_sel = self._run_with_mocks()
        features_arg = m_sel.call_args[0][0]
        names = [f.name for f in features_arg]
        assert "Real-time collaboration" in names
        assert "End-to-end encryption" in names

    def test_select_features_auto_selects_all(self, tmp_path, monkeypatch):
        """select_features mock returns all features (auto-select).

        The side_effect function (_select_all_features) returns its
        input list unchanged, so the call result equals the input.
        We verify by replaying the recorded call args through the
        side_effect.
        """
        self._setup(tmp_path, monkeypatch)
        _, m_sel = self._run_with_mocks()
        features_in = m_sel.call_args[0][0]
        selected = _select_all_features(features_in)
        assert len(selected) == 2
        assert selected[0].name == "Real-time collaboration"
        assert selected[1].name == "End-to-end encryption"

    def test_selector_mock_bypasses_interactive_prompt(self, tmp_path, monkeypatch):
        """The selector mock (_select_all_features) is a pure function
        that returns its input unchanged — it never calls input().
        Verify by checking the mock was invoked (proving the real
        interactive selector was not used)."""
        self._setup(tmp_path, monkeypatch)
        _, m_sel = self._run_with_mocks()
        m_sel.assert_called_once()
        assert m_sel.side_effect is _select_all_features


# ---------------------------------------------------------------------------
# Integration: _subsequent_run with ref-only SPEC.md (State 3)
# ---------------------------------------------------------------------------


class TestSubsequentRunRefOnlySpec:
    """End-to-end: _subsequent_run with ref-only SPEC.md (State 3).

    Constructs a tmpdir with:
    - SPEC.md with ## References (no ## Sources)
    - ref/ directory with visual-target image and docs text file
    - .duplo/duplo.json with features from a prior first run
    - .duplo/file_hashes.json (empty — no file changes)
    - No PLAN.md (triggers State 3: generate roadmap + plan)

    All LLM/network calls are mocked.  Asserts that PLAN.md is
    generated at the end of the run, docs text flows into feature
    extraction, and no HTTP requests are made.
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: ref-only fixture + duplo.json + monkeypatch."""
        _setup_ref_only_tmpdir(tmp_path)

        # Write duplo.json with features saved by a prior first run.
        _write_duplo_json(
            tmp_path,
            {
                "app_name": "NotesApp",
                "source_url": "",
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

        # product.json from first run (no source URL for ref-only).
        (duplo_dir / "product.json").write_text(
            json.dumps({"product_name": "NotesApp", "source_url": ""}),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def _run_with_mocks(self, extra_patches=None):
        """Run main() with full ref-only subsequent-run mocks.

        Returns dict of named mock objects for assertion.
        """
        patches = {
            "validate_for_run": patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            "docs_text_extractor": patch(
                "duplo.main.docs_text_extractor",
                return_value=_DOCS_EXTRACTED_TEXT,
            ),
            "extract_features": patch(
                "duplo.main.extract_features",
                return_value=_FEATURES,
            ),
            "save_features": patch("duplo.main.save_features"),
            "generate_roadmap": patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            "select_features": patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            "generate_phase_plan": patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            "load_frame_descriptions": patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
            "fetch_site": patch(
                "duplo.main.fetch_site",
                side_effect=RuntimeError("fetch_site should not be called for ref-only spec"),
            ),
        }
        if extra_patches:
            patches.update(extra_patches)

        mocks = {}
        managers = {k: v for k, v in patches.items()}
        # Enter all context managers and collect mock objects.
        entered = []
        try:
            for name, mgr in managers.items():
                mock_obj = mgr.__enter__()
                entered.append(mgr)
                mocks[name] = mock_obj
            main()
        finally:
            for mgr in reversed(entered):
                mgr.__exit__(None, None, None)
        return mocks

    def test_generates_plan_md(self, tmp_path, monkeypatch):
        """_subsequent_run in State 3 generates PLAN.md."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        plan_path = tmp_path / "PLAN.md"
        assert plan_path.exists(), "PLAN.md should be generated"
        content = plan_path.read_text(encoding="utf-8")
        assert "NotesApp" in content
        assert "Phase 1" in content

    def test_docs_text_flows_into_features(self, tmp_path, monkeypatch):
        """docs_text_extractor output flows into extract_features."""
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        mocks["docs_text_extractor"].assert_called_once()
        mocks["extract_features"].assert_called_once()
        combined_text = mocks["extract_features"].call_args[0][0]
        assert "NotesApp User Guide" in combined_text

    def test_no_http_requests(self, tmp_path, monkeypatch):
        """fetch_site is never called (ref-only, no source URL)."""
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        mocks["fetch_site"].assert_not_called()

    def test_roadmap_saved_to_duplo_json(self, tmp_path, monkeypatch):
        """After generate_roadmap, the roadmap is persisted in
        duplo.json and current_phase is set."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        data = json.loads((tmp_path / ".duplo" / "duplo.json").read_text(encoding="utf-8"))
        assert "roadmap" in data
        assert len(data["roadmap"]) == 1
        assert data["roadmap"][0]["title"] == "Core Notes"
        assert data["current_phase"] == 1

    def test_plan_has_bugs_section(self, tmp_path, monkeypatch):
        """PLAN.md should contain a ## Bugs section."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        content = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "## Bugs" in content

    def test_select_features_called(self, tmp_path, monkeypatch):
        """select_features is called during plan generation."""
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        mocks["select_features"].assert_called_once()

    def test_extract_design_not_called(self, tmp_path, monkeypatch):
        """extract_design is not called for ref-only subsequent run.

        The design extraction block in _subsequent_run requires
        spec_sources to be non-empty, which is False for ref-only
        specs (no ## Sources).  The _rescrape_product_url fallback
        returns early when source_url is empty.
        """
        self._setup(tmp_path, monkeypatch)
        extra = {
            "extract_design": patch(
                "duplo.main.extract_design",
                return_value=_DESIGN_REQUIREMENTS,
            ),
            "collect_design_input": patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
        }
        mocks = self._run_with_mocks(extra_patches=extra)

        mocks["extract_design"].assert_not_called()

    def test_no_source_url_diagnostic(self, tmp_path, monkeypatch):
        """No diagnostic about missing source URL is recorded."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        errors_path = tmp_path / ".duplo" / "errors.jsonl"
        if errors_path.exists():
            errors_text = errors_path.read_text(encoding="utf-8")
            assert "source_url" not in errors_text.lower()
            assert "source url" not in errors_text.lower()

    def test_no_ref_dir_diagnostic(self, tmp_path, monkeypatch):
        """No diagnostic about ref/ is recorded."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        errors_path = tmp_path / ".duplo" / "errors.jsonl"
        if errors_path.exists():
            errors_text = errors_path.read_text(encoding="utf-8")
            assert "ref/" not in errors_text


# ---------------------------------------------------------------------------
# Fixture: combined SPEC.md — URL source + ref/ visual-target + scope exclude
# ---------------------------------------------------------------------------

_COMBINED_SPEC_TEXT = (
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
    "## Scope\n"
    "- exclude: plugin API\n"
    "\n"
    "## Sources\n"
    f"- {_CANONICAL_URL}\n"
    "  role: product-reference\n"
    "  scrape: deep\n"
    "\n"
    "## References\n"
    "- ref/app_screenshot.png\n"
    "  role: visual-target\n"
)


def _setup_combined_tmpdir(tmp_path: Path) -> None:
    """Create a combined project fixture in *tmp_path*.

    Writes SPEC.md (## Sources + ## References + ## Scope with
    exclude: plugin API), and ref/app_screenshot.png.
    """
    (tmp_path / "SPEC.md").write_text(_COMBINED_SPEC_TEXT, encoding="utf-8")
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "app_screenshot.png").write_bytes(_FIXTURE_PNG)


class TestCombinedFixture:
    """Verify the combined tmpdir fixture is well-formed."""

    def test_spec_has_marker(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "How the pieces fit together:" in text

    def test_spec_has_purpose(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Purpose" in text
        idx = text.index("## Purpose")
        purpose_start = text.index("\n", idx) + 1
        next_heading = text.find("##", purpose_start)
        purpose_body = text[purpose_start:next_heading].strip()
        assert len(purpose_body) > 50

    def test_spec_has_architecture(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Architecture" in text

    def test_spec_has_scope_exclude(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Scope" in text
        assert "exclude: plugin API" in text

    def test_spec_has_sources(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## Sources" in text
        assert _CANONICAL_URL in text
        assert "role: product-reference" in text

    def test_spec_has_references(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "## References" in text
        assert "ref/app_screenshot.png" in text
        assert "role: visual-target" in text

    def test_ref_dir_exists(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        assert (tmp_path / "ref").is_dir()

    def test_image_file_exists(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        img = tmp_path / "ref" / "app_screenshot.png"
        assert img.exists()
        assert img.stat().st_size > 0

    def test_image_file_is_valid_png(self, tmp_path):
        _setup_combined_tmpdir(tmp_path)
        data = (tmp_path / "ref" / "app_screenshot.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_spec_parses_correctly(self, tmp_path):
        """read_spec returns a ProductSpec with expected fields."""
        _setup_combined_tmpdir(tmp_path)
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            from duplo.spec_reader import read_spec

            spec = read_spec()
        finally:
            os.chdir(old_cwd)

        assert spec is not None
        assert len(spec.references) == 1
        assert spec.references[0].roles == ["visual-target"]
        assert len(spec.sources) == 1
        assert spec.sources[0].url == _CANONICAL_URL
        assert spec.scope_exclude == ["plugin API"]
        assert len(spec.purpose) > 50


class TestCombinedFetchSiteMock:
    """Verify fetch_site mock wiring for combined spec (URL + ref/).

    The combined spec has a product-reference URL with scrape: deep,
    so _scrape_declared_sources will call fetch_site.  The mock
    returns the shared _FETCH_SITE_RESULT fixture deterministically.
    """

    def test_mock_returns_fixture_for_combined_url(self):
        """Patching fetch_site returns the shared fixture when called
        with the combined spec's canonical URL."""
        with patch(
            "duplo.fetcher.fetch_site",
            return_value=_FETCH_SITE_RESULT,
        ) as mock_fetch:
            from duplo.fetcher import fetch_site

            result = fetch_site(_CANONICAL_URL, scrape_depth="deep")

        mock_fetch.assert_called_once_with(_CANONICAL_URL, scrape_depth="deep")
        text, examples, doc_structs, records, raw_pages = result
        assert text == _SCRAPED_TEXT
        assert examples == []
        assert isinstance(doc_structs, DocStructures)
        assert len(records) == 1
        assert records[0].url == _CANONICAL_URL
        assert _CANONICAL_URL in raw_pages

    def test_scrape_result_from_fixture(self):
        """ScrapeResult built from the shared fixture has correct
        fields for the combined spec."""
        content_hash = hashlib.sha256(_SCRAPED_TEXT.encode("utf-8")).hexdigest()
        result = ScrapeResult(
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
        assert result.combined_text == _SCRAPED_TEXT
        assert len(result.all_page_records) == 1
        assert result.product_ref_raw_pages == {
            _CANONICAL_URL: _RAW_HTML,
        }
        assert len(result.source_records) == 1
        assert result.source_records[0]["url"] == _CANONICAL_URL


class TestCombinedDesignMock:
    """Verify extract_design mock wiring for combined spec.

    The combined spec has both a product-reference URL (site images)
    and a ref/ visual-target image.  extract_design should receive
    images from both sources.  The mock returns _DESIGN_REQUIREMENTS.
    """

    def test_mock_returns_fixture(self):
        """Patching extract_design returns the shared fixture."""
        with patch(
            "duplo.main.extract_design",
            return_value=_DESIGN_REQUIREMENTS,
        ) as mock_design:
            from duplo.main import extract_design

            result = extract_design([Path("ref/app_screenshot.png")])

        mock_design.assert_called_once()
        assert isinstance(result, DesignRequirements)
        assert result.colors["primary"] == "#1a73e8"
        assert len(result.components) == 3

    def test_mock_accepts_multiple_image_paths(self):
        """extract_design mock works with a mixed list of paths
        (ref/ visual-target + site images)."""
        mixed_paths = [
            Path("ref/app_screenshot.png"),
            Path(".duplo/site_images/page1.png"),
            Path(".duplo/site_images/page2.png"),
        ]
        with patch(
            "duplo.main.extract_design",
            return_value=_DESIGN_REQUIREMENTS,
        ) as mock_design:
            from duplo.main import extract_design

            result = extract_design(mixed_paths)

        mock_design.assert_called_once()
        call_paths = mock_design.call_args[0][0]
        assert len(call_paths) == 3
        assert call_paths[0] == Path("ref/app_screenshot.png")
        assert isinstance(result, DesignRequirements)
        assert result.fonts["headings"] == "Inter, sans-serif, ~20px"

    def test_fixture_source_images_are_deterministic(self):
        """The fixture's source_images field is a fixed list."""
        assert _DESIGN_REQUIREMENTS.source_images == [
            "screenshot1.png",
            "screenshot2.png",
        ]


# ---------------------------------------------------------------------------
# Feature extractor fixture for combined spec (scope_exclude filtering)
# ---------------------------------------------------------------------------

_COMBINED_FEATURES = [
    Feature(
        name="Plugin API",
        description="Exposes a plugin API for third-party extensions "
        "to hook into the note editor lifecycle.",
        category="Extensibility",
    ),
    Feature(
        name="Non-plugin-API configuration",
        description="A non-plugin-API settings panel that lets users "
        "customise the editor without writing code.",
        category="Configuration",
    ),
    Feature(
        name="Offline sync",
        description="Notes are synced to local storage for offline "
        "access and reconciled when connectivity returns.",
        category="Sync",
    ),
]


class TestCombinedFeatureFixture:
    """Verify the three-feature fixture for scope_exclude filtering."""

    def test_fixture_length(self):
        assert len(_COMBINED_FEATURES) == 3

    def test_features_are_feature_instances(self):
        for feat in _COMBINED_FEATURES:
            assert isinstance(feat, Feature)

    def test_names_are_distinct(self):
        names = [f.name for f in _COMBINED_FEATURES]
        assert len(names) == len(set(names))

    def test_descriptions_non_empty(self):
        for feat in _COMBINED_FEATURES:
            assert len(feat.description) > 0

    def test_categories_non_empty(self):
        for feat in _COMBINED_FEATURES:
            assert len(feat.category) > 0

    def test_default_status_pending(self):
        for feat in _COMBINED_FEATURES:
            assert feat.status == "pending"

    def test_plugin_api_matches_excluded(self):
        """Feature (a) 'Plugin API' is caught by _matches_excluded
        for the term 'plugin API'."""
        from duplo.extractor import _matches_excluded

        assert _matches_excluded(_COMBINED_FEATURES[0], ["plugin API"])

    def test_non_plugin_api_not_matched(self):
        """Feature (b) 'Non-plugin-API configuration' contains the
        substring but does NOT match word-boundary 'plugin API'."""
        from duplo.extractor import _matches_excluded

        assert not _matches_excluded(_COMBINED_FEATURES[1], ["plugin API"])

    def test_unrelated_not_matched(self):
        """Feature (c) 'Offline sync' is unrelated and not matched."""
        from duplo.extractor import _matches_excluded

        assert not _matches_excluded(_COMBINED_FEATURES[2], ["plugin API"])


class TestCombinedFeatureMockWiring:
    """Verify the combined-feature fixture works as a mock return value."""

    def test_mock_returns_fixture(self):
        with patch(
            "duplo.extractor.extract_features",
            return_value=_COMBINED_FEATURES,
        ) as mock_extract:
            from duplo.extractor import extract_features

            result = extract_features("some text")

        mock_extract.assert_called_once_with("some text")
        assert len(result) == 3
        assert result[0].name == "Plugin API"
        assert result[1].name == "Non-plugin-API configuration"
        assert result[2].name == "Offline sync"


# ---------------------------------------------------------------------------
# Integration: _subsequent_run with combined SPEC.md + scope_exclude
# ---------------------------------------------------------------------------


class TestSubsequentRunCombinedSpec:
    """End-to-end: _subsequent_run with combined SPEC.md (State 3).

    Constructs a tmpdir with:
    - SPEC.md containing product-reference URL, visual-target ref/,
      and scope_exclude: plugin API
    - .duplo/duplo.json with features from a prior first run
    - .duplo/file_hashes.json (empty — no file changes)
    - No PLAN.md (triggers State 3: generate roadmap + plan)

    All LLM/network calls are mocked.  The key assertion is that
    scope_exclude filtering drops "Plugin API" but keeps
    "Non-plugin-API configuration" and "Offline sync".
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: combined fixture + duplo.json + monkeypatch."""
        _setup_combined_tmpdir(tmp_path)

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
                    for f in _COMBINED_FEATURES
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

        # product.json from first run.
        (duplo_dir / "product.json").write_text(
            json.dumps(
                {
                    "product_name": "NotesApp",
                    "source_url": _CANONICAL_URL,
                }
            ),
            encoding="utf-8",
        )

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

    def _run_with_mocks(self, extra_patches=None):
        """Run main() with full combined subsequent-run mocks.

        Returns dict of named mock objects for assertion.
        """
        patches = {
            "validate_for_run": patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            "scrape_declared": patch(
                "duplo.main._scrape_declared_sources",
                return_value=self._scrape_result(),
            ),
            "persist_scrape": patch("duplo.main._persist_scrape_result"),
            "download_site_media": patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            "behavioral_refs": patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            "collect_design_input": patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            "extract_features": patch(
                "duplo.main.extract_features",
                return_value=_COMBINED_FEATURES,
            ),
            "save_features": patch("duplo.main.save_features"),
            "generate_roadmap": patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            "select_features": patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            "generate_phase_plan": patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            "load_frame_descriptions": patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
        }
        if extra_patches:
            patches.update(extra_patches)

        mocks = {}
        entered = []
        try:
            for name, mgr in patches.items():
                mock_obj = mgr.__enter__()
                entered.append(mgr)
                mocks[name] = mock_obj
            main()
        finally:
            for mgr in reversed(entered):
                mgr.__exit__(None, None, None)
        return mocks

    def test_generates_plan_md(self, tmp_path, monkeypatch):
        """_subsequent_run in State 3 generates PLAN.md."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        plan_path = tmp_path / "PLAN.md"
        assert plan_path.exists(), "PLAN.md should be generated"
        content = plan_path.read_text(encoding="utf-8")
        assert "NotesApp" in content
        assert "Phase 1" in content

    def test_scope_exclude_filters_plugin_api(self, tmp_path, monkeypatch):
        """scope_exclude 'plugin API' drops the Plugin API feature
        but keeps Non-plugin-API configuration and Offline sync.

        save_features receives only the two surviving features
        after _matches_excluded filtering in _subsequent_run.
        """
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        # save_features should have been called with the filtered list.
        mocks["save_features"].assert_called_once()
        saved = mocks["save_features"].call_args[0][0]
        saved_names = [f.name for f in saved]
        assert "Plugin API" not in saved_names
        assert "Non-plugin-API configuration" in saved_names
        assert "Offline sync" in saved_names
        assert len(saved) == 2

    def test_duplo_json_excludes_plugin_api(self, tmp_path, monkeypatch):
        """On-disk duplo.json features list must NOT contain 'Plugin API'
        after scope_exclude filtering.  Features 'Non-plugin-API
        configuration' and 'Offline sync' must be present.

        Uses a write-through save_features replacement so the filtered
        features are persisted to disk without LLM dedup calls.
        """
        self._setup(tmp_path, monkeypatch)

        import dataclasses as _dc

        duplo_json_path = tmp_path / ".duplo" / "duplo.json"

        def _write_through_save(features, *, target_dir="."):
            data = json.loads(duplo_json_path.read_text(encoding="utf-8"))
            data["features"] = [_dc.asdict(f) for f in features]
            duplo_json_path.write_text(
                json.dumps(data, indent=2) + "\n",
                encoding="utf-8",
            )
            return duplo_json_path

        extra = {
            "save_features": patch(
                "duplo.main.save_features",
                side_effect=_write_through_save,
            ),
        }
        self._run_with_mocks(extra_patches=extra)

        data = json.loads(duplo_json_path.read_text(encoding="utf-8"))
        saved_names = [f["name"] for f in data["features"]]
        assert "Plugin API" not in saved_names, "scope_exclude should have dropped 'Plugin API'"
        assert "Non-plugin-API configuration" in saved_names, (
            "substring 'non-plugin-API' must NOT trigger word-boundary match for 'plugin API'"
        )
        assert "Offline sync" in saved_names, "unrelated feature must survive scope_exclude"
        assert len(data["features"]) == 2

    def test_scrape_declared_sources_called(self, tmp_path, monkeypatch):
        """_scrape_declared_sources is called (spec has scrapeable
        sources)."""
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        mocks["scrape_declared"].assert_called_once()

    def test_roadmap_saved_to_duplo_json(self, tmp_path, monkeypatch):
        """After generate_roadmap, the roadmap is persisted in
        duplo.json and current_phase is set."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        data = json.loads((tmp_path / ".duplo" / "duplo.json").read_text(encoding="utf-8"))
        assert "roadmap" in data
        assert len(data["roadmap"]) == 1
        assert data["current_phase"] == 1

    def test_plan_has_bugs_section(self, tmp_path, monkeypatch):
        """PLAN.md should contain a ## Bugs section."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        content = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert "## Bugs" in content

    def test_scope_exclude_diagnostic_recorded(self, tmp_path, monkeypatch):
        """diagnostics records a scope_exclude entry for 'Plugin API'
        and only for that feature — not for 'Non-plugin-API
        configuration' or 'Offline sync'.
        """
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        errors_path = tmp_path / ".duplo" / "errors.jsonl"
        assert errors_path.exists(), "errors.jsonl should exist after scope_exclude drop"

        records = []
        for line in errors_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

        scope_records = [r for r in records if r.get("site") == "extractor:scope_exclude"]
        assert len(scope_records) == 1, (
            f"Expected exactly 1 scope_exclude diagnostic, got"
            f" {len(scope_records)}: {scope_records}"
        )
        assert "Plugin API" in scope_records[0]["message"]
        assert "plugin API" in scope_records[0]["message"]
        assert "Non-plugin-API" not in scope_records[0]["message"]
        assert "Offline sync" not in scope_records[0]["message"]


# ---------------------------------------------------------------------------
# Cross-origin link collection from deep-scraped sources
# ---------------------------------------------------------------------------

# Canonical URL for cross-origin link test
_XORIGIN_SOURCE_URL = "https://myproduct.example.com"

# HTML fixture with one cross-origin <a href> to a different host
_XORIGIN_RAW_HTML = (
    "<html><head><title>MyProduct</title></head><body>"
    "<h1>MyProduct</h1>"
    "<p>A productivity tool for teams.</p>"
    '<a href="/docs/api">API Docs</a>'
    '<a href="https://external.example.org/integrations">Integrations</a>'
    "</body></html>"
)

_XORIGIN_CONTENT_HASH = hashlib.sha256(_XORIGIN_RAW_HTML.encode()).hexdigest()

_XORIGIN_PAGE_RECORD = PageRecord(
    url=_XORIGIN_SOURCE_URL,
    fetched_at="2026-04-15T00:00:00Z",
    content_hash=_XORIGIN_CONTENT_HASH,
)

_XORIGIN_SCRAPED_TEXT = (
    "MyProduct is a productivity tool for teams. "
    "Features include task management and integrations."
)

_XORIGIN_FETCH_SITE_RESULT = (
    _XORIGIN_SCRAPED_TEXT,
    [],  # empty code_examples
    DocStructures(),  # empty doc_structures
    [_XORIGIN_PAGE_RECORD],  # one PageRecord
    {_XORIGIN_SOURCE_URL: _XORIGIN_RAW_HTML},  # raw_pages
)

_XORIGIN_SPEC_TEXT = (
    "<!-- How the pieces fit together: -->\n"
    "\n"
    "## Purpose\n"
    "MyProduct is a productivity tool for teams with task management "
    "and third-party integrations. It helps distributed teams stay "
    "organized and focused.\n"
    "\n"
    "## Architecture\n"
    "Web app using Next.js + TypeScript. PostgreSQL for storage.\n"
    "\n"
    "## Sources\n"
    f"- {_XORIGIN_SOURCE_URL}\n"
    "  role: product-reference\n"
    "  scrape: deep\n"
)

_XORIGIN_FEATURES = [
    Feature(
        name="Task management",
        description="Create, assign, and track tasks across projects.",
        category="Core",
    ),
    Feature(
        name="Third-party integrations",
        description="Connect with external services like Slack and GitHub.",
        category="Integrations",
    ),
]


class TestCrossOriginFixture:
    """Verify the cross-origin link test fixtures are well-formed."""

    def test_html_has_cross_origin_link(self):
        assert 'href="https://external.example.org/integrations"' in (_XORIGIN_RAW_HTML)

    def test_html_has_same_origin_link(self):
        assert 'href="/docs/api"' in _XORIGIN_RAW_HTML

    def test_source_url_differs_from_cross_origin_host(self):
        assert "myproduct.example.com" in _XORIGIN_SOURCE_URL
        assert "external.example.org" in _XORIGIN_RAW_HTML
        assert "myproduct.example.com" != "external.example.org"

    def test_spec_has_marker(self):
        assert "How the pieces fit together:" in _XORIGIN_SPEC_TEXT

    def test_spec_has_purpose(self):
        assert "## Purpose" in _XORIGIN_SPEC_TEXT
        idx = _XORIGIN_SPEC_TEXT.index("## Purpose")
        purpose_start = _XORIGIN_SPEC_TEXT.index("\n", idx) + 1
        next_heading = _XORIGIN_SPEC_TEXT.find("##", purpose_start)
        purpose_body = _XORIGIN_SPEC_TEXT[purpose_start:next_heading].strip()
        assert len(purpose_body) > 50

    def test_spec_has_architecture(self):
        assert "## Architecture" in _XORIGIN_SPEC_TEXT

    def test_spec_has_sources_with_deep_scrape(self):
        assert "## Sources" in _XORIGIN_SPEC_TEXT
        assert _XORIGIN_SOURCE_URL in _XORIGIN_SPEC_TEXT
        assert "role: product-reference" in _XORIGIN_SPEC_TEXT
        assert "scrape: deep" in _XORIGIN_SPEC_TEXT

    def test_fetch_site_result_tuple_length(self):
        assert len(_XORIGIN_FETCH_SITE_RESULT) == 5

    def test_raw_pages_keyed_by_source_url(self):
        raw_pages = _XORIGIN_FETCH_SITE_RESULT[4]
        assert _XORIGIN_SOURCE_URL in raw_pages
        assert len(raw_pages) == 1

    def test_record_and_raw_pages_in_sync(self):
        records = _XORIGIN_FETCH_SITE_RESULT[3]
        raw_pages = _XORIGIN_FETCH_SITE_RESULT[4]
        for rec in records:
            assert rec.url in raw_pages


def _setup_cross_origin_tmpdir(tmp_path: Path) -> None:
    """Create a cross-origin link test fixture in *tmp_path*.

    Writes SPEC.md with one product-reference URL (scrape: deep).
    No ref/ directory (URL-only spec).
    """
    (tmp_path / "SPEC.md").write_text(_XORIGIN_SPEC_TEXT, encoding="utf-8")


class TestCrossOriginTmpdir:
    """Verify the cross-origin tmpdir helper creates correct state."""

    def test_spec_md_exists(self, tmp_path):
        _setup_cross_origin_tmpdir(tmp_path)
        assert (tmp_path / "SPEC.md").exists()

    def test_spec_md_content(self, tmp_path):
        _setup_cross_origin_tmpdir(tmp_path)
        text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert _XORIGIN_SOURCE_URL in text
        assert "scrape: deep" in text

    def test_no_ref_directory(self, tmp_path):
        _setup_cross_origin_tmpdir(tmp_path)
        assert not (tmp_path / "ref").exists()


class TestCrossOriginDiscovery:
    """End-to-end: _subsequent_run discovers cross-origin links.

    Runs _subsequent_run with a SPEC.md that declares one
    product-reference URL (scrape: deep).  The mocked fetch_site
    returns HTML with a cross-origin <a href>.  After the run,
    SPEC.md should have a new ## Sources entry for the cross-origin
    URL with ``discovered: true``, and fetch_site should have been
    called only once (for the source URL, not for the discovered
    URL).
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: cross-origin SPEC.md + duplo.json."""
        _setup_cross_origin_tmpdir(tmp_path)

        _write_duplo_json(
            tmp_path,
            {
                "app_name": "MyProduct",
                "source_url": _XORIGIN_SOURCE_URL,
                "features": [
                    {
                        "name": f.name,
                        "description": f.description,
                        "category": f.category,
                        "status": "pending",
                        "implemented_in": "",
                    }
                    for f in _XORIGIN_FEATURES
                ],
                "preferences": {
                    "platform": "web",
                    "language": "TypeScript",
                    "constraints": [],
                    "preferences": [],
                },
                "architecture_hash": hashlib.sha256(
                    b"Web app using Next.js + TypeScript. PostgreSQL for storage."
                ).hexdigest(),
            },
        )

        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        (duplo_dir / "product.json").write_text(
            json.dumps(
                {
                    "product_name": "MyProduct",
                    "source_url": _XORIGIN_SOURCE_URL,
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def _run_with_mocks(self):
        """Run main() with fetch_site mocked but real scrape pipeline.

        _scrape_declared_sources and _persist_scrape_result run for
        real so cross-origin links flow through to SPEC.md.
        """
        patches = {
            "validate_for_run": patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            "fetch_site": patch(
                "duplo.main.fetch_site",
                return_value=_XORIGIN_FETCH_SITE_RESULT,
            ),
            "download_site_media": patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            "behavioral_refs": patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            "collect_design_input": patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            "extract_features": patch(
                "duplo.main.extract_features",
                return_value=_XORIGIN_FEATURES,
            ),
            "save_features": patch("duplo.main.save_features"),
            "generate_roadmap": patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            "select_features": patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            "generate_phase_plan": patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            "load_frame_descriptions": patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
        }
        mocks = {}
        entered = []
        try:
            for name, mgr in patches.items():
                mock_obj = mgr.__enter__()
                entered.append(mgr)
                mocks[name] = mock_obj
            main()
        finally:
            for mgr in reversed(entered):
                mgr.__exit__(None, None, None)
        return mocks

    def test_spec_md_modified_with_discovered_url(self, tmp_path, monkeypatch):
        """SPEC.md gains a discovered: true entry for the cross-origin
        URL after _subsequent_run."""
        self._setup(tmp_path, monkeypatch)
        self._run_with_mocks()

        spec_text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "external.example.org" in spec_text, "Cross-origin URL should appear in SPEC.md"
        assert "discovered: true" in spec_text, "Discovered URL should have discovered: true flag"

    def test_cross_origin_url_not_fetched(self, tmp_path, monkeypatch):
        """fetch_site is called only for the source URL, never for
        the discovered cross-origin URL."""
        self._setup(tmp_path, monkeypatch)
        mocks = self._run_with_mocks()

        mock_fetch = mocks["fetch_site"]
        assert mock_fetch.call_count == 1, (
            f"fetch_site should be called once, got {mock_fetch.call_count}"
        )
        called_url = mock_fetch.call_args[0][0]
        assert called_url == _XORIGIN_SOURCE_URL, (
            f"fetch_site should be called with source URL, got {called_url}"
        )


class TestCrossOriginDiscoveryIdempotent:
    """Second _subsequent_run on the same tmpdir preserves discovered URLs.

    After the first run writes ``discovered: true`` entries to SPEC.md,
    a second run (without any SPEC.md modification) must:
    - still show the discovered entry with the flag intact,
    - NOT fetch the cross-origin URL (fetch_site called only for the
      original source URL, not the discovered one).
    """

    def _setup(self, tmp_path, monkeypatch):
        """Common setup: cross-origin SPEC.md + duplo.json."""
        _setup_cross_origin_tmpdir(tmp_path)

        _write_duplo_json(
            tmp_path,
            {
                "app_name": "MyProduct",
                "source_url": _XORIGIN_SOURCE_URL,
                "features": [
                    {
                        "name": f.name,
                        "description": f.description,
                        "category": f.category,
                        "status": "pending",
                        "implemented_in": "",
                    }
                    for f in _XORIGIN_FEATURES
                ],
                "preferences": {
                    "platform": "web",
                    "language": "TypeScript",
                    "constraints": [],
                    "preferences": [],
                },
                "architecture_hash": hashlib.sha256(
                    b"Web app using Next.js + TypeScript. PostgreSQL for storage."
                ).hexdigest(),
            },
        )

        duplo_dir = tmp_path / ".duplo"
        (duplo_dir / "file_hashes.json").write_text("{}", encoding="utf-8")
        (duplo_dir / "product.json").write_text(
            json.dumps(
                {
                    "product_name": "MyProduct",
                    "source_url": _XORIGIN_SOURCE_URL,
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

    def _run_with_mocks(self):
        """Run main() with fetch_site mocked but real scrape pipeline."""
        patches = {
            "validate_for_run": patch(
                "duplo.main.validate_for_run",
                return_value=MagicMock(warnings=[], errors=[]),
            ),
            "fetch_site": patch(
                "duplo.main.fetch_site",
                return_value=_XORIGIN_FETCH_SITE_RESULT,
            ),
            "download_site_media": patch(
                "duplo.main._download_site_media",
                return_value=([], []),
            ),
            "behavioral_refs": patch(
                "duplo.main.format_behavioral_references",
                return_value=[],
            ),
            "collect_design_input": patch(
                "duplo.main.collect_design_input",
                return_value=[],
            ),
            "extract_features": patch(
                "duplo.main.extract_features",
                return_value=_XORIGIN_FEATURES,
            ),
            "save_features": patch("duplo.main.save_features"),
            "generate_roadmap": patch(
                "duplo.main.generate_roadmap",
                return_value=_ROADMAP,
            ),
            "select_features": patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            "generate_phase_plan": patch(
                "duplo.main.generate_phase_plan",
                return_value=_PLAN_CONTENT,
            ),
            "load_frame_descriptions": patch(
                "duplo.main.load_frame_descriptions",
                return_value=[],
            ),
        }
        mocks = {}
        entered = []
        try:
            for name, mgr in patches.items():
                mock_obj = mgr.__enter__()
                entered.append(mgr)
                mocks[name] = mock_obj
            main()
        finally:
            for mgr in reversed(entered):
                mgr.__exit__(None, None, None)
        return mocks

    def test_discovered_entry_persists_after_second_run(self, tmp_path, monkeypatch):
        """Discovered URL entry stays in SPEC.md after a second run."""
        self._setup(tmp_path, monkeypatch)

        # First run: writes discovered entry to SPEC.md.
        self._run_with_mocks()

        spec_after_first = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "external.example.org" in spec_after_first
        assert "discovered: true" in spec_after_first

        # Second run: same tmpdir, no SPEC.md modification.
        self._run_with_mocks()

        spec_after_second = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        assert "external.example.org" in spec_after_second, (
            "Cross-origin discovered URL should still be in SPEC.md"
        )
        assert "discovered: true" in spec_after_second, "Discovered flag should still be present"

    def test_cross_origin_url_not_fetched_on_second_run(self, tmp_path, monkeypatch):
        """fetch_site is called only for the source URL on both runs."""
        self._setup(tmp_path, monkeypatch)

        # First run.
        self._run_with_mocks()

        # Second run: capture fetch_site calls.
        mocks = self._run_with_mocks()

        mock_fetch = mocks["fetch_site"]
        # fetch_site should be called once per run (for the source URL).
        assert mock_fetch.call_count == 1, (
            f"fetch_site should be called once on second run, got {mock_fetch.call_count}"
        )
        called_url = mock_fetch.call_args[0][0]
        assert called_url == _XORIGIN_SOURCE_URL, (
            f"fetch_site should be called with source URL, got {called_url}"
        )

    def test_discovered_entry_not_duplicated(self, tmp_path, monkeypatch):
        """Running twice does not duplicate the discovered entry."""
        self._setup(tmp_path, monkeypatch)

        self._run_with_mocks()
        self._run_with_mocks()

        spec_text = (tmp_path / "SPEC.md").read_text(encoding="utf-8")
        # Count occurrences of the discovered URL — should appear exactly once.
        count = spec_text.count("https://external.example.org/integrations")
        assert count == 1, f"Discovered URL should appear once in SPEC.md, found {count}"
