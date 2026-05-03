"""Per-invocation payload persistence.

Adapter payloads land on disk as JSON files under
``<run_dir>/payloads/`` so the log records that reference them by
``payload_ref`` stay small. The write helper is the single durability
boundary for the payload; the load helper is its inverse, used by
replay to hydrate envelopes when guards on resume need to consult
``state.payload.*`` values.

Both helpers strip keys starting with ``_`` so parser side-channel
fields never reach disk and never leak back into a guard's view.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from orchestra.errors import ResumeError


def strip_internal(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose names start with ``_`` (parser side-channel)."""
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def write_payload(
    payloads_dir: Path,
    run_id: str,
    seq: int,
    payload: dict[str, Any],
) -> str:
    """Persist the payload to disk with fsync. Returns the
    ``payload_ref`` (relative to the run directory) that the log
    record stores.
    """
    payloads_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payloads_dir / f"{run_id}-{seq}.json"
    with open(payload_path, "w", encoding="utf-8") as fh:
        json.dump(strip_internal(payload), fh, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return f"payloads/{run_id}-{seq}.json"


def load_payload(run_dir: Path, payload_ref: str) -> dict[str, Any]:
    """Inverse of ``write_payload``.

    A non-empty ``payload_ref`` is a contract that the file exists,
    decodes as JSON, and contains a JSON object. Any deviation
    (missing file, malformed JSON, non-object contents, or a path
    that escapes ``run_dir``) is durable corruption and raises
    ``ResumeError``. The "no payload" case is signalled by writing
    ``payload_ref: null`` to the log; resume callers must check that
    before invoking this helper rather than passing an empty string.
    """
    if not payload_ref:
        raise ResumeError(
            "load_payload requires a non-empty payload_ref; the "
            "absence of a payload must be signalled at the call site"
        )
    run_root = run_dir.resolve()
    payload_path = (run_dir / payload_ref).resolve()
    try:
        payload_path.relative_to(run_root)
    except ValueError as exc:
        raise ResumeError(
            f"payload_ref {payload_ref!r} resolves outside run "
            f"directory {run_root}"
        ) from exc
    try:
        with open(payload_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except FileNotFoundError as exc:
        raise ResumeError(
            f"payload file missing for payload_ref {payload_ref!r}: "
            f"{payload_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ResumeError(
            f"payload file for {payload_ref!r} is not valid JSON: "
            f"{exc.msg}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ResumeError(
            f"payload file for {payload_ref!r} must be a JSON object, "
            f"got {type(loaded).__name__}"
        )
    return loaded
