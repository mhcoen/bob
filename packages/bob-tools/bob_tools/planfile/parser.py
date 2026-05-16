"""Parse PLAN.md text into a typed Plan object."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Protocol

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanSyntaxError,
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

# H1 project-title heading: exactly one ``#`` followed by free title
# text. Per design doc section 4.1 grammar ``Preamble ← H1 NL Prose?``.
# H2 (``##``) is consumed earlier by `_STAGE_RE` / `_BUGS_RE`; this
# regex requires a single leading ``#`` so multi-hash headings cannot
# match here. Recognized only before the first phase or bugs heading;
# subsequent H1 lines (malformed in strict mode, dropped in compat
# mode) fall through to the active prose accumulator.
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")

# Leading task ID: ``T-NNNNNN:`` followed by mandatory whitespace.
# Per design doc section 4.2 grammar ``TaskId ← TaskRef ":" WS``.
# Recognized only at the start of the task body, before any flag tags.
_TASK_ID_RE = re.compile(r"^(T-\d+):\s+")

# Magic format-version line: anchored to a full line per design doc
# section 4.1 grammar ``Magic ← "<!--" WS? "bob-plan-format:" WS? Int
# WS? "-->" NL``. Captures the version integer. Only recognized when
# this is the first non-blank line of the document; later occurrences
# fall through to prose handling.
_MAGIC_RE = re.compile(r"^<!--\s*bob-plan-format:\s*(\d+)\s*-->\s*$")

# Phase-id comment: ``<!-- phase_id: phase_NNN -->``. Captures the id.
# Pattern must match ``mcloop/ledger_emit.py``'s ``_PHASE_ID_COMMENT_RE``
# byte-for-byte (modulo group naming) so the two libraries cannot
# disagree about which lines carry a phase id. Used in search-form for
# line scanning; ``_parse_phase_id_comment_line`` wraps it with
# fullmatch-on-stripped-line semantics so a comment embedded in a task
# line is not mistaken for a phase-level annotation.
_PHASE_ID_COMMENT_RE = re.compile(r"<!--\s*phase_id\s*:\s*([A-Za-z0-9_]+)\s*-->")

# Ledger-form phase heading: ``## Phase <id>: <title>`` where the id is
# identifier-shaped (e.g. ``phase_001``) rather than a bare integer.
# Pattern must match ``mcloop/ledger_emit.py``'s ``_PHASE_HEADER_RE``
# byte-for-byte so the two libraries cannot disagree about which lines
# are ledger-form phase headers. Distinct from ``_STAGE_RE``: the
# compat-mode regex requires bare digits in the ordinal and would
# reject ``## Phase phase_001: ...``; this strict/ledger form accepts
# the identifier directly as the phase id.
_LEDGER_PHASE_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)

# Format versions this parser knows how to read. Listed as a tuple so
# adding a v2 later is a single-line change. Absence of the magic line
# triggers compat mode; presence with a version outside this set is a
# fail-fast PlanSyntaxError so a v2-only file does not silently degrade.
_SUPPORTED_MAGIC_VERSIONS: tuple[int, ...] = (1,)


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

    Stage 2.5.1 wired the signature; 2.5.2 walks ``text`` line by line,
    tracking the current phase (or bugs section), the current subsection
    within a phase, and a stack of open tasks by indent. 2.5.3 extracts
    the project title from the first H1 and accumulates prose in three
    regions: the preamble (after the H1, before the first phase or bugs
    heading), phase prose (after a phase heading, before the first task
    or subsection), and subsection prose (after a ``###`` heading,
    before the first task). Prose-region accumulators close when the
    relevant boundary line is consumed; lines outside any active
    accumulator (e.g. a task line before any phase, or prose after the
    first task in a phase) are dropped silently in compat mode (the
    orphan-task case matches mcloop, which assigns ``stage=""`` rather
    than erroring; strict mode in Stage 3 will raise). Each task line
    opens or closes scopes by indent comparison, matching mcloop's logic
    in ``checklist.py:parse``. ``@deps`` and ``[RULEDOUT]`` sibling
    lines are routed via :func:`_attach_deps` and :func:`_attach_ruledout`
    and accumulated on the parent task. 2.5.4 raises
    :class:`PlanSyntaxError` on syntax violations even in compat mode:
    today, an ``@deps`` line that cannot attach to any preceding task
    in the current section. The orphan ``@deps`` case has no semantic
    interpretation and no mcloop-tolerance to preserve (mcloop never
    recognized ``@deps`` at all). The error message quotes the offending
    line so the location is unambiguous to the human fixing the file.
    2.6.1 runs :func:`_check_structural_sanity` first: it scans
    the raw lines for duplicate H1 titles, multiple Bugs sections, and
    duplicate phase/stage ordinals before any structural parsing
    happens, so the typed model never has to represent a corrupted
    document. Mirrors mcloop's pre-parse corruption check.
    3.2.2 (this) accepts the ledger-form ``## Phase <id>: <title>``
    heading whose id is non-numeric (e.g. ``phase_001``), per design
    doc section 7.1 mechanism 2: the line opens a new phase with
    ``phase_id`` set from the heading and ``phase_id_source`` set to
    ``"explicit_header"``. The ordinal in this case is positional
    (``len(phases) + 1``) since there is no digit to extract. The
    ordinal-form check runs first so ``## Phase 1: ...`` still takes
    the ordinal path and produces ``phase_id=None`` until an explicit
    comment attaches one.
    """
    del strict
    lines = text.splitlines()
    magic_version = _detect_magic_line(lines, source_path)
    _check_structural_sanity(lines, source_path)

    phases_b: list[_PhaseBuilder] = []
    bugs_b: _BugsBuilder | None = None
    current_phase: _PhaseBuilder | None = None
    current_subsection: _SubsectionBuilder | None = None
    in_bugs = False
    stack: list[_TaskBuilder] = []

    project_title = ""
    project_title_seen = False
    preamble_lines: list[str] = []
    preamble_active = False
    phase_prose_lines: list[str] | None = None
    subsection_prose_lines: list[str] | None = None

    def _close_subsection_prose() -> None:
        nonlocal subsection_prose_lines
        if subsection_prose_lines is not None and current_subsection is not None:
            current_subsection.prose = _finalize_prose(subsection_prose_lines)
        subsection_prose_lines = None

    def _close_phase_prose() -> None:
        nonlocal phase_prose_lines
        if phase_prose_lines is not None and current_phase is not None:
            current_phase.prose = _finalize_prose(phase_prose_lines)
        phase_prose_lines = None

    for idx, line in enumerate(lines):
        line_number = idx + 1

        heading = _parse_phase_heading(line)
        if heading is not None:
            _close_subsection_prose()
            _close_phase_prose()
            preamble_active = False
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
            phase_prose_lines = []
            continue

        ledger_heading = _parse_ledger_phase_heading(line)
        if ledger_heading is not None:
            _close_subsection_prose()
            _close_phase_prose()
            preamble_active = False
            phase_id, title = ledger_heading
            current_phase = _PhaseBuilder(
                ordinal=len(phases_b) + 1,
                keyword="Phase",
                title=title,
                phase_id=phase_id,
                phase_id_source="explicit_header",
                line_number=line_number,
            )
            phases_b.append(current_phase)
            current_subsection = None
            in_bugs = False
            stack.clear()
            phase_prose_lines = []
            continue

        if _BUGS_RE.match(line):
            _close_subsection_prose()
            _close_phase_prose()
            preamble_active = False
            bugs_b = _BugsBuilder(line_number=line_number)
            current_phase = None
            current_subsection = None
            in_bugs = True
            stack.clear()
            continue

        if current_phase is None and not in_bugs:
            h1_m = _H1_RE.match(line)
            if h1_m is not None:
                if not project_title_seen:
                    project_title = h1_m.group(1).strip()
                    project_title_seen = True
                    preamble_active = True
                    continue
                # Subsequent H1 falls through to the preamble accumulator
                # so the raw text is preserved. Strict mode (Stage 3)
                # will raise on duplicate H1s.

        if not in_bugs and current_phase is not None:
            sm = _SUBSECTION_RE.match(line)
            if sm is not None:
                _close_subsection_prose()
                _close_phase_prose()
                current_subsection = _SubsectionBuilder(
                    title=sm.group(1).strip(),
                    line_number=line_number,
                )
                current_phase.subsections.append(current_subsection)
                stack.clear()
                subsection_prose_lines = []
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
            if target is None:
                _raise_syntax_error(
                    "@deps line has no preceding task to attach to",
                    line_text=line,
                    line_number=line_number,
                    source_path=source_path,
                    column=indent + 1,
                )
            target.deps.extend(dm.group(2).split())
            continue

        raw = _parse_task_line(line, line_number)
        if raw is None:
            if (
                phase_prose_lines is not None
                and current_phase is not None
                and subsection_prose_lines is None
            ):
                pid = _parse_phase_id_comment_line(line)
                if pid is not None:
                    # Per design doc grammar the phase-id comment sits
                    # between the phase heading and any prose, so this
                    # branch fires while `phase_prose_lines` is still
                    # the active accumulator. Overwriting on a repeat
                    # comment matches mcloop's ``find_explicit_phase_id_for_task``
                    # behavior (last write wins) so the two libraries
                    # never disagree about which id a phase carries.
                    current_phase.phase_id = pid
                    current_phase.phase_id_source = "explicit_comment"
                    continue
            if preamble_active:
                preamble_lines.append(line)
            elif subsection_prose_lines is not None:
                subsection_prose_lines.append(line)
            elif phase_prose_lines is not None:
                phase_prose_lines.append(line)
            # else: line outside any active prose region (e.g. before
            # the first H1 or after the first task in a section) —
            # dropped in compat mode.
            continue

        _close_subsection_prose()
        _close_phase_prose()

        builder = _build_task(raw)
        indent = builder.indent_level

        while stack and stack[-1].indent_level >= indent:
            stack.pop()

        if stack:
            stack[-1].children.append(builder)
        elif scope_tasks is not None:
            scope_tasks.append(builder)
        # else: task line before any phase/bugs — silently dropped in
        # compat mode (matches mcloop, which assigns ``stage=""``);
        # strict mode (Stage 3) will surface this as a PlanSyntaxError.

        stack.append(builder)

    _close_subsection_prose()
    _close_phase_prose()

    return Plan(
        magic_version=magic_version,
        project_title=project_title,
        preamble=_finalize_prose(preamble_lines),
        phases=tuple(p.freeze() for p in phases_b),
        bugs=bugs_b.freeze() if bugs_b is not None else None,
        source_path=source_path,
    )


