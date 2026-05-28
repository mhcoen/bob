"""Plan validation (``validate_plan`` and the ``_check_*`` family)."""

from __future__ import annotations

from bob_tools.planfile._shared import (
    _ANNOTATION_KEY_RE,
    _ANNOTATION_OK_RE,
    _KNOWN_LEADING_FLAGS,
    _LEADING_TAG_LIKE_RE,
    _RESERVED_SIBLING_MARKERS,
    _TASK_REF_RE,
    _TRAILING_BRACKET_RE,
    _contains_newline,
    _task_ref,
)
from bob_tools.planfile.construction import (
    _assert_task_field_stability,
    _construction_sentinel_phase,
    _construction_sentinel_plan,
    _construction_sentinel_task,
    _round_trip_scalar,
)
from bob_tools.planfile.iteration import (
    _iter_plan_tasks,
    _iter_plan_tasks_with_label,
    _iter_plan_top_level_tasks_with_label,
    _plan_phase_path,
    _plan_subsection_path,
)
from bob_tools.planfile.model import (
    Plan,
    PlanValidationError,
    Subsection,
    Task,
)


def _check_leading_bracket_tag(task: Task, errors: list[str]) -> None:
    """Flag a leading bracket form that does not match a known tag.

    Per design doc section 4.3, leading-position tags are ``[USER]``,
    ``[BATCH]``, and ``[AUTO:<word>]``. The parser strips known tags
    from the task body, so any tag-shaped bracket form still at the
    leading position of ``task.text`` is by definition unknown to this
    library (either a typo or an attempt to add a new tag without a
    library change). ``[RULEDOUT]`` is a sibling line, not a task tag
    (design doc section 4.3, planfile.md:415-417); when it appears at
    the leading position of a task body it is prose (the task title
    documents the RULEDOUT feature itself), so it is skipped here
    rather than reported as an unknown tag.

    Lowercase bracket forms and multi-word bracket forms are prose
    (``_LEADING_TAG_LIKE_RE`` requires an all-caps identifier of two
    or more characters), so a task that legitimately starts with prose
    like ``[note] do thing`` is not flagged. ``[USER]`` and ``[BATCH]``
    appearing here are skipped: if the parser left them in text it is
    a parser bug, not a validation concern, and double-reporting would
    confuse the user fixing the file.
    """
    m = _LEADING_TAG_LIKE_RE.match(task.text)
    if m is None:
        return
    content = m.group(1)
    if content in _KNOWN_LEADING_FLAGS:
        return
    if content in _RESERVED_SIBLING_MARKERS:
        return
    if ":" in content and content.split(":", 1)[0] == "AUTO":
        return
    errors.append(f"task {_task_ref(task)} has unknown bracket tag [{content}]")


def _check_trailing_annotation(task: Task, errors: list[str]) -> None:
    """Flag a trailing bracket form that looks like a broken annotation.

    Per design doc section 4.2, an annotation is ``[key: value]``: an
    identifier-shaped key, a colon, mandatory whitespace, then a
    non-empty value. The parser strips well-formed annotations from
    the task body; a trailing bracket still in ``task.text`` whose
    content has the ``key:`` prefix but does not satisfy
    ``key: value`` (missing whitespace, empty value, etc.) is the
    canonical malformed-annotation case.

    Bracket forms that do not look like annotation attempts at all
    (no colon, or no identifier-shaped prefix before the colon) are
    treated as prose and left alone — flagging ``[some text]`` at end
    of a task description would produce more false positives than
    real catches. The malformed signal is the *colon* that the author
    typed when reaching for an annotation.
    """
    m = _TRAILING_BRACKET_RE.search(task.text)
    if m is None:
        return
    content = m.group(1)
    if _ANNOTATION_KEY_RE.match(content) is None:
        return
    if _ANNOTATION_OK_RE.match(content) is not None:
        return
    errors.append(f"task {_task_ref(task)} has malformed annotation [{content}]")


