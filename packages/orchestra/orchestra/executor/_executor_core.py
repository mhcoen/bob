"""The central ``Executor`` class.

Inherits from _ProgressMixin, _SchemaMixin, _TransitionMixin, _FanOutMixin,
_StateExecMixin (each in a sibling module). Contains the lifecycle
entrypoints (__init__, run_to_completion, step), prompt resolution, and
actor-binding helpers. The per-state execution loop lives in
_StateExecMixin.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestra.config import CriterionDecl
from orchestra.errors import ExecutorError
from orchestra.executor._executor_common import (
    _TERMINAL_TARGETS,
    ACTOR_PROGRESS_INTERVAL_SECONDS,
    FAN_OUT_PROGRESS_INTERVAL_SECONDS,
    FanOutSnapshot,
    ProgressWatchdogFactory,
    _default_progress_watchdog_factory,
    _format,
)
from orchestra.executor._executor_fan_out import _FanOutMixin
from orchestra.executor._executor_progress import _ProgressMixin
from orchestra.executor._executor_schema import _SchemaMixin
from orchestra.executor._executor_state_exec import _StateExecMixin
from orchestra.executor._executor_transition import _TransitionMixin
from orchestra.executor.criteria import (
    DecisionConsistencyMode,
)
from orchestra.log import LogWriter
from orchestra.registry import ProfileRegistry
from orchestra.schema import SchemaSpec, load_schema
from orchestra.spine import (
    ArtifactDecl,
    Envelope,
    PromptSource,
    StateDecl,
    Workflow,
)
from orchestra.store import ArtifactStore
from orchestra.visibility import VisibilityIndex


class Executor(_ProgressMixin, _SchemaMixin, _TransitionMixin, _FanOutMixin, _StateExecMixin):
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
        # once per ``state_exit`` for sequential states, plus periodic
        # ``actor_progress`` heartbeats while an actor invocation is
        # in flight. For parallel groups it fires once per
        # ``fan_out_start``, periodic ``fan_out_progress`` heartbeats
        # while the group is open, and once per ``fan_out_end``. The
        # callback receives
        # ``(kind, state_name, role, index, total, elapsed_seconds,
        # children)`` where ``children`` is a tuple of
        # ``(child_state_name, child_role)`` pairs on
        # ``fan_out_start`` and ``None`` otherwise. ``index`` is the
        # 1-based ordinal of the state's first entry (retries reuse
        # the same index); for ``fan_out_start`` and
        # ``fan_out_progress`` it is the index of the first child in
        # the dispatch range. ``total`` is the count of declared
        # states in the workflow. ``elapsed_seconds`` is ``None`` for
        # the start events, a float duration for the exit events, and
        # elapsed watchdog time for progress events. The api wraps the
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
        ]
        | None = None,
        # F2.5a decision-consistency invariant. When ``criteria`` is
        # non-empty the executor enforces the configured criteria
        # against each schema-validated verdict's
        # ``criteria_compliance`` field, with the strength governed by
        # ``decision_consistency_mode``. Empty tuple ⇒ checks
        # disabled (back-compat for non-calibration workflows).
        criteria: tuple[CriterionDecl, ...] = (),
        decision_consistency_mode: DecisionConsistencyMode = (DecisionConsistencyMode.ACCEPT_ONLY),
        progress_watchdog_factory: ProgressWatchdogFactory | None = None,
        actor_progress_interval_seconds: float = ACTOR_PROGRESS_INTERVAL_SECONDS,
        fan_out_progress_interval_seconds: float = (FAN_OUT_PROGRESS_INTERVAL_SECONDS),
        # Optional incremental-transcript hook. Fires after each
        # state_exit log record has been durably written (and after
        # the visibility index update). Receives ``(state, envelope,
        # payload_ref)``; raises from the callback are swallowed so a
        # misbehaving transcript writer cannot abort an in-flight run.
        # The executor invokes this synchronously from the same thread
        # that wrote ``state_exit``, which for fan-out children is a
        # worker thread; callbacks that touch shared state must be
        # thread-safe.
        on_state_exit: Callable[[StateDecl, Envelope, str | None], None] | None = None,
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
        self._progress_fan_out_range_starts: dict[str, int] = {}
        self._progress_total: int = len(workflow.states)
        self._progress_lock: threading.Lock = threading.Lock()
        self._progress_watchdog_factory = (
            progress_watchdog_factory
            if progress_watchdog_factory is not None
            else _default_progress_watchdog_factory
        )
        self._actor_progress_interval_seconds = actor_progress_interval_seconds
        self._fan_out_progress_interval_seconds = fan_out_progress_interval_seconds
        self._on_state_exit_callback: Callable[[StateDecl, Envelope, str | None], None] | None = (
            on_state_exit
        )
        # F2.5a decision-consistency invariant configuration.
        self._criteria: tuple[CriterionDecl, ...] = criteria
        self._decision_consistency_mode: DecisionConsistencyMode = decision_consistency_mode
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
