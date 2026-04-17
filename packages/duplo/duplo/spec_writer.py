"""Text-layer helpers for modifying SPEC.md content.

This module operates on raw spec text (strings), not on parsed
``ProductSpec`` objects.  It must NOT import from pipeline-stage
modules (``extractor``, ``design_extractor``, etc.).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from duplo.claude_cli import ClaudeCliError, query_with_images
from duplo.diagnostics import record_failure
from duplo.parsing import extract_json
from duplo.spec_reader import (
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
)
from duplo.url_canon import canonicalize_url

# Matches a ``## Sources`` heading (exactly level 2).
_SOURCES_HEADING = re.compile(r"^## Sources\s*$", re.MULTILINE)

# Matches a ``## References`` heading (exactly level 2).
_REFERENCES_HEADING = re.compile(r"^## References\s*$", re.MULTILINE)

# Matches a ``## Purpose`` heading (exactly level 2).
_PURPOSE_HEADING = re.compile(r"^## Purpose\s*$", re.MULTILINE)

# Matches a ``## Architecture`` heading.
_ARCHITECTURE_HEADING = re.compile(r"^## Architecture\s*$", re.MULTILINE)

# Matches a ``## Design`` heading (exactly level 2).
_DESIGN_HEADING = re.compile(r"^## Design\s*$", re.MULTILINE)

# AUTO-GENERATED block markers — MUST match ``_AUTOGEN_RE`` in spec_reader.py.
_AUTOGEN_RE = re.compile(
    r"<!--\s*BEGIN AUTO-GENERATED[^>]*-->(.*?)<!--\s*END AUTO-GENERATED\s*-->",
    re.DOTALL,
)

_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED design-requirements -->"
_END_MARKER = "<!-- END AUTO-GENERATED -->"

# Matches a source entry start line: ``- <url>``
_SOURCE_ENTRY_START = re.compile(r"^-\s+(https?://\S+)\s*$", re.MULTILINE)

# Matches a reference entry start line: ``- <path>``.  The path is
# anything on the same line that is not an HTTP(S) URL (Sources own
# those) and is not the start of a comment or another Markdown list
# construct we care about.
_REFERENCE_ENTRY_START = re.compile(r"^-\s+(?!https?://)(\S+)\s*$", re.MULTILINE)

# Ordered list of (pattern, role) pairs used by ``_infer_url_role``.
# The role is chosen by the earliest-starting match across all
# patterns; ties break by list order, so counter-example patterns
# precede product-reference patterns that share a keyword (e.g.
# ``not like`` vs ``like``).
_URL_ROLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnot\s+like\b", re.IGNORECASE), "counter-example"),
    (re.compile(r"\bunlike\b", re.IGNORECASE), "counter-example"),
    (re.compile(r"\bavoid\b", re.IGNORECASE), "counter-example"),
    (re.compile(r"\bsee\s+also\b", re.IGNORECASE), "docs"),
    (re.compile(r"\bfor\s+reference\b", re.IGNORECASE), "docs"),
    (re.compile(r"\blike\b", re.IGNORECASE), "product-reference"),
    (re.compile(r"\bsuch\s+as\b", re.IGNORECASE), "product-reference"),
    (re.compile(r"\binspired\s+by\b", re.IGNORECASE), "product-reference"),
]


def _infer_url_role(context: str) -> str:
    """Infer a ``## Sources`` role from prose surrounding a URL.

    Light heuristic per DRAFTER-design.md § "Inferring URL roles":
    "like"/"such as"/"inspired by" → ``product-reference``;
    "see also"/"for reference" → ``docs``;
    "not like"/"unlike"/"avoid" → ``counter-example``. Falls back to
    ``product-reference`` when nothing matches. When multiple
    patterns match, the one starting earliest in *context* wins.
    """
    earliest_pos: int | None = None
    earliest_role = "product-reference"
    for pattern, role in _URL_ROLE_PATTERNS:
        m = pattern.search(context)
        if m is None:
            continue
        if earliest_pos is None or m.start() < earliest_pos:
            earliest_pos = m.start()
            earliest_role = role
    return earliest_role


def _extract_existing_urls(sources_body: str) -> set[str]:
    """Return the set of canonical URLs already present in a Sources body."""
    urls: set[str] = set()
    for m in _SOURCE_ENTRY_START.finditer(sources_body):
        urls.add(canonicalize_url(m.group(1)))
    return urls


def _format_entry(entry: SourceEntry) -> str:
    """Format a single SourceEntry as spec text lines."""
    lines = [f"- {entry.url}"]
    lines.append(f"  role: {entry.role}")
    lines.append(f"  scrape: {entry.scrape}")
    if entry.notes:
        lines.append(f"  notes: {entry.notes}")
    if entry.proposed:
        lines.append("  proposed: true")
    if entry.discovered:
        lines.append("  discovered: true")
    return "\n".join(lines)


def _sources_section_range(
    text: str,
) -> tuple[int, int] | None:
    """Find the start and end offsets of the ``## Sources`` section body.

    Returns ``(body_start, body_end)`` where *body_start* is the offset
    immediately after the heading line (including its newline) and
    *body_end* is the offset of the next ``##`` heading or end of text.
    Returns ``None`` if no ``## Sources`` heading exists.
    """
    m = _SOURCES_HEADING.search(text)
    if m is None:
        return None
    # Body starts after the heading line.
    body_start = m.end()
    # Find the next level-2 heading.
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        body_end = body_start + next_heading.start()
    else:
        body_end = len(text)
    return body_start, body_end


def append_sources(
    existing_spec_text: str,
    new_entries: list[SourceEntry],
) -> str:
    """Append new source entries to ``## Sources``, deduplicating by URL.

    Skips entries whose canonical URL already appears in the section.
    If ``## Sources`` does not exist, creates it after ``## Architecture``
    (if present) or at the end of the file.

    Returns the modified spec text.
    """
    if not new_entries:
        return existing_spec_text

    section_range = _sources_section_range(existing_spec_text)

    if section_range is not None:
        body_start, body_end = section_range
        sources_body = existing_spec_text[body_start:body_end]
        existing_urls = _extract_existing_urls(sources_body)

        # Filter out duplicates.
        to_add = [e for e in new_entries if canonicalize_url(e.url) not in existing_urls]
        if not to_add:
            return existing_spec_text

        # Build the text block to insert.
        formatted = "\n".join(_format_entry(e) for e in to_add)

        # Determine insertion point: end of the section body, before
        # any trailing whitespace that precedes the next heading.
        body_text = existing_spec_text[body_start:body_end]
        stripped = body_text.rstrip("\n")
        insert_at = body_start + len(stripped)

        return (
            existing_spec_text[:insert_at]
            + "\n"
            + formatted
            + "\n"
            + existing_spec_text[body_end:]
        )
    else:
        # No ## Sources section — create one.
        formatted = "\n".join(_format_entry(e) for e in new_entries)
        new_section = f"\n## Sources\n\n{formatted}\n"

        # Place after ## Architecture if present, else at end.
        arch_range = _architecture_section_end(existing_spec_text)
        if arch_range is not None:
            insert_at = arch_range
            return (
                existing_spec_text[:insert_at].rstrip("\n")
                + "\n"
                + new_section
                + existing_spec_text[insert_at:]
            )
        else:
            return existing_spec_text.rstrip("\n") + "\n" + new_section


def _normalize_ref_path(path: str) -> str:
    """Compare-as-is with trailing slash stripped."""
    return path.rstrip("/")


def _references_section_range(text: str) -> tuple[int, int] | None:
    """Find the start and end offsets of ``## References`` body.

    Same semantics as :func:`_sources_section_range`.
    """
    m = _REFERENCES_HEADING.search(text)
    if m is None:
        return None
    body_start = m.end()
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        body_end = body_start + next_heading.start()
    else:
        body_end = len(text)
    return body_start, body_end


def _extract_existing_reference_paths(references_body: str) -> set[str]:
    """Return the set of normalized paths already present in References."""
    paths: set[str] = set()
    for m in _REFERENCE_ENTRY_START.finditer(references_body):
        paths.add(_normalize_ref_path(m.group(1)))
    return paths


def _purpose_section_end(text: str) -> int | None:
    """Return the end offset of the ``## Purpose`` section body."""
    m = _PURPOSE_HEADING.search(text)
    if m is None:
        return None
    body_start = m.end()
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        return body_start + next_heading.start()
    return len(text)


