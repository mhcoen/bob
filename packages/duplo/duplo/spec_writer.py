"""Text-layer helpers for modifying SPEC.md content.

This module operates on raw spec text (strings), not on parsed
``ProductSpec`` objects.  It must NOT import from pipeline-stage
modules (``extractor``, ``design_extractor``, etc.).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from duplo.claude_cli import ClaudeCliError, query, query_with_images
from duplo.diagnostics import record_failure
from duplo.parsing import extract_json, strip_fences
from duplo.spec_reader import (
    BehaviorContract,
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
)
from duplo.url_canon import canonicalize_url


class SectionNotFound(Exception):
    """Raised when an append/update target section is absent from the file.

    Per DRAFTER-design.md § "Error handling".  Carries the name of the
    missing section so callers can report it without parsing the
    exception message.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"section not found: {name}")


class MalformedSpec(Exception):
    """Raised when a parse-during-modify fails on an existing SPEC.md.

    Per DRAFTER-design.md § "Error handling".  Carries the reason the
    parse failed so callers can decide whether to overwrite or bail.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class DraftingFailed(Exception):
    """Raised when the LLM call in :func:`_draft_from_inputs` fails.

    Per DRAFTER-design.md § "Error handling".  Raised after all retries
    are exhausted (transport error, JSON parse error, or non-object
    response).  Callers fall back to a template-only draft.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class DraftInputs:
    """Inputs consumed by :func:`draft_spec` / :func:`_draft_from_inputs`.

    Per DRAFTER-design.md § "API → draft_spec".  ``vision_proposals``
    maps ref/ paths to role strings already proposed by
    :func:`_propose_file_role`; ``draft_spec`` uses them to construct
    ``ReferenceEntry`` objects with ``proposed: true``.
    """

    url: str | None = None
    url_scrape: str | None = None
    description: str | None = None
    existing_ref_files: list[Path] = field(default_factory=list)
    vision_proposals: dict[Path, str] = field(default_factory=dict)


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
# those).  Non-greedy ``.+?`` (anchored by ``\s*$``) allows paths
# containing spaces — e.g. ``ref/Screen Shot.png`` — which ``\S+``
# would truncate at the first space.
_REFERENCE_ENTRY_START = re.compile(r"^-\s+(?!https?://)(.+?)\s*$", re.MULTILINE)

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


# --------------------------------------------------------------------------
# _draft_from_inputs — the only LLM call in the drafter
# --------------------------------------------------------------------------
#
# Per DRAFTER-design.md § "Drafting from inputs".  Calls claude -p with
# a structured-output prompt, parses the JSON response, and constructs
# a ProductSpec.  On LLM failure or JSON parse error, retries up to
# _DRAFT_RETRIES times with exponential backoff; after exhausting
# retries returns an empty ProductSpec and logs a diagnostic.

_DRAFT_RETRIES = 2
_DRAFT_BACKOFF = 1.0

_DRAFT_PROMPT_HEADER = (
    "You are drafting a SPEC.md for a software project.\n"
    "Given the inputs below, produce a JSON object with these fields:\n"
    "\n"
    "- purpose: one or two sentences describing what to build, or null\n"
    "  if you can't determine it from inputs.\n"
    "- architecture: language/framework/platform constraints, ONLY IF\n"
    "  the description prose explicitly states a stack, platform, or\n"
    "  language. Do NOT infer architecture from scraped product pages\n"
    "  or from product identity. Return null otherwise.\n"
    "- design: visual direction (colors, typography, aesthetic), or\n"
    "  null if not specified.\n"
    "- behavior_contracts: list of {input, expected} pairs extracted\n"
    "  from inputs, or an empty list.\n"
    "- scope_include: list of feature names the user explicitly wants,\n"
    "  or an empty list.\n"
    "- scope_exclude: list of feature names the user explicitly does\n"
    "  NOT want, or an empty list.\n"
    "\n"
    "Return ONLY the JSON object — no prose, no code fences.\n"
)


def _format_draft_inputs_for_prompt(inputs: DraftInputs) -> str:
    """Render *inputs* as the "Inputs:" block of the drafter prompt."""
    parts: list[str] = []
    if inputs.url:
        parts.append(f"URL: {inputs.url}")
    if inputs.url_scrape:
        parts.append(f"URL scrape:\n{inputs.url_scrape}")
    if inputs.description:
        parts.append(f"Description prose:\n{inputs.description}")
    if inputs.existing_ref_files:
        inventory = "\n".join(f"- {p}" for p in inputs.existing_ref_files)
        parts.append(f"ref/ file inventory:\n{inventory}")
    if not parts:
        return "(no inputs provided)"
    return "\n\n".join(parts)


def _build_draft_prompt(inputs: DraftInputs) -> str:
    """Build the full drafter prompt: header + Inputs block."""
    return f"{_DRAFT_PROMPT_HEADER}\nInputs:\n{_format_draft_inputs_for_prompt(inputs)}\n"