def validate_plan(plan: Plan, *, constructed: bool = False) -> None:
    """Validate structural and referential integrity of ``plan``.

    Raises :class:`PlanValidationError` carrying one message per problem
    found; validation does not short-circuit on the first failure so a
    single run surfaces every fix the user needs to make. Checks, in
    the order they are reported:

    1. **Duplicate task ids.** Each ``T-NNNNNN`` must occur exactly
       once in the plan. Tasks without an id (compat-mode) are not
       counted. Per design doc section 7.2: task ids are the canonical
       reference, so two tasks sharing one id makes ``@deps`` ambiguous
       and ``complete_task`` / ``fail_task`` non-deterministic.
    2. **Unknown bracket tags.** A bracket form at the leading position
       of any task body that does not match a known tag (``[USER]``,
       ``[BATCH]``, ``[AUTO:<word>]``) — per design doc section 4.2
       Notes, "unknown bracket tags are rejected by validation, not
       silently ignored. New tags require a library change." Detection
       is delegated to :func:`_check_leading_bracket_tag`.
    3. **Malformed annotations.** A trailing bracket form that looks
       like an annotation attempt (``[key:value]``) but does not match
       the ``key: value`` shape the parser accepts. Detection is
       delegated to :func:`_check_trailing_annotation`.
    4. **Unknown ``@deps`` references.** Every task id listed in any
       task's ``deps`` must resolve to a known task id in the plan
       (design doc section 8 phase A: "validation requires referenced
       IDs to exist in the plan"). Duplicate ids are still added to
       the known set so dep references resolve, since the duplicate
       diagnostic above already reports the underlying problem.

    Parse-time concerns (syntax, structure of headings) are not
    re-checked here; the parser raises :class:`PlanSyntaxError` for
    those.

    When ``constructed=True`` (per v4 Contract 4), additionally enforces
    the construction-API invariants: ``magic_version == 1``; phase
    ordinals unique and contiguous ``1..N``; ``keyword`` in ``{"Phase",
    "Stage"}``; every phase has ``phase_id`` and ``phase_id_source !=
    "none"``; every task carries a ``T-NNNNNN`` id; no duplicate phase
    ids; no ``trailing_lines`` on any task; and semantic field-stability
    over every task plus the non-task scalars (``project_title``,
    ``preamble``, each ``Phase.title`` / ``Phase.prose``, each
    ``Subsection.title`` / ``Subsection.prose``) per the v4 R3 oracle.
    ``constructed=False`` preserves the task-centric behavior above
    exactly; the Stage 10 task field-stability harness is reused
    for the per-task check rather than duplicated here.
    """
    errors: list[str] = []

    id_lines: dict[str, list[int]] = {}
    for task in _iter_plan_tasks(plan):
        if task.task_id is None:
            continue
        id_lines.setdefault(task.task_id, []).append(task.line_number)

    for task_id, lines in id_lines.items():
        if len(lines) > 1:
            locs = ", ".join(str(n) for n in lines)
            errors.append(f"duplicate task id {task_id} at lines {locs}")

    known_ids: set[str] = set(id_lines.keys())

    for task in _iter_plan_tasks(plan):
        _check_leading_bracket_tag(task, errors)
        _check_trailing_annotation(task, errors)

    for task in _iter_plan_tasks(plan):
        for dep in task.deps:
            if dep not in known_ids:
                errors.append(f"task {_task_ref(task)} references unknown dep {dep}")

    if constructed:
        _check_constructed_invariants(plan, errors)

    if errors:
        raise PlanValidationError(errors)


def _check_constructed_invariants(plan: Plan, errors: list[str]) -> None:
    """Add v4 Contract 4 ``constructed=True`` violations to ``errors``.

    Order matches the contract text so error output is stable across
    runs: magic_version, phase ordinals, per-phase keyword and
    phase_id, duplicate phase ids, per-task id and trailing_lines,
    non-task scalar field-stability oracles (v4 R3), then per-task
    field-stability via the Stage 10 harness.
    """
    if plan.magic_version != 1:
        errors.append(
            f"plan.magic_version must be 1 on constructed plans, "
            f"got {plan.magic_version!r}"
        )

    expected_ordinals = list(range(1, len(plan.phases) + 1))
    actual_ordinals = [phase.ordinal for phase in plan.phases]
    if actual_ordinals != expected_ordinals:
        errors.append(
            f"phase ordinals must be contiguous 1..{len(plan.phases)}, "
            f"got {actual_ordinals}"
        )

    phase_id_positions: dict[str, list[int]] = {}
    for phase_index, phase in enumerate(plan.phases):
        if phase.keyword not in ("Phase", "Stage"):
            errors.append(
                f"{_plan_phase_path(phase_index)}.keyword must be "
                f"'Phase' or 'Stage', got {phase.keyword!r}"
            )
        if phase.phase_id is None or phase.phase_id_source == "none":
            errors.append(
                f"{_plan_phase_path(phase_index)} missing phase_id "
                f"(source {phase.phase_id_source!r})"
            )
        if phase.phase_id is not None:
            phase_id_positions.setdefault(phase.phase_id, []).append(phase_index)

    for phase_id, positions in phase_id_positions.items():
        if len(positions) > 1:
            errors.append(f"duplicate phase_id {phase_id} at phases {positions}")

    for label, task in _iter_plan_tasks_with_label(plan):
        if task.task_id is None:
            errors.append(f"{label}.task_id is missing on constructed task")
        elif _TASK_REF_RE.fullmatch(task.task_id) is None:
            errors.append(
                f"{label}.task_id is malformed on constructed task: {task.task_id!r}"
            )
        if task.trailing_lines:
            errors.append(f"{label}.trailing_lines must be empty on constructed tasks")

    _check_non_task_scalar_field_stability(plan, errors)
    _check_each_task_field_stability(plan, errors)


