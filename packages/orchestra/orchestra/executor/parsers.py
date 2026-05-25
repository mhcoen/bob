"""Result parsers registered by the core (slice 1)."""

from __future__ import annotations

from orchestra.errors import AdapterError
from orchestra.registry.registry import ResultParser
from orchestra.spine import Envelope


def _identity_text_parse_fn(envelope: Envelope, _store: object) -> list[tuple[str, object]]:
    """Identity model-output parser.

    For each declared write of type ``text``, emit one (name, value)
    pair with the model payload's ``output`` field.

    The parser is registered against the model backing with an
    artifact-type filter of ``text``. It is the simplest possible
    parser and is sufficient for slice 1.
    """
    payload = envelope.payload or {}
    output = payload.get("output")
    if not isinstance(output, str):
        raise AdapterError(
            "identity_text_parser: model payload missing 'output' field"
        )
    # The executor passes the list of declared writes via a side channel
    # on the envelope (envelope-internal field). We use the same channel
    # to know what writes to produce. See executor._dispatch_parsers.
    declared_writes = (envelope.payload or {}).get("_declared_writes", [])
    out: list[tuple[str, object]] = []
    for w in declared_writes:
        if w.get("type") == "text":
            out.append((w["name"], output))
    return out


identity_text_parser = ResultParser(
    name="identity_text",
    backing_filter=("model",),
    artifact_type_filter=("text",),
    fn=_identity_text_parse_fn,
)
