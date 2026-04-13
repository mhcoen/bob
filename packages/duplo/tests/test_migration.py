"""Tests for duplo.migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from duplo.migration import _MIGRATION_MESSAGE, _check_migration, needs_migration


class TestMigrationMessage:
    """Tests for _MIGRATION_MESSAGE constant."""

    def test_mentions_spec_template(self) -> None:
        """Message tells the user to author SPEC.md from the template."""
        assert "SPEC-template.md" in _MIGRATION_MESSAGE

    def test_does_not_mention_duplo_init(self) -> None:
        """Phase 2 message must NOT reference `duplo init` (ships in Phase 4)."""
        assert "duplo init" not in _MIGRATION_MESSAGE

    def test_mentions_ref_directory(self) -> None:
        """Message instructs the user to create ref/ and move files."""
        assert "mkdir ref" in _MIGRATION_MESSAGE

    def test_mentions_run_duplo_again(self) -> None:
        """Message ends with instruction to re-run duplo."""
        assert "Run `duplo` again" in _MIGRATION_MESSAGE

    def test_mentions_sources_section(self) -> None:
        """Message tells the user to fill in ## Sources."""
        assert "## Sources" in _MIGRATION_MESSAGE

    def test_reassures_no_changes(self) -> None:
        """Message reassures the user nothing was moved or modified."""
        assert "Nothing has been moved or modified" in _MIGRATION_MESSAGE

    def test_lists_four_numbered_steps(self) -> None:
        """Phase 2 message has exactly four numbered steps."""
        import re

        steps = re.findall(r"^\s+\d+\.", _MIGRATION_MESSAGE, re.MULTILINE)
        assert len(steps) == 4

    def test_minimum_field_purpose(self) -> None:
        """Message lists Purpose as a minimum SPEC.md field."""
        assert "## Purpose" in _MIGRATION_MESSAGE

    def test_minimum_field_architecture(self) -> None:
        """Message lists Architecture as a minimum SPEC.md field."""
        assert "## Architecture" in _MIGRATION_MESSAGE

    def test_minimum_field_references(self) -> None:
        """Message lists References as a minimum SPEC.md field."""
        assert "## References" in _MIGRATION_MESSAGE

    def test_mentions_plan_md_unchanged(self) -> None:
        """Message reassures PLAN.md is unchanged."""
        assert "PLAN.md" in _MIGRATION_MESSAGE

    def test_mentions_duplo_json_unchanged(self) -> None:
        """Message reassures .duplo/duplo.json is unchanged."""
        assert ".duplo/duplo.json" in _MIGRATION_MESSAGE

    def test_mentions_source_code_unchanged(self) -> None:
        """Message reassures source code is unchanged."""
        assert "source code" in _MIGRATION_MESSAGE

    def test_move_reference_files_step(self) -> None:
        """Message instructs moving reference files into ref/."""
        assert "Move reference files into ref/" in _MIGRATION_MESSAGE

    def test_snapshot_matches_fixture(self) -> None:
        """Pin exact message content against fixture file."""
        fixture = Path(__file__).parent / "fixtures" / "migration_message.txt"
        expected = fixture.read_text()
        assert _MIGRATION_MESSAGE == expected


def test_no_duplo_json(tmp_path: Path) -> None:
    """No .duplo/duplo.json → not a duplo project, no migration."""
    assert needs_migration(tmp_path) is False


def test_duplo_json_no_spec(tmp_path: Path) -> None:
    """duplo.json exists but no SPEC.md → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    assert needs_migration(tmp_path) is True


def test_spec_with_marker_string(tmp_path: Path) -> None:
    """New-format SPEC.md with marker string → no migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# My App\nHow the pieces fit together:\n- stuff\n")
    assert needs_migration(tmp_path) is False


def test_spec_with_sources_heading(tmp_path: Path) -> None:
    """New-format SPEC.md with ## Sources heading → no migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# My App\n## Sources\n")
    assert needs_migration(tmp_path) is False


def test_spec_with_sources_heading_trailing_spaces(tmp_path: Path) -> None:
    """## Sources with trailing whitespace still matches."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# My App\n## Sources   \n")
    assert needs_migration(tmp_path) is False


