"""Termination derivation and transcript writing for run_role."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from orchestra.api._common import (
    ArtifactView,
    ErrorRecord,
    Turn,
)
from orchestra.log import LogReader
from orchestra.spine import Envelope, StateDecl, Workflow


def _derive_termination(
    log_path: Path,
) -> tuple[Literal["CONVERGED", "CAPPED", "ERROR"], ErrorRecord | None]:
    """Classify a finished run's terminal disposition from the log.

    Walks the JSONL log to find the last ``transition`` record. The
    executor only recognizes ``done``/``stop`` as terminal targets, so
    CONVERGED/CAPPED/ERROR has to be inferred from the *outcome* of
    the transition that landed on a terminal:

    - target == "done" AND outcome == "done"
      → CONVERGED (judge emitted the ``done`` action)
    - target == "done" AND outcome != "done"
      → CAPPED (cap-hit transition routed ``iterate``/``revise`` to
      ``done``)
    - target == "stop" OR outcome in {stuck, error, timeout, cancelled}
      → ERROR (with an ErrorRecord built from the last state_exit's
      error field when available)
    - no transition record found → ERROR (workflow never produced a
      transition; e.g. crashed during setup)
    """
    records = LogReader(log_path).read_all()
    last_transition = None
    last_state_exit_error: dict[str, Any] | None = None
    last_state_exit_state: str | None = None
    for r in records:
        if r.event == "transition":
            last_transition = r
        elif r.event == "state_exit":
            err = r.fields.get("error")
            if isinstance(err, dict):
                last_state_exit_error = err
                last_state_exit_state = r.state_id
    if last_transition is None:
        return "ERROR", ErrorRecord(
            kind="runner_failure",
            message="workflow produced no transition records",
        )
    outcome = str(last_transition.fields.get("outcome") or "")
    target = str(last_transition.fields.get("target") or "")
    if target == "done" and outcome == "done":
        return "CONVERGED", None
    if target == "done":
        return "CAPPED", None
    # target == "stop" or an unexpected terminal: classify as ERROR
    # and attach the last error envelope we observed (if any) so the
    # consumer can postmortem.
    err_kind = "runner_failure"
    err_message = f"workflow terminated via outcome={outcome!r} target={target!r}"
    err_detail: dict[str, Any] | None = None
    if last_state_exit_error is not None:
        err_kind = str(last_state_exit_error.get("kind") or err_kind)
        err_message = str(last_state_exit_error.get("message") or err_message)
        raw_detail = last_state_exit_error.get("detail")
        if isinstance(raw_detail, dict):
            err_detail = dict(raw_detail)
    return "ERROR", ErrorRecord(
        kind=err_kind,
        message=err_message,
        state=last_state_exit_state,
        detail=err_detail,
    )


def _build_transcript(
    log_path: Path,
    run_dir: Path,
    workflow: Workflow,
) -> list[Turn]:
    """Reconstruct an ordered Turn list from the run log.

    Each successful or failed ``state_exit`` record becomes one Turn.
    The ``output`` field is populated by loading the referenced
    payload file from ``<run_dir>/payloads/`` when available; if the
    payload is missing or unreadable the field defaults to empty
    string so the transcript stays well-formed.
    """
    from orchestra.payloads import load_payload

    records = LogReader(log_path).read_all()
    role_by_state = {s.name: (s.role or "") for s in workflow.states}
    turns: list[Turn] = []
    for r in records:
        if r.event != "state_exit":
            continue
        state_name = r.state_id or ""
        fields = r.fields
        payload_ref = fields.get("payload_ref")
        output = ""
        if isinstance(payload_ref, str) and payload_ref:
            try:
                payload = load_payload(run_dir, payload_ref)
            except Exception:
                payload = {}
            raw_output = payload.get("output")
            if isinstance(raw_output, str):
                output = raw_output
        artifacts_written_raw = fields.get("artifacts_written") or []
        artifacts_written: list[dict[str, str]] = []
        if isinstance(artifacts_written_raw, list):
            for entry in artifacts_written_raw:
                if isinstance(entry, dict):
                    artifacts_written.append({str(k): str(v) for k, v in entry.items()})
        turns.append(
            Turn(
                role=role_by_state.get(state_name, ""),
                state=state_name,
                attempt=int(r.attempt or 0),
                started_at=str(r.ts or ""),
                ended_at=str(r.ts or ""),
                duration_ms=int(fields.get("duration_ms") or 0),
                status=str(fields.get("status") or ""),
                outcome=str(fields.get("outcome") or ""),
                output=output,
                artifacts_written=artifacts_written,
            )
        )
    return turns


class _IncrementalTranscriptWriter:
    """Appends one ``Turn`` JSON line to ``transcript.jsonl`` per
    state_exit, fsynced before returning to the executor.

    The executor calls this from inside the same thread that wrote the
    state_exit log record (the linear thread for sequential states,
    the worker thread for fan-out children); a lock serializes the
    append so concurrent fan-out completions cannot interleave bytes
    inside a record. The file is truncated at construction time so a
    resumed run does not concatenate to a prior run's transcript.

    A crash mid-run leaves the file with every role completion that
    landed before the crash, fsynced to disk; the post-run
    ``_build_transcript`` view of the log remains the canonical
    in-memory representation, but consumers reading ``transcript.jsonl``
    after a crash see the same partial-but-coherent record sequence
    that ``log.jsonl`` does.
    """

    def __init__(self, path: Path, run_dir: Path, workflow: Workflow) -> None:
        self._path = path
        self._run_dir = run_dir
        self._role_by_state = {s.name: (s.role or "") for s in workflow.states}
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate any pre-existing file so the run's transcript starts
        # empty. The writer fsyncs each appended line individually.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")
            fh.flush()
            os.fsync(fh.fileno())

    def __call__(
        self,
        state: StateDecl,
        envelope: Envelope,
        payload_ref: str | None,
    ) -> None:
        from orchestra.payloads import load_payload

        output = ""
        if isinstance(payload_ref, str) and payload_ref:
            try:
                payload = load_payload(self._run_dir, payload_ref)
            except Exception:
                payload = {}
            raw_output = payload.get("output")
            if isinstance(raw_output, str):
                output = raw_output
        artifacts_written: list[dict[str, str]] = []
        for entry in envelope.artifacts_written:
            if isinstance(entry, dict):
                artifacts_written.append({str(k): str(v) for k, v in entry.items()})
        turn = Turn(
            role=self._role_by_state.get(state.name, state.role or ""),
            state=state.name,
            attempt=envelope.attempt,
            started_at=envelope.started_at,
            ended_at=envelope.ended_at,
            duration_ms=envelope.duration_ms,
            status=str(envelope.status),
            outcome=str(envelope.outcome),
            output=output,
            artifacts_written=artifacts_written,
        )
        line = json.dumps(asdict(turn), sort_keys=True, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())


def _count_judge_rounds(transcript: list[Turn], workflow: Workflow) -> int:
    """Count successful judge-role completions in the transcript.

    The design_loop pattern issues one judge call per round (produce
    on the first turn, then revise or done on subsequent turns), so a
    judge-state success count is the round count. The judge role's
    name is whichever role is bound to the workflow's first
    judge-style state; for design_loop that is the state named
    ``judge``.

    Falls back to counting all successful state completions when the
    workflow has no state named ``judge`` so non-design_loop callers
    still get a meaningful number.
    """
    has_judge_state = any(s.name == "judge" for s in workflow.states)
    if not has_judge_state:
        return sum(1 for t in transcript if t.status == "ok")
    return sum(1 for t in transcript if t.state == "judge" and t.status == "ok")


def _select_final_artifact(
    workflow: Workflow,
    artifacts: dict[str, ArtifactView],
    transcript: list[Turn],
) -> str:
    """Pick the most recently judge-produced artifact text.

    Design_loop's primary text output lives in the ``proposal``
    artifact; prefer it when present. Otherwise walk the transcript
    in reverse for the last successful judge-state turn's payload
    output, which the workflow restructure (T-000009/10) will replace
    with a schema-extracted artifact. The fallback path keeps
    run_role usable against the current ``design_loop.orc`` shape
    before the restructure lands.
    """
    proposal = artifacts.get("proposal")
    if proposal is not None and isinstance(proposal.value, str) and proposal.value:
        return proposal.value
    for turn in reversed(transcript):
        if turn.state == "judge" and turn.status == "ok" and turn.output:
            return turn.output
    return ""
