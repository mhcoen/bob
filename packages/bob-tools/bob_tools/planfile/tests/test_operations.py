"""Tests for bob_tools.planfile.operations.

Covers :func:`validate_plan` referential-integrity checks for ``@deps``:

  - Plans with no tasks and plans whose ``@deps`` all resolve return
    ``None`` (no error).
  - Unknown dep references — at the root, on nested children, in a
    subsection task, or on a bug task — raise
    :class:`PlanValidationError` with one message per missing reference.
  - Tasks without a ``task_id`` (compat mode) still have their ``deps``
    checked; the error message identifies the offender by source line.
  - Cross-section references resolve: a phase task may depend on a bug
    task or vice versa, and a task may depend on another task's child.
  - Validation reports every error in a single raise rather than
    short-circuiting on the first failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanValidationError,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile.operations import validate_plan


def _task(
    *,
    task_id: str | None,
    deps: tuple[str, ...] = (),
    children: tuple[Task, ...] = (),
    indent_level: int = 0,
    line_number: int = 1,
    text: str = "do thing",
) -> Task:
    return Task(
        task_id=task_id,
        text=text,
        status=TaskStatus.TODO,
        flag_tags=(),
        action_tag=None,
        annotations=(),
        deps=deps,
        children=children,
        ruled_out=(),
        indent_level=indent_level,
        line_number=line_number,
    )


def _phase(tasks: tuple[Task, ...], subsections: tuple[Subsection, ...] = ()) -> Phase:
    return Phase(
        phase_id="phase_001",
        phase_id_source="explicit_comment",
        ordinal=1,
        keyword="Stage",
        title="Core",
        prose="",
        subsections=subsections,
        tasks=tasks,
        line_number=5,
    )


def _plan(
    phases: tuple[Phase, ...] = (),
    bugs: BugsSection | None = None,
) -> Plan:
    return Plan(
        magic_version=1,
        project_title="Project",
        preamble="",
        phases=phases,
        bugs=bugs,
        source_path=Path("/tmp/PLAN.md"),
    )


class TestValidatePlan:
    def test_empty_plan_is_valid(self) -> None:
        validate_plan(_plan())

    def test_plan_with_no_deps_is_valid(self) -> None:
        plan = _plan(
            phases=(
                _phase(tasks=(_task(task_id="T-000001"), _task(task_id="T-000002"))),
            )
        )
        validate_plan(plan)

    def test_dep_resolves_to_sibling(self) -> None:
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001"),
                        _task(task_id="T-000002", deps=("T-000001",)),
                    )
                ),
            )
        )
        validate_plan(plan)

    def test_dep_resolves_to_nested_child(self) -> None:
        # Deps may point at a deeply nested task; _iter_plan_tasks must
        # walk children so child IDs end up in the known set.
        child = _task(task_id="T-000002", indent_level=2)
        parent = _task(task_id="T-000001", children=(child,))
        other = _task(task_id="T-000003", deps=("T-000002",))
        plan = _plan(phases=(_phase(tasks=(parent, other)),))
        validate_plan(plan)

    def test_unknown_dep_raises(self) -> None:
        plan = _plan(
            phases=(_phase(tasks=(_task(task_id="T-000001", deps=("T-000999",)),)),)
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000001 references unknown dep T-000999",
        ]

    def test_reports_every_error_not_just_first(self) -> None:
        # validate_plan should not short-circuit; every missing
        # reference appears in `messages` so a single run surfaces all
        # of them to the user.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001", deps=("T-000998",)),
                        _task(task_id="T-000002", deps=("T-000999",)),
                    )
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000001 references unknown dep T-000998",
            "task T-000002 references unknown dep T-000999",
        ]

    def test_multiple_deps_on_one_task_each_checked(self) -> None:
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001"),
                        _task(
                            task_id="T-000002",
                            deps=("T-000001", "T-000888", "T-000999"),
                        ),
                    )
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000002 references unknown dep T-000888",
            "task T-000002 references unknown dep T-000999",
        ]

    def test_dep_on_nested_child_unknown_raises(self) -> None:
        # An unknown dep on a deeply nested child must surface; iteration
        # has to recurse for deps checking, not just for ID collection.
        bad_child = _task(
            task_id="T-000002",
            deps=("T-000999",),
            indent_level=2,
            line_number=11,
        )
        parent = _task(task_id="T-000001", children=(bad_child,))
        plan = _plan(phases=(_phase(tasks=(parent,)),))
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000002 references unknown dep T-000999",
        ]

    def test_dep_can_reference_bugs_task(self) -> None:
        # Bug tasks are part of the plan; their IDs are valid dep
        # targets for phase tasks (and vice versa).
        bug = _task(task_id="T-000900", line_number=100)
        plan = _plan(
            phases=(_phase(tasks=(_task(task_id="T-000001", deps=("T-000900",)),)),),
            bugs=BugsSection(tasks=(bug,), line_number=99),
        )
        validate_plan(plan)

    def test_bug_task_with_unknown_dep_raises(self) -> None:
        plan = _plan(
            bugs=BugsSection(
                tasks=(_task(task_id="T-000900", deps=("T-000999",), line_number=100),),
                line_number=99,
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000900 references unknown dep T-000999",
        ]

    def test_subsection_task_ids_known_and_checked(self) -> None:
        # Subsection tasks are visible both as ID sources (a phase task
        # may depend on one) and as dep-bearers (an unknown ref on a
        # subsection task must raise).
        sub = Subsection(
            title="Manual verification",
            prose="",
            tasks=(
                _task(task_id="T-000010", line_number=20),
                _task(task_id="T-000011", deps=("T-000999",), line_number=21),
            ),
            line_number=19,
        )
        phase = _phase(
            tasks=(_task(task_id="T-000001", deps=("T-000010",)),),
            subsections=(sub,),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(_plan(phases=(phase,)))
        assert exc_info.value.messages == [
            "task T-000011 references unknown dep T-000999",
        ]

    def test_task_without_id_still_has_deps_checked(self) -> None:
        # Compat-mode tasks have no ID but may still have @deps. The
        # error message falls back to the source line number so the
        # user can locate the offending line.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(_task(task_id=None, deps=("T-000999",), line_number=42),)
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task line 42 references unknown dep T-000999",
        ]
