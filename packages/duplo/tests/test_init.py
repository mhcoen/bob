"""Tests for duplo.init."""

from __future__ import annotations

import argparse

import pytest

from duplo.init import _REF_README_CONTENT, _SPEC_EXISTS_ERROR, run_init
from duplo.spec_reader import ProductSpec
from duplo.spec_writer import format_spec


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