def append_references(
    existing_spec_text: str,
    new_entries: list[ReferenceEntry],
) -> str:
    """Append new reference entries to ``## References``, deduplicating by path.

    Deduplication is path-only: two entries with the same path (after
    stripping trailing slash) are duplicates regardless of role.
    First-write-wins — the existing entry is kept and the incoming
    entry is skipped.

    If ``## References`` does not exist, creates it after ``## Sources``
    (if present), else after ``## Purpose`` (if present), else at the
    end of the file.

    Side-effect-free: takes existing content as a string, returns the
    modified string.
    """
    if not new_entries:
        return existing_spec_text

    section_range = _references_section_range(existing_spec_text)

    if section_range is not None:
        body_start, body_end = section_range
        references_body = existing_spec_text[body_start:body_end]
        existing_paths = _extract_existing_reference_paths(references_body)

        to_add: list[ReferenceEntry] = []
        seen_in_batch: set[str] = set()
        for e in new_entries:
            key = _normalize_ref_path(str(e.path))
            if key in existing_paths or key in seen_in_batch:
                continue
            seen_in_batch.add(key)
            to_add.append(e)
        if not to_add:
            return existing_spec_text

        formatted = "\n".join(_format_reference_entry(e) for e in to_add)

        body_text = existing_spec_text[body_start:body_end]
        stripped = body_text.rstrip("\n")
        insert_at = body_start + len(stripped)

        return (
            existing_spec_text[:insert_at]
            + "\n"
            + formatted
            + "\n"
            + existing_spec_text[body_end:]
        )

    # No ## References section — create one.
    # Dedup within the incoming batch (first-write-wins).
    seen: set[str] = set()
    deduped: list[ReferenceEntry] = []
    for e in new_entries:
        key = _normalize_ref_path(str(e.path))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    formatted = "\n".join(_format_reference_entry(e) for e in deduped)
    new_section = f"\n## References\n\n{formatted}\n"

    # Placement: after ## Sources if present, else after ## Purpose,
    # else at end of file.
    sources_end = _sources_section_end(existing_spec_text)
    if sources_end is not None:
        insert_at = sources_end
    else:
        purpose_end = _purpose_section_end(existing_spec_text)
        if purpose_end is not None:
            insert_at = purpose_end
        else:
            return existing_spec_text.rstrip("\n") + "\n" + new_section

    return (
        existing_spec_text[:insert_at].rstrip("\n")
        + "\n"
        + new_section
        + existing_spec_text[insert_at:]
    )


