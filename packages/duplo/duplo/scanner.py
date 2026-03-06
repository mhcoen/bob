"""Scan the current directory for reference materials."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_PDF_EXTS = {".pdf"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".text"}

_SKIP_DIRS = {".duplo", ".git", "__pycache__", "node_modules", ".venv", "venv"}

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass
class ScanResult:
    """Results of scanning a project directory for reference materials."""

    images: list[Path] = field(default_factory=list)
    pdfs: list[Path] = field(default_factory=list)
    text_files: list[Path] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


def scan_directory(directory: Path | str = ".") -> ScanResult:
    """Scan *directory* for reference materials.

    Finds images, PDFs, and text/markdown files.  Extracts URLs from
    text files.  Skips ``.duplo/``, ``.git/``, and other non-project
    directories.

    Returns a :class:`ScanResult` with categorised file lists and
    extracted URLs (deduplicated, order-preserved).
    """
    root = Path(directory).resolve()
    result = ScanResult()
    seen_urls: set[str] = set()

    for path in sorted(root.iterdir()):
        if path.name.startswith(".") and path.is_dir():
            continue
        if path.is_dir() and path.name in _SKIP_DIRS:
            continue
        if path.is_dir():
            continue
        _classify_file(path, result, seen_urls)

    return result


def _classify_file(
    path: Path,
    result: ScanResult,
    seen_urls: set[str],
) -> None:
    """Classify a single file and add it to the appropriate list."""
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTS:
        result.images.append(path)
    elif suffix in _PDF_EXTS:
        result.pdfs.append(path)
    elif suffix in _TEXT_EXTS:
        result.text_files.append(path)
        _extract_urls_from_file(path, result, seen_urls)


def _extract_urls_from_file(
    path: Path,
    result: ScanResult,
    seen_urls: set[str],
) -> None:
    """Extract HTTP(S) URLs from a text file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url not in seen_urls:
            seen_urls.add(url)
            result.urls.append(url)
