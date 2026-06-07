"""Coverage-proven verification for unmapped behavioral Python changes.

This is the *primary fallback* the gate reaches for when a behavior-
relevant Python change has no named test partner (no ``test_<name>.py``
and no test that references the module by name). Rather than expanding to
the full suite -- which could pass vacuously without ever exercising the
change -- the gate runs coverage over a deterministic, *scoped* candidate
set of tests and asserts that the changed source lines were actually
executed.

The candidate set is the union of:

  * the change's mapped test nodes (empty for a genuinely unmapped
    change), and
  * dependent tests discovered by a transitive first-party import walk --
    every test file that imports the changed module directly or reaches
    it through a chain of project imports.

If that scoped set is empty the change cannot be proven and the gate
fails closed (or requires an explicit waiver); it is NEVER widened to the
whole suite. A change exercised by an integration/dependent test passes
even with no namesake test; a change exercised by nothing fails.

Non-code inputs that carry no executable logic -- dependency manifests
(pyproject.toml), tool config (ruff/mypy/pytest), requirement/lock files,
and plain data/docs -- are recognized as a no-test-needed change class
(see :func:`mcloop.change_class.is_no_test_needed_input`) and pass this
gate directly: there is no source line to cover, so demanding a test or a
waiver would be busywork. Logic-bearing non-Python inputs (templates,
SQL, build scripts) still have no executable coverage lines here and
require a named-test mapping or the explicit waiver/hard-failure path
handled by the caller.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# A unified-diff hunk header: ``@@ -a,b +c,d @@``. Group 1 is the new-side
# start line; group 2 (optional) the new-side length.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class _ModuleNameCollision(Exception):
    """Two distinct project files resolve to the same dotted module name.

    e.g. ``pkg/lint.py`` and ``pkg/lint/__init__.py`` both map to
    ``pkg.lint``. Such a pair makes the import-graph module->file map
    ambiguous, so dependent-test discovery cannot be trusted.
    """

    def __init__(self, name: str, paths: list[str]):
        self.module_name = name
        self.paths = paths
        super().__init__(f"{name}: {', '.join(paths)}")


@dataclass(frozen=True)
class CoverageVerdict:
    """Outcome of attempting to prove a change is exercised by tests."""

    proven: bool
    reason: str
    candidate_nodes: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Coverage-exempt Python file class (interface / re-export only).
# --------------------------------------------------------------------------
#
# Some changed ``.py`` files carry no executable application logic and so
# have no coverage line a test could meaningfully execute:
#
#   * pure *interface* modules -- every class is a ``typing.Protocol``
#     subclass or an ABC whose method bodies are only abstract stubs
#     (``...`` / ``pass`` / a docstring / ``raise NotImplementedError``),
#     and
#   * pure *re-export* modules -- the module body is solely imports and an
#     ``__all__`` re-export list, with no executable statements.
#
# A change confined to such a file cannot be proven by coverage (there is
# nothing to cover) and forcing a mapped test or a waiver for it is
# busywork. Detection is a conservative AST check, consistent in spirit
# with :mod:`mcloop.change_class`: anything carrying real executable logic
# (a function definition with a body, a module-level call, an ``if`` block,
# a concrete class) makes the file NON-exempt, so the gate fails closed.

# Bare-class-name bases that mark a class as an interface (Protocol/ABC).
_INTERFACE_BASE_NAMES = frozenset({"Protocol", "ABC"})
_INTERFACE_METACLASS_NAMES = frozenset({"ABCMeta"})


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    """True iff *stmt* is a bare string-literal expression (a docstring)."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_ellipsis_stmt(stmt: ast.stmt) -> bool:
    """True iff *stmt* is a bare ``...`` expression."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def _is_not_implemented_raise(stmt: ast.stmt) -> bool:
    """True iff *stmt* is ``raise NotImplementedError`` (with or without call)."""
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    exc = stmt.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"


def _is_trivial_stub_body(body: list[ast.stmt]) -> bool:
    """True iff every statement in *body* is an abstract/no-op stub.

    A stub body contains only docstrings, ``pass``, ``...``, and
    ``raise NotImplementedError`` -- the conventional bodies of abstract
    or Protocol methods. Any real statement makes the body non-trivial.
    """
    return all(
        _is_docstring_stmt(s)
        or isinstance(s, ast.Pass)
        or _is_ellipsis_stmt(s)
        or _is_not_implemented_raise(s)
        for s in body
    )


def _base_simple_name(node: ast.expr) -> str | None:
    """Return the trailing identifier of a base/metaclass expression.

    Unwraps a generic subscript (``Protocol[T]`` -> ``Protocol``) and
    resolves both bare names (``Protocol``) and dotted attributes
    (``typing.Protocol`` -> ``Protocol``).
    """
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_interface_class(cls: ast.ClassDef) -> bool:
    """True iff *cls* is a Protocol/ABC interface with only stub members.

    The class must derive from ``Protocol``/``typing.Protocol`` or
    ``ABC``/``abc.ABC`` (or declare ``metaclass=ABCMeta``), and every
    statement in its body must be a docstring, ``pass``, ``...``, a bare
    attribute annotation (``name: str``), or a method whose body is an
    abstract/no-op stub. A concrete method body or any other class-level
    statement disqualifies it.
    """
    is_interface = any(_base_simple_name(base) in _INTERFACE_BASE_NAMES for base in cls.bases)
    if not is_interface:
        for kw in cls.keywords:
            if kw.arg == "metaclass" and _base_simple_name(kw.value) in _INTERFACE_METACLASS_NAMES:
                is_interface = True
                break
    if not is_interface:
        return False

    for stmt in cls.body:
        if _is_docstring_stmt(stmt) or isinstance(stmt, ast.Pass) or _is_ellipsis_stmt(stmt):
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_trivial_stub_body(stmt.body):
                continue
            return False
        if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
            # A bare type annotation (``name: str``) is a Protocol member
            # declaration, not executable.
            continue
        return False
    return True


def _is_all_reexport(stmt: ast.stmt) -> bool:
    """True iff *stmt* is an ``__all__ = [...]`` re-export assignment."""
    if isinstance(stmt, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__"
    return False


def is_coverage_exempt_python(source: str) -> bool:
    """True iff *source* is an interface-only or re-export-only module.

    A module is coverage-exempt when every top-level statement is inert:
    a docstring, an import, an ``__all__`` re-export assignment, or a
    Protocol/ABC interface class whose members are abstract stubs. Such a
    file has no executable line a test could cover. The check is
    conservative -- a module-level function definition, call, conditional,
    concrete class, or any unparseable input returns False so the gate
    keeps requiring a mapped test or waiver.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    saw_exempt_content = False
    for stmt in tree.body:
        if _is_docstring_stmt(stmt):
            continue
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            saw_exempt_content = True
            continue
        if _is_all_reexport(stmt):
            saw_exempt_content = True
            continue
        if isinstance(stmt, ast.ClassDef):
            if _is_interface_class(stmt):
                saw_exempt_content = True
                continue
            return False
        # Any other top-level statement carries executable logic.
        return False
    return saw_exempt_content