def _parse_behavior_contracts(raw: object) -> list[BehaviorContract]:
    """Extract ``BehaviorContract`` pairs from a JSON list element."""
    if not isinstance(raw, list):
        return []
    contracts: list[BehaviorContract] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        inp = str(item.get("input", "")).strip()
        expected = str(item.get("expected", "")).strip()
        if inp and expected:
            contracts.append(BehaviorContract(input=inp, expected=expected))
    return contracts


def _parse_string_list(raw: object) -> list[str]:
    """Extract a list of non-empty strings from a JSON list element."""
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _construct_spec_from_draft_json(data: dict) -> ProductSpec:
    """Build a :class:`ProductSpec` from the drafter's JSON response.

    Fields the LLM returned as ``null`` / missing become empty strings
    or empty lists — :func:`format_spec` emits the template ``<FILL IN>``
    marker or the optional-section comment hint on the next write.
    """
    purpose_raw = data.get("purpose")
    purpose = str(purpose_raw).strip() if purpose_raw else ""

    arch_raw = data.get("architecture")
    architecture = str(arch_raw).strip() if arch_raw else ""

    design_raw = data.get("design")
    design_prose = str(design_raw).strip() if design_raw else ""

    return ProductSpec(
        purpose=purpose,
        architecture=architecture,
        design=DesignBlock(user_prose=design_prose),
        behavior_contracts=_parse_behavior_contracts(data.get("behavior_contracts")),
        scope_include=_parse_string_list(data.get("scope_include")),
        scope_exclude=_parse_string_list(data.get("scope_exclude")),
    )


def _draft_from_inputs(inputs: DraftInputs) -> ProductSpec:
    """Draft a :class:`ProductSpec` from *inputs* via a single LLM call.

    Per DRAFTER-design.md § "Drafting from inputs".  This is the only
    place in the drafter that calls an LLM.  The function:

    1. Builds a structured-output prompt asking for a JSON object with
       ``purpose``, ``architecture``, ``design``, ``behavior_contracts``,
       ``scope_include``, and ``scope_exclude``.  ``notes`` is
       deliberately NOT in the schema — :func:`draft_spec` populates
       ``## Notes`` from the raw description prose (see design doc).
    2. Calls ``claude -p`` via :func:`duplo.claude_cli.query`, retrying
       up to ``_DRAFT_RETRIES`` times with exponential backoff on
       :class:`~duplo.claude_cli.ClaudeCliError`.
    3. Parses the JSON response (stripping code fences if present).
    4. Constructs a ``ProductSpec`` with filled fields from JSON and
       empty content where the LLM returned ``null``.

    On LLM failure after retries, or on unrecoverable JSON parse
    errors after retries, raises :class:`DraftingFailed` and logs a
    diagnostic via :func:`~duplo.diagnostics.record_failure`.  The
    caller (:func:`draft_spec`) catches the exception and falls back
    to a template-only draft.
    """
    prompt = _build_draft_prompt(inputs)

    last_error: str = ""
    for attempt in range(_DRAFT_RETRIES + 1):
        try:
            raw = query(prompt)
        except ClaudeCliError as exc:
            last_error = f"ClaudeCliError: {exc}"
            if attempt >= _DRAFT_RETRIES:
                reason = f"Draft LLM call failed after {attempt + 1} attempts: {last_error}"
                record_failure(
                    "spec_writer:_draft_from_inputs",
                    "llm",
                    reason,
                )
                raise DraftingFailed(reason) from exc
            time.sleep(_DRAFT_BACKOFF * (2**attempt))
            continue

        try:
            data = json.loads(strip_fences(raw).strip())
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"JSON parse error: {exc}"
            if attempt >= _DRAFT_RETRIES:
                reason = f"Draft JSON parse failed after {attempt + 1} attempts: {last_error}"
                record_failure(
                    "spec_writer:_draft_from_inputs",
                    "llm",
                    reason,
                    context={"raw": raw[:2000]},
                )
                raise DraftingFailed(reason) from exc
            time.sleep(_DRAFT_BACKOFF * (2**attempt))
            continue

        if not isinstance(data, dict):
            reason = "Draft response was not a JSON object"
            record_failure(
                "spec_writer:_draft_from_inputs",
                "llm",
                reason,
                context={"raw": raw[:2000]},
            )
            raise DraftingFailed(reason)

        return _construct_spec_from_draft_json(data)

    # Unreachable: every branch in the loop either returns or raises.
    raise DraftingFailed("drafter exited retry loop unexpectedly")


# --------------------------------------------------------------------------
# draft_spec — orchestrate _draft_from_inputs and format_spec
# --------------------------------------------------------------------------

_NOTES_DESCRIPTION_HEADER = "Original description provided to `duplo init`:"


# Matches HTTP(S) URLs anywhere in prose.  Trailing punctuation
# (period, comma, paren, quote) is stripped from each match at use
# time because natural-language prose commonly ends a sentence or
# parenthetical right after a URL.
_PROSE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_PROSE_URL_TRAILING_PUNCT = ".,;:!?)\"'"

# Size of the context window (in characters) supplied to
# :func:`_infer_url_role` for each prose-extracted URL.  Enough to
# capture phrases like "not like https://..." or "see also https://..."
# immediately preceding a URL.
_PROSE_URL_CONTEXT_CHARS = 80


