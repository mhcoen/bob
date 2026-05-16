"""Parse PLAN.md text into a typed Plan object."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

# Checkbox line: indent, status marker, body text. Matches mcloop's
# CHECKBOX_RE so loose-edited PLAN.md files parse identically.
_CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")

# Leading flag tag: [USER] or [BATCH] anchored at start of input. The
# caller strips any inter-tag whitespace between iterations.
_FLAG_TAG_RE = re.compile(r"^\[(USER|BATCH)\]")

# Leading action tag: [AUTO:<word>]. The argument string is everything
# after the closing bracket (with the single separating space removed).
_ACTION_TAG_RE = re.compile(r"^\[AUTO:(\w+)\]")

# Content of an annotation (the bytes between `[` and `]`): an
# identifier-shaped key, a colon, then mandatory whitespace, then the
# value. The required whitespace after the colon is what distinguishes
# an annotation from an action tag (`[AUTO:run]` has no whitespace
# after its colon).
_ANNOTATION_CONTENT_RE = re.compile(r"^([A-Za-z_]\w*):\s+(.*)$", re.DOTALL)

# RULEDOUT sibling line: optional indent, the literal `[RULEDOUT]`
# token, and optional trailing text. Matches mcloop's `parse`, which
# treats a line as RULEDOUT when its stripped form starts with the
# literal bracket token.
_RULEDOUT_RE = re.compile(r"^(\s*)\[RULEDOUT\](.*)$")


@dataclass(frozen=True)
class _RawTaskLine:
    """Recognize-step output of `_parse_task_line`.

    Stage 2 of the parser splits recognize-then-classify. This record
    is the recognize step: indent text, status marker, body text, and
    source line number. Higher-level functions classify the body into
    task id, tags, deps, and prose.
    """

    indent: str
    status_char: str
    text: str
    line_number: int


def _parse_task_line(line: str, line_number: int) -> _RawTaskLine | None:
    """Match a single checkbox task line. Returns None if not a task."""
    m = _CHECKBOX_RE.match(line)
    if m is None:
        return None
    return _RawTaskLine(
        indent=m.group(1),
        status_char=m.group(2),
        text=m.group(3),
        line_number=line_number,
    )


@dataclass(frozen=True)
class _RawRuledOut:
    """Recognize-step output of `_parse_ruledout_line`.

    A RULEDOUT line is a sibling of the task it pertains to (design
    doc grammar `Indent* "[RULEDOUT]" WS Text NL`). The recognizer
    captures only indent, body text, and source line number; attaching
    the line to its parent task by indentation is a higher-level step.
    """

    indent: str
    text: str
    line_number: int


def _parse_ruledout_line(line: str, line_number: int) -> _RawRuledOut | None:
    """Match a single ``[RULEDOUT]`` sibling line. Returns None otherwise.

    Per mcloop's ``parse``, a line is a RULEDOUT line when its stripped
    form starts with the literal ``[RULEDOUT]`` bracket token. Trailing
    whitespace on the body text is stripped; an empty body is allowed.
    """
    m = _RULEDOUT_RE.match(line)
    if m is None:
        return None
    return _RawRuledOut(
        indent=m.group(1),
        text=m.group(2).strip(),
        line_number=line_number,
    )


class _HasIndentLevel(Protocol):
    """Anything with an integer ``indent_level`` attribute.

    Used by :func:`_attach_ruledout` so it can resolve attachment over
    either the eventual frozen ``Task`` model objects or the mutable
    builder records the parser uses while constructing them.
    """

    @property
    def indent_level(self) -> int: ...


_T = TypeVar("_T", bound=_HasIndentLevel)


def _attach_ruledout(
    indent: int,
    stack: Sequence[_T],
    root_tasks: Sequence[_T],
) -> _T | None:
    """Resolve the task a ``[RULEDOUT]`` line should attach to.

    Mirrors mcloop's ``parse`` (``checklist.py`` lines 189-204): walk
    the open-ancestor stack from innermost to outermost and return the
    first task whose indent is strictly less than the RULEDOUT line's
    indent. If no such ancestor exists in the current phase, fall back
    to the most recent root task (mcloop's "orphan" handling). Returns
    ``None`` only when both ``stack`` and ``root_tasks`` are empty —
    i.e. a stray ``[RULEDOUT]`` before any task in the phase, which
    the caller should drop.
    """
    for task in reversed(stack):
        if task.indent_level < indent:
            return task
    if root_tasks:
        return root_tasks[-1]
    return None


def _extract_flag_tags(text: str) -> tuple[tuple[str, ...], str]:
    """Strip leading USER/BATCH flag tags from ``text``.

    Per design doc section 4.3, flag tags are recognized only at the
    leading position. The caller is responsible for stripping the task
    ID (if any) first. A bracketed flag form appearing later in the
    text is prose and is left in place.
    """
    tags: list[str] = []
    remaining = text
    while True:
        m = _FLAG_TAG_RE.match(remaining)
        if m is None:
            break
        tags.append(m.group(1))
        remaining = remaining[m.end() :].lstrip()
    return tuple(tags), remaining


def _extract_action_tag(text: str) -> tuple[tuple[str, str] | None, str]:
    """Strip a leading ``[AUTO:<action>] <args>`` tag from ``text``.

    Per design doc section 4.3, the action tag is recognized only at
    the leading position, after any flag tags. The argument string is
    the text from the closing bracket to end of line, with the single
    separating whitespace removed. Non-leading ``[AUTO:...]`` tokens
    are prose and are left in place.

    Returns ``(None, text)`` when no leading action tag is present;
    otherwise consumes the rest of the line as the argument string
    and returns ``((action, args), "")``.
    """
    m = _ACTION_TAG_RE.match(text)
    if m is None:
        return None, text
    action = m.group(1)
    args = text[m.end() :].lstrip()
    return (action, args), ""


def _extract_annotations(text: str) -> tuple[tuple[tuple[str, str], ...], str]:
    """Strip trailing ``[key: value]`` annotations from ``text``.

    Per design doc sections 4.2 and 4.3, annotations sit at end of line,
    are bracketed, and are separated from the preceding text by
    whitespace. Multiple annotations are allowed. Values may contain
    balanced bracket pairs; this function scans right-to-left with
    bracket-depth tracking so nested brackets stay inside a single
    annotation rather than being misparsed as a separate one.

    Annotation keys today are ``feat`` and ``fix``; this extractor
    accepts any identifier-shaped key. Validation of allowed keys is
    a separate concern.
    """
    annotations: list[tuple[str, str]] = []
    remaining = text
    while True:
        rstripped = remaining.rstrip()
        if not rstripped.endswith("]"):
            remaining = rstripped
            break
        start = _find_matching_open_bracket(rstripped)
        if start is None:
            remaining = rstripped
            break
        # An annotation must be separated from preceding text by
        # whitespace (or be at column 0). A `[` abutting a non-WS
        # character is part of the task text, not an annotation.
        if start > 0 and not rstripped[start - 1].isspace():
            remaining = rstripped
            break
        content = rstripped[start + 1 : -1]
        m = _ANNOTATION_CONTENT_RE.match(content)
        if m is None:
            remaining = rstripped
            break
        annotations.insert(0, (m.group(1), m.group(2)))
        remaining = rstripped[:start].rstrip()
    return tuple(annotations), remaining


def _find_matching_open_bracket(s: str) -> int | None:
    """Return the index of the ``[`` matching the final ``]`` in ``s``.

    Walks right-to-left tracking bracket depth so balanced nested
    brackets inside the candidate annotation are stepped over.
    Returns ``None`` if ``s`` does not end in ``]`` with a matching
    opener.
    """
    if not s.endswith("]"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == "]":
            depth += 1
        elif c == "[":
            depth -= 1
            if depth == 0:
                return i
    return None