def _check_non_task_scalar_field_stability(plan: Plan, errors: list[str]) -> None:
    """Run the v4 R3 oracles for each non-task scalar in ``plan``.

    Pre-filters reject embedded ``\\n``/``\\r`` unconditionally — there
    is no multi-line prose exception (v4 R3). Each value is rendered
    inside a minimal canonical plan, the result re-parsed, and the
    parsed scalar required to equal the candidate; inequality surfaces
    a ``...failed to round-trip`` message naming the offending field
    so a rephrase loop can target it.
    """
    if _contains_newline(plan.project_title):
        errors.append("project_title contains an embedded newline")
    else:
        _round_trip_scalar(
            _construction_sentinel_plan(project_title=plan.project_title),
            lambda parsed: parsed.project_title,
            plan.project_title,
            "project_title",
            errors,
        )

    if _contains_newline(plan.preamble):
        errors.append("preamble contains an embedded newline")
    else:
        _round_trip_scalar(
            _construction_sentinel_plan(preamble=plan.preamble),
            lambda parsed: parsed.preamble,
            plan.preamble,
            "preamble",
            errors,
        )

    for phase_index, phase in enumerate(plan.phases):
        title_field = f"{_plan_phase_path(phase_index)}.title"
        if _contains_newline(phase.title):
            errors.append(f"{title_field} contains an embedded newline")
        else:
            _round_trip_scalar(
                _construction_sentinel_plan(
                    phase=_construction_sentinel_phase(title=phase.title)
                ),
                lambda parsed: parsed.phases[0].title,
                phase.title,
                title_field,
                errors,
            )

        prose_field = f"{_plan_phase_path(phase_index)}.prose"
        if _contains_newline(phase.prose):
            errors.append(f"{prose_field} contains an embedded newline")
        else:
            _round_trip_scalar(
                _construction_sentinel_plan(
                    phase=_construction_sentinel_phase(prose=phase.prose)
                ),
                lambda parsed: parsed.phases[0].prose,
                phase.prose,
                prose_field,
                errors,
            )

        for sub_index, sub in enumerate(phase.subsections):
            sub_title_field = f"{_plan_subsection_path(phase_index, sub_index)}.title"
            if _contains_newline(sub.title):
                errors.append(f"{sub_title_field} contains an embedded newline")
            else:
                _round_trip_scalar(
                    _construction_sentinel_plan(
                        phase=_construction_sentinel_phase(
                            tasks=(),
                            subsections=(
                                Subsection(
                                    title=sub.title,
                                    prose="",
                                    tasks=(_construction_sentinel_task(),),
                                    line_number=0,
                                ),
                            ),
                        )
                    ),
                    lambda parsed: parsed.phases[0].subsections[0].title,
                    sub.title,
                    sub_title_field,
                    errors,
                )

            sub_prose_field = f"{_plan_subsection_path(phase_index, sub_index)}.prose"
            if _contains_newline(sub.prose):
                errors.append(f"{sub_prose_field} contains an embedded newline")
            else:
                _round_trip_scalar(
                    _construction_sentinel_plan(
                        phase=_construction_sentinel_phase(
                            tasks=(),
                            subsections=(
                                Subsection(
                                    title="S",
                                    prose=sub.prose,
                                    tasks=(_construction_sentinel_task(),),
                                    line_number=0,
                                ),
                            ),
                        )
                    ),
                    lambda parsed: parsed.phases[0].subsections[0].prose,
                    sub.prose,
                    sub_prose_field,
                    errors,
                )


def _check_each_task_field_stability(plan: Plan, errors: list[str]) -> None:
    """Run the Stage 10 per-task harness for every top-level task in ``plan``.

    The Stage 10 harness (:func:`_assert_task_field_stability`) recurses
    through ``children``, so iterating only the top-level tasks is
    sufficient. Per-task harness failures are re-prefixed with the
    task's plan-location label so the user knows which task in the
    full plan failed to round-trip without losing the per-field
    diagnostic the harness already produced.
    """
    for label, task in _iter_plan_top_level_tasks_with_label(plan):
        try:
            _assert_task_field_stability(task)
        except PlanValidationError as exc:
            for message in exc.messages:
                errors.append(f"{label}: {message}")