def _design_section_range(text: str) -> tuple[int, int] | None:
    """Find the start and end offsets of the ``## Design`` section body.

    Returns ``(body_start, body_end)`` where *body_start* is the offset
    immediately after the heading line (including its newline) and
    *body_end* is the offset of the next ``##`` heading or end of text.
    Returns ``None`` if no ``## Design`` heading exists.
    """
    m = _DESIGN_HEADING.search(text)
    if m is None:
        return None
    body_start = m.end()
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        body_end = body_start + next_heading.start()
    else:
        body_end = len(text)
    return body_start, body_end


def _sources_section_end(text: str) -> int | None:
    """Return the end offset of the ``## Sources`` section body.

    Returns the offset where the next ``##`` heading starts, or end of
    text if Sources is the last section.  Returns ``None`` if no
    ``## Sources`` heading exists.
    """
    m = _SOURCES_HEADING.search(text)
    if m is None:
        return None
    body_start = m.end()
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        return body_start + next_heading.start()
    return len(text)


def _format_autogen_block(body: str) -> str:
    """Wrap *body* in BEGIN/END AUTO-GENERATED markers."""
    return f"{_BEGIN_MARKER}\n{body}\n{_END_MARKER}"


def update_design_autogen(existing_spec_text: str, body: str) -> str:
    """Populate the AUTO-GENERATED block in ``## Design``.

    Write-once-never-replace semantics: if a well-formed
    AUTO-GENERATED block with non-empty body already exists, returns
    *existing_spec_text* unchanged.  An existing block with an empty
    body IS replaced (allows regeneration after the user clears it).

    If ``## Design`` exists but has no AUTO-GENERATED block, appends
    the block after any existing user prose.  If ``## Design`` does
    not exist, creates the section.  Placement: after ``## Sources``
    if present, else after ``## Architecture`` if present, else at
    the end of the file.
    """
    section_range = _design_section_range(existing_spec_text)

    if section_range is not None:
        body_start, body_end = section_range
        section_body = existing_spec_text[body_start:body_end]

        # Check for existing AUTO-GENERATED block.
        m = _AUTOGEN_RE.search(section_body)
        if m:
            existing_autogen = m.group(1).strip()
            if existing_autogen:
                # Non-empty: write-once, do not replace.
                return existing_spec_text
            # Empty block: replace it.
            block = _format_autogen_block(body)
            abs_start = body_start + m.start()
            abs_end = body_start + m.end()
            return existing_spec_text[:abs_start] + block + existing_spec_text[abs_end:]

        # No autogen block — append at end of section body.
        block = _format_autogen_block(body)
        stripped = section_body.rstrip("\n")
        insert_at = body_start + len(stripped)
        return (
            existing_spec_text[:insert_at] + "\n\n" + block + "\n" + existing_spec_text[body_end:]
        )
    else:
        # No ## Design section — create one.
        block = _format_autogen_block(body)
        new_section = f"\n## Design\n\n{block}\n"

        # Placement: after ## Sources if present, else after
        # ## Architecture, else at end.
        sources_end = _sources_section_end(existing_spec_text)
        if sources_end is not None:
            insert_at = sources_end
            return (
                existing_spec_text[:insert_at].rstrip("\n")
                + "\n"
                + new_section
                + existing_spec_text[insert_at:]
            )
        arch_end = _architecture_section_end(existing_spec_text)
        if arch_end is not None:
            insert_at = arch_end
            return (
                existing_spec_text[:insert_at].rstrip("\n")
                + "\n"
                + new_section
                + existing_spec_text[insert_at:]
            )
        return existing_spec_text.rstrip("\n") + "\n" + new_section


