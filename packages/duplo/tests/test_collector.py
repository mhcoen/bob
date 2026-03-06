"""Tests for duplo.collector."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.collector import collect_feedback, _read_interactive


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
        inputs = iter(["line one", "line two", ""])
        with patch("builtins.input", side_effect=inputs):
            result = collect_feedback()
        assert result == "line one\nline two"

    def test_eof_terminates_input(self):
        inputs = iter(["only line", EOFError()])
        with patch("builtins.input", side_effect=inputs):
            result = collect_feedback()
        assert result == "only line"

    def test_raises_if_no_input(self):
        with patch("builtins.input", side_effect=[""]):
            with pytest.raises(ValueError, match="No feedback"):
                collect_feedback()

    def test_raises_on_immediate_eof(self):
        with patch("builtins.input", side_effect=EOFError()):
            with pytest.raises(ValueError, match="No feedback"):
                collect_feedback()

    def test_prints_prompt(self, capsys):
        with patch("builtins.input", side_effect=["hello", ""]):
            collect_feedback()
        out = capsys.readouterr().out
        assert "feedback" in out.lower()


class TestReadInteractive:
    def test_multi_line_input(self):
        with patch("builtins.input", side_effect=["a", "b", "c", ""]):
            result = _read_interactive()
        assert result == "a\nb\nc"

    def test_strips_result(self):
        with patch("builtins.input", side_effect=["  spaced  ", ""]):
            result = _read_interactive()
        assert result == "spaced"
