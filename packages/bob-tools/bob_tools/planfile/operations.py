"""Pure operations on typed Plan objects (validate, migrate, mutate, schedule)."""

from __future__ import annotations

import re
from collections.abc import Iterator

from bob_tools.planfile.model import (
    Phase,
    Plan,
    PlanValidationError,
    Task,
    TaskContext,
)

_POSITIONAL_LABEL_RE = re.compile(r"^\d+(?:\.\d+)+$")


def bug_count(plan: Plan) -> int:
    """Return the number of bug tasks in ``plan``.

    Returns ``0`` when ``plan.bugs is None`` (no Bugs section was parsed)
    and the count of top-level bug tasks otherwise. Verification scripts
    use this to surface a concrete count rather than the ambiguous
    ``bugs={p.bugs is not None}`` boolean (where ``bugs=False`` can be
    misread as "the Bugs section exists but is empty" instead of "no
    Bugs section was found"). The count covers root tasks only; nested
    subtask counts are left for callers that need them.
    """
    if plan.bugs is None:
        return 0
    return len(plan.bugs.tasks)


def _iter_tasks(tasks: tuple[Task, ...]) -> Iterator[Task]:
    """Yield every task in ``tasks``, descending into ``children`` first."""
    for task in tasks:
        yield task
        yield from _iter_tasks(task.children)


def _iter_plan_tasks(plan: Plan) -> Iterator[Task]:
    """Yield every Task in the plan: phase tasks, subsection tasks, bugs."""
    for phase in plan.phases:
        yield from _iter_tasks(phase.tasks)
        for subsection in phase.subsections:
            yield from _iter_tasks(subsection.tasks)
    if plan.bugs is not None:
        yield from _iter_tasks(plan.bugs.tasks)


def _find_task_by_id(plan: Plan, task_id: str) -> Task | None:
    """Return the task whose ``task_id`` equals ``task_id``, or ``None``.

    Walks the parsed tree via :func:`_iter_plan_tasks` and compares
    ``task.task_id == task_id``. The library MUST NOT resolve task
    references with substring matching (e.g. ``task_id in line``)
    because ``T-NNNNNN`` IDs prefix-overlap: ``T-000001`` is a substring
    of ``T-0000010``, so a substring search would conflate the two. Per
    design doc section 7.2 caveat. Returns the first match in iteration
    order; a well-formed plan has unique IDs, so the order only matters
    when callers want to surface a duplicate-id diagnostic separately.
    """
    for task in _iter_plan_tasks(plan):
        if task.task_id == task_id:
            return task
    return None


def validate_plan(plan: Plan) -> None:
    """Validate referential integrity of ``@deps`` references in ``plan``.

    Every task ID listed in any task's ``deps`` must resolve to the
    ``task_id`` of some task in the plan. Otherwise raise
    :class:`PlanValidationError` carrying one message per missing
    reference (design doc section 8 phase A: "validation requires
    referenced IDs to exist in the plan").

    Parse-time concerns (syntax, structure) are not re-checked here; the
    parser raises :class:`PlanSyntaxError` for those. Validation only
    cross-checks references between already-parsed objects.
    """
    known_ids: set[str] = {
        task.task_id for task in _iter_plan_tasks(plan) if task.task_id is not None
    }

    errors: list[str] = []
    for task in _iter_plan_tasks(plan):
        for dep in task.deps:
            if dep not in known_ids:
                ref = (
                    task.task_id
                    if task.task_id is not None
                    else (f"line {task.line_number}")
                )
                errors.append(f"task {ref} references unknown dep {dep}")

    if errors:
        raise PlanValidationError(errors)


def _task_matches_label(task: Task, ref: str) -> bool:
    """Return ``True`` when ``ref`` resolves to ``task``.

    Match order (each step decisive; no fall-through ambiguity):

    1. Exact match against ``task.task_id``. This is the canonical
       form and is the only path that survives once :func:`migrate`
       has assigned ids to every task. Per design doc section 7.2
       caveat, the comparison is whole-string equality so the
       ``T-000001`` / ``T-0000010`` prefix overlap cannot conflate
       two distinct tasks.
    2. Exact match against ``task.text``.
    3. Prefix match where ``task.text`` begins with ``ref`` followed
       by a structural separator (``:``, ``)``, or whitespace). Pre-id
       PLAN.md files put a duplo-style label at the start of the task
       text (``task-001: Bring up scaffold``); matching with a required
       trailing separator keeps ``task-001`` from matching
       ``task-0010: ...``.

    Substring matches without a trailing separator are deliberately
    rejected: that was the bug the design doc calls out as the
    motivation for moving resolution off raw-text scanning and onto
    parsed task entries.
    """
    if task.task_id is not None and task.task_id == ref:
        return True
    if task.text == ref:
        return True
    return any(task.text.startswith(ref + sep) for sep in (":", ")", " ", "\t"))


