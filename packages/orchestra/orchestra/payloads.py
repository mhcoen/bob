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
    """Inverse of ``write_payload``. Returns an empty dict when the
    payload file is missing (e.g., a cancelled state never wrote a
    payload) or the contents are not a JSON object.
    """
    payload_path = run_dir / payload_ref
    try:
        with open(payload_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded
