"""Check whether CLAUDE.md was updated alongside source file changes."""

from __future__ import annotations

from pathlib import Path

_SOURCE_EXTENSIONS = frozenset(
    (
        ".py",
        ".swift",
        ".rs",
        ".go",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".rb",
        ".sh",
    )
)

_SOURCE_DIRS = ("src/", "lib/", "package/")


def _is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file."""
    name = Path(path).name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.go"):
        return True
    return False


def _is_source_file(path: str) -> bool:
    """Return True if *path* is a non-test source file."""
    if _is_test_file(path):
        return False
    suffix = Path(path).suffix
    if suffix in _SOURCE_EXTENSIONS:
        return True
    for prefix in _SOURCE_DIRS:
        if path.startswith(prefix):
            return True
    return False


def check_claude_md_freshness(
    changed_files: list[str],
    project_dir: Path,  # noqa: ARG001
) -> bool:
    """Return False if source files changed but CLAUDE.md did not.

    *changed_files* should be a list of repo-relative paths (e.g. from
    ``git diff --name-only``).  *project_dir* is accepted for future use
    but currently unused.

    Returns True when no source files were touched **or** when CLAUDE.md
    is among the changed files.
    """
    has_source = False
    has_claude_md = False

    for path in changed_files:
        if Path(path).name == "CLAUDE.md":
            has_claude_md = True
        if _is_source_file(path):
            has_source = True

    if not has_source:
        return True
    return has_claude_md
