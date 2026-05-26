"""Plan Ledger Slice C: Duplo re-author mode.

Slices A and B (in ``bob_tools.ledger``) produce the event ledger
and detect threshold crossings. Slice C is the response: when a
threshold crossing warrants re-authoring, Duplo consumes the ledger
plus current PLAN.md and produces an updated PLAN.md preserving
lineage from old phases to new.

Entry point: ``reauthor_plan(plan_path, ledger_dir,
crossing_event_id, ...)``. Side effect: appends one or more
lifecycle events (``phase_superseded`` / ``phase_split`` /
``phase_merged`` / ``phase_abandoned``) FIRST, then a single
``plan_reauthored`` event referencing them. Replay determinism: a
projector reading the log in event-id order encounters lifecycle
changes with their full payload before the meta-event that ties
them together (option (a) per the design doc and the Codex round 2
review).

This module's public surface is intentionally narrow: the
``reauthor`` CLI subcommand and ``reauthor_plan`` Python API.
Helpers for ledger-slice and design-context construction are
private; tests exercise them through the public entry point.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bob_tools.planfile import (
    PlanArtifactRejected,
    PlanValidationError,
    assert_mcloop_canonical,
    parse_plan,
    sanitize_plan_artifact,
)
from bob_tools.planfile import save as planfile_save
from duplo.reauthor_assemble import (
    assemble_reauthored_plan,
    normalize_lineage_for_preservation,
    rebuild_phase_constructed,
)
from duplo.reauthor_phase_ids import (
    LineageDiff,
    LineageValidationError,
    ParsedHeader,
    compute_lineage_diff,
    parse_plan_phases,
    validate_lineage,
)
from duplo.schema_classification import (
    SchemaClassification,
    SchemaFailure,
    SchemaFailureKind,
    classify_schema_failure,
    read_schema_validation_failures,
)


class ReauthorError(RuntimeError):
    """Raised when re-author cannot proceed for an input or wiring reason.

    Distinguishes from ``LineageValidationError`` (synthesizer-output
    integrity) and ``CouncilError`` (orchestra-side failure).
    """


class PlanArtifactError(ReauthorError):
    """Raised when the synthesizer's plan artifact violates the
    plan-artifact contract.

    Subclass of :class:`ReauthorError` so callers that catch
    ``ReauthorError`` continue to do the right thing, but distinct
    enough that mcloop's HardStop layer can surface a specific
    pause reason (``plan_artifact_invalid``) instead of folding the
    failure into the generic ``reauthor_failed`` bucket.

    Failure modes covered:

      - The plan body contains a fenced ``json`` block in a shape
        that is not the documented trailing-fenced-verdict shape
        (mid-body, multiple blocks, etc.). See
        :func:`bob_tools.planfile.sanitize_plan_artifact`.
      - The trailing fenced verdict extracted from the plan
        artifact does not equal orchestra's judge_verdict artifact
        value (parser disagreement on the model's output).
    """


class CommitAttributionError(ReauthorError):
    """Raised when the synthesizer's verdict.commit_attributions
    array violates the per-attribution contract.

    Subclass of :class:`ReauthorError`; mcloop's HardStop layer
    surfaces this as ``commit_attribution_invalid`` (distinct from
    the generic ``reauthor_failed`` and from
    ``plan_artifact_invalid``).

    Failure modes covered (errors accumulate, one raise lists all):

      - ``commit_sha`` does not prefix-match any unattributable
        commit referenced in the triggering crossing's slice. The
        synthesizer is naming a commit the runtime doesn't expect
        to attribute.
      - ``phase_id`` is not in the union of the current prior plan
        ids and the new ids introduced in this reauthor's lineage.
        The synthesizer is naming a phase that doesn't exist.
      - ``rationale`` is empty after strip. The synthesizer must
        explain the match.
    """


class SchemaValidationError(ReauthorError):
    """Raised when orchestra rejected the synthesizer's verdict
    against the artifact schema (or when the model emitted output
    that did not parse as JSON).

    Subclass of :class:`ReauthorError`; mcloop's HardStop layer
    surfaces this as ``schema_validation_invalid`` (distinct from
    the generic ``reauthor_failed``, from ``plan_artifact_invalid``,
    and from ``commit_attribution_invalid``).

    The error carries a :class:`SchemaClassification` (``.classification``)
    naming the primary failure kind and the full set of kinds present
    across the verdict's validation errors. The retry loop in
    :func:`reauthor_plan` shares its budget with
    :class:`LineageValidationError`; either failure on attempt 1 burns
    the one retry the run is allotted. Kind-specific feedback is
    threaded into the workflow's ``previous_attempt_error`` external
    input so the synthesizer sees a structural-named correction
    before re-running.

    Failure modes covered (classified by
    :class:`SchemaFailureKind`):

      - ``additional_properties``: the verdict carried fields the
        schema does not declare.
      - ``missing_required``: a required field is absent.
      - ``enum_mismatch``: a string value is outside its enum.
      - ``malformed_array``: an array field is not an array.
      - ``json_parse``: the model output did not parse as JSON.
      - ``other``: anything else (recorded but not specially named).
    """

    def __init__(
        self,
        message: str,
        *,
        classification: SchemaClassification,
        failures: tuple[SchemaFailure, ...],
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.failures = failures


_LEDGER_DIR_DEFAULT_NAME = ".duplo/ledger"


@dataclass
class ReauthorResult:
    """Summary of one re-author run.

    Returned by ``reauthor_plan`` for callers (CLI, tests) that want
    structured output; the entry-point also returns the path of the
    written plan.
    """

    new_plan_path: Path
    new_plan_text: str
    lineage_diff: LineageDiff
    lifecycle_event_ids: list[str]
    plan_reauthored_event_id: str
    council_run_id: str | None


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


def reauthor_plan(
    plan_path: Path,
    ledger_dir: Path,
    crossing_event_id: str,
    *,
    out_path: Path | None = None,
    council_config_path: Path | None = None,
    run_id: str | None = None,
    project_dir: Path | None = None,
    target_phase_id: str | None = None,
) -> ReauthorResult:
    """Drive a re-authoring of ``plan_path`` against the Plan Ledger.

    Parameters
    ----------
    plan_path
        Path to the existing PLAN.md.
    ledger_dir
        Path to the ledger directory (PLAN.events.jsonl plus
        ``.writers/`` per-writer seq files). Defaults to
        ``.duplo/ledger`` relative to ``project_dir`` when invoked
        from the CLI.
    crossing_event_id
        Event id of the ``threshold_crossed`` event that triggered
        this re-author. Required: Slice C never re-authors
        implicitly.
    out_path
        Where to write the new plan. Defaults to overwriting
        ``plan_path``.
    council_config_path
        Optional explicit ``.orchestra/config.json`` for the council
        invocation. Forwarded to ``duplo.council``.
    run_id
        Stable run identifier for the lifecycle and ``plan_reauthored``
        events. Defaults to a fresh ``reauthor-<short-uuid>`` string.
    project_dir
        The project the plan applies to. Defaults to the parent of
        ``plan_path``.
    target_phase_id
        Optional phase id to scope the re-author to. When supplied,
        the council brief tells the synthesizer that only this
        phase (and its lineage successors) are in scope; unchanged
        priors are preserved by the runtime via the existing
        preserve-by-default assembly. When None, the synthesizer
        re-authors plan-wide. Used by mcloop's ``auto_reauthor`` to
        honor the ``recommended_action == reauthor_phase`` scope on
        crossings whose triggering event identifies a specific
        phase, instead of escalating every phase-scoped crossing
        into a plan-wide synthesis.

    Returns
    -------
    ReauthorResult
        Includes the path written, the lineage diff, the emitted
        event ids, and the council run id (if any).

    Raises
    ------
    ReauthorError
        On input validation failures (missing crossing event,
        missing PLAN.md, etc.).
    LineageValidationError
        When the synthesizer's plan body and lineage sidecar
        violate Slice C's lineage invariants (missing or
        contradictory action declarations, unaccounted prior ids,
        unknown predecessors, etc.).
    """
    # Lazy imports for the same reason ``duplo.council`` does it: keep
    # the legacy duplo CLI usable without orchestra installed.
    from bob_tools.ledger import (
        EventType,
        GitSnapshot,
        Storage,
        allocate_writer_id,
        project,
    )
    from duplo import council

    plan_path = Path(plan_path).resolve()
    ledger_dir = Path(ledger_dir).resolve()
    out_path = Path(out_path).resolve() if out_path is not None else plan_path
    project_dir = (project_dir or plan_path.parent).resolve()

    if not plan_path.exists():
        raise ReauthorError(f"plan not found: {plan_path}")
    if not crossing_event_id:
        raise ReauthorError("crossing_event_id is required")

    plan_text_old = plan_path.read_text(encoding="utf-8")
    old_phases = parse_plan_phases(plan_text_old)
    # Parse the prior plan structurally via bob_tools.planfile. The
    # assembly path walks this Plan's phases to preserve unchanged
    # phases verbatim and substitute changed/new phases in place via
    # replace_phase_validated. A structurally corrupt prior raises at
    # parse rather than getting amplified across reauthor passes.
    try:
        prior_plan = parse_plan(plan_text_old, source_path=plan_path)
    except Exception as exc:
        raise ReauthorError(
            f"prior PLAN.md at {plan_path} cannot be parsed as a "
            f"canonical plan document: {exc}. The reauthor path "
            "requires a structurally valid prior plan; manual repair "
            "or rollback to a known-good commit is needed before "
            "reauthor can proceed."
        ) from exc

    storage_writer_id = allocate_writer_id(prefix="duplo-reauthor")
    storage = Storage(ledger_dir, writer_id=storage_writer_id)
    events = storage.read_all()
    state = project(events)

    crossing = _find_crossing(events, crossing_event_id)

    since_event_id = _previous_plan_reauthored_event_id(events)
    ledger_slice_md = _build_ledger_slice(events, state, crossing, since_event_id, old_phases)
    design_context_md = _build_design_context(events, state, plan_text_old, old_phases)

    # Build the question string. Slice C invokes the same
    # council_four workflow Duplo's canonical mode uses; the question
    # tells the synthesizer this is a re-author rather than a fresh
    # authoring so it consults the lineage-preservation discipline in
    # the council_synthesizer template.
    crossing_summary = _crossing_summary(crossing)
    next_available_phase_id = _next_available_phase_id_from_priors(old_phases)
    if target_phase_id is not None:
        if target_phase_id not in {p.id for p in old_phases}:
            raise ReauthorError(
                f"target_phase_id {target_phase_id!r} is not a current "
                f"prior plan id; current prior plan ids: "
                f"{[p.id for p in old_phases]}"
            )
        scope_clause = (
            f"SCOPE: this re-author is phase-scoped to {target_phase_id!r}. "
            "Author changes for this phase only (and any phase you derive "
            "from it via supersede/split/merge or any genuinely new phase "
            "you must introduce alongside it). All other prior phases "
            "MUST be left as preserve; do not touch their content. "
            "Duplo's preserve-by-default assembly will carry the "
            "unchanged priors forward verbatim. "
        )
    else:
        scope_clause = ""
    question = (
        "Re-author the plan in light of the following triggering "
        f"threshold crossing: {crossing_summary}. " + scope_clause + "Preserve phase ids "
        "from the prior plan where the phase remains valid; introduce "
        f"new ids for derived phases STARTING AT {next_available_phase_id} "
        "(the runtime computes this; the prior plan's ids are listed "
        "in the state block — do NOT pick an id from that list). "
        "Declare lineage in the verdict JSON's 'lineage' object (NOT "
        "in the markdown). Each plan header gets one phases[] entry "
        "with an action from {preserve, supersede, split, merge, new}; "
        "supersede/split/merge entries name their predecessors in "
        "'from' (the predecessors MUST be ids from the prior plan's "
        "list — do NOT invent ancestor ids). Drop a prior phase "
        "entirely by listing it in lineage.abandoned. See the "
        "council_synthesizer template's phase-id and lineage discipline "
        "section for the schema and per-action constraints."
    )

    state_blob = _build_state_blob(plan_text_old, old_phases, state)

    if council_config_path is not None:
        council.set_config_path(str(council_config_path))

    council_run_id = run_id or _new_run_id()
    prior_ids = [p.id for p in old_phases]
    next_new_phase_id_floor = _next_available_phase_id_from_priors(old_phases)

    # Bounded retry shared between LineageValidationError and
    # SchemaValidationError. Both are recurring synthesizer-output
    # failure modes that benefit from one retry with named feedback
    # before pausing the loop:
    #
    #   - LineageValidationError: structural lineage rejects, e.g.
    #     lineage.from[] referencing historical phase_ids no longer
    #     in current priors.
    #   - SchemaValidationError: orchestra rejected the verdict at
    #     schema_validation (additional properties on lineage.phases[],
    #     missing required fields, enum miss, malformed array, or a
    #     json-parse failure).
    #
    # Budget is one retry shared by both (max_attempts=2). Council
    # cycles are ~12 min wall clock and 5+ model calls; a second
    # retry is too expensive for a structural error the model has
    # already failed once even with explicit feedback. If the model
    # can't produce a valid verdict after one retry, the structural
    # ownership is wrong, not the model. The shared budget means an
    # attempt-1 schema failure followed by an attempt-2 lineage
    # failure (or any other combination) raises immediately — the
    # run does not get a third attempt.
    max_attempts = 2
    previous_attempt_error: str | None = None
    assembled_plan = None
    normalized_lineage: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        try:
            new_plan_text, verdict = _invoke_council_for_reauthor(
                council=council,
                state_blob=state_blob,
                question=question,
                ledger_slice_md=ledger_slice_md,
                design_context_md=design_context_md,
                project_dir=project_dir,
                previous_attempt_error=previous_attempt_error,
            )

            lineage_obj = verdict.get("lineage") if isinstance(verdict, dict) else None
            if not isinstance(lineage_obj, dict):
                raise LineageValidationError(
                    "synthesizer verdict is missing the required "
                    "'lineage' object; the re-author path requires "
                    "the JSON lineage sidecar"
                )

            # Preserve-by-default assembly. The synthesizer authors
            # changed phase content and non-preserve lineage intent;
            # Duplo wraps the output in the deterministic envelope.
            # See orchestra/design/synthesizer-output-contract.md.
            # Phase C Increment 12 (T-000192) routes parsing through
            # bob_tools.planfile and rebuilds each synth phase for
            # constructed-mode field-stability before assembly.
            synth_plan_parsed = parse_plan(new_plan_text)
            synth_phases = tuple(
                rebuild_phase_constructed(phase, ordinal=index + 1)
                for index, phase in enumerate(synth_plan_parsed.phases)
            )

            # Structural lineage check on the synthesizer's RAW
            # output. Runs before normalize_lineage_for_preservation
            # so the error surface reports what the model wrote,
            # not values the runtime filled in. Errors here include
            # collision with current prior ids, below-floor ids,
            # and historical 'from' references — all the cases the
            # retry feedback can name precisely for the model.
            _validate_lineage_structural(prior_ids, next_new_phase_id_floor, lineage_obj)

            normalized_lineage = normalize_lineage_for_preservation(prior_ids, lineage_obj)

            # Lineage check on the normalized sidecar. Runs BEFORE
            # assembly because assembly composes around
            # ``replace_phase_validated``, which fails fast with a
            # ``PlanValidationError`` the moment a substitution would
            # introduce a duplicate phase id. A contradictory lineage
            # (e.g. a prior id appearing as both ``preserve`` and a
            # supersede ``from`` source) would trip that fail-fast and
            # mask the actual cause behind the canonical validator's
            # message. Pre-flighting the lineage here lets
            # ``validate_lineage`` surface the contradiction with its
            # named message before assembly is attempted. The
            # ``new_plan_ids`` passed in is the set of ids the
            # synthesizer declared in lineage; the header-vs-phases
            # check (#5 in ``validate_lineage``) trivially passes
            # because we compute the list from the same lineage. The
            # internal contradiction checks (preserve+consume,
            # mixed-action consume, unaccounted priors) are what we
            # need to fire first.
            lineage_seen_ids = [
                entry["id"]
                for entry in normalized_lineage.get("phases", [])
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            ]
            validate_lineage(
                prior_ids,
                lineage_seen_ids,
                normalized_lineage,
            )

            assembled_plan = assemble_reauthored_plan(
                prior_plan=prior_plan,
                synth_phases=synth_phases,
                normalized_lineage=normalized_lineage,
            )

            # Final lineage check on the assembled Plan + normalized
            # lineage. The internal contradictions are already covered
            # by the pre-flight call above; this run catches the
            # header-vs-phases mismatch that only the assembled plan
            # can surface (e.g. assembly emitted an unexpected id).
            assembled_ids = tuple(
                p.phase_id for p in assembled_plan.phases if p.phase_id is not None
            )
            validate_lineage(
                prior_ids,
                assembled_ids,
                normalized_lineage,
            )

            # Commit-attribution check. Optional in the schema; when
            # present, every entry must name a known unattributable
            # commit + a phase that exists in the assembled plan.
            # Runs after assembly so new_plan_ids includes any new
            # phases the synthesizer introduced this pass.
            attributions = (
                verdict.get("commit_attributions") if isinstance(verdict, dict) else None
            )
            if isinstance(attributions, list) and attributions:
                triggering_commits = _triggering_unattributable_commits(crossing, events)
                _validate_commit_attributions(
                    attributions,
                    triggering_commits,
                    prior_ids,
                    [p.phase_id for p in assembled_plan.phases if p.phase_id is not None],
                )
            break
        except LineageValidationError as exc:
            if attempt >= max_attempts:
                raise
            previous_attempt_error = _format_lineage_feedback_for_retry(
                error=exc,
                prior_plan_ids=prior_ids,
                next_new_phase_id_floor=next_new_phase_id_floor,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        except SchemaValidationError as exc:
            if attempt >= max_attempts:
                raise
            previous_attempt_error = _format_schema_feedback_for_retry(
                error=exc,
                attempt=attempt,
                max_attempts=max_attempts,
            )

    # Loop exited normally (break on success). assembled_plan is
    # guaranteed non-None here because the only exit-without-break
    # path is the final-attempt raise above.
    assert assembled_plan is not None

    # Canonical validation. assert_mcloop_canonical runs the renderer,
    # re-parses the result, requires semantic equality after the
    # canonical normalizer, and enforces the R1/R2 equivalents
    # independently. It is the contract gate the saved PLAN.md must
    # pass before mcloop is allowed to consume it. Per T-000192,
    # raw-text writes have been removed from this path; the assembled
    # plan goes through bob_tools.planfile.save (default validation
    # mode is "canonical"), which renders and writes exactly the
    # validated bytes.
    try:
        assembled_plan_text = assert_mcloop_canonical(assembled_plan, source_path=plan_path)
    except PlanValidationError as exc:
        raise ReauthorError(
            "assembled reauthor plan failed the canonical contract: "
            f"{exc}. The synthesizer's output combined with Duplo's "
            "preserve-by-default assembly produced a plan that is "
            "not mcloop-canonical; the run is paused so the inputs "
            "can be inspected."
        ) from exc

    diff = compute_lineage_diff(normalized_lineage)

    git_snapshot = _capture_git_snapshot(project_dir)

    from_plan_commit = _git_head_sha(project_dir)
    planfile_save(out_path, assembled_plan)
    to_plan_commit = _git_head_sha(project_dir)

    lifecycle_event_ids = _emit_lifecycle_events(
        storage=storage,
        diff=diff,
        crossing_event_id=crossing_event_id,
        run_id=council_run_id,
        git=git_snapshot,
    )

    plan_reauthored_event_id = _emit_plan_reauthored(
        storage=storage,
        crossing_event_id=crossing_event_id,
        lifecycle_event_ids=lifecycle_event_ids,
        from_plan_commit=from_plan_commit,
        to_plan_commit=to_plan_commit,
        council_run_id=council_run_id,
        run_id=council_run_id,
        git=git_snapshot,
    )

    # Hush the unused-import warning for EventType when callers do not
    # exercise it (the storage helper imports it through its public
    # interface).
    _ = EventType
    _ = GitSnapshot

    return ReauthorResult(
        new_plan_path=out_path,
        new_plan_text=assembled_plan_text,
        lineage_diff=diff,
        lifecycle_event_ids=lifecycle_event_ids,
        plan_reauthored_event_id=plan_reauthored_event_id,
        council_run_id=council_run_id,
    )


# ---------------------------------------------------------------------
# Crossing lookup
# ---------------------------------------------------------------------


def _find_crossing(events: Iterable[Any], crossing_event_id: str) -> Any:
    from bob_tools.ledger import EventType

    for ev in events:
        if ev.event_id == crossing_event_id:
            if ev.type is not EventType.THRESHOLD_CROSSED:
                raise ReauthorError(
                    f"event {crossing_event_id!r} is type "
                    f"{ev.type.value!r}, expected threshold_crossed"
                )
            return ev
    raise ReauthorError(f"threshold_crossed event {crossing_event_id!r} not found in ledger")


def _triggering_unattributable_commits(crossing: Any, events: Iterable[Any]) -> list[str]:
    """Return the list of full commit SHAs from the unattributable
    commit_landed events that triggered ``crossing``.

    The crossing's payload carries ``triggering_event_ids``; each
    such event is looked up in ``events``. For COMMIT_LANDED events
    whose ``attributed_phase_id`` is None (the unattributable case
    that fires rule 1), the ``commit`` field is collected.

    When the triggering events aren't commit_landed (e.g., a
    phase_superseded crossing), the returned list is empty — the
    synthesizer shouldn't emit ``commit_attributions`` for non-
    unattributable_commit crossings.
    """
    from bob_tools.ledger import EventType

    triggering_ids = (
        crossing.payload.get("triggering_event_ids") or []
        if hasattr(crossing, "payload") and isinstance(crossing.payload, dict)
        else []
    )
    if not triggering_ids:
        return []
    events_by_id = {ev.event_id: ev for ev in events}
    out: list[str] = []
    for trig_id in triggering_ids:
        trig = events_by_id.get(trig_id)
        if trig is None or trig.type is not EventType.COMMIT_LANDED:
            continue
        payload = getattr(trig, "payload", None) or {}
        if payload.get("attributed_phase_id") is not None:
            continue
        commit = payload.get("commit")
        if isinstance(commit, str) and commit:
            out.append(commit)
    return out


def _previous_plan_reauthored_event_id(
    events: Iterable[Any],
) -> str | None:
    """Return the event_id of the most recent prior plan_reauthored,
    or None for first re-author. The since-cursor for ledger_slice
    construction."""
    from bob_tools.ledger import EventType

    most_recent: str | None = None
    for ev in events:
        if ev.type is EventType.PLAN_REAUTHORED:
            if most_recent is None or ev.event_id > most_recent:
                most_recent = ev.event_id
    return most_recent


def _crossing_summary(crossing: Any) -> str:
    """Compact human-readable summary used in the question prompt."""
    rule_id = crossing.payload.get("rule_id", "?")
    summary = crossing.payload.get("summary", "")
    return f"{rule_id}: {summary}"


# ---------------------------------------------------------------------
# ledger_slice and design_context builders
# ---------------------------------------------------------------------


def _build_ledger_slice(
    events: Iterable[Any],
    state: Any,
    crossing: Any,
    since_event_id: str | None,
    old_phases: list[ParsedHeader],
) -> str:
    """Markdown brief grouped by phase, per the Codex Q3 shape.

    Triggering crossing first; per phase: status, lineage, lifecycle
    events, evidence refs, assumptions/invariants/findings, design
    reasoning refs; unattributed/orphaned section; since boundary.
    Event ids in every bullet for ledger traceability.
    """
    from bob_tools.ledger.thresholds import (
        ThresholdRecommendedAction,
        ThresholdRuleId,
        ThresholdSeverity,
    )

    rule_id_str = crossing.payload.get("rule_id", "")
    severity, recommended_action = _derive_severity_and_action(
        rule_id_str,
        ThresholdRuleId,
        ThresholdSeverity,
        ThresholdRecommendedAction,
    )

    triggering_ids = crossing.payload.get("triggering_event_ids") or []
    triggering_str = ", ".join(triggering_ids) or "(none)"

    lines: list[str] = []
    lines.append("# Ledger slice")
    lines.append("")
    lines.append("## Triggering threshold crossing")
    lines.append("")
    lines.append(f"- crossing_event_id: {crossing.event_id}")
    lines.append(f"- rule_id: {rule_id_str}")
    lines.append(f"- severity: {severity}")
    lines.append(f"- recommended_action: {recommended_action}")
    lines.append(f"- triggering_event_ids: {triggering_str}")
    summary_text = crossing.payload.get("summary", "")
    if summary_text:
        lines.append(f"- summary: {summary_text}")
    lines.append("")
    lines.append("## Since boundary")
    lines.append("")
    if since_event_id is None:
        lines.append("- (first re-author; window covers all prior events)")
    else:
        lines.append(f"- previous plan_reauthored event_id: {since_event_id}")
    lines.append("")

    phase_old_titles = {p.id: p.title for p in old_phases}
    lines.append("## Phases (current)")
    lines.append("")
    if not state.phases:
        lines.append("- (none)")
        lines.append("")
    for phase in state.phases:
        title = phase_old_titles.get(phase.id, phase.title)
        lines.append(f"### Phase {phase.id}: {title}")
        lines.append("")
        lines.append(f"- created_event_id: {phase.created_event_id}")
        lines.append(f"- status: {phase.status}")
        if phase.lineage.predecessors:
            lines.append("- lineage.predecessors: " + ", ".join(phase.lineage.predecessors))
        if phase.lineage.successors:
            lines.append("- lineage.successors: " + ", ".join(phase.lineage.successors))
        if phase.lineage.supersession is not None:
            lines.append(
                "- lineage.supersession: superseded_by="
                f"{phase.lineage.supersession.superseded_by_id}, "
                f"reason={phase.lineage.supersession.reason}"
            )
        if phase.modification_history:
            lines.append("- modification_history: " + ", ".join(phase.modification_history))
        if phase.evidence_refs:
            lines.append("- evidence_refs: " + ", ".join(phase.evidence_refs))
        if phase.design_reasoning_refs:
            lines.append("- design_reasoning_refs: " + ", ".join(phase.design_reasoning_refs))
        lines.append("")

    if state.invariants:
        lines.append("## Invariants")
        lines.append("")
        for inv in state.invariants:
            scope = f"phase={inv.phase_id}" if inv.phase_id else "scope=plan"
            lines.append(
                f"- {inv.invariant_id}: {inv.statement} "
                f"({scope}, declared_event_id={inv.declared_event_id})"
            )
        lines.append("")

    if state.assumptions:
        lines.append("## Assumptions")
        lines.append("")
        for ar in state.assumptions:
            falsified = f", falsified_event_id={ar.falsified_event_id}" if ar.falsified else ""
            scope = f"phase={ar.phase_id}" if ar.phase_id else "scope=plan"
            lines.append(
                f"- {ar.assumption_id} ({scope}, "
                f"confidence={ar.confidence}, "
                f"falsified={ar.falsified}{falsified}, "
                f"declared_event_id={ar.declared_event_id}): "
                f"{ar.statement}"
            )
        lines.append("")

    if state.human_decisions:
        lines.append("## Human decisions")
        lines.append("")
        for decision in state.human_decisions:
            applies = ", ".join(decision.applies_to_phase_ids) or "(plan-wide)"
            lines.append(
                f"- {decision.decision_id} "
                f"(decided_event_id={decision.decided_event_id}, "
                f"applies_to={applies}, by={decision.decided_by}): "
                f"{decision.summary}"
            )
        lines.append("")

    unattributed = state.findings_unattributed
    orphaned = state.orphaned_design_reasoning
    if unattributed or orphaned:
        lines.append("## Unattributed and orphaned")
        lines.append("")
        for ev_id in unattributed:
            lines.append(f"- finding_observed (no phase): {ev_id}")
        for ev_id in orphaned:
            lines.append(f"- design_reasoning_recorded (orphaned): {ev_id}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _derive_severity_and_action(
    rule_id_str: str,
    rule_id_enum: Any,
    severity_enum: Any,
    action_enum: Any,
) -> tuple[str, str]:
    """Map a persisted ``rule_id`` string to its severity and action.

    Per the Slice A schema the threshold_crossed payload carries
    only ``{rule_id, triggering_event_ids, summary}``; severity and
    recommended_action are constants per rule. This helper centralizes
    the lookup.
    """
    plan_rules = {
        rule_id_enum.UNATTRIBUTABLE_COMMIT.value,
        rule_id_enum.INVARIANT_DECLARED.value,
        rule_id_enum.EXPLORATORY_COUNT_EXCEEDED.value,
    }
    phase_rules = {
        rule_id_enum.PHASE_ABANDONED.value,
        rule_id_enum.PHASE_SUPERSEDED.value,
        rule_id_enum.PHASE_TOPOLOGY_CHANGED.value,
        rule_id_enum.ASSUMPTION_FALSIFIED.value,
    }
    severity = severity_enum.TRIGGER_REAUTHOR.value
    if rule_id_str in plan_rules:
        action = action_enum.REAUTHOR_PLAN.value
    elif rule_id_str in phase_rules:
        action = action_enum.REAUTHOR_PHASE.value
    else:
        action = action_enum.LOG_ONLY.value
    return severity, action


def _build_design_context(
    events: Iterable[Any],
    state: Any,
    plan_text: str,
    old_phases: list[ParsedHeader],
) -> str:
    """Markdown brief of design rationale.

    Primary source: ``design_reasoning_recorded`` events for the
    current phases. Fallback: best-effort regex scan of PLAN.md
    sections (rationale, decisions, constraints, risks,
    assumptions). The fallback is tagged
    ``source: plan_text_best_effort`` so downstream prompts see it
    is weaker than structured ledger data.
    """
    from bob_tools.ledger import EventType

    reasoning_events_by_id = {
        ev.event_id: ev for ev in events if ev.type is EventType.DESIGN_REASONING_RECORDED
    }

    lines: list[str] = ["# Design context", ""]

    structured_present = any(phase.design_reasoning_refs for phase in state.phases)

    if structured_present:
        lines.append("## Per-phase design reasoning (from ledger)")
        lines.append("")
        for phase in state.phases:
            if not phase.design_reasoning_refs:
                continue
            title = next(
                (p.title for p in old_phases if p.id == phase.id),
                phase.title,
            )
            lines.append(f"### Phase {phase.id}: {title}")
            lines.append("")
            for ref_id in phase.design_reasoning_refs:
                ev = reasoning_events_by_id.get(ref_id)
                if ev is None:
                    continue
                payload = ev.payload
                rationale = payload.get("rationale", "")
                lines.append(
                    f"- decision_id: {payload.get('decision_id', '?')} (event_id={ev.event_id})"
                )
                if rationale:
                    lines.append(f"  - rationale: {rationale}")
                constraints = payload.get("constraints") or []
                if constraints:
                    lines.append("  - constraints: " + "; ".join(constraints))
                rejected = payload.get("approaches_rejected") or []
                for entry in rejected:
                    approach = entry.get("approach", "?")
                    reason = entry.get("reason", "")
                    lines.append(f"  - rejected: {approach} ({reason})")
            lines.append("")

    fallback = _extract_plan_text_design_context(plan_text)
    if fallback:
        lines.append("## Plan-text fallback (source: plan_text_best_effort)")
        lines.append("")
        for heading, body in fallback:
            lines.append(f"### {heading}")
            lines.append("")
            lines.append(body.strip())
            lines.append("")

    if not structured_present and not fallback:
        lines.append(
            "(no structured design reasoning or recognizable rationale sections in PLAN.md)"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_PLAN_TEXT_HEADINGS = {
    "rationale",
    "decisions",
    "constraints",
    "risks",
    "assumptions",
    "rejected approaches",
    "rejected options",
    "design reasoning",
}


def _extract_plan_text_design_context(
    plan_text: str,
) -> list[tuple[str, str]]:
    """Best-effort scan for rationale-shaped sections in PLAN.md.

    Returns a list of (heading, body) pairs for any markdown header
    whose text (case-insensitive) matches one of the recognized
    design-context heading words. Used when the ledger has no
    ``design_reasoning_recorded`` events (typical for plans authored
    before Slice C existed).
    """
    out: list[tuple[str, str]] = []
    lines = plan_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        # Match `#`, `##`, or `###` headers.
        match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if match is not None:
            heading_text = match.group(2).lower().strip(":").strip()
            if heading_text in _PLAN_TEXT_HEADINGS:
                # Collect body until the next header at this level or
                # higher.
                level = len(match.group(1))
                body_lines: list[str] = []
                j = i + 1
                while j < len(lines):
                    next_match = re.match(r"^(#{1,6})\s+", lines[j])
                    if next_match is not None and len(next_match.group(1)) <= level:
                        break
                    body_lines.append(lines[j])
                    j += 1
                body = "\n".join(body_lines).strip()
                if body:
                    out.append((match.group(2).strip(), body))
                i = j
                continue
        i += 1
    return out


# ---------------------------------------------------------------------
# Council invocation
# ---------------------------------------------------------------------


def _next_available_phase_id_from_priors(
    old_phases: list[ParsedHeader],
) -> str:
    """Highest-suffix + 1, three-digit zero-padded ``phase_NNN``.

    Mirrors duplo.council.compute_required_phase_id's safe rule
    (highest + 1, not the smallest gap). When there are no prior
    phases, returns ``"phase_001"``.

    Reauthor's reason for needing this is exactly the canonical
    mode's reason for required_phase_id: synthesizers cannot be
    trusted to compute a non-colliding new id from inspection of
    the prior plan, especially when that plan has gaps from
    earlier reauthor runs that consumed intermediate ids. A run
    where the prior is ``phase_001`` plus ``phase_006-009`` (gap
    where ``phase_002-005`` were superseded) saw the synthesizer
    pick ``phase_006-009`` as "new" ids — colliding with the
    existing priors — because it had assumed sequential numbering.
    Injecting the start value explicitly removes the guess.
    """
    highest = 0
    for phase in old_phases:
        suffix = phase.id.removeprefix("phase_")
        if suffix.isdigit():
            n = int(suffix)
            if n > highest:
                highest = n
    return f"phase_{highest + 1:03d}"


_NEW_ID_ACTIONS_STRUCTURAL = frozenset(["supersede", "split", "merge", "new"])
_FROM_BEARING_ACTIONS_STRUCTURAL = frozenset(["supersede", "split", "merge"])
_STRICT_PHASE_ID_RE = re.compile(r"^phase_(\d{3,})$")


def _phase_id_strict_suffix(pid: str) -> int | None:
    """Return the numeric suffix of a strict ``phase_NNN`` id, else None.

    Mirrors :func:`duplo.council.compute_required_phase_id`'s strict-id
    helper: only ids matching the canonical zero-padded form
    participate in the floor comparison. Non-strict ids (legacy
    free-form names from pre-Slice C plans, or label-style ids the
    synthesizer might invent) are ignored by the floor check; the
    collision-with-prior check still applies to them.
    """
    match = _STRICT_PHASE_ID_RE.match(pid)
    if match is None:
        return None
    return int(match.group(1))


def _validate_lineage_structural(
    prior_plan_ids: list[str],
    next_new_phase_id_floor: str,
    lineage: Any,
) -> None:
    """Reject reauthor lineage that collides with prior ids or sits
    below the runtime-supplied id floor, with explicit error
    messages.

    Runs BEFORE :func:`validate_lineage` and accumulates all
    violations into a single :class:`LineageValidationError`. The
    Slice C contract (existing in ``validate_lineage``) catches the
    same collisions structurally, but with generic phrasing. This
    layer's purpose is the diagnostic: error messages name the
    current prior id list and the floor explicitly so the
    synthesizer (or a human operator) sees what value to use next
    instead of having to derive it.

    The smoke run that motivated this layer had a prior plan of
    ``phase_001`` + ``phase_006-009`` and a synthesizer that emitted
    ``phase_006-009`` as new supersede ids while citing
    ``phase_002-005`` (historical, no longer current) in ``from``.
    ``validate_lineage`` would still catch both, but the failure
    message did not name the floor or the current prior id set
    explicitly; the operator had to read the validator source to
    derive the right next value.

    Rules enforced:

      - Action in {supersede, split, merge, new}: ``id`` MUST NOT be
        in ``prior_plan_ids``. Error message names the current
        prior id list and the floor.
      - Action in {supersede, split, merge, new}: if ``id`` matches
        strict ``phase_NNN`` form, its suffix MUST be >= the floor's
        suffix. Error message names the floor.
      - Action in {supersede, split, merge}: every ``from`` entry
        MUST be in ``prior_plan_ids``. Error message names the
        current prior id list.
    """
    if not isinstance(lineage, dict):
        return  # validate_lineage will fail with its own message
    phases = lineage.get("phases")
    if not isinstance(phases, list):
        return  # validate_lineage will fail with its own message

    prior_set = set(prior_plan_ids)
    floor_suffix = _phase_id_strict_suffix(next_new_phase_id_floor)
    prior_repr = ", ".join(prior_plan_ids) if prior_plan_ids else "(none)"

    errors: list[str] = []
    for entry in phases:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        action = entry.get("action")
        from_field = entry.get("from")
        if not isinstance(pid, str) or not pid:
            continue
        if action not in _NEW_ID_ACTIONS_STRUCTURAL:
            # preserve, or unknown action — handled by validate_lineage
            continue

        if pid in prior_set:
            errors.append(
                f"new phase id {pid!r} (action={action!r}) collides with "
                f"a current prior plan id; current prior plan ids: "
                f"[{prior_repr}]; use the runtime-supplied floor "
                f"{next_new_phase_id_floor!r} or higher for new ids"
            )
        elif floor_suffix is not None:
            entry_suffix = _phase_id_strict_suffix(pid)
            if entry_suffix is not None and entry_suffix < floor_suffix:
                errors.append(
                    f"new phase id {pid!r} (action={action!r}) is below "
                    f"the runtime-supplied floor "
                    f"{next_new_phase_id_floor!r}; use the floor or higher"
                )

        if action in _FROM_BEARING_ACTIONS_STRUCTURAL and isinstance(from_field, list):
            for fid in from_field:
                if not isinstance(fid, str) or not fid:
                    continue
                if fid not in prior_set:
                    errors.append(
                        f"lineage entry {pid!r} (action={action!r}) names "
                        f"{fid!r} in 'from', but it is not a current "
                        f"prior plan id; current prior plan ids: "
                        f"[{prior_repr}] (historical ids from earlier "
                        f"reauthor runs are not valid 'from' references)"
                    )

    if errors:
        raise LineageValidationError(
            "structural lineage violations:\n  - " + "\n  - ".join(errors)
        )


def _validate_commit_attributions(
    attributions: list[dict[str, Any]],
    triggering_unattributable_commits: list[str],
    prior_plan_ids: list[str],
    new_plan_ids: list[str],
) -> None:
    """Reject ill-formed verdict.commit_attributions entries.

    Schema validation runs upstream (orchestra) and catches shape
    errors (wrong types, additional properties, etc.). This layer
    checks the semantic contract:

      - ``commit_sha`` MUST prefix-match an unattributable commit in
        the triggering crossing's slice. Schema only requires a
        7-64 char string; the runtime requires it to actually
        identify a known unattributed commit.
      - ``phase_id`` MUST be in (prior_plan_ids ∪ new_plan_ids).
        The synthesizer cannot attribute a commit to a phase that
        doesn't exist anywhere in the plan.
      - ``rationale`` MUST be non-empty after strip. Schema requires
        minLength=1; this re-checks defensively (whitespace-only
        slips past minLength=1 unless trimmed).

    Errors accumulate; a single :class:`CommitAttributionError`
    lists every violation so the synthesizer (or the human
    watching the smoke pipeline) sees the full picture, matching
    the pattern of :func:`_validate_lineage_structural`.
    """
    if not attributions:
        return

    valid_phase_ids = set(prior_plan_ids) | set(new_plan_ids)
    triggering_repr = (
        ", ".join(triggering_unattributable_commits)
        if triggering_unattributable_commits
        else "(none)"
    )
    valid_phase_repr = ", ".join(sorted(valid_phase_ids)) if valid_phase_ids else "(none)"

    errors: list[str] = []
    for index, entry in enumerate(attributions):
        if not isinstance(entry, dict):
            errors.append(f"commit_attributions[{index}] is not an object")
            continue
        sha = entry.get("commit_sha")
        pid = entry.get("phase_id")
        rationale = entry.get("rationale")

        if not isinstance(sha, str) or not sha:
            errors.append(f"commit_attributions[{index}].commit_sha is missing or empty")
        elif not any(
            isinstance(c, str) and c and (c.startswith(sha) or sha.startswith(c))
            for c in triggering_unattributable_commits
        ):
            errors.append(
                f"commit_attributions[{index}].commit_sha={sha!r} does "
                "not prefix-match any unattributable commit in the "
                f"triggering crossing's slice; known commits: "
                f"[{triggering_repr}]"
            )

        if not isinstance(pid, str) or not pid:
            errors.append(f"commit_attributions[{index}].phase_id is missing or empty")
        elif pid not in valid_phase_ids:
            errors.append(
                f"commit_attributions[{index}].phase_id={pid!r} is not "
                "in the union of prior plan ids and new ids "
                f"introduced in this reauthor's lineage; valid phase "
                f"ids: [{valid_phase_repr}]"
            )

        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(
                f"commit_attributions[{index}].rationale must be a "
                "non-empty string explaining the attribution; got "
                f"{rationale!r}"
            )

    if errors:
        raise CommitAttributionError(
            "commit_attribution violations:\n  - " + "\n  - ".join(errors)
        )


_SCHEMA_KIND_HINTS: dict[SchemaFailureKind, str] = {
    SchemaFailureKind.ADDITIONAL_PROPERTIES: (
        "Remove fields the schema does not declare. Lineage entries "
        "carry only id / action / from; commit attribution lives in "
        "the top-level commit_attributions array, not on "
        "lineage.phases[]. Refer to the schema enumerated in your "
        "synthesizer brief for the complete allowed-field list."
    ),
    SchemaFailureKind.MISSING_REQUIRED: (
        "Populate every required field. Common omissions: 'feedback' "
        "(synthesizer prose), 'criteria_compliance' (one entry per "
        "configured criterion), and the per-action 'from' list on "
        "supersede / split / merge lineage entries."
    ),
    SchemaFailureKind.ENUM_MISMATCH: (
        "Use only the documented enum values. decision must be one "
        "of {accept, reframe, stuck}; lineage.phases[].action must "
        "be one of {preserve, supersede, split, merge, new}. Do not "
        "invent intermediate states (e.g. 'pending', 'tentative')."
    ),
    SchemaFailureKind.MALFORMED_ARRAY: (
        "Array fields require JSON arrays. Wrap singletons in []; "
        "do not emit a bare string where an array is required."
    ),
    SchemaFailureKind.JSON_PARSE: (
        "Your prior response could not be parsed as JSON inside the "
        "trailing ```json ... ``` fence. Emit exactly one fenced "
        "json block at the end of your response and ensure the body "
        "is a single well-formed JSON object."
    ),
    SchemaFailureKind.OTHER: (
        "Re-read the schema your synthesizer brief enumerates and "
        "match every field's type, required-ness, and shape."
    ),
}


def _format_schema_feedback_for_retry(
    *,
    error: SchemaValidationError,
    attempt: int,
    max_attempts: int,
) -> str:
    """Build the previous_attempt_error block for a schema-failure retry.

    Mirrors :func:`_format_lineage_feedback_for_retry` in shape so
    the framer template treats both retry causes uniformly. The
    block names every classified kind (so multi-violation verdicts
    get all hints), enumerates the raw validator error strings
    verbatim (so the synthesizer sees what orchestra actually wrote
    in the log), and reminds the synthesizer that the budget is one
    retry total.
    """
    cls = error.classification
    kind_lines: list[str] = []
    seen: set[SchemaFailureKind] = set()
    # Surface kinds in a stable order: primary first, then the rest
    # of the present kinds. Each kind appears at most once.
    for kind in [cls.primary, *sorted(cls.kinds, key=lambda k: k.value)]:
        if kind in seen:
            continue
        seen.add(kind)
        hint = _SCHEMA_KIND_HINTS.get(kind, _SCHEMA_KIND_HINTS[SchemaFailureKind.OTHER])
        kind_lines.append(f"  - {kind.value}: {hint}")

    raw_lines = [f"  - {e}" for e in cls.errors] or [
        "  - (validator emitted no error strings; see audit log)"
    ]

    lines: list[str] = [
        f"RETRY ATTEMPT {attempt + 1} of {max_attempts}",
        "",
        "PREVIOUS ATTEMPT'S VERDICT FAILED SCHEMA VALIDATION. The "
        "orchestra runtime rejected the verdict JSON before lineage "
        "or attribution checks could run. Classified kinds:",
        *kind_lines,
        "",
        "Raw validator errors (verbatim from the schema_validation log record):",
        *raw_lines,
        "",
        "Regenerate the plan and verdict. Keep correct content; fix "
        "only the named schema violations. This is the LAST retry the "
        "run is allotted; subsequent failures fail closed.",
    ]
    return "\n".join(lines)


def _format_lineage_feedback_for_retry(
    *,
    error: LineageValidationError,
    prior_plan_ids: list[str],
    next_new_phase_id_floor: str,
    attempt: int,
    max_attempts: int,
) -> str:
    """Format a retry-feedback block for the next council brief.

    The block is injected into the workflow's
    ``previous_attempt_error`` external input. The framer template
    renders it verbatim at the top of the council brief so every
    proposer and the synthesizer see the named constraints before
    regenerating.

    The block lists every violation the validator accumulated (not
    just the first), plus the allowed-prior-id whitelist and the
    next-available-id floor. Even when the violation is minimal, the
    whitelist and floor are surfaced explicitly: the model should
    always be reminded of the constraint, not just told what it got
    wrong this time.
    """
    raw_message = str(error).strip()
    # _validate_lineage_structural raises with a "structural lineage
    # violations:\n  - <line>\n  - <line>..." shape; preserve those
    # bullets when present, otherwise surface the raw message as one
    # bullet so the model still sees the named cause.
    violations: list[str] = []
    if raw_message.startswith("structural lineage violations:"):
        for line in raw_message.splitlines()[1:]:
            stripped = line.strip()
            if stripped.startswith("- "):
                violations.append(stripped[2:].strip())
            elif stripped:
                # Continuation line (multi-line message); append to
                # the most recent bullet.
                if violations:
                    violations[-1] += " " + stripped
                else:
                    violations.append(stripped)
    else:
        violations.append(raw_message)

    prior_repr = ", ".join(prior_plan_ids) if prior_plan_ids else "(none)"
    lines: list[str] = [
        f"RETRY ATTEMPT {attempt + 1} of {max_attempts}",
        "",
        "PREVIOUS ATTEMPT REJECTED. Reasons:",
    ]
    for violation in violations:
        lines.append(f"  - {violation}")
    lines.extend(
        [
            "",
            "Allowed prior plan ids you may reference in lineage.from[] "
            "(this is the WHITELIST; values outside this list will be "
            "rejected by the runtime validator):",
            f"  [{prior_repr}]",
            "",
            "Do not reference historical phase ids from ledger slice "
            "events in lineage.from[]. The ledger slice may name phases "
            "that were superseded / merged / abandoned in earlier "
            "reauthor cycles; those are causal context only, NOT valid "
            "'from' targets.",
            "",
            "next_new_phase_id_floor is "
            f"{next_new_phase_id_floor!r}; all NEW ids you introduce "
            "(action in {supersede, split, merge, new}) must have a "
            "strict phase_NNN suffix >= the floor's suffix and must NOT "
            "appear in the allowed-prior-id whitelist above.",
            "",
            "Regenerate the plan and verdict. The body and structure are "
            "fine to keep where they were correct; but lineage.from[] "
            "entries MUST come from the allowed list, and new ids MUST "
            "respect the floor.",
        ]
    )
    return "\n".join(lines)


def _build_state_blob(plan_text: str, old_phases: list[ParsedHeader], state: Any) -> str:
    """The ``state`` external input passed into council_four.

    Slice C concatenates the plan text with a short summary of the
    parsed phase ids (so the synthesizer can reason about preserved
    vs new ids without re-parsing the markdown). The plan text
    itself is the primary substrate.

    The summary is followed by an explicit ``next available phase
    id`` value the synthesizer MUST use as the starting point for
    new ids (supersede / split / merge / new). This mirrors the
    canonical-mode required_phase_id contract: protocol metadata
    is owned by the runtime, not the model. A synthesizer that
    ignores the supplied value and picks colliding ids will be
    rejected by validate_lineage; the explicit value reduces the
    error rate by removing the need to guess.
    """
    summary_lines: list[str] = ["Phase ids in the prior plan:"]
    if old_phases:
        for phase in old_phases:
            summary_lines.append(f"  - {phase.id}: {phase.title}")
    else:
        summary_lines.append(
            "  - (none recognized; this is a fresh-id labeling pass on a pre-Slice C plan)"
        )

    next_id = _next_available_phase_id_from_priors(old_phases)
    summary_lines.append("")
    summary_lines.append(
        "Next available phase id (use VERBATIM as the starting "
        f"point for any new ids you introduce): {next_id}"
    )
    summary_lines.append(
        "When introducing multiple new ids in one re-author pass, "
        "increment from this start (e.g., next, next+1, next+2). "
        "NEVER reuse an id that already appears in the prior plan "
        "list above; the validator rejects collisions."
    )

    return "Existing PLAN.md:\n" + plan_text.strip() + "\n\n" + "\n".join(summary_lines) + "\n"


def _invoke_council_for_reauthor(
    *,
    council: Any,
    state_blob: str,
    question: str,
    ledger_slice_md: str,
    design_context_md: str,
    project_dir: Path,
    previous_attempt_error: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run council_four with the five framer inputs populated.

    Returns ``(plan_text, verdict)`` where ``plan_text`` is the
    synthesizer's plan body (the ``plan`` artifact) and ``verdict``
    is the parsed ``judge_verdict`` JSON object (which carries the
    lineage sidecar the re-author consumer needs). Raises
    ``ReauthorError`` on terminal != done, missing plan artifact, or
    a verdict whose shape is not a JSON object.

    When ``previous_attempt_error`` is non-None, it is threaded into
    the workflow's ``previous_attempt_error`` external input. The
    framer template renders it verbatim at the top of the council
    brief so every proposer and the synthesizer see the runtime
    validator's named constraints before regenerating. When None,
    the input is passed as the empty string (the framer template
    treats empty-string as "this is the first attempt; no retry
    feedback to surface").
    """
    from orchestra import run_workflow

    from duplo.council import make_duplo_progress_callback

    cfg = _resolve_orchestra_config(council, project_dir)

    inputs: dict[str, Any] = {
        "state": state_blob,
        "question": question,
        "ledger_slice": ledger_slice_md,
        "design_context": design_context_md,
        "previous_attempt_error": previous_attempt_error or "",
    }

    audits_root = project_dir / ".duplo" / "audits" / "council"
    audits_root.mkdir(parents=True, exist_ok=True)
    result = run_workflow(
        "council_four_reauthor",
        inputs,
        cfg,
        project_dir=project_dir,
        data_root=audits_root / "_runs",
        progress_callback=make_duplo_progress_callback(),
    )

    if result.terminal != "done":
        # The synthesizer state routes to its error outcome on schema
        # validation or json-parse failure; that path produces
        # terminal != "done" with one or more schema_validation log
        # records carrying outcome != "valid". When those records
        # exist, surface SchemaValidationError so the retry loop can
        # see the classified kind and thread named feedback into
        # previous_attempt_error. Only when the log has no such
        # records do we fall through to the generic ReauthorError
        # (terminal != done for some other reason, e.g. an upstream
        # state failed before the synthesizer ran).
        schema_failures = read_schema_validation_failures(result.log_path)
        if schema_failures:
            # The first failure in log order is the one the run
            # actually terminated on; later records (if any) reflect
            # a multi-attempt synthesizer state that retried internally
            # before bubbling out. We classify all errors from the
            # FIRST failure so the retry feedback names the actual
            # blocking cause.
            first = schema_failures[0]
            classification = classify_schema_failure(
                first.validation_errors, outcome=first.outcome
            )
            raise SchemaValidationError(
                "council_four_reauthor terminated at schema validation "
                f"(outcome={first.outcome!r}, "
                f"primary_kind={classification.primary.value!r}); "
                f"see audit at {audits_root / result.run_id}",
                classification=classification,
                failures=tuple(schema_failures),
            )
        raise ReauthorError(
            "council_four_reauthor did not accept during re-author "
            f"(terminal={result.terminal!r}); see audit at "
            f"{audits_root / result.run_id}"
        )

    plan_view = result.artifacts.get("plan")
    if plan_view is None or not isinstance(plan_view.value, str):
        raise ReauthorError("council_four_reauthor accepted but produced no 'plan' artifact")
    plan_text: str = plan_view.value.strip()
    if not plan_text:
        raise ReauthorError("council_four_reauthor produced an empty plan")

    # Plan-artifact contract: the synthesizer template instructs
    # the model to emit its response in two parts — the plan body
    # (markdown) followed by a single trailing fenced ``json`` block
    # carrying the verdict. The text adapter captures the entire
    # response into the plan artifact, so the verdict text appears
    # INSIDE the plan artifact even though orchestra also surfaces
    # a parsed verdict via the judge_verdict artifact. The sanitizer
    # extracts the trailing fenced verdict (if present) and returns
    # the plan body without it; we reconcile the extraction against
    # orchestra's judge_verdict artifact to surface model-output
    # errors that would otherwise corrupt PLAN.md or the lineage
    # sidecar.
    try:
        plan_text, extracted_verdict = sanitize_plan_artifact(plan_text)
    except PlanArtifactRejected as exc:
        raise PlanArtifactError(
            "plan_artifact_contained_verdict_json: "
            f"council_four_reauthor's plan artifact carried a "
            f"fenced 'json' block in a shape that does not match the "
            "documented trailing-fenced-verdict contract. "
            f"Underlying error: {exc}"
        ) from exc

    verdict_view = result.artifacts.get("judge_verdict")
    if verdict_view is None or not isinstance(verdict_view.value, dict):
        raise ReauthorError(
            "council_four_reauthor accepted but produced no 'judge_verdict' "
            "artifact (or its value is not a JSON object); the "
            "re-author path requires the verdict for the lineage "
            "sidecar"
        )
    verdict: dict[str, Any] = dict(verdict_view.value)

    # Reconcile the extracted trailing-fenced verdict (if any)
    # against orchestra's parsed judge_verdict artifact. They should
    # be byte-equivalent JSON objects: orchestra extracts the
    # verdict via its own parser; this sanitizer extracts the same
    # fence via bob_tools.planfile.sanitize_plan_artifact. If they
    # disagree, the model emitted something the two parsers
    # disagree on — better to fail closed than to silently pick one.
    if extracted_verdict is not None and extracted_verdict != verdict:
        raise PlanArtifactError(
            "plan_artifact_verdict_mismatch: the trailing fenced "
            "verdict extracted from the plan artifact does not "
            "equal the judge_verdict artifact value. This points "
            "to a parser disagreement on the model's output and is "
            "treated as a model error."
        )

    return plan_text + "\n", verdict