def _extract_prose_urls(description: str) -> list[tuple[str, str]]:
    """Return ``[(url, context)]`` for every HTTP(S) URL in *description*.

    *context* is the text immediately surrounding the URL (see
    ``_PROSE_URL_CONTEXT_CHARS``).  It is the value
    :func:`_infer_url_role` consumes.  URLs keep their original form —
    canonicalization is the caller's responsibility because duplicate
    detection happens after canonicalization.  Trailing sentence
    punctuation is stripped from each URL so "see https://x.com."
    yields ``https://x.com`` rather than ``https://x.com.``.
    """
    if not description:
        return []
    results: list[tuple[str, str]] = []
    for m in _PROSE_URL_RE.finditer(description):
        url = m.group(0).rstrip(_PROSE_URL_TRAILING_PUNCT)
        start = max(0, m.start() - _PROSE_URL_CONTEXT_CHARS)
        end = min(len(description), m.end() + _PROSE_URL_CONTEXT_CHARS)
        context = description[start:end]
        results.append((url, context))
    return results


def _build_draft_spec(inputs: DraftInputs) -> ProductSpec:
    """Shared core of :func:`draft_spec` — build a ``ProductSpec`` from *inputs*.

    Split out so callers that need to inspect the drafted spec (e.g.
    ``duplo init`` deciding which sections to report as pre-filled)
    can share the same construction path rather than re-parsing the
    serialized text.  Steps 1–4 of :func:`draft_spec` live here; step
    5 (serialize) is :func:`format_spec`, applied by the wrapper.

    On :class:`DraftingFailed` from the LLM call, falls back to a
    fresh empty :class:`ProductSpec` so user-supplied inputs are
    still applied by subsequent steps.
    """
    try:
        spec = _draft_from_inputs(inputs)
    except DraftingFailed:
        spec = ProductSpec()

    if inputs.description:
        spec.notes = f"{_NOTES_DESCRIPTION_HEADER}\n\n{inputs.description}"
        # Extract URLs referenced in prose (per DRAFTER-design.md
        # § "Inferring URL roles").  Each becomes a ``proposed: true``
        # Sources entry with role inferred from surrounding context.
        existing_urls = {canonicalize_url(s.url) for s in spec.sources}
        for raw_url, context in _extract_prose_urls(inputs.description):
            canon = canonicalize_url(raw_url)
            if canon in existing_urls:
                continue
            role = _infer_url_role(context)
            # Counter-example scrape coercion: same rule as the parser
            # and ``append_sources`` — counter-examples must not be
            # scraped, so force ``scrape: none`` at write time.
            scrape = "none" if role == "counter-example" else "deep"
            spec.sources.append(
                SourceEntry(
                    url=canon,
                    role=role,
                    scrape=scrape,
                    proposed=True,
                )
            )
            existing_urls.add(canon)

    if inputs.url:
        canon_user_url = canonicalize_url(inputs.url)
        # If the user's explicit URL also appeared in prose, drop the
        # prose-derived (proposed) copy before prepending the explicit
        # entry so Sources stays single-entry-per-URL.
        spec.sources = [s for s in spec.sources if canonicalize_url(s.url) != canon_user_url]
        spec.sources.insert(
            0,
            SourceEntry(
                url=inputs.url,
                role="product-reference",
                scrape="deep",
            ),
        )

    for path in inputs.existing_ref_files:
        role = inputs.vision_proposals.get(path, "")
        roles = [role] if role else []
        spec.references.append(ReferenceEntry(path=path, roles=roles, proposed=True))

    return spec


def draft_spec(inputs: DraftInputs) -> str:
    """Draft a fresh SPEC.md from *inputs* and return the serialized text.

    Per DRAFTER-design.md § ``draft_spec``.  Orchestrates:

    1. :func:`_draft_from_inputs` to turn *inputs* into a
       :class:`ProductSpec` via the drafter LLM call.
    2. If ``inputs.description`` is set, copy the verbatim prose into
       ``spec.notes`` under a labeled header.  The LLM never writes
       ``## Notes``; this step guarantees the user's original words are
       preserved even if the structured extraction missed nuances.
       URLs mentioned in the prose become ``proposed: true`` Sources
       entries with roles inferred via :func:`_infer_url_role`.
    3. Prepend a :class:`SourceEntry` for ``inputs.url`` (if any) with
       ``role="product-reference"`` and ``scrape="deep"``.  The user
       provided the URL explicitly, so no ``proposed`` / ``discovered``
       flag is set.
    4. Append a :class:`ReferenceEntry` for each file in
       ``inputs.existing_ref_files`` with ``proposed=True`` and the
       role from ``inputs.vision_proposals``.
    5. Serialize the result with :func:`format_spec`.

    The caller (``duplo init``) writes the returned string to SPEC.md.

    On :class:`DraftingFailed` from step 1, falls back to a fresh empty
    :class:`ProductSpec` (template markers for required sections) and
    still applies steps 2–4 so user-supplied inputs are preserved.
    """
    return format_spec(_build_draft_spec(inputs))