def _detect_magic_line(
    lines: list[str],
    source_path: Path | None,
) -> int | None:
    """Return the magic-line version, or ``None`` if absent.

    Per design doc section 4.1, the magic line ``<!-- bob-plan-format:
    N -->`` is the first non-blank line of a strict-form PLAN.md. The
    parser only recognizes it in that position; a stray match later in
    the file falls through to ordinary prose handling so it cannot
    silently upgrade a compat-mode plan to strict-form parsing. An
    unrecognized version (anything not in :data:`_SUPPORTED_MAGIC_VERSIONS`)
    is rejected with :class:`PlanSyntaxError`; this fail-fast matches the
    contract in the task description and ensures a v2-only file does not
    parse as v1 by accident.
    """
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        m = _MAGIC_RE.match(line)
        if m is None:
            return None
        version = int(m.group(1))
        if version not in _SUPPORTED_MAGIC_VERSIONS:
            _raise_syntax_error(
                f"unrecognized bob-plan-format version {version}",
                line_text=line,
                line_number=idx + 1,
                source_path=source_path,
            )
        return version
    return None


def _parse_phase_id_comment_line(line: str) -> str | None:
    """Return the phase id when ``line`` is a standalone phase-id comment.

    The line content (after stripping leading and trailing whitespace)
    must consist solely of the ``<!-- phase_id: ... -->`` marker. A
    comment embedded inside a task line (e.g. ``- [ ] ... <!-- phase_id:
    phase_002 -->``) returns ``None`` because the design doc scopes
    phase-id comments to phase-level attachment, not per-task. Using
    ``fullmatch`` on the stripped line is the cheapest way to distinguish
    "comment is the whole line" from "comment is somewhere on the line".
    """
    m = _PHASE_ID_COMMENT_RE.fullmatch(line.strip())
    if m is None:
        return None
    return m.group(1)


