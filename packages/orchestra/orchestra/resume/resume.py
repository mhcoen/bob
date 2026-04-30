"""Log replay and resume-hook dispatch.

After a crash, replay reconstructs:

  1. The current state (or a terminal target if the workflow ended).
  2. The counter table (attempts.<state>, retries.<state>).
  3. The envelopes for every state that has a state_exit record, so
     that guards on resumed transitions can reference prior-state
     results.
  4. The step counter, recovered from the most recent ``transition``
     record's ``step_count`` field.
  5. Whether the last state completed (case 1) or was interrupted
     (case 2).

The artifact store is rebuilt by the SQLite database itself: committed
versions are durable, so reconstruction means simply opening the same
database file. ``Executor._discard_stale_tentatives`` cleans up
tentatives left orphan by an interrupted attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestra.errors import ResumeError
from orchestra.log import LogReader
from orchestra.registry import ProfileRegistry
from orchestra.spine import Envelope, ErrorRecord, Workflow

_TERMINAL_TARGETS = {"done", "stop"}


@dataclass
class ReplayState:
    """The runner's state as reconstructed from the log."""

    last_run_id: str = ""
    next_seq: int = 0
    attempts: dict[str, int] = field(default_factory=dict)
    retries: dict[str, int] = field(default_factory=dict)
    envelopes: dict[str, Envelope] = field(default_factory=dict)
    step_count: int = 0
    current_state: str | None = None
    last_state_completed: bool = False
    """True if the workflow has ended (current_state is terminal) or
    the last state visited was followed by a transition (case 1)."""
    is_terminal: bool = False
    """True if current_state is ``done`` or ``stop``."""
    last_outcome: str | None = None
    last_target: str | None = None
    # Slice A: fan-out group reconstruction. ``open_fan_out`` is the
    # fan_out_start fields when a fan_out group has been opened but
    # not yet closed by a matching fan_out_end. ``last_fan_out_target``
    # is set when a durable fan_out_end was observed; the executor
    # uses it to skip re-running the group.
    open_fan_out: dict[str, Any] | None = None
    last_fan_out_target: str | None = None
    # invocation_id -> "pending" | "success" | "error" rebuilt from
    # state_enter and state_exit records; the resume helper feeds
    # this to ``VisibilityIndex.replace_from``.
    visibility_statuses: dict[str, str] = field(default_factory=dict)


