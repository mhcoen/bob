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

_IMAGE_REF_FILENAME = "mockup.png"
_PDF_REF_FILENAME = "manual.pdf"
# PNG / PDF magic byte stubs — enough to look like the right type on
# disk; the image Vision call is mocked, and the PDF path does not
# involve an LLM, so no real decoding ever happens.
_IMAGE_REF_BYTES = b"\x89PNG\r\n\x1a\n"
_PDF_REF_BYTES = b"%PDF-1.4\n"
_IMAGE_VISION_DESCRIPTION = "Screenshot of the calculator UI showing inline answers."
_IMAGE_VISION_ROLE = "visual-target"

# Custom SPEC.md body used by the --force overwrite tests.  The prose
# is deliberately distinctive ("HAND-AUTHORED MARKER") so later
# subtasks can assert overwrite-vs-preserve by presence/absence of
# this exact substring.  It is NOT valid SPEC.md (no required
# sections) — the overwrite flow must not parse it; it only checks
# that the file exists before deciding whether to error or overwrite.
_CUSTOM_SPEC_MARKER = "HAND-AUTHORED MARKER"
_CUSTOM_SPEC_CONTENT = (
    "# My Project\n\n"
    "## Purpose\n\n"
    f"{_CUSTOM_SPEC_MARKER}: a calculator I wrote by hand before running duplo init.\n"
)


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


def _write_ref_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Drop a .png and a .pdf into ``tmp_path/ref`` and return their paths.

    Centralized here so later subtasks that run ``run_init`` against a
    ``ref/`` pre-populated with user files reuse the same fixture.
    The file bodies are just magic-byte stubs — the image goes through
    a mocked :func:`_propose_file_role` (see
    :func:`_stub_propose_file_role`) and the PDF takes the
    extension-default ``docs`` path in the real function, which does
    not call an LLM.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    image_path = ref_dir / _IMAGE_REF_FILENAME
    image_path.write_bytes(_IMAGE_REF_BYTES)
    pdf_path = ref_dir / _PDF_REF_FILENAME
    pdf_path.write_bytes(_PDF_REF_BYTES)
    return (image_path, pdf_path)


def _write_custom_spec_fixture(tmp_path: Path) -> Path:
    """Drop a hand-authored SPEC.md into *tmp_path* and return its path.

    Centralized here so later subtasks that exercise the
    ``--force`` / no-``--force`` branches of ``run_init`` against a
    pre-existing SPEC.md reuse the same fixture.  The body contains
    :data:`_CUSTOM_SPEC_MARKER` so overwrite-vs-preserve assertions
    can key off a single substring: present ⇒ untouched, absent ⇒
    overwritten by the template / drafter output.
    """
    path = tmp_path / "SPEC.md"
    path.write_text(_CUSTOM_SPEC_CONTENT)
    return path


def _stub_propose_file_role(path: Path) -> tuple[str, str]:
    """Stand-in for :func:`duplo.spec_writer._propose_file_role`.

    Returns a deterministic Vision-inferred ``(description, role)``
    pair for ``.png`` images so tests avoid a real
    ``query_with_images`` call.  For ``.pdf`` files it reproduces the
    real extension-default branch (``("", "docs")``, no LLM involved)
    so callers can patch a single function and still see the PDF
    proposal come through correctly.  Unexpected extensions fail
    loudly — the fixture contains only the two file types above.
    """
    suffix = path.suffix.lower()
    if suffix == ".png":
        return (_IMAGE_VISION_DESCRIPTION, _IMAGE_VISION_ROLE)
    if suffix == ".pdf":
        return ("", "docs")
    raise AssertionError(f"unexpected fixture extension {suffix!r}")


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


_NETWORK_ERROR_MESSAGE = "Network is unreachable"


