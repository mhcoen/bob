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