def replay_log(log_path: str) -> ReplayState:
    """Read a log and produce a ReplayState."""
    reader = LogReader(log_path)
    records = reader.read_all()
    state = ReplayState()
    if not records:
        return state

    for rec in records:
        state.last_run_id = rec.run_id
        state.next_seq = max(state.next_seq, rec.seq + 1)
        if rec.event == "state_enter":
            assert rec.state_id is not None
            assert rec.attempt is not None
            state.attempts[rec.state_id] = rec.attempt
            attempts_field = rec.fields.get("attempts")
            if isinstance(attempts_field, dict):
                state.attempts.update(
                    {k: int(v) for k, v in attempts_field.items()}
                )
            retries_field = rec.fields.get("retries")
            if isinstance(retries_field, dict):
                state.retries.update(
                    {k: int(v) for k, v in retries_field.items()}
                )
            state.current_state = rec.state_id
            state.last_state_completed = False
            # Slice A: track invocation status from the log so the
            # resume path can rebuild VisibilityIndex.
            inv_id = rec.fields.get("invocation_id")
            if isinstance(inv_id, str):
                state.visibility_statuses[inv_id] = "pending"
        elif rec.event == "fan_out_start":
            state.open_fan_out = dict(rec.fields)
        elif rec.event == "fan_out_end":
            state.open_fan_out = None
            target = rec.fields.get("target")
            if isinstance(target, str):
                state.last_fan_out_target = target
                state.last_target = target
                state.last_state_completed = True
                state.current_state = target
                if target in _TERMINAL_TARGETS:
                    state.is_terminal = True
        elif rec.event == "state_exit":
            assert rec.state_id is not None
            assert rec.attempt is not None
            state.last_state_completed = True
            outcome = rec.fields.get("outcome")
            state.last_outcome = str(outcome) if outcome is not None else None
            # Slice A: invocation_id -> success/error in the
            # rebuilt visibility map.
            inv_id = rec.fields.get("invocation_id")
            if isinstance(inv_id, str):
                status_field = rec.fields.get("status")
                state.visibility_statuses[inv_id] = (
                    "success" if status_field == "ok" else "error"
                )
            # Rebuild the envelope so guards on resumed transitions
            # can reference this state's results.
            err_field = rec.fields.get("error")
            err: ErrorRecord | None = None
            if isinstance(err_field, dict):
                err = ErrorRecord(
                    kind=err_field.get("kind", "runner_failure"),
                    message=err_field.get("message", ""),
                    detail=err_field.get("detail"),
                )
            envelope = Envelope(
                state_id=rec.state_id,
                attempt=rec.attempt,
                actor_binding={},
                status=rec.fields.get("status", "ok"),
                outcome=str(outcome) if outcome is not None else "",
                started_at="",
                ended_at="",
                duration_ms=int(rec.fields.get("duration_ms", 0) or 0),
                inputs_read=list(rec.fields.get("inputs_read", []) or []),
                artifacts_written=list(rec.fields.get("artifacts_written", []) or []),
                payload={},  # payload is in payload_ref, not loaded by replay
                error=err,
            )
            state.envelopes[rec.state_id] = envelope
        elif rec.event == "transition":
            target = rec.fields.get("target")
            state.last_target = str(target) if target is not None else None
            sc = rec.fields.get("step_count")
            if isinstance(sc, int):
                state.step_count = sc

    # Final routing decision.
    if state.last_state_completed and state.last_target is not None:
        state.current_state = state.last_target
        if state.current_state in _TERMINAL_TARGETS:
            state.is_terminal = True
    elif state.last_state_completed and state.last_target is None:
        # A state_exit was logged but no transition followed (crash in
        # the small window between the two). The current state is the
        # state that exited; the executor will re-select its
        # transition on resume by re-entering ... wait, that's wrong:
        # re-entering would re-run the state. The right resume action
        # is to re-select the transition without re-running. We
        # signal this by leaving last_state_completed=True so that
        # cmd_resume can detect and handle this case.
        # For slice 1 we treat it conservatively as case 2: re-run
        # the state. The executor's stale-tentative discard ensures
        # the rerun produces a fresh attempt.
        state.last_state_completed = False
    else:
        # case 2: state_enter without state_exit. current_state stays
        # at the entered state.
        state.last_state_completed = False

    return state


def run_resume_hooks(
    workflow: Workflow,
    registry: ProfileRegistry,
    replay: ReplayState,
    log_writer: Any,
) -> None:
    """Run profile-registered resume hooks before re-entering an
    interrupted state.

    Slice 1 has no registered resume hooks. The dispatch path runs
    with an empty hook set: it walks the interrupted state's writes,
    finds zero matching hooks, and emits no records. The ordering
    invariant ("hooks before state_enter on re-entry") is satisfied
    vacuously.

    A hook failure raises ResumeError; per the runner spec's open
    question 7, hook failure aborts resume.
    """
    if replay.current_state is None or replay.last_state_completed:
        return  # case 1 or terminal: nothing to do
    if replay.is_terminal:
        return
    if replay.current_state in _TERMINAL_TARGETS:
        return
    state = workflow.state(replay.current_state)
    matching = []
    for hook_name, hook in registry.resume_hooks.items():
        type_filter, callback = hook
        applicable = any(w.type in type_filter for w in state.writes)
        if applicable:
            matching.append((hook_name, callback))
    for hook_name, callback in matching:
        try:
            callback(state)
        except Exception as exc:
            raise ResumeError(f"resume hook {hook_name!r} failed: {exc}") from exc
        log_writer.write(
            "resume_hook",
            state_id=state.name,
            attempt=replay.attempts.get(state.name, 0),
            fields={"hook": hook_name},
        )
