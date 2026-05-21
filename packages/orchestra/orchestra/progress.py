"""Per-state progress reporting for the verb-style CLI, the REPL, and
the library API.

The executor surfaces ``state_enter``, ``state_exit``,
``actor_progress``, ``fan_out_start``, ``fan_out_progress``, and
``fan_out_end`` events to a
``ProgressCallback`` whenever one is supplied via
``run_workflow(progress_callback=...)``. Library callers get the same
default-on stderr reporting as the CLI: ``run_workflow`` installs
``stderr_reporter()`` unless the caller explicitly passes
``quiet=True`` (or ``progress_callback=None`` to suppress without
opting in to a custom reporter).

Sequential states render one line per phase:

    [1/7] framer (claude_code_text:opus) ... starting
    [1/7] framer (claude_code_text:opus) ... still running, 15.0s elapsed
    [1/7] framer (claude_code_text:opus) ... done in 3.2s

Parallel groups (fan-out) render a header listing every child binding
up front, individual completion lines as each child finishes (in
completion order), and a closing summary line whose elapsed value is
the longest individual child duration (parallel wall-clock), not the
sum:

    [2-6/7] 5 advisors starting in parallel:
       contrarian (claude_code_text:kimi-k2.6)
       first_principles (claude_code_text:opus)
       expansionist (claude_code_text:sonnet)
       outsider (claude_code_text:kimi-k2.6)
       executor_lens (claude_code_text:opus)
    [2-6/7] contrarian done in 4.1s
    [2-6/7] expansionist done in 4.8s
    [2-6/7] all 5 still running, 30.0s elapsed
    ...
    [2-6/7] all 5 done, parallel wall-clock 6.3s

The "done" lines do not overwrite the "starting" lines because
adapter-side progress dots and other interleaved output would corrupt
cursor position.

Stdout is reserved for the workflow's final answer so piping
``orchestra ask "..." | something`` keeps working.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class ChildBinding:
    """One child of a fan-out group, enriched with its resolved
    adapter and model so the reporter can render the parallel header
    without a second lookup."""

    state_name: str
    role: str | None
    adapter: str | None
    model: str | None


@dataclass(frozen=True)
class ProgressEvent:
    """One progress notification surfaced by the executor.

    ``kind`` is one of ``"state_enter"``, ``"state_exit"``,
    ``"actor_progress"``, ``"fan_out_start"``,
    ``"fan_out_progress"``, or ``"fan_out_end"``. For sequential
    states ``index`` is the 1-based ordinal of the state's first
    entry (retries reuse the same index) and ``total`` is the count
    of declared states in the workflow. For ``fan_out_start`` the
    ``index`` is the 1-based start of the child range and
    ``children`` lists every child in dispatch order with adapter
    and model resolved. ``elapsed_seconds`` is ``None`` for
    ``state_enter`` and ``fan_out_start``, the per-state wall-clock
    duration for ``state_exit``, elapsed actor-invoke time for
    ``actor_progress``, elapsed parallel-block time for
    ``fan_out_progress``, and ``None`` for ``fan_out_end`` (the
    reporter computes the parallel wall-clock from accumulated child
    durations).
    """

    kind: str
    state_name: str
    role: str | None
    adapter: str | None
    model: str | None
    index: int
    total: int
    elapsed_seconds: float | None
    children: tuple[ChildBinding, ...] | None = None


ProgressCallback = Callable[[ProgressEvent], None]
"""Signature the executor calls per progress event."""


def _format_backing(adapter: str | None, model: str | None) -> str:
    """Render the ``(adapter:model)`` backing label.

    Collapses to just ``adapter`` when no model is configured (e.g.
    a transform state). Falls back to a literal ``"transform"`` when
    neither is set so the line still reads cleanly.
    """
    if adapter and model:
        return f"{adapter}:{model}"
    if adapter:
        return adapter
    if model:
        return model
    return "transform"


def format_event(event: ProgressEvent) -> str:
    """Render one ``ProgressEvent`` as a single line.

    Used by the stateless reporter test cases and by callers that want
    one-shot rendering of a sequential event. Parallel groups span
    multiple lines and are rendered by ``_StatefulStderrReporter``,
    which holds the necessary cross-event state.
    """
    role_label = event.role or event.state_name
    backing = _format_backing(event.adapter, event.model)
    counter = f"[{event.index}/{event.total}]"
    head = f"{counter} {role_label} ({backing}) ..."
    if event.kind == "state_enter":
        return f"{head} starting"
    if event.kind == "state_exit":
        if event.elapsed_seconds is None:
            return f"{head} done"
        return f"{head} done in {event.elapsed_seconds:.1f}s"
    if event.kind == "actor_progress":
        elapsed = event.elapsed_seconds or 0.0
        return f"{head} still running, {elapsed:.1f}s elapsed"
    if event.kind == "fan_out_progress":
        elapsed = event.elapsed_seconds or 0.0
        return f"{head} fan-out still running, {elapsed:.1f}s elapsed"
    return f"{head} {event.kind}"


class _StatefulStderrReporter:
    """Stateful reporter that handles sequential AND parallel formats.

    Maintains a single open parallel block at a time. Events that
    arrive while a fan-out group is in flight are rendered against
    the open block's range counter; the block closes on
    ``fan_out_end`` with a summary line whose elapsed value is the
    longest individual child duration (parallel wall-clock).
    """

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._parallel_active = False
        self._parallel_start: int = 0
        self._parallel_end: int = 0
        self._parallel_total: int = 0
        self._parallel_count: int = 0
        self._parallel_max_elapsed: float = 0.0

    def __call__(self, event: ProgressEvent) -> None:
        with self._lock:
            if event.kind == "fan_out_start":
                self._handle_fan_out_start(event)
            elif event.kind == "fan_out_end":
                self._handle_fan_out_end(event)
            elif event.kind == "fan_out_progress":
                self._handle_fan_out_progress(event)
            elif event.kind == "state_enter":
                self._handle_state_enter(event)
            elif event.kind == "state_exit":
                self._handle_state_exit(event)
            elif event.kind == "actor_progress":
                self._handle_actor_progress(event)

    # ----- handlers --------------------------------------------------

    def _handle_fan_out_start(self, event: ProgressEvent) -> None:
        children = event.children or ()
        count = len(children)
        start = event.index
        end = start + count - 1 if count > 0 else start
        self._parallel_active = True
        self._parallel_start = start
        self._parallel_end = end
        self._parallel_total = event.total
        self._parallel_count = count
        self._parallel_max_elapsed = 0.0
        label = event.role or event.state_name
        # The header names the parallel group by the parent role (or
        # the parent state name if the parent has none) plus the
        # number of children. Listing every child binding up front
        # gives the user the full picture before any child finishes.
        header_label = f"{count} {label} children"
        self._print(
            f"[{start}-{end}/{event.total}] "
            f"{header_label} starting in parallel:"
        )
        for child in children:
            backing = _format_backing(child.adapter, child.model)
            child_label = child.role or child.state_name
            self._print(f"   {child_label} ({backing})")

    def _handle_fan_out_end(self, event: ProgressEvent) -> None:
        if not self._parallel_active:
            # Defensive: a fan_out_end without a matching open block
            # means the executor surfaced an unexpected event. Print a
            # single diagnostic line and reset state.
            self._print(
                f"[?/{event.total}] fan_out_end without open block "
                f"for {event.state_name!r}"
            )
            return
        start = self._parallel_start
        end = self._parallel_end
        total = self._parallel_total
        n = self._parallel_count
        wall = self._parallel_max_elapsed
        self._print(
            f"[{start}-{end}/{total}] all {n} done, "
            f"parallel wall-clock {wall:.1f}s"
        )
        self._parallel_active = False
        self._parallel_start = 0
        self._parallel_end = 0
        self._parallel_total = 0
        self._parallel_count = 0
        self._parallel_max_elapsed = 0.0

    def _handle_fan_out_progress(self, event: ProgressEvent) -> None:
        elapsed = event.elapsed_seconds or 0.0
        if not self._parallel_active:
            self._print(
                f"[?/{event.total}] fan_out_progress without open block "
                f"for {event.state_name!r}, {elapsed:.1f}s elapsed"
            )
            return
        self._print(
            f"[{self._parallel_start}-{self._parallel_end}/"
            f"{self._parallel_total}] all {self._parallel_count} "
            f"still running, {elapsed:.1f}s elapsed"
        )

    def _handle_state_enter(self, event: ProgressEvent) -> None:
        if self._parallel_active:
            # Per-child "starting" lines are suppressed; the
            # fan_out_start header already listed every child.
            return
        backing = _format_backing(event.adapter, event.model)
        label = event.role or event.state_name
        self._print(
            f"[{event.index}/{event.total}] {label} ({backing}) "
            "... starting"
        )

    def _handle_state_exit(self, event: ProgressEvent) -> None:
        elapsed = event.elapsed_seconds or 0.0
        if self._parallel_active:
            label = event.role or event.state_name
            if elapsed > self._parallel_max_elapsed:
                self._parallel_max_elapsed = elapsed
            self._print(
                f"[{self._parallel_start}-{self._parallel_end}/"
                f"{self._parallel_total}] {label} done in "
                f"{elapsed:.1f}s"
            )
            return
        backing = _format_backing(event.adapter, event.model)
        label = event.role or event.state_name
        if event.elapsed_seconds is None:
            self._print(
                f"[{event.index}/{event.total}] {label} ({backing}) "
                "... done"
            )
            return
        self._print(
            f"[{event.index}/{event.total}] {label} ({backing}) "
            f"... done in {event.elapsed_seconds:.1f}s"
        )

    def _handle_actor_progress(self, event: ProgressEvent) -> None:
        if self._parallel_active:
            # Fan-out children are expected to be suppressed by the
            # executor. Keep the reporter defensive so one stray child
            # event cannot corrupt the parallel block format.
            return
        backing = _format_backing(event.adapter, event.model)
        label = event.role or event.state_name
        elapsed = event.elapsed_seconds or 0.0
        self._print(
            f"[{event.index}/{event.total}] {label} ({backing}) "
            f"... still running, {elapsed:.1f}s elapsed"
        )

    # ----- io --------------------------------------------------------

    def _print(self, line: str) -> None:
        print(line, file=self._stream, flush=True)


def stderr_reporter(stream: IO[str] | None = None) -> ProgressCallback:
    """Return a stateful reporter that prints each event to ``stream``.

    Defaults to ``sys.stderr``. The returned callable is safe to call
    from multiple threads (the underlying state is lock-guarded), so
    fan-out worker threads that surface state_exit events can share
    one reporter with the controller thread.
    """
    target = stream if stream is not None else sys.stderr
    return _StatefulStderrReporter(target)


def silent_reporter() -> ProgressCallback:
    """Return a callback that drops every event.

    Equivalent to passing ``progress_callback=None`` from the
    executor's perspective. Useful when a caller wants to express
    explicit suppression rather than thread ``None`` through several
    layers (the CLI uses this for ``--quiet``).
    """

    def _drop(_event: ProgressEvent) -> None:
        return None

    return _drop
