"""Resolve Swift evidence with compiler ranges, retaining enclosing declarations."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

_DECL = re.compile(
    r"^(\s*)\((struct_decl|class_decl|enum_decl|protocol|extension_decl|func_decl|typealias)"
    r'.*?range=\[<stdin>:(\d+):\d+ - line:(\d+):\d+\](?: unbound)? "([^"\n]+)"'
)
_ATTR = re.compile(r"\(\w+_attr range=\[<stdin>:(\d+):")


@dataclass
class _Node:
    name: str
    start: int
    end: int


@lru_cache(maxsize=16)
def _declarations(compiler: str, text: str) -> tuple[tuple[str, int, int], ...] | None:
    try:
        result = subprocess.run(
            [compiler, "-frontend", "-dump-parse", "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    nodes: list[tuple[_Node, _Node, str]] = []
    stack: list[tuple[int, _Node]] = []
    for line in result.stdout.splitlines():
        match = _DECL.match(line)
        if match:
            indent, kind, start, end, name = match.groups()
            depth = len(indent)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            node = _Node(name.split("(", 1)[0], int(start), int(end))
            # Members need their enclosing type/extension's generic and conformance context.
            if stack:
                outer = stack[0][1]
            else:
                outer = node
            qualified = ".".join([parent.name for _, parent in stack] + [node.name])
            nodes.append((node, outer, qualified))
            if kind in {
                "struct_decl",
                "class_decl",
                "enum_decl",
                "protocol",
                "extension_decl",
                "func_decl",
            }:
                stack.append((depth, node))
        elif re.match(
            r"\s*\((?:struct_decl|class_decl|enum_decl|protocol|extension_decl)\b", line
        ):
            return None
        elif nodes:
            attr = _ATTR.search(line)
            if attr:
                nodes[-1][0].start = min(nodes[-1][0].start, int(attr.group(1)))
    lines = text.splitlines()
    resolved = []
    for node, outer, qualified in nodes:
        name = node.name
        start, end = min(node.start, outer.start), max(node.end, outer.end)
        while start > 1 and lines[start - 2].lstrip().startswith("///"):
            start -= 1
        if not 1 <= start <= end <= len(lines):
            return None
        resolved.append((name, start, end))
        if qualified != name:
            resolved.append((qualified, start, end))
    return tuple(resolved) or None


def declaration(reference: str, text: str, anchor: str) -> tuple[int, int] | None:
    compiler = shutil.which("swiftc")
    if compiler is None:
        return None
    declarations = _declarations(compiler, text)
    if declarations is None:
        return None
    # Overloads within one enclosing declaration supply exactly the same evidence.
    # Keep every overload by returning that declaration once.
    matches = sorted({(start, end) for name, start, end in declarations if name == anchor})
    if len(matches) > 1:
        raise ValueError(f"Evidence symbol must identify one declaration: {reference}")
    # Conditional compilation may omit branches from the dump. Keep the file in that case.
    if any(re.match(r"\s*#(?:if|elseif|else|endif)\b", line) for line in text.splitlines()):
        return None
    return matches[0] if matches else None
