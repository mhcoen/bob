"""Scan the current directory for reference materials."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from duplo.diagnostics import record_failure
from duplo.spec_reader import ReferenceEntry

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}
_PDF_EXTS = {".pdf"}
_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".text"}

_SKIP_DIRS = {
    ".duplo",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".build",
    "logs",
    ".mcloop",
    ".claude",
}

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
class ScanResult:
    """Results of scanning a project directory for reference materials."""

    images: list[Path] = field(default_factory=list)
    videos: list[Path] = field(default_factory=list)
    pdfs: list[Path] = field(default_factory=list)
    text_files: list[Path] = field(default_factory=list)
    roles: dict[Path, list[str]] = field(default_factory=dict)


def scan_files(
    paths: list[Path],
    references: list[ReferenceEntry] | None = None,
) -> ScanResult:
    """Classify specific files into a :class:`ScanResult`.

    Works like :func:`scan_directory` but operates on an explicit list
    of file paths instead of walking a directory.  Used by subsequent
    runs to analyze only new or changed files.

    When *references* is provided, each file's path is checked against
    the parsed ``## References`` entries to determine its declared
    roles.  Matched roles are stored in ``result.roles`` keyed by the
    original path.
    """
    result = ScanResult()
    ref_index = _build_reference_index(references) if references else {}
    for path in paths:
        if not path.is_file():
            continue
        _classify_file(path, result)
        roles = _lookup_roles(path, ref_index)
        if roles:
            result.roles[path] = roles
    return result


def scan_directory(ref_dir: Path | str = ".") -> ScanResult:
    """Scan *ref_dir* for reference materials.

    Finds images, videos, PDFs, and text/markdown files.  Skips
    ``.duplo/``, ``.git/``, and other non-project directories.  URLs
    are sourced from SPEC.md's ``## Sources`` section, not extracted
    from file contents.

    Returns a :class:`ScanResult` with categorised file lists.
    """
    root = Path(ref_dir).resolve()
    result = ScanResult()
    if not root.is_dir():
        return result

    for path in sorted(root.iterdir()):
        if path.is_dir():
            continue
        _classify_file(path, result)

    return result


def _classify_file(path: Path, result: ScanResult) -> None:
    """Classify a single file and add it to the appropriate list."""
    suffix = path.suffix.lower()

    if suffix in _IGNORE_EXTS:
        return

    if suffix in _IMAGE_EXTS:
        result.images.append(path)
        return

    if suffix in _VIDEO_EXTS:
        result.videos.append(path)
        return

    if suffix in _PDF_EXTS:
        result.pdfs.append(path)
        return

    if suffix in _TEXT_EXTS:
        result.text_files.append(path)
        return


def check_unlisted_ref_files(
    scan: ScanResult,
    references: list[ReferenceEntry],
    *,
    ref_dir: Path | str = "ref",
    errors_path: Path | str = ".duplo/errors.jsonl",
) -> list[Path]:
    """Emit diagnostics for files in ``ref/`` not listed in ``## References``.

    Compares all files found by :func:`scan_directory` against the paths
    declared in ``## References``.  Any file present on disk but absent
    from the reference list gets a non-fatal diagnostic via
    :func:`~duplo.diagnostics.record_failure`.

    Returns the list of unlisted paths (for testing convenience).
    """
    ref_dir = Path(ref_dir)
    # Build a set of declared paths (as resolved absolute paths).
    declared: set[Path] = set()
    for entry in references:
        declared.add((ref_dir.parent / entry.path).resolve())

    # Collect all scanned file paths.
    all_files: list[Path] = scan.images + scan.videos + scan.pdfs + scan.text_files

    unlisted: list[Path] = []
    for file_path in all_files:
        resolved = file_path.resolve()
        if resolved not in declared:
            # Build a relative path for the message.
            try:
                rel = file_path.relative_to(ref_dir.parent)
            except ValueError:
                rel = file_path
            record_failure(
                "scanner",
                "io",
                f"file in ref/ has no entry in ## References; will be ignored: {rel}",
                errors_path=errors_path,
            )
            unlisted.append(file_path)
    return unlisted


def _build_reference_index(
    references: list[ReferenceEntry],
) -> dict[str, list[str]]:
    """Build a lookup from normalised path strings to declared roles.

    Keys are the ``str(entry.path)`` values from ``## References``
    (e.g. ``"ref/shot.png"``).  This allows fast matching against
    file paths encountered during scanning.
    """
    index: dict[str, list[str]] = {}
    for entry in references:
        index[str(entry.path)] = list(entry.roles)
    return index


def _lookup_roles(
    path: Path,
    ref_index: dict[str, list[str]],
) -> list[str]:
    """Return declared roles for *path*, or an empty list if unlisted.

    Tries matching by the full ``str(path)`` first (covers paths
    already relative, e.g. ``ref/shot.png``).  Then falls back to
    matching by filename alone against entries whose parent is
    ``ref`` — this handles the case where *path* is absolute or
    differently anchored but names the same file.
    """
    key = str(path)
    if key in ref_index:
        return ref_index[key]
    # Fallback: match by filename within ref/ entries.
    name = path.name
    for ref_path, roles in ref_index.items():
        parts = ref_path.split("/")
        if len(parts) >= 2 and parts[0] == "ref" and parts[-1] == name:
            return roles
    return []