def _architecture_section_end(text: str) -> int | None:
    """Return the end offset of the ``## Architecture`` section body.

    Returns the offset where the next ``##`` heading starts, or end of
    text if Architecture is the last section.  Returns ``None`` if no
    ``## Architecture`` heading exists.
    """
    m = _ARCHITECTURE_HEADING.search(text)
    if m is None:
        return None
    body_start = m.end()
    next_heading = re.search(r"^## ", text[body_start:], re.MULTILINE)
    if next_heading:
        return body_start + next_heading.start()
    return len(text)


# --------------------------------------------------------------------------
# format_spec — serialize a ProductSpec to SPEC.md text
# --------------------------------------------------------------------------
#
# The template strings below mirror the content of SPEC-template.md.
# Tests pin that format_spec(ProductSpec()) matches the template shape.

_TEMPLATE_TOP_MATTER = (
    "# SPEC\n"
    "\n"
    "<!--\n"
    "Your specification for what duplo should build.\n"
    "Fill in sections marked <FILL IN>. Leave others blank to skip.\n"
    "\n"
    "How the pieces fit together:\n"
    "SPEC.md → duplo → PLAN.md → mcloop.\n"
    "You author SPEC.md. duplo generates PLAN.md from it. mcloop\n"
    "executes PLAN.md. mcloop never reads SPEC.md.\n"
    "\n"
    "duplo may append to ## Sources and ## References (marked\n"
    "`proposed: true`) but never modifies your other sections.\n"
    "\n"
    "See SPEC-guide.md for details.\n"
    "-->"
)

_PURPOSE_FILL_IN = "<FILL IN: one or two sentences describing what you're building>"

_ARCHITECTURE_FILL_IN = "<FILL IN: language, framework, platform, constraints>"

_SOURCES_COMMENT = (
    "<!-- URLs duplo should scrape. See SPEC-guide.md for role/scrape options. -->\n"
    "\n"
    "<!-- Example:\n"
    "- https://numi.app\n"
    "  role: product-reference\n"
    "  scrape: deep\n"
    "-->"
)

_REFERENCES_COMMENT = (
    "<!-- Files in ref/. Optional if ## Sources or ## Purpose is enough. -->\n"
    "\n"
    "<!-- Example:\n"
    "- ref/numi-main.png\n"
    "  role: visual-target\n"
    "-->"
)

_DESIGN_COMMENT = "<!-- Optional if ## References has visual-target files. -->"

_SCOPE_COMMENT = (
    "<!-- Optional. Overrides for include/exclude. -->\n"
    "\n"
    "<!-- Example:\n"
    "include:\n"
    "  - Unit conversion\n"
    "exclude:\n"
    "  - Plugin API\n"
    "-->"
)

_BEHAVIOR_COMMENT = (
    "<!-- Optional. Input → output pairs become verification tasks. -->\n"
    "\n"
    "<!-- Example:\n"
    "- `2 + 3` → `5`\n"
    "- `5 km in miles` → `3.11 mi`\n"
    "-->"
)

_NOTES_COMMENT = "<!-- Optional. Free-form context for duplo. -->"


