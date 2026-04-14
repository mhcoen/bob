"""Text-layer helpers for modifying SPEC.md content.

This module operates on raw spec text (strings), not on parsed
``ProductSpec`` objects.  It must NOT import from pipeline-stage
modules (``extractor``, ``design_extractor``, etc.).
"""

from __future__ import annotations

import re

from duplo.spec_reader import SourceEntry
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
