"""Tests for duplo.init."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from duplo.doc_tables import DocStructures
from duplo.fetcher import PageRecord
from duplo.init import (
    _DESCRIPTION_FILE_NOT_FOUND,
    _DESCRIPTION_NEXT_STEPS,
    _NO_ARGS_NEXT_STEPS,
    _REF_README_CONTENT,
    _SPEC_EXISTS_ERROR,
    _STDIN_TTY_PROMPT,
    _URL_FETCH_FAILED_PRELUDE,
    _URL_NEXT_STEPS_FETCH_FAILED,
    _URL_NEXT_STEPS_IDENTIFIED,
    _URL_NEXT_STEPS_UNIDENTIFIED,
    run_init,
)
from duplo.spec_reader import ProductSpec
from duplo.spec_writer import format_spec
from duplo.validator import ValidationResult


def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "url": None,
        "from_description": None,
        "deep": False,
        "force": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRunInitNoArgsExistingSpec:
    """Per INIT-design.md § 'duplo init against an existing SPEC.md'."""

    def test_existing_spec_without_force_exits_1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("pre-existing user content\n")

        with pytest.raises(SystemExit) as exc_info:
            run_init(_make_args())

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert _SPEC_EXISTS_ERROR in captured.err
        # Existing SPEC.md must not be clobbered.
        assert (tmp_path / "SPEC.md").read_text() == "pre-existing user content\n"

    def test_existing_spec_with_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("pre-existing user content\n")

        run_init(_make_args(force=True))

        new_content = (tmp_path / "SPEC.md").read_text()
        assert new_content != "pre-existing user content\n"
        assert "How the pieces fit together:" in new_content

    def test_error_message_matches_init_design(self):
        # INIT-design.md pins this exact wording.
        assert "SPEC.md already exists in this directory." in _SPEC_EXISTS_ERROR
        assert "`duplo init --force`" in _SPEC_EXISTS_ERROR
        assert "`duplo`" in _SPEC_EXISTS_ERROR

    def test_no_existing_spec_no_force_proceeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "SPEC.md").exists()

        run_init(_make_args())

        assert (tmp_path / "SPEC.md").exists()
        assert "How the pieces fit together:" in (tmp_path / "SPEC.md").read_text()


class TestRunInitNoArgsRefDir:
    """Per INIT-design.md § 'duplo init (no arguments)': ref/ creation."""

    def test_creates_ref_dir_when_absent(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "ref").exists()

        run_init(_make_args())

        ref_dir = tmp_path / "ref"
        assert ref_dir.is_dir()
        captured = capsys.readouterr()
        assert "Created ref/ (empty)." in captured.out

    def test_skips_creation_when_ref_dir_exists(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        # Pre-existing user file must not be touched.
        user_file = ref_dir / "mockup.png"
        user_file.write_bytes(b"user data")

        run_init(_make_args())

        assert ref_dir.is_dir()
        assert user_file.read_bytes() == b"user data"
        captured = capsys.readouterr()
        assert "Created ref/ (empty)." not in captured.out


class TestRunInitNoArgsRefReadme:
    """Per INIT-design.md § 'ref/README.md content': write-once semantics."""

    def test_writes_readme_when_absent(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        readme = tmp_path / "ref" / "README.md"
        assert readme.is_file()
        assert readme.read_text() == _REF_README_CONTENT
        captured = capsys.readouterr()
        assert "Created ref/README.md." in captured.out

    def test_content_matches_init_design(self):
        # INIT-design.md § "ref/README.md content" pins this text.
        assert _REF_README_CONTENT.startswith("# ref/\n")
        assert "Accepted file types:" in _REF_README_CONTENT
        assert "**This directory can be empty.**" in _REF_README_CONTENT
        assert "visual-target, behavioral-target, docs," in _REF_README_CONTENT
        assert "See SPEC-guide.md (in the project root)" in _REF_README_CONTENT

    def test_does_not_overwrite_existing_readme(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        existing = "user-authored README\n"
        readme = ref_dir / "README.md"
        readme.write_text(existing)

        run_init(_make_args())

        assert readme.read_text() == existing
        captured = capsys.readouterr()
        assert "Created ref/README.md." not in captured.out

    def test_writes_readme_even_when_ref_dir_preexists(self, tmp_path, capsys, monkeypatch):
        # ref/ exists but README.md does not — still write it.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ref").mkdir()

        run_init(_make_args())

        readme = tmp_path / "ref" / "README.md"
        assert readme.read_text() == _REF_README_CONTENT
        captured = capsys.readouterr()
        assert "Created ref/README.md." in captured.out


class TestRunInitNoArgsSpecWrite:
    """Per INIT-design.md § 'duplo init (no arguments)': SPEC.md is the
    template produced by format_spec on an empty ProductSpec."""

    def test_spec_matches_format_spec_of_empty_product_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        written = (tmp_path / "SPEC.md").read_text()
        assert written == format_spec(ProductSpec())

    def test_spec_has_fill_in_markers_for_required_sections(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        written = (tmp_path / "SPEC.md").read_text()
        # Required sections must carry the template's <FILL IN> markers
        # so the user knows where to author content.
        assert "## Purpose" in written
        assert "<FILL IN: one or two sentences" in written
        assert "## Architecture" in written
        assert "<FILL IN: language, framework, platform, constraints>" in written

    def test_prints_wrote_spec_message(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        captured = capsys.readouterr()
        assert "Wrote SPEC.md (template, no inputs)." in captured.out


class TestRunInitNoArgsOutputMessage:
    """Per INIT-design.md § 'duplo init (no arguments)': full output
    including the 'Next steps:' block."""

    def test_prints_next_steps_block(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        captured = capsys.readouterr()
        assert _NO_ARGS_NEXT_STEPS in captured.out

    def test_next_steps_content_matches_init_design(self):
        # INIT-design.md § "duplo init (no arguments)" pins this text.
        assert _NO_ARGS_NEXT_STEPS.startswith("Next steps:\n")
        assert "1. Open SPEC.md in your editor." in _NO_ARGS_NEXT_STEPS
        assert "<FILL IN> marker" in _NO_ARGS_NEXT_STEPS
        assert "2. (Optional) Drop reference files into ref/" in _NO_ARGS_NEXT_STEPS
        assert "3. (Optional) Add a URL to ## Sources" in _NO_ARGS_NEXT_STEPS
        assert "4. Run `duplo` to extract features" in _NO_ARGS_NEXT_STEPS

    def test_output_ordering_matches_init_design(self, tmp_path, capsys, monkeypatch):
        # Created ref/, Created ref/README.md, Wrote SPEC.md, blank
        # line, then Next steps — in that order.
        monkeypatch.chdir(tmp_path)

        run_init(_make_args())

        out = capsys.readouterr().out
        idx_ref = out.index("Created ref/ (empty).")
        idx_readme = out.index("Created ref/README.md.")
        idx_spec = out.index("Wrote SPEC.md (template, no inputs).")
        idx_next = out.index("Next steps:")
        assert idx_ref < idx_readme < idx_spec < idx_next
        # Blank line separates the "Wrote SPEC.md" line from "Next steps:".
        between = out[idx_spec:idx_next]
        assert "\n\n" in between


def _fetch_site_success(
    text: str = "=== https://numi.app ===\nNumi — a calculator.",
    url: str = "https://numi.app",
):
    """Build a fetch_site return tuple that looks like a successful shallow fetch."""
    record = PageRecord(
        url=url,
        fetched_at="2026-04-17T00:00:00+00:00",
        content_hash="deadbeef",
    )
    return (text, [], DocStructures(), [record], {url: "<html></html>"})


_FETCH_SITE_FAILURE = ("", [], DocStructures(), [], {})


class TestRunInitUrlSuccess:
    """Per INIT-design.md § 'duplo init <url>' happy path."""

    def test_identified_product_prints_identity_and_drafts_spec(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()) as mock_fetch,
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                    unclear_boundaries=False,
                ),
            ) as mock_val,
            patch(
                "duplo.init.draft_spec",
                return_value="## Purpose\n\nNumi.\n## Sources\n\n- https://numi.app\n",
            ) as mock_draft,
        ):
            run_init(_make_args(url="https://numi.app"))

        mock_fetch.assert_called_once_with("https://numi.app", scrape_depth="shallow")
        mock_val.assert_called_once()
        mock_draft.assert_called_once()
        inputs = mock_draft.call_args.args[0]
        assert inputs.url == "https://numi.app"
        assert inputs.url_scrape  # non-empty

        out = capsys.readouterr().out
        assert "Fetched https://numi.app (shallow scrape for product identity)." in out
        assert "→ Identified product: Numi" in out
        assert "→ Pre-filled ## Purpose, ## Sources" in out
        assert "Wrote SPEC.md." in out  # no "(template)" suffix
        assert "deep-crawl https://numi.app on the next run" in out

        written = (tmp_path / "SPEC.md").read_text()
        assert "Numi" in written

    def test_deep_flag_passes_deep_depth(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()) as mock_fetch,
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch("duplo.init.draft_spec", return_value="## Purpose\n\nX\n"),
        ):
            run_init(_make_args(url="https://numi.app", deep=True))

        mock_fetch.assert_called_once_with("https://numi.app", scrape_depth="deep")
        out = capsys.readouterr().out
        assert "deep scrape for product identity" in out
        # --deep means the scrape is already done; the deferred-deep note
        # should not be printed.
        assert "deep-crawl" not in out

    def test_url_canonicalized_before_fetch_and_write(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch(
                "duplo.init.fetch_site",
                return_value=_fetch_site_success(url="https://Numi.App/"),
            ) as mock_fetch,
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch(
                "duplo.init.draft_spec",
                return_value="## Purpose\n\nX\n## Sources\n\n- https://numi.app\n",
            ) as mock_draft,
        ):
            run_init(_make_args(url="https://Numi.App/"))

        # fetch_site gets the canonical form, not the raw trailing-slash input.
        mock_fetch.assert_called_once_with("https://numi.app", scrape_depth="shallow")
        inputs = mock_draft.call_args.args[0]
        assert inputs.url == "https://numi.app"

    def test_force_overwrites_existing_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("pre-existing\n")
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch(
                "duplo.init.draft_spec",
                return_value="## Purpose\n\nNumi.\n",
            ),
        ):
            run_init(_make_args(url="https://numi.app", force=True))

        written = (tmp_path / "SPEC.md").read_text()
        assert "pre-existing" not in written
        assert "Numi" in written

    def test_existing_spec_without_force_exits_1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("pre-existing\n")
        with (
            patch("duplo.init.fetch_site") as mock_fetch,
            patch("duplo.init.draft_spec") as mock_draft,
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_init(_make_args(url="https://numi.app"))

        assert exc_info.value.code == 1
        mock_fetch.assert_not_called()
        mock_draft.assert_not_called()
        assert _SPEC_EXISTS_ERROR in capsys.readouterr().err
        # Existing SPEC.md must not be clobbered.
        assert (tmp_path / "SPEC.md").read_text() == "pre-existing\n"


class TestRunInitUrlUnidentified:
    """Per INIT-design.md § 'URL fetch succeeds but identifies nothing'."""

    def test_unidentified_prefills_sources_only_and_leaves_purpose_fill_in(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=False,
                    product_name="",
                    products=[],
                    reason="generic landing page",
                    unclear_boundaries=True,
                ),
            ),
            patch("duplo.init.draft_spec") as mock_draft,
        ):
            run_init(_make_args(url="https://example.com"))

        # Per INIT-design.md: when URL fetch succeeds but no product is
        # identified, skip the drafter entirely — Purpose stays FILL IN.
        mock_draft.assert_not_called()
        out = capsys.readouterr().out
        assert "Fetched https://example.com." in out
        assert "Could not identify a specific product" in out
        assert "Pre-filled ## Sources only." in out
        assert "Wrote SPEC.md." in out

        written = (tmp_path / "SPEC.md").read_text()
        assert "https://example.com" in written
        assert "scrape: deep" in written
        # Purpose must remain a FILL IN marker (not pre-filled from the scrape).
        purpose_block = written.split("## Purpose", 1)[1].split("##", 1)[0]
        assert "FILL IN" in purpose_block


class TestRunInitUrlFetchFailure:
    """Per INIT-design.md § 'URL fetch fails'."""

    def test_fetch_failure_writes_template_with_scrape_none(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_FETCH_SITE_FAILURE),
            patch("duplo.init.validate_product_url") as mock_val,
            patch("duplo.init.draft_spec") as mock_draft,
        ):
            # Must not raise; exit 0 (normal return).
            run_init(_make_args(url="https://does-not-exist.invalid"))

        # Fetch failure → no validator call, no drafter call.
        mock_val.assert_not_called()
        mock_draft.assert_not_called()

        out = capsys.readouterr().out
        assert "Fetching https://does-not-exist.invalid ..." in out
        assert "Failed" in out
        assert "template-only setup" in out
        assert "Wrote SPEC.md (template)." in out

        written = (tmp_path / "SPEC.md").read_text()
        # URL is in Sources with scrape: none and product-reference role.
        assert "- https://does-not-exist.invalid" in written
        assert "role: product-reference" in written
        assert "scrape: none" in written
        # No proposed/discovered flag on the entry — user provided the URL.
        # (The template top-matter mentions "proposed: true" as example
        # text, so check the entry itself.)
        entry_start = written.index("- https://does-not-exist.invalid")
        next_heading = written.index("\n## ", entry_start)
        entry_block = written[entry_start:next_heading]
        assert "proposed:" not in entry_block
        assert "discovered:" not in entry_block
        # Required sections left as FILL IN markers.
        assert "<FILL IN: one or two sentences" in written
        assert "<FILL IN: language, framework, platform, constraints>" in written

    def test_fetch_failure_canonicalizes_url_before_writing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_FETCH_SITE_FAILURE),
        ):
            run_init(_make_args(url="https://Numi.App/"))

        written = (tmp_path / "SPEC.md").read_text()
        # Canonical form (lowercase host, trailing slash stripped).
        assert "- https://numi.app" in written
        assert "- https://Numi.App/" not in written


class TestRunInitUrlOutputOrdering:
    """Per INIT-design.md § 'Output discipline' and § 'duplo init <url>':
    lock the output layout for each URL-flow outcome so future edits do
    not drift from the design doc's example shapes."""

    def test_identified_flow_ordering_matches_init_design(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch("duplo.init.draft_spec", return_value="## Purpose\n\nNumi.\n"),
        ):
            run_init(_make_args(url="https://numi.app"))

        out = capsys.readouterr().out
        idx_fetched = out.index("Fetched https://numi.app (shallow scrape for product identity).")
        idx_identified = out.index("→ Identified product: Numi")
        idx_prefilled = out.index("→ Pre-filled ## Purpose, ## Sources")
        idx_ref = out.index("Created ref/ (empty).")
        idx_readme = out.index("Created ref/README.md.")
        idx_spec = out.index("Wrote SPEC.md.")
        idx_next = out.index(_URL_NEXT_STEPS_IDENTIFIED)
        idx_note = out.index("Note: duplo will deep-crawl https://numi.app")
        assert (
            idx_fetched
            < idx_identified
            < idx_prefilled
            < idx_ref
            < idx_readme
            < idx_spec
            < idx_next
            < idx_note
        )
        # Blank line separates the pre-filled sub-results from the
        # "Created ref/" block, matching INIT-design.md.
        assert "\n\n" in out[idx_prefilled:idx_ref]
        # Blank line separates "Wrote SPEC.md." from "Next steps:".
        assert "\n\n" in out[idx_spec:idx_next]
        # Blank line separates "Next steps:" block from the deferred-deep note.
        assert "\n\n" in out[idx_next:idx_note]

    def test_unidentified_flow_ordering_matches_init_design(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=False,
                    product_name="",
                    products=[],
                    reason="generic landing page",
                    unclear_boundaries=True,
                ),
            ),
        ):
            run_init(_make_args(url="https://example.com"))

        out = capsys.readouterr().out
        idx_fetched = out.index("Fetched https://example.com.")
        idx_reason = out.index("→ Could not identify a specific product")
        idx_sources = out.index("→ Pre-filled ## Sources only.")
        idx_ref = out.index("Created ref/ (empty).")
        idx_readme = out.index("Created ref/README.md.")
        idx_spec = out.index("Wrote SPEC.md.")
        idx_next = out.index(_URL_NEXT_STEPS_UNIDENTIFIED)
        assert idx_fetched < idx_reason < idx_sources < idx_ref < idx_readme < idx_spec < idx_next
        assert "\n\n" in out[idx_sources:idx_ref]
        assert "\n\n" in out[idx_spec:idx_next]

    def test_fetch_failure_flow_ordering_matches_init_design(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_FETCH_SITE_FAILURE),
        ):
            run_init(_make_args(url="https://does-not-exist.invalid"))

        out = capsys.readouterr().out
        idx_fetching = out.index("Fetching https://does-not-exist.invalid ...")
        idx_failed = out.index("→ Failed:")
        idx_prelude = out.index(_URL_FETCH_FAILED_PRELUDE)
        idx_ref = out.index("Created ref/ (empty).")
        idx_readme = out.index("Created ref/README.md.")
        idx_spec = out.index("Wrote SPEC.md (template).")
        idx_next = out.index(_URL_NEXT_STEPS_FETCH_FAILED)
        assert idx_fetching < idx_failed < idx_prelude < idx_ref < idx_readme < idx_spec < idx_next
        assert "\n\n" in out[idx_failed:idx_prelude]
        assert "\n\n" in out[idx_prelude:idx_ref]
        assert "\n\n" in out[idx_spec:idx_next]