def _format_reference_entry(entry: ReferenceEntry) -> str:
    """Format a single ReferenceEntry as spec text lines."""
    lines = [f"- {entry.path}"]
    if entry.roles:
        lines.append(f"  role: {', '.join(entry.roles)}")
    if entry.notes:
        lines.append(f"  notes: {entry.notes}")
    if entry.proposed:
        lines.append("  proposed: true")
    return "\n".join(lines)


def _format_design_section(design: DesignBlock) -> str:
    """Format the body of the ``## Design`` section."""
    has_user = bool(design.user_prose)
    has_auto = bool(design.auto_generated)
    if not has_user and not has_auto:
        return _DESIGN_COMMENT
    parts: list[str] = []
    if has_user:
        parts.append(design.user_prose)
    if has_auto:
        parts.append(_format_autogen_block(design.auto_generated))
    return "\n\n".join(parts)


def _format_scope_section(spec: ProductSpec) -> str:
    """Format the body of the ``## Scope`` section."""
    if spec.scope:
        return spec.scope
    lines: list[str] = []
    if spec.scope_include:
        lines.append("include:")
        lines.extend(f"  - {item}" for item in spec.scope_include)
    if spec.scope_exclude:
        lines.append("exclude:")
        lines.extend(f"  - {item}" for item in spec.scope_exclude)
    if not lines:
        return _SCOPE_COMMENT
    return "\n".join(lines)


def _format_behavior_section(spec: ProductSpec) -> str:
    """Format the body of the ``## Behavior`` section."""
    if spec.behavior:
        return spec.behavior
    if not spec.behavior_contracts:
        return _BEHAVIOR_COMMENT
    return "\n".join(f"- `{c.input}` → `{c.expected}`" for c in spec.behavior_contracts)


def format_spec(spec: ProductSpec) -> str:
    """Serialize a :class:`ProductSpec` to SPEC.md format.

    The inverse of :func:`duplo.spec_reader._parse_spec`.  Section
    order: Purpose, Sources, References, Architecture, Design, Scope,
    Behavior, Notes.

    Empty required sections (Purpose, Architecture) are rendered with
    the template's ``<FILL IN>`` marker.  Empty optional sections are
    rendered with the template's ``<!-- ... -->`` hint.  Filled
    sections are rendered with their content and no hint.
    """
    parts: list[str] = [_TEMPLATE_TOP_MATTER]

    # ## Purpose (required)
    purpose_body = spec.purpose.strip() if spec.purpose else _PURPOSE_FILL_IN
    parts.append(f"## Purpose\n\n{purpose_body}")

    # ## Sources
    if spec.sources:
        entries = "\n\n".join(_format_entry(e) for e in spec.sources)
        parts.append(f"## Sources\n\n{entries}")
    else:
        parts.append(f"## Sources\n\n{_SOURCES_COMMENT}")

    # ## References
    if spec.references:
        entries = "\n\n".join(_format_reference_entry(e) for e in spec.references)
        parts.append(f"## References\n\n{entries}")
    else:
        parts.append(f"## References\n\n{_REFERENCES_COMMENT}")

    # ## Architecture (required)
    arch_body = spec.architecture.strip() if spec.architecture else _ARCHITECTURE_FILL_IN
    parts.append(f"## Architecture\n\n{arch_body}")

    # ## Design
    parts.append(f"## Design\n\n{_format_design_section(spec.design)}")

    # ## Scope
    parts.append(f"## Scope\n\n{_format_scope_section(spec)}")

    # ## Behavior
    parts.append(f"## Behavior\n\n{_format_behavior_section(spec)}")

    # ## Notes
    notes_body = spec.notes if spec.notes else _NOTES_COMMENT
    parts.append(f"## Notes\n\n{notes_body}")

    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# _propose_file_role — Vision-based role inference for files in ref/
# --------------------------------------------------------------------------
#
# Per DRAFTER-design.md § "Inferring file roles via Vision".  The caller
# sets ``proposed: true`` on the resulting ReferenceEntry; this function
# never does.

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".avi"})
_TEXT_SUFFIXES = frozenset({".txt", ".md"})
_VALID_FILE_ROLES = frozenset(
    {"visual-target", "behavioral-target", "docs", "counter-example", "ignore"}
)

