"""Shared type surface for executor mixins.

The concrete :class:`Executor` is assembled from several mixins. Mypy
checks each mixin in isolation, so cross-mixin attributes and helper
methods have to be declared in one shared base.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestra.adapters.base import Adapter
from orchestra.config import CriterionDecl
from orchestra.executor._executor_common import (
    FanOutSnapshot,
    ProgressWatchdogFactory,
)
from orchestra.executor.criteria import DecisionConsistencyMode
from orchestra.log import LogWriter
from orchestra.registry import ProfileRegistry
from orchestra.schema import SchemaSpec
from orchestra.spine import (
    ArtifactDecl,
    Envelope,
    PreparedInvocation,
    PromptSource,
    StateDecl,
    Transition,
    Workflow,
)
from orchestra.store import ArtifactStore
from orchestra.visibility import VisibilityIndex

ProgressCallback = Callable[
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


class _ExecutorMixinBase:
    """Attribute and method declarations shared by executor mixins."""

    _wf: Workflow
    _registry: ProfileRegistry
    _store: ArtifactStore
    _log: LogWriter
    _run_dir: Path
    _run_id: str
    _external: dict[str, Any]
    _attempts: dict[str, int]
    _retries: dict[str, int]
    _envelopes: dict[str, Envelope]
    _last_outcome: str | None
    _last_state: str | None
    _current_state: str
    _step_count: int
    _payloads_dir: Path
    _invocation_options: dict[str, Any]
    _visibility_index: VisibilityIndex
    _attempt_lock: threading.Lock
    _envelope_lock: threading.Lock
    _progress_callback: ProgressCallback | None
    _progress_state_indices: dict[str, int]
    _progress_fan_out_range_starts: dict[str, int]
    _progress_total: int
    _progress_lock: threading.Lock
    _progress_watchdog_factory: ProgressWatchdogFactory
    _actor_progress_interval_seconds: float
    _fan_out_progress_interval_seconds: float
    _on_state_exit_callback: Callable[[StateDecl, Envelope, str | None], None] | None
    _criteria: tuple[CriterionDecl, ...]
    _decision_consistency_mode: DecisionConsistencyMode
    _schema_specs: dict[str, SchemaSpec]
    _schema_artifacts: dict[str, ArtifactDecl]
    _schema_handled_artifacts: set[str]

    def _emit_state_exit_hook(
        self,
        state: StateDecl,
        envelope: Envelope,
        payload_ref: str | None,
    ) -> None:
        raise NotImplementedError

    def _emit_progress(
        self,
        kind: str,
        state: StateDecl,
        elapsed_seconds: float | None = None,
        *,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        raise NotImplementedError

    def _start_progress_watchdog(
        self,
        kind: str,
        state: StateDecl,
        interval_seconds: float,
    ) -> Callable[[], None]:
        raise NotImplementedError

    def _invoke_actor_with_progress(
        self,
        state: StateDecl,
        adapter: Adapter,
        prepared: PreparedInvocation,
        timeout_ms: int | None,
        *,
        emit_actor_progress: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _run_one_state(self) -> str:
        raise NotImplementedError

    def _execute_state_body(
        self,
        state_name: str,
        snapshot: FanOutSnapshot | None = None,
        on_prepared: Callable[[PreparedInvocation, str], None] | None = None,
        is_cancelled_after_register: Callable[[], bool] | None = None,
        suppress_actor_progress: bool = False,
    ) -> Envelope:
        raise NotImplementedError

    def _invoke_with_timeout(
        self,
        adapter: Adapter,
        prepared: PreparedInvocation,
        timeout_ms: int | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _read_artifacts(
        self,
        state: StateDecl,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _write_payload(self, invocation_id: str, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def _dispatch_parsers(
        self,
        state: StateDecl,
        prepared: PreparedInvocation,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str | None = None,
    ) -> list[str]:
        raise NotImplementedError

    def _artifact_writes_record(
        self,
        state: StateDecl,
        prepared: PreparedInvocation | None,
        payload: dict[str, Any],
        committed_ids: list[str],
    ) -> list[dict[str, str]]:
        raise NotImplementedError

    def _state_schema_artifact(self, state: StateDecl) -> ArtifactDecl | None:
        raise NotImplementedError

    def _apply_schema_layer(
        self,
        state: StateDecl,
        payload: dict[str, Any],
        attempt: int,
        invocation_id: str,
        existing_handles: list[str],
        payload_ref: str | None,
    ) -> tuple[list[str], str | None, Any]:
        raise NotImplementedError

    def _discard_stale_tentatives(self, state_name: str) -> None:
        raise NotImplementedError

    def _run_fan_out_group(
        self,
        parent_state: StateDecl,
        parent_envelope: Envelope,
        transition: Transition,
    ) -> str:
        raise NotImplementedError

    def _fan_out_child_worker(
        self,
        child_name: str,
        registry: Any,
        snapshot_envelopes: dict[str, dict[str, Any]],
        snapshot_artifacts: dict[str, Any],
        snapshot_attempts: dict[str, int],
        snapshot_retries: dict[str, int],
    ) -> Envelope:
        raise NotImplementedError

    def close_fan_out_pending_transition(
        self,
        *,
        parent_state_name: str,
        parent_attempt: int,
        target: str,
    ) -> str:
        raise NotImplementedError

    def resume_fan_out(
        self,
        parent_state_name: str,
        children: list[str],
        join_target: str,
        error_target: str,
        completed_children: dict[str, Envelope],
        parent_attempt: int | None = None,
    ) -> str:
        raise NotImplementedError

    def _close_resumed_fan_out_transition(
        self,
        parent_state_name: str,
        parent_attempt: int | None,
        target: str,
    ) -> str:
        raise NotImplementedError

    def _write_cancelled_state_exit(self, child_name: str) -> Envelope:
        raise NotImplementedError

    def _derive_outcome(self, state: StateDecl, payload: dict[str, Any], status: str) -> str:
        raise NotImplementedError

    def _close_pending_transition(
        self,
        *,
        state_id: str,
        attempt: int,
        outcome: str,
        target: str,
    ) -> str:
        raise NotImplementedError

    def _select_transition(self, state: StateDecl, envelope: Envelope) -> str:
        raise NotImplementedError

    def _select_transition_decl(
        self,
        state: StateDecl,
        envelope: Envelope,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> Transition | None:
        raise NotImplementedError

    def _run_transform_linear(self, state: StateDecl) -> str:
        raise NotImplementedError

    def _execute_transform_body(
        self,
        state_name: str,
        snapshot: FanOutSnapshot | None = None,
    ) -> Envelope:
        raise NotImplementedError

    def resume_pending_transition(self, state_name: str) -> str:
        raise NotImplementedError

    def _build_actor_binding(self, state: StateDecl) -> dict[str, Any]:
        raise NotImplementedError

    def _resolve_prompt(
        self,
        state: StateDecl,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> str | None:
        raise NotImplementedError

    def _render_prompt(
        self,
        source: PromptSource,
        *,
        snapshot: FanOutSnapshot | None = None,
    ) -> str:
        raise NotImplementedError
