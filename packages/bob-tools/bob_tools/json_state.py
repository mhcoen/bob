"""Validated JSON persistence on local POSIX filesystems.

Reuse the planfile's fsync/replace and stable sidecar locking utilities.
Read-modify-write callers must use ``update_json_object``; atomic replacement
by itself only provides last-writer-wins publication. Callbacks must not nest
updates to the same path. Locks are advisory and do not protect against editors
or older code that writes without acquiring them.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bob_tools.planfile.fileio import _acquire_exclusive_lock, _atomic_write_text

JSONObject = dict[str, Any]
Validator = Callable[[JSONObject], None]


class StateError(RuntimeError):
    """State cannot be safely used. Keep the original file for recovery."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> JSONObject:
    result: JSONObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number {value}")


def read_json_object(path: Path) -> JSONObject | None:
    """Return None only for an absent file; malformed state raises StateError.

    Reads never rewrite or migrate. Duplicate keys and non-standard NaN/Infinity
    values are rejected instead of silently discarding information.
    """
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        if path.is_symlink():
            raise StateError(
                f"{path}: dangling state symlink; restore its target"
            ) from None
        return None
    try:
        data = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except (ValueError, UnicodeError) as exc:
        raise StateError(
            f"{path}: invalid state ({exc}); file preserved. "
            "Stop writers, keep a copy, and repair or restore a known-good backup."
        ) from exc
    return data


def atomic_write_json(path: Path, data: JSONObject) -> None:
    """Publish a complete JSON object; no read, schema check, or merge is implied.

    Serialize before touching disk. Preserve existing permission bits (new files
    are 0600). On pre-replace failure the old file remains; after replace a
    directory-fsync error can leave the new file visible despite raising. The
    caller must inspect it before retrying a non-idempotent operation. Filesystem
    support limits power-loss durability; see design/persistence.md.
    """
    if not isinstance(data, dict):
        raise TypeError("expected a JSON object")
    content = json.dumps(data, indent=2, allow_nan=False) + "\n"
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    _atomic_write_text(path, content, mode=mode)


def update_json_object(
    path: Path,
    operation: Callable[[JSONObject], JSONObject],
    *,
    validate: Validator | None = None,
) -> JSONObject:
    """Lock, read, validate, transform, validate, and atomically publish.

    The operation receives an empty dict for missing state, otherwise the latest
    committed object. It runs once, inside the lock. Schema owners may accept
    legacy input in validate and migrate it in operation. Existing invalid state
    is never replaced, and unknown fields survive if the operation preserves them.
    """
    # Detect broken symlinks before resolve() erases that distinction.
    if path.is_symlink() and not path.exists():
        raise StateError(f"{path}: dangling state symlink; restore its target")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _acquire_exclusive_lock(path):
        current = read_json_object(path)
        if current is not None and validate is not None:
            validate(current)
        updated = operation(current if current is not None else {})
        if validate is not None:
            validate(updated)
        atomic_write_json(path, updated)
        return updated
