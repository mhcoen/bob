"""Progress reporting + actor-invocation watchdog wiring."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from orchestra.adapters.base import Adapter
from orchestra.spine import (
    Envelope,
    PreparedInvocation,
    StateDecl,
)


class _ProgressMixin:
    """Mixin: Progress reporting + actor-invocation watchdog wiring."""

    # ----- helpers ------------------------------------------------

    def _emit_state_exit_hook(
        self,
        state: StateDecl,
        envelope: Envelope,
        payload_ref: str | None,
    ) -> None:
        """Notify the optional ``on_state_exit`` callback.

        Called from inside the three state_exit-producing paths
        (``_run_one_state``, ``_execute_state_body``,
        ``_execute_transform_body``) AFTER the state_exit log record
        has been durably written. Provides a hook for consumers that
        want to maintain a curated incremental view of the run (e.g.
        the api's transcript.jsonl). Exceptions are swallowed so a
        misbehaving writer cannot abort an in-flight run.
        """
        if self._on_state_exit_callback is None:
            return
        try:
            self._on_state_exit_callback(state, envelope, payload_ref)
        except Exception:
            pass

    def _emit_progress(
        self,
        kind: str,
        state: StateDecl,
        elapsed_seconds: float | None = None,
        *,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        """Surface a progress event to the optional callback.

        Assigns a stable 1-based index per state name on first entry
        so retries reuse the same index. For ``fan_out_start`` the
        index reported is the next-available index, which the api
        wrapper renders as the start of a [N-M/total] range. Safe to
        call from worker threads (the index map is guarded). If no
        callback is set, this is a no-op.
        """
        if self._progress_callback is None:
            return
        with self._progress_lock:
            index = self._progress_state_indices.get(state.name)
            if index is None:
                index = len(self._progress_state_indices) + 1
                self._progress_state_indices[state.name] = index
            # For a fan-out group, pre-assign indices to every child
            # so subsequent child state_enter events reuse the same
            # range and the parallel block stays coherent.
            if kind == "fan_out_start" and children is not None:
                next_index = max(self._progress_state_indices.values(), default=0) + 1
                child_start = next_index
                for child_name, _child_role in children:
                    if child_name not in self._progress_state_indices:
                        self._progress_state_indices[child_name] = next_index
                        next_index += 1
                # Report the child range start as the event index so
                # the reporter can render [child_start-child_end/total].
                index = child_start
                self._progress_fan_out_range_starts[state.name] = child_start
            elif kind == "fan_out_progress":
                index = self._progress_fan_out_range_starts.get(state.name, index)
        try:
            self._progress_callback(
                kind,
                state.name,
                state.role,
                index,
                self._progress_total,
                elapsed_seconds,
                children,
            )
        except Exception:
            # The progress callback is for UX only. A misbehaving
            # reporter must never abort an in-flight run.
            pass

    def _start_progress_watchdog(
        self,
        kind: str,
        state: StateDecl,
        interval_seconds: float,
    ) -> Callable[[], None]:
        if self._progress_callback is None or interval_seconds <= 0:
            return lambda: None
        started_perf = time.perf_counter()

        def _emit() -> None:
            elapsed = max(0.0, time.perf_counter() - started_perf)
            self._emit_progress(kind, state, elapsed_seconds=elapsed)

        return self._progress_watchdog_factory(interval_seconds, _emit)

    def _invoke_actor_with_progress(
        self,
        state: StateDecl,
        adapter: Adapter,
        prepared: PreparedInvocation,
        timeout_ms: int | None,
        *,
        emit_actor_progress: bool,
    ) -> dict[str, Any]:
        stop_watchdog = (
            self._start_progress_watchdog(
                "actor_progress",
                state,
                self._actor_progress_interval_seconds,
            )
            if emit_actor_progress
            else (lambda: None)
        )
        try:
            return self._invoke_with_timeout(adapter, prepared, timeout_ms)
        finally:
            stop_watchdog()
