"""Text-layer helpers for modifying SPEC.md content.

This module operates on raw spec text (strings), not on parsed
``ProductSpec`` objects.  It must NOT import from pipeline-stage
modules (``extractor``, ``design_extractor``, etc.).
"""

from __future__ import annotations

import re

from duplo.spec_reader import (
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
)
from duplo.url_canon import canonicalize_url

# Matches a ``## Sources`` heading (exactly level 2).
_SOURCES_HEADING = re.compile(r"^## Sources\s*$", re.MULTILINE)

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
