"""Backward-compatible re-export shim for ``orchestra.executor.executor``.

Implementation moved to focused sibling modules:
  _executor_common      free helpers + data classes (leaf)
  _executor_progress    _ProgressMixin
  _executor_schema      _SchemaMixin
  _executor_transition  _TransitionMixin
  _executor_fan_out     _FanOutMixin
  _executor_state_exec  _StateExecMixin (the per-state execution loop)
  _executor_core        the Executor class itself (inherits from the mixins)

Every name previously importable from ``orchestra.executor.executor``
continues to work via this shim. New code may import from the focused
submodules directly.
"""

from __future__ import annotations

from orchestra.executor._executor_common import (  # noqa: F401
    ACTOR_PROGRESS_INTERVAL_SECONDS,
    FAN_OUT_PROGRESS_INTERVAL_SECONDS,
    FanOutSnapshot,
    ProgressWatchdogFactory,
    _CancellationRegistry,
    _ChildEntry,
    _DefaultMissing,
    _JsonExtractError,
    _TERMINAL_TARGETS,
    _TimeoutSignal,
    _adapter_manages_own_timeout,
    _balanced_json_spans,
    _coerce_to_text,
    _default_progress_watchdog_factory,
    _envelope_to_view,
    _error_to_dict,
    _extract_last_json_object,
    _format,
    _now_iso,
    _payload_summary,
    _scan_balanced_object,
    new_run_id,
)
from orchestra.executor._executor_core import Executor  # noqa: F401

__all__ = [
    "ACTOR_PROGRESS_INTERVAL_SECONDS",
    "Executor",
    "FAN_OUT_PROGRESS_INTERVAL_SECONDS",
    "FanOutSnapshot",
    "ProgressWatchdogFactory",
    "_CancellationRegistry",
    "_ChildEntry",
    "_DefaultMissing",
    "_JsonExtractError",
    "_TERMINAL_TARGETS",
    "_TimeoutSignal",
    "_adapter_manages_own_timeout",
    "_balanced_json_spans",
    "_coerce_to_text",
    "_default_progress_watchdog_factory",
    "_envelope_to_view",
    "_error_to_dict",
    "_extract_last_json_object",
    "_format",
    "_now_iso",
    "_payload_summary",
    "_scan_balanced_object",
    "new_run_id",
]