class TestRunInitUrlRefScaffolding:
    """Per INIT-design.md § 'duplo init <url>': the URL flow must create
    ref/ and ref/README.md the same way as the no-arguments case, and
    write SPEC.md from draft_spec output (task 6.14.8)."""

    def test_identified_flow_writes_draft_spec_output_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        drafted = "## Purpose\n\nNumi — a calculator.\n\n## Sources\n\n- https://numi.app\n"
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch("duplo.init.draft_spec", return_value=drafted),
        ):
            run_init(_make_args(url="https://numi.app"))

        assert (tmp_path / "SPEC.md").read_text() == drafted

    def test_identified_flow_creates_ref_dir_and_readme(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch("duplo.init.draft_spec", return_value="## Purpose\n\nX\n"),
        ):
            run_init(_make_args(url="https://numi.app"))

        ref_dir = tmp_path / "ref"
        readme = ref_dir / "README.md"
        assert ref_dir.is_dir()
        assert readme.read_text() == _REF_README_CONTENT
        out = capsys.readouterr().out
        assert "Created ref/ (empty)." in out
        assert "Created ref/README.md." in out

    def test_unidentified_flow_creates_ref_dir_and_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=False,
                    product_name="",
                    products=[],
                    reason="generic landing page",
                    unclear_boundaries=True,
                ),
            ),
        ):
            run_init(_make_args(url="https://example.com"))

        assert (tmp_path / "ref" / "README.md").read_text() == _REF_README_CONTENT

    def test_fetch_failure_flow_creates_ref_dir_and_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("duplo.init.fetch_site", return_value=_FETCH_SITE_FAILURE),
        ):
            run_init(_make_args(url="https://does-not-exist.invalid"))

        assert (tmp_path / "ref" / "README.md").read_text() == _REF_README_CONTENT

    def test_url_flow_preserves_existing_ref_readme(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        existing = "user-authored README\n"
        (ref_dir / "README.md").write_text(existing)
        with (
            patch("duplo.init.fetch_site", return_value=_fetch_site_success()),
            patch(
                "duplo.init.validate_product_url",
                return_value=ValidationResult(
                    single_product=True,
                    product_name="Numi",
                    products=[],
                    reason="ok",
                ),
            ),
            patch("duplo.init.draft_spec", return_value="## Purpose\n\nX\n"),
        ):
            run_init(_make_args(url="https://numi.app"))

        assert (ref_dir / "README.md").read_text() == existing
        out = capsys.readouterr().out
        assert "Created ref/ (empty)." not in out
        assert "Created ref/README.md." not in out


def _stub_build_draft_spec(**fields):
    """Return a stub callable that yields a ProductSpec with *fields* set.

    Used to avoid real LLM calls from ``_build_draft_spec`` (which
    internally calls ``_draft_from_inputs`` -> ``query`` -> ``claude -p``)
    while still exercising the description flow's inspection logic.
    """
    from duplo.spec_reader import DesignBlock

    def _stub(inputs):
        spec = ProductSpec(
            purpose=fields.get("purpose", ""),
            architecture=fields.get("architecture", ""),
            design=DesignBlock(user_prose=fields.get("design", "")),
            behavior_contracts=fields.get("behavior_contracts", []),
            scope_include=fields.get("scope_include", []),
            scope_exclude=fields.get("scope_exclude", []),
        )
        if inputs.description:
            spec.notes = "Original description provided to `duplo init`:\n\n" + inputs.description
        return spec

    return _stub


class TestRunInitDescriptionFile:
    """Per INIT-design.md § 'duplo init --from-description description.txt'."""

    def test_reads_description_from_file_and_writes_spec(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        prose = "Build a SwiftUI calculator with inline results."
        desc_path.write_text(prose)

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(purpose="A SwiftUI calculator."),
        ) as mock_build:
            run_init(_make_args(from_description=str(desc_path)))

        mock_build.assert_called_once()
        inputs = mock_build.call_args.args[0]
        assert inputs.description == prose
        assert inputs.url is None

        written = (tmp_path / "SPEC.md").read_text()
        assert "A SwiftUI calculator." in written
        # Verbatim prose lands in ## Notes.
        notes_idx = written.index("Original description provided to `duplo init`:")
        assert prose in written[notes_idx:]

        out = capsys.readouterr().out
        assert f"Read {len(prose)} chars of description from {desc_path}." in out
        assert "Drafted SPEC.md from description." in out
        assert "Wrote SPEC.md." in out

    def test_prints_next_steps_block(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Build something.")

        with patch("duplo.init._build_draft_spec", side_effect=_stub_build_draft_spec()):
            run_init(_make_args(from_description=str(desc_path)))

        assert _DESCRIPTION_NEXT_STEPS in capsys.readouterr().out

    def test_missing_file_prints_error_and_exits_1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        missing = tmp_path / "missing.txt"

        with (
            patch("duplo.init._build_draft_spec") as mock_build,
            pytest.raises(SystemExit) as exc_info,
        ):
            run_init(_make_args(from_description=str(missing)))

        assert exc_info.value.code == 1
        mock_build.assert_not_called()
        assert _DESCRIPTION_FILE_NOT_FOUND.format(path=str(missing)) in capsys.readouterr().err
        assert not (tmp_path / "SPEC.md").exists()

    def test_existing_spec_without_force_exits_1(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Build something.")
        (tmp_path / "SPEC.md").write_text("pre-existing\n")

        with (
            patch("duplo.init._build_draft_spec") as mock_build,
            pytest.raises(SystemExit) as exc_info,
        ):
            run_init(_make_args(from_description=str(desc_path)))

        assert exc_info.value.code == 1
        mock_build.assert_not_called()
        assert _SPEC_EXISTS_ERROR in capsys.readouterr().err
        assert (tmp_path / "SPEC.md").read_text() == "pre-existing\n"

    def test_force_overwrites_existing_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Build something.")
        (tmp_path / "SPEC.md").write_text("pre-existing\n")

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(purpose="Something."),
        ):
            run_init(_make_args(from_description=str(desc_path), force=True))

        written = (tmp_path / "SPEC.md").read_text()
        assert "pre-existing" not in written
        assert "Something." in written

    def test_creates_ref_and_readme(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Build a thing.")

        with patch("duplo.init._build_draft_spec", side_effect=_stub_build_draft_spec()):
            run_init(_make_args(from_description=str(desc_path)))

        assert (tmp_path / "ref").is_dir()
        assert (tmp_path / "ref" / "README.md").read_text() == _REF_README_CONTENT
        out = capsys.readouterr().out
        assert "Created ref/ (empty)." in out
        assert "Created ref/README.md." in out


class TestRunInitDescriptionStdin:
    """Per INIT-design.md § 'duplo init --from-description -'."""

    def test_reads_description_from_stdin_pipe(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        prose = "Build a calculator."
        # Piped stdin: isatty() is False, no prompt emitted.
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(prose))

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(purpose="Calc."),
        ) as mock_build:
            run_init(_make_args(from_description="-"))

        inputs = mock_build.call_args.args[0]
        assert inputs.description == prose
        out = capsys.readouterr().out
        assert f"Read {len(prose)} chars of description from stdin." in out
        # No TTY prompt when stdin is not a terminal.
        assert _STDIN_TTY_PROMPT not in out

    def test_tty_stdin_prints_prompt(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        class _TtyStdin:
            def isatty(self):
                return True

            def read(self):
                return "Interactive prose."

        monkeypatch.setattr("sys.stdin", _TtyStdin())

        with patch("duplo.init._build_draft_spec", side_effect=_stub_build_draft_spec()):
            run_init(_make_args(from_description="-"))

        out = capsys.readouterr().out
        assert _STDIN_TTY_PROMPT in out
        assert "Read 18 chars of description from stdin." in out


class TestRunInitDescriptionUrlExtraction:
    """Per DRAFTER-design.md § 'Inferring URL roles': URLs in prose
    become Sources entries with proposed: true and the inferred role.

    The extraction happens inside ``draft_spec`` /
    ``_build_draft_spec`` (spec_writer).  These tests run the real
    drafter path with a mocked ``_draft_from_inputs`` so the LLM is
    bypassed but URL extraction still runs."""

    def test_like_url_in_prose_becomes_proposed_product_reference(
        self, tmp_path, capsys, monkeypatch
    ):
        from duplo.spec_reader import DesignBlock

        monkeypatch.chdir(tmp_path)
        prose = "Build a calculator like Numi at https://numi.app."
        desc_path = tmp_path / "description.txt"
        desc_path.write_text(prose)

        # Bypass the LLM call inside _build_draft_spec while leaving
        # the real URL extraction path intact.
        def fake_draft_from_inputs(inputs):
            return ProductSpec(
                purpose="A calculator.",
                architecture="",
                design=DesignBlock(user_prose=""),
            )

        monkeypatch.setattr(
            "duplo.spec_writer._draft_from_inputs",
            fake_draft_from_inputs,
        )

        run_init(_make_args(from_description=str(desc_path)))

        from duplo.spec_reader import _parse_spec

        written = (tmp_path / "SPEC.md").read_text()
        spec = _parse_spec(written)
        assert len(spec.sources) == 1
        entry = spec.sources[0]
        assert entry.url == "https://numi.app"
        assert entry.role == "product-reference"
        assert entry.proposed is True
        # User-provided no URL flag, so discovered is not set either.
        assert entry.discovered is False

    def test_unlike_url_in_prose_becomes_proposed_counter_example_scrape_none(
        self, tmp_path, monkeypatch
    ):
        from duplo.spec_reader import DesignBlock

        monkeypatch.chdir(tmp_path)
        prose = "Build a calculator, unlike https://bad-calc.example/."
        desc_path = tmp_path / "description.txt"
        desc_path.write_text(prose)

        monkeypatch.setattr(
            "duplo.spec_writer._draft_from_inputs",
            lambda inputs: ProductSpec(
                purpose="",
                architecture="",
                design=DesignBlock(user_prose=""),
            ),
        )

        run_init(_make_args(from_description=str(desc_path)))

        from duplo.spec_reader import _parse_spec

        written = (tmp_path / "SPEC.md").read_text()
        spec = _parse_spec(written)
        assert len(spec.sources) == 1
        entry = spec.sources[0]
        # Canonicalized (trailing slash stripped).
        assert entry.url == "https://bad-calc.example"
        assert entry.role == "counter-example"
        assert entry.scrape == "none"
        assert entry.proposed is True


class TestRunInitDescriptionBullets:
    """Per INIT-design.md § 'duplo init --from-description': the
    per-section bullets printed after 'Wrote SPEC.md.' must reflect
    what the drafter actually pre-filled."""

    def test_architecture_filled_when_prose_states_stack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("SwiftUI calculator.")

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(architecture="SwiftUI on macOS."),
        ):
            run_init(_make_args(from_description=str(desc_path)))

        out = capsys.readouterr().out
        assert "## Architecture filled from prose" in out
        assert "## Architecture left as <FILL IN>" not in out

    def test_architecture_fill_in_when_prose_silent_on_stack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Build a calculator.")

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(purpose="A calculator."),
        ):
            run_init(_make_args(from_description=str(desc_path)))

        out = capsys.readouterr().out
        assert "## Architecture left as <FILL IN>" in out
        assert "Pre-filled ## Purpose from prose." in out

    def test_notes_bullet_always_present(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Anything.")

        with patch("duplo.init._build_draft_spec", side_effect=_stub_build_draft_spec()):
            run_init(_make_args(from_description=str(desc_path)))

        assert "## Notes contains the verbatim original description." in capsys.readouterr().out

    def test_behavior_bullet_reports_empty_when_no_contracts(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Calc.")

        with patch("duplo.init._build_draft_spec", side_effect=_stub_build_draft_spec()):
            run_init(_make_args(from_description=str(desc_path)))

        out = capsys.readouterr().out
        assert "## Behavior left empty (no input/output pairs detected)." in out

    def test_design_bullet_when_design_filled(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Monospaced dark theme.")

        with patch(
            "duplo.init._build_draft_spec",
            side_effect=_stub_build_draft_spec(
                purpose="A calculator.",
                design="Monospaced, dark theme.",
            ),
        ):
            run_init(_make_args(from_description=str(desc_path)))

        out = capsys.readouterr().out
        assert "Pre-filled ## Purpose, ## Design from prose." in out
