"""Shared public types for the orchestra.api package.

WorkflowApiError + public result dataclasses (ArtifactView, WorkflowRunResult,
Turn, ErrorRecord, IterativeDesignResult). Plus the FINAL_PROMPT_INPUT sentinel.
Leaf module: imported by every sibling, imports no siblings itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.errors import OrchestraError
from orchestra.spine import Envelope

class WorkflowApiError(OrchestraError):
    """Raised when ``run_workflow`` cannot wire up or run the workflow."""

@dataclass(frozen=True)
class ArtifactView:
    """A read-only view of one committed artifact."""

    name: str
    type: str
    version_id: str
    value: Any

@dataclass
class WorkflowRunResult:
    """Outcome of a ``run_workflow`` invocation."""

    run_id: str
    terminal: str
    envelope: Envelope
    artifacts: dict[str, ArtifactView]
    log_path: Path
    summary: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Turn:
    """One role completion in a ``run_role`` transcript.

    Captures the salient envelope fields for a single state's
    successful or failed completion: which role ran, in which state,
    on which attempt, the duration, the status/outcome, and the
    artifact payload the model produced. The transcript is a
    chronologically-ordered list of these.
    """

    role: str
    state: str
    attempt: int
    started_at: str
    ended_at: str
    duration_ms: int
    status: str
    outcome: str
    output: str
    artifacts_written: list[dict[str, str]] = field(default_factory=list)

@dataclass(frozen=True)
class ErrorRecord:
    """A run_role-level error record.

    Populated on ``IterativeDesignResult.error`` iff
    ``termination == "ERROR"``. The ``state`` field names the state
    that produced the failing transition (or None when the failure
    happened before any state ran). ``detail`` is a free-form payload
    for the consumer's postmortem.
    """

    kind: str
    message: str
    state: str | None = None
    detail: dict[str, Any] | None = None

@dataclass
class IterativeDesignResult:
    """Outcome of an ``orchestra.run_role`` invocation against a
    design_loop-shaped workflow.

    Field semantics:

    - ``termination``: derived from the workflow's final transition.
      ``CONVERGED`` when the judge emitted ``done``; ``CAPPED`` when
      the round-cap guard routed the judge's ``iterate`` outcome to
      ``done``; ``ERROR`` for ``stuck``/``error``/``timeout`` or any
      pre-run failure.
    - ``rounds_completed``: count of successful judge-role state
      completions observed in the log. One judge call = one round.
    - ``final_artifact``: the most recent judge-produced artifact text
      (the workflow's primary text-typed write). Empty string when
      ``termination == "ERROR"`` and no judge artifact has been
      committed yet.
    - ``transcript``: in-memory chronological list of ``Turn`` records,
      one per state completion (judge or reviewer).
    - ``transcript_path``: absolute path to the JSONL on-disk
      transcript. Each line is a JSON-serialized Turn. Written
      incrementally by the api after run completion (so a crashed run
      still leaves the partial transcript on disk via the underlying
      log, which is also available at ``<transcript_path>.log``).
    - ``run_id``: the orchestra-assigned run id (also the directory
      name under ``~/.orchestra/runs/``).
    - ``error``: populated iff ``termination == "ERROR"``.
    """

    termination: Literal["CONVERGED", "CAPPED", "ERROR"]
    rounds_completed: int
    final_artifact: str
    transcript: list[Turn]
    transcript_path: Path
    run_id: str
    error: ErrorRecord | None = None

FINAL_PROMPT_INPUT: str = "final_prompt"
