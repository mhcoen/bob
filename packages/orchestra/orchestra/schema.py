"""Schema loader and validator for schema-backed json artifacts.

The runtime mechanism backing the ``artifact <name> json schema "<path>"``
qualifier specified in
``design/schema-verdict-runtime-support.md``. The module loads a JSON
Schema file from disk, validates it against the supported v0 shape,
and offers a runtime ``validate`` method that checks a parsed model
output against the schema and returns either the routing ``decision``
or a structured error.

Supported schema shape
----------------------

v0 supports JSON Schemas of this shape only:

  - root ``type`` is ``object``,
  - ``decision`` field is in ``required``, declared with ``type: string``
    and an ``enum`` constraint listing the allowed routing outcomes,
  - other top-level fields may be present in ``required`` (forced into
    runtime presence) or in ``properties`` only (optional). Supported
    field types: ``string``, ``integer``, ``number``, ``boolean``,
    ``array`` of supported types, ``object`` with the same shape
    constraints,
  - ``additionalProperties: false`` is recommended but not required.

Schemas using ``$ref``, ``oneOf``, ``anyOf``, ``allOf``, ``not``, or
any other non-supported keyword are rejected at load time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.errors import OrchestraError

SUPPORTED_FIELD_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "array", "object"}
)
"""Top-level field types the v0 schema validator supports."""

EXTRACTABLE_FIELD_TYPES: frozenset[str] = frozenset({"string", "integer", "number", "boolean"})
"""Source-field types admissible in an ``extract`` clause. Object and
array source fields are a load error in v0."""


class SchemaError(OrchestraError):
    """Raised when a schema file cannot be loaded or fails the v0
    supported-shape check."""


@dataclass(frozen=True)
class SchemaSpec:
    """Parsed and shape-validated schema, ready for runtime use."""

    path: str
    decision_enum: tuple[str, ...]
    field_types: dict[str, str]
    """Top-level property name to its declared JSON-Schema type. Used
    by the workflow validator to police ``extract`` clauses and by the
    runtime validator to type-check the parsed object."""
    required_fields: frozenset[str]
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Raw per-property schema fragments, used for nested validation
    (array item types, object subschema)."""
    additional_properties: bool = True

    def validate(self, value: Any) -> ValidationResult:
        """Validate ``value`` against the schema. Returns either
        ``Valid`` carrying the decision string or ``Invalid`` carrying
        a list of validation error messages.
        """
        return _validate_object(self, value)


@dataclass(frozen=True)
class Valid:
    decision: str


@dataclass(frozen=True)
class Invalid:
    errors: tuple[str, ...]


ValidationResult = Valid | Invalid


