"""Extract text content from PDF files."""

from __future__ import annotations

from pathlib import Path

import pypdf


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
