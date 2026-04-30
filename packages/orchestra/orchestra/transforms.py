"""Transform state primitive (Slice B).

A transform is a registered, pure-function adapter: declared inputs and
outputs are dicts of artifact name to typed value, the callable runs
synchronously inside the per-state sequence, and replay treats a
completed transform state like any other completed state.

The schema vocabulary is intentionally narrow. Slice B accepts the
primitive types ``str``, ``int``, ``float``, ``bool`` plus the
parameterized type ``dict[str, str]``. Other types are rejected at
registration time. No third-party type-checking library is pulled in.

``bytes`` was in the plan's draft type list but is not supported
here. The artifact store hashes values via ``json.dumps``
(``orchestra/store/store.py`` ``_canonicalize``), which rejects
``bytes``, so a registered ``bytes`` schema would pass the runtime
typecheck and then crash at ``tentative_write``. A binary artifact
contract is deferred to a later slice and the plan's Section 2 type
list should be updated to drop ``bytes``.

The runtime type checker uses ``isinstance`` for primitives plus a
custom recursive walk for ``dict[str, str]``.
"""

from __future__ import annotations

import hashlib
import json
import random
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from orchestra.errors import RegistryConflict

_PRIMITIVE_TYPES: tuple[type, ...] = (str, int, float, bool)
"""Schema-supported primitive Python types."""

# Canonical artifact-type that each Python type maps to. The validator
# uses this mapping to check that a workflow's artifact declaration is
# compatible with the transform's declared schema.
_TYPE_TO_ARTIFACT_TYPE: dict[Any, str] = {
    str: "text",
    int: "json",
    float: "json",
    bool: "json",
}


def is_dict_str_str(t: Any) -> bool:
    """True for the parameterized type ``dict[str, str]`` only.

    Detects any construction path: ``dict[str, str]``,
    ``typing.Dict[str, str]``. Plain ``dict`` (no parameters) is NOT
    accepted because Slice B's runtime type checker needs to know both
    key and value types.
    """
    origin = typing.get_origin(t)
    if origin is not dict:
        return False
    args = typing.get_args(t)
    return args == (str, str)


def is_supported_type(t: Any) -> bool:
    """True if ``t`` is in the Slice B supported set."""
    if t in _PRIMITIVE_TYPES:
        return True
    return is_dict_str_str(t)


def type_label(t: Any) -> str:
    """Human-readable label for a schema type, used in error messages."""
    if t in _PRIMITIVE_TYPES:
        return str(t.__name__)
    if is_dict_str_str(t):
        return "dict[str, str]"
    return repr(t)


def schema_artifact_type(t: Any) -> str:
    """Map a schema Python type to the artifact-type string the
    workflow's ``artifact <name> <type>`` declaration must use.

    Raises ``RegistryConflict`` for unsupported types so callers see a
    consistent error path even if validation reaches them out of order.
    """
    if t in _TYPE_TO_ARTIFACT_TYPE:
        return _TYPE_TO_ARTIFACT_TYPE[t]
    if is_dict_str_str(t):
        return "json"
    raise RegistryConflict(
        f"transform schema: type {type_label(t)} is not supported "
        "(supported: str, int, float, bool, dict[str, str])"
    )


def validate_schema(schema: dict[str, Any], *, where: str) -> None:
    """Reject schema entries whose type is not in the supported set.

    ``where`` is included in the error message so the caller knows
    which schema (input or output) failed.
    """
    if not isinstance(schema, dict):
        raise RegistryConflict(
            f"transform {where}: schema must be a dict[str, type]"
        )
    for key, t in schema.items():
        if not isinstance(key, str):
            raise RegistryConflict(
                f"transform {where}: schema keys must be str, got {key!r}"
            )
        if not is_supported_type(t):
            raise RegistryConflict(
                f"transform {where}: type for {key!r} is {type_label(t)}, "
                "which is not supported (supported: str, int, float, "
                "bool, dict[str, str])"
            )


