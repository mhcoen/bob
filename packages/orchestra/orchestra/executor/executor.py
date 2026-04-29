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
"""

from __future__ import annotations

import json
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
        # Track whether the current entry into a state is a retry-from-self
        # (which increments retries.<state>) versus a normal entry.
        self._last_outcome: str | None = None
        self._last_state: str | None = None
        self._current_state: str = current_state or workflow.start_state_name()
        self._step_count = 0
        self._payloads_dir = run_dir / "payloads"
        self._payloads_dir.mkdir(parents=True, exist_ok=True)

    # ----- entry points ------------------------------------------

    def run_to_completion(self) -> str:
        """Run states until a terminal target is reached.

        Returns the terminal target string (``done`` or ``stop``).
        """
        while True:
            outcome = self.step()
            if outcome in _TERMINAL_TARGETS:
                return outcome

    def step(self) -> str:
        """Run exactly one state.

        Returns the transition target: a state name, ``done``, or
        ``stop``. Tests that need to interleave executor activity with
        manual log writes (such as Test C) call this directly rather
        than ``run_to_completion``.
        """
        return self._run_one_state()

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
        # Carry options through backing_options too, so adapters that
        # need them (the human adapter) read from one place.
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
            schema=None,  # slice 1 has no schemas
            backing_options=backing_options,
            timeout_ms=timeout_ms,
        )

        # Step 3: prepare invocation.
        adapter: Adapter = self._registry.adapter_for(state.actor.kind)
        prepared = adapter.prepare(request)
        self._log.write(
            "actor_prepare",
            state_id=state.name,
            attempt=attempt,
            fields={"summary": prepared.summary},
        )

        # Step 4: invoke.
        started_at = _now_iso()
        started_perf = time.perf_counter()
        self._log.write(
            "actor_invoke_start",
            state_id=state.name,
            attempt=attempt,
            fields={"actor_binding": actor_binding},
        )
        error_record: ErrorRecord | None = None
        payload: dict[str, Any]
        try:
            payload = adapter.invoke(prepared)
        except Exception as exc:
            error_record = ErrorRecord(
                kind="actor_failure",
                message=str(exc),
                detail={"exception": type(exc).__name__},
            )
            payload = {}
        ended_at = _now_iso()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)

        # Persist the payload to disk and record payload_ref.
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

        status = "error" if error_record is not None else "ok"
        outcome = self._derive_outcome(state, payload, status)

        # Step 5: postcondition checks. Slice 1 registers none.

        artifacts_written: list[dict[str, str]] = []
        # Step 6: result parser dispatch.
        tentative_handles: list[str] = []
        if status == "ok" and state.writes:
            try:
                tentative_handles = self._dispatch_parsers(
                    state, prepared, payload, attempt
                )
            except Exception as exc:
                # Parser failure: discard tentatives, set error.
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
        if status == "ok" and tentative_handles:
            committed_ids = self._store.commit_tentative(tentative_handles)
            # Re-walk parser outputs to pair committed version IDs
            # with their artifact names. We re-run the parser fn purely
            # for naming; this is cheap because parsers are pure.
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
            fields={"outcome": envelope.outcome, "target": target},
        )

        # Step 11: route.
        self._last_state = state.name
        self._last_outcome = envelope.outcome
        if target in _TERMINAL_TARGETS:
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
        # Prefer the state-level prompt; fall back to the role's default.
        prompt = state.prompt
        if prompt is None and state.role is not None:
            for r in self._wf.roles:
                if r.name == state.role:
                    prompt = r.default_prompt
                    break
        if prompt is None:
            # Non-LLM backings (shell, human) may have prompts (human
            # gates display a prompt). For human, pick up from
            # state-level prompt if set.
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
            # Slice 1 does not exercise this path. Resolution would
            # walk envelopes for a 'prompt' artifact field. We surface
            # the limitation rather than silently returning empty.
            raise ExecutorError("'prompt from' references are slice-2 territory")
        raise ExecutorError(f"unknown prompt source kind: {source.kind!r}")

    def _read_artifacts(self, state: StateDecl) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in state.reads:
            if r in self._external:
                # External inputs flow through reads-by-name too, which
                # matches the design's "external inputs are referenced
                # in templates by name" rule.
                out[r] = {"value": self._external[r], "__version_id": ""}
                continue
            art = self._store.read_latest(r)
            if art is not None:
                out[r] = {"value": art.value, "__version_id": art.version_id}
            else:
                out[r] = {"value": None, "__version_id": ""}
        return out

    def _write_payload(self, seq: int, payload: dict[str, Any]) -> str:
        payload_path = self._payloads_dir / f"{self._run_id}-{seq}.json"
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(_strip_internal(payload), fh, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        return f"payloads/{self._run_id}-{seq}.json"

    def _derive_outcome(
        self, state: StateDecl, payload: dict[str, Any], status: str
    ) -> str:
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
        # Side-channel: tell parsers what writes the state declared.
        # The identity parser uses ``_declared_writes`` to know which
        # artifact name(s) to emit values for.
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
            pairs = parser.fn(envelope_for_parser, self._store)
            for name, value in pairs:
                handle = self._store.tentative_write(
                    name,
                    value,
                    written_by=f"{state.name}#{attempt}",
                )
                handles.append(handle)
        return handles

    def _artifact_writes_record(
        self,
        state: StateDecl,
        prepared: PreparedInvocation,
        payload: dict[str, Any],
        committed_ids: list[str],
    ) -> list[dict[str, str]]:
        """Pair committed version IDs with their artifact names by
        re-running the parsers in their declared order.

        This duplicates a tiny bit of work but keeps the dispatch path
        and the recording path out of each other's hair.
        """
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
        for t in state.transitions:
            if t.outcome != envelope.outcome:
                continue
            if t.guard is None or guards.evaluate(t.guard, ctx):
                return t.target
        # No transition matched. Fail closed.
        raise ExecutorError(
            f"state {state.name!r}: no transition matched outcome {envelope.outcome!r}"
        )


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
