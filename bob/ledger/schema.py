"""JSON Schema for Plan Ledger events.

Validates a raw event dict against the Slice A envelope and the
payload schema for its declared ``type``. Events whose type is in
``RESERVED_EVENT_TYPES`` validate against their schema (so future
writers can emit them now and have them survive Slice A's check),
even though Slice A's projector ignores them.

This module exposes:

  - ``ENVELOPE_SCHEMA``: JSON Schema (Draft 2020-12) for the envelope
    fields excluding ``payload``.
  - ``PAYLOAD_SCHEMAS``: one JSON Schema per ``EventType`` for the
    payload object.
  - ``EVENT_SCHEMA``: combined schema using ``allOf`` to enforce both
    the envelope and (per-type) payload via ``oneOf`` discrimination
    on ``type``.
  - ``validate_event(raw)``: validate one event dict, raising
    ``EventSchemaError`` on failure. The error message points at the
    first JSON-pointer mismatch.
  - ``validate_event_id_format(value)``: separate runtime check that
    an envelope ``event_id`` is a well-formed UUIDv7. JSON Schema's
    ``format: uuid`` does not enforce the version nibble, so the
    validator's format checker delegates to this helper.

The schema is intentionally narrow: required fields are enumerated,
extra payload fields are rejected via ``additionalProperties: false``
on each type. Slice B may relax this for forward-compatible payload
extensions; Slice A keeps the contract tight.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from bob.ledger.events import (
    SCHEMA_VERSION,
    AssumptionConfidence,
    CommitChangeClass,
    EventType,
    is_well_formed_event_id,
)


class EventSchemaError(ValueError):
    """Raised when an event dict fails schema validation."""


# ---------------------------------------------------------------------
# Schema fragments
# ---------------------------------------------------------------------


def _string(*, min_length: int = 0) -> dict[str, Any]:
    return {"type": "string", "minLength": min_length}


def _int(*, minimum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _bool() -> dict[str, Any]:
    return {"type": "boolean"}


def _nullable_string(*, min_length: int = 1) -> dict[str, Any]:
    return {"type": ["string", "null"], "minLength": min_length}


def _nullable_bool() -> dict[str, Any]:
    return {"type": ["boolean", "null"]}


def _array_of(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item_schema}


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


_GIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "commit": _nullable_string(),
        "branch": _nullable_string(),
        "dirty": _nullable_bool(),
        "worktree": _nullable_string(),
    },
    "required": ["commit", "branch", "dirty", "worktree"],
}


_EVENT_TYPE_VALUES: list[str] = [t.value for t in EventType]


# ---------------------------------------------------------------------
# Envelope schema (without payload-shape constraints)
# ---------------------------------------------------------------------


ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://bob.local/ledger/event-envelope.json",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "event_id",
        "seq",
        "ts",
        "writer_id",
        "run_id",
        "schema_version",
        "type",
        "git",
        "payload",
    ],
    "properties": {
        "event_id": {
            "type": "string",
            "format": "ledger-event-id",
        },
        "seq": _int(minimum=0),
        "ts": {"type": "string", "format": "date-time"},
        "writer_id": _string(min_length=1),
        "run_id": _string(min_length=1),
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "type": _enum(_EVENT_TYPE_VALUES),
        "git": _GIT_SCHEMA,
        "payload": {"type": "object"},
    },
}


# ---------------------------------------------------------------------
# Per-type payload schemas
# ---------------------------------------------------------------------


def _phase_started_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "title", "goal", "predecessor_phase_ids"],
        "properties": {
            "phase_id": _string(min_length=1),
            "title": _string(min_length=1),
            "goal": _nullable_string(min_length=1),
            "predecessor_phase_ids": _array_of(_string(min_length=1)),
        },
    }


def _phase_completed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "commit_event_ids", "artifact_paths"],
        "properties": {
            "phase_id": _string(min_length=1),
            "commit_event_ids": _array_of(_string(min_length=1)),
            "artifact_paths": _array_of(_string(min_length=1)),
        },
    }


def _phase_abandoned_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "reason"],
        "properties": {
            "phase_id": _string(min_length=1),
            "reason": _string(min_length=1),
        },
    }


def _phase_blocked_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "reason", "blocker_event_id"],
        "properties": {
            "phase_id": _string(min_length=1),
            "reason": _string(min_length=1),
            "blocker_event_id": _nullable_string(),
        },
    }


def _phase_superseded_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "superseded_by_phase_id", "reason"],
        "properties": {
            "phase_id": _string(min_length=1),
            "superseded_by_phase_id": _string(min_length=1),
            "reason": _string(min_length=1),
        },
    }


def _phase_split_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["phase_id", "into_phase_ids", "reason"],
        "properties": {
            "phase_id": _string(min_length=1),
            "into_phase_ids": {
                "type": "array",
                "items": _string(min_length=1),
                "minItems": 2,
            },
            "reason": _string(min_length=1),
        },
    }


def _phase_merged_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["merged_phase_ids", "into_phase_id", "reason"],
        "properties": {
            "merged_phase_ids": {
                "type": "array",
                "items": _string(min_length=1),
                "minItems": 2,
            },
            "into_phase_id": _string(min_length=1),
            "reason": _string(min_length=1),
        },
    }


def _commit_landed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "commit",
            "parent_commits",
            "branch",
            "author",
            "subject",
            "attributed_phase_id",
            "files_changed",
            "lines_added",
            "lines_removed",
            "change_class",
        ],
        "properties": {
            "commit": _string(min_length=4),
            "parent_commits": _array_of(_string(min_length=4)),
            "branch": _nullable_string(),
            "author": _string(min_length=1),
            "subject": _string(min_length=1),
            "attributed_phase_id": _nullable_string(),
            "files_changed": _int(minimum=0),
            "lines_added": _int(minimum=0),
            "lines_removed": _int(minimum=0),
            "change_class": _enum([c.value for c in CommitChangeClass]),
            "touched_paths": _array_of(_string(min_length=1)),
        },
    }


def _test_failed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "test_id",
            "phase_id",
            "failure_kind",
            "summary",
            "transcript_ref",
        ],
        "properties": {
            "test_id": _string(min_length=1),
            "phase_id": _nullable_string(),
            "failure_kind": _string(min_length=1),
            "summary": _string(min_length=1),
            "transcript_ref": _nullable_string(),
        },
    }


def _finding_observed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "phase_id", "evidence_ref", "tags"],
        "properties": {
            "summary": _string(min_length=1),
            "phase_id": _nullable_string(),
            "evidence_ref": _nullable_string(),
            "tags": _array_of(_string(min_length=1)),
        },
    }


def _invariant_declared_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["invariant_id", "statement", "source", "phase_id"],
        "properties": {
            "invariant_id": _string(min_length=1),
            "statement": _string(min_length=1),
            "source": _string(min_length=1),
            "phase_id": _nullable_string(),
        },
    }


def _assumption_declared_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["assumption_id", "statement", "phase_id", "confidence"],
        "properties": {
            "assumption_id": _string(min_length=1),
            "statement": _string(min_length=1),
            "phase_id": _nullable_string(),
            "confidence": _enum([c.value for c in AssumptionConfidence]),
        },
    }


def _assumption_falsified_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["assumption_id", "evidence_event_id", "summary"],
        "properties": {
            "assumption_id": _string(min_length=1),
            "evidence_event_id": _string(min_length=1),
            "summary": _string(min_length=1),
        },
    }


def _work_observed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "phase_id", "evidence_ref"],
        "properties": {
            "summary": _string(min_length=1),
            "phase_id": _nullable_string(),
            "evidence_ref": _nullable_string(),
        },
    }


def _human_decision_recorded_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decision_id",
            "summary",
            "rationale",
            "decided_by",
            "applies_to_phase_ids",
        ],
        "properties": {
            "decision_id": _string(min_length=1),
            "summary": _string(min_length=1),
            "rationale": _string(min_length=1),
            "decided_by": _string(min_length=1),
            "applies_to_phase_ids": _array_of(_string(min_length=1)),
        },
    }


def _design_reasoning_recorded_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decision_id",
            "linked_event_id",
            "rationale",
            "constraints",
            "approaches_rejected",
        ],
        "properties": {
            "decision_id": _string(min_length=1),
            "linked_event_id": _string(min_length=1),
            "rationale": _string(min_length=1),
            "constraints": _array_of(_string(min_length=1)),
            "approaches_rejected": _array_of(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["approach", "reason"],
                    "properties": {
                        "approach": _string(min_length=1),
                        "reason": _string(min_length=1),
                    },
                }
            ),
        },
    }


def _threshold_crossed_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rule_id", "triggering_event_ids", "summary"],
        "properties": {
            "rule_id": _string(min_length=1),
            "triggering_event_ids": _array_of(_string(min_length=1)),
            "summary": _string(min_length=1),
        },
    }


def _plan_reauthored_payload() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "from_plan_commit",
            "to_plan_commit",
            "ledger_slice_event_ids",
            "trigger_event_id",
            "council_run_id",
        ],
        "properties": {
            "from_plan_commit": _string(min_length=4),
            "to_plan_commit": _string(min_length=4),
            "ledger_slice_event_ids": _array_of(_string(min_length=1)),
            "trigger_event_id": _string(min_length=1),
            "council_run_id": _nullable_string(),
        },
    }


PAYLOAD_SCHEMAS: dict[EventType, dict[str, Any]] = {
    EventType.PHASE_STARTED: _phase_started_payload(),
    EventType.PHASE_COMPLETED: _phase_completed_payload(),
    EventType.PHASE_ABANDONED: _phase_abandoned_payload(),
    EventType.PHASE_BLOCKED: _phase_blocked_payload(),
    EventType.PHASE_SUPERSEDED: _phase_superseded_payload(),
    EventType.PHASE_SPLIT: _phase_split_payload(),
    EventType.PHASE_MERGED: _phase_merged_payload(),
    EventType.COMMIT_LANDED: _commit_landed_payload(),
    EventType.TEST_FAILED: _test_failed_payload(),
    EventType.FINDING_OBSERVED: _finding_observed_payload(),
    EventType.INVARIANT_DECLARED: _invariant_declared_payload(),
    EventType.ASSUMPTION_DECLARED: _assumption_declared_payload(),
    EventType.ASSUMPTION_FALSIFIED: _assumption_falsified_payload(),
    EventType.WORK_OBSERVED: _work_observed_payload(),
    EventType.HUMAN_DECISION_RECORDED: _human_decision_recorded_payload(),
    EventType.DESIGN_REASONING_RECORDED: _design_reasoning_recorded_payload(),
    EventType.THRESHOLD_CROSSED: _threshold_crossed_payload(),
    EventType.PLAN_REAUTHORED: _plan_reauthored_payload(),
}


# ---------------------------------------------------------------------
# Combined event schema with type discrimination
# ---------------------------------------------------------------------


def _combined_schema() -> dict[str, Any]:
    """Build a top-level schema that enforces envelope + per-type payload.

    Uses ``allOf``: first the envelope shape, then a ``oneOf`` over
    type-discriminated payloads. Matching on ``type`` is done via
    ``if/then`` over each event-type value so a single event passes
    exactly one branch.
    """
    branches: list[dict[str, Any]] = []
    for event_type, payload_schema in PAYLOAD_SCHEMAS.items():
        branches.append(
            {
                "if": {
                    "properties": {"type": {"const": event_type.value}},
                    "required": ["type"],
                },
                "then": {
                    "properties": {"payload": payload_schema},
                    "required": ["payload"],
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://bob.local/ledger/event.json",
        "allOf": [ENVELOPE_SCHEMA, *branches],
    }


EVENT_SCHEMA: dict[str, Any] = _combined_schema()


# ---------------------------------------------------------------------
# Format checker for the custom UUIDv7 format
# ---------------------------------------------------------------------


_FORMAT_CHECKER = FormatChecker()


def _check_event_id(value: object) -> bool:
    return isinstance(value, str) and is_well_formed_event_id(value)


# jsonschema's FormatChecker.checks decorator is not typed in upstream
# stubs; register the format function imperatively to avoid the mypy
# untyped-decorator warning.
_FORMAT_CHECKER.checks("ledger-event-id", raises=())(_check_event_id)


_VALIDATOR: Draft202012Validator = Draft202012Validator(
    EVENT_SCHEMA, format_checker=_FORMAT_CHECKER
)


def validate_event(raw: dict[str, Any]) -> None:
    """Validate one event dict against envelope + payload schemas.

    Raises ``EventSchemaError`` with a human-readable pointer to the
    first failure on mismatch. Returns None on success.
    """
    try:
        _VALIDATOR.validate(raw)
    except ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "/"
        raise EventSchemaError(f"event invalid at {path}: {exc.message}") from exc


def iter_validation_errors(raw: dict[str, Any]) -> list[str]:
    """Return all validation errors as human-readable strings.

    Useful for tests and tooling that wants to surface every issue at
    once rather than the first one. Empty list means valid.
    """
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '/'}: {err.message}"
        for err in sorted(
            _VALIDATOR.iter_errors(raw),
            key=lambda e: list(e.absolute_path),
        )
    ]


def validate_event_id_format(value: str) -> None:
    """Standalone runtime check; useful when constructing events."""
    if not is_well_formed_event_id(value):
        raise EventSchemaError(
            f"event_id {value!r} is not a well-formed UUIDv7"
        )


# Re-export the third-party error so callers can catch via this module
# without importing jsonschema directly.
JsonSchemaValidationError = jsonschema.ValidationError


__all__ = [
    "ENVELOPE_SCHEMA",
    "EVENT_SCHEMA",
    "PAYLOAD_SCHEMAS",
    "EventSchemaError",
    "JsonSchemaValidationError",
    "iter_validation_errors",
    "validate_event",
    "validate_event_id_format",
]
