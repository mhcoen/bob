"""Extract text content from PDF and other docs-role files."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pypdf

if TYPE_CHECKING:
    from duplo.spec_reader import ReferenceEntry

_TEXT_EXTENSIONS = {".txt", ".md"}


def docs_text_extractor(entries: list[ReferenceEntry]) -> str:
    """Extract text from docs-role reference entries, routed by extension.

    - ``.pdf`` files go through ``extract_pdf_text``.
    - ``.txt`` and ``.md`` files are read directly.
    - Other extensions are silently skipped.

    Returns combined text with a filename header per file.
    """
    pdfs: list[Path] = []
    text_parts: list[str] = []

    for entry in entries:
        path = entry.path
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            pdfs.append(path)
        elif suffix in _TEXT_EXTENSIONS:
            text = _read_text_file(path)
            if text:
                text_parts.append(f"=== {path.name} ===\n{text}")

    pdf_text = extract_pdf_text(pdfs)
    all_parts = []
    if pdf_text:
        all_parts.append(pdf_text)
    all_parts.extend(text_parts)
    return "\n\n".join(all_parts)


def _read_text_file(path: Path) -> str:
    """Read a text file, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def extract_pdf_text(paths: list[Path]) -> str:
    """Extract and concatenate text from a list of PDF files.

    Skips files that cannot be read. Returns the combined text from
    all pages of all PDFs, separated by newlines. Each PDF's content
    is preceded by a header line with the filename.
    """
    parts: list[str] = []
    for path in paths:
        text = _extract_single(path)
        if text:
            parts.append(f"=== {path.name} ===\n{text}")
    return "\n\n".join(parts)


def _extract_single(path: Path) -> str:
    """Extract text from a single PDF file.

    Returns empty string if the file cannot be read or contains no text.
    """
    try:
        reader = pypdf.PdfReader(path)
    except Exception:
        return ""
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)
