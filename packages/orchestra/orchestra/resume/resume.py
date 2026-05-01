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
from pathlib import Path
from typing import Any

from orchestra.errors import ResumeError
from orchestra.log import LogReader
from orchestra.payloads import load_payload
from orchestra.registry import ProfileRegistry
from orchestra.spine import Envelope, ErrorRecord, Workflow
from orchestra.visibility import VisibilityStatus

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
    open_fan_out_attempt: int | None = None
    """Round-3 fix: parent's ``attempt`` from ``fan_out_start``. Lost
    by the previous code path because only ``rec.fields`` was
    captured into ``open_fan_out``. ``cmd_resume`` threads this
    through ``resume_fan_out`` so resumed ``fan_out_resume``,
    ``fan_out_end``, and the closing parent ``transition`` records
    carry the same attempt the live path would have written."""
    last_fan_out_target: str | None = None
    # invocation_id -> "pending" | "success" | "error" rebuilt from
    # state_enter and state_exit records; the resume helper feeds
    # this to ``VisibilityIndex.replace_from``.
    visibility_statuses: dict[str, VisibilityStatus] = field(default_factory=dict)
    # True when the latest record sequence is ``state_exit`` followed
    # by no ``transition`` (or any other terminator). The state's body
    # is fully complete but the routing decision was not durably
    # written before the crash. Resume must select and write the
    # transition from the reconstructed envelope WITHOUT re-running
    # the actor body. ``current_state`` remains the just-exited state
    # so the caller can locate the envelope; the caller is expected
    # to advance ``current_state`` after writing the transition.
    state_exit_without_transition: bool = False
    pending_fan_out_transition: dict[str, Any] | None = None
    """Round-3 fix: when a durable ``fan_out_end`` is the latest
    routing-relevant record and the parent's ``transition`` write
    never made it durable, this dict carries
    ``{parent_state, attempt, target}`` so ``cmd_resume`` can close
    the missing transition without re-running the fan-out group.
    Cleared when a matching parent transition is observed in the
    log."""


def replay_log(log_path: str | Path) -> ReplayState:
    """Read a log and produce a ReplayState.

    The log path's parent is the run directory, which is also where
    payload files live. Hydrating envelopes with their durable
    payloads at replay time means resume's transition selector sees
    the same ``state.payload.*`` view the live path saw, so guards
    that read payload data select the same branch.
    """
    log_path_p = Path(log_path)
    run_dir = log_path_p.parent
    reader = LogReader(str(log_path_p))
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
            # Round-2 fix: ``last_target`` is a per-state question
            # ("does the state we just entered have a durable
            # transition?"), so it is cleared on every state_enter.
            # Without this, a transition written by an earlier state
            # would make the most recent ``state_exit_without_transition``
            # detection misfire and the just-completed state would be
            # re-entered and re-executed by the executor's resume path.
            state.last_target = None
            # Slice A: track invocation status from the log so the
            # resume path can rebuild VisibilityIndex.
            inv_id = rec.fields.get("invocation_id")
            if isinstance(inv_id, str):
                state.visibility_statuses[inv_id] = "pending"
        elif rec.event == "fan_out_start":
            state.open_fan_out = dict(rec.fields)
            state.open_fan_out_attempt = rec.attempt
        elif rec.event == "fan_out_end":
            state.open_fan_out = None
            state.open_fan_out_attempt = None
            target = rec.fields.get("target")
            if isinstance(target, str):
                state.last_fan_out_target = target
                state.last_target = target
                state.last_state_completed = True
                state.current_state = target
                # Round-3 fix: track that the parent's transition is
                # still pending. Cleared when the matching parent
                # transition record is observed in the same replay.
                if rec.state_id is not None and rec.attempt is not None:
                    state.pending_fan_out_transition = {
                        "parent_state": rec.state_id,
                        "attempt": rec.attempt,
                        "target": target,
                    }
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
            # Round-2 fix: hydrate the payload from its durable file
            # so ``_select_transition_decl`` can evaluate guards that
            # read ``state.payload.*`` on resume the same way they
            # would on the live path. A missing payload_ref (cancelled
            # state, transform with no payload) keeps the envelope's
            # payload empty.
            payload_ref = rec.fields.get("payload_ref")
            payload: dict[str, Any] = {}
            if isinstance(payload_ref, str) and payload_ref:
                payload = load_payload(run_dir, payload_ref)
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
                payload=payload,
                error=err,
            )
            state.envelopes[rec.state_id] = envelope
        elif rec.event == "transition":
            target = rec.fields.get("target")
            state.last_target = str(target) if target is not None else None
            sc = rec.fields.get("step_count")
            if isinstance(sc, int):
                state.step_count = sc
            # Round-3 fix: clear the pending fan-out transition when
            # this transition record closes the same parent that
            # ``fan_out_end`` opened. A transition for a different
            # state (or attempt) does not close it.
            pft = state.pending_fan_out_transition
            if (
                pft is not None
                and rec.state_id == pft.get("parent_state")
                and rec.attempt == pft.get("attempt")
            ):
                state.pending_fan_out_transition = None

    # Final routing decision.
    if state.last_state_completed and state.last_target is not None:
        state.current_state = state.last_target
        if state.current_state in _TERMINAL_TARGETS:
            state.is_terminal = True
    elif state.last_state_completed and state.last_target is None:
        if state.open_fan_out is not None:
            # An open fan-out group dominates the routing decision.
            # ``cmd_resume`` dispatches to ``resume_fan_out`` which
            # drains the children, writes ``fan_out_end``, and writes
            # the parent's missing transition. Setting
            # ``state_exit_without_transition`` here would cause the
            # caller to also try to re-run the fan-out via
            # ``resume_pending_transition``, which is wrong.
            pass
        else:
            # A ``state_exit`` was logged but no ``transition``
            # followed (crash in the small window between the two).
            # The state is complete: its actor body must NOT run
            # again. Resume's job is to re-select the transition
            # from the reconstructed envelope and write the missing
            # record, then continue from the chosen target.
            # ``last_state_completed`` stays True (the state IS
            # done), and the special-case flag tells the caller
            # this is the no-transition-yet recovery path.
            # ``current_state`` stays at the just-exited state so
            # the caller can locate the envelope to feed the
            # transition selector.
            state.state_exit_without_transition = True
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
