"""Regression tests for the vendored UUIDv7 generator.

The projector uses ``event_id`` as the *primary* replay-ordering key, so
the generator must never emit an id whose 48-bit timestamp prefix sorts
before a previously emitted id, even when the wall clock steps backward
(NTP) or the 12-bit sub-millisecond counter wraps within one millisecond.
Because the ids are zero-padded 32-char hex strings, lexicographic string
order equals numeric order, so ``sorted()`` is a valid monotonicity check.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from bob_tools.ledger import _uuid7
from bob_tools.ledger._uuid7 import is_uuid7, uuid7


@pytest.fixture(autouse=True)
def _reset_generator_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the module-global monotonic state for each test."""
    monkeypatch.setattr(_uuid7, "_last_ms", -1, raising=False)
    monkeypatch.setattr(_uuid7, "_last_seq12", -1, raising=False)


def _patch_clock(monkeypatch: pytest.MonkeyPatch, seconds: list[float]) -> None:
    """Make ``time.time`` yield the given values, one per uuid7() call."""
    it = iter(seconds)
    monkeypatch.setattr(_uuid7.time, "time", lambda: next(it))


def test_backward_clock_step_still_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ten emits at one instant, then the clock jumps back a full second.
    # Without the max(now_ms, _last_ms) clamp the post-jump ids carry a
    # smaller timestamp prefix and sort before the earlier ones.
    seconds = [1_700_000.000] * 10 + [1_699_999.000] * 10
    _patch_clock(monkeypatch, seconds)

    ids = [uuid7() for _ in range(len(seconds))]

    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(is_uuid7(i) for i in ids)


def test_same_millisecond_sequence_wrap_stays_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A constant clock forces the same-ms path for every call, so the
    # 12-bit counter wraps (4096) at least once and the generator must
    # bump the timestamp to preserve strictly increasing ids.
    count = 5000
    _patch_clock(monkeypatch, [1_700_000.000] * count)

    ids = [uuid7() for _ in range(count)]

    assert ids == sorted(ids)
    # Strictly increasing: (timestamp, seq) is unique per emit, and those
    # bits dominate the trailing randomness.
    assert all(a < b for a, b in pairwise(ids))
    assert len(set(ids)) == count
