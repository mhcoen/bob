"""Task timings retain failed phases and identify observed approval waits."""

import pytest
from bob_tools import timing


def test_nested_same_phase_is_not_double_counted(monkeypatch):
    timing.start_task()
    clock = iter([10.0, 15.0])
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(clock))
    with timing.measure("checks"):
        with timing.measure("checks"):
            pass
    assert timing.snapshot() == {"checks": 5.0}


def test_failed_phase_and_approval_wait_are_recorded(monkeypatch):
    timing.start_task()
    clock = iter([10.0, 18.0])
    monkeypatch.setattr(timing.time, "monotonic", lambda: next(clock))
    wait_clock = iter([1.0, 4.0, 6.0])
    monkeypatch.setattr(timing.time, "perf_counter", lambda: next(wait_clock))

    @timing.timed("editor")
    @timing.observe_approvals
    def editor():
        timing.approval_waiting(True)
        timing.approval_waiting(False)
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError):
        editor()
    assert timing.snapshot() == {"editor": 8.0, "approval_wait": 3.0}
    timing.start_task()
    assert timing.snapshot() == {}
