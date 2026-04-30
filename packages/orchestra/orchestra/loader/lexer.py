"""Lexer for Orchestra workflow files.

Produces a stream of tokens with synthetic INDENT/DEDENT tokens, per
the rules in ``design/orchestra-grammar.md`` section "Whitespace and
indentation".

Slice 1 implements the grammar subset used by ``echo.orc``:

  - Identifiers, keywords, integers, strings (short only; no triple
    quotes in slice 1's fixture), arrows ('=>'), commas, dots.
  - Indentation-sensitive blocks.
  - Line comments starting with '#'.

Long strings (triple-quoted) are recognized but not exercised by
slice 1's fixture. They are included so slice 2 does not require a
lexer rewrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from orchestra.errors import ParseError

TokenKind = Literal[
    "IDENT",
    "INT",
    "STRING",
    "ARROW",     # =>
    "COMMA",
    "DOT",
    "LT",        # <
    "LE",        # <=
    "GT",        # >
    "GE",        # >=
    "EQ",        # ==
    "NEQ",       # !=
    "BANG",      # !
    "LPAREN",
    "RPAREN",
    "LBRACKET",  # [
    "RBRACKET",  # ]
    "NEWLINE",
    "INDENT",
    "DEDENT",
    "EOF",
]


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    line: int
    col: int


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha()


def _is_ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch in "_-"


class Lexer:
    """Indentation-sensitive lexer."""

    def __init__(self, source: str) -> None:
        self._src = source
        self._pos = 0
        self._line = 1
        self._col = 1
        # Indentation stack measured in raw whitespace characters.
        # The stack holds the leading-whitespace string of the line
        # that opened each currently-open block.
        self._indent_stack: list[str] = [""]
        # Pending tokens (used to flush DEDENTs at end-of-file).
        self._pending: list[Token] = []
        # Tab/space mixing detection: the lexer locks in to whichever
        # is used first and rejects mixing.
        self._indent_unit: str | None = None

    # ----- public API ---------------------------------------------

    def tokens(self) -> list[Token]:
        out: list[Token] = []
        for tok in self._iter():
            out.append(tok)
            if tok.kind == "EOF":
                break
        return out

    # ----- internals ----------------------------------------------

    def _iter(self) -> Iterator[Token]:
        # Process line by line so indentation is straightforward.
        while True:
            if self._pending:
                yield self._pending.pop(0)
                continue
            if self._pos >= len(self._src):
                # Flush remaining DEDENTs.
                while len(self._indent_stack) > 1:
                    self._indent_stack.pop()
                    yield Token("DEDENT", "", self._line, self._col)
                yield Token("EOF", "", self._line, self._col)
                return
            # We are at the start of a line. Read leading whitespace.
            ws = self._read_leading_ws()
            # Blank or comment-only line: skip without changing indent.
            if self._pos >= len(self._src) or self._src[self._pos] == "\n":
                if self._pos < len(self._src):
                    self._consume_newline()
                continue
            if self._src[self._pos] == "#":
                # Skip to end of line.
                while self._pos < len(self._src) and self._src[self._pos] != "\n":
                    self._pos += 1
                if self._pos < len(self._src):
                    self._consume_newline()
                continue
            # Validate indentation consistency.
            if ws and self._indent_unit is None:
                self._indent_unit = "tab" if ws[0] == "\t" else "space"
            if ws:
                used = "tab" if ws[0] == "\t" else "space"
                if self._indent_unit and used != self._indent_unit:
                    raise ParseError(
                        "mixed tabs and spaces in indentation",
                        line=self._line,
                    )
                if any((c != "\t") if used == "tab" else (c != " ") for c in ws):
                    raise ParseError(
                        "mixed tabs and spaces within a single indent",
                        line=self._line,
                    )
            # Compare to indentation stack.
            top = self._indent_stack[-1]
            if ws == top:
                pass  # same level; no INDENT/DEDENT
            elif len(ws) > len(top) and ws.startswith(top):
                self._indent_stack.append(ws)
                yield Token("INDENT", ws, self._line, 1)
            elif len(ws) < len(top):
                # Pop until we find a matching level.
                while self._indent_stack and self._indent_stack[-1] != ws:
                    if len(self._indent_stack[-1]) <= len(ws):
                        raise ParseError(
                            "inconsistent indentation",
                            line=self._line,
                        )
                    self._indent_stack.pop()
                    yield Token("DEDENT", "", self._line, 1)
                if not self._indent_stack:
                    raise ParseError(
                        "indentation does not match any open block",
                        line=self._line,
                    )
            else:
                raise ParseError(
                    "inconsistent indentation",
                    line=self._line,
                )
            # Now lex the rest of the line.
            yield from self._lex_line()

    def _read_leading_ws(self) -> str:
        start = self._pos
        while self._pos < len(self._src) and self._src[self._pos] in " \t":
            self._pos += 1
            self._col += 1
        return self._src[start : self._pos]

    def _consume_newline(self) -> None:
        if self._pos < len(self._src) and self._src[self._pos] == "\r":
            self._pos += 1
        if self._pos < len(self._src) and self._src[self._pos] == "\n":
            self._pos += 1
        self._line += 1
        self._col = 1

    def _lex_line(self) -> Iterator[Token]:
        """Lex tokens from the current position to the next newline."""
        while self._pos < len(self._src):
            ch = self._src[self._pos]
            if ch == "\n" or ch == "\r":
                yield Token("NEWLINE", "", self._line, self._col)
                self._consume_newline()
                return
            if ch in " \t":
                self._pos += 1
                self._col += 1
                continue
            if ch == "#":
                while self._pos < len(self._src) and self._src[self._pos] != "\n":
                    self._pos += 1
                continue
            if ch == '"':
                yield self._lex_string()
                continue
            if _is_ident_start(ch):
                yield self._lex_ident()
                continue
            if ch.isdigit() or (
                ch == "-"
                and self._pos + 1 < len(self._src)
                and self._src[self._pos + 1].isdigit()
            ):
                yield self._lex_number()
                continue
            if ch == "=" and self._peek(1) == ">":
                yield Token("ARROW", "=>", self._line, self._col)
                self._pos += 2
                self._col += 2
                continue
            if ch == "=" and self._peek(1) == "=":
                yield Token("EQ", "==", self._line, self._col)
                self._pos += 2
                self._col += 2
                continue
            if ch == "!" and self._peek(1) == "=":
                yield Token("NEQ", "!=", self._line, self._col)
                self._pos += 2
                self._col += 2
                continue
            if ch == "<" and self._peek(1) == "=":
                yield Token("LE", "<=", self._line, self._col)
                self._pos += 2
                self._col += 2
                continue
            if ch == ">" and self._peek(1) == "=":
                yield Token("GE", ">=", self._line, self._col)
                self._pos += 2
                self._col += 2
                continue
            if ch == "<":
                yield Token("LT", "<", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == ">":
                yield Token("GT", ">", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == "!":
                yield Token("BANG", "!", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == ",":
                yield Token("COMMA", ",", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == ".":
                yield Token("DOT", ".", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == "(":
                yield Token("LPAREN", "(", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == ")":
                yield Token("RPAREN", ")", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == "[":
                yield Token("LBRACKET", "[", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            if ch == "]":
                yield Token("RBRACKET", "]", self._line, self._col)
                self._pos += 1
                self._col += 1
                continue
            raise ParseError(
                f"unexpected character {ch!r}",
                line=self._line,
            )

    def _peek(self, offset: int) -> str:
        idx = self._pos + offset
        if idx >= len(self._src):
            return ""
        return self._src[idx]

    def _lex_string(self) -> Token:
        start_col = self._col
        # Triple-quoted string?
        if self._peek(1) == '"' and self._peek(2) == '"':
            # Long string (slice 1's fixture does not use these but the
            # lexer recognizes them for forward compatibility).
            self._pos += 3
            self._col += 3
            buf: list[str] = []
            while self._pos < len(self._src):
                if (
                    self._src[self._pos] == '"'
                    and self._peek(1) == '"'
                    and self._peek(2) == '"'
                ):
                    self._pos += 3
                    self._col += 3
                    return Token("STRING", "".join(buf), self._line, start_col)
                ch = self._src[self._pos]
                if ch == "\\":
                    buf.append(self._lex_escape())
                    continue
                if ch == "\n":
                    self._line += 1
                    self._col = 1
                else:
                    self._col += 1
                buf.append(ch)
                self._pos += 1
            raise ParseError("unterminated long string", line=self._line)
        # Short string.
        self._pos += 1
        self._col += 1
        buf2: list[str] = []
        while self._pos < len(self._src):
            ch = self._src[self._pos]
            if ch == '"':
                self._pos += 1
                self._col += 1
                return Token("STRING", "".join(buf2), self._line, start_col)
            if ch == "\n":
                raise ParseError(
                    "newline inside short string", line=self._line
                )
            if ch == "\\":
                buf2.append(self._lex_escape())
                continue
            buf2.append(ch)
            self._pos += 1
            self._col += 1
        raise ParseError("unterminated string", line=self._line)

    def _lex_escape(self) -> str:
        # Caller has seen the backslash; consume it plus the next char.
        self._pos += 1
        self._col += 1
        if self._pos >= len(self._src):
            raise ParseError("trailing backslash in string", line=self._line)
        esc = self._src[self._pos]
        self._pos += 1
        self._col += 1
        mapping = {
            '"': '"',
            "\\": "\\",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "0": "\0",
        }
        if esc in mapping:
            return mapping[esc]
        raise ParseError(f"unknown escape: \\{esc}", line=self._line)

    def _lex_ident(self) -> Token:
        start = self._pos
        start_col = self._col
        while self._pos < len(self._src) and _is_ident_cont(self._src[self._pos]):
            self._pos += 1
            self._col += 1
        return Token(
            "IDENT", self._src[start : self._pos], self._line, start_col
        )

    def _lex_number(self) -> Token:
        start = self._pos
        start_col = self._col
        if self._src[self._pos] == "-":
            self._pos += 1
            self._col += 1
        while self._pos < len(self._src) and self._src[self._pos].isdigit():
            self._pos += 1
            self._col += 1
        return Token(
            "INT", self._src[start : self._pos], self._line, start_col
        )
