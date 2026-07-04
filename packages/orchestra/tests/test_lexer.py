"""Unit tests for the workflow-file lexer.

These exercise ``Lexer._iter`` directly, in particular the
end-of-file DEDENT flush. The lexer once carried a ``_pending`` token
buffer that was never populated (a check that was always false); its
removal (T-000007) must not change tokenization, and the EOF flush of
open indentation levels must still emit the DEDENT/EOF sequence.
"""

from __future__ import annotations

from orchestra.loader.lexer import Lexer


def _kinds(source: str) -> list[str]:
    return [t.kind for t in Lexer(source).tokens()]


def test_flat_line_tokenizes_without_indent() -> None:
    kinds = _kinds("workflow demo\n")
    assert kinds == ["IDENT", "IDENT", "NEWLINE", "EOF"]


def test_nested_block_flushes_dedents_at_eof() -> None:
    """An indented block that runs to end-of-file must still close its
    open levels: the tail of ``_iter`` pops the indent stack and emits
    one DEDENT per open level before EOF."""
    source = "outer\n  inner\n    deep\n"
    kinds = _kinds(source)
    assert kinds == [
        "IDENT",  # outer
        "NEWLINE",
        "INDENT",
        "IDENT",  # inner
        "NEWLINE",
        "INDENT",
        "IDENT",  # deep
        "NEWLINE",
        "DEDENT",  # flushed at EOF
        "DEDENT",  # flushed at EOF
        "EOF",
    ]


def test_blank_and_comment_lines_do_not_emit_tokens() -> None:
    source = "a\n\n  # comment\nb\n"
    kinds = _kinds(source)
    assert kinds == ["IDENT", "NEWLINE", "IDENT", "NEWLINE", "EOF"]