# Number of retry attempts after the first call fails (total attempts =
# 1 + _FILE_ROLE_RETRIES).
_FILE_ROLE_RETRIES = 2
# Base delay (seconds) for exponential backoff between retry attempts.
_FILE_ROLE_BACKOFF = 1.0

_VISION_FILE_ROLE_PROMPT = (
    "Look at this image and answer two questions:\n"
    "1. Describe the visual content (1 sentence).\n"
    "2. What role does this play in a software project? Choose ONE:\n"
    "   - visual-target: a screenshot or mockup of a UI to build\n"
    "   - behavioral-target: a recording or sequence showing how\n"
    "     an app behaves\n"
    "   - docs: a diagram, spec illustration, or reference figure\n"
    "   - counter-example: a screenshot of something to AVOID\n"
    "   - ignore: irrelevant to building the product (e.g. a logo,\n"
    "     stock photo, or unrelated image)\n"
    "\n"
    'Return JSON: {"description": "...", "role": "..."}\n'
)


def _propose_file_role(path: Path) -> tuple[str, str]:
    """Propose a ``(description, role)`` pair for a file in ``ref/``.

    For images, calls ``claude -p`` Vision with a prompt asking for a
    one-sentence description and a role from ``visual-target``,
    ``behavioral-target``, ``docs``, ``counter-example``, or ``ignore``
    (per DRAFTER-design.md § "Inferring file roles via Vision").  On LLM
    failure, retries up to ``_FILE_ROLE_RETRIES`` times with exponential
    backoff; after exhausting retries, returns ``("", "ignore")`` and
    logs a diagnostic.  On JSON parse or schema errors, returns
    ``("", "ignore")`` with a diagnostic (no retry).

    For non-image files the role is extension-based:
    ``.pdf``/``.txt``/``.md`` → ``docs``;
    ``.mp4``/``.mov``/``.webm``/``.avi`` → ``behavioral-target``;
    unknown extensions → ``ignore`` (with diagnostic).  Description is
    an empty string for non-image files.

    The caller is responsible for setting ``proposed: true`` on the
    resulting ``ReferenceEntry``; this function returns only the
    inferred content.
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return _propose_image_role(path)
    if suffix == ".pdf" or suffix in _TEXT_SUFFIXES:
        return ("", "docs")
    if suffix in _VIDEO_SUFFIXES:
        return ("", "behavioral-target")
    record_failure(
        "spec_writer:_propose_file_role",
        "llm",
        f"unknown extension {suffix!r} for {path}",
        context={"path": str(path), "suffix": suffix},
    )
    return ("", "ignore")


def _propose_image_role(path: Path) -> tuple[str, str]:
    """Call Vision to propose ``(description, role)`` for an image file.

    Retries up to ``_FILE_ROLE_RETRIES`` times on ``ClaudeCliError``
    with exponential backoff (``_FILE_ROLE_BACKOFF * 2**attempt``).  On
    final failure or on JSON parse/schema error, returns
    ``("", "ignore")`` and logs a diagnostic.
    """
    last_error: str = ""
    raw: str = ""
    for attempt in range(_FILE_ROLE_RETRIES + 1):
        try:
            raw = query_with_images(_VISION_FILE_ROLE_PROMPT, [path])
            break
        except ClaudeCliError as exc:
            last_error = str(exc)
            if attempt >= _FILE_ROLE_RETRIES:
                record_failure(
                    "spec_writer:_propose_file_role",
                    "llm",
                    f"Vision call failed after {attempt + 1} attempts for {path}: {last_error}",
                    context={"path": str(path)},
                )
                return ("", "ignore")
            time.sleep(_FILE_ROLE_BACKOFF * (2**attempt))

    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        record_failure(
            "spec_writer:_propose_file_role",
            "llm",
            f"JSON parse error for {path}: {exc}",
            context={"path": str(path), "raw": raw[:2000]},
        )
        return ("", "ignore")

    if not isinstance(data, dict):
        record_failure(
            "spec_writer:_propose_file_role",
            "llm",
            f"Vision response not a JSON object for {path}",
            context={"path": str(path), "raw": raw[:2000]},
        )
        return ("", "ignore")

    description = str(data.get("description", "")).strip()
    role = str(data.get("role", "")).strip()
    if role not in _VALID_FILE_ROLES:
        record_failure(
            "spec_writer:_propose_file_role",
            "llm",
            f"Vision returned invalid role {role!r} for {path}",
            context={"path": str(path), "role": role, "raw": raw[:2000]},
        )
        return (description, "ignore")
    return (description, role)
