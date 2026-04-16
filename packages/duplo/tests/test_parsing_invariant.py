"""Regression test: the ``strip_fences`` + ``json.loads`` pattern must not
be reintroduced into modules that were migrated to ``extract_json``.

``duplo.parsing.extract_json`` wraps the pattern correctly; consumers should
call it directly rather than chaining ``strip_fences`` into ``json.loads``.
"""

from __future__ import annotations

import ast
from pathlib import Path

DUPLO_DIR = Path(__file__).resolve().parent.parent / "duplo"

# ``parsing.py`` is the canonical home of the pattern (inside ``extract_json``
# itself). All other modules are expected to use ``extract_json`` instead.
#
# ``ALLOWED_UNMIGRATED`` lists modules that still contain the old pattern and
# have not yet been migrated. Entries should be removed as each module is
# migrated. Do NOT add new entries — that would defeat the regression guard.
ALLOWED_PARSING_HOME = {"parsing.py"}
ALLOWED_UNMIGRATED = {
    "roadmap.py",
    "verification_extractor.py",
    "investigator.py",
    "task_matcher.py",
    "saver.py",
}


def _is_strip_fences(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id == "strip_fences"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "strip_fences"
    return False


def _is_json_loads(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.Attribute)
        and expr.attr == "loads"
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "json"
    )


def _violations(tree: ast.AST, filename: str) -> list[str]:
    hits: list[str] = []

    # Pattern A: direct nesting — ``json.loads(strip_fences(...))``.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _is_json_loads(node.func)
            and any(isinstance(a, ast.Call) and _is_strip_fences(a.func) for a in node.args)
        ):
            hits.append(f"{filename}:{node.lineno}: json.loads(strip_fences(...))")

    # Pattern B: ``x = strip_fences(...)`` followed by ``json.loads(x)`` within
    # the same function (or module) scope.
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(node)

    seen: set[str] = set()
    for scope in scopes:
        bound: dict[str, int] = {}
        body = getattr(scope, "body", []) or []
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign):
                    val = sub.value
                    if isinstance(val, ast.Call) and _is_strip_fences(val.func):
                        for tgt in sub.targets:
                            if isinstance(tgt, ast.Name):
                                bound[tgt.id] = sub.lineno
                elif isinstance(sub, ast.Call) and _is_json_loads(sub.func):
                    for arg in sub.args:
                        if isinstance(arg, ast.Name) and arg.id in bound:
                            msg = (
                                f"{filename}:{sub.lineno}: json.loads({arg.id}) "
                                f"where {arg.id} = strip_fences(...) at line "
                                f"{bound[arg.id]}"
                            )
                            if msg not in seen:
                                seen.add(msg)
                                hits.append(msg)
    return hits


def _scan(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _violations(tree, path.name)


def test_no_strip_fences_then_json_loads_in_migrated_modules():
    """Fail if the old pattern appears in any module that was migrated."""
    skip = ALLOWED_PARSING_HOME | ALLOWED_UNMIGRATED
    violations: list[str] = []
    for py in sorted(DUPLO_DIR.glob("*.py")):
        if py.name in skip:
            continue
        violations.extend(_scan(py))
    assert not violations, (
        "strip_fences + json.loads pattern must be replaced by "
        "duplo.parsing.extract_json:\n" + "\n".join(violations)
    )


def test_allowed_unmigrated_list_is_accurate():
    """Each allow-listed module must actually still contain the pattern.

    If a module has been migrated, it should be removed from
    ``ALLOWED_UNMIGRATED`` so the regression guard covers it going forward.
    """
    stale: list[str] = []
    for name in sorted(ALLOWED_UNMIGRATED):
        path = DUPLO_DIR / name
        if not path.exists():
            stale.append(f"{name}: no longer exists")
            continue
        if not _scan(path):
            stale.append(f"{name}: no longer contains the pattern — remove from allowlist")
    assert not stale, "ALLOWED_UNMIGRATED is out of date:\n" + "\n".join(stale)
