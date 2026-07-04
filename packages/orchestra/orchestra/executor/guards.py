"""Guard expression evaluation."""

from __future__ import annotations

from typing import Any

from orchestra.spine import (
    AndExpr,
    Comparison,
    GuardExpr,
    Literal_,
    NotExpr,
    OrExpr,
    Reference,
    TruthyTest,
)


class GuardContext:
    """Read-only view of runtime values that guards consult.

    The runner builds a fresh GuardContext each time it evaluates a
    transition guard. The context's lookups are O(1) on dicts.
    """

    def __init__(
        self,
        *,
        attempts: dict[str, int],
        retries: dict[str, int],
        external_inputs: dict[str, Any],
        artifacts: dict[str, Any],
        envelopes: dict[str, Any],
    ) -> None:
        self._attempts = attempts
        self._retries = retries
        self._external_inputs = external_inputs
        self._artifacts = artifacts
        self._envelopes = envelopes

    def resolve(self, ref: Reference) -> Any:
        head = ref.head()
        rest = ref.parts[1:]
        if head == "attempts":
            if len(rest) != 1:
                raise ValueError(f"attempts reference must be attempts.<state>: {ref}")
            return self._attempts.get(rest[0], 0)
        if head == "retries":
            if len(rest) != 1:
                raise ValueError(f"retries reference must be retries.<state>: {ref}")
            return self._retries.get(rest[0], 0)
        # External inputs.
        if head in self._external_inputs:
            value = self._external_inputs[head]
            return _walk(value, rest)
        # Artifacts.
        if head in self._artifacts:
            value = self._artifacts[head]
            return _walk(value, rest)
        # State envelopes.
        if head in self._envelopes:
            value = self._envelopes[head]
            return _walk(value, rest)
        raise KeyError(f"unresolved reference: {ref}")


def _walk(value: Any, parts: tuple[str, ...]) -> Any:
    for p in parts:
        if isinstance(value, dict):
            # A dict step resolves only against its keys. A missing key
            # is absent, never a fallback to hasattr: ``foo.items`` on a
            # dict without an ``items`` key must not resolve to the bound
            # ``dict.items`` method (a truthy builtin that would make a
            # TruthyTest always fire). Absent resolves to None so the
            # guard reads it as false.
            if p in value:
                value = value[p]
            else:
                return None
        elif hasattr(value, p):
            value = getattr(value, p)
        else:
            raise KeyError(f"cannot walk {p!r} on {type(value).__name__}")
    return value


def evaluate(expr: GuardExpr, ctx: GuardContext) -> bool:
    if isinstance(expr, TruthyTest):
        return bool(ctx.resolve(expr.ref))
    if isinstance(expr, Comparison):
        left = ctx.resolve(expr.left)
        right = expr.right.value if isinstance(expr.right, Literal_) else ctx.resolve(expr.right)
        return _cmp(expr.op, left, right)
    if isinstance(expr, NotExpr):
        return not evaluate(expr.inner, ctx)
    if isinstance(expr, AndExpr):
        return all(evaluate(p, ctx) for p in expr.parts)
    if isinstance(expr, OrExpr):
        return any(evaluate(p, ctx) for p in expr.parts)
    raise TypeError(f"unknown guard node: {type(expr).__name__}")


def _cmp(op: str, left: Any, right: Any) -> bool:
    if op == "<":
        return bool(left < right)
    if op == "<=":
        return bool(left <= right)
    if op == ">":
        return bool(left > right)
    if op == ">=":
        return bool(left >= right)
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    raise ValueError(f"unknown comparison operator: {op!r}")