def runtime_check(value: Any, t: Any) -> bool:
    """Runtime type check.

    ``isinstance`` for primitives. For ``dict[str, str]``: the value
    must be a ``dict``, every key must be a ``str``, every value must
    be a ``str``.

    ``bool`` is handled specially: Python's ``isinstance(True, int)``
    is ``True``, so a ``bool`` value would otherwise satisfy an
    ``int`` schema. Slice B treats the two as distinct.
    """
    if t is bool:
        return isinstance(value, bool)
    if t is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if t in _PRIMITIVE_TYPES:
        return isinstance(value, t)
    if is_dict_str_str(t):
        if not isinstance(value, dict):
            return False
        for k, v in value.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return False
        return True
    return False


@dataclass(frozen=True)
class TransformContext:
    """Per-invocation context handed to a transform callable.

    Slice B's deterministic transforms (notably ``anonymize_outputs``)
    derive their RNG seed from these fields; ``sorted_input_keys``
    pins the seed across runs even when the underlying artifact-store
    iteration order would otherwise differ.
    """

    run_id: str
    state_name: str
    sorted_input_keys: list[str]


TransformCallable = Callable[
    [dict[str, Any], TransformContext], dict[str, Any]
]


@dataclass(frozen=True)
class Transform:
    """A registered transform.

    Stored in ``ProfileRegistry.transforms`` keyed by ``name``. The
    validator and the executor read the same record so they cannot
    drift on input/output key sets or types.
    """

    name: str
    callable: TransformCallable
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


# --------------------------------------------------------------------
# Built-in transforms
# --------------------------------------------------------------------


def anonymize_outputs(
    inputs: dict[str, Any], ctx: TransformContext
) -> dict[str, Any]:
    """Map named string inputs to anonymous keys ``A``, ``B``, ``C``...

    Determinism contract: the seed is derived from
    ``(run_id, state_name, sorted_input_keys)``. The same inputs in the
    same run produce the same mapping; two transform states in the
    same run produce different mappings (state_name differs).

    Input keys are sorted before shuffling so the artifact store's
    iteration order cannot perturb the result.
    """
    sorted_keys = sorted(inputs.keys())
    # Slice B contract: the seed is the literal default
    # ``json.dumps([run_id, state_name, sorted_input_keys])``. Custom
    # encoder flags would change the byte representation for
    # non-ASCII characters (``ensure_ascii=False`` emits raw UTF-8;
    # the default escapes to ``\uXXXX``). Determinism across
    # platforms and Python versions requires the literal default.
    seed_material = json.dumps(
        [ctx.run_id, ctx.state_name, sorted_keys]
    )
    seed_hash = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    seed_int = int(seed_hash, 16)
    rng = random.Random(seed_int)
    shuffled = list(sorted_keys)
    rng.shuffle(shuffled)
    anon_keys = [_anon_letter(i) for i in range(len(shuffled))]
    anon_map: dict[str, str] = {}
    for letter, original_key in zip(anon_keys, shuffled, strict=True):
        value = inputs[original_key]
        if not isinstance(value, str):
            raise TypeError(
                f"anonymize_outputs: input {original_key!r} is not a str"
            )
        anon_map[letter] = value
    return {"anon_map": anon_map}


def _anon_letter(index: int) -> str:
    """A, B, ..., Z, AA, AB, ... so the mapping is open-ended.

    Slice B's only consumer (the ``ask_council`` workflow) has five
    advisors, so the simple A-Z range suffices in practice, but the
    helper keeps the door open for larger fan-outs without revisiting
    the seed contract.
    """
    if index < 26:
        return chr(ord("A") + index)
    first = index // 26 - 1
    second = index % 26
    return chr(ord("A") + first) + chr(ord("A") + second)


__all__ = [
    "Transform",
    "TransformCallable",
    "TransformContext",
    "anonymize_outputs",
    "is_dict_str_str",
    "is_supported_type",
    "runtime_check",
    "schema_artifact_type",
    "type_label",
    "validate_schema",
]