def _fetch_site_network_error(*_args, **_kwargs):
    """Stand-in for :func:`duplo.fetcher.fetch_site` that raises a network error.

    Used as a ``side_effect`` when patching ``duplo.init.fetch_site``
    so tests can exercise the URL-flow branch where the fetch itself
    aborts with an exception (as opposed to returning an empty
    ``records`` tuple, which covers the "fetched but got nothing"
    branch).  Raises :class:`ConnectionError` — a builtin that reads
    cleanly as "network error" and doesn't require importing a
    third-party exception hierarchy.
    """
    raise ConnectionError(_NETWORK_ERROR_MESSAGE)


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

    def test_run_init_with_from_description_argument(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with ``--from-description`` pointing to the fixture.

        Stages the description-flow integration path: writes the prose
        fixture, patches ``duplo.spec_writer._draft_from_inputs`` (the
        single LLM call on this code path — :func:`_build_draft_spec`
        delegates to it), and invokes ``run_init`` with
        ``args.from_description`` set to the fixture path.  This
        subtask only confirms the description flow runs to completion
        under the mock and lays down SPEC.md / ref/; the content-level
        assertions on ``## Notes`` verbatim prose and Purpose arrive
        in the next subtask.
        """
        from duplo.init import run_init

        monkeypatch.chdir(tmp_path)

        desc_path = _write_description_fixture(tmp_path)

        with patch(
            "duplo.spec_writer._draft_from_inputs",
            side_effect=_stub_draft_from_inputs,
        ):
            run_init(_make_args(from_description=str(desc_path)))

        # Drain captured output so it does not bleed into later tests.
        capsys.readouterr()

        assert (tmp_path / "SPEC.md").is_file()
        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").is_file()

    def test_run_init_from_description_notes_and_purpose(self, tmp_path, capsys, monkeypatch):
        """Assert SPEC.md Notes has the header + byte-for-byte prose; Purpose is populated.

        Content-level check for the description flow: after
        ``run_init`` completes under the mocked
        ``_draft_from_inputs``, SPEC.md's ``## Notes`` section must
        contain the labeled header followed immediately by the exact
        bytes of ``description.txt`` (no reflow, no trimming), and
        ``## Purpose`` must carry the drafter's LLM output rather than
        a ``FILL IN`` marker.
        """
        from duplo.init import run_init
        from duplo.spec_reader import read_spec
        from duplo.spec_writer import _NOTES_DESCRIPTION_HEADER

        monkeypatch.chdir(tmp_path)

        desc_path = _write_description_fixture(tmp_path)

        with patch(
            "duplo.spec_writer._draft_from_inputs",
            side_effect=_stub_draft_from_inputs,
        ):
            run_init(_make_args(from_description=str(desc_path)))

        capsys.readouterr()

        spec_text = (tmp_path / "SPEC.md").read_text()
        expected_notes_block = f"{_NOTES_DESCRIPTION_HEADER}\n\n{_DESCRIPTION_FIXTURE_PROSE}"
        assert expected_notes_block in spec_text

        spec = read_spec(target_dir=tmp_path)
        assert spec is not None
        assert spec.purpose == _DESCRIPTION_FIXTURE_PURPOSE
        assert "FILL IN" not in spec.purpose
        assert spec.fill_in_purpose is False


class TestInitWithExistingRefFilesProposesRoles:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_with_existing_ref_files_proposes_roles``.
    """

    def test_ref_fixture_and_propose_file_role_mock(self, tmp_path, monkeypatch):
        """Create a tmpdir with ref/ containing a .png and a .pdf; mock
        ``_propose_file_role`` for the image.

        Stages the existing-ref-files integration test: drops one
        image and one PDF into ``tmp_path/ref`` and confirms that
        patching ``duplo.init._propose_file_role`` (the name
        :func:`_scan_existing_ref_files` sees after its top-level
        import from ``duplo.spec_writer``) routes the Vision-inferred
        role to the image while the PDF falls to the extension-default
        ``docs`` — all without any real ``claude -p`` call.  Later
        subtasks call ``run_init`` under this mock and assert on
        SPEC.md ``## References``.
        """
        monkeypatch.chdir(tmp_path)

        image_path, pdf_path = _write_ref_fixture(tmp_path)
        assert image_path.is_file()
        assert image_path.suffix == ".png"
        assert pdf_path.is_file()
        assert pdf_path.suffix == ".pdf"
        assert image_path.parent == tmp_path / "ref"
        assert pdf_path.parent == tmp_path / "ref"

        with patch(
            "duplo.init._propose_file_role",
            side_effect=_stub_propose_file_role,
        ) as mock_propose:
            from duplo.init import _propose_file_role as patched

            image_result = patched(image_path)
            pdf_result = patched(pdf_path)

        assert mock_propose.call_count == 2
        assert image_result == (_IMAGE_VISION_DESCRIPTION, _IMAGE_VISION_ROLE)
        assert pdf_result == ("", "docs")

    def test_run_init_with_existing_ref_files(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` against a ``ref/`` pre-populated with user files.

        Stages the existing-ref-files integration path: drops the .png
        and .pdf fixture into ``tmp_path/ref`` and invokes ``run_init``
        under ``--from-description`` (the simplest flow that exercises
        :func:`_scan_existing_ref_files` — the no-args flow does not
        scan, and the URL flow needs extra ``fetch_site`` /
        ``validate_product_url`` mocks we don't need here).  Two mocks
        keep the LLM out: :func:`duplo.init._propose_file_role` routes
        the image to a deterministic role via
        :func:`_stub_propose_file_role`, and
        :func:`duplo.spec_writer._draft_from_inputs` returns the same
        :class:`ProductSpec` the description tests use.  This subtask
        only confirms the flow runs to completion under the mocks and
        lays down SPEC.md / ref/; the content-level assertions on
        ``## References`` entries arrive in the next subtask.
        """
        from duplo.init import run_init

        monkeypatch.chdir(tmp_path)

        image_path, pdf_path = _write_ref_fixture(tmp_path)
        desc_path = _write_description_fixture(tmp_path)

        with (
            patch(
                "duplo.init._propose_file_role",
                side_effect=_stub_propose_file_role,
            ),
            patch(
                "duplo.spec_writer._draft_from_inputs",
                side_effect=_stub_draft_from_inputs,
            ),
        ):
            run_init(_make_args(from_description=str(desc_path)))

        # Drain captured output so it does not bleed into later tests.
        capsys.readouterr()

        assert (tmp_path / "SPEC.md").is_file()
        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").is_file()
        # Fixture files must not have been disturbed by run_init.
        assert image_path.is_file()
        assert pdf_path.is_file()

    def test_run_init_references_populated_with_proposed_roles(
        self, tmp_path, capsys, monkeypatch
    ):
        """Assert ``## References`` has both files, both ``proposed: true``,
        image carries the Vision-inferred role, PDF carries ``docs``.

        Content-level check for the existing-ref-files flow: after
        ``run_init`` completes under the mocked ``_propose_file_role``
        and ``_draft_from_inputs``, SPEC.md's ``## References`` section
        must list one entry per fixture file, both flagged
        ``proposed: true`` (user-reviewable), with the image's role
        coming from the Vision stub (``visual-target``) and the PDF's
        role from the real extension-default branch (``docs``).
        """
        from duplo.init import run_init
        from duplo.spec_reader import read_spec

        monkeypatch.chdir(tmp_path)

        _write_ref_fixture(tmp_path)
        desc_path = _write_description_fixture(tmp_path)

        with (
            patch(
                "duplo.init._propose_file_role",
                side_effect=_stub_propose_file_role,
            ),
            patch(
                "duplo.spec_writer._draft_from_inputs",
                side_effect=_stub_draft_from_inputs,
            ),
        ):
            run_init(_make_args(from_description=str(desc_path)))

        capsys.readouterr()

        spec = read_spec(target_dir=tmp_path)
        assert spec is not None

        by_name = {entry.path.name: entry for entry in spec.references}
        assert set(by_name) == {_IMAGE_REF_FILENAME, _PDF_REF_FILENAME}

        image_entry = by_name[_IMAGE_REF_FILENAME]
        pdf_entry = by_name[_PDF_REF_FILENAME]

        assert image_entry.proposed is True
        assert pdf_entry.proposed is True

        assert image_entry.roles == [_IMAGE_VISION_ROLE]
        assert pdf_entry.roles == ["docs"]


class TestInitUrlFetchFailureWritesScrapeNone:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_url_fetch_failure_writes_scrape_none``.
    """

    def test_fetch_site_mocked_to_raise_network_error(self, tmp_path, monkeypatch):
        """Mock ``fetch_site`` to raise an exception (network error).

        Stages the URL-fetch-failure integration test: confirms that
        patching ``duplo.init.fetch_site`` with a ``side_effect`` that
        raises :class:`ConnectionError` routes the exception to callers
        (rather than e.g. swallowing it or returning ``None``).  Later
        subtasks call ``run_init`` under this mock and assert on the
        template-with-``scrape: none`` SPEC.md that the URL-flow
        produces when the fetch aborts.
        """
        import pytest

        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.init.fetch_site",
            side_effect=_fetch_site_network_error,
        ) as mock_fetch:
            from duplo.init import fetch_site as patched_fetch_site

            with pytest.raises(ConnectionError, match=_NETWORK_ERROR_MESSAGE):
                patched_fetch_site(_IDENTIFIED_FIXTURE_URL, scrape_depth="shallow")

        mock_fetch.assert_called_once_with(_IDENTIFIED_FIXTURE_URL, scrape_depth="shallow")

    def test_run_init_with_url_under_network_error(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with a URL argument while ``fetch_site`` raises.

        Stages the URL-fetch-failure integration path: patches
        ``duplo.init.fetch_site`` so any call raises
        :class:`ConnectionError`, then invokes ``run_init`` with
        ``args.url`` set.  :func:`_run_url` must treat the exception as
        a fetch failure (equivalent to "fetched but got nothing"),
        fall through to the template-only branch, and still lay down
        SPEC.md plus ``ref/``.  The content-level assertions on
        ``## Sources`` carrying ``scrape: none`` and ``## Purpose``
        keeping its ``FILL IN`` marker arrive in the next subtask;
        this subtask only confirms the URL flow runs to completion
        under the raising mock.  The validator and drafter are not
        patched because the failure branch never reaches them (both
        are gated on ``fetch_ok`` / ``text`` in :func:`_run_url`), so
        any unintended call would surface as a real-LLM failure.
        """
        from duplo.init import run_init

        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.init.fetch_site",
            side_effect=_fetch_site_network_error,
        ):
            run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))

        # Drain captured output so it does not bleed into later tests.
        capsys.readouterr()

        assert (tmp_path / "SPEC.md").is_file()
        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").is_file()

    def test_run_init_fetch_failure_writes_template_with_scrape_none(
        self, tmp_path, capsys, monkeypatch
    ):
        """Assert exit 0, SPEC.md written, URL in Sources with ``scrape: none``,
        Purpose keeps its ``FILL IN`` marker.

        Content-level check for the URL-fetch-failure flow: after
        ``run_init`` runs under a ``fetch_site`` that raises
        :class:`ConnectionError`, :func:`_run_url` must fall through to
        the template-only branch.  The function returns normally (no
        ``SystemExit(1)``) — the fetch failure is recoverable: the URL
        is recorded in ``## Sources`` with ``scrape: none`` so the user
        can re-enable scraping after fixing the network, and
        ``## Purpose`` keeps its ``FILL IN`` marker so the user knows
        the drafter had no scraped content to work from.  The test
        also confirms the architecture FILL IN is preserved (the
        template branch does not draft anything from the URL alone).
        """
        import pytest

        from duplo.init import run_init
        from duplo.spec_reader import read_spec

        monkeypatch.chdir(tmp_path)

        with patch(
            "duplo.init.fetch_site",
            side_effect=_fetch_site_network_error,
        ):
            # run_init returns None on the recoverable fetch-failure
            # path; a SystemExit would mean exit code 1 (the path
            # guarded by the existing-SPEC.md / force check, or an
            # unrelated failure), which is NOT what this path should
            # produce.
            result = run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))
            assert result is None

        capsys.readouterr()

        spec_path = tmp_path / "SPEC.md"
        assert spec_path.is_file()

        spec = read_spec(target_dir=tmp_path)
        assert spec is not None

        source_urls = [s.url for s in spec.sources]
        assert _IDENTIFIED_FIXTURE_URL in source_urls
        matching = [s for s in spec.sources if s.url == _IDENTIFIED_FIXTURE_URL]
        assert len(matching) == 1
        entry = matching[0]
        assert entry.scrape == "none"

        assert spec.fill_in_purpose is True
        assert spec.fill_in_architecture is True

        # Sanity: run_init must not raise SystemExit on this path even
        # when called a second time with --force (the file exists now,
        # so without --force it WOULD exit 1; that confirms the exit-0
        # behavior above was about the fetch-failure branch, not about
        # a missing SPEC.md).
        with patch(
            "duplo.init.fetch_site",
            side_effect=_fetch_site_network_error,
        ):
            with pytest.raises(SystemExit) as excinfo:
                run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))
            assert excinfo.value.code == 1

        capsys.readouterr()


class TestInitForceOverwritesExistingSpec:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_force_overwrites_existing_spec``.
    """

    def test_custom_spec_fixture_in_tmpdir(self, tmp_path, monkeypatch):
        """Create a tmpdir with an existing SPEC.md containing custom content.

        Stages the force-overwrite integration test: drops a
        hand-authored SPEC.md (distinguished by
        :data:`_CUSTOM_SPEC_MARKER`) into ``tmp_path`` and confirms
        the fixture lands on disk with the expected bytes.  Later
        subtasks run ``run_init`` against this tmpdir with and
        without ``--force`` and assert that ``--force`` overwrites
        the marker while the default path errors out and leaves the
        marker intact.
        """
        monkeypatch.chdir(tmp_path)

        spec_path = _write_custom_spec_fixture(tmp_path)

        assert spec_path == tmp_path / "SPEC.md"
        assert spec_path.is_file()
        assert spec_path.read_text() == _CUSTOM_SPEC_CONTENT
        assert _CUSTOM_SPEC_MARKER in spec_path.read_text()

    def test_run_init_with_force_overwrites_existing_spec(self, tmp_path, capsys, monkeypatch):
        """Run ``run_init`` with ``--force``; assert SPEC.md overwritten
        with new content.

        Content-level check for the force-overwrite flow: after a
        hand-authored SPEC.md (carrying :data:`_CUSTOM_SPEC_MARKER`) is
        in place, invoking ``run_init`` under the no-args flow with
        ``force=True`` must replace the file with the no-args template
        output.  The marker must be gone and the template's
        distinguishing ``"How the pieces fit together:"`` header must
        be present — that combination proves the file was rewritten
        rather than merely appended to.  The no-args flow is used
        here because it is the simplest path that exercises
        :func:`_run_no_args` (which holds one of the ``force`` guards);
        the URL and description flows have their own ``force`` guards
        verified indirectly by the existing subtasks plus this one.
        """
        from duplo.init import run_init

        monkeypatch.chdir(tmp_path)

        spec_path = _write_custom_spec_fixture(tmp_path)
        assert _CUSTOM_SPEC_MARKER in spec_path.read_text()

        run_init(_make_args(force=True))

        capsys.readouterr()

        assert spec_path.is_file()
        new_text = spec_path.read_text()
        assert _CUSTOM_SPEC_MARKER not in new_text
        assert "How the pieces fit together:" in new_text
        assert "<FILL IN: one or two sentences describing what you're building>" in new_text

    def test_run_init_without_force_errors_and_preserves_existing_spec(
        self, tmp_path, capsys, monkeypatch
    ):
        """Run ``run_init`` without ``--force``; assert exits 1 and SPEC.md unchanged.

        Content-level check for the default (no-``--force``) branch of
        the force-overwrite flow: after a hand-authored SPEC.md
        (carrying :data:`_CUSTOM_SPEC_MARKER`) is in place, invoking
        ``run_init`` under the no-args flow with ``force=False`` must
        raise :class:`SystemExit` with code 1, print the pinned
        :data:`_SPEC_EXISTS_ERROR` message to stderr (per
        INIT-design.md), and leave the file bytes-identical to the
        fixture.  This is the complement of
        :meth:`test_run_init_with_force_overwrites_existing_spec`:
        together they prove ``run_init`` respects the ``force`` flag
        both ways — overwrites when set, errors out when not — without
        ever clobbering user content by accident.
        """
        import pytest

        from duplo.init import _SPEC_EXISTS_ERROR, run_init

        monkeypatch.chdir(tmp_path)

        spec_path = _write_custom_spec_fixture(tmp_path)
        original_bytes = spec_path.read_bytes()
        assert _CUSTOM_SPEC_MARKER in spec_path.read_text()

        with pytest.raises(SystemExit) as excinfo:
            run_init(_make_args())
        assert excinfo.value.code == 1

        captured = capsys.readouterr()
        assert _SPEC_EXISTS_ERROR in captured.err

        # SPEC.md must be byte-for-byte identical to the fixture —
        # the error path must not rewrite, truncate, or touch it.
        assert spec_path.read_bytes() == original_bytes
        assert _CUSTOM_SPEC_MARKER in spec_path.read_text()


# ---------------------------------------------------------------------------
# 6.25 fixtures: end-to-end flow from ``run_init`` through ``_subsequent_run``
# ---------------------------------------------------------------------------

# Architecture prose used to fill in the ``<FILL IN>`` marker that
# ``draft_spec`` leaves behind in the URL flow.  Distinctive enough that
# later subtasks can assert the edited spec round-trips through
# :func:`read_spec` with ``fill_in_architecture`` flipped to ``False``.
_FILLED_IN_ARCHITECTURE = "Swift 5.9, SwiftUI, macOS 14+."

# The ``<FILL IN>`` marker that the URL-flow drafter leaves in
# ``## Architecture`` (matches the ``drafted`` string in
# :meth:`TestInitUrlProducesPrefilledSpec.test_run_init_with_url_argument`).
_ARCHITECTURE_FILL_IN = "<FILL IN: language, framework, platform, constraints>"

# ``draft_spec`` return value used for the 6.25 URL flow.  Purpose is
# long enough (>50 chars) to clear ``validate_for_run``'s sparse check;
# the single ``product-reference`` source with ``scrape: deep`` makes
# :func:`scrapeable_sources` non-empty so ``_subsequent_run`` takes the
# declared-sources branch (which exercises ``fetch_site`` per PLAN.md
# § 6.25.2).  ``## Architecture`` intentionally carries the ``FILL IN``
# marker — the test flips it to :data:`_FILLED_IN_ARCHITECTURE` after
# ``run_init`` completes.
_END_TO_END_DRAFTED_SPEC = (
    "## Purpose\n\n"
    "Numi — a calculator app that shows answers inline as you type, "
    "with history and unit conversion.\n\n"
    "## Sources\n\n"
    f"- {_IDENTIFIED_FIXTURE_URL}\n"
    "  role: product-reference\n"
    "  scrape: deep\n\n"
    "## Architecture\n\n"
    f"{_ARCHITECTURE_FILL_IN}\n"
)


def _make_end_to_end_features():
    """Return the feature list the mocked ``extract_features`` should yield.

    Two features give ``generate_roadmap`` something to assemble and
    ``select_features`` something to filter.  Names are intentionally
    distinct so ``save_features``'s ``_find_duplicate_groups`` (which is
    mocked away) never has to decide.  Built lazily so the import of
    :class:`duplo.extractor.Feature` does not become a load-time
    dependency of this module — keeps import failures scoped to tests
    that actually use this fixture.
    """
    from duplo.extractor import Feature

    return [
        Feature(
            name="Inline calculation",
            description="Answers appear inline as the user types the expression.",
            category="Calculation",
        ),
        Feature(
            name="Unit conversion",
            description="Convert between units like '3 miles in km' inline.",
            category="Calculation",
        ),
    ]


def _make_end_to_end_roadmap():
    """Return the roadmap the mocked ``generate_roadmap`` should yield."""
    return [
        {
            "phase": 1,
            "title": "Core calculator",
            "goal": "Inline expression evaluation",
            "features": ["Inline calculation", "Unit conversion"],
            "test": "User types an expression and sees the result inline.",
        },
    ]


def _make_end_to_end_plan_content():
    """Return the PLAN.md body the mocked ``generate_phase_plan`` should yield."""
    return (
        "# Numi — Phase 1: Core calculator\n\n"
        "- [ ] Set up project scaffolding\n"
        '- [ ] Implement inline evaluation [feat: "Inline calculation"]\n'
        '- [ ] Implement unit conversion [feat: "Unit conversion"]\n'
    )


def _select_all_features(features, **_kwargs):
    """Mock replacement for ``select_features`` that returns every feature.

    Matches the helper in ``tests/test_phase5_integration.py``; duplicated
    here so the two test modules stay independent.
    """
    return list(features)


class TestInitThenDuploRunWorksEndToEnd:
    """Per PLAN.md § 'Automated integration tests':
    ``test_init_then_duplo_run_works_end_to_end``.
    """

    def test_run_init_then_edit_architecture_then_subsequent_run(
        self, tmp_path, capsys, monkeypatch
    ):
        """Run ``run_init`` with a URL, fill in ``## Architecture``, then
        run ``_subsequent_run`` against the same tmpdir.

        Stages the end-to-end integration test for PLAN.md § 6.25:
        invokes the URL-flow ``run_init`` (under the same three mocks
        the 6.20 subtasks use: ``fetch_site`` /
        ``validate_product_url`` / ``draft_spec``) so SPEC.md and
        ``ref/`` land on disk, programmatically rewrites
        ``## Architecture`` to replace the drafter's
        :data:`_ARCHITECTURE_FILL_IN` marker with
        :data:`_FILLED_IN_ARCHITECTURE` (so :func:`validate_for_run`
        stops blocking), then calls :func:`_subsequent_run` directly
        under the mocks that keep the pipeline off the LLM and network
        (``fetch_site``, ``extract_features``, the in-``save_features``
        ``_find_duplicate_groups`` call, ``select_features``,
        ``generate_roadmap``, ``generate_phase_plan``).
        ``_subsequent_run`` is called — not ``main()`` — because the
        task targets the pipeline proper, not the migration gate
        (subtask 6.25.3 will assert no migration message was printed,
        which is trivially true when the gate is bypassed).  This
        subtask only confirms the three-action sequence runs to
        completion under the mocks and lays down PLAN.md; the
        content-level assertions on PLAN.md / spec consumption arrive
        in later 6.25 subtasks.
        """
        from duplo.init import run_init
        from duplo.pipeline import _subsequent_run
        from duplo.validator import ValidationResult

        monkeypatch.chdir(tmp_path)

        # --- Action 1: run_init with a URL to produce SPEC.md. -------------
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
            patch("duplo.init.draft_spec", return_value=_END_TO_END_DRAFTED_SPEC),
        ):
            run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))

        # Drain captured output so it does not bleed into the
        # _subsequent_run output the next step produces.
        capsys.readouterr()

        spec_path = tmp_path / "SPEC.md"
        assert spec_path.is_file()
        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").is_file()
        # Precondition for the edit step: the drafter left the marker.
        assert _ARCHITECTURE_FILL_IN in spec_path.read_text()

        # --- Action 2: programmatically edit SPEC.md to fill in Architecture.
        spec_text = spec_path.read_text()
        spec_text = spec_text.replace(_ARCHITECTURE_FILL_IN, _FILLED_IN_ARCHITECTURE)
        spec_path.write_text(spec_text)
        assert _ARCHITECTURE_FILL_IN not in spec_path.read_text()
        assert _FILLED_IN_ARCHITECTURE in spec_path.read_text()

        # --- Action 3: _subsequent_run against the same tmpdir. ------------
        # The fetch_site fixture reused here is the same one run_init
        # consumed; _scrape_declared_sources will re-fetch the URL
        # declared in SPEC.md via the patched duplo.pipeline.fetch_site and
        # receive the same deterministic tuple.
        with (
            patch(
                "duplo.pipeline.fetch_site",
                return_value=_fetch_site_identified_fixture(),
            ),
            patch(
                "duplo.pipeline.extract_features",
                return_value=_make_end_to_end_features(),
            ),
            # save_features's own early-return handles the empty-
            # existing-list case without LLM, but the post-merge pass
            # calls _find_duplicate_groups once 2+ features are in the
            # list.  Patch it to skip that LLM call.
            patch("duplo.saver._find_duplicate_groups", return_value=[]),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
                return_value=_make_end_to_end_roadmap(),
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value=_make_end_to_end_plan_content(),
            ),
        ):
            _subsequent_run()

        capsys.readouterr()

        # Minimal staging assertion: the pipeline ran to completion and
        # produced PLAN.md.  Detailed content-level assertions (PLAN.md
        # body, spec consumption, migration-message absence) arrive in
        # the follow-on 6.25 subtasks.
        assert (tmp_path / "PLAN.md").is_file()

    def test_end_to_end_plan_produced_no_migration_no_spec_consumption(
        self, tmp_path, capsys, monkeypatch
    ):
        """Assert PLAN.md produced, no migration message printed, pipeline
        consumed SPEC.md correctly.

        Content-level check for PLAN.md § 6.25.3.  Re-runs the same
        three-action sequence the staging test uses (URL-flow
        ``run_init`` → hand-edit Architecture →
        :func:`_subsequent_run`) under the same mocks, then asserts on
        the three things the subtask calls out:

        * **PLAN.md produced**: the file exists and its body contains
          the mocked :func:`generate_phase_plan` output verbatim.  Both
          feature names (``"Inline calculation"`` and
          ``"Unit conversion"``) must appear so a regression that
          loses the ``[feat: ...]`` annotations or the phase H1 shows
          up here, not just in the staging test's ``is_file()`` check.
        * **No migration message printed**: neither stdout nor stderr
          contains the pinned opening line of :data:`_MIGRATION_MESSAGE`
          (``"This project predates the SPEC.md / ref/ redesign"``).
          :func:`_subsequent_run` is called directly — it does not run
          the migration gate — so this is a property of the pipeline
          proper, not of the gate.  If a future change routes
          ``_subsequent_run`` through ``_check_migration`` (which would
          fire here because ``.duplo/duplo.json`` gets written during
          the run), this assertion catches it.
        * **Pipeline consumed SPEC.md correctly**: the ``read_spec``
          banner (``"Product spec loaded from SPEC.md"``) appears in
          stdout, and the mocked features land in
          ``.duplo/duplo.json``.  Those two signals together prove the
          spec reached both :func:`validate_for_run` (the banner only
          prints when ``read_spec`` succeeds) and
          :func:`extract_features` (whose output is what
          :func:`save_features` writes to ``duplo.json``).
        """
        import json

        from duplo.init import run_init
        from duplo.pipeline import _subsequent_run
        from duplo.migration import _MIGRATION_MESSAGE
        from duplo.validator import ValidationResult

        monkeypatch.chdir(tmp_path)

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
            patch("duplo.init.draft_spec", return_value=_END_TO_END_DRAFTED_SPEC),
        ):
            run_init(_make_args(url=_IDENTIFIED_FIXTURE_URL))

        capsys.readouterr()

        spec_path = tmp_path / "SPEC.md"
        spec_text = spec_path.read_text()
        spec_text = spec_text.replace(_ARCHITECTURE_FILL_IN, _FILLED_IN_ARCHITECTURE)
        spec_path.write_text(spec_text)

        plan_content = _make_end_to_end_plan_content()
        features = _make_end_to_end_features()

        with (
            patch(
                "duplo.pipeline.fetch_site",
                return_value=_fetch_site_identified_fixture(),
            ),
            patch(
                "duplo.pipeline.extract_features",
                return_value=features,
            ),
            patch("duplo.saver._find_duplicate_groups", return_value=[]),
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ),
            patch(
                "duplo.pipeline.generate_roadmap",
                return_value=_make_end_to_end_roadmap(),
            ),
            patch(
                "duplo.pipeline.generate_phase_plan",
                return_value=plan_content,
            ),
        ):
            _subsequent_run()

        captured = capsys.readouterr()

        # --- PLAN.md produced --------------------------------------------
        plan_path = tmp_path / "PLAN.md"
        assert plan_path.is_file()
        plan_text = plan_path.read_text()
        # Mocked plan body must land on disk verbatim — save_plan does
        # not touch the generated content itself.
        assert plan_content in plan_text
        for feat in features:
            assert feat.name in plan_text

        # --- No migration message printed --------------------------------
        migration_signal = _MIGRATION_MESSAGE.splitlines()[0]
        assert migration_signal not in captured.out
        assert migration_signal not in captured.err

        # --- Pipeline consumed SPEC.md correctly -------------------------
        # Banner only prints when read_spec returned a ProductSpec.
        assert "Product spec loaded from SPEC.md" in captured.out
        # Features from mocked extract_features must have reached
        # save_features → duplo.json.
        duplo_json = tmp_path / ".duplo" / "duplo.json"
        assert duplo_json.is_file()
        data = json.loads(duplo_json.read_text())
        saved_names = {f["name"] for f in data.get("features", [])}
        expected_names = {f.name for f in features}
        assert expected_names.issubset(saved_names)

    def test_subsequent_run_pipeline_mocks_route_correctly(self, tmp_path, monkeypatch):
        """Confirm the three mocks 6.25.2 names route to the functions
        ``_subsequent_run`` actually calls.

        Staging step for PLAN.md § 6.25.2: the parent end-to-end test
        already wires these mocks in its Action 3 block, but this
        standalone test pins them down at the routing layer — the same
        pattern 6.20.1 and 6.22.1 use — so a future rename that moves
        the import (e.g.  ``from duplo.fetcher import fetch_site`` →
        ``from duplo import fetcher``) fails here with a targeted message
        instead of surfacing as a confusing failure in the end-to-end
        test.  Three mocks, three checks:

        * ``fetch_site`` patched at ``duplo.pipeline.fetch_site`` — this is
          the binding :func:`_scrape_declared_sources` sees.  Called
          explicitly with ``scrape_depth="deep"`` here because that is
          the depth the 6.25 end-to-end flow uses (SPEC.md declares
          ``scrape: deep`` on the one source).
        * ``extract_features`` patched at ``duplo.pipeline.extract_features``
          — the binding :func:`_subsequent_run` closes over when it
          calls ``extract_features(combined_text, ...)``.
        * ``select_features`` and ``select_issues`` patched at
          ``duplo.main.select_features`` / ``duplo.main.select_issues``
          — the two interactive selectors on the ``_subsequent_run``
          path.  Both must route without hitting ``input()``, which is
          what the parent end-to-end test depends on to run headless.
        """
        from duplo.extractor import Feature

        monkeypatch.chdir(tmp_path)

        fetch_fixture = _fetch_site_identified_fixture()
        features_fixture = _make_end_to_end_features()

        sentinel_issue = {"description": "sentinel issue", "status": "open"}

        with (
            patch("duplo.pipeline.fetch_site", return_value=fetch_fixture) as mock_fetch,
            patch(
                "duplo.pipeline.extract_features",
                return_value=features_fixture,
            ) as mock_extract,
            patch(
                "duplo.main.select_features",
                side_effect=_select_all_features,
            ) as mock_select_features,
            patch(
                "duplo.main.select_issues",
                return_value=[sentinel_issue],
            ) as mock_select_issues,
        ):
            from duplo.main import (
                select_features as patched_select_features,
                select_issues as patched_select_issues,
            )
            from duplo.pipeline import (
                extract_features as patched_extract,
                fetch_site as patched_fetch,
            )

            fetch_result = patched_fetch(_IDENTIFIED_FIXTURE_URL, scrape_depth="deep")
            extract_result = patched_extract(_IDENTIFIED_FIXTURE_TEXT)
            select_features_result = patched_select_features(list(features_fixture))
            select_issues_result = patched_select_issues([sentinel_issue])

        assert fetch_result == fetch_fixture
        mock_fetch.assert_called_once_with(_IDENTIFIED_FIXTURE_URL, scrape_depth="deep")

        assert extract_result == features_fixture
        assert all(isinstance(f, Feature) for f in extract_result)
        mock_extract.assert_called_once_with(_IDENTIFIED_FIXTURE_TEXT)

        # ``select_features`` is a pass-through in the stub, so the
        # result must be the same list by value (new list, same items)
        # — that is what the parent test relies on to keep every
        # extracted feature in the phase.
        assert select_features_result == list(features_fixture)
        mock_select_features.assert_called_once()

        assert select_issues_result == [sentinel_issue]
        mock_select_issues.assert_called_once_with([sentinel_issue])
