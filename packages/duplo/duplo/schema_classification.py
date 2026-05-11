"""Classify orchestra schema-validation failures by failure kind.

Orchestra's executor emits a ``schema_validation`` log record when
the synthesizer's verdict fails to validate against the artifact
schema. The ``fields.validation_errors`` array carries one or more
human-readable error strings produced by
``orchestra.schema._validate_value_against_schema`` and
``_validate_object``. Those strings follow stable, documented
shapes (see ``orchestra/schema.py``):

  - ``"<path>: additional property not permitted"``
  - ``"<path>: required field missing"``
  - ``"<path>: value <repr> is not in enum [...]"``
  - ``"<path>: expected <type>, got <type>"``

This module reads ``log.jsonl`` for a council run that failed at
the synthesizer state, surfaces those ``schema_validation`` records,
and classifies the violations into a small enum that Duplo's
re-author retry path consumes. The retry loop shares its budget
with lineage validation (``max_attempts=2``); the classifier names
the failure kinds so the feedback threaded into
``previous_attempt_error`` can be tailored.

The kinds are deliberately the four documented schema rejections plus
``JSON_PARSE`` (json parse error, written by the executor with
``outcome="parse_error"``) and ``OTHER`` (anything that does not
match the four documented patterns). All kinds are treated as
retryable; the kind influences only the feedback message and the
audit surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SchemaFailureKind(str, Enum):
    """The kind of orchestra schema-validation failure.

    The string values are the audit-surface tags (used in
    HardStop detail and operator logs); they MUST stay stable
    because they are referenced by ledger-pause tests.
    """

    ADDITIONAL_PROPERTIES = "additional_properties"
    ENUM_MISMATCH = "enum_mismatch"
    MISSING_REQUIRED = "missing_required"
    MALFORMED_ARRAY = "malformed_array"
    JSON_PARSE = "json_parse"
    OTHER = "other"


@dataclass(frozen=True)
class SchemaFailure:
    """One ``schema_validation`` failure record from log.jsonl."""

    state_id: str
    attempt: int
    outcome: str
    artifact: str
    validation_errors: tuple[str, ...]


@dataclass(frozen=True)
class SchemaClassification:
    """Result of classifying a collection of validation_errors.

    ``primary`` is the kind chosen for the audit surface (the first
    classified kind encountered in the validation_errors list, in
    log order — this matches the order orchestra emits them).

    ``kinds`` is the full set of kinds present across all errors;
    a single verdict commonly carries multiple unrelated
    violations and the retry feedback names every kind so the
    model can address them all at once.

    ``errors`` retains the raw error strings for verbatim inclusion
    in the retry feedback (the model gets the human-readable text,
    not just the kind tag).
    """

    primary: SchemaFailureKind
    kinds: frozenset[SchemaFailureKind]
    errors: tuple[str, ...] = field(default_factory=tuple)


# Substring matchers that recognize the four documented schema
# rejections plus json-parse. Order matters: the first matcher that
# hits names the kind. Multiple matchers may hit the same error
# string; the substring tests are intentionally disjoint for the
# four core kinds (see orchestra/schema.py), so the order is mostly
# decorative — but kept stable to lock the precedence in case a
# future error format introduces overlap.
_KIND_SUBSTRINGS: tuple[tuple[SchemaFailureKind, str], ...] = (
    (SchemaFailureKind.ADDITIONAL_PROPERTIES, "additional property not permitted"),
    (SchemaFailureKind.MISSING_REQUIRED, "required field missing"),
    (SchemaFailureKind.ENUM_MISMATCH, "is not in enum"),
    (SchemaFailureKind.MALFORMED_ARRAY, "expected array, got"),
)


def _classify_one(error: str) -> SchemaFailureKind:
    """Classify a single error string from orchestra's validator.

    Returns the first kind whose recognizer substring matches.
    Falls back to :data:`SchemaFailureKind.OTHER` when no kind
    matches; the retry path still surfaces the raw string so the
    operator has the information even when the classifier doesn't.
    """
    for kind, needle in _KIND_SUBSTRINGS:
        if needle in error:
            return kind
    # Type-mismatch errors that are not "expected array" land in
    # OTHER deliberately. A non-array type mismatch is a different
    # failure mode (the synthesizer emitted a scalar where an
    # object was expected, etc.) and naming it "malformed_array"
    # would mislead the operator.
    return SchemaFailureKind.OTHER


def classify_schema_failure(
    validation_errors: list[str] | tuple[str, ...],
    *,
    outcome: str = "schema_error",
) -> SchemaClassification:
    """Classify the validation_errors emitted by orchestra.

    When ``outcome == "parse_error"``, the executor never invoked
    schema validation: the model's output was not valid JSON. We
    return :data:`SchemaFailureKind.JSON_PARSE` regardless of the
    error text, so the retry feedback can prompt the model to fix
    the JSON envelope before re-attempting structural validation.

    When ``outcome == "schema_error"``, every error string is
    classified and the primary kind is the first classified kind
    (in log order). The full set of present kinds is exposed via
    ``kinds`` so the caller can build a multi-kind feedback message.
    """
    errs = tuple(validation_errors)
    if outcome == "parse_error":
        return SchemaClassification(
            primary=SchemaFailureKind.JSON_PARSE,
            kinds=frozenset({SchemaFailureKind.JSON_PARSE}),
            errors=errs,
        )
    if not errs:
        return SchemaClassification(
            primary=SchemaFailureKind.OTHER,
            kinds=frozenset({SchemaFailureKind.OTHER}),
            errors=errs,
        )
    per_error = [_classify_one(e) for e in errs]
    return SchemaClassification(
        primary=per_error[0],
        kinds=frozenset(per_error),
        errors=errs,
    )


def read_schema_validation_failures(log_path: Path) -> list[SchemaFailure]:
    """Read ``log.jsonl`` for an orchestra council run, return every
    ``schema_validation`` record whose outcome is NOT ``valid``.

    The log writer fsyncs after each record, so a partial final line
    after a crash is the only tolerated malformation. We skip a
    final unterminated line silently — schema validation failures
    that crash mid-write are vanishingly rare and the orchestra
    LogReader's strict mode would refuse to surface the failure at
    all if we required full integrity.

    Records are returned in log order so the caller's notion of
    "the first attempt's schema failure" matches the log timeline.
    """
    if not log_path.exists():
        return []
    failures: list[SchemaFailure] = []
    with open(log_path, encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                # Truncated final record. Stop reading; preceding
                # records are already collected.
                break
            if not isinstance(rec, dict):
                continue
            if rec.get("event") != "schema_validation":
                continue
            outcome = rec.get("outcome")
            if outcome == "valid" or outcome is None:
                continue
            raw_errors = rec.get("validation_errors")
            if not isinstance(raw_errors, list):
                raw_errors = []
            failures.append(
                SchemaFailure(
                    state_id=str(rec.get("state_id") or ""),
                    attempt=int(rec.get("attempt") or 0),
                    outcome=str(outcome),
                    artifact=str(rec.get("artifact") or ""),
                    validation_errors=tuple(str(e) for e in raw_errors),
                )
            )
    return failures


__all__ = [
    "SchemaClassification",
    "SchemaFailure",
    "SchemaFailureKind",
    "classify_schema_failure",
    "read_schema_validation_failures",
]
