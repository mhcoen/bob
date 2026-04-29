"""Log replay and resume-hook dispatch.

After a crash, replay reconstructs:

  1. The current state.
  2. The counter table (attempts.<state>, retries.<state>).
  3. Whether the last state completed (case 1) or was interrupted
     (case 2).

The artifact store is rebuilt by the SQLite database itself: committed
versions are durable, so reconstruction means simply opening the same
database file. No replay of artifact_write records is needed; the log
records exist for audit, not for store reconstruction.

This is a slice-1 simplification, valid because the slice writes only
inline artifacts and ``commit_tentative`` is single-transaction. Slice
2's git-workspace handling will need explicit reconstruction; that is
in scope for the resume hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestra.errors import ResumeError
from orchestra.log import LogReader
from orchestra.registry import ProfileRegistry
from orchestra.spine import Workflow


@dataclass
class ReplayState:
    """The runner's state as reconstructed from the log."""

    last_run_id: str = ""
    next_seq: int = 0
    attempts: dict[str, int] = field(default_factory=dict)
    retries: dict[str, int] = field(default_factory=dict)
    current_state: str | None = None
    last_state_completed: bool = False
    """True if the last state visited has a state_exit (case 1)."""
    last_outcome: str | None = None
    last_target: str | None = None
    """The target named by the most recent ``transition`` record."""


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
        elif rec.event == "state_exit":
            state.last_state_completed = True
            outcome = rec.fields.get("outcome")
            state.last_outcome = str(outcome) if outcome is not None else None
        elif rec.event == "transition":
            target = rec.fields.get("target")
            state.last_target = str(target) if target is not None else None

    # Final routing decision: if the last record was a transition, the
    # current state is its target. Otherwise we re-enter what was being
    # executed when the log was truncated.
    if state.last_state_completed and state.last_target is not None:
        state.current_state = state.last_target
        state.last_state_completed = True
    else:
        # Either no state_exit yet (case 2) or no transition recorded.
        # Either way, current_state is what state_enter named.
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
        return  # case 1: nothing to do
    state = workflow.state(replay.current_state)
    matching = []
    for hook_name, hook in registry.resume_hooks.items():
        # The hook is a tuple of (artifact_type_filter, callback) by
        # convention. Slice 1 does not register any, so this loop is
        # empty in practice; it is here so slice 2 can register the
        # versioned-workspace hook without changing this code.
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
