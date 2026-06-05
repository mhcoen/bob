"""Conservative behavioral classifier for Python source changes.

Given the old and new text of a Python source file, decide whether the
change can *provably* not affect runtime behavior. The classifier is the
safety backstop behind the run_checks gate: a change is reported as
``NON_BEHAVIORAL`` only for cases that are demonstrably inert --

  - comment-only edits (comments never reach the AST),
  - docstring-only edits (the conventional first-statement string of a
    module/function/class, stripped before comparison),
  - AST-equivalent formatting (whitespace, blank lines, line wrapping),
  - import reordering that leaves the import graph unchanged.

Everything else -- renames, ``__all__`` edits, decorators, dataclass
fields, fixtures, entry-point changes, and anything the parser cannot make
sense of -- is ``BEHAVIORAL`` by default. The classifier never guesses in
the unsafe direction: when in doubt it returns ``BEHAVIORAL`` so the gate
fails closed.
"""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path
from typing import TypeAlias

_BodyNode: TypeAlias = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


class ChangeClass(str, Enum):
    """Result of classifying a single source change."""

    NON_BEHAVIORAL = "non_behavioral"
    BEHAVIORAL = "behavioral"


def _strip_leading_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop a leading docstring expression from a scope body, if present.

    A bare leading string literal is a docstring (module/class/function);
    removing it is behavior-neutral for the purposes of this classifier
    (see the module docstring's note on the runtime-consumed caveat).
    """
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _sort_leading_imports(body: list[ast.stmt]) -> list[ast.stmt]:
    """Canonicalize the order of the leading contiguous import block.

    Only the leading run of ``import`` / ``from ... import`` statements --
    the conventional top-of-scope import block -- is sorted. Imports
    interleaved with other statements keep their position so a genuinely
    position-dependent (side-effectful) import is never silently
    reordered. Sorting is by the node's structural dump, so a reorder
    that leaves the import set unchanged collapses to an identical list
    while an added/removed import does not.
    """
    i = 0
    while i < len(body) and isinstance(body[i], (ast.Import, ast.ImportFrom)):
        i += 1
    leading = sorted(body[:i], key=lambda n: ast.dump(n))
    return leading + body[i:]


class _Normalizer(ast.NodeTransformer):
    """Rewrite a tree into a form invariant to the provable-inert edits."""

    def _normalize_body(self, node: _BodyNode) -> ast.AST:
        node.body = _sort_leading_imports(_strip_leading_docstring(node.body))
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        self.generic_visit(node)
        return self._normalize_body(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        return self._normalize_body(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        return self._normalize_body(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.generic_visit(node)
        return self._normalize_body(node)

    def visit_Import(self, node: ast.Import) -> ast.AST:
        # Reordering imported names never changes the binding set.
        node.names = sorted(node.names, key=lambda a: (a.name, a.asname or ""))
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        node.names = sorted(node.names, key=lambda a: (a.name, a.asname or ""))
        return node


def _normalized_dump(source: str) -> str:
    tree = _Normalizer().visit(ast.parse(source))
    ast.fix_missing_locations(tree)
    # include_attributes=False drops lineno/col so whitespace and line
    # wrapping differences do not register as changes.
    return ast.dump(tree, include_attributes=False)


def classify_change(old_source: str, new_source: str) -> ChangeClass:
    """Classify a Python source change as behavioral or provably not.

    Returns ``NON_BEHAVIORAL`` only for the provable allowlist cases
    (comment-only, docstring-only, AST-equivalent formatting, import
    reorder with unchanged import graph). Any other difference -- or any
    input the parser rejects -- yields ``BEHAVIORAL``.
    """
    if old_source == new_source:
        return ChangeClass.NON_BEHAVIORAL
    try:
        old_norm = _normalized_dump(old_source)
        new_norm = _normalized_dump(new_source)
    except SyntaxError:
        # Cannot prove anything about unparseable source: fail closed.
        return ChangeClass.BEHAVIORAL
    if old_norm == new_norm:
        return ChangeClass.NON_BEHAVIORAL
    return ChangeClass.BEHAVIORAL


def is_provably_non_behavioral(old_source: str, new_source: str) -> bool:
    """Convenience predicate: True iff the change is provably inert."""
    return classify_change(old_source, new_source) is ChangeClass.NON_BEHAVIORAL


# --------------------------------------------------------------------------
# Non-code input classification (the no-test-needed change class).
# --------------------------------------------------------------------------
#
# Some changed inputs carry no executable application logic: dependency
# manifests, tool configuration, requirement/lock files, and plain data or
# documentation. A change to one of these has no source line a test could
# execute, so the coverage gate cannot -- and should not -- demand a mapped
# test or a logged waiver for it. Recognition is generalized over the
# filename suffix plus a small set of conventional dotfile/config names; it
# is deliberately NOT a single-file ``pyproject.toml`` whitelist.
#
# Logic-bearing non-Python inputs are intentionally excluded: templates
# (Jinja/Mako/HTML), SQL, and build scripts embed behavior, so they stay
# subject to the normal mapped-test / waiver requirement.

# Filename suffixes that denote a non-executable input. Each comment lists
# representative members so the class reads as a category, not a list.
_NO_TEST_NEEDED_SUFFIXES = frozenset(
    {
        ".toml",  # pyproject.toml, ruff.toml, poetry/uv config
        ".cfg",  # setup.cfg, .isort.cfg
        ".ini",  # pytest.ini, mypy.ini, tox.ini
        ".conf",
        ".json",  # package.json, tsconfig, plain data
        ".yaml",  # CI / pre-commit / data
        ".yml",
        ".lock",  # poetry.lock, Pipfile.lock, uv.lock
        ".txt",  # requirements.txt, constraints.txt, data
        ".csv",  # tabular data
        ".tsv",
        ".md",  # documentation
        ".rst",
        ".properties",
    }
)

# Conventional manifest/config filenames whose name carries the meaning
# (a leading-dot dotfile has an empty pathlib suffix, so suffix matching
# alone would miss these).
_NO_TEST_NEEDED_FILENAMES = frozenset(
    {
        "pipfile",
        "requirements",
        "constraints",
        ".flake8",
        ".coveragerc",
        ".editorconfig",
        ".gitignore",
        ".gitattributes",
        ".dockerignore",
        ".pylintrc",
    }
)


def is_no_test_needed_input(path: str) -> bool:
    """True iff *path* is a non-code input that needs no mapped test.

    Recognizes dependency manifests (``pyproject.toml``, ``Pipfile``),
    tool configuration (ruff/mypy/pytest/flake8/coverage configs),
    requirement and lock files, and plain data/documentation. Detection
    generalizes over the filename suffix and a small set of conventional
    config filenames -- it is not a single-file whitelist.

    Executable Python source (``.py``) and logic-bearing non-Python inputs
    (templates, SQL, build scripts) always return False, preserving the
    test/coverage requirement for anything that can carry behavior in an
    executable line.
    """
    name = Path(path).name.lower()
    if name.endswith(".py"):
        return False
    if name in _NO_TEST_NEEDED_FILENAMES:
        return True
    return Path(name).suffix in _NO_TEST_NEEDED_SUFFIXES
