"""Tests for duplo.init."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from duplo.doc_tables import DocStructures
from duplo.fetcher import PageRecord
from duplo.init import (
    _NO_ARGS_NEXT_STEPS,
    _REF_README_CONTENT,
    _SPEC_EXISTS_ERROR,
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

    def test_identified_flow_ordering_matches_init_design(
        self, tmp_path, capsys, monkeypatch
    ):
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
        idx_fetched = out.index(
            "Fetched https://numi.app (shallow scrape for product identity)."
        )
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

    def test_unidentified_flow_ordering_matches_init_design(
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
        assert (
            idx_fetched
            < idx_reason
            < idx_sources
            < idx_ref
            < idx_readme
            < idx_spec
            < idx_next
        )
        assert "\n\n" in out[idx_sources:idx_ref]
        assert "\n\n" in out[idx_spec:idx_next]

    def test_fetch_failure_flow_ordering_matches_init_design(
        self, tmp_path, capsys, monkeypatch
    ):
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
        assert (
            idx_fetching
            < idx_failed
            < idx_prelude
            < idx_ref
            < idx_readme
            < idx_spec
            < idx_next
        )
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
