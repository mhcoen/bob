"""UUIDv7 generator.

Implements the UUIDv7 layout from RFC 9562: a 48-bit Unix milliseconds
timestamp, a 4-bit version field set to 7, a 12-bit sub-millisecond
randomness field, a 2-bit variant set to ``10``, and a 62-bit
randomness field. The string form sorts roughly by emit time, which is
why the Plan Ledger uses it as the primary replay key. Within a single
millisecond two emits produce different IDs because the trailing 74
random bits collide only with vanishing probability.

This is a small local implementation rather than a dependency on
``uuid_extensions`` or similar so the ledger can be vendored without
extra wheels. It is monotonic only across processes that read the wall
clock; concurrent emits on different writers can produce out-of-order
IDs across writers, which is exactly what the (writer_id, seq)
tiebreaker in the envelope is for.
"""

from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_last_ms = -1
_last_seq12 = -1


def uuid7() -> str:
    """Return a new UUIDv7 as a canonical hyphenated hex string."""
    global _last_ms, _last_seq12
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms:
            seq12 = (_last_seq12 + 1) & 0xFFF
            if seq12 == 0:
                # Sequence wrapped within the same millisecond; bump
                # the timestamp by one to preserve monotonic order
                # within a single process.
                now_ms += 1
        else:
            seq12 = int.from_bytes(os.urandom(2), "big") & 0xFFF
        _last_ms = now_ms
        _last_seq12 = seq12

        rand62 = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    # Pack the fields into 128 bits.
    ts48 = now_ms & ((1 << 48) - 1)
    raw = (
        (ts48 << 80)
        | (0x7 << 76)
        | (seq12 << 64)
        | (0x2 << 62)
        | rand62
    )
    hex_str = f"{raw:032x}"
    return (
        f"{hex_str[0:8]}-{hex_str[8:12]}-{hex_str[12:16]}-"
        f"{hex_str[16:20]}-{hex_str[20:32]}"
    )


def is_uuid7(value: str) -> bool:
    """Return True if *value* is a canonical UUIDv7 hex string.

    Only the version nibble (``7``) and variant bits (``10``) are
    checked; the implementation does not verify time monotonicity
    against any earlier observation.
    """
    if len(value) != 36:
        return False
    if value[8] != "-" or value[13] != "-" or value[18] != "-" or value[23] != "-":
        return False
    hex_chars = value.replace("-", "")
    if len(hex_chars) != 32:
        return False
    try:
        bits = int(hex_chars, 16)
    except ValueError:
        return False
    version = (bits >> 76) & 0xF
    variant = (bits >> 62) & 0x3
    return version == 0x7 and variant == 0x2
