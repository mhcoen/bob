"""Elapsed task phases. Editor time includes its subprocesses and provider waits."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
_current: ContextVar[dict[str, float] | None] = ContextVar("task_timing", default=None)
_active: ContextVar[frozenset[str]] = ContextVar("timed_phases", default=frozenset())


def start_task() -> None:
    _current.set({})
    _active.set(frozenset())


def snapshot() -> dict[str, float]:
    return {key: round(value, 3) for key, value in (_current.get() or {}).items()}


@contextmanager
def measure(phase: str) -> Iterator[None]:
    values = _current.get()
    if values is None or phase in _active.get():
        yield
        return
    token = _active.set(_active.get() | {phase})
    started = time.monotonic()
    try:
        yield
    finally:
        values[phase] = values.get(phase, 0.0) + time.monotonic() - started
        _active.reset(token)


def timed(phase: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with measure(phase):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def report() -> None:
    values = snapshot()
    if values:
        labels = {
            "editor": "editor (includes tools/waits)",
            "approval_wait": "observed approval wait (within editor)",
        }
        print(
            "\n>>> Task timing: "
            + "; ".join(
                f"{labels.get(key, key)} {value:.1f}s" for key, value in values.items()
            ),
            flush=True,
        )


class _ApprovalWait:
    def __init__(self) -> None:
        self.started: float | None = None
        self.elapsed = 0.0

    def observe(self, waiting: bool) -> None:
        now = time.perf_counter()
        if waiting and self.started is None:
            self.started = now
        elif not waiting and self.started is not None:
            self.elapsed += now - self.started
            self.started = None


_wait: ContextVar[_ApprovalWait | None] = ContextVar("approval_wait", default=None)


def approval_waiting(waiting: bool) -> None:
    tracker = _wait.get()
    if tracker is not None:
        tracker.observe(waiting)


def observe_approvals[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        tracker = _ApprovalWait()
        token = _wait.set(tracker)
        try:
            return function(*args, **kwargs)
        finally:
            tracker.observe(False)
            values = _current.get()
            if values is not None:
                values["approval_wait"] = (
                    values.get("approval_wait", 0.0) + tracker.elapsed
                )
            _wait.reset(token)

    return wrapped