def _resolve_positional_label(plan: Plan, label: str) -> tuple[Phase, int, Task] | None:
    """Resolve a positional label like ``1.3.2`` to ``(phase, doc_index, task)``.

    Mirrors mcloop's ``task_label`` output (``checklist.py``): the first
    dot-separated token is the phase ordinal as printed in the
    ``Stage N`` / ``Phase N`` heading (i.e. ``Phase.ordinal``); each
    subsequent token is a 1-based positional index into the task tree.
    The second token picks a root task from ``phase.tasks``, the third
    picks a child of that task, and so on. Subsection tasks are
    intentionally not addressable by positional label — they sit under
    a sub-heading whose stage number is empty in mcloop, so mcloop never
    produces ``N.M`` labels for them. Mirroring that here keeps the
    resolver consistent with the strings mcloop actually emits.

    ``doc_index`` is the 1-based position of the matched phase within
    ``plan.phases``, surfaced so :func:`resolve_task_context` can
    synthesize an ordinal-fallback phase_id when the matched phase has
    ``phase_id_source == "none"`` (design doc section 7.1). The
    distinction matters: ``Phase.ordinal`` comes from the heading text
    (``Stage 5`` → ``ordinal == 5``), but a plan whose first phase is
    ``Stage 5`` still has that phase at ``doc_index == 1``.

    Returns ``None`` when ``label`` is not in ``N.M[.K...]`` form (the
    pattern is anchored — at least two dot-separated all-numeric
    tokens), when no phase has the requested ordinal, or when any
    positional index along the path is out of range. Bare ``N`` (no
    dots) is intentionally not a positional label: it would be too
    easy to collide with an unrelated single-digit literal in
    ``task.text``, and mcloop's ``task_label`` always emits at least
    a stage-plus-position pair for stage-headed sections.

    Tokenization is explicit: ``label.split(".")`` and integer
    conversion under the anchored regex above. The resolver MUST NOT
    fall back to ``ref in task.text``-style substring scanning — that
    is the bug the design doc section 7.2 caveat calls out and the
    reason this helper exists.
    """
    if _POSITIONAL_LABEL_RE.match(label) is None:
        return None
    parts = [int(token) for token in label.split(".")]
    phase_ordinal, *task_indexes = parts

    matched_phase: Phase | None = None
    matched_doc_index: int = 0
    for doc_index, phase in enumerate(plan.phases, start=1):
        if phase.ordinal == phase_ordinal:
            matched_phase = phase
            matched_doc_index = doc_index
            break
    if matched_phase is None:
        return None

    current_tasks: tuple[Task, ...] = matched_phase.tasks
    matched_task: Task | None = None
    for index in task_indexes:
        if index < 1 or index > len(current_tasks):
            return None
        matched_task = current_tasks[index - 1]
        current_tasks = matched_task.children

    if matched_task is None:
        return None
    return matched_phase, matched_doc_index, matched_task


def _iter_phase_tasks_with_phase(
    plan: Plan,
) -> Iterator[tuple[Task, Phase, int]]:
    """Yield ``(task, phase, doc_index)`` for every task inside a phase.

    ``doc_index`` is the 1-based position of ``phase`` within
    ``plan.phases``; :func:`resolve_task_context` uses it to synthesize
    a ``phase_NNN`` ordinal fallback when the containing phase has no
    explicit id (design doc section 7.1, "Ordinal fallback. The n-th
    phase heading in document order"). The plan's positional addressing
    uses 1-based indexes throughout, so the synthesized id starts at
    ``phase_001``.

    Bug tasks are not yielded here; resolve_task_context handles them
    in a separate pass so the ``phase`` parameter is never ``None``
    inside this iterator. Subsection tasks share their parent phase's
    identity for resolution purposes (design doc section 11 question 5:
    subsections are humans-only grouping; they have no phase_id of
    their own).
    """
    for doc_index, phase in enumerate(plan.phases, start=1):
        for task in _iter_tasks(phase.tasks):
            yield task, phase, doc_index
        for subsection in phase.subsections:
            for task in _iter_tasks(subsection.tasks):
                yield task, phase, doc_index


