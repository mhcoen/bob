"""Runtime-mutation preflight for legacy PLAN.md files.

Runtime mutation surfaces — mcloop's run loop (behind ``check_off`` /
``fail`` / ``reset`` / checkpoint) and the ``bob`` CLI ``done`` / ``fail``
commands — must operate on whatever PLAN.md a user already has on disk,
including legacy files that predate the constructed-mode canonical form
(no ``<!-- bob-plan-format: 1 -->`` magic line, id-less tasks, gappy
ordinals, parser-side ``trailing_lines``). The save gate
(``validation="canonical"``) is strict: it enforces the constructed
STRUCTURAL invariants. So a runtime surface cannot just mutate-and-save a
legacy plan — the canonical save would reject it.

:func:`preflight_runtime_plan` reconciles the two by running ONCE at the
runtime entry boundary, before any mutation:

  1. Load the plan.
  2. If it is already constructed-structurally valid
     (``validate_plan(constructed=True, require_acceptance=False)``
     passes), return it unchanged.
  3. Else, attempt a pure structural normalization: rebuild every task
     so position metadata and ``trailing_lines`` are dropped, normalize
     phase ordinals to ``1..N`` and the legacy ``explicit_header``
     phase-id source to ``explicit_comment``, promote a missing magic
     version to ``1``, and :func:`migrate` to assign any missing
     ``T-NNNNNN`` ids. If the normalized plan now passes
     ``validate_plan(constructed=True, require_acceptance=False)``, the
     legacy plan was *cleanly migratable*: emit a one-line notice, write
     the migrated form back through the strict canonical save path
     (atomic, lock-held), and return it.
  4. Else the plan is *corrupt* — a violation structural normalization
     cannot resolve (duplicate task ids, malformed deps, a scalar
     instability migration cannot fix). REFUSE: raise
     :class:`PlanPreflightError` carrying the validator's precise
     messages, having mutated nothing on disk. This is exactly the
     out-of-band-corruption class (e.g. the writer duplicate-id
     incident); it should refuse so a human repairs it deliberately
     rather than have ids silently auto-resolved.

Acceptance is NOT part of preflight. Acceptance is an authoring-layer
proof contract; legacy plans with missing ``accept`` annotations must
still be mutatable at runtime during the migration window. Every
validation here passes ``require_acceptance=False``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from bob_tools.planfile.construction import make_task
from bob_tools.planfile.fileio import load, save
from bob_tools.planfile.migration import migrate
from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanValidationError,
    RuledOut,
    Task,
)
from bob_tools.planfile.validation import validate_plan


class PlanPreflightError(Exception):
    """Raised when a legacy PLAN.md is corrupt beyond structural migration.

    Carries the constructed-mode validator's messages (``errors``) so the
    runtime surface can surface a precise diagnostic. The plan on disk is
    left untouched when this is raised.
    """

    def __init__(self, path: Path, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        joined = "; ".join(errors)
        super().__init__(
            f"{path}: cannot be migrated to canonical form; "
            f"the following must be fixed by hand: {joined}"
        )


def _rebuild_task(task: Task) -> Task:
    """Rebuild ``task`` via :func:`make_task` so it is constructed-stable.

    Drops parser-side position metadata and ``trailing_lines`` (the
    constructed validator rejects nonempty ``trailing_lines``) while
    preserving text, status, tags, annotations, deps, ruled-out lines,
    the existing id, and the created/completed timestamps. Children
    rebuild recursively.
    """
    return make_task(
        task.text,
        status=task.status,
        flag_tags=task.flag_tags,
        action_tag=task.action_tag,
        annotations=task.annotations,
        deps=task.deps,
        children=tuple(_rebuild_task(c) for c in task.children),
        ruled_out=tuple(RuledOut(text=r.text, line_number=0) for r in task.ruled_out),
        task_id=task.task_id,
        created_at=task.created_at,
        completed_at=task.completed_at,
    )


def _rebuild_phase(phase: Phase, *, ordinal: int) -> Phase:
    source = phase.phase_id_source
    if source == "explicit_header":
        source = "explicit_comment"
    return dataclasses.replace(
        phase,
        phase_id_source=source,
        ordinal=ordinal,
        tasks=tuple(_rebuild_task(t) for t in phase.tasks),
        subsections=tuple(
            dataclasses.replace(
                s,
                tasks=tuple(_rebuild_task(t) for t in s.tasks),
                line_number=0,
            )
            for s in phase.subsections
        ),
        line_number=0,
    )


def _normalize_to_constructed(plan: Plan) -> Plan:
    """Return ``plan`` structurally normalized toward the constructed form.

    Pure structural normalization only: rebuilds tasks (dropping
    ``trailing_lines`` / positions), renumbers ordinals ``1..N``,
    promotes ``magic_version`` to ``1``, and migrates missing ids. Does
    NOT touch task text, status, or acceptance, and never resolves a
    duplicate id — a still-invalid result after this is genuinely
    corrupt.
    """
    phases = tuple(
        _rebuild_phase(p, ordinal=index + 1) for index, p in enumerate(plan.phases)
    )
    bugs: BugsSection | None = plan.bugs
    if bugs is not None:
        bugs = dataclasses.replace(
            bugs,
            tasks=tuple(_rebuild_task(t) for t in bugs.tasks),
            line_number=0,
        )
    normalized = dataclasses.replace(
        plan,
        phases=phases,
        bugs=bugs,
        magic_version=1,
    )
    return migrate(normalized)


def preflight_runtime_plan(
    path: Path,
    *,
    notice: Callable[[str], None] | None = None,
    label: str | None = None,
) -> Plan:
    """Ensure the PLAN.md at ``path`` is constructed-structurally valid.

    Returns the constructed plan to mutate. A cleanly-migratable legacy
    plan is migrated once and written back through the strict canonical
    save path (a one-line notice is emitted via ``notice`` if supplied).
    A corrupt plan raises :class:`PlanPreflightError` with the
    validator's messages and leaves disk untouched. See the module
    docstring for the full contract.

    ``label`` defaults to ``path``'s display string and is used only in
    the migration notice.
    """
    display = label if label is not None else str(path)
    plan = load(path)

    try:
        validate_plan(plan, constructed=True, require_acceptance=False)
        return plan
    except PlanValidationError:
        pass

    normalized = _normalize_to_constructed(plan)
    try:
        validate_plan(normalized, constructed=True, require_acceptance=False)
    except PlanValidationError as exc:
        errors = (
            exc.args[0] if exc.args and isinstance(exc.args[0], list) else [str(exc)]
        )
        raise PlanPreflightError(path, list(errors)) from exc

    if notice is not None:
        notice(
            f"migrating legacy {display} to canonical form (magic line, ids, ordinals)"
        )
    save(path, normalized, validation="canonical")
    return normalized


__all__ = ["PlanPreflightError", "preflight_runtime_plan"]
