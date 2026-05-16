"""Parse PLAN.md text into a typed Plan object."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    RuledOut,
    Subsection,
    Task,
    TaskStatus,
)

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

# Deps sibling line: optional indent, the literal `@deps` token, then
# whitespace-separated task IDs (bare `T-NNNNNN`, no trailing colon)
# per design doc grammar `Indent* "@deps" (WS TaskRef)+ NL`. The tail
# group captures the remainder of the line; splitting on whitespace
# and validating each ID is a higher-level step.
_DEPS_RE = re.compile(r"^(\s*)@deps\s+(.+)$")

# Stage/Phase heading: any heading level whose title contains
# "Stage N" or "Phase N" with bare digits. Mirrors mcloop's
# ``STAGE_RE`` so loose PLAN.md files parse identically; extends the
# pattern to also pull out the keyword and the title text after the
# (optional) colon. ``## Phase phase_001:`` is intentionally rejected
# (no bare digits) — see design doc section 2.5.
_STAGE_RE = re.compile(
    r"^#+\s+.*?\b(?P<kw>stage|phase)\s+(?P<num>\d+)\b\s*:?\s*(?P<title>.*?)\s*$",
    re.IGNORECASE,
)

# Bugs heading: any heading level whose title is exactly ``Bugs``.
# Mirrors mcloop's ``BUGS_RE``.
_BUGS_RE = re.compile(r"^#+\s+Bugs\s*$", re.IGNORECASE)

# Subsection heading: ``###`` followed by free title text. Only level-3
# headings; level-1/2 are handled by ``_STAGE_RE`` / ``_BUGS_RE`` (or
# the H1 recognizer added in 2.5.3). Subsections sit inside a phase and
# group following tasks until another subsection or phase ends them.
_SUBSECTION_RE = re.compile(r"^###\s+(.+?)\s*$")

# Leading task ID: ``T-NNNNNN:`` followed by mandatory whitespace.
# Per design doc section 4.2 grammar ``TaskId ← TaskRef ":" WS``.
# Recognized only at the start of the task body, before any flag tags.
_TASK_ID_RE = re.compile(r"^(T-\d+):\s+")


def parse_plan(
    text: str,
    *,
    strict: bool = False,
    source_path: Path | None = None,
) -> Plan:
    """Parse PLAN.md ``text`` into a typed :class:`Plan`.

    ``strict=False`` (the default) is compatibility mode — accepts the
    PLAN.md format mcloop's ``checklist.py`` accepts today (no magic
    line, optional task IDs, no phase-id comments). ``strict=True``
    enables the format additions in design doc section 4 (magic line,
    mandatory ``T-NNNNNN:`` ids, ``<!-- phase_id: ... -->`` comments).

    Stage 2.5.1 wired the signature; 2.5.2 (this) walks ``text`` line
    by line, tracking the current phase (or bugs section), the current
    subsection within a phase, and a stack of open tasks by indent.
    Each task line opens or closes scopes by indent comparison, matching
    mcloop's logic in ``checklist.py:parse``. ``@deps`` and
    ``[RULEDOUT]`` sibling lines are routed via :func:`_attach_deps`
    and :func:`_attach_ruledout` and accumulated on the parent task.
    Project title and preamble prose extraction land in 2.5.3;
    syntax-error reporting in 2.5.4; strict-mode behavior in Stage 3.
    """
    del strict
    lines = text.splitlines()

    phases_b: list[_PhaseBuilder] = []
    bugs_b: _BugsBuilder | None = None
    current_phase: _PhaseBuilder | None = None
    current_subsection: _SubsectionBuilder | None = None
    in_bugs = False
    stack: list[_TaskBuilder] = []

    for idx, line in enumerate(lines):
        line_number = idx + 1

        heading = _parse_phase_heading(line)
        if heading is not None:
            ordinal, keyword, title = heading
            current_phase = _PhaseBuilder(
                ordinal=ordinal,
                keyword=keyword,
                title=title,
                line_number=line_number,
            )
            phases_b.append(current_phase)
            current_subsection = None
            in_bugs = False
            stack.clear()
            continue

        if _BUGS_RE.match(line):
            bugs_b = _BugsBuilder(line_number=line_number)
            current_phase = None
            current_subsection = None
            in_bugs = True
            stack.clear()
            continue

        if not in_bugs and current_phase is not None:
            sm = _SUBSECTION_RE.match(line)
            if sm is not None:
                current_subsection = _SubsectionBuilder(
                    title=sm.group(1).strip(),
                    line_number=line_number,
                )
                current_phase.subsections.append(current_subsection)
                stack.clear()
                continue

        scope_tasks = _current_scope_tasks(
            in_bugs=in_bugs,
            bugs_b=bugs_b,
            current_subsection=current_subsection,
            current_phase=current_phase,
        )

        ro = _parse_ruledout_line(line, line_number)
        if ro is not None:
            indent = len(ro.indent)
            roots = scope_tasks if scope_tasks is not None else []
            target = _attach_ruledout(indent, stack, roots)
            if target is not None:
                target.ruled_out.append(RuledOut(text=ro.text, line_number=line_number))
            continue

        dm = _DEPS_RE.match(line)
        if dm is not None:
            indent = len(dm.group(1))
            target, _lenient = _attach_deps(indent, stack)
            if target is not None:
                target.deps.extend(dm.group(2).split())
            continue

        raw = _parse_task_line(line, line_number)
        if raw is None:
            continue

        builder = _build_task(raw)
        indent = builder.indent_level

        while stack and stack[-1].indent_level >= indent:
            stack.pop()

        if stack:
            stack[-1].children.append(builder)
        elif scope_tasks is not None:
            scope_tasks.append(builder)
        # else: task line before any phase/bugs — silently dropped in
        # compat mode; strict mode (2.5.4) will surface this as a
        # PlanSyntaxError.

        stack.append(builder)

    return Plan(
        magic_version=None,
        project_title="",
        preamble="",
        phases=tuple(p.freeze() for p in phases_b),
        bugs=bugs_b.freeze() if bugs_b is not None else None,
        source_path=source_path,
    )


def _parse_phase_heading(line: str) -> tuple[int, str, str] | None:
    """Match a Stage/Phase heading and return (ordinal, keyword, title).

    Returns ``None`` for non-matching lines. The keyword is normalized
    to title case (``Stage`` / ``Phase``); design doc section 11 Q4
    keeps the keyword cosmetic, so the canonicalizer can choose to
    preserve original casing if it wants — the typed model only needs
    one stable form.
    """
    m = _STAGE_RE.match(line)
    if m is None:
        return None
    return int(m.group("num")), m.group("kw").capitalize(), m.group("title").strip()


def _extract_task_id(text: str) -> tuple[str | None, str]:
    """Strip a leading ``T-NNNNNN:`` task ID from ``text``.

    Per design doc section 4.2 grammar, the task ID is the first token
    in the body after the checkbox. Returns ``(None, text)`` when no
    leading task ID is present; task IDs are optional in compat mode
    and mandatory in strict mode (enforced separately in Stage 3).
    """
    m = _TASK_ID_RE.match(text)
    if m is None:
        return None, text
    return m.group(1), text[m.end() :]


def _build_task(raw: _RawTaskLine) -> _TaskBuilder:
    """Classify a checkbox body into a mutable :class:`_TaskBuilder`.

    Order matters: annotations come first (they live at end of line
    and would otherwise be swallowed by the action tag's args, which
    span to end of line). Task ID, then flag tags, then action tag run
    left-to-right. The remaining text is the task description.
    """
    annotations, remaining = _extract_annotations(raw.text)
    task_id, remaining = _extract_task_id(remaining)
    flag_tags, remaining = _extract_flag_tags(remaining)
    action_tag, remaining = _extract_action_tag(remaining)
    return _TaskBuilder(
        task_id=task_id,
        text=remaining.strip(),
        status=TaskStatus.from_marker(raw.status_char),
        flag_tags=flag_tags,
        action_tag=action_tag,
        annotations=annotations,
        indent_level=len(raw.indent),
        line_number=raw.line_number,
    )


def _current_scope_tasks(
    *,
    in_bugs: bool,
    bugs_b: _BugsBuilder | None,
    current_subsection: _SubsectionBuilder | None,
    current_phase: _PhaseBuilder | None,
) -> list[_TaskBuilder] | None:
    """Pick the root-task list for the active section.

    The order encodes the grammar: a Bugs section excludes phase scope;
    inside a phase, a current subsection captures following tasks until
    the next subsection or phase. Returns ``None`` when no section is
    active — i.e. the input has not yet introduced a phase or bugs
    heading, so any task/sibling line is an orphan.
    """
    if in_bugs:
        return bugs_b.tasks if bugs_b is not None else None
    if current_subsection is not None:
        return current_subsection.tasks
    if current_phase is not None:
        return current_phase.tasks
    return None


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


def _attach_deps(
    indent: int,
    stack: Sequence[_T],
) -> tuple[_T | None, bool]:
    """Resolve the task a ``@deps`` sibling line should attach to.

    Walks the open-ancestor stack from innermost (top) to outermost.
    Returns ``(parent, False)`` for the strict form — first ancestor
    whose indent is strictly less than the deps line's indent. Returns
    ``(parent, True)`` for the lenient form — first ancestor at the
    same indent as the deps line, which callers should report as a
    validation warning (the deps was not indented under its task).
    Returns ``(None, False)`` when no candidate exists; the caller
    should drop the line.

    Unlike ``_attach_ruledout`` there is no root-task fallback: a
    ``@deps`` line not anchored to a preceding task at lesser-or-equal
    indent is malformed input rather than an orphan that needs adopting.
    """
    for task in reversed(stack):
        if task.indent_level < indent:
            return task, False
        if task.indent_level == indent:
            return task, True
    return None, False


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


# Mutable builder records used while walking the input. The public
# model dataclasses are frozen, so the parser builds with these and
# calls ``freeze`` to produce the immutable tree at the end. Each
# builder also satisfies the ``_HasIndentLevel`` protocol so the
# existing ``_attach_*`` helpers operate on them directly.


@dataclass
class _TaskBuilder:
    """Mutable counterpart of :class:`Task` used during parsing."""

    task_id: str | None
    text: str
    status: TaskStatus
    flag_tags: tuple[str, ...]
    action_tag: tuple[str, str] | None
    annotations: tuple[tuple[str, str], ...]
    indent_level: int
    line_number: int
    deps: list[str] = field(default_factory=list)
    children: list[_TaskBuilder] = field(default_factory=list)
    ruled_out: list[RuledOut] = field(default_factory=list)

    def freeze(self) -> Task:
        return Task(
            task_id=self.task_id,
            text=self.text,
            status=self.status,
            flag_tags=self.flag_tags,
            action_tag=self.action_tag,
            annotations=self.annotations,
            deps=tuple(self.deps),
            children=tuple(c.freeze() for c in self.children),
            ruled_out=tuple(self.ruled_out),
            indent_level=self.indent_level,
            line_number=self.line_number,
        )


@dataclass
class _SubsectionBuilder:
    """Mutable counterpart of :class:`Subsection`."""

    title: str
    line_number: int
    prose: str = ""
    tasks: list[_TaskBuilder] = field(default_factory=list)

    def freeze(self) -> Subsection:
        return Subsection(
            title=self.title,
            prose=self.prose,
            tasks=tuple(t.freeze() for t in self.tasks),
            line_number=self.line_number,
        )


@dataclass
class _PhaseBuilder:
    """Mutable counterpart of :class:`Phase`."""

    ordinal: int
    keyword: str
    title: str
    line_number: int
    phase_id: str | None = None
    phase_id_source: str = "none"
    prose: str = ""
    subsections: list[_SubsectionBuilder] = field(default_factory=list)
    tasks: list[_TaskBuilder] = field(default_factory=list)

    def freeze(self) -> Phase:
        return Phase(
            phase_id=self.phase_id,
            phase_id_source=self.phase_id_source,
            ordinal=self.ordinal,
            keyword=self.keyword,
            title=self.title,
            prose=self.prose,
            subsections=tuple(s.freeze() for s in self.subsections),
            tasks=tuple(t.freeze() for t in self.tasks),
            line_number=self.line_number,
        )


@dataclass
class _BugsBuilder:
    """Mutable counterpart of :class:`BugsSection`."""

    line_number: int
    tasks: list[_TaskBuilder] = field(default_factory=list)

    def freeze(self) -> BugsSection:
        return BugsSection(
            tasks=tuple(t.freeze() for t in self.tasks),
            line_number=self.line_number,
        )
