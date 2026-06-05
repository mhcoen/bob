"""Acceptance annotations for duplo-authored plans."""

from __future__ import annotations

import pytest
from bob_tools.planfile import (
    Phase,
    Plan,
    Task,
    make_task,
    migrate,
    validate_plan,
)

from duplo.acceptance import AcceptanceAuthoringError, ensure_acceptance_annotations
from duplo.batch_coverage import ensure_batch_test_coverage
from duplo.council import typed_plan_from_synthesizer_text


def _plan_with_tasks(tasks: tuple[Task, ...]) -> Plan:
    return Plan(
        magic_version=1,
        project_title="Demo",
        preamble="",
        phases=(
            Phase(
                phase_id="phase_001",
                phase_id_source="explicit_comment",
                ordinal=1,
                keyword="Phase",
                title="Core",
                prose="",
                subsections=(),
                tasks=tasks,
                line_number=0,
            ),
        ),
        bugs=None,
        source_path=None,
    )


def _authored(plan: Plan) -> Plan:
    return ensure_acceptance_annotations(migrate(ensure_batch_test_coverage(plan)))


def _accept_value(task: Task) -> str | None:
    for key, value in task.annotations:
        if key == "accept":
            return value
    return None


def test_synthesized_plan_validates_constructed_with_accept_annotations() -> None:
    # The real synthesizer emits a markdown plan body with phase headers
    # and id-less ``- [ ]`` checklist lines and no magic-version line;
    # typed_plan_from_synthesizer_text assigns ids via migrate itself.
    body = (
        "# Demo\n"
        "\n"
        "## Phase phase_001: Core\n"
        "\n"
        "- [ ] [BATCH] Build widget module\n"
        "  - [ ] Create duplo/widget.py with render_widget()\n"
    )
    plan = typed_plan_from_synthesizer_text(body, required_phase_id="phase_001")

    validate_plan(plan, constructed=True)
    batch = plan.phases[0].tasks[0]
    assert _accept_value(batch.children[0]) == "pytest"
    assert _accept_value(batch.children[1]) == "pytest"


def test_pytest_acceptance_and_covering_test_are_same_batch_siblings() -> None:
    plan = _authored(
        _plan_with_tasks(
            (
                make_task(
                    "Build parser module",
                    flag_tags=("BATCH",),
                    children=(make_task("Create duplo/parser.py with parse_input()"),),
                ),
            )
        )
    )

    batch = plan.phases[0].tasks[0]
    implementation, covering_test = batch.children
    assert _accept_value(implementation) == "pytest"
    assert _accept_value(covering_test) == "pytest"
    assert "tests/test_parser.py" in covering_test.text


def test_unprovable_leaf_implementation_is_refused() -> None:
    plan = migrate(_plan_with_tasks((make_task("Design dashboard interaction"),)))

    with pytest.raises(
        AcceptanceAuthoringError,
        match="cannot derive accept annotation",
    ):
        ensure_acceptance_annotations(plan)
