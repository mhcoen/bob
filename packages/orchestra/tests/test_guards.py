"""Unit tests for guard evaluation and template formatting.

Two resolution defects are pinned here so they cannot regress:

* ``guards._walk`` must not fall back to ``hasattr`` for a missing
  dict key. ``when foo.items`` on a dict without an ``items`` key
  must read as false, not resolve to the bound ``dict.items`` builtin.
* ``_format`` must render a dict-valued substitution whole. It must
  not collapse ``{"value": ...}`` to its ``"value"`` member, which
  would silently drop the rest of the mapping.
"""

from __future__ import annotations

import pytest

from orchestra.executor._executor_common import _format
from orchestra.executor.guards import GuardContext, evaluate
from orchestra.spine import Comparison, Literal_, Reference, TruthyTest


def _ctx(**inputs: object) -> GuardContext:
    return GuardContext(
        attempts={},
        retries={},
        external_inputs=dict(inputs),
        artifacts={},
        envelopes={},
    )


# --------------------------------------------------------------------
# guards._walk / TruthyTest
# --------------------------------------------------------------------


def test_missing_dict_key_reads_as_false() -> None:
    # ``foo`` has no ``items`` key. The walk must not resolve to the
    # bound ``dict.items`` method (which is truthy).
    ctx = _ctx(foo={"other": 1})
    ref = Reference(parts=("foo", "items"))
    assert ctx.resolve(ref) is None
    assert evaluate(TruthyTest(ref), ctx) is False


def test_missing_dict_key_named_like_builtin_method() -> None:
    # ``get`` / ``keys`` / ``values`` are all bound methods on a dict.
    # None of them may leak through as a truthy resolution.
    ctx = _ctx(foo={"present": 1})
    for name in ("get", "keys", "values", "items"):
        ref = Reference(parts=("foo", name))
        assert ctx.resolve(ref) is None, name
        assert evaluate(TruthyTest(ref), ctx) is False, name


def test_present_dict_key_still_resolves() -> None:
    ctx = _ctx(foo={"items": [1, 2, 3]})
    ref = Reference(parts=("foo", "items"))
    assert ctx.resolve(ref) == [1, 2, 3]
    assert evaluate(TruthyTest(ref), ctx) is True


def test_present_but_falsy_dict_key_reads_as_false() -> None:
    ctx = _ctx(foo={"items": []})
    ref = Reference(parts=("foo", "items"))
    assert ctx.resolve(ref) == []
    assert evaluate(TruthyTest(ref), ctx) is False


def test_attribute_fallback_still_works_for_objects() -> None:
    class Env:
        outcome = "ok"

    ctx = _ctx(env=Env())
    ref = Reference(parts=("env", "outcome"))
    assert ctx.resolve(ref) == "ok"
    assert evaluate(TruthyTest(ref), ctx) is True


# --------------------------------------------------------------------
# _format
# --------------------------------------------------------------------


def test_format_renders_dict_value_whole() -> None:
    # A dict-valued variable that happens to carry a "value" key is a
    # real value in its own right. It must render whole, not collapse
    # to its "value" member.
    var = {"value": "x", "lang": "en"}
    out = _format("payload={var}", {"var": var})
    assert out == "payload={'value': 'x', 'lang': 'en'}"


def test_format_substitutes_plain_value() -> None:
    assert _format("topic={topic}", {"topic": "hello"}) == "topic=hello"


def test_format_leaves_missing_key_literal() -> None:
    assert _format("a={a} b={b}", {"a": "1"}) == "a=1 b={b}"


def test_ordering_comparison_on_missing_key_raises_named_error() -> None:
    # A missing key resolves to None so truthiness works for optional
    # fields, but an ORDERING comparison on that None is a guard
    # authoring error (usually a typo'd key). The old behavior was a
    # bare "'<' not supported between NoneType and int" TypeError that
    # named nothing; the error must now name the reference.
    ctx = _ctx(foo={"other": 1})
    expr = Comparison(op="<", left=Reference(parts=("foo", "missing")), right=Literal_(value=3))
    with pytest.raises(KeyError, match="missing"):
        evaluate(expr, ctx)


def test_equality_comparison_with_missing_key_stays_legal() -> None:
    # Equality against an absent value is meaningful (absent != value,
    # absent == absent) and must not raise.
    ctx = _ctx(foo={"other": 1})
    expr = Comparison(op="!=", left=Reference(parts=("foo", "missing")), right=Literal_(value=3))
    assert evaluate(expr, ctx) is True
