"""Tests for mcloop.output."""

from mcloop.output import (
    _dry_run,
    _print_error_tail,
    _print_notes_update,
    _print_summary,
    _snapshot_notes,
    _tail,
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
