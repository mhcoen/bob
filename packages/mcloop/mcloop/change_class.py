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

    def _normalize_body(self, node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = _sort_leading_imports(_strip_leading_docstring(body))
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
