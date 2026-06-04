"""The duplo authoring adapter over ``orchestra.run_role("plan_author")``.

This is the PLAN.md-authoring counterpart to ``duplo.design`` (which
wraps ``run_role("design", ...)`` for ``design_extractor``). It is a
DISTINCT call surface: ``run_iterative_design`` runs the shared
``design`` role and tolerates a non-converged result (it returns the
best-so-far body on ``CAPPED``), whereas this adapter authors a
PLAN.md body through the duplo-owned ``plan_author`` role and
``plan_author.orc`` validation-gated loop, and must FAIL CLOSED on a
non-converged result -- a body that never passes canonical validation
within ``max_rounds`` is never returned for PLAN.md.

Call shape
----------
``run_plan_author`` builds the two text inputs the ``plan_author``
workflow declares (``query`` and ``history``) and dispatches via
``orchestra.run_role("plan_author", query=..., history=...,
required_phase_id=..., registry_customizer=<register
validate_plan_body>)``:

- ``query`` is built from the same prompt/system material
  :func:`duplo.council.author_phase_plan` assembles, with the system
  directive folded into the query text exactly as
  :func:`duplo.council._build_state_text` does. The current phase's
  source/spec lives here.
- ``history`` is COMPACT prior-phase context only -- prior phase
  ids/titles, completed-phase summaries, files already created, and
  prior validation failures on retry. It is NOT a transcript and never
  carries the current phase's source/spec (that stays in ``query``).
- ``required_phase_id`` reaches the validation transform through the
  ``registry_customizer`` closure (see
  :func:`duplo.plan_validation_transform.register_validate_plan_body`),
  not as a model-visible input.
- ``criteria_block`` is the rendered configured-criteria list
  (:func:`duplo.plan_author_role.render_criteria_block`) injected into the
  judge prompt so the judge emits a ``criteria_compliance`` entry per
  configured criterion using those exact ids -- generated from the same
  ``PLAN_AUTHOR_CRITERIA`` the binding hands the executor, so the prompt's
  ids and the consistency check cannot drift.

Termination translation
------------------------
- ``CONVERGED``: return the converged ``proposal`` body (the
  ``final_artifact``, which for ``plan_author`` is the ``proposal``
  artifact -- see ``orchestra.api._select_final_artifact``).
- ``CAPPED``: ``run_role``'s termination derivation cannot tell a
  passing validation gate from a cap-exhausted fallthrough -- the
  ``validate`` transform emits the same ``complete`` outcome into
  ``done`` for both, so a run whose final draft DID validate is still
  labelled ``CAPPED`` (see NOTES [9.9] [T-000791]). So the adapter does
  not trust the label here: it re-runs the gate's own check
  (:func:`duplo.council.typed_plan_from_synthesizer_text` against
  ``required_phase_id``, exactly what ``validate_plan_body`` runs) on
  the final body. If it passes, that body IS the converged plan and is
  returned. If it never passed, the adapter fails closed -- raises
  :class:`PlanAuthorCappedError`, no body for PLAN.md; the best-so-far
  body rides the exception for audit/postmortem ONLY and must never be
  used as a plan. ``CAPPED`` is the disposition produced by the
  ``plan_author.orc`` validation-cap routing (T-000786) when a body
  never validates within ``max_rounds``.
- ``ERROR``: raise :class:`PlanAuthorRunError` carrying the orchestra
  ``ErrorRecord`` and the on-disk transcript path for postmortem.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import orchestra
from bob_tools.planfile import PlanSyntaxError, PlanValidationError
from orchestra import ErrorRecord, IterativeDesignResult
from orchestra.api import WorkflowApiError

from duplo.council import (
    _build_state_text,
    make_duplo_progress_callback,
    typed_plan_from_synthesizer_text,
)
from duplo.plan_author_role import render_criteria_block
from duplo.plan_validation_transform import register_validate_plan_body

_LOGGER = logging.getLogger("duplo.plan_author_adapter")

ROLE_NAME = "plan_author"


class PlanAuthorError(RuntimeError):
    """Base class for ``run_plan_author`` failures."""


class PlanAuthorCappedError(PlanAuthorError):
    """Raised when the ``plan_author`` loop terminates in ``CAPPED``.

    The proposed body never passed canonical validation within
    ``max_rounds``, so the adapter fails closed: no body is returned
    for PLAN.md. ``best_so_far`` carries the most recent (still-invalid)
    body for audit/postmortem only -- it must NEVER be used as a plan.
    """

    def __init__(
        self,
        *,
        run_id: str,
        transcript_path: Path,
        rounds_completed: int,
        best_so_far: str,
    ) -> None:
        super().__init__(
            f"plan_author run {run_id!r} terminated in CAPPED: the body never "
            f"passed canonical validation within max_rounds "
            f"(rounds_completed={rounds_completed}, transcript: {transcript_path}). "
            f"No plan was produced; best-so-far retained for audit only."
        )
        self.run_id = run_id
        self.transcript_path = transcript_path
        self.rounds_completed = rounds_completed
        self.best_so_far = best_so_far


class PlanAuthorRunError(PlanAuthorError):
    """Raised when the ``plan_author`` loop terminates in ``ERROR``.

    Carries the orchestra ``ErrorRecord`` that caused termination plus
    the on-disk transcript path so the caller can postmortem the run.
    """

    def __init__(
        self,
        *,
        error: ErrorRecord,
        transcript_path: Path,
        run_id: str,
    ) -> None:
        super().__init__(
            f"plan_author run {run_id!r} terminated in ERROR: "
            f"{error.message} (transcript: {transcript_path})"
        )
        self.error = error
        self.transcript_path = transcript_path
        self.run_id = run_id


@dataclass(frozen=True)
class PriorPhaseContext:
    """Compact prior-phase context folded into the ``history`` input.

    Deliberately holds only summarized prior-phase signal -- NOT full
    transcripts and NOT the current phase's source/spec (that belongs in
    ``query``). Every field is optional; an empty context yields an empty
    ``history`` string.

    - ``phases``: ``(phase_id, title)`` pairs for prior phases, in order.
    - ``completed_summaries``: one-line summaries of completed phases.
    - ``files_created``: paths already created by prior phases.
    - ``validation_failures``: prior canonical-validation failure
      messages, supplied on a retry so the author can avoid repeating
      them.
    """

    phases: Sequence[tuple[str, str]] = field(default_factory=tuple)
    completed_summaries: Sequence[str] = field(default_factory=tuple)
    files_created: Sequence[str] = field(default_factory=tuple)
    validation_failures: Sequence[str] = field(default_factory=tuple)


def build_history(context: PriorPhaseContext) -> str:
    """Render :class:`PriorPhaseContext` into the compact ``history`` text.

    Sections are emitted only when non-empty, so a context with no prior
    phases produces an empty string. The current phase's source/spec is
    never included -- that material stays in ``query``.
    """
    parts: list[str] = []
    if context.phases:
        parts.append("Prior phases:")
        parts.extend(f"- {phase_id}: {title}" for phase_id, title in context.phases)
        parts.append("")
    if context.completed_summaries:
        parts.append("Completed-phase summaries:")
        parts.extend(f"- {summary}" for summary in context.completed_summaries)
        parts.append("")
    if context.files_created:
        parts.append("Files already created:")
        parts.extend(f"- {path}" for path in context.files_created)
        parts.append("")
    if context.validation_failures:
        parts.append("Prior validation failures (do not repeat):")
        parts.extend(f"- {failure}" for failure in context.validation_failures)
        parts.append("")
    return "\n".join(parts).strip()


def run_plan_author(
    *,
    prompt: str,
    system: str,
    required_phase_id: str,
    history: PriorPhaseContext | None = None,
    project_dir: Path | None = None,
) -> str:
    """Author one phase's PLAN.md body via ``run_role("plan_author")``.

    Builds ``query`` from ``prompt``/``system`` (system directive folded
    in via :func:`duplo.council._build_state_text`) and ``history`` from
    the compact :class:`PriorPhaseContext`, then dispatches to
    ``orchestra.run_role`` with the duplo-owned ``validate_plan_body``
    transform registered through the ``registry_customizer`` hook bound to
    ``required_phase_id``.

    Returns the final ``proposal`` body when it passes the canonical
    validation gate (whether ``run_role`` labelled the run ``CONVERGED``
    or, because it cannot distinguish a passing gate from a cap
    fallthrough, ``CAPPED``). Raises :class:`PlanAuthorCappedError` when
    the final body never validates (fail closed -- no body) and
    :class:`PlanAuthorRunError` on ``ERROR``.
    """
    query = _build_state_text(prompt=prompt, system=system)
    history_text = build_history(history or PriorPhaseContext())

    try:
        result: IterativeDesignResult = orchestra.run_role(
            ROLE_NAME,
            query=query,
            history=history_text,
            required_phase_id=required_phase_id,
            # The judge prompt's criterion-id list is rendered from the same
            # PLAN_AUTHOR_CRITERIA the binding feeds to the executor, so the
            # ids the judge is asked to emit match the ids the consistency
            # check enforces (no missing_ids / extra_ids).
            criteria_block=render_criteria_block(),
            registry_customizer=register_validate_plan_body(required_phase_id),
            project_dir=project_dir,
            progress_callback=make_duplo_progress_callback(),
        )
    except WorkflowApiError as exc:
        # The role lookup or workflow load failed before any turn ran, so
        # no transcript exists. Surface it as a run error with empty
        # transcript path, mirroring duplo.design's config-missing path.
        raise PlanAuthorRunError(
            error=ErrorRecord(kind="config_missing", message=str(exc)),
            transcript_path=Path(""),
            run_id="",
        ) from exc

    if result.termination == "CONVERGED":
        # Annotate the local so mypy accepts the return: orchestra ships no
        # ``py.typed`` marker, so ``result.final_artifact`` is seen as ``Any``
        # (mirrors duplo.design's converged path).
        converged_body: str = result.final_artifact
        return converged_body

    if result.termination == "ERROR":
        error = result.error or ErrorRecord(
            kind="runner_failure",
            message="orchestra returned ERROR termination without an ErrorRecord",
        )
        raise PlanAuthorRunError(
            error=error,
            transcript_path=result.transcript_path,
            run_id=result.run_id,
        )

    # result.termination == "CAPPED". run_role's generic termination
    # derivation cannot distinguish a passing validation gate from a
    # cap-exhausted fallthrough: the plan_author ``validate`` transform
    # emits the same ``complete`` outcome into ``done`` for both, so a run
    # whose final draft DID pass canonical validation is still reported as
    # CAPPED (the two reach ``done`` via an identical (outcome, target)
    # pair in the run log -- see NOTES [9.9] [T-000791]). Decide fail-closed
    # from the ground truth instead of the label: re-run the gate's own
    # check -- ``typed_plan_from_synthesizer_text`` against
    # ``required_phase_id``, exactly what ``validate_plan_body`` runs -- on
    # the final body. A body that passes IS the converged plan and is
    # returned; a body that never passed is a true cap and is never
    # returned for PLAN.md (the best-so-far rides the exception for audit
    # only).
    capped_body: str = result.final_artifact
    try:
        typed_plan_from_synthesizer_text(capped_body, required_phase_id=required_phase_id)
    except (PlanSyntaxError, PlanValidationError):
        _LOGGER.warning(
            "plan_author did not converge to a valid body within max_rounds "
            "(run_id=%s, rounds_completed=%d, transcript=%s); failing closed",
            result.run_id,
            result.rounds_completed,
            result.transcript_path,
        )
        raise PlanAuthorCappedError(
            run_id=result.run_id,
            transcript_path=result.transcript_path,
            rounds_completed=result.rounds_completed,
            best_so_far=capped_body,
        ) from None
    return capped_body
