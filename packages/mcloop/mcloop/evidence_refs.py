"""Resolve evidence files, headings and symbols without model-generated line numbers."""

from __future__ import annotations

import ast
import re

from mcloop.swift_evidence import declaration


class UnresolvedCodeAnchor(ValueError):
    """The local parser cannot establish a requested code symbol's range."""


def _python_matches(tree: ast.AST, anchor: str) -> list:
    matches = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
            if anchor == (".".join(scope) if "." in anchor else node.name):
                matches.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return matches


def resolve(reference: str, text: str) -> tuple[str, int, int]:
    """Resolve existing anchors; retain the whole file when several candidates match."""
    lines = text.splitlines()
    legacy = re.fullmatch(r"(.+):(\d+)-(\d+)", reference)
    if legacy:
        name, first, last = legacy.groups()
        start, end = int(first), int(last)
    else:
        name, separator, anchor = reference.partition("#")
        start, end = 1, len(lines)
        if separator:
            if not anchor:
                raise ValueError(f"Empty evidence anchor: {reference}")
            headings = [
                i
                for i, line in enumerate(lines)
                if re.match(r"^#{1,6}\s", line)
                and (
                    line.lstrip("#").strip() == anchor
                    or line.lstrip("#").strip().startswith(anchor + ":")
                )
            ]
            if len(headings) > 1:
                return name, 1, len(lines)
            if len(headings) == 1:
                index = headings[0]
                level = len(lines[index]) - len(lines[index].lstrip("#"))
                end = next(
                    (
                        i
                        for i in range(index + 1, len(lines))
                        if re.match(rf"^#{{1,{level}}}\s", lines[i])
                    ),
                    len(lines),
                )
                start = index + 1
            elif name.endswith(".py"):
                try:
                    tree = ast.parse(text)
                except SyntaxError as exc:
                    raise UnresolvedCodeAnchor(
                        f"Cannot resolve Python symbol: {reference}"
                    ) from exc
                matches = _python_matches(tree, anchor)
                if not matches:
                    raise UnresolvedCodeAnchor(f"Evidence symbol not resolved: {reference}")
                if len(matches) > 1:
                    return name, 1, len(lines)
                node = matches[0]
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                end = node.end_lineno or node.lineno
            else:
                swift_range = (
                    declaration(reference, text, anchor) if name.endswith(".swift") else None
                )
                if swift_range is not None:
                    start, end = swift_range
                    return name, start, end
                # For other languages select the complete file once the declaration is
                # identified. This preserves contracts without guessing brace/string syntax.
                pattern = re.compile(
                    r"\b(?:class|struct|enum|protocol|actor|func|function|interface|type|def|let|var|const)\s+"
                    + re.escape(anchor)
                    + r"\b"
                )
                declarations = [line for line in lines if pattern.search(line)]
                if not declarations:
                    error = (
                        ValueError
                        if name.lower().endswith((".md", ".markdown"))
                        else UnresolvedCodeAnchor
                    )
                    raise error(
                        f"Evidence anchor must identify one heading or declaration: {reference}"
                    )
    if not 1 <= start <= end <= len(lines):
        raise ValueError(f"Evidence line range does not exist: {reference}")
    return name, start, end


def filename(reference: str) -> str:
    legacy = re.fullmatch(r"(.+):\d+-\d+", reference)
    return legacy.group(1) if legacy else reference.partition("#")[0]