def _check_structural_sanity(
    lines: list[str],
    source_path: Path | None,
) -> None:
    """Scan ``lines`` for structural corruption signals before parsing.

    Three anomalies are flagged, mirroring mcloop's
    ``_check_structural_sanity`` (``mcloop/checklist.py``):

    1. Duplicate H1 headings with identical title text — typically a
       botched insertion that left the original tail in place,
       producing two copies of the document's top header.
    2. Multiple Bugs sections (any heading level) — there should
       only ever be one.
    3. Duplicate phase/stage ordinals across stage headers — two
       headers numbered ``Phase 2``, for example. This breaks any
       label that prefixes with the stage number and almost always
       signals merged content from two attempts at the same phase.

    Each anomaly has been observed in real PLAN.md corruption
    incidents and none has a legitimate use; auto-fixing is
    deliberately not attempted because mutating an already-corrupted
    plan risks compounding the corruption rather than recovering it.
    Raises :class:`PlanSyntaxError` whose message lists every anomaly
    found, with one-based source line numbers, so the user can locate
    and fix the corruption by hand.
    """
    h1_titles: dict[str, list[int]] = {}
    bugs_lines: list[int] = []
    stage_nums: dict[int, list[int]] = {}

    for i, line in enumerate(lines):
        # Order matters: a single-hash ``# Phase 1: Bootstrapping``
        # is matched by both ``_STAGE_RE`` and ``_H1_RE``. A header is
        # either a stage header OR a plain H1 OR a Bugs header — never
        # two of those at once. Check stage first so ``# Phase N`` is
        # not double-counted as an H1 duplicate and a stage duplicate.
        stage_match = _STAGE_RE.match(line)
        if stage_match is not None:
            stage_nums.setdefault(int(stage_match.group("num")), []).append(i)
            continue

        if _BUGS_RE.match(line):
            bugs_lines.append(i)
            continue

        h1_match = _H1_RE.match(line)
        if h1_match is not None:
            # ``_H1_RE`` requires a single leading ``#``; the explicit
            # ``not startswith("## ")`` guard is belt-and-suspenders so
            # the intent (true H1 only) is visible in the reader's eye.
            if line.startswith("# ") and not line.startswith("## "):
                title = h1_match.group(1).strip()
                h1_titles.setdefault(title, []).append(i)

    problems: list[tuple[int, str]] = []

    for title, line_nums in h1_titles.items():
        if len(line_nums) > 1:
            locs = ", ".join(str(n + 1) for n in line_nums)
            problems.append(
                (
                    line_nums[0] + 1,
                    f"duplicate top-level heading '# {title}' at lines {locs}",
                )
            )

    if len(bugs_lines) > 1:
        locs = ", ".join(str(n + 1) for n in bugs_lines)
        problems.append((bugs_lines[0] + 1, f"multiple Bugs sections at lines {locs}"))

    for num, line_nums in stage_nums.items():
        if len(line_nums) > 1:
            locs = ", ".join(str(n + 1) for n in line_nums)
            problems.append(
                (line_nums[0] + 1, f"duplicate Phase/Stage {num} at lines {locs}")
            )

    if not problems:
        return

    problems.sort(key=lambda p: p[0])
    first_line = problems[0][0]
    body = "\n  - ".join(msg for _, msg in problems)
    raise PlanSyntaxError(
        f"structural corruption detected:\n  - {body}",
        first_line,
        1,
        source_path,
    )


