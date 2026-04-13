"""Tests for duplo.migration."""

from __future__ import annotations

from pathlib import Path

from duplo.migration import needs_migration


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
