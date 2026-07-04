"""Unit tests for log replay (the resume module).

These tests exercise the case-1 / case-2 distinction directly on
synthetic logs, without running an executor. The end-to-end resume
test in ``test_e2e.py`` exercises the same logic in context.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.errors import ResumeError
from orchestra.resume import replay_log


def _write_log(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def _common(seq: int, event: str, **extras) -> dict:
    base = {
        "ts": "2026-01-01T00:00:00.000Z",
        "run_id": "test-run",
        "seq": seq,
        "event": event,
        "state_id": None,
        "attempt": None,
    }
    base.update(extras)
    return base


def test_replay_empty_log_returns_zeroed_state(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    log.write_text("")
    state = replay_log(str(log))
    assert state.last_run_id == ""
    assert state.next_seq == 0
    assert state.attempts == {}
    assert state.retries == {}
    assert state.current_state is None


def test_replay_case_1_after_transition(tmp_path: Path) -> None:
    """A complete state plus a transition record => current_state is
    the transition's target, not the just-completed state."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(3, "actor_invoke_start", state_id="A", attempt=1),
            _common(4, "actor_invoke_end", state_id="A", attempt=1),
            _common(
                5,
                "state_exit",
                state_id="A",
                attempt=1,
                status="ok",
                outcome="complete",
            ),
            _common(6, "transition", state_id="A", attempt=1, outcome="complete", target="B"),
        ],
    )
    state = replay_log(str(log))
    assert state.current_state == "B"
    assert state.last_state_completed is True
    assert state.last_outcome == "complete"
    assert state.last_target == "B"
    assert state.attempts == {"A": 1}
    assert state.next_seq == 7
    assert state.last_run_id == "test-run"


def test_replay_state_exit_without_transition_preserves_completion(
    tmp_path: Path,
) -> None:
    """A state_exit landed but no transition followed (crash between
    the two log writes). Replay must report the state as completed
    (its actor body finished durably) AND set
    ``state_exit_without_transition`` so the resume caller knows to
    re-select and write the transition WITHOUT re-entering the state.

    Regression test for the Slice A bug where this case was treated
    as case 2 and the state's actor body would be re-executed.
    """
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(3, "actor_invoke_start", state_id="A", attempt=1),
            _common(4, "actor_invoke_end", state_id="A", attempt=1),
            _common(
                5,
                "state_exit",
                state_id="A",
                attempt=1,
                status="ok",
                outcome="complete",
            ),
            # Crash here: no transition record was written.
        ],
    )
    state = replay_log(str(log))
    assert state.current_state == "A"
    assert state.last_state_completed is True
    assert state.state_exit_without_transition is True
    assert "A" in state.envelopes
    assert state.envelopes["A"].outcome == "complete"


def test_replay_case_2_state_enter_only(tmp_path: Path) -> None:
    """A state_enter with no following state_exit => the state was
    interrupted; current_state is the entered state."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
        ],
    )
    state = replay_log(str(log))
    assert state.current_state == "A"
    assert state.last_state_completed is False
    assert state.attempts == {"A": 1}
    assert state.next_seq == 3


def test_replay_case_2_after_transition_to_unstarted_state(tmp_path: Path) -> None:
    """If transition records target B but no state_enter for B, we
    still treat this as case 1: current_state is B, last_state_completed
    is True, and the executor enters B fresh on resume."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(3, "actor_invoke_start", state_id="A", attempt=1),
            _common(4, "actor_invoke_end", state_id="A", attempt=1),
            _common(
                5,
                "state_exit",
                state_id="A",
                attempt=1,
                status="ok",
                outcome="complete",
            ),
            _common(6, "transition", state_id="A", attempt=1, outcome="complete", target="B"),
        ],
    )
    state = replay_log(str(log))
    assert state.current_state == "B"
    assert state.last_state_completed is True


def test_replay_truncated_last_line_dropped(tmp_path: Path) -> None:
    """A partial last line is silently dropped (per orchestra-runner.md
    open question 6)."""
    log = tmp_path / "log.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_common(0, "run_start"), sort_keys=True) + "\n")
        fh.write(
            json.dumps(
                _common(
                    1,
                    "state_enter",
                    state_id="A",
                    attempt=1,
                    attempts={"A": 1},
                    retries={"A": 0},
                ),
                sort_keys=True,
            )
            + "\n"
        )
        # Truncated record (no closing brace, no newline)
        fh.write('{"ts": "2026-01-01", "run_id": "test-run", "seq": 2, "ev')
    state = replay_log(str(log))
    assert state.current_state == "A"
    assert state.next_seq == 2  # last good record was seq 1


