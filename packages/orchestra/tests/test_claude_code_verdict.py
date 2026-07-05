"""Regression tests for the shared exit-code-to-verdict mapping.

``run_session`` (adapters/_subprocess.py) exit-codes a wall-clock
overrun with ``TIMEOUT_KILL_EXIT`` (-102) and a stuck session with
``IDLE_KILL_EXIT`` (-103) after ``IDLE_TIMEOUT_S``. Both are genuine
timeouts and must map to the ``timeout`` verdict so upstream retry
logic treats them alike. The sentinels live OUTSIDE the kernel signal
range on purpose: Popen reports a child killed by signal N as ``-N``,
so the old ``-2``/``-3`` sentinels collided with real SIGINT/SIGQUIT
deaths and misclassified externally-killed runs as retryable timeouts.
A caller-initiated interrupt (130) is deliberately kept as ``error`` so
a run the caller aborted is not retried.

The mapping is one shared function (all four adapters import it), so
one test covers every adapter; the per-adapter byte-identical copies
this replaces were exactly the drift vector the dedup removed.

No live CLI is invoked here; only the pure mapping function is
exercised.
"""

from __future__ import annotations

from orchestra.adapters._subprocess import (
    IDLE_KILL_EXIT,
    TIMEOUT_KILL_EXIT,
    verdict_for_exit_code,
)


def test_verdict_mapping() -> None:
    assert verdict_for_exit_code(0) == "complete"
    assert verdict_for_exit_code(TIMEOUT_KILL_EXIT) == "timeout"
    # The idle-kill sentinel is a genuine timeout, not a hard error.
    assert verdict_for_exit_code(IDLE_KILL_EXIT) == "timeout"
    assert verdict_for_exit_code(1) == "error"
    # 130 (interrupt) is a deliberate caller-initiated cancel, kept as
    # error so it is not retried as a timeout would be.
    assert verdict_for_exit_code(130) == "error"


def test_real_signal_deaths_are_not_timeouts() -> None:
    # A child killed externally by SIGINT (-2) or SIGQUIT (-3) is an
    # error, NOT one of run_session's own kills: retrying a
    # deliberately killed run is wrong. This is the collision the
    # non-signal-range sentinels exist to prevent.
    assert verdict_for_exit_code(-2) == "error"
    assert verdict_for_exit_code(-3) == "error"
    assert verdict_for_exit_code(-9) == "error"


def test_sentinels_are_outside_signal_range() -> None:
    # Popen returncode for a signal-killed child is -signum; signals
    # top out well below 100 on every supported platform.
    assert TIMEOUT_KILL_EXIT < -64
    assert IDLE_KILL_EXIT < -64
