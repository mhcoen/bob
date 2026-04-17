"""Phase 6 end-to-end integration tests for ``duplo init``.

Each test constructs a fixture project in a tmpdir, runs ``run_init``
programmatically, and asserts on the output state. LLM calls must be
mocked so tests do not depend on claude -p availability or network.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

from duplo.doc_tables import DocStructures
from duplo.fetcher import PageRecord


_IDENTIFIED_FIXTURE_URL = "https://numi.app"
_IDENTIFIED_FIXTURE_TEXT = (
    "=== https://numi.app ===\nNumi — a calculator app that shows answers inline as you type."
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
