"""Transition outcome derivation, selection, transform execution, resume."""

from __future__ import annotations

import time
from typing import Any

from orchestra.errors import ExecutorError
from orchestra.executor import guards
from orchestra.executor._executor_common import (
    _TERMINAL_TARGETS,
    FanOutSnapshot,
    _error_to_dict,
    _now_iso,
)
from orchestra.executor.guards import GuardContext
from orchestra.spine import (
    Envelope,
    ErrorRecord,
    StateDecl,
)
from orchestra.transforms import (
    TransformContext,
    runtime_check,
    type_label,
)
from orchestra.visibility import make_invocation_id


class _TransitionMixin:
    """Mixin: Transition outcome derivation, selection, transform execution, resume."""

    def _derive_outcome(self, state: StateDecl, payload: dict[str, Any], status: str) -> str:
        if status == "timeout":
            return "timeout"
        if status == "error":
            return "error"
        kind = state.actor.kind
        if kind in ("model", "agent"):
            verdict = payload.get("verdict")
            if verdict is not None:
                return str(verdict)
            return "complete"
        if kind == "human":
            chosen = payload.get("chosen")
            return str(chosen) if chosen is not None else "cancelled"
        if kind == "shell":
            agg = payload.get("aggregate") or {}
            if (agg.get("fail_count", 0) > 0) or (agg.get("skipped_count", 0) > 0):
                return "fail"
            return "pass"
        return "complete"

    def _close_pending_transition(
        self,
        *,
        state_id: str,
        attempt: int,
        outcome: str,
        target: str,
    ) -> str:
        """Increment ``_step_count``, apply the step budget, write the
        ``transition`` log record, advance ``_current_state`` to the
        chosen target, and return the (possibly budget-overridden)
        target.

        Round-3 factoring: the live linear path, the live transform
        linear path, ``resume_pending_transition``, ``resume_fan_out``,
        and ``close_fan_out_pending_transition`` all close their
        per-step accounting and durable transition write through this
        single helper so the on-disk format and the budget guard
        remain identical. ``state_id``/``attempt``/``outcome`` are
        named arguments so callers cannot silently swap them.
        """
        self._step_count += 1
        if self._step_count >= self._wf.max_total_steps:
            if target not in _TERMINAL_TARGETS:
                self._log.write(
                    "step_budget_exhausted",
                    state_id=state_id,
                    attempt=attempt,
                    fields={"max_total_steps": self._wf.max_total_steps},
                )
                target = "stop"
        self._log.write(
            "transition",
            state_id=state_id,
            attempt=attempt,
            fields={
                "outcome": outcome,
                "target": target,
                "step_count": self._step_count,
            },
        )
        self._last_state = state_id
        self._last_outcome = outcome
        self._current_state = target
        return target

    def _select_transition(self, state: StateDecl, envelope: Envelope) -> str:
        """Return the next state name. Linear shape only.

        For fan_out transitions, callers should use
        ``_select_transition_decl`` and dispatch through
        ``_run_fan_out_group``.
        """
        decl = self._select_transition_decl(state, envelope)
        if decl is None:
            raise ExecutorError(
                f"state {state.name!r}: no transition matched outcome {envelope.outcome!r}"
            )
        if decl.retry_max is not None:
            if self._retries.get(state.name, 0) < decl.retry_max:
                return state.name
            return str(decl.target)
        return str(decl.target)

    def _select_transition_decl(
        self,
        state: StateDecl,
        envelope: Envelope,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> Any:
        """Return the matched ``Transition`` declaration or None.

        Used by the run loop to dispatch on fan_out vs linear. Linear
        callers can use ``_select_transition`` for the resolved target
        string.

        ``snapshot`` is supplied by the fan-out child path so the
        guard context sees the same view of pre-fan-out state every
        sibling sees. Pass-6 fix: when a snapshot is supplied, every
        sibling-visible field (artifacts, envelopes, attempts,
        retries) comes from the snapshot, not from the live executor
        state. The current child's own envelope is layered on top so
        self-envelope guards still see the just-completed
        invocation; the current child's own attempts/retries also
        come from the live counters because the child has already
        observed its own counter increment in
        ``_execute_state_body``. Reading the live store or the live
        counter dicts for siblings from a worker thread would break
        the snapshot-isolation invariant pinned by
        ``tests/test_fan_out_executor::test_fan_out_sibling_reads_use_snapshot_not_live_store``.
        """
        artifact_values: dict[str, Any] = {}
        attempts: dict[str, int]
        retries: dict[str, int]
        envelope_views: dict[str, dict[str, Any]]
        if snapshot is not None:
            for art in self._wf.artifacts:
                if art.name in snapshot.artifacts:
                    artifact_values[art.name] = snapshot.artifacts[art.name]
                else:
                    artifact_values[art.name] = None
            attempts = dict(snapshot.attempts)
            retries = dict(snapshot.retries)
            with self._attempt_lock:
                attempts[state.name] = self._attempts.get(state.name, 0)
                retries[state.name] = self._retries.get(state.name, 0)
            envelope_views = dict(snapshot.envelopes)
            envelope_views[state.name] = {
                "outcome": envelope.outcome,
                "status": envelope.status,
                "duration_ms": envelope.duration_ms,
                "attempt": envelope.attempt,
                "payload": envelope.payload,
            }
        else:
            for art in self._wf.artifacts:
                v = self._store.read_latest(art.name)
                artifact_values[art.name] = v.value if v else None
            attempts = self._attempts
            retries = self._retries
            envelope_views = {
                name: {
                    "outcome": e.outcome,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                    "attempt": e.attempt,
                    "payload": e.payload,
                }
                for name, e in self._envelopes.items()
            }
        ctx = GuardContext(
            attempts=attempts,
            retries=retries,
            external_inputs=self._external,
            artifacts=artifact_values,
            envelopes=envelope_views,
        )
        for t in state.transitions:
            if t.outcome != envelope.outcome:
                continue
            if t.guard is not None and not guards.evaluate(t.guard, ctx):
                continue
            return t
        return None

    # ----- transform execution (Slice B) -------------------------

    def _run_transform_linear(self, state: StateDecl) -> str:
        """Linear-path entry point for transform states.

        Mirrors the adapter-path control flow in ``_run_one_state``
        (envelope, transition selection, step budget, route) but
        delegates the actual per-state work to
        ``_execute_transform_body``. Transform states do not retry
        because transforms are pure functions, so the retry branch is
        omitted.
        """
        envelope = self._execute_transform_body(state.name)
        decl = self._select_transition_decl(state, envelope)
        if decl is None:
            raise ExecutorError(
                f"state {state.name!r}: no transition matched outcome {envelope.outcome!r}"
            )
        if decl.is_fan_out():
            target = self._run_fan_out_group(state, envelope, decl)
        else:
            target = decl.target
        return self._close_pending_transition(
            state_id=state.name,
            attempt=envelope.attempt,
            outcome=envelope.outcome,
            target=target,
        )

    def _execute_transform_body(
        self,
        state_name: str,
        snapshot: FanOutSnapshot | None = None,
    ) -> Envelope:
        """Run a transform state's per-state sequence and return the
        envelope.

        Slice B contract: invocation_id is minted at state_enter time,
        the transform callable is invoked synchronously with a
        ``TransformContext`` carrying ``(run_id, state_name,
        sorted_input_keys)``, runtime type checks gate both inputs and
        outputs against the registered schema, output values flow
        through the same tentative_write/commit_tentative path adapter
        states use, and a Python exception (or a type violation)
        produces an error ``state_exit`` with no retry.

        The fan-out child worker can call this helper through
        ``_execute_state_body`` so transforms can participate in
        fan-out groups.
        """
        state = self._wf.state(state_name)
        assert state.actor.kind == "transform"
        assert state.actor.ref is not None
        transform = self._registry.transforms[state.actor.ref]

        with self._attempt_lock:
            self._attempts[state_name] = self._attempts.get(state_name, 0) + 1
            attempt = self._attempts[state_name]
            attempts_snapshot = dict(self._attempts)
            retries_snapshot = dict(self._retries)
        invocation_id = make_invocation_id(self._run_id, state_name, attempt)
        self._discard_stale_tentatives(state.name)
        self._visibility_index.insert_pending(invocation_id)
        self._log.write(
            "state_enter",
            state_id=state.name,
            attempt=attempt,
            fields={
                "attempts": attempts_snapshot,
                "retries": retries_snapshot,
                "invocation_id": invocation_id,
            },
        )
        self._emit_progress("state_enter", state)

        actor_binding = self._build_actor_binding(state)
        reads = self._read_artifacts(state, snapshot=snapshot)
        inputs: dict[str, Any] = {name: wrapper.get("value") for name, wrapper in reads.items()}
        sorted_input_keys = sorted(inputs.keys())
        ctx = TransformContext(
            run_id=self._run_id,
            state_name=state_name,
            sorted_input_keys=list(sorted_input_keys),
        )

        started_at = _now_iso()
        started_perf = time.perf_counter()
        error_record: ErrorRecord | None = None
        outputs: dict[str, Any] = {}

        # Slice B contract: the validator pins the input/output shape,
        # but the runtime values may diverge if upstream states wrote
        # the wrong type (a model-backed write that smuggled in a
        # non-string, for example). The runtime check catches that
        # before the transform callable runs.
        for read_name, expected_type in transform.input_schema.items():
            value = inputs.get(read_name)
            if not runtime_check(value, expected_type):
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=(
                        f"transform input {read_name!r}: value does not "
                        f"match declared type {type_label(expected_type)}"
                    ),
                    detail={"phase": "transform_input_typecheck"},
                )
                break

        if error_record is None:
            try:
                outputs = transform.callable(inputs, ctx)
            except Exception as exc:
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=str(exc),
                    detail={
                        "exception": type(exc).__name__,
                        "phase": "transform",
                    },
                )

        ended_at = _now_iso()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)

        if error_record is None:
            if not isinstance(outputs, dict):
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=(f"transform returned {type(outputs).__name__}, expected dict"),
                    detail={"phase": "transform_output_shape"},
                )
            else:
                expected_keys = set(transform.output_schema.keys())
                actual_keys = set(outputs.keys())
                if actual_keys != expected_keys:
                    missing = expected_keys - actual_keys
                    extra = actual_keys - expected_keys
                    error_record = ErrorRecord(
                        kind="actor_failure",
                        message=(
                            "transform output keys do not match "
                            f"output_schema; missing={sorted(missing)} "
                            f"extra={sorted(extra)}"
                        ),
                        detail={"phase": "transform_output_shape"},
                    )
                else:
                    for write_name, expected_type in transform.output_schema.items():
                        if not runtime_check(outputs[write_name], expected_type):
                            error_record = ErrorRecord(
                                kind="actor_failure",
                                message=(
                                    f"transform output {write_name!r}: "
                                    "value does not match declared type "
                                    f"{type_label(expected_type)}"
                                ),
                                detail={"phase": "transform_output_typecheck"},
                            )
                            break

        status: str = "ok" if error_record is None else "error"
        outcome = "complete" if status == "ok" else "error"

        artifacts_written: list[dict[str, str]] = []
        if status == "ok":
            tentative_handles: list[str] = []
            try:
                for w in state.writes:
                    handle = self._store.tentative_write(
                        w.name,
                        outputs[w.name],
                        written_by=f"{state.name}#{attempt}",
                        invocation_id=invocation_id,
                    )
                    tentative_handles.append(handle)
            except Exception as exc:
                self._store.discard_tentative(tentative_handles)
                tentative_handles = []
                status = "error"
                outcome = "error"
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=f"tentative_write failed: {exc}",
                    detail={
                        "exception": type(exc).__name__,
                        "phase": "transform_commit",
                    },
                )
            if status == "ok" and tentative_handles:
                committed_ids = self._store.commit_tentative(tentative_handles)
                for w, vid in zip(state.writes, committed_ids, strict=False):
                    artifacts_written.append({"artifact": w.name, "version_id": vid})
                    self._log.write(
                        "artifact_write",
                        state_id=state.name,
                        attempt=attempt,
                        fields={
                            "artifact": w.name,
                            "version_id": vid,
                            "invocation_id": invocation_id,
                        },
                    )

        envelope = Envelope(
            state_id=state.name,
            attempt=attempt,
            actor_binding=actor_binding,
            status=status,  # type: ignore[arg-type]
            outcome=outcome,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            inputs_read=[
                {"artifact": name, "version_id": v.get("__version_id", "")}
                for name, v in (reads or {}).items()
            ],
            artifacts_written=artifacts_written,
            payload={},
            error=error_record,
        )
        with self._envelope_lock:
            self._envelopes[state.name] = envelope
        self._log.write(
            "state_exit",
            state_id=state.name,
            attempt=attempt,
            fields={
                "status": status,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "inputs_read": envelope.inputs_read,
                "artifacts_written": envelope.artifacts_written,
                "error": _error_to_dict(error_record),
                "payload_ref": None,
                "invocation_id": invocation_id,
            },
        )
        self._emit_progress("state_exit", state, elapsed_seconds=duration_ms / 1000.0)
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)
        self._emit_state_exit_hook(state, envelope, None)
        return envelope

    def resume_pending_transition(self, state_name: str) -> str:
        """Replay rule for a crash between ``state_exit`` and
        ``transition``: the state's actor body has already run to a
        durable ``state_exit``, so re-entering would violate replay
        semantics. Instead, re-select the transition from the
        reconstructed envelope, write the ``transition`` log record,
        advance ``_current_state`` to the chosen target, and return
        the target string so the caller can continue
        ``run_to_completion`` from there.

        Step-budget accounting matches the linear path: this is the
        same step the original execution would have closed with the
        ``transition`` write, so ``_step_count`` is incremented once
        and the budget guard runs before the transition is written.
        """
        state = self._wf.state(state_name)
        with self._envelope_lock:
            envelope = self._envelopes.get(state_name)
        if envelope is None:
            raise ExecutorError(
                f"resume_pending_transition: no envelope reconstructed "
                f"for state {state_name!r}; cannot select transition"
            )
        decl = self._select_transition_decl(state, envelope)
        if decl is None:
            raise ExecutorError(
                f"resume_pending_transition: state {state_name!r}: no "
                f"transition matched outcome {envelope.outcome!r}"
            )
        if decl.is_fan_out():
            target = self._run_fan_out_group(state, envelope, decl)
        elif decl.retry_max is not None and self._retries.get(state_name, 0) < decl.retry_max:
            # The original execution would have re-entered the state
            # under the retry budget. Re-entering is the only correct
            # behavior here too: the state_exit was an error and the
            # workflow author asked for a retry. The actor body runs
            # again under a new attempt_seq.
            target = state_name
        else:
            target = decl.target
        return self._close_pending_transition(
            state_id=state_name,
            attempt=envelope.attempt,
            outcome=envelope.outcome,
            target=target,
        )
