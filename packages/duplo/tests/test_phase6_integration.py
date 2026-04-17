"""Phase 6 end-to-end integration tests for ``duplo init``.

Each test constructs a fixture project in a tmpdir, runs ``run_init``
programmatically, and asserts on the output state. LLM calls must be
mocked so tests do not depend on claude -p availability or network.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from duplo.doc_tables import DocStructures
from duplo.fetcher import PageRecord
from duplo.spec_reader import DesignBlock, ProductSpec


_IDENTIFIED_FIXTURE_URL = "https://numi.app"
_IDENTIFIED_FIXTURE_TEXT = (
    "=== https://numi.app ===\nNumi — a calculator app that shows answers inline as you type."
)

_DESCRIPTION_FIXTURE_PROSE = (
    "Build a tiny SwiftUI calculator app that shows answers inline as you type, "
    "similar to Numi. The app keeps a history of recent calculations and supports "
    'unit conversions like "3 miles in km".\n'
)
_DESCRIPTION_FIXTURE_PURPOSE = (
    "A SwiftUI calculator that shows answers inline as you type, with history and unit conversion."
)
_DESCRIPTION_FIXTURE_ARCHITECTURE = "Swift 5.9, SwiftUI, iOS 17+."


def _write_description_fixture(tmp_path: Path) -> Path:
    """Drop a ``description.txt`` fixture into *tmp_path* and return its path.

    Centralized here so later subtasks that run ``run_init`` under
    ``--from-description`` reuse the same prose.  The prose is written
    byte-for-byte so the "verbatim in ## Notes" assertion in the parent
    test can compare against the same constant the fixture wrote.
    """
    path = tmp_path / "description.txt"
    path.write_text(_DESCRIPTION_FIXTURE_PROSE)
    return path


def _stub_draft_from_inputs(inputs):
    """Stand-in for :func:`duplo.spec_writer._draft_from_inputs`.

    Returns a :class:`ProductSpec` that mimics what a well-behaved
    drafter LLM would produce for :data:`_DESCRIPTION_FIXTURE_PROSE`:
    Purpose populated; Architecture populated (the prose explicitly
    names a stack); optional sections left empty.  ``notes`` stays
    empty here — :func:`_build_draft_spec` copies the verbatim prose
    into ``## Notes`` after this call, which is what the parent test
    is verifying.
    """
    return ProductSpec(
        purpose=_DESCRIPTION_FIXTURE_PURPOSE,
        architecture=_DESCRIPTION_FIXTURE_ARCHITECTURE,
        design=DesignBlock(),
    )


def _make_args(**overrides) -> argparse.Namespace:
    """Build an argparse Namespace matching what ``duplo init`` produces."""
    defaults = {
        "url": None,
        "from_description": None,
        "deep": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fetch_site_identified_fixture() -> tuple:
    """Build a ``fetch_site`` return tuple whose scraped text names a product.

    Shape matches what :func:`duplo.fetcher.fetch_site` returns for a
    successful shallow scrape: ``(text, code_examples, doc_structures,
    page_records, raw_pages)``. The scraped *text* deliberately names
    "Numi" so the (separately mocked) validator in downstream subtasks
    can flag it as a single identifiable product.
    """
    record = PageRecord(
        url=_IDENTIFIED_FIXTURE_URL,
        fetched_at="2026-04-17T00:00:00+00:00",
        content_hash="deadbeef",
    )
    return (
        _IDENTIFIED_FIXTURE_TEXT,
        [],
        DocStructures(),
        [record],
        {_IDENTIFIED_FIXTURE_URL: "<html></html>"},
    )


class TestInitNoArgsProducesTemplate:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_no_args_produces_template``.
    """

    def test_run_init_no_url_no_description_in_tmpdir(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with no URL, no description in a tmpdir.

        This covers the happy-path scaffolding: ``run_init`` is called
        with both optional inputs absent and an empty working
        directory.  It must return without raising.  Later subtasks
        add the content-level assertions (SPEC.md contents, ref/
        layout, migration status).
        """
        from duplo.init import _REF_README_CONTENT, run_init
        from duplo.migration import needs_migration

        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        # Drain captured output so it does not bleed into other tests.
        capsys.readouterr()

        spec_path = tmp_path / "SPEC.md"
        assert spec_path.is_file()
        spec_text = spec_path.read_text()
        assert "How the pieces fit together:" in spec_text
        assert "<FILL IN: one or two sentences describing what you're building>" in spec_text
        assert "<FILL IN: language, framework, platform, constraints>" in spec_text

        ref_dir = tmp_path / "ref"
        assert ref_dir.is_dir()
        readme_path = ref_dir / "README.md"
        assert readme_path.is_file()
        assert readme_path.read_text() == _REF_README_CONTENT

        assert needs_migration(tmp_path) is False


class TestInitUrlProducesPrefilledSpec:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_url_produces_prefilled_spec``.
    """

    def test_fetch_site_mocked_with_identified_fixture(self, tmp_path, monkeypatch):
        """Mock ``fetch_site`` to return a fixture scrape with identifiable product name.

        Stages the URL-flow integration test: confirms the helper
        produces a scrape tuple whose text names a product and that
        patching ``duplo.init.fetch_site`` with it routes the fixture
        to callers. Later subtasks call ``run_init`` under this mock
        and assert on SPEC.md contents.
        """
        monkeypatch.chdir(tmp_path)

        fixture = _fetch_site_identified_fixture()
        text, _examples, _structures, records, _raw = fixture
        assert records, "fixture must look like a successful fetch"
        assert "Numi" in text

        with patch("duplo.init.fetch_site", return_value=fixture) as mock_fetch:
            from duplo.init import fetch_site as patched_fetch_site

            assert patched_fetch_site(_IDENTIFIED_FIXTURE_URL, scrape_depth="shallow") == fixture

        mock_fetch.assert_called_once_with(_IDENTIFIED_FIXTURE_URL, scrape_depth="shallow")

    def test_run_init_with_url_argument(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with a URL argument.

        Stages the URL-flow integration path: patches the three LLM /
        network dependencies (``fetch_site`` with the identified
        fixture, ``validate_product_url`` with a ``single_product``
        result for "Numi", and ``draft_spec`` with a minimal
        pre-filled SPEC body) and invokes ``run_init`` with
        ``args.url`` set.  This subtask only confirms the URL flow
        runs to completion under mocks and lays down SPEC.md / ref/;
        the content-level assertions on Purpose / Sources /
        Architecture arrive in the next subtask.
        """
        from duplo.init import run_init
        from duplo.validator import ValidationResult

        monkeypatch.chdir(tmp_path)

        drafted = (
            "## Purpose\n\nNumi — a calculator app that shows answers inline.\n\n"
            "## Sources\n\n"
            f"- {_IDENTIFIED_FIXTURE_URL}\n"
            "  role: product-reference\n"
            "  scrape: deep\n\n"
            "## Architecture\n\n"
            "<FILL IN: language, framework, platform, constraints>\n"
        )

        with (
            patch(
                "duplo.init.fetch_site",
                return_value=_fetch_site_identified_fixture(),
            ),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="single identifiable product",
                ),
            ),
            patch("duplo.init.draft_spec", return_value=drafted),
        ):
            run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))

        # Drain captured output so it does not bleed into later tests.
        capsys.readouterr()

        assert (tmp_path / "SPEC.md").is_file()
        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").is_file()

        from duplo.spec_reader import read_spec

        spec = read_spec(target_dir=tmp_path)
        assert spec is not None

        assert spec.purpose
        assert "FILL IN" not in spec.purpose
        assert spec.fill_in_purpose is False

        source_urls = [s.url for s in spec.sources]
        assert _IDENTIFIED_FIXTURE_URL in source_urls
        matching = [s for s in spec.sources if s.url == _IDENTIFIED_FIXTURE_URL]
        assert len(matching) == 1
        entry = matching[0]
        assert entry.role == "product-reference"
        assert entry.scrape == "deep"

        assert spec.fill_in_architecture is True


class TestInitDescriptionProducesNotesWithVerbatimProse:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_description_produces_notes_with_verbatim_prose``.
    """

    def test_description_fixture_and_draft_from_inputs_mock(self, tmp_path, monkeypatch):
        """Write a ``description.txt`` fixture and mock the LLM call in
        ``_draft_from_inputs``.

        Stages the description-flow integration test: writes the prose
        fixture to ``tmp_path/description.txt`` and confirms that
        patching ``duplo.spec_writer._draft_from_inputs`` (the only
        LLM call on the description-flow path — :func:`_build_draft_spec`
        delegates to it, and :func:`draft_spec` wraps that) routes a
        deterministic :class:`ProductSpec` to callers.  Later subtasks
        call ``run_init`` under this mock and assert on the resulting
        SPEC.md.
        """
        monkeypatch.chdir(tmp_path)

        desc_path = _write_description_fixture(tmp_path)
        assert desc_path.is_file()
        assert desc_path.read_text() == _DESCRIPTION_FIXTURE_PROSE

        from duplo.spec_writer import DraftInputs

        with patch(
            "duplo.spec_writer._draft_from_inputs",
            side_effect=_stub_draft_from_inputs,
        ) as mock_draft:
            from duplo.spec_writer import _draft_from_inputs as patched

            result = patched(DraftInputs(description=desc_path.read_text()))

        mock_draft.assert_called_once()
        assert result.purpose == _DESCRIPTION_FIXTURE_PURPOSE
        assert result.architecture == _DESCRIPTION_FIXTURE_ARCHITECTURE
        assert result.notes == ""
