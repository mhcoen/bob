"""Tests for the idea subcommand."""

from __future__ import annotations

import re

from mcloop.idea_cmd import _HEADER, _cmd_idea


def test_creates_ideas_file_when_missing(tmp_path):
    """_cmd_idea creates IDEAS.md with header + entry when the file doesn't exist."""
    _cmd_idea(tmp_path, "first idea")
    ideas = (tmp_path / "IDEAS.md").read_text()
    assert ideas.startswith("# Ideas")
    assert "first idea" in ideas


def test_appends_to_existing_file(tmp_path):
    """_cmd_idea appends to an existing IDEAS.md without overwriting."""
    ideas_path = tmp_path / "IDEAS.md"
    ideas_path.write_text(_HEADER)
    _cmd_idea(tmp_path, "alpha")
    _cmd_idea(tmp_path, "beta")
    content = ideas_path.read_text()
    lines = content.strip().splitlines()
    idea_lines = [line for line in lines if line.startswith("- [")]
    assert len(idea_lines) == 2
    assert "alpha" in idea_lines[0]
    assert "beta" in idea_lines[1]


def test_entry_has_date_stamp(tmp_path):
    """Each entry has a YYYY-MM-DD timestamp."""
    _cmd_idea(tmp_path, "timestamped idea")
    content = (tmp_path / "IDEAS.md").read_text()
    assert re.search(r"- \[\d{4}-\d{2}-\d{2}\] timestamped idea", content)


def test_preserves_custom_content(tmp_path):
    """If the user has edited IDEAS.md manually, _cmd_idea preserves that content."""
    ideas_path = tmp_path / "IDEAS.md"
    custom = "# My Ideas\n\nSome custom notes.\n"
    ideas_path.write_text(custom)
    _cmd_idea(tmp_path, "appended idea")
    content = ideas_path.read_text()
    assert content.startswith("# My Ideas")
    assert "Some custom notes." in content
    assert "appended idea" in content
