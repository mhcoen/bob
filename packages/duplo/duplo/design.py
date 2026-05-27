"""Iterative design entry point.

Wraps ``orchestra.run_role("design", ...)`` and translates its
``IterativeDesignResult`` into duplo's call surface: the final
judge-produced artifact text on ``CONVERGED``, the most recent
artifact (plus a warning) on ``CAPPED``, and ``IterativeDesignError``
on ``ERROR``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import orchestra
from orchestra import ErrorRecord, IterativeDesignResult

_LOGGER = logging.getLogger("duplo.design")


class IterativeDesignError(RuntimeError):
    """Raised when ``run_iterative_design`` terminates in ERROR.

    Carries the orchestra ``ErrorRecord`` that caused termination plus
    the on-disk transcript path so the caller can postmortem the run.
    """

    def __init__(
        self,
        error: ErrorRecord,
        transcript_path: Path,
        run_id: str,
    ) -> None:
        super().__init__(
            f"iterative design run {run_id!r} terminated in ERROR: "
            f"{error.message} (transcript: {transcript_path})"
        )
        self.error = error
        self.transcript_path = transcript_path
        self.run_id = run_id


def run_iterative_design(seed_input: Any) -> str:
    """Run the design loop and return the final artifact text.

    Dispatches to ``orchestra.run_role("design", seed_input=...)`` and
    translates the terminal disposition:

    - ``CONVERGED``: return ``final_artifact``.
    - ``CAPPED``: return ``final_artifact``; log a warning on the
      ``duplo.design`` logger that the design did not converge within
      ``max_rounds``.
    - ``ERROR``: raise :class:`IterativeDesignError` wrapping the
      orchestra ``ErrorRecord`` with the transcript path included for
      postmortem.
    """
    result: IterativeDesignResult = orchestra.run_role("design", seed_input=seed_input)
    final_artifact: str = result.final_artifact
    if result.termination == "CONVERGED":
        return final_artifact
    if result.termination == "CAPPED":
        _LOGGER.warning(
            "design did not converge within max_rounds "
            "(run_id=%s, rounds_completed=%d, transcript=%s)",
            result.run_id,
            result.rounds_completed,
            result.transcript_path,
        )
        return final_artifact
    error = result.error or ErrorRecord(
        kind="runner_failure",
        message="orchestra returned ERROR termination without an ErrorRecord",
    )
    raise IterativeDesignError(
        error=error,
        transcript_path=result.transcript_path,
        run_id=result.run_id,
    )