def _resolve_orchestra_config(council: Any, project_dir: Path) -> Any:
    """Reuse council's config-resolution path for parity with the
    canonical-mode council invocation."""
    from orchestra.config import (
        ConfigError,
        OrchestraConfig,
        RoleBinding,
        WorkflowConfig,
        load_config,
    )

    return council._load_or_fallback_config(  # noqa: SLF001
        project_dir,
        load_config=load_config,
        config_cls=OrchestraConfig,
        role_cls=RoleBinding,
        workflow_cls=WorkflowConfig,
        config_error=ConfigError,
    )


# ---------------------------------------------------------------------
# Lifecycle event emission
# ---------------------------------------------------------------------


def _emit_lifecycle_events(
    *,
    storage: Any,
    diff: LineageDiff,
    crossing_event_id: str,
    run_id: str,
    git: Any,
) -> list[str]:
    """Append phase_superseded / split / merged / abandoned events.

    Order is deterministic per the LineageDiff sort: superseded
    pairs by (old_id, new_id), splits by old_id, merges by sorted
    parents, then abandonment entries by id. The returned list is
    the event ids in emission order; the plan_reauthored payload's
    ``ledger_slice_event_ids`` references this list plus the
    triggering crossing.
    """
    from bob_tools.ledger import EventType
    from bob_tools.ledger.events import (
        make_phase_abandoned_payload,
        make_phase_merged_payload,
        make_phase_split_payload,
        make_phase_superseded_payload,
    )

    emitted: list[str] = []

    for old_id, new_id in diff.superseded:
        ev = storage.append(
            event_type=EventType.PHASE_SUPERSEDED,
            payload=make_phase_superseded_payload(
                phase_id=old_id,
                superseded_by_phase_id=new_id,
                reason=(f"re-author triggered by threshold crossing {crossing_event_id}"),
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(ev.event_id)

    for old_id, new_ids in diff.split:
        ev = storage.append(
            event_type=EventType.PHASE_SPLIT,
            payload=make_phase_split_payload(
                phase_id=old_id,
                into_phase_ids=new_ids,
                reason=(f"re-author triggered by threshold crossing {crossing_event_id}"),
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(ev.event_id)

    for merged_ids, into_id in diff.merged:
        ev = storage.append(
            event_type=EventType.PHASE_MERGED,
            payload=make_phase_merged_payload(
                merged_phase_ids=merged_ids,
                into_phase_id=into_id,
                reason=(f"re-author triggered by threshold crossing {crossing_event_id}"),
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(ev.event_id)

    for old_id, reason in diff.abandoned:
        ev = storage.append(
            event_type=EventType.PHASE_ABANDONED,
            payload=make_phase_abandoned_payload(
                phase_id=old_id,
                reason=reason or "abandoned in re-author",
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(ev.event_id)

    return emitted


def _emit_plan_reauthored(
    *,
    storage: Any,
    crossing_event_id: str,
    lifecycle_event_ids: list[str],
    from_plan_commit: str | None,
    to_plan_commit: str | None,
    council_run_id: str | None,
    run_id: str,
    git: Any,
) -> str:
    """Append the plan_reauthored meta-event.

    The schema requires from_plan_commit and to_plan_commit as
    strings of length >= 4. When the consumer is not in a git
    checkout, fall back to a synthetic short hash of the plan text
    so the schema stays satisfied and the audit value persists.
    """
    from bob_tools.ledger import EventType
    from bob_tools.ledger.events import make_plan_reauthored_payload

    ledger_slice_event_ids = [crossing_event_id, *lifecycle_event_ids]

    ev = storage.append(
        event_type=EventType.PLAN_REAUTHORED,
        payload=make_plan_reauthored_payload(
            from_plan_commit=from_plan_commit or _NO_GIT_PLACEHOLDER,
            to_plan_commit=to_plan_commit or _NO_GIT_PLACEHOLDER,
            ledger_slice_event_ids=ledger_slice_event_ids,
            trigger_event_id=crossing_event_id,
            council_run_id=council_run_id,
        ),
        run_id=run_id,
        git=git,
    )
    return str(ev.event_id)


_NO_GIT_PLACEHOLDER = "no-git"


# ---------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------


def _git_head_sha(project_dir: Path) -> str | None:
    """Return the short git HEAD sha or None when not in a git
    checkout. Best-effort; failures are silent because a plan-only
    workflow without git is a legitimate use case."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _capture_git_snapshot(project_dir: Path) -> Any:
    """Build a GitSnapshot from the current project_dir state."""
    from bob_tools.ledger import GitSnapshot

    head = _git_head_sha(project_dir)
    if head is None:
        return GitSnapshot.empty()
    branch = _git_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
    dirty_text = _git_text(["git", "status", "--porcelain"], project_dir)
    dirty = bool(dirty_text and dirty_text.strip())
    return GitSnapshot(
        commit=head,
        branch=branch,
        dirty=dirty,
        worktree=str(project_dir),
    )


def _git_text(cmd: list[str], project_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout
    return text if text else None


# ---------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------


def _new_run_id() -> str:
    return "reauthor-" + os.urandom(4).hex()


def default_ledger_dir(project_dir: Path | str) -> Path:
    """Default ledger directory location for a duplo project."""
    return Path(project_dir) / _LEDGER_DIR_DEFAULT_NAME


def _plan_sha256(text: str) -> str:
    """Available for future use if the schema gains a plan-hash field."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CommitAttributionError",
    "LineageValidationError",
    "PlanArtifactError",
    "ReauthorError",
    "ReauthorResult",
    "SchemaFailureKind",
    "SchemaValidationError",
    "default_ledger_dir",
    "reauthor_plan",
]
