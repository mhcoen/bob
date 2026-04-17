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

        # Skip config and metadata
        if p.name.startswith("__"):
            continue

        # Include changed test files directly
        if p.name.startswith("test_"):
            candidate = project_dir / p
            if candidate.exists():
                test_files.add(str(p))
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
    parts = cmd.split()
    if not parts:
        return False
    if parts[0] == "pytest":
        return True
    if parts[0] == "python" and len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest":
        return True
    return False


def is_scoped_python_linter(cmd: str) -> bool:
    """Return True if cmd is a ruff check/format invocation we can scope.

    Recognizes the two repo-wide forms mcloop itself generates via
    auto-detection:
      - ``ruff check .``
      - ``ruff format --check .``

    Non-default invocations (with extra flags, glob patterns, or config
    overrides) are left alone to avoid changing behavior the user
    configured deliberately.
    """
    parts = cmd.split()
    if parts == ["ruff", "check", "."]:
        return True
    if parts == ["ruff", "format", "--check", "."]:
        return True
    return False


def targeted_linter_command(
    cmd: str,
    python_files: list[str],
) -> str:
    """Rewrite a scoped ruff command to target specific Python files.

    Replaces the trailing ``.`` (whole repo) with a space-joined list
    of file paths. Caller must have already filtered ``python_files``
    to .py files that actually exist.
    """
    parts = cmd.split()
    # Drop the trailing "." and append the explicit file list.
    assert parts[-1] == ".", f"unexpected linter cmd shape: {cmd!r}"
    return " ".join(parts[:-1] + python_files)