def load_schema(path: Path) -> SchemaSpec:
    """Read, parse, and shape-validate a JSON Schema file.

    Raises ``SchemaError`` on file-read errors, JSON-parse errors, or
    any deviation from the supported v0 shape.
    """
    if not path.is_file():
        raise SchemaError(f"schema file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"schema file unreadable {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"schema file is not valid JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"schema {path}: root must be a JSON object, got {type(data).__name__}")
    return _build_spec(data, str(path))


def _build_spec(data: dict[str, Any], where: str) -> SchemaSpec:
    if data.get("type") != "object":
        raise SchemaError(f"schema {where}: root 'type' must be 'object', got {data.get('type')!r}")
    _reject_unsupported_keywords(data, where, top_level=True)

    properties_raw = data.get("properties")
    if not isinstance(properties_raw, dict):
        raise SchemaError(f"schema {where}: 'properties' must be an object")
    required_raw = data.get("required") or []
    if not isinstance(required_raw, list) or not all(isinstance(r, str) for r in required_raw):
        raise SchemaError(f"schema {where}: 'required' must be a list of strings")
    required: set[str] = set(required_raw)

    if "decision" not in required:
        raise SchemaError(f"schema {where}: 'decision' must be listed in 'required'")
    decision_schema = properties_raw.get("decision")
    if not isinstance(decision_schema, dict):
        raise SchemaError(f"schema {where}: 'decision' property is missing")
    if decision_schema.get("type") != "string":
        raise SchemaError(f"schema {where}: 'decision' type must be 'string'")
    enum_raw = decision_schema.get("enum")
    if (
        not isinstance(enum_raw, list)
        or not enum_raw
        or not all(isinstance(v, str) and v for v in enum_raw)
    ):
        raise SchemaError(
            f"schema {where}: 'decision' must declare a non-empty 'enum' of string values"
        )
    if len(set(enum_raw)) != len(enum_raw):
        raise SchemaError(f"schema {where}: 'decision' enum values must be unique")

    field_types: dict[str, str] = {}
    for prop_name, prop_schema in properties_raw.items():
        if not isinstance(prop_name, str):
            raise SchemaError(f"schema {where}: property name must be a string, got {prop_name!r}")
        if not isinstance(prop_schema, dict):
            raise SchemaError(f"schema {where}: property {prop_name!r} schema must be an object")
        prop_type = prop_schema.get("type")
        if prop_type not in SUPPORTED_FIELD_TYPES:
            raise SchemaError(
                f"schema {where}: property {prop_name!r} declares "
                f"unsupported type {prop_type!r} (supported: "
                f"{sorted(SUPPORTED_FIELD_TYPES)})"
            )
        _validate_property_subschema(prop_name, prop_schema, where)
        field_types[prop_name] = prop_type

    add_props = data.get("additionalProperties", True)
    if not isinstance(add_props, bool):
        raise SchemaError(f"schema {where}: 'additionalProperties' must be a bool")

    for r in required:
        if r not in properties_raw:
            raise SchemaError(f"schema {where}: required field {r!r} is not listed in 'properties'")

    return SchemaSpec(
        path=where,
        decision_enum=tuple(enum_raw),
        field_types=field_types,
        required_fields=frozenset(required),
        properties={k: dict(v) for k, v in properties_raw.items()},
        additional_properties=add_props,
    )


_SUPPORTED_TOPLEVEL_KEYWORDS: frozenset[str] = frozenset(
    {"$schema", "type", "required", "properties", "additionalProperties", "title", "description"}
)
_SUPPORTED_PROPERTY_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "enum",
        "items",
        "properties",
        "required",
        "additionalProperties",
        "title",
        "description",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)
_REJECTED_KEYWORDS: frozenset[str] = frozenset(
    {"$ref", "oneOf", "anyOf", "allOf", "not", "$defs", "definitions"}
)


def _reject_unsupported_keywords(obj: dict[str, Any], where: str, *, top_level: bool) -> None:
    allowed = _SUPPORTED_TOPLEVEL_KEYWORDS if top_level else _SUPPORTED_PROPERTY_KEYWORDS
    for key in obj.keys():
        if key in _REJECTED_KEYWORDS:
            raise SchemaError(f"schema {where}: keyword {key!r} is not supported in v0")
        if key not in allowed:
            raise SchemaError(f"schema {where}: keyword {key!r} is not recognized")


def _validate_property_subschema(prop_name: str, prop_schema: dict[str, Any], where: str) -> None:
    """Recursively check a property's schema for v0 compliance."""
    _reject_unsupported_keywords(prop_schema, f"{where} (property {prop_name!r})", top_level=False)
    prop_type = prop_schema.get("type")
    if prop_type == "array":
        items = prop_schema.get("items")
        if not isinstance(items, dict):
            raise SchemaError(
                f"schema {where}: array property {prop_name!r} must "
                "declare 'items' as an object schema"
            )
        item_type = items.get("type")
        if item_type not in SUPPORTED_FIELD_TYPES:
            raise SchemaError(
                f"schema {where}: array property {prop_name!r} 'items' "
                f"type {item_type!r} is unsupported"
            )
        _validate_property_subschema(f"{prop_name}.items", items, where)
    elif prop_type == "object":
        # Nested object: recurse with same shape rules but no decision check.
        nested_props = prop_schema.get("properties") or {}
        if not isinstance(nested_props, dict):
            raise SchemaError(
                f"schema {where}: object property {prop_name!r} 'properties' must be an object"
            )
        for sub_name, sub_schema in nested_props.items():
            if not isinstance(sub_schema, dict):
                raise SchemaError(
                    f"schema {where}: nested property "
                    f"{prop_name}.{sub_name} schema must be an object"
                )
            sub_type = sub_schema.get("type")
            if sub_type not in SUPPORTED_FIELD_TYPES:
                raise SchemaError(
                    f"schema {where}: nested property "
                    f"{prop_name}.{sub_name} type {sub_type!r} is "
                    "unsupported"
                )
            _validate_property_subschema(f"{prop_name}.{sub_name}", sub_schema, where)


# --------------------------------------------------------------------
# Runtime value validation
# --------------------------------------------------------------------


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _validate_value_against_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is None:
        return errors
    if not _type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors
    if expected_type == "string":
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: value {value!r} is not in enum {enum!r}")
    elif expected_type == "array":
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(value):
                errors.extend(_validate_value_against_schema(item, items_schema, f"{path}[{i}]"))
    elif expected_type == "object":
        nested_props = schema.get("properties") or {}
        nested_required = schema.get("required") or []
        nested_add = schema.get("additionalProperties", True)
        if isinstance(nested_required, list):
            for r in nested_required:
                if r not in value:
                    errors.append(f"{path}.{r}: required field missing")
        if isinstance(nested_props, dict):
            for k, sub_schema in nested_props.items():
                if k in value and isinstance(sub_schema, dict):
                    errors.extend(
                        _validate_value_against_schema(value[k], sub_schema, f"{path}.{k}")
                    )
            if isinstance(nested_add, bool) and not nested_add:
                extras = sorted(set(value.keys()) - set(nested_props.keys()))
                for extra in extras:
                    errors.append(f"{path}.{extra}: additional property not permitted")
    return errors


def _validate_object(spec: SchemaSpec, value: Any) -> ValidationResult:
    if not isinstance(value, dict):
        return Invalid(errors=(f"root: expected object, got {type(value).__name__}",))
    errors: list[str] = []
    for r in spec.required_fields:
        if r not in value:
            errors.append(f"{r}: required field missing")
    for prop_name, prop_schema in spec.properties.items():
        if prop_name in value:
            errors.extend(_validate_value_against_schema(value[prop_name], prop_schema, prop_name))
    if not spec.additional_properties:
        extras = sorted(set(value.keys()) - set(spec.properties.keys()))
        for extra in extras:
            errors.append(f"{extra}: additional property not permitted")
    if errors:
        return Invalid(errors=tuple(errors))
    decision = value.get("decision")
    assert isinstance(decision, str)
    if decision not in spec.decision_enum:
        return Invalid(
            errors=(f"decision: value {decision!r} is not in enum {list(spec.decision_enum)!r}",)
        )
    return Valid(decision=decision)


__all__ = [
    "EXTRACTABLE_FIELD_TYPES",
    "Invalid",
    "SchemaError",
    "SchemaSpec",
    "SUPPORTED_FIELD_TYPES",
    "Valid",
    "ValidationResult",
    "load_schema",
]
