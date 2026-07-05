"""Pin the strict ``canonical`` save gate contract (storage-integrity).

``save(validation="canonical")`` enforces the constructed-mode STRUCTURAL
invariants (magic_version, contiguous ordinals, no duplicate ids, task id
presence, no trailing_lines, scalar field-stability including the
embedded-newline preamble check) and the mcloop canonical-input contract,
but NOT declared-acceptance completeness — acceptance is an authoring-layer
contract, deferred at the save gate during the legacy-migration window.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from bob_tools.planfile import (
    Phase,
    Plan,
    PlanValidationError,
    Task,
    load,
    make_task,
    save,
)


def _phase(phase_id: str, ordinal: int, title: str, task: Task) -> Phase:
    return Phase(
        phase_id=phase_id,
        phase_id_source="explicit_comment",
        ordinal=ordinal,
        keyword="Stage",
        title=title,
        prose="",
        subsections=(),
        tasks=(task,),
        line_number=0,
    )


def _good_plan() -> Plan:
    """A fully constructed + acceptance-complete plan.

    Built via the typed model (not parsed) so leaf tasks carry empty
    ``trailing_lines`` — the baseline that must always save cleanly and
    the base we mutate to inject exactly one violation per test.
    """
    return Plan(
        magic_version=1,
        project_title="Save-gate fixture",
        preamble="",
        phases=(
            _phase(
                "phase_001",
                1,
                "One",
                make_task(
                    "first task",
                    task_id="T-000001",
                    annotations=(("accept", "pytest"),),
                ),
            ),
            _phase(
                "phase_002",
                2,
                "Two",
                make_task(
                    "second task",
                    task_id="T-000002",
                    annotations=(("accept", "command-exit: true"),),
                ),
            ),
        ),
        bugs=None,
        source_path=None,
    )


def _first_task(plan: Plan) -> Task:
    return plan.phases[0].tasks[0]


def _replace_first_task(plan: Plan, task: Task) -> Plan:
    phase0 = plan.phases[0]
    new_phase = dataclasses.replace(phase0, tasks=(task, *phase0.tasks[1:]))
    return dataclasses.replace(plan, phases=(new_phase, *plan.phases[1:]))


def test_fully_canonical_acceptance_complete_plan_saves(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    save(path, _good_plan())
    reloaded = load(path)
    assert reloaded.magic_version == 1
    assert reloaded.phases[0].tasks[0].task_id == "T-000001"


def test_canonical_rejects_wrong_magic_version(tmp_path: Path) -> None:
    plan = dataclasses.replace(_good_plan(), magic_version=2)
    with pytest.raises(PlanValidationError):
        save(tmp_path / "PLAN.md", plan)


def test_canonical_rejects_non_contiguous_ordinals(tmp_path: Path) -> None:
    plan = _good_plan()
    bad_second = dataclasses.replace(plan.phases[1], ordinal=3)
    plan = dataclasses.replace(plan, phases=(plan.phases[0], bad_second))
    with pytest.raises(PlanValidationError):
        save(tmp_path / "PLAN.md", plan)


def test_canonical_rejects_embedded_newline_preamble(tmp_path: Path) -> None:
    plan = dataclasses.replace(_good_plan(), preamble="line one\nline two")
    with pytest.raises(PlanValidationError):
        save(tmp_path / "PLAN.md", plan)


def test_canonical_preserves_trailing_lines(tmp_path: Path) -> None:
    # Trailing lines are lossless parsed-from-disk content; the canonical
    # gate passes them through and the renderer writes them verbatim.
    # (The old rejection made runtime saves destroy content fmt keeps.)
    plan = _good_plan()
    task = dataclasses.replace(_first_task(plan), trailing_lines=("  stray line",))
    save(tmp_path / "PLAN.md", _replace_first_task(plan, task))
    assert "  stray line" in (tmp_path / "PLAN.md").read_text()


def test_canonical_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    plan = _good_plan()
    collide = dataclasses.replace(_first_task(plan), task_id="T-000002")
    with pytest.raises(PlanValidationError):
        save(tmp_path / "PLAN.md", _replace_first_task(plan, collide))


def test_canonical_accepts_structurally_canonical_plan_missing_acceptance(
    tmp_path: Path,
) -> None:
    """The legacy-repair case: structurally canonical, but a leaf task has
    NO accept annotation. The save gate ACCEPTS it (acceptance deferred)."""
    plan = Plan(
        magic_version=1,
        project_title="No-acceptance fixture",
        preamble="",
        phases=(
            Phase(
                phase_id="phase_001",
                phase_id_source="explicit_comment",
                ordinal=1,
                keyword="Stage",
                title="One",
                prose="",
                subsections=(),
                tasks=(
                    make_task(
                        "do work without declared acceptance", task_id="T-000001"
                    ),
                ),
                line_number=0,
            ),
        ),
        bugs=None,
        source_path=None,
    )
    path = tmp_path / "PLAN.md"
    save(path, plan)  # must not raise
    reloaded = load(path)
    assert reloaded.phases[0].tasks[0].task_id == "T-000001"
    assert all(key != "accept" for key, _ in reloaded.phases[0].tasks[0].annotations)
