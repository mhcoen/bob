"""Map changed source files to their corresponding test files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Non-Python suffixes whose changes can alter runtime/test behavior and
# therefore must be accounted rather than silently dropped. Plain prose
# (.md, .rst) is intentionally excluded: it does not change behavior.
_BEHAVIOR_SUFFIXES = frozenset(
    {
        ".toml",
        ".cfg",
        ".ini",
        ".json",
        ".yaml",
        ".yml",
        ".j2",
        ".jinja",
        ".jinja2",
        ".html",
        ".tmpl",
        ".template",
        ".mako",
        ".csv",
        ".sql",
    }
)

# Non-Python filenames (regardless of suffix) that are behavior-relevant:
# build/config and entry-point declarations.
_BEHAVIOR_FILENAMES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "pytest.ini",
        "conftest.py",
        "MANIFEST.in",
        "Makefile",
    }
)


@dataclass(frozen=True)
class InputAccount:
    """Explicit accounting record for one changed input.

    Either ``test_files`` is non-empty (the input mapped to one or more
    tests) or ``unmapped`` is True (no test mapping could be derived).
    An input is never silently omitted: a changed input that affects
    behavior always produces an account so callers can decide to widen
    the test run rather than ship the change under a green targeted gate.
    """

    source: str
    test_files: tuple[str, ...] = ()
    k_module: str = ""  # module name used for ``-k`` style selection, if any
    unmapped: bool = False
    reason: str = ""

    @property
    def mapped(self) -> bool:
        return not self.unmapped


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(Path(path).relative_to(project_dir))
    except ValueError:
        return str(path)


def _is_behavior_relevant(p: Path) -> bool:
    """True if a change to *p* can affect program or test behavior.

    Pure docs (README.md, docs/*.rst) return False and are omitted from
    accounting entirely; everything else — Python sources, test-support
    files under ``tests/``, config/entry-point declarations, templates,
    and data files — is accounted.
    """
    if p.suffix == ".py":
        return True
    if "tests" in p.parts:
        return True
    if p.name in _BEHAVIOR_FILENAMES:
        return True
    return p.suffix in _BEHAVIOR_SUFFIXES


def _concrete_test_files(
    stem: str,
    tests_dir: Path,
    project_dir: Path,
) -> set[str]:
    """Find ``test_<stem>.py`` by the flat convention and recursively in
    any subdirectory of ``tests/``."""
    found: set[str] = set()
    flat = tests_dir / f"test_{stem}.py"
    if flat.is_file():
        found.add(_rel(flat, project_dir))
    if tests_dir.is_dir():
        for match in tests_dir.rglob(f"test_{stem}.py"):
            if match.is_file():
                found.add(_rel(match, project_dir))
    return found


def _k_referencing_tests(
    stem: str,
    tests_dir: Path,
    project_dir: Path,
) -> set[str]:
    """Test files that reference the module name as a whole word.

    Realizes pytest ``-k <module>`` style selection at the file level so
    loosely-named tests (not following ``test_<module>.py``) are still
    found when the conventional file does not exist.
    """
    if not tests_dir.is_dir():
        return set()
    pattern = re.compile(rf"\b{re.escape(stem)}\b")
    found: set[str] = set()
    for f in tests_dir.rglob("test_*.py"):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            found.add(_rel(f, project_dir))
    return found


def _sibling_tests(p: Path, project_dir: Path) -> set[str]:
    """Test files in the same directory as a test-support file (fixture,
    data file, or conftest living under ``tests/``)."""
    directory = (project_dir / p).parent
    if not directory.is_dir():
        return set()
    return {_rel(f, project_dir) for f in directory.glob("test_*.py") if f.is_file()}


def _map_one(
    p: Path,
    tests_dir: Path,
    project_dir: Path,
) -> tuple[set[str], str, str]:
    """Return ``(test_files, reason, k_module)`` for a single changed
    input. An empty ``test_files`` set means the input is unmapped and
    ``reason`` explains why."""
    name = p.name

    # A changed test file maps to itself when it still exists.
    if p.suffix == ".py" and name.startswith("test_"):
        if (project_dir / p).is_file():
            return {_rel(project_dir / p, project_dir)}, "", ""
        return set(), "changed test file does not exist", ""

    # Test-support file under tests/ (fixture, data file, conftest).
    if "tests" in p.parts:
        siblings = _sibling_tests(p, project_dir)
        if siblings:
            return siblings, "", ""
        return set(), "test-support file with no sibling tests", ""

    # Python source module: map by module name.
    if p.suffix == ".py":
        stem = p.stem
        if stem.startswith("__"):
            return set(), f"package/dunder module ({name}); no name-based mapping", ""
        files = _concrete_test_files(stem, tests_dir, project_dir)
        if files:
            return files, "", ""
        k_files = _k_referencing_tests(stem, tests_dir, project_dir)
        if k_files:
            return k_files, "", stem
        return set(), f"no test_{stem}.py and no test references module '{stem}'", ""

    # Non-Python behavior input (pyproject.toml, json/yaml data, template,
    # entry-point declaration). No name-based test mapping exists.
    label = p.suffix or name
    return set(), f"non-Python behavior input ({label}); no name-based test mapping", ""


def account_changed_inputs(
    changed_files: list[str],
    project_dir: Path,
) -> list[InputAccount]:
    """Account for every behavior-relevant changed input.

    For each changed input, return either the test files it maps to or an
    explicit unmapped marker — never a silent omission. Purely
    documentary inputs (markdown, rst) are excluded; everything that can
    affect behavior is represented so callers can widen the test run for
    unmapped inputs instead of shipping them untested.
    """
    project_dir = Path(project_dir)
    tests_dir = project_dir / "tests"
    accounts: list[InputAccount] = []
    seen: set[str] = set()

    for filepath in changed_files:
        if filepath in seen:
            continue
        seen.add(filepath)

        p = Path(filepath)
        if not _is_behavior_relevant(p):
            continue

        files, reason, k_module = _map_one(p, tests_dir, project_dir)
        if files:
            accounts.append(
                InputAccount(
                    source=filepath,
                    test_files=tuple(sorted(files)),
                    k_module=k_module,
                )
            )
        else:
            accounts.append(
                InputAccount(source=filepath, unmapped=True, reason=reason),
            )

    return accounts


def map_to_tests(
    changed_files: list[str],
    project_dir: Path,
) -> list[str]:
    """Return test file paths corresponding to the changed source files.

    Maps ``pkg/foo.py`` to ``tests/test_foo.py`` and to any
    ``tests/**/test_foo.py`` in a subdirectory; only returns files that
    actually exist. This is the flat projection of
    :func:`account_changed_inputs` over the mapped inputs; use that
    function directly when unmapped inputs must be surfaced rather than
    dropped.
    """
    project_dir = Path(project_dir)
    test_files: set[str] = set()
    for account in account_changed_inputs(changed_files, project_dir):
        test_files.update(account.test_files)
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
