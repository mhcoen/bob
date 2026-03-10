"""Tests for duplo.collector."""

from __future__ import annotations

from pathlib import Path

import pytest

from duplo.collector import collect_feedback, collect_issues, _read_interactive


def make_input(*answers: str):
    """Return an input_fn that yields *answers* in order."""
    it = iter(answers)
    return lambda _prompt: next(it)


def make_input_with_eof(*answers):
    """Return an input_fn that yields *answers*, raising EOFError for None."""
    it = iter(answers)

    def _input_fn(_prompt):
        val = next(it)
        if val is None:
            raise EOFError
        return val

    return _input_fn


class TestCollectFeedbackFromFile:
    def test_reads_file_contents(self, tmp_path: Path):
        f = tmp_path / "feedback.txt"
        f.write_text("Great app!\nNeeds dark mode.", encoding="utf-8")
        result = collect_feedback(feedback_file=f)
        assert result == "Great app!\nNeeds dark mode."

    def test_strips_surrounding_whitespace(self, tmp_path: Path):
        f = tmp_path / "feedback.txt"
        f.write_text("\n  some feedback  \n\n", encoding="utf-8")
        assert collect_feedback(feedback_file=f) == "some feedback"

    def test_accepts_str_path(self, tmp_path: Path):
        f = tmp_path / "feedback.txt"
        f.write_text("feedback text", encoding="utf-8")
        result = collect_feedback(feedback_file=str(f))
        assert result == "feedback text"

    def test_raises_if_file_not_found(self, tmp_path: Path):
        missing = tmp_path / "no_such_file.txt"
        with pytest.raises(FileNotFoundError):
            collect_feedback(feedback_file=missing)

    def test_raises_if_file_empty(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.write_text("   \n  ", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            collect_feedback(feedback_file=f)


class TestCollectFeedbackInteractive:
    def test_returns_typed_lines(self):
        result = collect_feedback(input_fn=make_input("line one", "line two", ""))
        assert result == "line one\nline two"

    def test_eof_terminates_input(self):
        result = collect_feedback(input_fn=make_input_with_eof("only line", None))
        assert result == "only line"

    def test_raises_if_no_input(self):
        with pytest.raises(ValueError, match="No feedback"):
            collect_feedback(input_fn=make_input(""))

    def test_raises_on_immediate_eof(self):
        with pytest.raises(ValueError, match="No feedback"):
            collect_feedback(input_fn=make_input_with_eof(None))

    def test_prints_prompt(self):
        lines: list[str] = []
        collect_feedback(
            input_fn=make_input("hello", ""),
            print_fn=lines.append,
        )
        combined = "\n".join(lines)
        assert "feedback" in combined.lower()


class TestCollectIssues:
    def test_single_line_issues(self):
        # Each issue is one line, blank line between separates them.
        result = collect_issues(
            input_fn=make_input("button broken", "", "text misaligned", "", "")
        )
        assert result == ["button broken", "text misaligned"]

    def test_multi_line_issue(self):
        result = collect_issues(
            input_fn=make_input("crash on save", "stack trace attached", "", "")
        )
        assert result == ["crash on save\nstack trace attached"]

    def test_multiple_multi_line_issues(self):
        result = collect_issues(
            input_fn=make_input(
                "issue one line 1",
                "issue one line 2",
                "",
                "issue two line 1",
                "",
                "",
            )
        )
        assert result == [
            "issue one line 1\nissue one line 2",
            "issue two line 1",
        ]

    def test_empty_input_returns_empty_list(self):
        result = collect_issues(input_fn=make_input(""))
        assert result == []

    def test_eof_mid_issue_saves_it(self):
        result = collect_issues(input_fn=make_input_with_eof("crash on save", None))
        assert result == ["crash on save"]

    def test_eof_after_blank_line(self):
        result = collect_issues(input_fn=make_input_with_eof("an issue", "", None))
        assert result == ["an issue"]

    def test_immediate_eof_returns_empty(self):
        result = collect_issues(input_fn=make_input_with_eof(None))
        assert result == []

    def test_strips_whitespace_from_issue_text(self):
        result = collect_issues(input_fn=make_input("  padded issue  ", "", ""))
        assert result == ["padded issue"]

    def test_blank_only_lines_within_issue_are_stripped(self):
        result = collect_issues(input_fn=make_input("   ", "", ""))
        assert result == []

    def test_prints_prompt(self):
        lines: list[str] = []
        collect_issues(input_fn=make_input(""), print_fn=lines.append)
        combined = "\n".join(lines)
        assert "issue" in combined.lower()


class TestReadInteractive:
    def test_multi_line_input(self):
        result = _read_interactive(input_fn=make_input("a", "b", "c", ""))
        assert result == "a\nb\nc"

    def test_strips_result(self):
        result = _read_interactive(input_fn=make_input("  spaced  ", ""))
        assert result == "spaced"