# --------------------------------------------------------------------------
# Changed-line discovery (against the task's pre-edit baseline).
# --------------------------------------------------------------------------


def _parse_diff_new_lines(diff_text: str) -> set[int]:
    """Return the set of new-side line numbers added/changed in *diff_text*.

    Walks the unified diff tracking the new-file line counter declared by
    each hunk header. ``+`` lines are the added/modified lines we want;
    context lines advance the counter; ``-`` lines (deletions) do not
    consume a new-side line. ``+++``/``---`` file headers and the
    ``\\ No newline`` marker are ignored.
    """
    changed: set[int] = set()
    new_lineno = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            new_lineno = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("\\"):
            # "\ No newline at end of file" -- not a content line.
            continue
        if raw.startswith("+"):
            changed.add(new_lineno)
            new_lineno += 1
        elif raw.startswith("-"):
            # Deletion: the new file does not contain this line.
            continue
        else:
            # Context line (leading space) advances the new-side counter.
            new_lineno += 1
    return changed


def changed_new_lines(
    project_dir: Path,
    baseline_sha: str,
    src: str,
) -> set[int] | None:
    """Return new-side changed line numbers for *src* vs *baseline_sha*.

    Returns ``None`` (fail-closed) when the baseline is empty or the git
    diff cannot be produced. An empty set means the diff resolved but no
    added/changed lines were found on the new side.

    ``git diff`` against a committed baseline omits *untracked* files, so a
    brand-new file the editor just created would otherwise read as zero
    changed lines and fail the gate spuriously. To stay consistent with
    mcloop's file-level change detectors (``_changed_files`` /
    ``_changed_files_since``, which are untracked-aware via
    ``git ls-files --others``), an empty diff is re-checked: if *src* is
    untracked it is modeled as a new file whose every physical line (1..N)
    is added.
    """
    if not baseline_sha:
        return None
    try:
        result = subprocess.run(
            ["git", "diff", baseline_sha, "--", src],
            cwd=Path(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parsed = _parse_diff_new_lines(result.stdout)
    if parsed:
        return parsed
    # Empty diff: git diff against a committed baseline omits UNTRACKED files.
    # Mirror _changed_files / _changed_files_since (untracked-aware via
    # `git ls-files --others`) so the line-level prover agrees with the
    # file-level detectors. A brand-new untracked file is modeled as a
    # new-file diff: every physical line 1..N is an added line. Downstream
    # the change&executed intersection means non-executable lines
    # (blank/docstring/import) simply won't be in the intersection, so
    # returning all lines is safe and matches how git numbers a real
    # new-file diff.
    if _is_untracked(project_dir, src):
        return _all_file_lines(project_dir, src)
    return parsed  # genuinely tracked-but-unchanged -> empty set (unchanged)


def _is_untracked(project_dir: Path, src: str) -> bool:
    """True if *src* is an untracked, non-ignored file under *project_dir*.

    Mirrors the ``git ls-files --others --exclude-standard`` pattern the
    git_ops change detectors use; *src* is project-relative and ``cwd`` is
    the project, so no ``--relative`` is needed.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", src],
            cwd=Path(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _all_file_lines(project_dir: Path, src: str) -> set[int]:
    """Every physical line number (1..N) of *src*; empty set if unreadable.

    An empty file yields an empty set, so an untracked empty file still
    fails the gate ("no added/changed lines") -- there is nothing to prove.
    """
    path = Path(project_dir) / src
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    n = len(text.splitlines())
    return set(range(1, n + 1))


# --------------------------------------------------------------------------
# Dependent-test discovery (transitive first-party import graph).
# --------------------------------------------------------------------------


def _module_dotted(rel_path: str) -> str:
    """Dotted module name for a project-relative ``.py`` path.

    ``pkg/widget.py`` -> ``pkg.widget``; ``pkg/__init__.py`` -> ``pkg``.
    """
    p = Path(rel_path)
    posix = p.with_suffix("").as_posix()
    if posix.endswith("/__init__"):
        posix = posix[: -len("/__init__")]
    return posix.replace("/", ".")


def _iter_py_files(project_dir: Path) -> list[Path]:
    """All project ``.py`` files, skipping hidden and virtualenv trees."""
    out: list[Path] = []
    for f in project_dir.rglob("*.py"):
        parts = f.relative_to(project_dir).parts
        if any(part.startswith(".") or part == ".venv" for part in parts):
            continue
        out.append(f)
    return out


def _build_module_index(py_files: list[Path], project_dir: Path) -> dict[str, Path]:
    """Map dotted-name -> Path over *py_files*.

    Raise :class:`_ModuleNameCollision` if two distinct files share a
    dotted name (e.g. ``pkg/lint.py`` and ``pkg/lint/__init__.py`` both
    resolve to ``pkg.lint``). Collision detection is deterministic so the
    failure no longer depends on rglob iteration order.
    """
    index: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}
    for f in py_files:
        rel = f.relative_to(project_dir).as_posix()
        name = _module_dotted(rel)
        if name in index and index[name] != f:
            collisions.setdefault(name, [index[name]]).append(f)
        else:
            index[name] = f
    if collisions:
        # Deterministic ordering -- the whole point is killing rglob-order
        # dependence; report the lexicographically first colliding name.
        first = sorted(collisions)[0]
        paths = sorted(str(p) for p in collisions[first])
        raise _ModuleNameCollision(name=first, paths=paths)
    return index


def _resolve_imports(
    file_path: Path,
    file_module: str,
    known_modules: set[str],
) -> set[str]:
    """Return the set of *known* project modules imported by *file_path*."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        return set()

    found: set[str] = set()
    pkg_parts = file_module.split(".")[:-1]  # the file's containing package

    def _add_known(name: str) -> None:
        if name in known_modules:
            found.add(name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_known(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import: resolve against this file's package.
                base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                base = ".".join(base_parts)
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            module = module.strip(".")
            if not module:
                continue
            # ``from pkg import widget`` may name a submodule or a symbol.
            for alias in node.names:
                _add_known(f"{module}.{alias.name}")
            # ``from pkg.widget import thing`` -- the module itself is the dep.
            _add_known(module)
    return found


def dependent_test_files(project_dir: Path, src: str) -> list[str]:
    """Return test files that transitively import the changed module.

    Builds a first-party import graph over all project ``.py`` files and
    selects every ``tests/**/test_*.py`` whose transitive import closure
    contains the changed module. This finds integration/dependent tests
    that exercise the change without naming it -- the case name-based
    mapping misses -- while staying strictly scoped (a test that never
    reaches the module is never selected; the suite is never widened
    wholesale).
    """
    project_dir = Path(project_dir)
    target = _module_dotted(src)

    py_files = _iter_py_files(project_dir)
    module_to_file = _build_module_index(py_files, project_dir)
    known = set(module_to_file)
    if target not in known:
        # The changed module is not a recognizable project module; no
        # graph-based discovery is possible.
        return []

    # adjacency: module -> set of project modules it imports.
    adjacency: dict[str, set[str]] = {}
    for module, f in module_to_file.items():
        adjacency[module] = _resolve_imports(f, module, known)

    def _reaches_target(start_modules: set[str]) -> bool:
        seen: set[str] = set()
        stack = list(start_modules)
        while stack:
            mod = stack.pop()
            if mod == target:
                return True
            if mod in seen:
                continue
            seen.add(mod)
            stack.extend(adjacency.get(mod, set()))
        return False

    tests_dir = project_dir / "tests"
    selected: list[str] = []
    for module, f in module_to_file.items():
        try:
            f.relative_to(tests_dir)
        except ValueError:
            continue
        if not f.name.startswith("test_"):
            continue
        if _reaches_target(adjacency.get(module, set())):
            selected.append(f.relative_to(project_dir).as_posix())
    return sorted(selected)


# --------------------------------------------------------------------------
# Scoped coverage run.
# --------------------------------------------------------------------------


def _parse_coverage_json(json_text: str, src: str, base: Path) -> set[int]:
    """Return executed line numbers recorded for *src* in a coverage JSON."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return set()
    files = data.get("files", {})
    if not isinstance(files, dict):
        return set()
    want = (base / src).resolve()
    src_posix = Path(src).as_posix()
    for key, info in files.items():
        kp = Path(key)
        kp_abs = kp.resolve() if kp.is_absolute() else (base / kp).resolve()
        if kp_abs == want or kp.as_posix().endswith(src_posix):
            executed = info.get("executed_lines", []) if isinstance(info, dict) else []
            return {int(n) for n in executed}
    return set()


def _run_coverage(
    project_dir: Path,
    test_nodes: list[str],
    src: str,
    timeout: int,
) -> tuple[set[int] | None, str]:
    """Run scoped pytest with coverage; return (executed_lines, reason).

    Returns ``(None, reason)`` when the run produced no valid passing
    signal (tests failed, nothing collected, all skipped/deselected, or
    an unparseable summary) or no JSON report -- the change must not be
    treated as proven on a vacuous or failing run. Otherwise returns the
    set of executed line numbers for *src*.
    """
    from mcloop.pytest_signal import pytest_signal_verdict
    from mcloop.targeted import _absolute_node, _pytest_prefix_parts

    base = Path(project_dir).resolve()
    cov_target = _module_dotted(src)
    with tempfile.TemporaryDirectory() as td:
        json_path = Path(td) / "coverage.json"
        parts = _pytest_prefix_parts(base)
        parts += [_absolute_node(base, n) for n in test_nodes]
        parts += [
            f"--cov={cov_target}",
            f"--cov-report=json:{json_path}",
        ]
        try:
            result = subprocess.run(
                parts,
                cwd=base,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"coverage run timed out after {timeout}s"
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"coverage run failed to launch: {exc}"

        valid, reason = pytest_signal_verdict(result.stdout, result.stderr, result.returncode)
        if not valid:
            return None, f"scoped coverage run produced no valid passing signal: {reason}"
        if result.returncode != 0:
            return None, "scoped coverage run: tests did not pass"
        if not json_path.exists():
            return None, "scoped coverage run produced no JSON report"
        executed = _parse_coverage_json(json_path.read_text(encoding="utf-8"), src, base)
        return executed, ""


def verify_change_covered(
    project_dir: str | Path,
    baseline_sha: str,
    src: str,
    mapped_test_files: list[str],
    *,
    timeout: int = 300,
) -> CoverageVerdict:
    """Attempt to prove *src*'s changed lines are executed by scoped tests.

    Returns a :class:`CoverageVerdict`. ``proven`` is True only when the
    scoped coverage run passes and at least one changed line of *src* was
    executed by a candidate test. A no-test-needed non-code input
    (manifest/config/lock/data) is proven exempt with no coverage run. A
    logic-bearing non-Python input, an unresolvable baseline, an empty
    candidate set, a failing coverage run, and a run that never touches the
    changed lines all return ``proven=False`` with a distinguishing reason.
    """
    from mcloop.change_class import is_no_test_needed_input

    project_dir = Path(project_dir)

    if not src.endswith(".py"):
        if is_no_test_needed_input(src):
            return CoverageVerdict(
                True,
                "non-code input (dependency manifest, tool config, lock, "
                "or data file) carries no executable logic and needs no test",
                (),
            )
        return CoverageVerdict(
            False,
            "non-Python behavior input has no executable coverage lines",
            (),
        )

    try:
        source_text = (project_dir / src).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        source_text = None
    if source_text is not None and is_coverage_exempt_python(source_text):
        return CoverageVerdict(
            True,
            "interface-only Python file (Protocol/ABC abstract stubs and/or "
            "import re-exports) carries no executable logic to cover",
            (),
        )

    changed = changed_new_lines(project_dir, baseline_sha, src)
    if changed is None:
        return CoverageVerdict(
            False,
            "could not resolve changed lines against the task baseline",
            (),
        )
    if not changed:
        return CoverageVerdict(
            False,
            "no added/changed lines found for the change against the baseline",
            (),
        )

    try:
        dependent = dependent_test_files(project_dir, src)
    except _ModuleNameCollision as e:
        return CoverageVerdict(
            proven=False,
            reason=(
                f"coverage import graph collision for {e.module_name}: "
                f"{' and '.join(e.paths)} -- remove or rename one; "
                "package/module twins make dependent-test discovery ambiguous"
            ),
            candidate_nodes=(),
        )
    candidates = sorted(set(mapped_test_files) | set(dependent))
    if not candidates:
        return CoverageVerdict(
            False,
            "no scoped candidate test imports or reaches the changed module",
            (),
        )

    executed, reason = _run_coverage(project_dir, candidates, src, timeout)
    if executed is None:
        return CoverageVerdict(False, reason, tuple(candidates))

    covered = changed & executed
    if covered:
        return CoverageVerdict(
            True,
            f"changed lines {sorted(covered)} executed by scoped tests",
            tuple(candidates),
        )
    return CoverageVerdict(
        False,
        "changed lines were not executed by any scoped candidate test",
        tuple(candidates),
    )


__all__ = [
    "CoverageVerdict",
    "changed_new_lines",
    "dependent_test_files",
    "is_coverage_exempt_python",
    "verify_change_covered",
]
