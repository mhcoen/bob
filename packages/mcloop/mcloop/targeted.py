"""Map changed source files to their corresponding test files."""

from __future__ import annotations

from pathlib import Path


def map_to_tests(
    changed_files: list[str],
    project_dir: Path,
) -> list[str]:
    """Return test file paths corresponding to the changed source files.

    Uses naming convention: source ``pkg/foo.py`` maps to
    ``tests/test_foo.py``.  Only returns files that actually exist.
    """
    test_files: set[str] = set()
    tests_dir = project_dir / "tests"

    for filepath in changed_files:
        p = Path(filepath)

        # Skip non-Python files
        if p.suffix != ".py":
            continue

        # Skip test files themselves, config, and metadata
        if p.name.startswith("test_") or p.name.startswith("__"):
            continue

        candidate = tests_dir / f"test_{p.name}"
        if candidate.exists():
            test_files.add(str(candidate.relative_to(project_dir)))

    return sorted(test_files)


def targeted_pytest_command(
    test_files: list[str],
) -> str:
    """Build a pytest command targeting specific test files."""
    return "pytest " + " ".join(test_files)


def is_test_command(cmd: str) -> bool:
    """Return True if cmd is a test-runner command (not a linter)."""
    first = cmd.split()[0] if cmd.split() else ""
    return first in ("pytest", "python")