def _phase_id_for_task_context(phase: Phase, doc_index: int) -> tuple[str | None, str]:
    """Return the ``(phase_id, phase_id_source)`` pair for a TaskContext.

    Pass-through when the phase already has an explicit identifier
    (``"explicit_comment"`` or ``"explicit_header"``). When the
    containing phase has ``phase_id_source == "none"`` the function
    synthesizes the ordinal-derived id ``phase_{doc_index:03d}`` and
    reports ``phase_id_source == "ordinal"``, per design doc section
    7.1 ("Ordinal fallback. The n-th phase heading in document order")
    and section 2.4 (the explicit-required / ordinal-degraded
    contract). The synthesis lets callers proceed with a usable
    phase_id rather than having to thread a separate
    ``ordinal_index`` argument through the resolver, which is exactly
    the simplification that section 7.1's shim sketch is aiming at.
    """
    if phase.phase_id_source == "none":
        return f"phase_{doc_index:03d}", "ordinal"
    return phase.phase_id, phase.phase_id_source


def resolve_task_context(plan: Plan, task_label_or_id: str) -> TaskContext:
    """Resolve a task reference to its containing phase context.

    Replaces ``ledger_emit.find_explicit_phase_id_for_task``. Per design
    doc section 7.1, this is the single resolver for the task → phase
    mapping; ``ledger_emit.resolve_phase_id`` becomes a thin shim that
    promotes the returned :class:`TaskContext` to the ledger's
    ``PhaseIdResolution`` shape and supplies the ordinal-degraded
    fallback when the explicit lookup misses.

    Resolution walks parsed :class:`Task` entries (not raw text) using
    :func:`_task_matches_label`. Phase tasks are searched first, in
    document order, including subsection tasks. Bug-section tasks are
    searched last and return ``phase_id=None`` / ``phase_id_source=
    "none"`` since bugs have no containing phase. Unresolved references
    fall through to the same none-shaped :class:`TaskContext` so
    callers branch on ``phase_id is None`` (or on ``task_id is None``
    when they need to distinguish "matched a bug" from "matched
    nothing"), not on exceptions.

    When the matched task's containing phase has ``phase_id_source ==
    "none"`` (no ``<!-- phase_id: ... -->`` comment and no legacy
    ``## Phase phase_NNN: ...`` header), the resolver synthesizes an
    ordinal-derived ``phase_NNN`` id from the phase's 1-based position
    in ``plan.phases`` and reports ``phase_id_source == "ordinal"``.
    This implements the ordinal-fallback half of the explicit-required
    / ordinal-degraded contract inside the library, per design doc
    sections 2.4 and 7.1, so the ledger_emit shim does not need a
    separate ``ordinal_index`` pass over the plan to recover a usable
    phase_id. Bug-section tasks and unresolved references stay
    ``phase_id_source == "none"`` — they are not contained in a phase,
    so there is no ordinal to attribute to.

    ``plan_phase_count`` is always populated from ``len(plan.phases)``
    so the ordinal-fallback shim has the count it needs without a
    second pass over the plan.

    Positional labels (``N.M[.K...]``, as emitted by mcloop's
    ``task_label``) are tried first and bypass the task-walk
    entirely: tokenized index lookup is unambiguous, so a
    well-formed positional reference cannot be confused with a
    ``task_id`` or with task text — see :func:`_resolve_positional_label`
    for the contract. When the format does not match, when no phase
    has the requested ordinal, or when an index along the path is out
    of range, positional resolution returns ``None`` and the walk
    falls through to ``task_id`` / text matching.
    """
    plan_phase_count = len(plan.phases)

    positional = _resolve_positional_label(plan, task_label_or_id)
    if positional is not None:
        phase, doc_index, task = positional
        phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
        return TaskContext(
            task_id=task.task_id,
            phase_id=phase_id,
            phase_id_source=phase_id_source,
            label=task_label_or_id,
            plan_phase_count=plan_phase_count,
        )

    for task, phase, doc_index in _iter_phase_tasks_with_phase(plan):
        if _task_matches_label(task, task_label_or_id):
            phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
            return TaskContext(
                task_id=task.task_id,
                phase_id=phase_id,
                phase_id_source=phase_id_source,
                label=task_label_or_id,
                plan_phase_count=plan_phase_count,
            )

    if plan.bugs is not None:
        for task in _iter_tasks(plan.bugs.tasks):
            if _task_matches_label(task, task_label_or_id):
                return TaskContext(
                    task_id=task.task_id,
                    phase_id=None,
                    phase_id_source="none",
                    label=task_label_or_id,
                    plan_phase_count=plan_phase_count,
                )

    return TaskContext(
        task_id=None,
        phase_id=None,
        phase_id_source="none",
        label=task_label_or_id,
        plan_phase_count=plan_phase_count,
    )
