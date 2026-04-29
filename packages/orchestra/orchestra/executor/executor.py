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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

        self._log.write(
            "state_enter",
            state_id=state.name,
            attempt=attempt,
            fields={
                "attempts": dict(self._attempts),
                "retries": dict(self._retries),
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
                    state, prepared, payload, attempt
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
            },
        )

        # Step 9: transition selection.
        target = self._select_transition(state, envelope)

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

    def _resolve_prompt(self, state: StateDecl) -> str | None:
        prompt = state.prompt
        if prompt is None and state.role is not None:
            for r in self._wf.roles:
                if r.name == state.role:
                    prompt = r.default_prompt
                    break
        if prompt is None:
            return None
        return self._render_prompt(prompt)

    def _render_prompt(self, source: PromptSource) -> str:
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
                else:
                    art = self._store.read_latest(var)
                    substitutions[var] = art.value if art else None
            return _format(template, substitutions)
        if source.kind == "from":
            raise ExecutorError("'prompt from' references are slice-2 territory")
        raise ExecutorError(f"unknown prompt source kind: {source.kind!r}")

    def _read_artifacts(self, state: StateDecl) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in state.reads:
            if r in self._external:
                out[r] = {"value": self._external[r], "__version_id": ""}
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
        if kind == "model":
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
        # API for this, so we reach into its connection here. A
        # cleaner factoring lives in slice 2 when the store grows
        # other multi-tentative-management features.
        conn = self._store._conn
        prefix = f"{state_name}#"
        cur = conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute(
                """
                SELECT seq FROM versions
                WHERE is_tentative = 1 AND written_by LIKE ?
                """,
                (prefix + "%",),
            )
            rows = cur.fetchall()
            seqs = [r[0] for r in rows]
            for seq in seqs:
                cur.execute("DELETE FROM versions WHERE seq = ?", (seq,))
                cur.execute(
                    "DELETE FROM tentative_handles WHERE seq = ?", (seq,)
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def _select_transition(self, state: StateDecl, envelope: Envelope) -> str:
        # Build artifact and envelope dicts for guard evaluation.
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
        # First pass: pick a transition whose outcome matches.
        # ``retry max N then T`` is sugar for: re-enter this state up
        # to N times, then transition to T. We honor that here.
        for t in state.transitions:
            if t.outcome != envelope.outcome:
                continue
            if t.guard is not None and not guards.evaluate(t.guard, ctx):
                continue
            if t.retry_max is not None:
                if self._retries.get(state.name, 0) < t.retry_max:
                    return state.name
                return t.target
            return t.target
        raise ExecutorError(
            f"state {state.name!r}: no transition matched outcome {envelope.outcome!r}"
        )

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
        """
        if timeout_ms is None:
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
