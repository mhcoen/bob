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
from orchestra.registry import ProfileRegistry
from orchestra.spine import (
    Envelope,
    ErrorRecord,
    InvocationRequest,
    PreparedInvocation,
    PromptSource,
    StateDecl,
    Workflow,
)
from orchestra.store import ArtifactStore
from orchestra.visibility import VisibilityIndex, make_invocation_id

_TERMINAL_TARGETS = {"done", "stop"}


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
        # Per-call invocation options. Merged into every state's
        # backing_options at invoke time so adapters see the overrides
        # without polluting the workflow's external_inputs surface.
        invocation_options: dict[str, Any] | None = None,
        # Slice A: an externally-supplied visibility index. The
        # executor builds one with a persisted snapshot in run_dir
        # when omitted. The store consults the same instance through
        # set_visibility_index().
        visibility_index: VisibilityIndex | None = None,
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
        self._last_outcome: str | None = None
        self._last_state: str | None = None
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
        self._discard_stale_tentatives(state.name, attempt)

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
        payload_ref = self._write_payload(self._log.next_seq, payload)
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

        # Step 10: check step budget.
        self._step_count += 1
        if self._step_count >= self._wf.max_total_steps:
            if target not in _TERMINAL_TARGETS:
                self._log.write(
                    "step_budget_exhausted",
                    state_id=state.name,
                    attempt=attempt,
                    fields={"max_total_steps": self._wf.max_total_steps},
                )
                target = "stop"

        self._log.write(
            "transition",
            state_id=state.name,
            attempt=attempt,
            fields={
                "outcome": envelope.outcome,
                "target": target,
                "step_count": self._step_count,
            },
        )

        # Step 11: route.
        self._last_state = state.name
        self._last_outcome = envelope.outcome
        if target in _TERMINAL_TARGETS:
            self._current_state = target
            return target
        self._current_state = target
        return target

    # ----- helpers ------------------------------------------------

    def _build_actor_binding(self, state: StateDecl) -> dict[str, Any]:
        binding: dict[str, Any] = {"kind": state.actor.kind}
        if state.actor.ref is not None:
            if state.actor.kind == "model":
                binding["model"] = state.actor.ref
            elif state.actor.kind == "agent":
                binding["agent"] = state.actor.ref
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

    def _write_payload(self, seq: int, payload: dict[str, Any]) -> str:
        """Persist a payload to disk with fsync.

        Caller treats this as a durability boundary: the payload file
        exists on disk by the time this returns. The log record that
        references it is written after, ensuring no log record points
        at a missing payload.
        """
        import os as _os

        payload_path = self._payloads_dir / f"{self._run_id}-{seq}.json"
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(_strip_internal(payload), fh, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            _os.fsync(fh.fileno())
        return f"payloads/{self._run_id}-{seq}.json"

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
        """
        write_types = tuple(w.type for w in state.writes)
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
                    {"name": w.name, "type": w.type} for w in state.writes
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
        pure, so re-running is safe and cheap."""
        write_types = tuple(w.type for w in state.writes)
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
                    {"name": w.name, "type": w.type} for w in state.writes
                ],
            },
            error=None,
        )
        names: list[str] = []
        for parser in parsers:
            for name, _ in parser.fn(envelope_for_parser, self._store):
                names.append(name)
        out: list[dict[str, str]] = []
        for n, vid in zip(names, committed_ids, strict=False):
            out.append({"artifact": n, "version_id": vid})
        return out

    def _discard_stale_tentatives(self, state_name: str, attempt: int) -> None:
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
        self, state: StateDecl, envelope: Envelope
    ) -> Any:
        """Return the matched ``Transition`` declaration or None.

        Used by the run loop to dispatch on fan_out vs linear. Linear
        callers can use ``_select_transition`` for the resolved target
        string.
        """
        artifact_values: dict[str, Any] = {}
        for art in self._wf.artifacts:
            v = self._store.read_latest(art.name)
            artifact_values[art.name] = v.value if v else None
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
            attempts=self._attempts,
            retries=self._retries,
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

        registry = _CancellationRegistry()
        for child_name in transition.fan_out:
            registry.register_pending(child_name)

        # Submit child futures.
        per_child_attempt: dict[str, int] = {}
        with self._attempt_lock:
            for child_name in transition.fan_out:
                self._attempts[child_name] = (
                    self._attempts.get(child_name, 0) + 1
                )
                self._retries[child_name] = 0
                per_child_attempt[child_name] = self._attempts[child_name]

        futures: dict[str, Future[Envelope]] = {}
        executor = ThreadPoolExecutor(
            max_workers=max(1, len(transition.fan_out)),
            thread_name_prefix="orchestra-fan-out",
        )
        try:
            for child_name in transition.fan_out:
                attempt = per_child_attempt[child_name]
                fut = executor.submit(
                    self._fan_out_child_worker,
                    child_name,
                    attempt,
                    registry,
                    snapshot_envelopes,
                    snapshot_artifacts,
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
        return str(target)

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
            self._current_state = str(target)
            return str(target)

        registry = _CancellationRegistry()
        for child_name in pending_children:
            registry.register_pending(child_name)

        # Mint fresh attempt_seq for pending children. Per the plan's
        # fresh-budget-on-replay rule, ``retries`` resets to 0 for
        # each re-entered child.
        per_child_attempt: dict[str, int] = {}
        with self._attempt_lock:
            for child_name in pending_children:
                self._attempts[child_name] = (
                    self._attempts.get(child_name, 0) + 1
                )
                self._retries[child_name] = 0
                per_child_attempt[child_name] = self._attempts[child_name]

        if pending_children:
            futures: dict[str, Future[Envelope]] = {}
            executor = ThreadPoolExecutor(
                max_workers=max(1, len(pending_children)),
                thread_name_prefix="orchestra-fan-out-resume",
            )
            try:
                for child_name in pending_children:
                    attempt = per_child_attempt[child_name]
                    fut = executor.submit(
                        self._fan_out_child_worker,
                        child_name,
                        attempt,
                        registry,
                        snapshot_envelopes,
                        snapshot_artifacts,
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
        self._current_state = str(target)
        return str(target)

    def _fan_out_child_worker(
        self,
        child_name: str,
        attempt: int,
        registry: _CancellationRegistry,
        snapshot_envelopes: dict[str, dict[str, Any]],
        snapshot_artifacts: dict[str, Any],
    ) -> Envelope:
        """Run one fan-out child to a durable ``state_exit``.

        Workers mint their own invocation_id. They share the
        VisibilityIndex (which is its own thread-safe primitive) and
        the LogWriter and ArtifactStore (each lock-guarded). Workers
        do NOT mutate ``self._current_state``, ``self._step_count``,
        or write outgoing transition records; the fan-out controller
        owns those. Artifact reads and prompt template substitutions
        consume the captured snapshot, never the live store, so a
        sibling write that lands mid-fan-out is invisible.

        Slice A child-local retry: when the per-state body returns
        an error or timeout envelope and the child's state declares
        ``on error retry max N then T`` (or the timeout equivalent),
        the worker increments ``retries[child_name]`` under
        ``_attempt_lock``, mints a fresh invocation_id with a new
        attempt_seq, and re-runs the body. Loops until success or
        retry budget exhausted, then returns the final envelope.
        The per-entry budget rule applies: ``retries[child_name]``
        starts at 0 on first entry; the controller resets it to 0
        before submitting (in ``_run_fan_out_group``). The
        fresh-budget-on-replay rule (per the plan) is separate and
        applies only after a CRASH and replay re-entry.
        """
        snapshot = FanOutSnapshot(
            envelopes=dict(snapshot_envelopes),
            artifacts=dict(snapshot_artifacts),
        )
        state = self._wf.state(child_name)
        adapter = self._registry.adapter_for(state.actor.kind)
        current_attempt = attempt
        while True:
            if registry.is_cancelled(child_name):
                return self._write_cancelled_state_exit(
                    child_name, current_attempt
                )
            invocation_id = make_invocation_id(
                self._run_id, child_name, current_attempt
            )

            def _on_prepared(
                prepared: PreparedInvocation,
                _name: str = child_name,
                _id: str = invocation_id,
                _adapter: Adapter = adapter,
            ) -> None:
                registry.mark_started(_name, _id, prepared, _adapter)

            def _is_cancelled_after_register(
                _name: str = child_name,
            ) -> bool:
                return registry.is_cancelled(_name)

            envelope = self._execute_state_body(
                child_name,
                current_attempt,
                invocation_id,
                snapshot=snapshot,
                on_prepared=_on_prepared,
                is_cancelled_after_register=_is_cancelled_after_register,
            )
            should_retry = False
            outcome = envelope.outcome
            for t in state.transitions:
                if t.outcome != outcome or t.retry_max is None:
                    continue
                with self._attempt_lock:
                    used = self._retries.get(child_name, 0)
                    if used < t.retry_max:
                        self._retries[child_name] = used + 1
                        should_retry = True
                break
            if not should_retry:
                registry.mark_done(child_name)
                return envelope
            with self._attempt_lock:
                self._attempts[child_name] = (
                    self._attempts.get(child_name, 0) + 1
                )
                current_attempt = self._attempts[child_name]

    def _write_cancelled_state_exit(
        self, child_name: str, attempt: int
    ) -> Envelope:
        """Emit a state_enter/state_exit pair for a cancelled child
        that never invoked its adapter, so replay sees a complete
        durable record for the invocation."""
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
        attempt: int,
        invocation_id: str,
        snapshot: FanOutSnapshot | None = None,
        on_prepared: Callable[[PreparedInvocation], None] | None = None,
        is_cancelled_after_register: Callable[[], bool] | None = None,
    ) -> Envelope:
        """Run steps 1-9 of the per-state sequence for ``state_name``
        with the supplied attempt and invocation_id.

        Used by both the linear ``_run_one_state`` (which then does
        transition selection) and the fan-out child worker (which
        skips transition selection per the plan's "fan-out child"
        mode). The body mints no counters of its own; callers
        increment ``self._attempts`` / ``self._retries`` under
        ``self._attempt_lock`` before calling.
        """
        state = self._wf.state(state_name)
        self._discard_stale_tentatives(state.name, attempt)
        self._visibility_index.insert_pending(invocation_id)
        with self._attempt_lock:
            attempts_snapshot = dict(self._attempts)
            retries_snapshot = dict(self._retries)
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
                on_prepared(prepared)
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
            payload_ref = self._write_payload(self._log.next_seq, payload)
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


def _strip_internal(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove keys whose names start with '_' (parser-side-channel)."""
    return {k: v for k, v in payload.items() if not k.startswith("_")}


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
    resolution and ``reads`` clauses; live ``read_latest`` calls
    against the store from inside a fan-out child are forbidden so
    siblings cannot leak each other's writes."""

    envelopes: dict[str, dict[str, Any]]
    artifacts: dict[str, Any]


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