def test_replay_increments_attempts_across_repeated_entries(tmp_path: Path) -> None:
    """Two state_enter records for the same state (case 2 followed by
    a fresh attempt after resume) result in attempts.<state> tracking
    the highest attempt seen."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(
                3,
                "state_enter",
                state_id="A",
                attempt=2,
                attempts={"A": 2},
                retries={"A": 0},
            ),
            _common(4, "actor_prepare", state_id="A", attempt=2),
        ],
    )
    state = replay_log(str(log))
    assert state.attempts == {"A": 2}
    assert state.current_state == "A"
    assert state.last_state_completed is False


# --------------------------------------------------------------------
# Malformed-record guards (T-000007): a state_enter/state_exit with a
# null state_id or attempt must raise, not silently corrupt the map.
# The previous bare ``assert`` vanished under ``python -O``, letting a
# ``None`` state_id key the attempts dict.
# --------------------------------------------------------------------


def test_replay_state_enter_with_null_state_id_raises(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            # state_id/attempt default to None in _common: malformed.
            _common(1, "state_enter", attempt=1),
        ],
    )
    with pytest.raises(ResumeError, match="malformed state_enter"):
        replay_log(str(log))


def test_replay_state_enter_with_null_attempt_raises(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(1, "state_enter", state_id="A"),
        ],
    )
    with pytest.raises(ResumeError, match="malformed state_enter"):
        replay_log(str(log))


def test_replay_state_exit_with_null_state_id_raises(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "state_exit", attempt=1, status="ok", outcome="complete"),
        ],
    )
    with pytest.raises(ResumeError, match="malformed state_exit"):
        replay_log(str(log))


# --------------------------------------------------------------------
# Pass-2 fix #1: committed-without-exit detection
# --------------------------------------------------------------------


def test_replay_records_committed_without_exit(tmp_path: Path) -> None:
    """state_enter + artifact_write + crash before state_exit leaves
    a committed artifact version in the store with no completion
    record. Resume must surface this so cmd_resume can refuse the
    re-entry for an agent state."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(3, "actor_invoke_start", state_id="A", attempt=1),
            _common(4, "actor_invoke_end", state_id="A", attempt=1, payload_ref=None),
            _common(
                5,
                "artifact_write",
                state_id="A",
                attempt=1,
                artifact="reply",
                version_id="v1",
                invocation_id="test-run::A::1",
            ),
        ],
    )
    state = replay_log(str(log))
    assert ("A", 1) in state.committed_without_exit
    assert state.current_state == "A"
    assert state.last_state_completed is False


def test_replay_clears_committed_without_exit_on_state_exit(
    tmp_path: Path,
) -> None:
    """A matching state_exit closes the open artifact_write window.
    The pair must NOT remain in committed_without_exit."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(
                2,
                "artifact_write",
                state_id="A",
                attempt=1,
                artifact="reply",
                version_id="v1",
                invocation_id="test-run::A::1",
            ),
            _common(
                3,
                "state_exit",
                state_id="A",
                attempt=1,
                status="ok",
                outcome="complete",
            ),
        ],
    )
    state = replay_log(str(log))
    assert state.committed_without_exit == set()


# --------------------------------------------------------------------
# Pass-2 fix #2: last-transition reconstruction for retry counter
# --------------------------------------------------------------------


def test_replay_records_last_transition_state_and_outcome(
    tmp_path: Path,
) -> None:
    """A durable ``transition`` record's (state_id, outcome) is what
    the live executor stores in _last_state/_last_outcome via
    _close_pending_transition. Replay must recover both so a resume
    after `on error retry => same_state` does not reset retries."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(
                1,
                "state_enter",
                state_id="A",
                attempt=1,
                attempts={"A": 1},
                retries={"A": 0},
            ),
            _common(2, "actor_prepare", state_id="A", attempt=1),
            _common(3, "actor_invoke_start", state_id="A", attempt=1),
            _common(4, "actor_invoke_end", state_id="A", attempt=1),
            _common(
                5,
                "state_exit",
                state_id="A",
                attempt=1,
                status="error",
                outcome="error",
            ),
            _common(
                6,
                "transition",
                state_id="A",
                attempt=1,
                outcome="error",
                target="A",
                step_count=1,
            ),
        ],
    )
    state = replay_log(str(log))
    assert state.last_transition_state == "A"
    assert state.last_transition_outcome == "error"


def test_replay_last_transition_takes_latest_record(tmp_path: Path) -> None:
    """When multiple transition records are durable the latest one is
    what the live executor's _last_state/_last_outcome would hold."""
    log = tmp_path / "log.jsonl"
    _write_log(
        log,
        [
            _common(0, "run_start"),
            _common(1, "state_enter", state_id="A", attempt=1),
            _common(2, "state_exit", state_id="A", attempt=1, status="ok", outcome="complete"),
            _common(
                3,
                "transition",
                state_id="A",
                attempt=1,
                outcome="complete",
                target="B",
                step_count=1,
            ),
            _common(4, "state_enter", state_id="B", attempt=1),
            _common(5, "state_exit", state_id="B", attempt=1, status="error", outcome="timeout"),
            _common(
                6,
                "transition",
                state_id="B",
                attempt=1,
                outcome="timeout",
                target="B",
                step_count=2,
            ),
        ],
    )
    state = replay_log(str(log))
    assert state.last_transition_state == "B"
    assert state.last_transition_outcome == "timeout"
