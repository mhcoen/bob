"""Tests for mcloop.output."""

import json

from mcloop.output import (
    _dry_run,
    _print_error_tail,
    _print_notes_update,
    _print_summary,
    _snapshot_notes,
    _tail,
    _whitelist_suggestions,
)


def test_tail_returns_last_n_lines():
    text = "\n".join(f"line {i}" for i in range(100))
    result = _tail(text, max_lines=5)
    assert result == "line 95\nline 96\nline 97\nline 98\nline 99"


def test_tail_returns_all_when_short():
    text = "line 1\nline 2\nline 3"
    result = _tail(text, max_lines=10)
    assert result == text


def test_tail_strips_whitespace():
    text = "  \nline 1\nline 2\n  "
    result = _tail(text, max_lines=50)
    assert result == "line 1\nline 2"


def test_print_error_tail_shows_last_lines(capsys):
    output = "\n".join(f"line {i}" for i in range(50))
    _print_error_tail(output, max_lines=5)
    captured = capsys.readouterr().out
    assert "--- last output ---" in captured
    assert "line 49" in captured
    assert "line 44" not in captured
    assert "---" in captured


def test_print_error_tail_empty(capsys):
    _print_error_tail("")
    captured = capsys.readouterr().out
    assert captured == ""


def test_snapshot_notes_missing_file(tmp_path):
    result = _snapshot_notes(tmp_path)
    assert result == ("", 0)


def test_snapshot_notes_existing_file(tmp_path):
    notes = tmp_path / "NOTES.md"
    notes.write_text("## Observations\nSome note\n")
    h, count = _snapshot_notes(tmp_path)
    assert h != ""
    assert count == 2


def test_print_notes_update_new_file(tmp_path, capsys):
    notes = tmp_path / "NOTES.md"
    notes.write_text("## Observations\nNew note\n")
    _print_notes_update(tmp_path, ("", 0))
    captured = capsys.readouterr().out
    assert "created" in captured
    assert "2 lines" in captured


def test_print_notes_update_no_change(tmp_path, capsys):
    notes = tmp_path / "NOTES.md"
    notes.write_text("## Observations\nNote\n")
    snapshot = _snapshot_notes(tmp_path)
    _print_notes_update(tmp_path, snapshot)
    captured = capsys.readouterr().out
    assert captured == ""


def test_print_notes_update_changed(tmp_path, capsys):
    notes = tmp_path / "NOTES.md"
    notes.write_text("## Observations\nNote\n")
    snapshot = _snapshot_notes(tmp_path)
    notes.write_text("## Observations\nNote\nNew line\nAnother\n")
    _print_notes_update(tmp_path, snapshot)
    captured = capsys.readouterr().out
    assert "updated" in captured
    assert "2 new lines" in captured


def test_print_notes_update_missing_file(tmp_path, capsys):
    _print_notes_update(tmp_path, ("abc", 5))
    captured = capsys.readouterr().out
    assert captured == ""


def test_dry_run_flat(tmp_path, capsys):
    from mcloop.checklist import parse

    md = "- [ ] Task one\n- [x] Task two\n- [ ] Task three\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    _dry_run(tasks)
    captured = capsys.readouterr().out
    assert "[ ] Task one" in captured
    assert "[x] Task two" in captured
    assert "Next task" in captured
    assert "Task one" in captured


def test_dry_run_all_complete(tmp_path, capsys):
    from mcloop.checklist import parse

    md = "- [x] Task one\n- [x] Task two\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    _dry_run(tasks)
    captured = capsys.readouterr().out
    assert "No unchecked tasks remaining" in captured


def test_print_summary_basic(capsys, monkeypatch):
    monkeypatch.setattr("mcloop.output._whitelist_suggestions", lambda: [])
    _print_summary(
        completed=["Task A", "Task B"],
        failed_task=None,
        failed_reason="",
        remaining_tasks=[],
        total_seconds=125,
    )
    captured = capsys.readouterr().out
    assert "Completed: 2 task(s)" in captured
    assert "Task A" in captured
    assert "2m 5s" in captured


def test_print_summary_with_failure(capsys, monkeypatch):
    monkeypatch.setattr("mcloop.output._whitelist_suggestions", lambda: [])
    _print_summary(
        completed=["Task A"],
        failed_task="Task B",
        failed_reason="Something went wrong",
        remaining_tasks=[],
    )
    captured = capsys.readouterr().out
    assert "Failed: Task B" in captured
    assert "Something went wrong" in captured


def test_print_summary_stop_reason(capsys, monkeypatch):
    monkeypatch.setattr("mcloop.output._whitelist_suggestions", lambda: [])
    _print_summary(
        completed=["Task A"],
        failed_task=None,
        failed_reason="",
        remaining_tasks=[],
        stop_reason="Stopped after one task as requested",
    )
    captured = capsys.readouterr().out
    assert "Stopped after one task as requested" in captured


def test_print_summary_stop_reason_overrides_stage(capsys, monkeypatch):
    """stop_reason takes precedence over completed_stage."""
    monkeypatch.setattr("mcloop.output._whitelist_suggestions", lambda: [])
    _print_summary(
        completed=["Task A"],
        failed_task=None,
        failed_reason="",
        remaining_tasks=[],
        completed_stage="Core",
        stop_reason="Stopped after stage as requested (Core complete).",
    )
    captured = capsys.readouterr().out
    assert "Stopped after stage as requested (Core complete)." in captured
    # The generic message should NOT appear
    assert "Core complete. Run mcloop again for the next stage." not in captured


class TestWhitelistSuggestions:
    def test_no_session_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("mcloop.output.SESSION_FILE", tmp_path / "missing.json")
        assert _whitelist_suggestions() == []

    def test_empty_patterns(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"patterns": []}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        assert _whitelist_suggestions() == []

    def test_suggests_new_patterns(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"patterns": ["Bash:ruff check ."]}))
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": []}}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        monkeypatch.setattr("mcloop.output.SETTINGS_FILE", settings)
        result = _whitelist_suggestions()
        assert result == ["Bash(ruff:*)"]

    def test_skips_already_allowed(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"patterns": ["Bash:ruff check ."]}))
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ruff:*)"]}}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        monkeypatch.setattr("mcloop.output.SETTINGS_FILE", settings)
        assert _whitelist_suggestions() == []

    def test_skips_dangerous_commands(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"patterns": ["Bash:rm -rf /", "Bash:kill 1234"]}))
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        monkeypatch.setattr("mcloop.output.SETTINGS_FILE", settings)
        assert _whitelist_suggestions() == []

    def test_deduplicates(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(
            json.dumps(
                {
                    "patterns": [
                        "Bash:pytest tests/",
                        "Bash:pytest tests/test_foo.py",
                    ]
                }
            )
        )
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        monkeypatch.setattr("mcloop.output.SETTINGS_FILE", settings)
        result = _whitelist_suggestions()
        # Both map to Bash(pytest:*), should only appear once
        assert result == ["Bash(pytest:*)"]

    def test_pattern_without_colon(self, monkeypatch, tmp_path):
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"patterns": ["Read"]}))
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))
        monkeypatch.setattr("mcloop.output.SESSION_FILE", session)
        monkeypatch.setattr("mcloop.output.SETTINGS_FILE", settings)
        result = _whitelist_suggestions()
        assert result == ["Read"]
