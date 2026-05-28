"""Module-level helpers and data classes for the executor.

Leaf module: imported by every mixin and the core executor; imports no sibling executor modules itself.
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
from orchestra.config import CriterionDecl
from orchestra.errors import ExecutorError
from orchestra.executor import guards
from orchestra.executor.criteria import (
    DecisionConsistencyMode,
    DecisionConsistencyResult,
    check_decision_consistency,
)
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

ACTOR_PROGRESS_INTERVAL_SECONDS = 30.0

FAN_OUT_PROGRESS_INTERVAL_SECONDS = 30.0

ProgressWatchdogFactory = Callable[[float, Callable[[], None]], Callable[[], None]]

def _default_progress_watchdog_factory(
    interval_seconds: float,
    emit_progress: Callable[[], None],
) -> Callable[[], None]:
    stop = threading.Event()

    def _run() -> None:
        while not stop.wait(interval_seconds):
            if stop.is_set():
                return
            emit_progress()

    thread = threading.Thread(
        target=_run,
        name="orchestra-progress-watchdog",
        daemon=True,
    )
    thread.start()
    return stop.set

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

class _JsonExtractError(Exception):
    """Raised by ``_extract_last_json_object`` when no balanced JSON
    object span in the model output parses cleanly."""

def _extract_last_json_object(text: str) -> Any:
    """Tolerant JSON-object extraction for schema-backed model output.

    Scans ``text`` for balanced top-level ``{...}`` spans, respecting
    JSON string and escape boundaries, then attempts ``json.loads``
    from the last span to the first and returns the first that
    parses. The extractor is schema-agnostic: once it returns a
    parsed object, schema validation is the runtime's responsibility
    and is not retried here.

    Raises ``_JsonExtractError`` when no balanced object exists or
    when no candidate parses cleanly. The caller surfaces the
    exception's message in the schema_validation log record's
    ``validation_errors`` list.

    Real LLM CLI output wraps the model's JSON answer in non-JSON
    content: codex prepends a banner and prompt-echo and appends a
    "tokens used" footer; claude wraps the JSON in a markdown
    ``json fence with prose preamble. Strict ``json.loads`` on the
    raw payload's ``output`` field fails at line 1 col 0 in both
    cases. This extractor is the runtime's parse-tolerance contract:
    we accept any output that contains a parseable JSON object and
    let schema validation decide shape correctness.
    """
    spans = _balanced_json_spans(text)
    if not spans:
        raise _JsonExtractError("no balanced JSON object found in model output")
    last_parse_error: Exception | None = None
    for start, end in reversed(spans):
        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_parse_error = exc
            continue
    if last_parse_error is not None:
        raise _JsonExtractError(f"no balanced JSON object parsed cleanly: {last_parse_error}")
    raise _JsonExtractError("no balanced JSON object parsed cleanly")

def _balanced_json_spans(text: str) -> list[tuple[int, int]]:
    """Return half-open ``(start, end)`` indices of every balanced
    top-level ``{...}`` span in ``text``.

    A "top-level" span has its outermost ``{`` not contained inside
    another open object. The scanner respects JSON string boundaries:
    ``{`` and ``}`` characters inside double-quoted strings do not
    affect brace depth, and string-escape sequences (``\\"``,
    ``\\\\``, etc.) do not terminate the string prematurely. This
    makes the scanner transparent to nested markdown fences,
    JSON-shaped fragments inside string values, and trailing garbage
    after the closing brace.

    Square-bracket arrays are not extracted as top-level candidates;
    schema-backed verdicts in v0 are JSON objects only.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            end = _scan_balanced_object(text, i)
            if end is not None:
                spans.append((i, end + 1))
                i = end + 1
                continue
        i += 1
    return spans

def _scan_balanced_object(text: str, start: int) -> int | None:
    """Given ``text[start] == '{'``, return the index of the matching
    closing ``}`` or ``None`` if no balanced match exists.

    Tracks brace depth while honoring JSON string boundaries. Inside
    a double-quoted string, only an unescaped ``"`` ends the string;
    a backslash escapes the next character regardless of what it is.
    """
    depth = 0
    in_string = False
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
            if depth < 0:
                return None
        i += 1
    return None

def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

class _TimeoutSignal(Exception):
    pass

def _adapter_manages_own_timeout(adapter: Adapter, prepared: PreparedInvocation) -> bool:
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

    def request_cancel_all(self, futures: dict[str, Future[Envelope]]) -> None:
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
                    if entry.adapter is not None and entry.invocation_handle is not None:
                        registered_to_cancel.append((entry.adapter, entry.invocation_handle))
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