def _raise_syntax_error(
    message: str,
    *,
    line_text: str,
    line_number: int,
    source_path: Path | None,
    column: int = 1,
) -> NoReturn:
    """Raise :class:`PlanSyntaxError` with the offending line quoted.

    Per the design doc section 9 contract, the error carries
    ``(message, line, column, path)``; ``__str__`` formats them as
    ``PLAN.md invalid at line N, column M: <message>``. The task
    description for 2.5.4 adds: the message itself must quote the
    offending line so the human reading the error can see exactly which
    text triggered the rejection without having to open the file. The
    quoted form uses backticks rather than ``repr()`` because the line
    has already had its trailing newline stripped by ``splitlines()``,
    so there is no escape-sequence ambiguity to disambiguate.
    """
    raise PlanSyntaxError(
        f"{message}: `{line_text}`",
        line_number,
        column,
        source_path,
    )


def _finalize_prose(lines: list[str]) -> str:
    """Join accumulated prose lines, trimming blank lines from the ends.

    Internal blank lines (paragraph breaks) and any leading/trailing
    whitespace within a non-blank line are preserved — Markdown attaches
    semantic weight to indent (code blocks, list continuations), so we
    only strip wholly-blank wrapper lines rather than calling
    ``str.strip()`` on the joined text.
    """
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


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


def _parse_ledger_phase_heading(line: str) -> tuple[str, str] | None:
    """Match the ledger-form ``## Phase <id>: <title>`` heading.

    Per design doc section 7.1 mechanism 2: a heading with a non-numeric
    identifier (e.g. ``## Phase phase_001: Core``) does not match
    :data:`_STAGE_RE` (which requires bare digits after the keyword) but
    must still be accepted so a plan written in the ledger's own form
    parses. Returns ``(phase_id, title)`` or ``None``. The caller checks
    this only after :func:`_parse_phase_heading` has failed, so a digit
    id like ``## Phase 1: Core`` is consumed by the ordinal path first
    and never reaches here (which would otherwise set
    ``phase_id="1"`` from the same line).
    """
    m = _LEDGER_PHASE_HEADER_RE.match(line)
    if m is None:
        return None
    return m.group("id"), m.group("title").strip()


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


def _attach_ruledout[T: _HasIndentLevel](
    indent: int,
    stack: Sequence[T],
    root_tasks: Sequence[T],
) -> T | None:
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


def _attach_deps[T: _HasIndentLevel](
    indent: int,
    stack: Sequence[T],
) -> tuple[T | None, bool]:
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