def test_old_format_spec(tmp_path: Path) -> None:
    """SPEC.md without marker or ## Sources → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Old spec\nSome content\n")
    assert needs_migration(tmp_path) is True


def test_sources_in_body_text_not_heading(tmp_path: Path) -> None:
    """'Sources' in body text (not as ## heading) → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Old spec\nSee sources below.\n")
    assert needs_migration(tmp_path) is True


def test_sources_wrong_heading_level(tmp_path: Path) -> None:
    """### Sources (wrong level) → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Old spec\n### Sources\n")
    assert needs_migration(tmp_path) is True


def test_both_signals_present(tmp_path: Path) -> None:
    """Both marker and ## Sources → no migration (either suffices)."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("How the pieces fit together:\n## Sources\n")
    assert needs_migration(tmp_path) is False


def test_hand_authored_spec_with_sources(tmp_path: Path) -> None:
    """Minimal hand-authored SPEC.md with ## Sources but no template comment.

    Phase 2 instructs users to write SPEC.md by hand.  A user who writes
    a valid spec without copying the template top-matter comment should
    not be stuck in migration.  The ## Sources heading is sufficient.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    spec = "# MyApp\n\n## Purpose\nA calculator app.\n\n## Sources\n- https://example.com\n"
    (tmp_path / "SPEC.md").write_text(spec)
    assert needs_migration(tmp_path) is False


def test_empty_spec(tmp_path: Path) -> None:
    """Empty SPEC.md has neither signal → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("")
    assert needs_migration(tmp_path) is True


def test_old_format_spec_with_content(tmp_path: Path) -> None:
    """Old-format SPEC.md with real content but neither signal → needs migration.

    An old-layout project may have a SPEC.md with substantial content
    (sections, descriptions) that predates the new-format signals.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    spec = (
        "# MyApp\n\n"
        "## Overview\nA calculator app for quick math.\n\n"
        "## Features\n- Basic arithmetic\n- History\n\n"
        "## Notes\nSee sources for reference material.\n"
    )
    (tmp_path / "SPEC.md").write_text(spec)
    assert needs_migration(tmp_path) is True


def test_sources_h1_heading(tmp_path: Path) -> None:
    """# Sources (H1) is not ## Sources → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Sources\nstuff\n")
    assert needs_migration(tmp_path) is True


def test_sources_mid_document(tmp_path: Path) -> None:
    """## Sources appearing mid-document (not at top) still matches."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    spec = (
        "# MyApp\n\n"
        "## Purpose\nA calculator.\n\n"
        "## Design\nMinimal UI.\n\n"
        "## Sources\n- https://example.com\n"
    )
    (tmp_path / "SPEC.md").write_text(spec)
    assert needs_migration(tmp_path) is False


def test_my_sources_not_heading(tmp_path: Path) -> None:
    """'My sources' on a line does not match ## Sources heading."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Old spec\nMy sources\n")
    assert needs_migration(tmp_path) is True


def test_sources_inline_text(tmp_path: Path) -> None:
    """'## Sources and references' is not exactly '## Sources'."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_text("# Old spec\n## Sources and references\n")
    assert needs_migration(tmp_path) is True


def test_zero_byte_spec(tmp_path: Path) -> None:
    """Zero-byte SPEC.md → same as absent, needs migration.

    A zero-byte file has neither signal (marker string nor ## Sources),
    so it is classified the same as SPEC.md being absent entirely.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    (tmp_path / "SPEC.md").write_bytes(b"")
    assert needs_migration(tmp_path) is True


def test_marker_inside_html_comment(tmp_path: Path) -> None:
    """Marker string inside an HTML comment still matches → no migration.

    The template stores the marker in a comment
    (``<!-- How the pieces fit together: ... -->``).  The substring
    check intentionally hits — no special comment-stripping needed.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    spec = "<!-- How the pieces fit together: overview of architecture -->\n"
    (tmp_path / "SPEC.md").write_text(spec)
    assert needs_migration(tmp_path) is False


def test_corrupted_duplo_json(tmp_path: Path) -> None:
    """Corrupted .duplo/duplo.json → still needs migration.

    The presence of the file triggers migration detection, not its
    contents.  needs_migration must NOT try to parse the JSON.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{corrupt not json!!")
    assert needs_migration(tmp_path) is True


def test_corrupted_duplo_json_with_new_spec(tmp_path: Path) -> None:
    """Corrupted duplo.json + valid new-format SPEC.md → no migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("<<<garbage>>>")
    (tmp_path / "SPEC.md").write_text("How the pieces fit together:\n## Sources\n")
    assert needs_migration(tmp_path) is False


def test_bom_prefixed_spec_with_marker(tmp_path: Path) -> None:
    """UTF-8 BOM-prefixed SPEC.md with marker string → no migration.

    Some editors (notably Windows Notepad) write a UTF-8 BOM
    (``\\xef\\xbb\\xbf``) at the start of the file.  The read must
    strip it so the marker substring match still succeeds.
    """
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    bom = b"\xef\xbb\xbf"
    content = bom + "How the pieces fit together:\n## Purpose\nStuff\n".encode()
    (tmp_path / "SPEC.md").write_bytes(content)
    assert needs_migration(tmp_path) is False


def test_bom_prefixed_spec_with_sources(tmp_path: Path) -> None:
    """UTF-8 BOM-prefixed SPEC.md with ## Sources heading → no migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    bom = b"\xef\xbb\xbf"
    content = bom + "# MyApp\n## Sources\n- https://example.com\n".encode()
    (tmp_path / "SPEC.md").write_bytes(content)
    assert needs_migration(tmp_path) is False


def test_bom_prefixed_old_format_spec(tmp_path: Path) -> None:
    """UTF-8 BOM-prefixed old-format SPEC.md (no signals) → needs migration."""
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "duplo.json").write_text("{}")
    bom = b"\xef\xbb\xbf"
    content = bom + "# Old spec\nSome content\n".encode()
    (tmp_path / "SPEC.md").write_bytes(content)
    assert needs_migration(tmp_path) is True


class TestCheckMigration:
    """Tests for _check_migration()."""

    def test_exits_on_old_layout(self, tmp_path: Path, capsys) -> None:
        """Old-format project → prints message and exits with code 1."""
        (tmp_path / ".duplo").mkdir()
        (tmp_path / ".duplo" / "duplo.json").write_text("{}")
        with pytest.raises(SystemExit) as exc_info:
            _check_migration(tmp_path)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SPEC-template.md" in captured.out
        assert "mkdir ref" in captured.out

    def test_no_exit_on_new_format(self, tmp_path: Path, capsys) -> None:
        """New-format project → no output, no exit, returns None."""
        (tmp_path / ".duplo").mkdir()
        (tmp_path / ".duplo" / "duplo.json").write_text("{}")
        (tmp_path / "SPEC.md").write_text("How the pieces fit together:\n")
        result = _check_migration(tmp_path)
        assert result is None
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_no_exit_on_non_duplo_dir(self, tmp_path: Path, capsys) -> None:
        """Non-duplo directory (no .duplo/duplo.json) → no output, no exit."""
        result = _check_migration(tmp_path)
        assert result is None
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_exits_on_old_format_spec(self, tmp_path: Path, capsys) -> None:
        """Old-format SPEC.md (no signals) → prints message and exits."""
        (tmp_path / ".duplo").mkdir()
        (tmp_path / ".duplo" / "duplo.json").write_text("{}")
        (tmp_path / "SPEC.md").write_text("# Old spec\nSome content\n")
        with pytest.raises(SystemExit) as exc_info:
            _check_migration(tmp_path)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SPEC-template.md" in captured.out

    def test_prints_full_message(self, tmp_path: Path, capsys) -> None:
        """Printed output matches _MIGRATION_MESSAGE exactly."""
        (tmp_path / ".duplo").mkdir()
        (tmp_path / ".duplo" / "duplo.json").write_text("{}")
        with pytest.raises(SystemExit):
            _check_migration(tmp_path)
        captured = capsys.readouterr()
        assert captured.out.rstrip("\n") == _MIGRATION_MESSAGE
