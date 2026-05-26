"""Unit tests for the schema loader and runtime validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.schema import (
    Invalid,
    SchemaError,
    SchemaSpec,
    Valid,
    load_schema,
)

_TWO_BRANCH = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["decision"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["accept", "iterate"],
        },
        "feedback": {"type": "string"},
    },
    "additionalProperties": False,
}


def _write_schema(tmp_path: Path, name: str, body: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --------------------------------------------------------------------
# Loader: shape validation
# --------------------------------------------------------------------


def test_load_schema_succeeds_on_two_branch(tmp_path):
    p = _write_schema(tmp_path, "v.json", _TWO_BRANCH)
    spec = load_schema(p)
    assert isinstance(spec, SchemaSpec)
    assert spec.decision_enum == ("accept", "iterate")
    assert spec.field_types == {
        "decision": "string",
        "feedback": "string",
    }
    assert spec.required_fields == frozenset({"decision"})
    assert spec.additional_properties is False


def test_load_schema_missing_file(tmp_path):
    with pytest.raises(SchemaError) as exc:
        load_schema(tmp_path / "nope.json")
    assert "not found" in str(exc.value)


def test_load_schema_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "valid JSON" in str(exc.value)


def test_load_schema_root_not_object(tmp_path):
    p = _write_schema(tmp_path, "x.json", {"type": "string"})
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "object" in str(exc.value)


def test_load_schema_decision_must_be_required(tmp_path):
    body = dict(_TWO_BRANCH)
    body["required"] = []
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "decision" in str(exc.value)


def test_load_schema_decision_must_be_enum(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    del body["properties"]["decision"]["enum"]
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "enum" in str(exc.value)


def test_load_schema_unsupported_keyword_rejected(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["oneOf"] = [{"type": "object"}]
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "oneOf" in str(exc.value)


def test_load_schema_ref_rejected(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["nested"] = {"$ref": "#/definitions/foo"}
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError):
        load_schema(p)


def test_load_schema_unsupported_field_type_rejected(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["weird"] = {"type": "null"}
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "weird" in str(exc.value)


def test_load_schema_array_items_required(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["tags"] = {"type": "array"}
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "items" in str(exc.value)


def test_load_schema_required_field_not_in_properties(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["required"] = ["decision", "ghost"]
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError) as exc:
        load_schema(p)
    assert "ghost" in str(exc.value)


def test_load_schema_decision_duplicate_enum_values_rejected(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["decision"]["enum"] = ["accept", "accept"]
    p = _write_schema(tmp_path, "x.json", body)
    with pytest.raises(SchemaError):
        load_schema(p)


# --------------------------------------------------------------------
# Runtime validate()
# --------------------------------------------------------------------


def test_validate_accepts_minimal_object(tmp_path):
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "accept"})
    assert isinstance(result, Valid)
    assert result.decision == "accept"


def test_validate_rejects_extra_property_when_forbidden(tmp_path):
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "accept", "extra": "x"})
    assert isinstance(result, Invalid)
    assert any("additional property" in e for e in result.errors)


def test_validate_rejects_decision_not_in_enum(tmp_path):
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "punt"})
    assert isinstance(result, Invalid)
    assert any("not in enum" in e for e in result.errors)


def test_validate_rejects_missing_required(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["required"] = ["decision", "feedback"]
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    result = spec.validate({"decision": "accept"})
    assert isinstance(result, Invalid)
    assert any("feedback" in e for e in result.errors)


def test_validate_rejects_wrong_field_type(tmp_path):
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "accept", "feedback": 42})
    assert isinstance(result, Invalid)
    assert any("feedback" in e for e in result.errors)


def test_validate_rejects_non_object_value(tmp_path):
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate("not an object")
    assert isinstance(result, Invalid)


def test_validate_array_items(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["tags"] = {
        "type": "array",
        "items": {"type": "string"},
    }
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    bad = spec.validate({"decision": "accept", "tags": ["a", 2]})
    assert isinstance(bad, Invalid)
    assert any("tags[1]" in e for e in bad.errors)
    good = spec.validate({"decision": "accept", "tags": ["a", "b"]})
    assert isinstance(good, Valid)


def test_validate_distinguishes_int_and_bool(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["count"] = {"type": "integer"}
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    # bool is not int per schema validator's rule
    result = spec.validate({"decision": "accept", "count": True})
    assert isinstance(result, Invalid)


def test_validate_number_accepts_int_and_float(tmp_path):
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["score"] = {"type": "number"}
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    assert isinstance(spec.validate({"decision": "accept", "score": 3}), Valid)
    assert isinstance(spec.validate({"decision": "accept", "score": 3.14}), Valid)


# --------------------------------------------------------------------
# Validator error-string contract for downstream consumers.
#
# duplo.schema_classification matches validation_errors strings by
# substring to classify failures (additional_properties,
# missing_required, enum_mismatch, malformed_array). The shapes
# below are the downstream contract: changing them silently breaks
# the classifier, the bounded-retry feedback, and mcloop's
# schema_validation_invalid pause reason. Pin them here so any
# future edit to orchestra/schema.py that affects the wording
# trips a named test instead of breaking a sibling repo at runtime.
# --------------------------------------------------------------------


def test_additional_property_error_uses_canonical_phrase(tmp_path):
    """The additional-property error MUST contain the substring
    'additional property not permitted'. This is what
    duplo.schema_classification.classify_schema_failure matches on."""
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "accept", "unexpected_key": 1})
    assert isinstance(result, Invalid)
    assert any("additional property not permitted" in e for e in result.errors)


def test_missing_required_error_uses_canonical_phrase(tmp_path):
    """The missing-required error MUST contain the substring
    'required field missing'."""
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["required"] = ["decision", "feedback"]
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    result = spec.validate({"decision": "accept"})
    assert isinstance(result, Invalid)
    assert any("required field missing" in e for e in result.errors)


def test_enum_mismatch_error_uses_canonical_phrase(tmp_path):
    """The enum-mismatch error MUST contain the substring
    'is not in enum'."""
    spec = load_schema(_write_schema(tmp_path, "v.json", _TWO_BRANCH))
    result = spec.validate({"decision": "punt"})
    assert isinstance(result, Invalid)
    assert any("is not in enum" in e for e in result.errors)


def test_malformed_array_error_uses_canonical_phrase(tmp_path):
    """The malformed-array error (an array-typed field receiving a
    non-array value) MUST contain the substring 'expected array,
    got'. This is the duplo classifier's signal for the
    malformed_array kind."""
    body = json.loads(json.dumps(_TWO_BRANCH))
    body["properties"]["tags"] = {
        "type": "array",
        "items": {"type": "string"},
    }
    spec = load_schema(_write_schema(tmp_path, "v.json", body))
    result = spec.validate({"decision": "accept", "tags": "not-an-array"})
    assert isinstance(result, Invalid)
    assert any("expected array, got" in e for e in result.errors)
