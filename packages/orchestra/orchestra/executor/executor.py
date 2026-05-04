"""The runner's state-machine loop.

Implements the eleven-step per-state sequence specified in
``design/orchestra-runner.md`` section "Per-state execution":

  1. increment counters
  2. build invocation request
  3. prepare invocation
  4. invoke
  5. postcondition checks         (no postconditions in slice 1)
  6. result parser dispatch
  7. commit writes
  8. finalize envelope
  9. transition selection
 10. check step budget
 11. route

The executor is the only component that calls the artifact store's
``commit_tentative`` path. Adapters return payloads; parsers stage
tentative writes; the executor commits them.

Crash atomicity:

The store and the log are independent persistence systems. The
executor orders work so that a crash at any point leaves a state
that replay can correctly classify:

- Before any tentative is staged: case 2, replay re-enters.
- After tentatives staged but before commit: tentatives are
  visible in the store but not yet committed; the next time the
  store opens, those rows remain tentative. ``replay`` treats
  this as case 2 and re-enters; the new attempt's
  ``_discard_stale_tentatives`` cleans up the residue.
- After commit but before ``state_exit``: committed rows are
  durable. Replay sees ``state_enter`` without ``state_exit``;
  ``_discard_stale_tentatives`` would find none. ``replay``
  detects this case by looking for committed rows tagged
  ``written_by = "<state>#<attempt>"``; if any exist, the state
  is treated as completed (case 1.5) and the executor synthesizes
  the missing ``state_exit`` and ``transition`` records.
- After ``state_exit`` but before ``transition``: replay sees
  state_exit without a following transition. The executor
  re-selects and writes the transition. (Per the runner spec the
  transition selection is deterministic given the envelope, so
  this is safe.)
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from orchestra.adapters.base import Adapter
from orchestra.errors import ExecutorError
from orchestra.executor import guards
from orchestra.executor.guards import GuardContext
from orchestra.log import LogWriter
from orchestra.payloads import payload_name_from_invocation, write_payload
from orchestra.payloads import strip_internal as _strip_internal
from orchestra.registry import ProfileRegistry
from orchestra.schema import Invalid, SchemaSpec, Valid, load_schema
from orchestra.spine import (
    ArtifactDecl,
    Envelope,
    ErrorRecord,
    InvocationRequest,
    PreparedInvocation,
    PromptSource,
    StateDecl,
    Workflow,
)
from orchestra.store import ArtifactStore
from orchestra.transforms import (
    TransformContext,
    runtime_check,
    type_label,
)
from orchestra.visibility import VisibilityIndex, make_invocation_id

_TERMINAL_TARGETS = {"done", "stop"}


def _coerce_to_text(value: Any) -> str:
    """Convert a schema-extracted scalar value to its canonical text
    form for writing into a text artifact.

    Strings are passed through. Booleans are emitted as the
    lowercase JSON literals (``"true"``/``"false"``) rather than the
    Python title-case repr, matching the schema spec's "canonical text
    form". Integers and floats use ``str()``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Executor:
    """Run a workflow start-to-finish (or until ``stop``/``done``)."""

    def __init__(
        self,
        *,
        workflow: Workflow,
        registry: ProfileRegistry,
        store: ArtifactStore,
        log: LogWriter,
        run_dir: Path,
        run_id: str,
        external_inputs: dict[str, Any],
        # Resume state, optional.
        attempts: dict[str, int] | None = None,
        retries: dict[str, int] | None = None,
        envelopes: dict[str, Envelope] | None = None,
        current_state: str | None = None,
        step_count: int = 0,
        # Pass-2 fix #2: the (state_id, outcome) of the latest durable
        # transition before resume. The live executor tracks these in
        # _last_state/_last_outcome via _close_pending_transition; on
        # resume we rebuild them from the log so the next state entry
        # decides retry-counter increments the same way the live path
        # would. Default to None (fresh run); cmd_resume threads them
        # in from ReplayState.
        last_transition_state: str | None = None,
        last_transition_outcome: str | None = None,
        # Per-call invocation options. Merged into every state's
        # backing_options at invoke time so adapters see the overrides
        # without polluting the workflow's external_inputs surface.
        invocation_options: dict[str, Any] | None = None,
        # Slice A: an externally-supplied visibility index. The
        # executor builds one with a persisted snapshot in run_dir
        # when omitted. The store consults the same instance through
        # set_visibility_index().
        visibility_index: VisibilityIndex | None = None,
        # Optional progress hook. Fires once per ``state_enter`` and
        # once per ``state_exit`` for sequential states, plus once
        # per ``fan_out_start`` and once per ``fan_out_end`` for
        # parallel groups. The callback receives
        # ``(kind, state_name, role, index, total, elapsed_seconds,
        # children)`` where ``children`` is a tuple of
        # ``(child_state_name, child_role)`` pairs on
        # ``fan_out_start`` and ``None`` otherwise. ``index`` is the
        # 1-based ordinal of the state's first entry (retries reuse
        # the same index); for ``fan_out_start`` it is the index of
        # the first child in the dispatch range. ``total`` is the
        # count of declared states in the workflow.
        # ``elapsed_seconds`` is ``None`` for the start events and a
        # float duration for the exit events. The api wraps the
        # user-facing ``progress_callback`` to enrich with adapter
        # and model from the resolved role bindings; the executor
        # itself only knows the role name.
        progress_callback: Callable[
            [
                str,
                str,
                str | None,
                int,
                int,
                float | None,
                tuple[tuple[str, str | None], ...] | None,
            ],
            None,
        ] | None = None,
    ) -> None:
        self._wf = workflow
        self._registry = registry
        self._store = store
        self._log = log
        self._run_dir = run_dir
        self._run_id = run_id
        self._external = dict(external_inputs)
        self._attempts: dict[str, int] = dict(attempts or {})
        self._retries: dict[str, int] = dict(retries or {})
        self._envelopes: dict[str, Envelope] = dict(envelopes or {})
        self._last_outcome: str | None = last_transition_outcome
        self._last_state: str | None = last_transition_state
        self._current_state: str = current_state or workflow.start_state_name()
        self._step_count = step_count
        self._payloads_dir = run_dir / "payloads"
        self._payloads_dir.mkdir(parents=True, exist_ok=True)
        self._invocation_options: dict[str, Any] = dict(invocation_options or {})
        # Slice A: every per-state invocation gets a unique
        # ``invocation_id = run_id::state_name::attempt_seq``. The
        # VisibilityIndex tracks the per-invocation outcome so the
        # artifact store can hide artifacts written by incomplete or
        # errored invocations.
        self._visibility_index: VisibilityIndex = (
            visibility_index
            if visibility_index is not None
            else VisibilityIndex(persist_path=run_dir / "visibility.json")
        )
        self._store.set_visibility_index(self._visibility_index)
        # Locks that worker threads spawned by the fan-out controller
        # share with the controller thread. ``_attempt_lock`` guards
        # ``self._attempts`` and ``self._retries`` (the per-state
        # counters). ``_envelope_lock`` guards ``self._envelopes``.
        # The store and log have their own locks; these two cover the
        # remaining workflow-level mutable state.
        self._attempt_lock: threading.Lock = threading.Lock()
        self._envelope_lock: threading.Lock = threading.Lock()
        # Progress reporting: per-state 1-based index assigned on first
        # state_enter for that state name. Retries reuse the existing
        # index so the [N/M] counter does not jump on a retry.
        self._progress_callback = progress_callback
        self._progress_state_indices: dict[str, int] = {}
        self._progress_total: int = len(workflow.states)
        self._progress_lock: threading.Lock = threading.Lock()
        # Schema-verdict 1.4: per-artifact SchemaSpec cache. Loaded once
        # at executor construction so each state invocation reuses the
        # parsed schema rather than re-reading the file. Schemas are
        # static inputs (snapshotted by 1.5 the same way prompt files
        # are); resume rebuilds the cache from the same files.
        self._schema_specs: dict[str, SchemaSpec] = {}
        self._schema_artifacts: dict[str, ArtifactDecl] = {}
        for art in workflow.artifacts:
            if art.schema_path is None:
                continue
            schema_full = Path(art.schema_path)
            if not schema_full.is_absolute():
                schema_full = Path(workflow.source_dir) / art.schema_path
            self._schema_specs[art.name] = load_schema(schema_full)
            self._schema_artifacts[art.name] = art
        # Set of artifact names handled by the schema layer
        # (the json artifact itself plus every declared extraction
        # target). The parser-dispatch path filters writes to skip
        # these because the schema layer commits them directly.
        self._schema_handled_artifacts: set[str] = set()
        for name, art in self._schema_artifacts.items():
            self._schema_handled_artifacts.add(name)
            for ext in art.extractions:
                self._schema_handled_artifacts.add(ext.target)

    # ----- entry points ------------------------------------------

    def run_to_completion(self) -> str:
        """Run states until a terminal target is reached.

        Returns the terminal target string (``done`` or ``stop``).
        """
        if self._current_state in _TERMINAL_TARGETS:
            return self._current_state
        while True:
            outcome = self.step()
            if outcome in _TERMINAL_TARGETS:
                return outcome

    def step(self) -> str:
        """Run exactly one state."""
        return self._run_one_state()

    # ----- main loop ---------------------------------------------

    def _run_one_state(self) -> str:
        state = self._wf.state(self._current_state)
        if state.actor.kind == "transform":
            return self._run_transform_linear(state)

        # Step 1: increment counters.
        is_retry_from_self = (
            self._last_state == state.name
            and self._last_outcome in ("error", "timeout")
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
                payload = self._invoke_with_timeout(
                    adapter, prepared, timeout_ms
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
        if status == "ok" and prepared is not None and self._state_schema_artifact(state) is not None:
            try:
                schema_handles, decision_outcome, schema_error = (
                    self._apply_schema_layer(
                        state,
                        payload,
                        attempt,
                        invocation_id,
                        tentative_handles,
                        payload_ref,
                    )
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
        self._emit_progress(
            "state_exit", state, elapsed_seconds=envelope.duration_ms / 1000.0
        )
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)

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

    # ----- helpers ------------------------------------------------

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

    def _build_actor_binding(self, state: StateDecl) -> dict[str, Any]:
        binding: dict[str, Any] = {"kind": state.actor.kind}
        if state.actor.ref is not None:
            if state.actor.kind == "model":
                binding["model"] = state.actor.ref
            elif state.actor.kind == "agent":
                binding["agent"] = state.actor.ref
            elif state.actor.kind == "transform":
                binding["transform"] = state.actor.ref
        if state.role is not None:
            binding["role"] = state.role
        return binding

    def _resolve_prompt(
        self,
        state: StateDecl,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> str | None:
        prompt = state.prompt
        if prompt is None and state.role is not None:
            for r in self._wf.roles:
                if r.name == state.role:
                    prompt = r.default_prompt
                    break
        if prompt is None:
            return None
        return self._render_prompt(prompt, snapshot=snapshot)

    def _render_prompt(
        self,
        source: PromptSource,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> str:
        if source.kind == "file":
            assert source.path is not None
            full = Path(self._wf.source_dir) / source.path
            with open(full, encoding="utf-8") as fh:
                return fh.read()
        if source.kind == "template":
            assert source.path is not None
            full = Path(self._wf.source_dir) / source.path
            with open(full, encoding="utf-8") as fh:
                template = fh.read()
            substitutions: dict[str, Any] = {}
            for var in source.template_vars:
                if var in self._external:
                    substitutions[var] = self._external[var]
                elif snapshot is not None:
                    # Slice A: fan-out children read from the captured
                    # snapshot, NOT from the live store. A sibling
                    # write that landed mid-fan-out is invisible to
                    # this child.
                    if var in snapshot.artifacts:
                        substitutions[var] = snapshot.artifacts[var]
                    else:
                        substitutions[var] = None
                else:
                    art = self._store.read_latest(var)
                    substitutions[var] = art.value if art else None
            return _format(template, substitutions)
        if source.kind == "from":
            raise ExecutorError("'prompt from' references are slice-2 territory")
        raise ExecutorError(f"unknown prompt source kind: {source.kind!r}")

    def _read_artifacts(
        self,
        state: StateDecl,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in state.reads:
            if r in self._external:
                out[r] = {"value": self._external[r], "__version_id": ""}
                continue
            if snapshot is not None:
                # Slice A: fan-out children consume the captured
                # snapshot exclusively. Artifacts not in the snapshot
                # (sibling writes that landed mid-fan-out) are not
                # visible to the child, matching the plan's
                # sibling-visibility rule.
                if r in snapshot.artifacts:
                    out[r] = {
                        "value": snapshot.artifacts[r],
                        "__version_id": "snapshot",
                    }
                else:
                    out[r] = {"value": None, "__version_id": ""}
                continue
            art = self._store.read_latest(r)
            if art is not None:
                out[r] = {"value": art.value, "__version_id": art.version_id}
            else:
                out[r] = {"value": None, "__version_id": ""}
        return out

    def _write_payload(self, invocation_id: str, payload: dict[str, Any]) -> str:
        """Persist a payload to disk with fsync.

        Caller treats this as a durability boundary: the payload file
        exists on disk by the time this returns. The log record that
        references it is written after, ensuring no log record points
        at a missing payload. The on-disk encoding is shared with the
        replay-side loader in ``orchestra.payloads`` so resume's
        envelope-hydration path consumes the same format.

        The filename is derived from ``invocation_id`` rather than
        from the log writer's mutable ``seq`` counter so two fan-out
        children completing concurrently cannot collide on the same
        payload file. Each invocation's invocation_id is unique within
        a run.
        """
        return write_payload(
            self._payloads_dir,
            payload_name_from_invocation(invocation_id),
            payload,
        )

    def _derive_outcome(
        self, state: StateDecl, payload: dict[str, Any], status: str
    ) -> str:
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

    def close_fan_out_pending_transition(
        self,
        *,
        parent_state_name: str,
        parent_attempt: int,
        target: str,
    ) -> str:
        """Replay rule for a crash between ``fan_out_end`` and the
        parent ``transition``: the routing decision is durable in the
        ``fan_out_end`` record but the transition log entry was
        never written. Resume must close the missing transition
        without re-running the fan-out group. The parent envelope
        is consulted only for ``outcome``; everything else comes
        from the durable ``fan_out_end`` plus ``parent_attempt``
        threaded from the matching ``fan_out_start``.
        """
        with self._envelope_lock:
            envelope = self._envelopes.get(parent_state_name)
        if envelope is None:
            raise ExecutorError(
                "close_fan_out_pending_transition: no envelope "
                f"reconstructed for parent {parent_state_name!r}"
            )
        return self._close_pending_transition(
            state_id=parent_state_name,
            attempt=parent_attempt,
            outcome=envelope.outcome,
            target=target,
        )

    def _dispatch_parsers(
        self,
        state: StateDecl,
        prepared: PreparedInvocation,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str | None = None,
    ) -> list[str]:
        """Run applicable parsers and produce tentative writes.

        Returns the list of tentative handles that ``commit_tentative``
        will promote on success.

        Writes whose target is handled by the schema layer (the
        schema-backed json artifact and any of its declared extraction
        targets) are filtered out before the parser dispatch: the
        schema layer commits them directly in the same transaction
        as the json write, after this method returns.
        """
        parser_writes = tuple(
            w for w in state.writes
            if w.name not in self._schema_handled_artifacts
        )
        write_types = tuple(w.type for w in parser_writes)
        parsers = self._registry.parsers_for(
            backing=state.actor.kind, artifact_types=write_types
        )
        if not parsers:
            return []
        envelope_for_parser = Envelope(
            state_id=state.name,
            attempt=attempt,
            actor_binding={},
            status="ok",
            outcome="complete",
            started_at="",
            ended_at="",
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={
                **payload,
                "_declared_writes": [
                    {"name": w.name, "type": w.type} for w in parser_writes
                ],
            },
            error=None,
        )
        handles: list[str] = []
        for parser in parsers:
            self._log.write(
                "parser_run",
                state_id=state.name,
                attempt=attempt,
                fields={"parser": parser.name},
            )
            try:
                pairs = parser.fn(envelope_for_parser, self._store)
                for name, value in pairs:
                    handle = self._store.tentative_write(
                        name,
                        value,
                        written_by=f"{state.name}#{attempt}",
                        invocation_id=invocation_id,
                    )
                    handles.append(handle)
            except Exception:
                # Discard handles staged so far before re-raising. The
                # caller will discard the empty list and set status to
                # error.
                if handles:
                    self._store.discard_tentative(handles)
                    handles.clear()
                raise
        return handles

    def _artifact_writes_record(
        self,
        state: StateDecl,
        prepared: PreparedInvocation | None,
        payload: dict[str, Any],
        committed_ids: list[str],
    ) -> list[dict[str, str]]:
        """Pair committed version IDs with their artifact names by
        re-running the parsers in their declared order. Parsers are
        pure, so re-running is safe and cheap.

        The ``schema_handle_names`` field on the payload (set by
        ``_apply_schema_layer`` when a schema-backed write fires) lists
        the schema-layer-committed artifact names in their declared
        order; those names are appended after any parser-produced
        names so the (name, version_id) pairing matches the order of
        ``commit_tentative``'s input.
        """
        parser_writes = tuple(
            w for w in state.writes
            if w.name not in self._schema_handled_artifacts
        )
        write_types = tuple(w.type for w in parser_writes)
        parsers = self._registry.parsers_for(
            backing=state.actor.kind, artifact_types=write_types
        )
        envelope_for_parser = Envelope(
            state_id=state.name,
            attempt=0,
            actor_binding={},
            status="ok",
            outcome="complete",
            started_at="",
            ended_at="",
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={
                **payload,
                "_declared_writes": [
                    {"name": w.name, "type": w.type} for w in parser_writes
                ],
            },
            error=None,
        )
        names: list[str] = []
        for parser in parsers:
            for name, _ in parser.fn(envelope_for_parser, self._store):
                names.append(name)
        schema_names = payload.get("_schema_handle_names")
        if isinstance(schema_names, list):
            for n in schema_names:
                if isinstance(n, str):
                    names.append(n)
        out: list[dict[str, str]] = []
        for n, vid in zip(names, committed_ids, strict=False):
            out.append({"artifact": n, "version_id": vid})
        return out

    def _state_schema_artifact(
        self, state: StateDecl
    ) -> ArtifactDecl | None:
        """Return the schema-backed json artifact this state writes,
        or ``None`` when the state has no schema-backed write.

        At most one schema-backed write per state (enforced by the
        validator), so this returns either the single matching
        ArtifactDecl or None.
        """
        for w in state.writes:
            if w.name in self._schema_artifacts:
                return self._schema_artifacts[w.name]
        return None

    def _apply_schema_layer(
        self,
        state: StateDecl,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str,
        existing_handles: list[str],
        payload_ref: str | None,
    ) -> tuple[list[str], str | None, ErrorRecord | None]:
        """Schema-verdict 1.4: parse the model output as JSON, validate
        against the artifact's schema, tentative-write the json
        artifact and any declared extractions atomically (returned to
        the caller as additional handles), and emit a
        ``schema_validation`` log record.

        Returns ``(new_handles, decision_outcome, error_record)``:

        - ``new_handles``: tentative handles to append to the existing
          parser handles. Empty when validation failed or no schema
          applies.
        - ``decision_outcome``: the schema's decision string when
          validation passes; ``None`` otherwise.
        - ``error_record``: an ``ErrorRecord`` carrying ``reason`` in
          ``detail`` (``"json_parse"`` or ``"schema_violation"``) when
          validation fails; ``None`` on success or when no schema
          applies.

        On any failure the caller is expected to discard
        ``existing_handles`` and route to the state's ``error``
        outcome.
        """
        artifact = self._state_schema_artifact(state)
        if artifact is None:
            return [], None, None
        spec = self._schema_specs[artifact.name]
        raw_output = payload.get("output")
        if not isinstance(raw_output, str):
            err = ErrorRecord(
                kind="actor_failure",
                message=(
                    "schema-backed state requires a string 'output' "
                    f"field on the model payload, got {type(raw_output).__name__}"
                ),
                detail={
                    "reason": "json_parse",
                    "phase": "schema_validation",
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "parse_error",
                    "decision": None,
                    "validation_errors": ["payload.output is not a string"],
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            err = ErrorRecord(
                kind="actor_failure",
                message=f"json parse error: {exc}",
                detail={
                    "reason": "json_parse",
                    "phase": "schema_validation",
                    "exception": "JSONDecodeError",
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "parse_error",
                    "decision": None,
                    "validation_errors": [str(exc)],
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        result = spec.validate(parsed)
        if isinstance(result, Invalid):
            err = ErrorRecord(
                kind="actor_failure",
                message="schema validation failed",
                detail={
                    "reason": "schema_violation",
                    "phase": "schema_validation",
                    "errors": list(result.errors),
                },
            )
            self._log.write(
                "schema_validation",
                state_id=state.name,
                attempt=attempt,
                fields={
                    "artifact": artifact.name,
                    "outcome": "schema_error",
                    "decision": None,
                    "validation_errors": list(result.errors),
                    "payload_ref": payload_ref,
                    "invocation_id": invocation_id,
                },
            )
            return [], None, err
        assert isinstance(result, Valid)
        decision = result.decision
        new_handles: list[str] = []
        handle_names: list[str] = []
        try:
            json_handle = self._store.tentative_write(
                artifact.name,
                parsed,
                written_by=f"{state.name}#{attempt}",
                invocation_id=invocation_id,
            )
            new_handles.append(json_handle)
            handle_names.append(artifact.name)
            for ext in artifact.extractions:
                if ext.source_field not in parsed:
                    # Optional source field omitted from the validated
                    # object: skip the extraction; the target retains
                    # its prior value (or its declared ``initial``).
                    continue
                value = parsed[ext.source_field]
                text_value = _coerce_to_text(value)
                ext_handle = self._store.tentative_write(
                    ext.target,
                    text_value,
                    written_by=f"{state.name}#{attempt}",
                    invocation_id=invocation_id,
                )
                new_handles.append(ext_handle)
                handle_names.append(ext.target)
        except Exception:
            if new_handles:
                self._store.discard_tentative(new_handles)
            raise
        # Pass extraction handle names through to _artifact_writes_record
        # via the payload's side-channel slot. _strip_internal drops
        # this before the envelope is finalized.
        payload["_schema_handle_names"] = handle_names
        self._log.write(
            "schema_validation",
            state_id=state.name,
            attempt=attempt,
            fields={
                "artifact": artifact.name,
                "outcome": "valid",
                "decision": decision,
                "validation_errors": [],
                "payload_ref": payload_ref,
                "invocation_id": invocation_id,
            },
        )
        return new_handles, decision, None

    def _discard_stale_tentatives(self, state_name: str) -> None:
        """Discard tentatives left over from any earlier attempt on
        this state.

        On entry to attempt N, any tentatives written by attempts
        1..N-1 are stale: either they were committed in a prior run
        (in which case the rows are no longer tentative and are
        unaffected) or they were left tentative by a crash (in which
        case we discard them). The store's discard is by handle; we
        do not have those handles in memory after a process restart,
        so we discard by the underlying tentative rows tagged with
        the relevant ``written_by``.
        """
        # Slice 1 implementation: query tentative rows whose
        # written_by starts with f"{state_name}#" and discard them
        # directly via SQL. The store does not yet expose a public
        # API for this, so we reach into its connection here. The
        # store-level RLock and isolation_level=None contract
        # require explicit BEGIN IMMEDIATE / commit / rollback on
        # the connection (not via a cursor).
        conn = self._store._conn
        prefix = f"{state_name}#"
        with self._store.lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT seq FROM versions
                    WHERE is_tentative = 1 AND written_by LIKE ?
                    """,
                    (prefix + "%",),
                )
                rows = cur.fetchall()
                seqs = [r[0] for r in rows]
                # FK ordering: ``tentative_handles.seq`` references
                # ``versions.seq``. Under ``PRAGMA foreign_keys=ON``
                # the parent must be deleted AFTER the child,
                # otherwise the child delete fails with an
                # IntegrityError. ``store.discard_tentative`` does
                # the same thing (handle row first, then version
                # row); mirror that order here.
                for seq in seqs:
                    cur.execute(
                        "DELETE FROM tentative_handles WHERE seq = ?", (seq,)
                    )
                    cur.execute("DELETE FROM versions WHERE seq = ?", (seq,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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
        self, state: StateDecl, envelope: Envelope,
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
                f"state {state.name!r}: no transition matched outcome "
                f"{envelope.outcome!r}"
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
            self._attempts[state_name] = (
                self._attempts.get(state_name, 0) + 1
            )
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
        inputs: dict[str, Any] = {
            name: wrapper.get("value")
            for name, wrapper in reads.items()
        }
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
                    message=(
                        f"transform returned {type(outputs).__name__}, "
                        "expected dict"
                    ),
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
                    for write_name, expected_type in (
                        transform.output_schema.items()
                    ):
                        if not runtime_check(
                            outputs[write_name], expected_type
                        ):
                            error_record = ErrorRecord(
                                kind="actor_failure",
                                message=(
                                    f"transform output {write_name!r}: "
                                    "value does not match declared type "
                                    f"{type_label(expected_type)}"
                                ),
                                detail={
                                    "phase": "transform_output_typecheck"
                                },
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
                committed_ids = self._store.commit_tentative(
                    tentative_handles
                )
                for w, vid in zip(
                    state.writes, committed_ids, strict=False
                ):
                    artifacts_written.append(
                        {"artifact": w.name, "version_id": vid}
                    )
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
        self._emit_progress(
            "state_exit", state, elapsed_seconds=duration_ms / 1000.0
        )
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)
        return envelope

    # ----- fan-out execution -------------------------------------

    def _run_fan_out_group(
        self,
        parent_state: StateDecl,
        parent_envelope: Envelope,
        transition: Any,
    ) -> str:
        """Run a fan_out group and return the next state target.

        The parent state's ``state_exit`` is already durable. This
        method walks the fan-out lifecycle:

        1. Capture an immutable snapshot of pre-fan-out envelopes and
           the visible artifact store (via ``read_latest``) under the
           ``LogWriter -> ArtifactStore`` ordering rule.
        2. Append ``fan_out_start`` inside the same critical section.
        3. Submit one future per child state through a
           ``ThreadPoolExecutor``. Each child's worker mints a new
           invocation_id and runs the full per-state sequence ending
           at a durable ``state_exit``.
        4. Drain via ``as_completed``. On the first child error,
           cancel pending futures and continue to drain the running
           ones.
        5. Determine the aggregate outcome (any error -> error_target).
        6. Run the post-fan-out cleanup pass: purge committed
           ``state_invocation`` rows whose producing invocation is
           not ``success``.
        7. Append ``fan_out_end`` carrying the routing decision.
        8. Return the target state name.
        """
        # Step 1 + 2: snapshot capture and fan_out_start under the
        # LogWriter-then-store lock-ordering rule.
        #
        # Cleanup 2: fan_out_start is appended while BOTH locks are
        # held (LogWriter outer, store inner). Releasing the store
        # lock before the log write would let another thread mutate
        # the store between snapshot capture and the durable
        # fan_out_start record; per the spec the snapshot and the
        # log record sit in one critical section.
        with self._log.lock:
            with self._store.lock:
                snapshot_envelopes = {
                    name: _envelope_to_view(env)
                    for name, env in self._envelopes.items()
                }
                snapshot_artifacts: dict[str, Any] = {}
                for art in self._wf.artifacts:
                    v = self._store.read_latest(art.name)
                    if v is not None:
                        snapshot_artifacts[art.name] = v.value
                # Pass-6 fix: capture attempts and retries at
                # fan_out_start so child guards see deterministic
                # sibling-counter values, not whatever the live
                # _attempts/_retries dicts hold by the time a worker
                # thread evaluates its transition.
                with self._attempt_lock:
                    snapshot_attempts = dict(self._attempts)
                    snapshot_retries = dict(self._retries)
                self._log.write(
                    "fan_out_start",
                    state_id=parent_state.name,
                    attempt=parent_envelope.attempt,
                    fields={
                        "parent_state": parent_state.name,
                        "children": list(transition.fan_out),
                        "join_target": transition.target,
                        "error_target": transition.error_target,
                    },
                )
        # Surface the parallel-group start to the progress reporter
        # AFTER the durable log write but BEFORE worker dispatch so
        # the user sees the parallel header before any per-child
        # state_enter line. children carries each child's state name
        # plus its declared role; the api wrapper enriches each pair
        # with the resolved adapter and model.
        children_with_roles: tuple[tuple[str, str | None], ...] = tuple(
            (child_name, self._wf.state(child_name).role)
            for child_name in transition.fan_out
        )
        self._emit_progress(
            "fan_out_start", parent_state, children=children_with_roles
        )

        registry = _CancellationRegistry()
        for child_name in transition.fan_out:
            registry.register_pending(child_name)

        # Cleanup 1: do NOT pre-seed ``_attempts``. The reentrant
        # ``_execute_state_body`` increments per-state at
        # ``state_enter`` time. Reset ``_retries`` for first entry;
        # the per-entry retry budget starts at 0 here.
        with self._attempt_lock:
            for child_name in transition.fan_out:
                self._retries[child_name] = 0

        futures: dict[str, Future[Envelope]] = {}
        executor = ThreadPoolExecutor(
            max_workers=max(1, len(transition.fan_out)),
            thread_name_prefix="orchestra-fan-out",
        )
        try:
            for child_name in transition.fan_out:
                fut = executor.submit(
                    self._fan_out_child_worker,
                    child_name,
                    registry,
                    snapshot_envelopes,
                    snapshot_artifacts,
                    snapshot_attempts,
                    snapshot_retries,
                )
                futures[child_name] = fut

            # Drain. On first error, cancel pending futures.
            child_outcomes: dict[str, str] = {}
            child_invocation_ids: dict[str, str] = {}
            group_errored = False
            from concurrent.futures import as_completed
            for fut in as_completed(futures.values()):
                # Map future back to child name.
                child_name = next(
                    name for name, f in futures.items() if f is fut
                )
                try:
                    envelope = fut.result()
                except Exception as exc:
                    # The worker should have written its own
                    # state_exit; defensive fallback for unexpected
                    # crashes records the error here.
                    child_outcomes[child_name] = "error"
                    self._log.write(
                        "fan_out_child_crash",
                        state_id=parent_state.name,
                        fields={
                            "child": child_name,
                            "error": str(exc),
                        },
                    )
                    group_errored = True
                    registry.request_cancel_all(futures)
                    continue
                # Re-audit P2: use ``envelope.attempt`` (the FINAL
                # attempt after any child-local retries) rather than
                # ``per_child_attempt[child_name]`` (which captures
                # only the initial attempt). The state_exit and
                # artifact commits are keyed to the final attempt;
                # the aggregate must agree so downstream tooling can
                # correlate the per-child invocation_ids back to
                # the durable records.
                inv_id = make_invocation_id(
                    self._run_id, child_name, envelope.attempt
                )
                child_invocation_ids[child_name] = inv_id
                outcome = "success" if envelope.status == "ok" else "error"
                child_outcomes[child_name] = outcome
                if outcome == "error" and not group_errored:
                    group_errored = True
                    registry.request_cancel_all(futures)
        finally:
            executor.shutdown(wait=True)

        # Aggregate outcome.
        aggregate: Literal["success", "error"] = (
            "error" if group_errored else "success"
        )
        target = transition.error_target if aggregate == "error" else transition.target
        # Cleanup pass: purge state_invocation rows whose producing
        # invocation is not success. Idempotent.
        purged = self._store.purge_invisible_state_invocation_versions()
        # fan_out_end log record.
        self._log.write(
            "fan_out_end",
            state_id=parent_state.name,
            attempt=parent_envelope.attempt,
            fields={
                "parent_state": parent_state.name,
                "aggregate": aggregate,
                "per_child_outcome": child_outcomes,
                "child_invocation_ids": child_invocation_ids,
                "target": target,
                "purged_versions": purged,
            },
        )
        # Close the parallel block in the progress reporter. The
        # reporter computes parallel wall-clock from the maximum of
        # the per-child state_exit elapsed_seconds it observed, so
        # the executor does not need to compute it here.
        self._emit_progress("fan_out_end", parent_state)
        return str(target)

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
        elif (
            decl.retry_max is not None
            and self._retries.get(state_name, 0) < decl.retry_max
        ):
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

    def resume_fan_out(
        self,
        parent_state_name: str,
        children: list[str],
        join_target: str,
        error_target: str,
        completed_children: dict[str, Envelope],
        parent_attempt: int | None = None,
    ) -> str:
        """Resume an open fan-out group after a crash.

        The original ``fan_out_start`` is durable in the log but no
        ``fan_out_end`` was written, so replay surfaces the group in
        ``ReplayState.open_fan_out``. The resume entry point applies
        the rebuilt VisibilityIndex (BEFORE constructing the
        Executor, see ``cli.cmd_resume``), then dispatches here.

        Children with a durable ``state_exit`` are passed in via
        ``completed_children``. Their outcomes feed into the
        aggregate; their committed artifacts remain visible (the
        VisibilityIndex was rebuilt from the log to reflect this).
        Children listed in ``children`` but not in
        ``completed_children`` are pending: they get a fresh
        ``invocation_id`` (a new ``attempt_seq`` minted under
        ``_attempt_lock``) and re-run. Per the plan's
        fresh-budget-on-replay rule, each re-entered child's
        ``retries`` counter resets to 0.

        After the group finishes, this method writes ``fan_out_end``
        and advances ``_current_state`` to the chosen target so the
        caller can continue with ``run_to_completion``.
        """
        parent_state = self._wf.state(parent_state_name)

        # Re-audit RA-B1: reconstruct the snapshot from currently-
        # visible artifacts and envelopes, but EXCLUDE every
        # completed fan-out sibling from this group. The
        # VisibilityIndex (rebuilt from the log) marks completed
        # siblings as ``success`` so their committed artifacts are
        # visible to ``read_latest`` per the visibility rule. Without
        # filtering, a pending child re-entered after the crash
        # would see its completed siblings' outputs, violating the
        # sibling-visibility rule the snapshot machinery exists to
        # enforce. Other states completed before the fan-out
        # (e.g. the parent that produced the framing artifact)
        # remain visible.
        excluded_sibling_names = set(completed_children.keys())
        excluded_sibling_inv_ids = {
            make_invocation_id(self._run_id, name, env.attempt)
            for name, env in completed_children.items()
        }
        with self._log.lock:
            with self._store.lock:
                snapshot_envelopes = {
                    name: _envelope_to_view(env)
                    for name, env in self._envelopes.items()
                    if name not in excluded_sibling_names
                }
                snapshot_artifacts: dict[str, Any] = {}
                for art in self._wf.artifacts:
                    v = self._store.read_latest(art.name)
                    if v is None:
                        continue
                    if v.invocation_id in excluded_sibling_inv_ids:
                        # A completed fan-out sibling produced this
                        # version. Hide it from the pending child
                        # so the sibling-visibility rule is honored
                        # across resume.
                        continue
                    snapshot_artifacts[art.name] = v.value
                # Pass-6 fix: same counter snapshot as the live path,
                # excluding the completed siblings' counters so a
                # pending child's guard cannot read sibling state via
                # attempts.<sibling> or retries.<sibling>.
                with self._attempt_lock:
                    snapshot_attempts = {
                        n: v
                        for n, v in self._attempts.items()
                        if n not in excluded_sibling_names
                    }
                    snapshot_retries = {
                        n: v
                        for n, v in self._retries.items()
                        if n not in excluded_sibling_names
                    }
            self._log.write(
                "fan_out_resume",
                state_id=parent_state.name,
                attempt=parent_attempt,
                fields={
                    "parent_state": parent_state.name,
                    "children": list(children),
                    "completed": list(completed_children.keys()),
                    "pending": [
                        c for c in children if c not in completed_children
                    ],
                    "join_target": join_target,
                    "error_target": error_target,
                },
            )

        pending_children = [
            c for c in children if c not in completed_children
        ]

        # Seed outcomes from already-completed children; these were
        # committed and durable before the crash.
        child_outcomes: dict[str, str] = {}
        child_invocation_ids: dict[str, str] = {}
        group_errored = False
        for name, env in completed_children.items():
            outcome = "success" if env.status == "ok" else "error"
            child_outcomes[name] = outcome
            child_invocation_ids[name] = make_invocation_id(
                self._run_id, name, env.attempt
            )
            if outcome == "error":
                group_errored = True

        # Re-audit RA-B2: if any completed child already errored, the
        # group has already failed. The cancellation race rule says
        # the routing decision is fixed once any child errors. The
        # controller does NOT submit pending children: that would
        # create new fan-out child invocations during replay of an
        # already-failed group, which is the opposite of the
        # contract. Pending children are flagged ``not_launched`` in
        # the per-child outcome map so the log records they were
        # never launched, the cleanup pass runs, and ``fan_out_end``
        # routes to ``error_target``.
        if group_errored:
            for child_name in pending_children:
                child_outcomes[child_name] = "not_launched"
            aggregate: Literal["success", "error"] = "error"
            target = error_target
            purged = self._store.purge_invisible_state_invocation_versions()
            self._log.write(
                "fan_out_end",
                state_id=parent_state.name,
                attempt=parent_attempt,
                fields={
                    "parent_state": parent_state.name,
                    "aggregate": aggregate,
                    "per_child_outcome": child_outcomes,
                    "child_invocation_ids": child_invocation_ids,
                    "target": target,
                    "purged_versions": purged,
                },
            )
            return self._close_resumed_fan_out_transition(
                parent_state.name, parent_attempt, target
            )

        registry = _CancellationRegistry()
        for child_name in pending_children:
            registry.register_pending(child_name)

        # Cleanup 1: do NOT pre-seed ``_attempts`` for pending
        # children. The reentrant ``_execute_state_body`` increments
        # per-state at ``state_enter`` time, so a never-entered
        # pending child gets ``attempt_seq=1`` on its first entry
        # rather than an inflated counter from controller pre-seed.
        # Per the plan's fresh-budget-on-replay rule, ``retries``
        # resets to 0 for each re-entered child.
        with self._attempt_lock:
            for child_name in pending_children:
                self._retries[child_name] = 0

        if pending_children:
            futures: dict[str, Future[Envelope]] = {}
            executor = ThreadPoolExecutor(
                max_workers=max(1, len(pending_children)),
                thread_name_prefix="orchestra-fan-out-resume",
            )
            try:
                for child_name in pending_children:
                    fut = executor.submit(
                        self._fan_out_child_worker,
                        child_name,
                        registry,
                        snapshot_envelopes,
                        snapshot_artifacts,
                        snapshot_attempts,
                        snapshot_retries,
                    )
                    futures[child_name] = fut

                from concurrent.futures import as_completed

                for fut in as_completed(futures.values()):
                    child_name = next(
                        name for name, f in futures.items() if f is fut
                    )
                    try:
                        envelope = fut.result()
                    except Exception as exc:
                        child_outcomes[child_name] = "error"
                        self._log.write(
                            "fan_out_child_crash",
                            state_id=parent_state.name,
                            fields={
                                "child": child_name,
                                "error": str(exc),
                            },
                        )
                        group_errored = True
                        registry.request_cancel_all(futures)
                        continue
                    # Re-audit P2: use ``envelope.attempt`` so the
                    # aggregate's per-child invocation_id matches
                    # the final attempt's durable state_exit /
                    # artifact commits when child-local retries
                    # promoted the seq.
                    inv_id = make_invocation_id(
                        self._run_id,
                        child_name,
                        envelope.attempt,
                    )
                    child_invocation_ids[child_name] = inv_id
                    outcome = (
                        "success" if envelope.status == "ok" else "error"
                    )
                    child_outcomes[child_name] = outcome
                    if outcome == "error" and not group_errored:
                        group_errored = True
                        registry.request_cancel_all(futures)
            finally:
                executor.shutdown(wait=True)

        aggregate = "error" if group_errored else "success"
        target = error_target if aggregate == "error" else join_target
        purged = self._store.purge_invisible_state_invocation_versions()
        self._log.write(
            "fan_out_end",
            state_id=parent_state.name,
            attempt=parent_attempt,
            fields={
                "parent_state": parent_state.name,
                "aggregate": aggregate,
                "per_child_outcome": child_outcomes,
                "child_invocation_ids": child_invocation_ids,
                "target": target,
                "purged_versions": purged,
            },
        )
        return self._close_resumed_fan_out_transition(
            parent_state.name, parent_attempt, target
        )

    def _close_resumed_fan_out_transition(
        self,
        parent_state_name: str,
        parent_attempt: int | None,
        target: str,
    ) -> str:
        """Round-3 fix: ``resume_fan_out`` ends with the parent's
        durable ``transition`` record so the resumed log mirrors the
        live path's record sequence and step-count tracking. Falls
        back gracefully when ``parent_attempt`` is missing (older
        log fixtures may have been produced before parent_attempt
        threading landed)."""
        if parent_attempt is None:
            # Defensive: shouldn't occur once parent_attempt threading
            # is in place, but keep current_state advancement so the
            # caller can continue without a transition record.
            self._current_state = target
            return target
        with self._envelope_lock:
            parent_env = self._envelopes.get(parent_state_name)
        outcome = parent_env.outcome if parent_env is not None else "complete"
        return self._close_pending_transition(
            state_id=parent_state_name,
            attempt=parent_attempt,
            outcome=outcome,
            target=target,
        )

    def _fan_out_child_worker(
        self,
        child_name: str,
        registry: _CancellationRegistry,
        snapshot_envelopes: dict[str, dict[str, Any]],
        snapshot_artifacts: dict[str, Any],
        snapshot_attempts: dict[str, int],
        snapshot_retries: dict[str, int],
    ) -> Envelope:
        """Run one fan-out child to a durable ``state_exit``.

        Workers do NOT mutate ``self._current_state``,
        ``self._step_count``, or write outgoing transition records;
        the fan-out controller owns those. Artifact reads and prompt
        template substitutions consume the captured snapshot, never
        the live store, so a sibling write that lands mid-fan-out is
        invisible.

        Cleanup 1: workers no longer accept a pre-minted ``attempt``.
        ``_execute_state_body`` increments ``_attempts[child_name]``
        and mints the invocation_id at ``state_enter`` time. The
        envelope's ``attempt`` field is the source of truth for the
        retry loop and any downstream invocation_id reconstruction.

        Slice A child-local retry: when the per-state body returns
        an error or timeout envelope and the child's state declares
        ``on error retry max N then T``, the worker increments
        ``retries[child_name]`` under ``_attempt_lock`` and re-calls
        the body. The body's next invocation increments
        ``_attempts[child_name]`` again, producing a monotonic
        per-state attempt counter. The per-entry budget rule applies:
        ``retries[child_name]`` starts at 0 on first entry (controller
        resets it in ``_run_fan_out_group``). The
        fresh-budget-on-replay rule is separate and handled by the
        resume path.
        """
        snapshot = FanOutSnapshot(
            envelopes=dict(snapshot_envelopes),
            artifacts=dict(snapshot_artifacts),
            attempts=dict(snapshot_attempts),
            retries=dict(snapshot_retries),
        )
        state = self._wf.state(child_name)
        if state.actor.kind == "transform":
            # Slice B fix: transform children have no adapter to look
            # up, prepare, or cancel. ``adapter_for("transform")``
            # would raise because transform is not an actor backing.
            # Skip the cancellation registry's prepare-side hooks
            # (there is no in-flight adapter call) and dispatch
            # directly to ``_execute_state_body`` which routes to
            # ``_execute_transform_body``. Transforms do not retry.
            if registry.is_cancelled(child_name):
                envelope = self._write_cancelled_state_exit(child_name)
                registry.mark_done(child_name)
                return envelope
            envelope = self._execute_state_body(
                child_name,
                snapshot=snapshot,
            )
            registry.mark_done(child_name)
            return envelope
        adapter = self._registry.adapter_for(state.actor.kind)
        while True:
            if registry.is_cancelled(child_name):
                return self._write_cancelled_state_exit(child_name)

            def _on_prepared(
                prepared: PreparedInvocation,
                invocation_id: str,
                _name: str = child_name,
                _adapter: Adapter = adapter,
            ) -> None:
                registry.mark_started(
                    _name, invocation_id, prepared, _adapter
                )

            def _is_cancelled_after_register(
                _name: str = child_name,
            ) -> bool:
                return registry.is_cancelled(_name)

            envelope = self._execute_state_body(
                child_name,
                snapshot=snapshot,
                on_prepared=_on_prepared,
                is_cancelled_after_register=_is_cancelled_after_register,
            )
            # Pass-5 fix #3: route the child outcome through the same
            # first-match selection the linear path uses, then retry
            # only if the selected transition is itself a retry
            # transition with budget remaining. The pre-fix code
            # scanned every transition for one with the right outcome
            # AND retry_max set, ignoring declaration order and
            # picking up a retry transition declared AFTER a non-retry
            # transition for the same outcome (e.g. `on error => stop`
            # followed by `on error retry max 1 then stop`). That
            # routes the child to retry when the workflow author
            # explicitly chose `=> stop` first.
            selected = self._select_transition_decl(
                state, envelope, snapshot=snapshot
            )
            should_retry = False
            if selected is not None and selected.retry_max is not None:
                with self._attempt_lock:
                    used = self._retries.get(child_name, 0)
                    if used < selected.retry_max:
                        self._retries[child_name] = used + 1
                        should_retry = True
            if not should_retry:
                registry.mark_done(child_name)
                return envelope
            # Loop: the next iteration's ``_execute_state_body``
            # will increment ``_attempts[child_name]`` again.

    def _write_cancelled_state_exit(self, child_name: str) -> Envelope:
        """Emit a state_enter/state_exit pair for a cancelled child
        that never invoked its adapter, so replay sees a complete
        durable record for the invocation.

        Cleanup 1: ``_attempts[child_name]`` is minted HERE so
        cancellation that fires before the body runs still produces
        a monotonic per-state attempt counter. Mirrors
        ``_execute_state_body``'s minting discipline."""
        with self._attempt_lock:
            self._attempts[child_name] = (
                self._attempts.get(child_name, 0) + 1
            )
            attempt = self._attempts[child_name]
        invocation_id = make_invocation_id(self._run_id, child_name, attempt)
        self._visibility_index.insert_pending(invocation_id)
        self._log.write(
            "state_enter",
            state_id=child_name,
            attempt=attempt,
            fields={
                "attempts": {child_name: attempt},
                "retries": {child_name: 0},
                "invocation_id": invocation_id,
                "cancelled": True,
            },
        )
        envelope = Envelope(
            state_id=child_name,
            attempt=attempt,
            actor_binding={},
            status="error",
            outcome="cancelled",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            inputs_read=[],
            artifacts_written=[],
            payload={},
            error=ErrorRecord(
                kind="cancelled",
                message="cancelled by fan-out controller before adapter invoke",
            ),
        )
        with self._envelope_lock:
            self._envelopes[child_name] = envelope
        # state_exit FIRST, then visibility update (Slice A).
        self._log.write(
            "state_exit",
            state_id=child_name,
            attempt=attempt,
            fields={
                "status": "error",
                "outcome": "cancelled",
                "duration_ms": 0,
                "inputs_read": [],
                "artifacts_written": [],
                "error": _error_to_dict(envelope.error),
                "payload_ref": None,
                "invocation_id": invocation_id,
            },
        )
        self._visibility_index.mark_error(invocation_id)
        return envelope

    def _execute_state_body(
        self,
        state_name: str,
        snapshot: FanOutSnapshot | None = None,
        on_prepared: (
            Callable[[PreparedInvocation, str], None] | None
        ) = None,
        is_cancelled_after_register: Callable[[], bool] | None = None,
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
            return self._execute_transform_body(
                state_name, snapshot=snapshot
            )
        with self._attempt_lock:
            self._attempts[state_name] = (
                self._attempts.get(state_name, 0) + 1
            )
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
            if (
                is_cancelled_after_register is not None
                and is_cancelled_after_register()
            ):
                cancelled_post_register = True

        started_at = _now_iso()
        started_perf = time.perf_counter()
        error_record: ErrorRecord | None = prepare_error
        if cancelled_post_register:
            error_record = ErrorRecord(
                kind="cancelled",
                message=(
                    "cancelled by fan-out controller after register, "
                    "before invoke"
                ),
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
                payload = self._invoke_with_timeout(
                    adapter, prepared, timeout_ms
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
        if status == "ok" and prepared is not None and self._state_schema_artifact(state) is not None:
            try:
                schema_handles, decision_outcome, schema_error = (
                    self._apply_schema_layer(
                        state,
                        payload,
                        attempt,
                        invocation_id,
                        tentative_handles,
                        payload_ref,
                    )
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
        self._emit_progress(
            "state_exit", state, elapsed_seconds=envelope.duration_ms / 1000.0
        )
        if status == "ok":
            self._visibility_index.mark_success(invocation_id)
        else:
            self._visibility_index.mark_error(invocation_id)
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


class _TimeoutSignal(Exception):
    pass


def _adapter_manages_own_timeout(
    adapter: Adapter, prepared: PreparedInvocation
) -> bool:
    """Return whether the actually-selected adapter manages its own
    timeout, accounting for dispatchers that fan out per role.

    A dispatcher stashes the picked role-adapter on the prepared
    invocation under ``_role_adapter``. The executor consults that
    instance's flag instead of the dispatcher's so a mixed dispatcher
    does not mask a True-flagged adapter behind a False aggregate.
    Falls back to the adapter the executor already holds when the
    prepared invocation does not carry a per-dispatch reference.
    """
    inner = prepared.inner
    if isinstance(inner, dict):
        selected = inner.get("_role_adapter")
        if selected is not None:
            return bool(getattr(selected, "manages_own_timeout", False))
    return bool(getattr(adapter, "manages_own_timeout", False))


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def _format(template: str, substitutions: dict[str, Any]) -> str:
    """Light-weight ``{var}`` substitution.

    Slice 1 uses Python's ``str.format_map`` so ``{topic}`` in the
    template is replaced with the value of ``topic`` in the
    substitutions dict. Missing keys are left as ``{key}`` literals
    (see ``_DefaultMissing``).

    The substitution values may be nested dicts (e.g. a read artifact
    is wrapped in ``{"value": ..., "__version_id": ...}``). Unwrap to
    the underlying value before formatting.
    """
    flat: dict[str, Any] = {}
    for k, v in substitutions.items():
        if isinstance(v, dict) and "value" in v:
            flat[k] = v["value"]
        else:
            flat[k] = v
    return template.format_map(_DefaultMissing(flat))


class _DefaultMissing(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _payload_summary(state: StateDecl, payload: dict[str, Any]) -> dict[str, Any]:
    """Compact, log-friendly summary of a payload."""
    if state.actor.kind in ("model", "agent"):
        return {
            "output_chars": len(payload.get("output", "") or ""),
            "verdict": payload.get("verdict"),
            "tokens_in": payload.get("tokens_in"),
            "tokens_out": payload.get("tokens_out"),
        }
    if state.actor.kind == "shell":
        agg = payload.get("aggregate") or {}
        return {
            "pass_count": agg.get("pass_count"),
            "fail_count": agg.get("fail_count"),
            "skipped_count": agg.get("skipped_count"),
        }
    if state.actor.kind == "human":
        return {"chosen": payload.get("chosen")}
    return {}


def _error_to_dict(err: ErrorRecord | None) -> dict[str, Any] | None:
    if err is None:
        return None
    return {"kind": err.kind, "message": err.message, "detail": err.detail}


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------
# Slice A helpers: cancellation registry and snapshot view
# --------------------------------------------------------------------


@dataclass(frozen=True)
class FanOutSnapshot:
    """Immutable read-only view a fan-out child sees of pre-fan-out
    state. Captured atomically under the LogWriter-then-store
    lock-ordering rule. Children consume the snapshot for prompt
    resolution, ``reads`` clauses, and transition guard evaluation;
    live ``read_latest`` calls against the store from inside a
    fan-out child are forbidden so siblings cannot leak each other's
    writes.

    ``attempts`` and ``retries`` are the per-state counter dicts as
    they stood at fan_out_start. The audit's pass-6 finding showed
    that without them, a fan-out child guard like
    ``on error when attempts.<sibling> > 0 => stop`` reads
    ``self._attempts`` directly and the routing becomes dependent on
    sibling thread scheduling. Snapshotting the counter dicts at
    fan_out_start makes the read deterministic.
    """

    envelopes: dict[str, dict[str, Any]]
    artifacts: dict[str, Any]
    attempts: dict[str, int]
    retries: dict[str, int]


@dataclass
class _ChildEntry:
    cancel_requested: bool = False
    state: Literal["pending", "registered", "done"] = "pending"
    invocation_id: str | None = None
    invocation_handle: PreparedInvocation | None = None
    adapter: Adapter | None = None


class _CancellationRegistry:
    """Per-fan-out-group cancellation state, shared by the controller
    and worker threads.

    The registry tracks one entry per child state name. Workers move
    the entry from ``pending`` to ``registered`` once
    ``adapter.prepare`` returns and the prepared handle is stored,
    and to ``done`` when the worker finishes. The controller requests
    cancellation by calling ``request_cancel_all``: pending entries
    are flagged (so workers that have not yet started can short
    circuit before invoking the adapter); registered entries receive
    ``adapter.cancel(invocation_handle)`` so the adapter can attempt
    to abort an in-flight invocation cooperatively.

    Slice A's adapter implementations are non-cooperative for
    in-flight cancellation, so the registered-cancel call is best
    effort and the worker still drains the in-flight invocation to a
    durable ``state_exit``. The fix here is that the registry now
    CALLS ``adapter.cancel``; whether the adapter actually cooperates
    is the adapter's contract.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _ChildEntry] = {}

    def register_pending(self, child_name: str) -> None:
        with self._lock:
            self._entries[child_name] = _ChildEntry()

    def is_cancelled(self, child_name: str) -> bool:
        with self._lock:
            entry = self._entries.get(child_name)
            return entry is not None and entry.cancel_requested

    def mark_started(
        self,
        child_name: str,
        invocation_id: str,
        invocation_handle: PreparedInvocation,
        adapter: Adapter,
    ) -> None:
        with self._lock:
            entry = self._entries.get(child_name)
            if entry is None:
                return
            entry.state = "registered"
            entry.invocation_id = invocation_id
            entry.invocation_handle = invocation_handle
            entry.adapter = adapter

    def mark_done(self, child_name: str) -> None:
        with self._lock:
            entry = self._entries.get(child_name)
            if entry is None:
                return
            entry.state = "done"

    def request_cancel_all(
        self, futures: dict[str, Future[Envelope]]
    ) -> None:
        """Cancel every still-running child.

        - ``pending`` entries: set ``cancel_requested`` so the worker
          short circuits before invoking the adapter; also call
          ``future.cancel()`` for futures that have not yet started.
        - ``registered`` entries: call
          ``adapter.cancel(invocation_handle)`` so the adapter can
          attempt to abort the in-flight invocation. Running futures
          still drain to a durable ``state_exit``; the cancel call is
          best effort.
        - ``done`` entries: no-op.
        """
        # Snapshot the actions to take under the lock, then perform
        # them outside the lock so adapter cancel and future cancel
        # cannot deadlock against the registry lock.
        #
        # Slice A: ``cancel_requested`` is set for BOTH pending and
        # registered entries. The pending case is the obvious one
        # (worker has not yet invoked the adapter; flag short
        # circuits the top-of-loop check). The registered case
        # covers the small window between ``on_prepared`` and
        # ``actor_invoke_start`` where the worker has registered the
        # handle but not yet called ``adapter.invoke``: the worker's
        # post-register check reads the flag and takes the
        # cancelled path without invoking. Without this the
        # registered-cancel branch would call ``adapter.cancel`` on
        # a not-yet-invoked handle while the worker happily fires
        # ``adapter.invoke`` anyway.
        pending_to_cancel: list[str] = []
        registered_to_cancel: list[tuple[Adapter, PreparedInvocation]] = []
        with self._lock:
            for name, entry in self._entries.items():
                if entry.state == "pending":
                    entry.cancel_requested = True
                    pending_to_cancel.append(name)
                elif entry.state == "registered":
                    entry.cancel_requested = True
                    if (
                        entry.adapter is not None
                        and entry.invocation_handle is not None
                    ):
                        registered_to_cancel.append(
                            (entry.adapter, entry.invocation_handle)
                        )
        for name in pending_to_cancel:
            fut = futures.get(name)
            if fut is not None:
                fut.cancel()
        for adapter, handle in registered_to_cancel:
            try:
                adapter.cancel(handle)
            except Exception:
                # An adapter raising from cancel must not stall the
                # controller. The worker still drains to state_exit.
                pass


def _envelope_to_view(env: Envelope) -> dict[str, Any]:
    """Render an Envelope as a snapshot dict for fan-out workers.

    Workers receive an immutable, read-only snapshot of pre-fan-out
    envelopes. The plan's "sibling visibility rule" forbids workers
    from seeing each other's envelopes, so the snapshot is taken
    once at fan-out entry and never mutated.
    """
    return {
        "outcome": env.outcome,
        "status": env.status,
        "duration_ms": env.duration_ms,
        "attempt": env.attempt,
        "payload": env.payload,
    }
