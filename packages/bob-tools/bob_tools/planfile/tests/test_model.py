"""Tests for the planfile typed model and exception types.

Covers:

  - ``TaskStatus`` enum membership and the checkbox-marker mapping
    (space/x/X/!) per design doc section 2.1.
  - Dataclass construction for ``RuledOut``, ``Task``, ``Subsection``,
    ``Phase``, ``BugsSection``, and ``Plan`` with the field layout
    specified in stage 1.2.
  - Frozen-dataclass behavior: every model dataclass rejects attribute
    mutation by raising ``dataclasses.FrozenInstanceError``.
  - ``PlanSyntaxError.__str__`` formatting matches design doc section 9
    ("PLAN.md invalid at line N, column M: ...").
  - ``PlanValidationError`` and ``PlanInconsistencyError`` carry their
    messages list and survive round-tripping through ``str``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from bob_tools.planfile.model import (
    CHECKBOX_MARKER_TO_STATUS,
    BugsSection,
    Phase,
    Plan,
    PlanInconsistencyError,
    PlanSyntaxError,
    PlanValidationError,
    RuledOut,
    Subsection,
    Task,
    TaskStatus,
)


def _make_task(**overrides: object) -> Task:
    """Helper: build a Task with sensible defaults for tests."""
    base: dict[str, object] = {
        "task_id": "T-000001",
        "text": "Do the thing",
        "status": TaskStatus.TODO,
        "flag_tags": (),
        "action_tag": None,
        "annotations": (),
        "deps": (),
        "children": (),
        "ruled_out": (),
        "indent_level": 0,
        "line_number": 10,
    }
    base.update(overrides)
    return Task(**base)  # type: ignore[arg-type]


class TestTaskStatus:
    def test_three_members(self) -> None:
        assert {s.name for s in TaskStatus} == {"TODO", "DONE", "FAILED"}

    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            (" ", TaskStatus.TODO),
            ("x", TaskStatus.DONE),
            ("X", TaskStatus.DONE),
            ("!", TaskStatus.FAILED),
        ],
    )
    def test_from_marker(self, marker: str, expected: TaskStatus) -> None:
        assert TaskStatus.from_marker(marker) == expected

    def test_marker_table_matches_from_marker(self) -> None:
        for marker, status in CHECKBOX_MARKER_TO_STATUS.items():
            assert TaskStatus.from_marker(marker) == status

    def test_from_marker_unknown(self) -> None:
        with pytest.raises(ValueError, match="unknown checkbox marker"):
            TaskStatus.from_marker("?")


class TestRuledOut:
    def test_construction(self) -> None:
        ro = RuledOut(text="Tried X; failed because Y.", line_number=42)
        assert ro.text == "Tried X; failed because Y."
        assert ro.line_number == 42

    def test_frozen(self) -> None:
        ro = RuledOut(text="x", line_number=1)
        with pytest.raises(FrozenInstanceError):
            ro.text = "mutated"  # type: ignore[misc]


class TestTask:
    def test_default_construction(self) -> None:
        task = _make_task()
        assert task.task_id == "T-000001"
        assert task.status is TaskStatus.TODO
        assert task.flag_tags == ()
        assert task.action_tag is None
        assert task.children == ()

    def test_task_id_can_be_none_for_compat(self) -> None:
        # design doc section 7.2: IDs are optional in compatibility mode.
        task = _make_task(task_id=None)
        assert task.task_id is None

    def test_flag_tags_bare_names(self) -> None:
        # design doc section 4.3: flag tag members are bare names; the
        # brackets live only in serialized form.
        task = _make_task(flag_tags=("USER", "BATCH"))
        assert task.flag_tags == ("USER", "BATCH")

    def test_action_tag_pair(self) -> None:
        task = _make_task(action_tag=("run_cli", "mcloop --dry-run"))
        assert task.action_tag == ("run_cli", "mcloop --dry-run")

    def test_annotations_and_deps(self) -> None:
        task = _make_task(
            annotations=(("feat", "menu wired"), ("fix", "race condition")),
            deps=("T-000002", "T-000003"),
        )
        assert task.annotations[0] == ("feat", "menu wired")
        assert task.deps == ("T-000002", "T-000003")

    def test_children_and_ruled_out(self) -> None:
        child = _make_task(task_id="T-000002", indent_level=1, line_number=11)
        ruled = RuledOut(text="don't do Y", line_number=12)
        task = _make_task(children=(child,), ruled_out=(ruled,))
        assert task.children == (child,)
        assert task.ruled_out == (ruled,)

    def test_frozen(self) -> None:
        task = _make_task()
        with pytest.raises(FrozenInstanceError):
            task.text = "mutated"  # type: ignore[misc]


class TestSubsection:
    def test_construction(self) -> None:
        task = _make_task()
        sub = Subsection(
            title="Manual verification",
            prose="",
            tasks=(task,),
            line_number=20,
        )
        assert sub.title == "Manual verification"
        assert sub.tasks == (task,)

    def test_frozen(self) -> None:
        sub = Subsection(title="x", prose="", tasks=(), line_number=1)
        with pytest.raises(FrozenInstanceError):
            sub.title = "mutated"  # type: ignore[misc]


class TestPhase:
    def test_construction(self) -> None:
        task = _make_task()
        phase = Phase(
            phase_id="phase_001",
            phase_id_source="explicit_comment",
            ordinal=1,
            keyword="Stage",
            title="Core",
            prose="",
            subsections=(),
            tasks=(task,),
            line_number=5,
        )
        assert phase.phase_id == "phase_001"
        assert phase.phase_id_source == "explicit_comment"
        assert phase.keyword == "Stage"
        assert phase.tasks == (task,)

    def test_phase_id_can_be_none_with_none_source(self) -> None:
        phase = Phase(
            phase_id=None,
            phase_id_source="none",
            ordinal=2,
            keyword="Phase",
            title="Bare",
            prose="",
            subsections=(),
            tasks=(),
            line_number=30,
        )
        assert phase.phase_id is None
        assert phase.phase_id_source == "none"

    def test_frozen(self) -> None:
        phase = Phase(
            phase_id="phase_001",
            phase_id_source="explicit_comment",
            ordinal=1,
            keyword="Stage",
            title="Core",
            prose="",
            subsections=(),
            tasks=(),
            line_number=5,
        )
        with pytest.raises(FrozenInstanceError):
            phase.title = "mutated"  # type: ignore[misc]


class TestBugsSection:
    def test_construction(self) -> None:
        task = _make_task()
        bugs = BugsSection(tasks=(task,), line_number=100)
        assert bugs.tasks == (task,)
        assert bugs.line_number == 100

    def test_frozen(self) -> None:
        bugs = BugsSection(tasks=(), line_number=1)
        with pytest.raises(FrozenInstanceError):
            bugs.line_number = 2  # type: ignore[misc]


class TestPlan:
    def test_construction(self) -> None:
        phase = Phase(
            phase_id="phase_001",
            phase_id_source="explicit_comment",
            ordinal=1,
            keyword="Stage",
            title="Core",
            prose="",
            subsections=(),
            tasks=(),
            line_number=5,
        )
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(phase,),
            bugs=None,
            source_path=Path("/tmp/PLAN.md"),
        )
        assert plan.magic_version == 1
        assert plan.phases == (phase,)
        assert plan.bugs is None
        assert plan.source_path == Path("/tmp/PLAN.md")

    def test_optional_fields(self) -> None:
        plan = Plan(
            magic_version=None,
            project_title="Project",
            preamble="",
            phases=(),
            bugs=None,
            source_path=None,
        )
        assert plan.magic_version is None
        assert plan.source_path is None

    def test_frozen(self) -> None:
        plan = Plan(
            magic_version=None,
            project_title="x",
            preamble="",
            phases=(),
            bugs=None,
            source_path=None,
        )
        with pytest.raises(FrozenInstanceError):
            plan.project_title = "mutated"  # type: ignore[misc]


class TestPlanSyntaxError:
    def test_attributes(self) -> None:
        err = PlanSyntaxError("expected task id", 17, 5, Path("/tmp/PLAN.md"))
        assert err.message == "expected task id"
        assert err.line == 17
        assert err.column == 5
        assert err.path == Path("/tmp/PLAN.md")

    def test_str_matches_design_doc_format(self) -> None:
        # design doc section 9: "PLAN.md invalid at line N, column M: ..."
        err = PlanSyntaxError(
            "expected task id like T-000123 after checkbox marker",
            17,
            5,
            Path("/tmp/PLAN.md"),
        )
        assert str(err) == (
            "PLAN.md invalid at line 17, column 5: "
            "expected task id like T-000123 after checkbox marker"
        )

    def test_path_optional(self) -> None:
        err = PlanSyntaxError("bad", 1, 1)
        assert err.path is None
        assert str(err) == "PLAN.md invalid at line 1, column 1: bad"


class TestPlanValidationError:
    def test_messages_preserved(self) -> None:
        err = PlanValidationError(["dep T-000999 not found", "duplicate id T-000001"])
        assert err.messages == [
            "dep T-000999 not found",
            "duplicate id T-000001",
        ]

    def test_str_joins_messages(self) -> None:
        err = PlanValidationError(["a", "b"])
        assert "a" in str(err)
        assert "b" in str(err)

    def test_empty_messages(self) -> None:
        err = PlanValidationError([])
        assert err.messages == []
        assert str(err) == ""


class TestPlanInconsistencyError:
    def test_messages_preserved(self) -> None:
        err = PlanInconsistencyError(["task T-000001 [x] but no commit_landed event"])
        assert err.messages == ["task T-000001 [x] but no commit_landed event"]

    def test_str_joins_messages(self) -> None:
        err = PlanInconsistencyError(["x", "y"])
        assert "x" in str(err)
        assert "y" in str(err)
