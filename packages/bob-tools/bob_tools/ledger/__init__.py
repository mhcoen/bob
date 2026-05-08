"""Plan Ledger.

Append-only event log + deterministic state projection for Bob's plan
lifecycle. Slice A ships the event schema and validators; Slice B-D
build threshold rules, re-author mode, and McLoop pause-on-threshold
on top.

See ``bob/design/plan-ledger.md`` (in the sibling ``bob`` repo) for
the design doc, and ``bob_tools/ledger/SCHEMA.md`` for the
human-readable schema reference.
"""

from __future__ import annotations

from bob_tools.ledger.events import (
    ACTIVE_EVENT_TYPES,
    PAYLOAD_BUILDERS,
    RESERVED_EVENT_TYPES,
    SCHEMA_VERSION,
    AssumptionConfidence,
    CommitChangeClass,
    Event,
    EventType,
    GitSnapshot,
    RejectedApproach,
    is_well_formed_event_id,
)
from bob_tools.ledger.schema import (
    ENVELOPE_SCHEMA,
    EVENT_SCHEMA,
    PAYLOAD_SCHEMAS,
    EventSchemaError,
    iter_validation_errors,
    validate_event,
    validate_event_id_format,
)

__all__ = [
    "ACTIVE_EVENT_TYPES",
    "ENVELOPE_SCHEMA",
    "EVENT_SCHEMA",
    "PAYLOAD_BUILDERS",
    "PAYLOAD_SCHEMAS",
    "RESERVED_EVENT_TYPES",
    "SCHEMA_VERSION",
    "AssumptionConfidence",
    "CommitChangeClass",
    "Event",
    "EventSchemaError",
    "EventType",
    "GitSnapshot",
    "RejectedApproach",
    "is_well_formed_event_id",
    "iter_validation_errors",
    "validate_event",
    "validate_event_id_format",
]
