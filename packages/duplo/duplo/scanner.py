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

# Minimum image size (bytes) to consider relevant (skip tiny icons/favicons).
_MIN_IMAGE_BYTES = 1024

# Files that are clearly not reference material.
_IGNORE_EXTS = {
    ".pyc",
    ".pyo",
    ".o",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".class",
    ".jar",
    ".war",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".rar",
    ".whl",
    ".egg",
}


@dataclass
class FileRelevance:
    """Relevance assessment for a single file."""

    path: Path
    category: str  # "image", "pdf", "text", "url_source"
    relevant: bool = True
    reason: str = ""


@dataclass
class ScanResult:
    """Results of scanning a project directory for reference materials."""

    images: list[Path] = field(default_factory=list)
    pdfs: list[Path] = field(default_factory=list)
    text_files: list[Path] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    relevance: list[FileRelevance] = field(default_factory=list)


def scan_directory(directory: Path | str = ".") -> ScanResult:
    """Scan *directory* for reference materials.

    Finds images, PDFs, and text/markdown files.  Extracts URLs from
    any file that can be read as text.  Skips ``.duplo/``, ``.git/``,
    and other non-project directories.

    Each found file is assessed for relevance (e.g. tiny images are
    flagged as likely irrelevant).

    Returns a :class:`ScanResult` with categorised file lists,
    extracted URLs (deduplicated, order-preserved), and per-file
    relevance assessments.
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

    if suffix in _IGNORE_EXTS:
        return

    if suffix in _IMAGE_EXTS:
        result.images.append(path)
        _assess_image(path, result)
        return

    if suffix in _PDF_EXTS:
        result.pdfs.append(path)
        _assess_pdf(path, result)
        return

    if suffix in _TEXT_EXTS:
        result.text_files.append(path)
        _assess_text(path, result)
        _extract_urls_from_file(path, result, seen_urls)
        return

    # Any other file: try to extract URLs from it.
    _extract_urls_from_file(path, result, seen_urls)


def _assess_image(path: Path, result: ScanResult) -> None:
    """Assess relevance of an image file."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < _MIN_IMAGE_BYTES:
        result.relevance.append(
            FileRelevance(
                path=path,
                category="image",
                relevant=False,
                reason=f"very small ({size} bytes), likely icon or favicon",
            )
        )
    else:
        result.relevance.append(FileRelevance(path=path, category="image", relevant=True))


def _assess_pdf(path: Path, result: ScanResult) -> None:
    """Assess relevance of a PDF file."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size == 0:
        result.relevance.append(
            FileRelevance(
                path=path,
                category="pdf",
                relevant=False,
                reason="empty file",
            )
        )
    else:
        result.relevance.append(FileRelevance(path=path, category="pdf", relevant=True))


def _assess_text(path: Path, result: ScanResult) -> None:
    """Assess relevance of a text file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    stripped = text.strip()
    if not stripped:
        result.relevance.append(
            FileRelevance(
                path=path,
                category="text",
                relevant=False,
                reason="empty or whitespace-only",
            )
        )
    elif len(stripped) < 20:
        result.relevance.append(
            FileRelevance(
                path=path,
                category="text",
                relevant=False,
                reason=f"very short ({len(stripped)} chars)",
            )
        )
    else:
        result.relevance.append(FileRelevance(path=path, category="text", relevant=True))


def _extract_urls_from_file(
    path: Path,
    result: ScanResult,
    seen_urls: set[str],
) -> None:
    """Extract HTTP(S) URLs from a file.

    Attempts to read any file as UTF-8 text.  Binary files that
    fail to decode are silently skipped.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return
    found_any = False
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        found_any = True
        if url not in seen_urls:
            seen_urls.add(url)
            result.urls.append(url)
    # Track non-text files that contributed URLs.
    suffix = path.suffix.lower()
    if found_any and suffix not in _TEXT_EXTS:
        result.relevance.append(
            FileRelevance(
                path=path,
                category="url_source",
                relevant=True,
                reason="contains URLs",
            )
        )
