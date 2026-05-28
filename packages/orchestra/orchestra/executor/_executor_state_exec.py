"""Per-state execution loop and timeout wrapper."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from orchestra.adapters.base import Adapter
from orchestra.errors import ExecutorError
from orchestra.executor._executor_common import (
    FanOutSnapshot,
    _adapter_manages_own_timeout,
    _error_to_dict,
    _now_iso,
    _payload_summary,
    _TimeoutSignal,
)
from orchestra.payloads import strip_internal as _strip_internal
from orchestra.spine import (
    Envelope,
    ErrorRecord,
    InvocationRequest,
    PreparedInvocation,
)
from orchestra.visibility import make_invocation_id


class _StateExecMixin:
    """Mixin: Per-state execution loop and timeout wrapper."""

    # ----- main loop ---------------------------------------------

    def _run_one_state(self) -> str:
        state = self._wf.state(self._current_state)
        if state.actor.kind == "transform":
            return self._run_transform_linear(state)

        # Step 1: increment counters.
        is_retry_from_self = self._last_state == state.name and self._last_outcome in (
            "error",
            "timeout",
        )
        self._attempts[state.name] = self._attempts.get(state.name, 0) + 1
        if is_retry_from_self:
            self._retries[state.name] = self._retries.get(state.name, 0) + 1
        else:
            self._retries[state.name] = 0
        attempt = self._attempts[state.name]

        # Discard any tentatives left over from a previous interrupted
        # attempt on this state. Slice 1 has no profile resume hooks
        # for partial commits, so the discard is sufficient cleanup.
        self._discard_stale_tentatives(state.name)

        # Slice A: mint the per-invocation key and pre-register it
        # as pending in the visibility index BEFORE writing
        # ``state_enter``. The order matters for crash atomicity:
        # if a crash lands between insert_pending and the log
        # write, replay rebuilds the index from the log; the
        # transient pending entry is discarded because no
        # state_enter record references the lost invocation_id.
        invocation_id = make_invocation_id(self._run_id, state.name, attempt)
        self._visibility_index.insert_pending(invocation_id)

        self._log.write(
            "state_enter",
            state_id=state.name,
            attempt=attempt,
            fields={
                "attempts": dict(self._attempts),
                "retries": dict(self._retries),
                "invocation_id": invocation_id,
            },
        )
        self._emit_progress("state_enter", state)

        # Step 2: build invocation request.
        actor_binding = self._build_actor_binding(state)
        prompt_artifact = self._resolve_prompt(state)
        reads = self._read_artifacts(state)
        timeout_ms = state.timeout_ms
        backing_options = dict(state.backing_options)
        if state.options and state.actor.kind == "human":
            backing_options.setdefault("options", list(state.options))
        # Merge per-call invocation options. These are call-site
        # overrides (model, timeout, log_dir, project_dir) and win over
        # the state's declared backing_options so a wrapper like
        # mcloop's invoke_code_edit can pin values for this run
        # without editing the workflow.
        backing_options.update(self._invocation_options)
        opt_timeout = self._invocation_options.get("timeout")
        if isinstance(opt_timeout, int | float) and opt_timeout > 0:
            timeout_ms = int(opt_timeout * 1000)
        opt_model = self._invocation_options.get("model")
        if isinstance(opt_model, str) and opt_model:
            # Surface the override through backing_options so adapters
            # can prefer it over both their default model and the
            # workflow's actor.ref (which is just a workflow-local
            # identifier referencing the model declaration, not the
            # actual model id the CLI should receive).
            backing_options["model_override"] = opt_model

        request = InvocationRequest(
            state_id=state.name,
            attempt=attempt,
            actor_binding=actor_binding,
            reads=reads,
            external_inputs=dict(self._external),
            prompt_artifact=prompt_artifact,
            schema=None,
            backing_options=backing_options,
            timeout_ms=timeout_ms,
        )

        adapter: Adapter = self._registry.adapter_for(state.actor.kind)

        # Step 3: prepare invocation. prepare() exceptions are caught
        # and converted to envelopes so the state always emits
        # state_exit and a transition.
        prepared: PreparedInvocation | None = None
        prepare_error: ErrorRecord | None = None
        try:
            prepared = adapter.prepare(request)
        except Exception as exc:
            prepare_error = ErrorRecord(
                kind="actor_failure",
                message=f"prepare() raised: {exc}",
                detail={"exception": type(exc).__name__, "phase": "prepare"},
            )

        if prepared is not None:
            self._log.write(
                "actor_prepare",
                state_id=state.name,
                attempt=attempt,
                fields={"summary": prepared.summary},
            )

        # Step 4: invoke (with timeout enforcement).
        started_at = _now_iso()
        started_perf = time.perf_counter()
        error_record: ErrorRecord | None = prepare_error
        payload: dict[str, Any] = {}
        if prepared is not None:
            self._log.write(
                "actor_invoke_start",
                state_id=state.name,
                attempt=attempt,
                fields={"actor_binding": actor_binding},
            )
            try:
                payload = self._invoke_actor_with_progress(
                    state,
                    adapter,
                    prepared,
                    timeout_ms,
                    emit_actor_progress=True,
                )
            except _TimeoutSignal:
                error_record = ErrorRecord(
                    kind="timeout",
                    message=f"invocation exceeded timeout_ms={timeout_ms}",
                    detail={"timeout_ms": timeout_ms},
                )
                payload = {}
            except Exception as exc:
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=str(exc),
                    detail={"exception": type(exc).__name__, "phase": "invoke"},
                )
                payload = {}
        ended_at = _now_iso()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)

        # Persist the payload BEFORE writing the log record that
        # references it. The payload file is fsynced; the log record
        # follows. A crash between the two leaves a payload without a
        # log reference, which is acceptable. The reverse order would
        # leave a log reference with no payload, which is not.
        payload_ref = self._write_payload(invocation_id, payload)
        self._log.write(
            "actor_invoke_end",
            state_id=state.name,
            attempt=attempt,
            fields={
                "summary": _payload_summary(state, payload),
                "payload_ref": payload_ref,
                "duration_ms": duration_ms,
            },
        )

        if error_record is not None and error_record.kind == "timeout":
            status: str = "timeout"
        elif error_record is not None:
            status = "error"
        else:
            status = "ok"
        outcome = self._derive_outcome(state, payload, status)

        # Step 5: postcondition checks. Slice 1 registers none.

        artifacts_written: list[dict[str, str]] = []
        # Step 6: result parser dispatch.
        tentative_handles: list[str] = []
        if status == "ok" and state.writes and prepared is not None:
            try:
                tentative_handles = self._dispatch_parsers(
                    state, prepared, payload, attempt, invocation_id
                )
            except Exception as exc:
                self._store.discard_tentative(tentative_handles)
                tentative_handles = []
                status = "error"
                outcome = "error"
                error_record = ErrorRecord(
                    kind="parser_failure",
                    message=str(exc),
                    detail={"exception": type(exc).__name__},
                )

        # Step 6.5: schema-verdict layer. Runs after parsers (so
        # non-schema-handled writes are already staged) but before
        # commit. On a schema-validation failure all tentative handles
        # are discarded and the state routes to its ``error`` outcome.
        if (
            status == "ok"
            and prepared is not None
            and self._state_schema_artifact(state) is not None
        ):
            try:
                schema_handles, decision_outcome, schema_error = self._apply_schema_layer(
                    state,
                    payload,
                    attempt,
                    invocation_id,
                    tentative_handles,
                    payload_ref,
                )
            except Exception as exc:
                self._store.discard_tentative(tentative_handles)
                tentative_handles = []
                status = "error"
                outcome = "error"
                error_record = ErrorRecord(
                    kind="parser_failure",
                    message=f"schema layer raised: {exc}",
                    detail={"exception": type(exc).__name__},
                )
            else:
                if schema_error is not None:
                    if tentative_handles:
                        self._store.discard_tentative(tentative_handles)
                    tentative_handles = []
                    status = "error"
                    outcome = "error"
                    error_record = schema_error
                else:
                    tentative_handles.extend(schema_handles)
                    if decision_outcome is not None:
                        outcome = decision_outcome

        # Step 7: commit writes.
        committed_ids: list[str] = []
        if status == "ok" and tentative_handles:
            committed_ids = self._store.commit_tentative(tentative_handles)
            artifacts_written = self._artifact_writes_record(
                state, prepared, payload, committed_ids
            )
            for entry in artifacts_written:
                self._log.write(
                    "artifact_write",
                    state_id=state.name,
                    attempt=attempt,
                    fields={
                        "artifact": entry["artifact"],
                        "version_id": entry["version_id"],
                        "invocation_id": invocation_id,
                    },
                )

        # Step 8: finalize envelope.
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
            payload=_strip_internal(payload),
            error=error_record,
        )
        self._envelopes[state.name] = envelope
        # Slice A: write state_exit FIRST so the durable record is
        # the completion point, then update the visibility index.
        # A crash between the two leaves the index at "pending" plus
        # a durable state_exit on disk; replay's rebuild_from_records
        # reconstructs the correct visibility from the log. Doing
        # this in the opposite order would mean the in-memory index
        # could mark a row visible before the state_exit record is
        # actually durable, and a crash in that window would leak
        # visibility to a hypothetical concurrent reader on the
        # store-side path.
        self._log.write(
            "state_exit",
            state_id=state.name,
            attempt=attempt,
            fields={
                "status": envelope.status,
                "outcome": envelope.outcome,
                "duration_ms": envelope.duration_ms,
                "inputs_read": envelope.inputs_read,
                "artifacts_written": envelope.artifacts_written,
                "error": _error_to_dict(envelope.error),
                "payload_ref": payload_ref,
                "invocation_id": invocation_id,
            },
        )
        self._emit_progress("state_exit", state, elapsed_seconds=envelope.duration_ms / 1000.0)
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)
        self._emit_state_exit_hook(state, envelope, payload_ref)

        # Step 9: transition selection.
        decl = self._select_transition_decl(state, envelope)
        if decl is None:
            raise ExecutorError(
                f"state {state.name!r}: no transition matched outcome {envelope.outcome!r}"
            )
        if decl.is_fan_out():
            target = self._run_fan_out_group(state, envelope, decl)
        elif decl.retry_max is not None and self._retries.get(state.name, 0) < decl.retry_max:
            target = state.name
        else:
            target = decl.target

        # Steps 10 + 11: step-budget guard, transition write, route.
        return self._close_pending_transition(
            state_id=state.name,
            attempt=attempt,
            outcome=envelope.outcome,
            target=target,
        )

    def _execute_state_body(
        self,
        state_name: str,
        snapshot: FanOutSnapshot | None = None,
        on_prepared: (Callable[[PreparedInvocation, str], None] | None) = None,
        is_cancelled_after_register: Callable[[], bool] | None = None,
        suppress_actor_progress: bool = False,
    ) -> Envelope:
        """Run steps 1-9 of the per-state sequence for ``state_name``.

        Used by the fan-out child worker; the linear ``_run_one_state``
        path inlines an equivalent sequence today.

        Cleanup 1: ``_attempts[state_name]`` is incremented HERE, under
        ``_attempt_lock``, immediately before the ``state_enter`` log
        write. Callers no longer pre-seed the counter, so a child that
        never enters never gets a counter bump. The minted attempt and
        the invocation_id derived from it are returned via the
        envelope; ``on_prepared`` (if provided) receives the
        ``invocation_id`` alongside the prepared invocation so the
        fan-out cancellation registry can record the handle.

        Slice B: when ``state.actor.kind == 'transform'`` the helper
        delegates to ``_execute_transform_body``. Transforms have no
        adapter to prepare or cancel, so the cancellation callbacks
        are unused on the transform path.
        """
        state = self._wf.state(state_name)
        if state.actor.kind == "transform":
            return self._execute_transform_body(state_name, snapshot=snapshot)
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
        prompt_artifact = self._resolve_prompt(state, snapshot=snapshot)
        reads = self._read_artifacts(state, snapshot=snapshot)
        timeout_ms = state.timeout_ms
        backing_options = dict(state.backing_options)
        if state.options and state.actor.kind == "human":
            backing_options.setdefault("options", list(state.options))
        backing_options.update(self._invocation_options)
        opt_timeout = self._invocation_options.get("timeout")
        if isinstance(opt_timeout, int | float) and opt_timeout > 0:
            timeout_ms = int(opt_timeout * 1000)
        opt_model = self._invocation_options.get("model")
        if isinstance(opt_model, str) and opt_model:
            backing_options["model_override"] = opt_model

        request = InvocationRequest(
            state_id=state.name,
            attempt=attempt,
            actor_binding=actor_binding,
            reads=reads,
            external_inputs=dict(self._external),
            prompt_artifact=prompt_artifact,
            schema=None,
            backing_options=backing_options,
            timeout_ms=timeout_ms,
        )

        adapter: Adapter = self._registry.adapter_for(state.actor.kind)

        prepared: PreparedInvocation | None = None
        prepare_error: ErrorRecord | None = None
        try:
            prepared = adapter.prepare(request)
        except Exception as exc:
            prepare_error = ErrorRecord(
                kind="actor_failure",
                message=f"prepare() raised: {exc}",
                detail={"exception": type(exc).__name__, "phase": "prepare"},
            )

        # Slice A: ``cancelled_post_register`` covers the small window
        # between ``on_prepared`` (which transitions the cancellation
        # registry from ``pending`` to ``registered`` and stores the
        # handle) and ``actor_invoke_start``. Without this re-check
        # the controller could call ``request_cancel_all`` after the
        # worker passed its top-of-loop check and after the registry
        # transitioned to ``registered``, but BEFORE the adapter was
        # invoked. The pending-flag path would race past, the
        # registered-cancel path would call ``adapter.cancel`` on a
        # not-yet-invoked handle, and the worker would still call
        # ``adapter.invoke``. Re-checking here closes the window.
        cancelled_post_register = False
        if prepared is not None:
            self._log.write(
                "actor_prepare",
                state_id=state.name,
                attempt=attempt,
                fields={"summary": prepared.summary},
            )
            if on_prepared is not None:
                on_prepared(prepared, invocation_id)
            if is_cancelled_after_register is not None and is_cancelled_after_register():
                cancelled_post_register = True

        started_at = _now_iso()
        started_perf = time.perf_counter()
        error_record: ErrorRecord | None = prepare_error
        if cancelled_post_register:
            error_record = ErrorRecord(
                kind="cancelled",
                message=("cancelled by fan-out controller after register, before invoke"),
            )
        payload: dict[str, Any] = {}
        if prepared is not None and not cancelled_post_register:
            self._log.write(
                "actor_invoke_start",
                state_id=state.name,
                attempt=attempt,
                fields={"actor_binding": actor_binding},
            )
            try:
                payload = self._invoke_actor_with_progress(
                    state,
                    adapter,
                    prepared,
                    timeout_ms,
                    emit_actor_progress=not suppress_actor_progress,
                )
            except _TimeoutSignal:
                error_record = ErrorRecord(
                    kind="timeout",
                    message=f"invocation exceeded timeout_ms={timeout_ms}",
                    detail={"timeout_ms": timeout_ms},
                )
                payload = {}
            except Exception as exc:
                error_record = ErrorRecord(
                    kind="actor_failure",
                    message=str(exc),
                    detail={"exception": type(exc).__name__, "phase": "invoke"},
                )
                payload = {}
        ended_at = _now_iso()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)

        # If we never wrote ``actor_invoke_start`` (cancelled before
        # invoke), we also do not write ``actor_invoke_end`` and skip
        # ``_write_payload``: there is no payload to record.
        if prepared is not None and not cancelled_post_register:
            payload_ref = self._write_payload(invocation_id, payload)
            self._log.write(
                "actor_invoke_end",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "summary": _payload_summary(state, payload),
                    "payload_ref": payload_ref,
                    "duration_ms": duration_ms,
                },
            )
        else:
            payload_ref = None

        if error_record is not None and error_record.kind == "timeout":
            status: str = "timeout"
        elif error_record is not None:
            status = "error"
        else:
            status = "ok"
        outcome = self._derive_outcome(state, payload, status)
        # Preserve the "cancelled" outcome from the
        # cancelled-post-register path so downstream tooling can
        # distinguish a controller-cancelled invocation from a
        # generic error. This matches the convention used by
        # ``_write_cancelled_state_exit`` (cancellation observed
        # before prepare).
        if error_record is not None and error_record.kind == "cancelled":
            outcome = "cancelled"

        artifacts_written: list[dict[str, str]] = []
        tentative_handles: list[str] = []
        if status == "ok" and state.writes and prepared is not None:
            try:
                tentative_handles = self._dispatch_parsers(
                    state, prepared, payload, attempt, invocation_id
                )
            except Exception as exc:
                self._store.discard_tentative(tentative_handles)
                tentative_handles = []
                status = "error"
                outcome = "error"
                error_record = ErrorRecord(
                    kind="parser_failure",
                    message=str(exc),
                    detail={"exception": type(exc).__name__},
                )

        # Schema-verdict 1.4: parse the model output as JSON, validate
        # against the artifact's schema, and tentative-write the json
        # artifact plus any extraction targets.
        if (
            status == "ok"
            and prepared is not None
            and self._state_schema_artifact(state) is not None
        ):
            try:
                schema_handles, decision_outcome, schema_error = self._apply_schema_layer(
                    state,
                    payload,
                    attempt,
                    invocation_id,
                    tentative_handles,
                    payload_ref,
                )
            except Exception as exc:
                self._store.discard_tentative(tentative_handles)
                tentative_handles = []
                status = "error"
                outcome = "error"
                error_record = ErrorRecord(
                    kind="parser_failure",
                    message=f"schema layer raised: {exc}",
                    detail={"exception": type(exc).__name__},
                )
            else:
                if schema_error is not None:
                    if tentative_handles:
                        self._store.discard_tentative(tentative_handles)
                    tentative_handles = []
                    status = "error"
                    outcome = "error"
                    error_record = schema_error
                else:
                    tentative_handles.extend(schema_handles)
                    if decision_outcome is not None:
                        outcome = decision_outcome

        committed_ids: list[str] = []
        if status == "ok" and tentative_handles:
            committed_ids = self._store.commit_tentative(tentative_handles)
            artifacts_written = self._artifact_writes_record(
                state, prepared, payload, committed_ids
            )
            for entry in artifacts_written:
                self._log.write(
                    "artifact_write",
                    state_id=state.name,
                    attempt=attempt,
                    fields={
                        "artifact": entry["artifact"],
                        "version_id": entry["version_id"],
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
            payload=_strip_internal(payload),
            error=error_record,
        )
        with self._envelope_lock:
            self._envelopes[state.name] = envelope
        # Slice A: state_exit durability is the completion point.
        # Write the record FIRST, then update the visibility index.
        # A crash between the two leaves the index pending and the
        # state_exit on disk; replay's rebuild_from_records uses the
        # log to derive the correct visibility status.
        self._log.write(
            "state_exit",
            state_id=state.name,
            attempt=attempt,
            fields={
                "status": envelope.status,
                "outcome": envelope.outcome,
                "duration_ms": envelope.duration_ms,
                "inputs_read": envelope.inputs_read,
                "artifacts_written": envelope.artifacts_written,
                "error": _error_to_dict(envelope.error),
                "payload_ref": payload_ref,
                "invocation_id": invocation_id,
            },
        )
        self._emit_progress("state_exit", state, elapsed_seconds=envelope.duration_ms / 1000.0)
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)
        self._emit_state_exit_hook(state, envelope, payload_ref)
        return envelope

    # ----- timeout enforcement -----------------------------------

    def _invoke_with_timeout(
        self,
        adapter: Adapter,
        prepared: PreparedInvocation,
        timeout_ms: int | None,
    ) -> dict[str, Any]:
        """Invoke ``adapter.invoke(prepared)`` enforcing ``timeout_ms``.

        Implementation: run invoke() on a daemon thread; the main
        thread waits with a timer. On timeout, signal the adapter via
        ``cancel`` and raise ``_TimeoutSignal``. The thread is left to
        finish on its own; its result is discarded. This is correct
        for slice 1's mocks; real adapters with non-cooperative work
        will need the slice-2 cancellation contract.

        Adapters that manage their own timeout (the claude_code text
        and agent adapters return exit_code -2 from a wall-clock
        capped run_session) declare ``manages_own_timeout = True`` so
        the executor never wraps them in this thread-based timer.
        Wrapping would race the adapter's own loop and could discard
        its structured -2 payload before run_session returned.

        For dispatcher adapters that fan out to a per-role adapter,
        the actual adapter that will run is stashed on
        ``prepared.inner["_role_adapter"]``. The flag is read from
        there so a mixed dispatcher (some role-adapters manage their
        own timeout, others do not) honors each adapter's preference
        per dispatch instead of collapsing to a single aggregate.
        """
        if timeout_ms is None or _adapter_manages_own_timeout(adapter, prepared):
            return adapter.invoke(prepared)

        result_box: dict[str, Any] = {}
        error_box: list[BaseException] = []

        def _runner() -> None:
            try:
                result_box["payload"] = adapter.invoke(prepared)
            except BaseException as exc:
                error_box.append(exc)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join(timeout=timeout_ms / 1000.0)
        if thread.is_alive():
            try:
                adapter.cancel(prepared)
            except Exception:
                pass
            raise _TimeoutSignal()
        if error_box:
            exc = error_box[0]
            if isinstance(exc, Exception):
                raise exc
            raise RuntimeError(f"adapter raised non-Exception: {exc!r}") from exc
        return dict(result_box.get("payload", {}))
