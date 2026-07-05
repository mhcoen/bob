"""Tests for bob_tools.planfile.operations.

Covers :func:`validate_plan` referential-integrity checks for ``@deps``,
:func:`check_consistency` reconciliation of PLAN.md against ledger
events, and the other operation surfaces:

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

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bob_tools.planfile._shared import _ACCEPT_KINDS
from bob_tools.planfile.model import (
    BugsSection,
    Outcome,
    Phase,
    Plan,
    PlanInconsistencyError,
    PlanValidationError,
    RuledOut,
    Settlement,
    Subsection,
    Task,
    TaskContext,
    TaskStatus,
)
from bob_tools.planfile.operations import (
    _find_task_by_id,
    add_bug_task,
    add_phase_task,
    add_task,
    assert_mcloop_canonical,
    bug_count,
    check_consistency,
    clear_failed,
    complete_task,
    fail_task,
    make_task,
    next_tasks,
    purge_done_bug_tasks,
    replace_phase,
    replace_phase_validated,
    reset_task,
    resolve_task_context,
    validate_plan,
)
from bob_tools.planfile.renderer import render_plan
from bob_tools.planfile.validation import (
    AcceptKind,
    AcceptParseError,
    accept_annotation,
    parse_accept_value,
)


def _task(
    *,
    task_id: str | None,
    deps: tuple[str, ...] = (),
    children: tuple[Task, ...] = (),
    indent_level: int = 0,
    line_number: int = 1,
    text: str = "do thing",
    status: TaskStatus = TaskStatus.TODO,
    flag_tags: tuple[str, ...] = (),
    action_tag: tuple[str, str] | None = None,
    annotations: tuple[tuple[str, str], ...] = (("accept", "pytest"),),
) -> Task:
    return Task(
        task_id=task_id,
        text=text,
        status=status,
        flag_tags=flag_tags,
        action_tag=action_tag,
        annotations=annotations,
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
        source_path=Path("packages/bob-tools/.scratch/PLAN.md"),
    )


def test_package_root_exports_clear_failed_and_validate_plan() -> None:
    from bob_tools.planfile import clear_failed as exported_clear_failed
    from bob_tools.planfile import purge_done_bug_tasks as exported_purge_done_bug_tasks
    from bob_tools.planfile import validate_plan as exported_validate_plan

    assert exported_clear_failed is clear_failed
    assert exported_purge_done_bug_tasks is purge_done_bug_tasks
    assert exported_validate_plan is validate_plan


class TestAcceptAnnotationVocabulary:
    def test_shared_accept_kinds_are_closed_vocabulary(self) -> None:
        assert _ACCEPT_KINDS == frozenset(
            {"pytest", "command-exit", "coverage", "waived"}
        )

    def test_parse_accept_value_classifies_valid_values(self) -> None:
        assert parse_accept_value("pytest") == AcceptKind(kind="pytest")
        assert parse_accept_value("coverage") == AcceptKind(kind="coverage")
        assert parse_accept_value("command-exit: ./run.sh --help") == AcceptKind(
            kind="command-exit",
            command="./run.sh --help",
        )
        assert parse_accept_value(
            "waived: proven by parser round-trip; covered-by=T-AB-000123"
        ) == AcceptKind(
            kind="waived",
            reason="proven by parser round-trip",
            covered_by="T-AB-000123",
        )

    def test_parse_accept_value_returns_structured_errors(self) -> None:
        assert parse_accept_value("manual") == AcceptParseError(
            code="unknown_kind",
            message="unknown accept kind",
        )
        assert parse_accept_value("waived: external proof") == AcceptParseError(
            code="malformed_waived",
            message="waived accept has no covered-by= or malformed covered-by",
        )

    def test_accept_annotation_detects_ambiguity(self) -> None:
        task = _task(
            task_id="T-000001",
            annotations=(("accept", "pytest"), ("accept", "coverage")),
        )
        assert accept_annotation(task) == (None, "more than one accept annotation")


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

    def test_duplicate_task_id_raises(self) -> None:
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001", line_number=10),
                        _task(task_id="T-000001", line_number=12),
                    )
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "duplicate task id T-000001 at lines 10, 12",
        ]

    def test_duplicate_task_id_across_sections(self) -> None:
        # Duplicate detection walks every section — a phase task can
        # collide with a bug task or a subsection task. Lines list
        # the every occurrence in document order so the user can
        # locate both copies.
        sub = Subsection(
            title="Manual",
            prose="",
            tasks=(_task(task_id="T-000001", line_number=30),),
            line_number=29,
        )
        phase = _phase(
            tasks=(_task(task_id="T-000001", line_number=11),),
            subsections=(sub,),
        )
        plan = _plan(
            phases=(phase,),
            bugs=BugsSection(
                tasks=(_task(task_id="T-000001", line_number=99),),
                line_number=98,
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "duplicate task id T-000001 at lines 11, 30, 99",
        ]

    def test_dep_to_duplicate_id_still_resolves(self) -> None:
        # A duplicate id is its own error, but other tasks that depend
        # on it should not also be reported as referencing an unknown
        # id — the underlying problem is the duplicate, not the
        # reference. Reporting both would multiply error count and
        # confuse the fix-it experience.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001", line_number=10),
                        _task(task_id="T-000001", line_number=11),
                        _task(task_id="T-000002", deps=("T-000001",), line_number=12),
                    )
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "duplicate task id T-000001 at lines 10, 11",
        ]

    def test_unknown_leading_bracket_tag_raises(self) -> None:
        # A bracket form at the leading position whose content has
        # tag-shape (uppercase identifier) but is not a known tag is
        # rejected per design doc section 4.2 Notes.
        plan = _plan(
            phases=(_phase(tasks=(_task(task_id="T-000001", text="[FOO] do thing"),)),)
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000001 has unknown bracket tag [FOO]",
        ]

    def test_unknown_leading_action_tag_raises(self) -> None:
        # Same rule for action-tag-shaped brackets with an unknown
        # prefix (the prefix is what dispatches the runner, so a
        # made-up prefix would silently no-op without validation).
        plan = _plan(
            phases=(
                _phase(tasks=(_task(task_id="T-000001", text="[NEW:thing] do it"),)),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000001 has unknown bracket tag [NEW:thing]",
        ]

    def test_leading_ruledout_is_prose_not_unknown_tag(self) -> None:
        # ``[RULEDOUT]`` is reserved by the grammar for the sibling-line
        # construct (design doc section 4.3, planfile.md:415-417). A
        # task whose title legitimately documents the RULEDOUT feature
        # (e.g. mcloop/PLAN.EXAMPLE.md:243 "[RULEDOUT] tag for
        # recording failed approaches in PLAN.md") therefore has the
        # literal token at the leading position of ``task.text``; that
        # is prose, not an attempted unknown task tag. Pre-fix the
        # validator emitted ``unknown bracket tag [RULEDOUT]`` here,
        # breaking the fmt -> validate fixed-point invariant on every
        # plan that mentions the feature by name.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(
                            task_id="T-000193",
                            text="[RULEDOUT] tag for recording failed approaches",
                        ),
                    )
                ),
            )
        )
        validate_plan(plan)

    def test_known_tags_in_prose_not_flagged(self) -> None:
        # Lowercase/multi-word brackets are prose, not tag attempts —
        # ``_LEADING_TAG_LIKE_RE`` requires an all-caps identifier of
        # two or more chars, so legitimate prose with brackets passes.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001", text="[note] write design doc"),
                        _task(
                            task_id="T-000002",
                            text="[x] checkmark literal in text",
                            line_number=2,
                        ),
                    )
                ),
            )
        )
        validate_plan(plan)

    def test_malformed_annotation_raises(self) -> None:
        # Trailing ``[key:value]`` with no whitespace after the colon
        # is the canonical malformed-annotation case; the parser
        # leaves it in text because ``_ANNOTATION_CONTENT_RE`` rejects
        # it, and validation surfaces the broken form.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(_task(task_id="T-000001", text="do thing [feat:nospace]"),)
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "task T-000001 has malformed annotation [feat:nospace]",
        ]

    def test_trailing_prose_brackets_not_flagged(self) -> None:
        # A bracketed tail with no colon is prose, not an annotation
        # attempt — flagging ``[some text]`` would produce false
        # positives. The colon is the malformed-annotation signal.
        plan = _plan(
            phases=(
                _phase(tasks=(_task(task_id="T-000001", text="see references [1]"),)),
            )
        )
        validate_plan(plan)

    def test_all_error_kinds_reported_together(self) -> None:
        # validate_plan does not short-circuit across error kinds: a
        # plan with a duplicate id, an unknown tag, a malformed
        # annotation, and an unknown dep produces one message per
        # problem in the same raise.
        plan = _plan(
            phases=(
                _phase(
                    tasks=(
                        _task(task_id="T-000001", line_number=10),
                        _task(task_id="T-000001", line_number=11),
                        _task(
                            task_id="T-000002",
                            text="[FOO] do thing",
                            line_number=12,
                        ),
                        _task(
                            task_id="T-000003",
                            text="do other thing [feat:nospace]",
                            line_number=13,
                        ),
                        _task(
                            task_id="T-000004",
                            deps=("T-000999",),
                            line_number=14,
                        ),
                    )
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan)
        assert exc_info.value.messages == [
            "duplicate task id T-000001 at lines 10, 11",
            "task T-000002 has unknown bracket tag [FOO]",
            "task T-000003 has malformed annotation [feat:nospace]",
            "task T-000004 references unknown dep T-000999",
        ]


class TestValidatePlanConstructed:
    """Tests for ``validate_plan(plan, constructed=True)`` per v4 Contract 4.

    ``constructed=False`` is the default and preserves today's
    task-centric checks (covered by :class:`TestValidatePlan`); the
    ``constructed=True`` mode adds the construction-API invariants and
    semantic field-stability per the v4 R3 oracle. Each test pins one
    failing condition and one matching error message so a regression in
    any single check surfaces independently.
    """

    def _ctask(
        self,
        *,
        task_id: str | None = "T-000001",
        text: str = "do thing",
        trailing_lines: tuple[str, ...] = (),
        children: tuple[Task, ...] = (),
        deps: tuple[str, ...] = (),
        flag_tags: tuple[str, ...] = (),
        action_tag: tuple[str, str] | None = None,
        annotations: tuple[tuple[str, str], ...] = (("accept", "pytest"),),
    ) -> Task:
        return Task(
            task_id=task_id,
            text=text,
            status=TaskStatus.TODO,
            flag_tags=flag_tags,
            action_tag=action_tag,
            annotations=annotations,
            deps=deps,
            children=children,
            ruled_out=(),
            indent_level=0,
            line_number=0,
            trailing_lines=trailing_lines,
        )

    def _cphase(
        self,
        *,
        tasks: tuple[Task, ...] = (),
        subsections: tuple[Subsection, ...] = (),
        phase_id: str | None = "phase_001",
        phase_id_source: str = "explicit_comment",
        ordinal: int = 1,
        keyword: str = "Phase",
        title: str = "Stage",
        prose: str = "",
    ) -> Phase:
        return Phase(
            phase_id=phase_id,
            phase_id_source=phase_id_source,
            ordinal=ordinal,
            keyword=keyword,
            title=title,
            prose=prose,
            subsections=subsections,
            tasks=tasks,
            line_number=0,
        )

    def _cplan(
        self,
        *,
        magic_version: int | None = 1,
        project_title: str = "Project",
        preamble: str = "",
        phases: tuple[Phase, ...] | None = None,
        bugs: BugsSection | None = None,
    ) -> Plan:
        if phases is None:
            phases = (self._cphase(tasks=(self._ctask(),)),)
        return Plan(
            magic_version=magic_version,
            project_title=project_title,
            preamble=preamble,
            phases=phases,
            bugs=bugs,
            source_path=None,
        )

    def test_minimal_constructed_plan_validates(self) -> None:
        validate_plan(self._cplan(), constructed=True)

    def test_accept_annotations_round_trip_without_field_drift(self) -> None:
        from bob_tools.planfile.parser import parse_plan

        tasks = (
            self._ctask(
                task_id="T-000001",
                text="scoped test",
                annotations=(("accept", "pytest"),),
            ),
            self._ctask(
                task_id="T-000002",
                text="command proof",
                annotations=(("accept", "command-exit: ./run.sh --help"),),
            ),
        )
        plan = self._cplan(phases=(self._cphase(tasks=tasks),))

        reparsed = parse_plan(render_plan(plan))
        reparsed_tasks = reparsed.phases[0].tasks

        assert reparsed_tasks[0].annotations == (("accept", "pytest"),)
        assert reparsed_tasks[1].annotations == (
            ("accept", "command-exit: ./run.sh --help"),
        )

    def test_all_accept_kinds_validate_on_leaf_implementation_tasks(self) -> None:
        tasks = (
            self._ctask(task_id="T-000001", annotations=(("accept", "pytest"),)),
            self._ctask(
                task_id="T-000002",
                annotations=(("accept", "command-exit: ./run.sh --help"),),
            ),
            self._ctask(task_id="T-000003", annotations=(("accept", "coverage"),)),
            self._ctask(
                task_id="T-000004",
                annotations=(
                    ("accept", "waived: covered by first task; covered-by=T-000001"),
                ),
            ),
        )
        plan = self._cplan(phases=(self._cphase(tasks=tasks),))

        validate_plan(plan, constructed=True)

    def test_missing_accept_rejected_on_leaf_implementation_task(self) -> None:
        plan = self._cplan(phases=(self._cphase(tasks=(self._ctask(annotations=()),)),))
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].tasks[0] missing accept annotation on a leaf implementation task"
            in m
            for m in exc_info.value.messages
        )

    def test_duplicate_accept_annotations_rejected(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(
                        self._ctask(
                            annotations=(
                                ("accept", "pytest"),
                                ("accept", "coverage"),
                            )
                        ),
                    ),
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].tasks[0] has more than one accept annotation" in m
            for m in exc_info.value.messages
        )

    def test_unknown_accept_kind_rejected(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(tasks=(self._ctask(annotations=(("accept", "manual"),)),)),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].tasks[0] has unknown accept kind" in m
            for m in exc_info.value.messages
        )

    def test_waived_accept_missing_covered_by_rejected(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(
                        self._ctask(
                            annotations=(("accept", "waived: external proof"),)
                        ),
                    ),
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].tasks[0] has waived accept with no covered-by= / "
            "malformed covered-by" in m
            for m in exc_info.value.messages
        )

    def test_waived_accept_unknown_covered_by_rejected(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(
                        self._ctask(
                            annotations=(
                                (
                                    "accept",
                                    "waived: external proof; covered-by=T-999999",
                                ),
                            )
                        ),
                    ),
                ),
            )
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].tasks[0] has waived accept whose covered-by id is not "
            "present in the plan: T-999999" in m
            for m in exc_info.value.messages
        )

    def test_accept_not_required_for_parent_user_or_action_tasks(self) -> None:
        child = self._ctask(task_id="T-000002", annotations=(("accept", "pytest"),))
        parent = self._ctask(
            task_id="T-000001",
            children=(child,),
            annotations=(),
        )
        user = self._ctask(
            task_id="T-000003",
            flag_tags=("USER",),
            annotations=(),
        )
        action = self._ctask(
            task_id="T-000004",
            text="",
            action_tag=("run_cli", "./run.sh --dry-run"),
            annotations=(),
        )
        plan = self._cplan(phases=(self._cphase(tasks=(parent, user, action)),))

        validate_plan(plan, constructed=True)

    def test_default_mode_unchanged_with_legacy_inputs(self) -> None:
        # A plan with magic_version=None, ordinal mismatch, no phase_id,
        # and trailing_lines passes the default validator unchanged —
        # the constructed-only checks must not leak into compat mode.
        plan = Plan(
            magic_version=None,
            project_title="Legacy",
            preamble="multi\nline\nallowed",
            phases=(
                self._cphase(
                    phase_id=None,
                    phase_id_source="none",
                    ordinal=7,
                    tasks=(self._ctask(task_id=None, trailing_lines=("",)),),
                ),
            ),
            bugs=None,
            source_path=None,
        )
        validate_plan(plan)

    def test_default_mode_does_not_require_accept_on_leaf_tasks(self) -> None:
        plan = self._cplan(
            magic_version=None,
            phases=(self._cphase(tasks=(self._ctask(annotations=()),)),),
        )
        validate_plan(plan, constructed=False)

    def test_magic_version_must_be_one(self) -> None:
        plan = self._cplan(magic_version=None)
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any("plan.magic_version must be 1" in m for m in exc_info.value.messages)

    def test_ordinals_must_be_contiguous(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    ordinal=1,
                    phase_id="phase_001",
                    tasks=(self._ctask(task_id="T-000001"),),
                ),
                self._cphase(
                    ordinal=3,
                    phase_id="phase_003",
                    tasks=(self._ctask(task_id="T-000002"),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phase ordinals must be contiguous" in m for m in exc_info.value.messages
        )

    def test_keyword_must_be_phase_or_stage(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    keyword="Step",
                    tasks=(self._ctask(),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "keyword must be 'Phase' or 'Stage'" in m for m in exc_info.value.messages
        )

    def test_phase_must_have_phase_id(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    phase_id=None,
                    phase_id_source="none",
                    tasks=(self._ctask(),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any("missing phase_id" in m for m in exc_info.value.messages)

    def test_duplicate_phase_ids_rejected(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    ordinal=1,
                    phase_id="phase_dup",
                    tasks=(self._ctask(task_id="T-000001"),),
                ),
                self._cphase(
                    ordinal=2,
                    phase_id="phase_dup",
                    tasks=(self._ctask(task_id="T-000002"),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any("duplicate phase_id phase_dup" in m for m in exc_info.value.messages)

    def test_every_task_must_have_id(self) -> None:
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(self._ctask(task_id=None),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "task_id is missing on constructed task" in m
            for m in exc_info.value.messages
        )

    def test_nested_child_must_have_id(self) -> None:
        # The per-node check descends into children: a missing id on a
        # nested task must surface with the child-path-qualified label
        # so a fixer can locate the offending node.
        child = self._ctask(task_id=None, text="child")
        parent = self._ctask(task_id="T-000001", children=(child,))
        plan = self._cplan(phases=(self._cphase(tasks=(parent,)),))
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        messages = exc_info.value.messages
        assert any(
            "children[0].task_id is missing on constructed task" in m for m in messages
        )

    def test_trailing_lines_accepted_on_constructed_validation(self) -> None:
        # Trailing lines pass constructed validation: make_task cannot
        # produce them, so the old rejection only ever fired on plans
        # parsed from disk, where they are legitimate lossless content.
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(self._ctask(trailing_lines=("  # stash",)),),
                ),
            ),
        )
        validate_plan(plan, constructed=True)

    def test_project_title_newline_rejected(self) -> None:
        plan = self._cplan(project_title="line\nbreak")
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "project_title contains an embedded newline" in m
            for m in exc_info.value.messages
        )

    def test_preamble_newline_rejected(self) -> None:
        # Multi-line preamble is legal for parsed legacy plans but the
        # constructed oracle has no multi-line exception per v4 R3.
        plan = self._cplan(preamble="first\nsecond")
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "preamble contains an embedded newline" in m
            for m in exc_info.value.messages
        )

    def test_phase_title_round_trip_collision_rejected(self) -> None:
        # A phase title that re-parses as a different structure (here a
        # phase-id comment fragment) fails the v4 R3 round-trip oracle.
        # The colliding sequence is the literal phase-id comment opener;
        # placed inside a title it would be misread by the parser.
        plan = self._cplan(
            phases=(
                self._cphase(
                    title="legit\rcarriage",
                    tasks=(self._ctask(),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any("phases[0].title" in m for m in exc_info.value.messages)

    def test_task_text_grammar_collision_rejected(self) -> None:
        # The Stage 10 task harness catches text that re-parses as an
        # annotation; validate_plan(constructed=True) must surface that
        # failure with the task's plan-location label prepended.
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(self._ctask(text="title [fix: leaked]"),),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        messages = exc_info.value.messages
        assert any(
            "phases[0].tasks[0]:" in m and "text" in m and "failed to round-trip" in m
            for m in messages
        )

    def test_subsection_title_field_stability(self) -> None:
        sub = Subsection(
            title="Manual\nverification",
            prose="",
            tasks=(self._ctask(task_id="T-000002"),),
            line_number=0,
        )
        plan = self._cplan(
            phases=(
                self._cphase(
                    tasks=(self._ctask(task_id="T-000001"),),
                    subsections=(sub,),
                ),
            ),
        )
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(plan, constructed=True)
        assert any(
            "phases[0].subsections[0].title contains an embedded newline" in m
            for m in exc_info.value.messages
        )


class TestAssertMcloopCanonical:
    """Tests for ``assert_mcloop_canonical`` per v4 Contract 5.

    The function renders a plan, re-parses, requires semantic equality
    (NOT byte fixed point) under the v4 normalizer, and runs the R1/R2
    equivalents without importing mcloop. On success it returns the
    rendered text so the caller can persist exactly what was checked.
    Construction-API strictness remains under
    ``validate_plan(..., constructed=True)``.
    """

    def _ctask(
        self,
        *,
        task_id: str = "T-000001",
        text: str = "do thing",
        children: tuple[Task, ...] = (),
    ) -> Task:
        return Task(
            task_id=task_id,
            text=text,
            status=TaskStatus.TODO,
            flag_tags=(),
            action_tag=None,
            annotations=(),
            deps=(),
            children=children,
            ruled_out=(),
            indent_level=0,
            line_number=0,
            trailing_lines=(),
        )

    def _cplan(self, *, tasks: tuple[Task, ...] | None = None) -> Plan:
        phase_tasks = (self._ctask(),) if tasks is None else tasks
        return Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(
                Phase(
                    phase_id="phase_001",
                    phase_id_source="explicit_comment",
                    ordinal=1,
                    keyword="Phase",
                    title="P",
                    prose="",
                    subsections=(),
                    tasks=phase_tasks,
                    line_number=0,
                ),
            ),
            bugs=None,
            source_path=None,
        )

    def test_valid_plan_returns_rendered_text(self) -> None:
        plan = self._cplan()
        rendered = assert_mcloop_canonical(plan)
        # The returned text is exactly what render_plan produced — the
        # caller must persist this string verbatim to preserve the
        # validation guarantee.
        assert rendered == render_plan(plan)
        assert rendered.endswith("\n")
        assert "<!-- bob-plan-format: 1 -->" in rendered
        assert "T-000001" in rendered

    def test_missing_magic_version_is_allowed_by_mcloop_canonical_contract(
        self,
    ) -> None:
        plan = dataclasses.replace(self._cplan(), magic_version=None)
        rendered = assert_mcloop_canonical(plan)
        assert rendered == render_plan(plan)
        assert "<!-- bob-plan-format: 1 -->" not in rendered

    def test_multiline_phase_prose_allowed_by_mcloop_canonical_contract(
        self,
    ) -> None:
        phase = dataclasses.replace(
            self._cplan().phases[0],
            prose="First paragraph.\n\nSecond paragraph.",
        )
        plan = dataclasses.replace(self._cplan(), phases=(phase,))
        rendered = assert_mcloop_canonical(plan)
        assert rendered == render_plan(plan)
        assert "Second paragraph." in rendered

    def test_missing_id_rejected(self) -> None:
        # R2 equivalent: every parsed task must carry a stable
        # T-NNNNNN id, matching mcloop's canonical precondition.
        bare = Task(
            task_id=None,
            text="x",
            status=TaskStatus.TODO,
            flag_tags=(),
            action_tag=None,
            annotations=(),
            deps=(),
            children=(),
            ruled_out=(),
            indent_level=0,
            line_number=0,
            trailing_lines=(),
        )
        plan = dataclasses.replace(self._cplan(tasks=(bare,)), magic_version=None)
        with pytest.raises(PlanValidationError) as exc_info:
            assert_mcloop_canonical(plan)
        assert any(
            "parsed plan has 1 task(s) without stable T-NNNNNN id(s)" in m
            for m in exc_info.value.messages
        )

    def test_returned_text_round_trips_through_parse(self) -> None:
        # The text returned must itself satisfy the canonical contract
        # so a downstream save can re-validate without surprise. This
        # pins idempotence on the rendered byte stream.
        from bob_tools.planfile.parser import parse_plan as _parse

        plan = self._cplan()
        rendered = assert_mcloop_canonical(plan)
        reparsed = _parse(rendered)
        again = assert_mcloop_canonical(reparsed)
        assert again == rendered

    def test_multi_phase_plan_round_trips(self) -> None:
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(
                Phase(
                    phase_id="phase_001",
                    phase_id_source="explicit_comment",
                    ordinal=1,
                    keyword="Phase",
                    title="First",
                    prose="",
                    subsections=(),
                    tasks=(self._ctask(task_id="T-000001", text="a"),),
                    line_number=0,
                ),
                Phase(
                    phase_id="phase_002",
                    phase_id_source="explicit_comment",
                    ordinal=2,
                    keyword="Phase",
                    title="Second",
                    prose="",
                    subsections=(),
                    tasks=(self._ctask(task_id="T-000002", text="b"),),
                    line_number=0,
                ),
            ),
            bugs=None,
            source_path=None,
        )
        rendered = assert_mcloop_canonical(plan)
        assert rendered == render_plan(plan)
        assert "phase_001" in rendered
        assert "phase_002" in rendered
        assert "T-000001" in rendered
        assert "T-000002" in rendered

    def test_plan_with_bugs_section_round_trips(self) -> None:
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(
                Phase(
                    phase_id="phase_001",
                    phase_id_source="explicit_comment",
                    ordinal=1,
                    keyword="Phase",
                    title="P",
                    prose="",
                    subsections=(),
                    tasks=(self._ctask(task_id="T-000001"),),
                    line_number=0,
                ),
            ),
            bugs=BugsSection(
                tasks=(self._ctask(task_id="T-000900", text="some bug"),),
                line_number=0,
            ),
            source_path=None,
        )
        rendered = assert_mcloop_canonical(plan)
        assert rendered == render_plan(plan)
        assert "## Bugs" in rendered
        assert "T-000900" in rendered

    def test_source_path_forwarded_to_reparse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``source_path`` only manifests on a PlanSyntaxError raised by
        # the re-parse; on the happy path it has no observable effect on
        # the return value. Capture the kwarg the re-parse received to
        # pin that the contract's forwarding behavior is intact (so a
        # rendered-text parse failure surfaces with the correct file
        # context for the caller).
        from bob_tools.planfile import canonical as ops
        from bob_tools.planfile.parser import parse_plan as _real_parse

        captured: dict[str, Path | None] = {}

        def spy_parse(
            text: str,
            *,
            strict: bool = False,
            source_path: Path | None = None,
        ) -> Plan:
            captured["source_path"] = source_path
            return _real_parse(text, strict=strict, source_path=source_path)

        monkeypatch.setattr(ops, "parse_plan", spy_parse)
        plan = self._cplan()
        marker = Path("packages/bob-tools/.scratch/some-plan-file.md")
        assert_mcloop_canonical(plan, source_path=marker)
        assert captured["source_path"] == marker

    def test_v3_leak_class_byte_fixed_point_semantic_divergence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The v3 leak class: rendered bytes round-trip through parser, but
        # the parsed plan is semantically different from the intended one.
        # Contract 5's byte-fixed-point check alone would miss this; the
        # semantic compare must catch it. Simulate by intercepting
        # ``parse_plan`` to return a plan that differs from the intended
        # plan in a field the renderer would have preserved (here:
        # ``text``). Discriminate from the internal field-stability
        # parse calls via ``source_path``: the Contract 5 reparse is the
        # only call that forwards a non-None ``source_path``, so a test
        # marker is unambiguous here.
        from bob_tools.planfile import canonical as ops
        from bob_tools.planfile.parser import parse_plan as _real_parse

        plan = self._cplan()
        marker = Path("packages/bob-tools/.scratch/v3-leak-marker.md")

        def divergent_parse(
            text: str,
            *,
            strict: bool = False,
            source_path: Path | None = None,
        ) -> Plan:
            parsed = _real_parse(text, strict=strict, source_path=source_path)
            if source_path != marker:
                return parsed
            mutated_task = dataclasses.replace(
                parsed.phases[0].tasks[0], text="something else entirely"
            )
            mutated_phase = dataclasses.replace(parsed.phases[0], tasks=(mutated_task,))
            return dataclasses.replace(parsed, phases=(mutated_phase,))

        monkeypatch.setattr(ops, "parse_plan", divergent_parse)
        with pytest.raises(PlanValidationError) as exc_info:
            assert_mcloop_canonical(plan, source_path=marker)
        assert any("failed semantic round-trip" in m for m in exc_info.value.messages)

    def test_r1_shape_fixture_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # R1 (grammar-narrowing equivalent): every ``- [ ]`` line in the
        # rendered text must surface as a parsed TODO task. The canonical
        # R1-shape leak is a parse that silently drops a checkbox the
        # renderer emitted. Simulate by intercepting Contract 5's reparse
        # to drop all tasks (phases keep their structure so the semantic
        # compare also fires; either message satisfies the gate, but R1
        # is the specific contract under test here). Discriminate from
        # the internal field-stability parse calls via ``source_path``
        # exactly as the v3-leak test does.
        from bob_tools.planfile import canonical as ops
        from bob_tools.planfile.parser import parse_plan as _real_parse

        plan = self._cplan()
        marker = Path("packages/bob-tools/.scratch/r1-leak-marker.md")

        def dropping_parse(
            text: str,
            *,
            strict: bool = False,
            source_path: Path | None = None,
        ) -> Plan:
            parsed = _real_parse(text, strict=strict, source_path=source_path)
            if source_path != marker:
                return parsed
            stripped_phases = tuple(
                dataclasses.replace(phase, tasks=()) for phase in parsed.phases
            )
            return dataclasses.replace(parsed, phases=stripped_phases)

        monkeypatch.setattr(ops, "parse_plan", dropping_parse)
        with pytest.raises(PlanValidationError) as exc_info:
            assert_mcloop_canonical(plan, source_path=marker)
        assert any("silently dropped" in m for m in exc_info.value.messages)


class TestBugCount:
    """``bug_count`` disambiguates the three Bugs-section states.

    The verification one-liner from task 2.8 used ``p.bugs is not None``
    which printed ``bugs=False`` for both "no Bugs section" and
    "Bugs section present but empty" — the same string carried two
    very different meanings. ``bug_count`` separates them: an absent
    section reports ``0``, an empty section also reports ``0`` but
    paired with ``p.bugs is not None == True``, and a populated section
    reports the actual task count.
    """

    def test_no_bugs_section_returns_zero(self) -> None:
        plan = _plan()
        assert bug_count(plan) == 0
        assert plan.bugs is None

    def test_empty_bugs_section_returns_zero(self) -> None:
        plan = _plan(bugs=BugsSection(tasks=(), line_number=10))
        assert bug_count(plan) == 0
        # The Bugs section is present even though the count is zero; the
        # ``bugs=true bug_count=0`` pair distinguishes this case from a
        # plan with no Bugs heading at all.
        assert plan.bugs is not None

    def test_populated_bugs_section_returns_task_count(self) -> None:
        plan = _plan(
            bugs=BugsSection(
                tasks=(
                    _task(task_id="T-000900", line_number=20),
                    _task(task_id="T-000901", line_number=21),
                    _task(task_id="T-000902", line_number=22),
                ),
                line_number=19,
            ),
        )
        assert bug_count(plan) == 3

    def test_bug_count_does_not_count_nested_children(self) -> None:
        # ``bug_count`` reports root-level bug tasks only. A bug task
        # with nested subtasks counts as one, matching how the
        # verification command reports "phases" (root phase count, not
        # task totals).
        child = _task(task_id="T-000902", indent_level=2, line_number=22)
        root = _task(task_id="T-000901", children=(child,), line_number=21)
        plan = _plan(bugs=BugsSection(tasks=(root,), line_number=20))
        assert bug_count(plan) == 1


class TestFindTaskById:
    """``_find_task_by_id`` walks the parsed tree by ID equality.

    Per design doc section 7.2 caveat: the library must tokenize and
    match against parsed task entries, not substring-search raw lines,
    because ``T-000001`` is a substring of ``T-0000010``. These tests
    pin that contract — both the affirmative tree-walk behavior across
    every section type and the prefix-overlap regression that motivated
    the function in the first place.
    """

    def test_returns_none_when_id_absent(self) -> None:
        plan = _plan(phases=(_phase(tasks=(_task(task_id="T-000001"),)),))
        assert _find_task_by_id(plan, "T-000999") is None

    def test_finds_root_phase_task(self) -> None:
        target = _task(task_id="T-000002", text="target")
        plan = _plan(
            phases=(_phase(tasks=(_task(task_id="T-000001"), target)),),
        )
        assert _find_task_by_id(plan, "T-000002") is target

    def test_finds_nested_child(self) -> None:
        target = _task(task_id="T-000002", indent_level=2, text="child")
        parent = _task(task_id="T-000001", children=(target,))
        plan = _plan(phases=(_phase(tasks=(parent,)),))
        assert _find_task_by_id(plan, "T-000002") is target

    def test_finds_subsection_task(self) -> None:
        target = _task(task_id="T-000010", line_number=20, text="manual")
        sub = Subsection(title="Manual", prose="", tasks=(target,), line_number=19)
        phase = _phase(tasks=(_task(task_id="T-000001"),), subsections=(sub,))
        assert _find_task_by_id(_plan(phases=(phase,)), "T-000010") is target

    def test_finds_bugs_task(self) -> None:
        target = _task(task_id="T-000900", line_number=100, text="bug")
        plan = _plan(bugs=BugsSection(tasks=(target,), line_number=99))
        assert _find_task_by_id(plan, "T-000900") is target

    def test_skips_tasks_without_id(self) -> None:
        # Compat-mode tasks (task_id=None) must not match a lookup for
        # any string; the equality compare against ``None`` handles this
        # automatically, but pin it so a future refactor cannot silently
        # introduce ``startswith`` / ``in`` semantics that would coerce.
        with_id = _task(task_id="T-000001")
        without_id = _task(task_id=None, line_number=2)
        plan = _plan(phases=(_phase(tasks=(without_id, with_id)),))
        assert _find_task_by_id(plan, "T-000001") is with_id

    def test_does_not_substring_match_prefix_overlap(self) -> None:
        # The regression that motivates the whole function: ``T-000001``
        # is a substring of ``T-0000010``. A substring-based lookup
        # would conflate them. Tree-walk equality must keep them
        # distinct, finding each only by exact match.
        shorter = _task(task_id="T-000001", text="short id")
        longer = _task(task_id="T-0000010", text="long id", line_number=2)
        plan = _plan(phases=(_phase(tasks=(shorter, longer)),))
        assert _find_task_by_id(plan, "T-000001") is shorter
        assert _find_task_by_id(plan, "T-0000010") is longer


class TestResolveTaskContext:
    """``resolve_task_context`` is the single task → phase resolver.

    Replaces the substring-scan that ``find_explicit_phase_id_for_task``
    does today (design doc section 7.1). The contract is:

      - Match by ``task_id`` exact equality first (the canonical path).
      - Fall back to the parsed task's text — exact match or label-with-
        separator prefix — so pre-id PLAN.md files still resolve.
      - Bugs section tasks are findable but report ``phase_id=None``
        and ``phase_id_source="none"`` since bugs have no phase.
      - Unresolved input returns a none-shaped context (no exception).
      - ``plan_phase_count`` is always ``len(plan.phases)`` so the
        ledger_emit shim does not need a second pass over the plan.
    """

    def _phase_with_id(
        self,
        *,
        tasks: tuple[Task, ...],
        phase_id: str | None = "phase_001",
        phase_id_source: str = "explicit_comment",
        subsections: tuple[Subsection, ...] = (),
        ordinal: int = 1,
    ) -> Phase:
        return Phase(
            phase_id=phase_id,
            phase_id_source=phase_id_source,
            ordinal=ordinal,
            keyword="Stage",
            title="Core",
            prose="",
            subsections=subsections,
            tasks=tasks,
            line_number=5,
        )

    def test_resolves_by_task_id_exact_match(self) -> None:
        target = _task(task_id="T-000002")
        phase = self._phase_with_id(tasks=(_task(task_id="T-000001"), target))
        plan = _plan(phases=(phase,))
        ctx = resolve_task_context(plan, "T-000002")
        assert ctx == TaskContext(
            task_id="T-000002",
            phase_id="phase_001",
            phase_id_source="explicit_comment",
            label="T-000002",
            plan_phase_count=1,
        )

    def test_resolves_nested_child_to_containing_phase(self) -> None:
        child = _task(task_id="T-000002", indent_level=2)
        parent = _task(task_id="T-000001", children=(child,))
        plan = _plan(phases=(self._phase_with_id(tasks=(parent,)),))
        ctx = resolve_task_context(plan, "T-000002")
        assert ctx.task_id == "T-000002"
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "explicit_comment"

    def test_resolves_subsection_task_to_parent_phase(self) -> None:
        # Subsections are humans-only grouping (design doc Q5); their
        # tasks share the parent phase's id.
        sub_task = _task(task_id="T-000010", line_number=20)
        sub = Subsection(title="Manual", prose="", tasks=(sub_task,), line_number=19)
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),), subsections=(sub,)
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "T-000010")
        assert ctx.phase_id == "phase_001"
        assert ctx.task_id == "T-000010"

    def test_explicit_header_source_is_propagated(self) -> None:
        # The shim in ledger_emit collapses both explicit_* variants to
        # "explicit"; the resolver must surface the parser's value so
        # the shim has the raw signal to collapse.
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_002",
            phase_id_source="explicit_header",
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "T-000001")
        assert ctx.phase_id == "phase_002"
        assert ctx.phase_id_source == "explicit_header"

    def test_phase_with_none_source_fills_ordinal_id(self) -> None:
        # When the matched task's containing phase has no explicit
        # phase_id, the resolver synthesizes ``phase_NNN`` from the
        # phase's 1-based document order position and reports
        # ``phase_id_source="ordinal"``. Per design doc section 7.1
        # ("Ordinal fallback. The n-th phase heading in document
        # order") and section 2.4 (explicit-required / ordinal-degraded
        # contract). The pre-fill behavior — propagating ``None`` and
        # ``"none"`` to the caller — would have forced the ledger_emit
        # shim to do a second pass with an ``ordinal_index`` argument;
        # synthesizing inside the resolver eliminates that.
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "T-000001")
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "ordinal"
        # The match still happened — task_id is populated.
        assert ctx.task_id == "T-000001"

    def test_ordinal_fill_uses_document_position_not_phase_ordinal(self) -> None:
        # The synthesized id reflects the phase's 1-based index in
        # ``plan.phases`` (document order), not ``Phase.ordinal``
        # (which is parsed from the heading text and can start above 1
        # in a partial-plan snippet). A plan whose first phase is
        # ``Stage 5`` still synthesizes ``phase_001`` for that phase
        # when its source is "none".
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
            ordinal=5,
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "T-000001")
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "ordinal"

    def test_ordinal_fill_per_phase_position(self) -> None:
        # Each "none"-source phase gets its own synthesized id based
        # on its own document position; explicit phases in between
        # do not shift the count.
        none_first = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
            ordinal=1,
        )
        explicit_second = self._phase_with_id(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_named",
            phase_id_source="explicit_comment",
            ordinal=2,
        )
        none_third = self._phase_with_id(
            tasks=(_task(task_id="T-000003"),),
            phase_id=None,
            phase_id_source="none",
            ordinal=3,
        )
        plan = _plan(phases=(none_first, explicit_second, none_third))
        assert resolve_task_context(plan, "T-000001").phase_id == "phase_001"
        assert resolve_task_context(plan, "T-000001").phase_id_source == "ordinal"
        assert resolve_task_context(plan, "T-000002").phase_id == "phase_named"
        assert (
            resolve_task_context(plan, "T-000002").phase_id_source == "explicit_comment"
        )
        assert resolve_task_context(plan, "T-000003").phase_id == "phase_003"
        assert resolve_task_context(plan, "T-000003").phase_id_source == "ordinal"

    def test_ordinal_fill_via_positional_label(self) -> None:
        # Positional resolution exits the resolver early; it must
        # apply the same ordinal-fill rule as the task-walk path,
        # otherwise a "5.1" reference into an unmigrated phase would
        # leak the raw ``phase_id=None`` to callers.
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
            ordinal=5,
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "5.1")
        assert ctx.task_id == "T-000001"
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "ordinal"

    def test_unresolved_reference_stays_none_not_ordinal(self) -> None:
        # The ordinal-fill rule only applies when a task is actually
        # matched inside a phase. A miss must NOT silently invent a
        # phase_id — callers branch on ``phase_id is None`` to detect
        # "not found" and inventing an id here would mask that.
        none_phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
        )
        ctx = resolve_task_context(_plan(phases=(none_phase,)), "T-000999")
        assert ctx.task_id is None
        assert ctx.phase_id is None
        assert ctx.phase_id_source == "none"

    def test_unresolved_reference_returns_none_context(self) -> None:
        plan = _plan(phases=(self._phase_with_id(tasks=(_task(task_id="T-000001"),)),))
        ctx = resolve_task_context(plan, "T-000999")
        assert ctx == TaskContext(
            task_id=None,
            phase_id=None,
            phase_id_source="none",
            label="T-000999",
            plan_phase_count=1,
        )

    def test_bugs_task_resolves_with_none_phase(self) -> None:
        bug = _task(task_id="T-000900", line_number=100)
        plan = _plan(
            phases=(self._phase_with_id(tasks=(_task(task_id="T-000001"),)),),
            bugs=BugsSection(tasks=(bug,), line_number=99),
        )
        ctx = resolve_task_context(plan, "T-000900")
        # Found the task (so task_id is populated) but it sits outside
        # any phase, so the phase_id fields stay in "none" shape.
        assert ctx.task_id == "T-000900"
        assert ctx.phase_id is None
        assert ctx.phase_id_source == "none"
        assert ctx.plan_phase_count == 1

    def test_plan_phase_count_reflects_full_plan(self) -> None:
        # plan_phase_count is a snapshot of len(plan.phases) regardless
        # of which phase the matched task lives in; the ordinal-shim
        # needs this to bounds-check its ordinal_index argument.
        phase_one = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        phase_two = self._phase_with_id(
            tasks=(_task(task_id="T-000002"),), phase_id="phase_002"
        )
        plan = _plan(phases=(phase_one, phase_two))
        assert resolve_task_context(plan, "T-000001").plan_phase_count == 2
        assert resolve_task_context(plan, "T-000002").plan_phase_count == 2
        assert resolve_task_context(plan, "T-000999").plan_phase_count == 2

    def test_resolves_label_prefix_with_separator(self) -> None:
        # Pre-id PLAN.md files use duplo-style labels at the start of
        # the task text. ``task-001`` must resolve to the task whose
        # text is ``task-001: ...`` even though the task carries no id.
        labeled = _task(task_id=None, text="task-001: Bring up scaffold")
        plan = _plan(phases=(self._phase_with_id(tasks=(labeled,)),))
        ctx = resolve_task_context(plan, "task-001")
        assert ctx.task_id is None
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "explicit_comment"

    def test_label_prefix_requires_a_separator(self) -> None:
        # Bare substring matching is the bug the resolver was created
        # to fix: ``task-001`` must NOT resolve to ``task-0010: ...``.
        # Only ``ref`` followed by a structural separator counts.
        overlap = _task(task_id=None, text="task-0010: Adjacent label")
        plan = _plan(phases=(self._phase_with_id(tasks=(overlap,)),))
        ctx = resolve_task_context(plan, "task-001")
        assert ctx.task_id is None
        assert ctx.phase_id is None
        assert ctx.phase_id_source == "none"

    def test_task_id_prefix_overlap_does_not_misresolve(self) -> None:
        # The canonical regression: ``T-000001`` is a substring of
        # ``T-0000010``. resolve_task_context must keep them distinct.
        shorter = _task(task_id="T-000001", text="short id")
        longer = _task(task_id="T-0000010", text="long id", line_number=2)
        plan = _plan(phases=(self._phase_with_id(tasks=(shorter, longer)),))
        assert resolve_task_context(plan, "T-000001").task_id == "T-000001"
        assert resolve_task_context(plan, "T-0000010").task_id == "T-0000010"

    def test_first_match_wins_in_document_order(self) -> None:
        # When two tasks would both legitimately match the same
        # reference — one by task_id, one by text-prefix — the
        # resolver returns the first match in document order. Pin
        # the contract so the behavior cannot drift to "id-match
        # always wins" (which would require a second pass) or
        # "text-match always wins" without a deliberate change.
        text_first = _task(task_id=None, text="ref-token: incidental match")
        id_second = _task(task_id="ref-token", text="other text", line_number=2)
        plan = _plan(phases=(self._phase_with_id(tasks=(text_first, id_second)),))
        ctx = resolve_task_context(plan, "ref-token")
        assert ctx.task_id is None
        assert ctx.phase_id == "phase_001"

    def test_phase_tasks_searched_before_bugs(self) -> None:
        # If the same label could match both a phase task and a bug
        # task, the phase match wins (it has a real phase_id). The
        # bug-search pass only fires when the phase pass yields
        # nothing.
        phase_hit = _task(task_id="dup-id", text="phase task")
        bug_hit = _task(task_id="dup-id", text="bug task", line_number=99)
        plan = _plan(
            phases=(self._phase_with_id(tasks=(phase_hit,)),),
            bugs=BugsSection(tasks=(bug_hit,), line_number=98),
        )
        ctx = resolve_task_context(plan, "dup-id")
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "explicit_comment"

    def test_label_field_echoes_input(self) -> None:
        # The label field is informational — callers use it to thread
        # the original reference into diagnostics without re-stashing
        # it. Echo on hit and on miss alike.
        plan = _plan(phases=(self._phase_with_id(tasks=(_task(task_id="T-000001"),)),))
        assert resolve_task_context(plan, "T-000001").label == "T-000001"
        assert resolve_task_context(plan, "missing").label == "missing"

    def test_resolves_positional_label_two_tokens(self) -> None:
        # mcloop's task_label emits "N.M" for stage-headed root tasks
        # ("5.2" = the second root task in Stage 5). Positional
        # resolution finds the phase by ordinal and indexes into
        # phase.tasks (1-based).
        t1 = _task(task_id="T-000001")
        t2 = _task(task_id="T-000002", line_number=2)
        t3 = _task(task_id="T-000003", line_number=3)
        plan = _plan(
            phases=(
                self._phase_with_id(
                    tasks=(t1, t2, t3),
                    phase_id="phase_005",
                    ordinal=5,
                ),
            )
        )
        ctx = resolve_task_context(plan, "5.2")
        assert ctx.task_id == "T-000002"
        assert ctx.phase_id == "phase_005"
        assert ctx.phase_id_source == "explicit_comment"
        assert ctx.label == "5.2"

    def test_resolves_positional_label_three_tokens(self) -> None:
        # "1.3.2" = Stage 1, root task 3, child task 2. Descent uses
        # task.children, not subsections.
        child1 = _task(task_id="T-000010", indent_level=2, line_number=10)
        child2 = _task(task_id="T-000011", indent_level=2, line_number=11)
        root3 = _task(
            task_id="T-000003",
            line_number=3,
            children=(child1, child2),
        )
        plan = _plan(
            phases=(
                self._phase_with_id(
                    tasks=(
                        _task(task_id="T-000001"),
                        _task(task_id="T-000002", line_number=2),
                        root3,
                    ),
                ),
            )
        )
        ctx = resolve_task_context(plan, "1.3.2")
        assert ctx.task_id == "T-000011"
        assert ctx.phase_id == "phase_001"

    def test_positional_uses_phase_ordinal_not_document_index(self) -> None:
        # Phase.ordinal is the integer parsed from "Stage N"/"Phase N",
        # not the document position. A plan that opens at "Stage 3"
        # has phases[0].ordinal == 3, and "3.1" must resolve there.
        target = _task(task_id="T-000005")
        phase_three = self._phase_with_id(
            tasks=(target,), phase_id="phase_003", ordinal=3
        )
        plan = _plan(phases=(phase_three,))
        ctx = resolve_task_context(plan, "3.1")
        assert ctx.task_id == "T-000005"
        assert ctx.phase_id == "phase_003"

    def test_positional_compat_task_resolves_without_id(self) -> None:
        # Positional resolution must work on pre-migration plans where
        # tasks carry no task_id; that is the main case it serves
        # because once IDs exist, mcloop will hand IDs to the
        # resolver directly.
        compat = _task(task_id=None, text="bring up scaffold")
        plan = _plan(phases=(self._phase_with_id(tasks=(compat,)),))
        ctx = resolve_task_context(plan, "1.1")
        assert ctx.task_id is None
        assert ctx.phase_id == "phase_001"
        assert ctx.phase_id_source == "explicit_comment"

    def test_positional_out_of_range_root_falls_through(self) -> None:
        # "1.99" with only one root task: positional resolution must
        # not silently match the last task. It returns None, which
        # then falls through to id/text matching, which also misses,
        # producing a none-shaped context.
        plan = _plan(phases=(self._phase_with_id(tasks=(_task(task_id="T-000001"),)),))
        ctx = resolve_task_context(plan, "1.99")
        assert ctx.task_id is None
        assert ctx.phase_id is None
        assert ctx.phase_id_source == "none"
        assert ctx.label == "1.99"

    def test_positional_out_of_range_child_falls_through(self) -> None:
        # Indexes are checked at every level: a valid root pick
        # followed by an out-of-range child returns the none-shaped
        # context, not the parent task.
        parent = _task(task_id="T-000001", children=(_task(task_id="T-000010"),))
        plan = _plan(phases=(self._phase_with_id(tasks=(parent,)),))
        ctx = resolve_task_context(plan, "1.1.5")
        assert ctx.task_id is None
        assert ctx.phase_id is None

    def test_positional_missing_phase_falls_through(self) -> None:
        # "9.1" against a plan whose phases are ordinal 1, 2 must
        # not match — there is no phase 9.
        p1 = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        p2 = self._phase_with_id(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        ctx = resolve_task_context(_plan(phases=(p1, p2)), "9.1")
        assert ctx.task_id is None
        assert ctx.phase_id is None

    def test_positional_does_not_descend_into_subsections(self) -> None:
        # Mcloop's task_label puts subsection tasks under a different
        # "stage" string (the subsection heading), so they never carry
        # the parent stage's N.M label. The resolver mirrors that:
        # positional descent walks phase.tasks only, not subsections.
        sub_task = _task(task_id="T-000010", text="manual step")
        sub = Subsection(title="Manual", prose="", tasks=(sub_task,), line_number=19)
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),), subsections=(sub,)
        )
        # "1.2" would be the second root task, but there is only one,
        # and the subsection task must not be reached this way.
        ctx = resolve_task_context(_plan(phases=(phase,)), "1.2")
        assert ctx.task_id is None
        assert ctx.phase_id is None

    def test_bare_number_is_not_a_positional_label(self) -> None:
        # The pattern requires at least one dot. A bare "1" falls
        # through to id/text matching, where it only matches a task
        # whose id is "1" or whose text starts with "1: " etc.
        labeled = _task(task_id=None, text="1: top-level")
        plan = _plan(phases=(self._phase_with_id(tasks=(labeled,)),))
        ctx = resolve_task_context(plan, "1")
        # Resolved through the text-prefix path, not positional.
        assert ctx.task_id is None
        assert ctx.phase_id == "phase_001"
        # And a bare "1" with no text/id match in the plan stays
        # none-shaped (no accidental "first root task" interpretation).
        empty_phase = self._phase_with_id(tasks=(_task(task_id="T-000001"),))
        miss = resolve_task_context(_plan(phases=(empty_phase,)), "2")
        assert miss.task_id is None
        assert miss.phase_id is None

    def test_positional_tokenizes_does_not_substring_scan(self) -> None:
        # Substring scan would conflate "1.3" with "1.30" (the way
        # ``label_token in line`` did for T-NNNNNN ids — design doc
        # 7.2 caveat). Tokenization makes 1.3 = phase 1, task 3,
        # period — never "anything whose label starts with 1.3".
        # If only three root tasks exist, "1.30" must miss.
        plan = _plan(
            phases=(
                self._phase_with_id(
                    tasks=(
                        _task(task_id="T-000001"),
                        _task(task_id="T-000002", line_number=2),
                        _task(task_id="T-000003", line_number=3),
                    ),
                ),
            )
        )
        assert resolve_task_context(plan, "1.3").task_id == "T-000003"
        miss = resolve_task_context(plan, "1.30")
        assert miss.task_id is None
        assert miss.phase_id is None


def _phase_with_tasks(
    tasks: tuple[Task, ...],
    *,
    phase_id: str = "phase_001",
    ordinal: int = 1,
    subsections: tuple[Subsection, ...] = (),
) -> Phase:
    return Phase(
        phase_id=phase_id,
        phase_id_source="explicit_comment",
        ordinal=ordinal,
        keyword="Stage",
        title="Stage",
        prose="",
        subsections=subsections,
        tasks=tasks,
        line_number=5,
    )


class TestNextTasks:
    """``next_tasks`` returns the next actionable tasks per design doc 6.

    Pins the priority/scoping rules and the BATCH parent surfacing.
    Each scenario uses minimal Plan structures so the test names match
    the bullet they exercise — bug priority, first-incomplete-phase
    scope, leaf-before-parent, failed-sibling blocking, ``@deps``
    blocking and unblocking, and BATCH parent surfacing.
    """

    def test_returns_first_actionable_task_in_phase(self) -> None:
        a = _task(task_id="T-000001")
        b = _task(task_id="T-000002", line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(a, b)),))
        assert next_tasks(plan) == [a]

    def test_limit_zero_returns_empty(self) -> None:
        # limit <= 0 short-circuits without walking; the plan is not
        # inspected. Pin so a future refactor cannot drift to "return
        # one anyway" or to a slice of the walk.
        a = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(a,)),))
        assert next_tasks(plan, limit=0) == []
        assert next_tasks(plan, limit=-1) == []

    def test_skips_done_tasks(self) -> None:
        done = _task(task_id="T-000001", status=TaskStatus.DONE)
        todo = _task(task_id="T-000002", line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(done, todo)),))
        assert next_tasks(plan) == [todo]

    # Bug priority ----------------------------------------------------

    def test_bug_task_has_absolute_priority_over_phase(self) -> None:
        # When any bug task is actionable, the phase walk is not even
        # consulted — design doc section 6 priority list item 1.
        phase_task = _task(task_id="T-000001")
        bug = _task(task_id="T-000900", line_number=100, text="kernel panic")
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(phase_task,)),),
            bugs=BugsSection(tasks=(bug,), line_number=99),
        )
        assert next_tasks(plan) == [bug]

    def test_falls_through_to_phase_when_all_bugs_done(self) -> None:
        # Bugs section exists but every bug task is DONE: phase walk
        # runs normally. Distinguishes "bugs gate everything" (wrong)
        # from "bugs gate while any are actionable" (correct).
        phase_task = _task(task_id="T-000001")
        finished_bug = _task(
            task_id="T-000900",
            line_number=100,
            status=TaskStatus.DONE,
        )
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(phase_task,)),),
            bugs=BugsSection(tasks=(finished_bug,), line_number=99),
        )
        assert next_tasks(plan) == [phase_task]

    def test_bugs_scope_does_not_spill_into_phases(self) -> None:
        # Even when limit > number of actionable bug tasks, the result
        # stays inside the bugs scope. Pin the "bugs scope is a separate
        # phase" interpretation against a future drift toward "fill the
        # limit by mixing bugs and phase tasks".
        bug = _task(task_id="T-000900", line_number=100)
        phase_task = _task(task_id="T-000001")
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(phase_task,)),),
            bugs=BugsSection(tasks=(bug,), line_number=99),
        )
        assert next_tasks(plan, limit=5) == [bug]

    # First-incomplete-phase scoping ----------------------------------

    def test_later_phase_invisible_until_current_phase_done(self) -> None:
        # Phase 1 has unfinished work; Phase 2 has TODO tasks. Only
        # Phase 1's task surfaces (design doc 6 priority list item 2).
        phase_one = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        phase_two = _phase_with_tasks(
            tasks=(_task(task_id="T-000099", text="future work"),),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(phase_one, phase_two))
        result = next_tasks(plan)
        assert len(result) == 1
        assert result[0].task_id == "T-000001"

    def test_skips_complete_phase_to_next_incomplete(self) -> None:
        # Phase 1 is fully DONE; Phase 2 has work. The walk advances
        # past the complete phase and surfaces Phase 2's task.
        phase_one = _phase_with_tasks(
            tasks=(_task(task_id="T-000001", status=TaskStatus.DONE),),
            phase_id="phase_001",
            ordinal=1,
        )
        target = _task(task_id="T-000002", text="next phase work")
        phase_two = _phase_with_tasks(
            tasks=(target,),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(phase_one, phase_two))
        assert next_tasks(plan) == [target]

    def test_phase_with_failed_task_is_not_complete(self) -> None:
        # FAILED tasks do not count as complete — a phase with a
        # failed task is stuck, not done, mirroring mcloop's
        # _stage_complete semantics. The walk must stay in Phase 1
        # (and at root level the FAILED task is skipped, surfacing
        # the next TODO sibling), not advance to Phase 2.
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED)
        todo = _task(task_id="T-000002", line_number=2)
        phase_one = _phase_with_tasks(
            tasks=(failed, todo), phase_id="phase_001", ordinal=1
        )
        phase_two = _phase_with_tasks(
            tasks=(_task(task_id="T-000099"),), phase_id="phase_002", ordinal=2
        )
        plan = _plan(phases=(phase_one, phase_two))
        assert next_tasks(plan) == [todo]

    def test_subsection_tasks_searched_within_phase(self) -> None:
        # Subsection tasks are part of the phase; once the root tasks
        # are exhausted, the walk continues into each subsection in
        # document order, all within the same phase scope.
        root = _task(task_id="T-000001", status=TaskStatus.DONE)
        sub_task = _task(task_id="T-000010", line_number=20, text="manual step")
        sub = Subsection(title="Manual", prose="", tasks=(sub_task,), line_number=19)
        phase = _phase_with_tasks(tasks=(root,), subsections=(sub,))
        assert next_tasks(_plan(phases=(phase,))) == [sub_task]

    # Leaf-before-parent ----------------------------------------------

    def test_descends_into_children_before_returning_parent(self) -> None:
        child = _task(task_id="T-000010", indent_level=2, line_number=2)
        parent = _task(task_id="T-000001", children=(child,))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        assert next_tasks(plan) == [child]

    def test_returns_parent_when_no_actionable_descendant(self) -> None:
        # All children DONE — mcloop's `_search_tasks` returns the
        # parent itself in this scenario (the auto-check would normally
        # promote it but in a hand-edited transient state the parent
        # may still be unchecked). Pin that fallthrough.
        done_child = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
        )
        parent = _task(task_id="T-000001", children=(done_child,))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        assert next_tasks(plan) == [parent]

    # Failed sibling blocking -----------------------------------------

    def test_failed_subtask_blocks_later_subtask_siblings(self) -> None:
        # Within a child list (is_subtask=True), a FAILED sibling stops
        # the walk; later TODO siblings under the same parent are
        # blocked (implicit sequential dependency, design doc 6
        # priority list item 3 + mcloop `_search_tasks` lines 369-376).
        failed = _task(
            task_id="T-000010",
            status=TaskStatus.FAILED,
            indent_level=2,
            line_number=2,
        )
        blocked = _task(task_id="T-000011", indent_level=2, line_number=3)
        parent = _task(task_id="T-000001", children=(failed, blocked))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        # Parent has FAILED child, so parent cannot complete; at root
        # level the parent's `continue` lets the walk move on, but
        # nothing else is here, so no actionable task remains.
        assert next_tasks(plan) == []

    def test_failed_root_task_does_not_block_siblings(self) -> None:
        # At the root level (is_subtask=False) FAILED is skipped, not
        # blocking — design doc 6 contract carries through mcloop's
        # `_search_tasks` distinction.
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED)
        todo = _task(task_id="T-000002", line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed, todo)),))
        assert next_tasks(plan) == [todo]

    def test_failed_subtask_preceded_by_todo_yields_todo(self) -> None:
        # Ordering matters: [TODO, FAILED] under one parent yields the
        # TODO (it was visited first) before the FAILED blocks anything
        # later. Pin so a future "FAILED scans the whole child list
        # first" optimization doesn't change observable behavior.
        first = _task(task_id="T-000010", indent_level=2, line_number=2)
        failed = _task(
            task_id="T-000011",
            status=TaskStatus.FAILED,
            indent_level=2,
            line_number=3,
        )
        parent = _task(task_id="T-000001", children=(first, failed))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        assert next_tasks(plan) == [first]

    # @deps -----------------------------------------------------------

    def test_dep_blocks_task_until_completed(self) -> None:
        # Phase A: dep is TODO, dependent task is blocked. The walk
        # returns the dep itself (it's actionable). Phase B: dep is
        # DONE, dependent unblocks and is the next actionable task.
        # Two Plan instances exercise the transition explicitly per
        # the task description ("at least one test where a task is
        # unblocked only after its dep is completed").
        dep_todo = _task(task_id="T-000001")
        dependent = _task(
            task_id="T-000002",
            deps=("T-000001",),
            line_number=2,
        )
        plan_blocked = _plan(phases=(_phase_with_tasks(tasks=(dep_todo, dependent)),))
        # The dep itself is actionable; the dependent is blocked.
        assert next_tasks(plan_blocked) == [dep_todo]
        # limit=5 must still not surface the blocked task.
        result_high = next_tasks(plan_blocked, limit=5)
        assert dependent not in result_high
        assert result_high == [dep_todo]

        dep_done = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan_unblocked = _plan(phases=(_phase_with_tasks(tasks=(dep_done, dependent)),))
        assert next_tasks(plan_unblocked) == [dependent]

    def test_unknown_dep_blocks_silently(self) -> None:
        # Per design doc section 6: unknown deps are validation errors,
        # not actionability blockers — next_tasks does not raise on
        # them but still treats the dep as unsatisfied (so the task
        # stays blocked). Callers run validate_plan to surface the
        # bad ref explicitly.
        dependent = _task(
            task_id="T-000002",
            deps=("T-NONEXISTENT",),
            line_number=2,
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(dependent,)),))
        assert next_tasks(plan) == []

    def test_dep_pointing_at_failed_task_still_blocks(self) -> None:
        # A FAILED dep is not DONE, so the dependent stays blocked.
        # The walk also skips the FAILED dep at root level, leaving
        # no actionable task.
        failed_dep = _task(task_id="T-000001", status=TaskStatus.FAILED)
        dependent = _task(
            task_id="T-000002",
            deps=("T-000001",),
            line_number=2,
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed_dep, dependent)),))
        assert next_tasks(plan) == []

    # BATCH parent surfacing ------------------------------------------

    def test_batch_parent_surfaces_as_single_unit(self) -> None:
        # A BATCH parent with two actionable children: next_tasks
        # returns the parent (not the leaf), and the returned parent
        # carries the batched children. limit=1 still yields just the
        # one BATCH unit even though it transitively covers two leaves.
        child1 = _task(task_id="T-000010", indent_level=2, line_number=2)
        child2 = _task(task_id="T-000011", indent_level=2, line_number=3)
        batch_parent = _task(
            task_id="T-000001",
            text="batch group",
            flag_tags=("BATCH",),
            children=(child1, child2),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(batch_parent,)),))
        result = next_tasks(plan)
        assert len(result) == 1
        surfaced = result[0]
        # Same identity (id, text, tags) as the parent; only children
        # are replaced with the batched set.
        assert surfaced.task_id == "T-000001"
        assert "BATCH" in surfaced.flag_tags
        assert surfaced.children == (child1, child2)

    def test_batch_stops_at_user_child(self) -> None:
        # get_batch_children halts at the first [USER] child; that
        # child is not included in the batch unit (it's a separate
        # halt-and-prompt). Mirrors mcloop's behavior.
        child1 = _task(task_id="T-000010", indent_level=2, line_number=2)
        child2 = _task(
            task_id="T-000011",
            indent_level=2,
            line_number=3,
            flag_tags=("USER",),
            text="manual verify",
        )
        child3 = _task(task_id="T-000012", indent_level=2, line_number=4)
        batch_parent = _task(
            task_id="T-000001",
            flag_tags=("BATCH",),
            children=(child1, child2, child3),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(batch_parent,)),))
        result = next_tasks(plan)
        assert len(result) == 1
        assert result[0].children == (child1,)

    def test_batch_stops_at_auto_child(self) -> None:
        # action_tag is the AUTO surface (per the parser), so a child
        # with action_tag != None halts batch collection.
        child1 = _task(task_id="T-000010", indent_level=2, line_number=2)
        child2 = _task(
            task_id="T-000011",
            indent_level=2,
            line_number=3,
            action_tag=("run_cli", "./scripts/x.sh"),
        )
        child3 = _task(task_id="T-000012", indent_level=2, line_number=4)
        batch_parent = _task(
            task_id="T-000001",
            flag_tags=("BATCH",),
            children=(child1, child2, child3),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(batch_parent,)),))
        result = next_tasks(plan)
        assert result[0].children == (child1,)

    def test_batch_skips_done_children(self) -> None:
        # Already-DONE children are skipped over but counted toward
        # seen_non_failed, matching mcloop's get_batch_children.
        done = _task(
            task_id="T-000010",
            indent_level=2,
            line_number=2,
            status=TaskStatus.DONE,
        )
        live1 = _task(task_id="T-000011", indent_level=2, line_number=3)
        live2 = _task(task_id="T-000012", indent_level=2, line_number=4)
        batch_parent = _task(
            task_id="T-000001",
            flag_tags=("BATCH",),
            children=(done, live1, live2),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(batch_parent,)),))
        result = next_tasks(plan)
        assert result[0].children == (live1, live2)

    def test_non_batch_parent_yields_leaf_not_parent(self) -> None:
        # Without the BATCH flag, the standard leaf-before-parent rule
        # applies — the child is returned directly, not the parent.
        child = _task(task_id="T-000010", indent_level=2, line_number=2)
        parent = _task(task_id="T-000001", children=(child,))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        assert next_tasks(plan) == [child]

    # Limit behavior --------------------------------------------------

    def test_limit_two_returns_two_independent_leaves(self) -> None:
        # Two unrelated TODO root tasks; limit=2 returns both, in
        # document order. limit=1 returns just the first.
        a = _task(task_id="T-000001")
        b = _task(task_id="T-000002", line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(a, b)),))
        assert next_tasks(plan, limit=2) == [a, b]
        assert next_tasks(plan, limit=1) == [a]


class TestCompleteTask:
    """``complete_task`` flips a task to DONE and reports settlements.

    Pins the kind policy from design doc section 5 (plain task →
    ``commit_landed``; AUTO action / USER → ``work_observed``; derived
    parent completion → ``"none"``) and the innermost-outward ordering
    of derived Settlements when an ancestor chain auto-completes.
    """

    def test_returns_done_plan_and_commit_landed_settlement(self) -> None:
        # Plain task (no AUTO action_tag, no USER flag) = commit-producing.
        target = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        new_plan, settlements = complete_task(plan, "T-000001")
        assert new_plan.phases[0].tasks[0].status == TaskStatus.DONE
        assert settlements == (
            Settlement(
                kind="commit_landed",
                task_id="T-000001",
                phase_id="phase_001",
                summary="do thing",
                failure_kind=None,
                ledger_event_required=True,
            ),
        )

    def test_input_plan_not_mutated(self) -> None:
        # Plan is a frozen dataclass — mutation is rejected by Python
        # at attribute-assignment time. Pin the observable property
        # instead: the original Plan's task statuses don't change.
        target = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        complete_task(plan, "T-000001")
        assert plan.phases[0].tasks[0].status == TaskStatus.TODO

    def test_user_task_settles_as_work_observed(self) -> None:
        # USER-flagged task: verified manually, no commit produced.
        target = _task(task_id="T-000001", flag_tags=("USER",), text="manual step")
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        _new_plan, settlements = complete_task(plan, "T-000001")
        assert settlements[0].kind == "work_observed"
        assert settlements[0].ledger_event_required is True

    def test_auto_action_task_settles_as_work_observed(self) -> None:
        # AUTO action tasks emit work observations rather than commits.
        target = _task(
            task_id="T-000001",
            action_tag=("run_cli", "./scripts/x.sh"),
            text="run script",
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        _new_plan, settlements = complete_task(plan, "T-000001")
        assert settlements[0].kind == "work_observed"

    def test_unknown_task_id_raises(self) -> None:
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="T-000999"):
            complete_task(plan, "T-000999")

    def test_parent_auto_completes_when_all_children_done(self) -> None:
        # Completing the last unchecked child of a parent: derived
        # Settlement for the parent comes after the direct settlement.
        done_child = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
        )
        live_child = _task(task_id="T-000011", indent_level=2, line_number=3)
        parent = _task(task_id="T-000001", children=(done_child, live_child))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan, settlements = complete_task(plan, "T-000011")
        assert len(settlements) == 2
        # Direct first.
        assert settlements[0].task_id == "T-000011"
        assert settlements[0].kind == "commit_landed"
        # Derived parent next.
        assert settlements[1].task_id == "T-000001"
        assert settlements[1].kind == "none"
        assert settlements[1].ledger_event_required is False
        # Plan reflects the parent's auto-completion.
        assert new_plan.phases[0].tasks[0].status == TaskStatus.DONE

    def test_parent_not_auto_completed_when_sibling_still_todo(self) -> None:
        # A parent stays TODO when at least one sibling of the completed
        # child remains incomplete. Only the direct Settlement returns.
        live_a = _task(task_id="T-000010", indent_level=2, line_number=2)
        live_b = _task(task_id="T-000011", indent_level=2, line_number=3)
        parent = _task(task_id="T-000001", children=(live_a, live_b))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan, settlements = complete_task(plan, "T-000010")
        assert len(settlements) == 1
        assert settlements[0].task_id == "T-000010"
        assert new_plan.phases[0].tasks[0].status == TaskStatus.TODO

    def test_chain_of_two_batch_parents_yields_three_settlements(self) -> None:
        # Task description spec: completing the last unchecked child of
        # a chain of two BATCH parents returns three Settlements
        # (direct + two derived) in innermost-outward order. The cascade
        # is parent-state-driven (any all-DONE parent auto-completes);
        # BATCH-ness is the surfacing strategy, not the cascade trigger.
        leaf_done = _task(
            task_id="T-000100",
            status=TaskStatus.DONE,
            indent_level=4,
            line_number=4,
        )
        leaf_live = _task(
            task_id="T-000101",
            indent_level=4,
            line_number=5,
        )
        inner = _task(
            task_id="T-000010",
            text="inner batch",
            flag_tags=("BATCH",),
            indent_level=2,
            line_number=3,
            children=(leaf_done, leaf_live),
        )
        outer = _task(
            task_id="T-000001",
            text="outer batch",
            flag_tags=("BATCH",),
            children=(inner,),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(outer,)),))
        new_plan, settlements = complete_task(plan, "T-000101")
        assert len(settlements) == 3
        # Direct first (commit_landed because the leaf is plain).
        assert settlements[0].task_id == "T-000101"
        assert settlements[0].kind == "commit_landed"
        assert settlements[0].ledger_event_required is True
        # Innermost derived ancestor: the inner BATCH parent.
        assert settlements[1].task_id == "T-000010"
        assert settlements[1].kind == "none"
        assert settlements[1].ledger_event_required is False
        # Outermost derived ancestor: the outer BATCH parent.
        assert settlements[2].task_id == "T-000001"
        assert settlements[2].kind == "none"
        assert settlements[2].ledger_event_required is False
        # Both parents reflect their auto-completion in the new plan.
        assert new_plan.phases[0].tasks[0].status == TaskStatus.DONE
        assert new_plan.phases[0].tasks[0].children[0].status == TaskStatus.DONE

    def test_cascade_does_not_repeat_already_done_ancestor(self) -> None:
        # A parent whose status was already DONE before this call does
        # not yield a derived Settlement when its (now-DONE) children
        # would have triggered the cascade — the parent didn't "become"
        # complete on this call, it was already there.
        leaf_done = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
        )
        leaf_live = _task(task_id="T-000011", indent_level=2, line_number=3)
        # Parent is DONE (inconsistent hand-edit state — children include
        # a TODO leaf but the parent box is already checked).
        parent = _task(
            task_id="T-000001",
            status=TaskStatus.DONE,
            children=(leaf_done, leaf_live),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        _new_plan, settlements = complete_task(plan, "T-000011")
        assert len(settlements) == 1
        assert settlements[0].task_id == "T-000011"

    def test_completes_task_in_bugs_section(self) -> None:
        # Bug tasks have no containing phase; phase_id on the
        # Settlement stays None.
        bug = _task(task_id="T-000900", line_number=100, text="kernel panic")
        plan = _plan(bugs=BugsSection(tasks=(bug,), line_number=99))
        new_plan, settlements = complete_task(plan, "T-000900")
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.DONE
        assert settlements[0].phase_id is None
        assert settlements[0].task_id == "T-000900"

    def test_completes_subsection_task(self) -> None:
        # A task inside a subsection lives in the parent phase for
        # phase_id purposes; the cascade does not promote subsections
        # (they have no status), but the immediate-parent check still
        # works against root-level subsection tasks (no children, no
        # cascade).
        sub_task = _task(task_id="T-000010", line_number=20, text="manual step")
        sub = Subsection(title="Manual", prose="", tasks=(sub_task,), line_number=19)
        phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), subsections=(sub,)
        )
        plan = _plan(phases=(phase,))
        new_plan, settlements = complete_task(plan, "T-000010")
        assert new_plan.phases[0].subsections[0].tasks[0].status == TaskStatus.DONE
        assert settlements[0].phase_id == "phase_001"


class TestFailTask:
    """``fail_task`` flips a task to FAILED with a ``test_failed`` Settlement."""

    def test_marks_failed_and_returns_test_failed_settlement(self) -> None:
        target = _task(task_id="T-000001", text="run thing")
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        new_plan, settlements = fail_task(plan, "T-000001", reason="exit 1 from runner")
        assert new_plan.phases[0].tasks[0].status == TaskStatus.FAILED
        assert settlements == (
            Settlement(
                kind="test_failed",
                task_id="T-000001",
                phase_id="phase_001",
                summary="exit 1 from runner",
                failure_kind="max_retries_exceeded",
                ledger_event_required=True,
            ),
        )

    def test_outcome_failure_kind_overrides_default(self) -> None:
        target = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        _new_plan, settlements = fail_task(
            plan,
            "T-000001",
            reason="hook failed",
            outcome=Outcome(failure_kind="precommit_hook"),
        )
        assert settlements[0].failure_kind == "precommit_hook"

    def test_does_not_cascade_into_parent(self) -> None:
        # Failing a child must not auto-complete the parent, even when
        # every other child is DONE. Per design doc section 5: a failed
        # child leaves its ancestors stuck, not done.
        done_sibling = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
        )
        failing = _task(task_id="T-000011", indent_level=2, line_number=3)
        parent = _task(task_id="T-000001", children=(done_sibling, failing))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan, settlements = fail_task(plan, "T-000011", reason="bad exit")
        assert len(settlements) == 1
        assert new_plan.phases[0].tasks[0].status == TaskStatus.TODO

    def test_unknown_task_id_raises(self) -> None:
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="T-000999"):
            fail_task(plan, "T-000999", reason="missing")


class TestResetTask:
    """``reset_task`` flips FAILED → TODO with a ``none``-kind Settlement."""

    def test_failed_to_todo_with_none_settlement(self) -> None:
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED, text="retry me")
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed,)),))
        new_plan, settlements = reset_task(plan, "T-000001")
        assert new_plan.phases[0].tasks[0].status == TaskStatus.TODO
        assert settlements == (
            Settlement(
                kind="none",
                task_id="T-000001",
                phase_id="phase_001",
                summary="retry me",
                failure_kind=None,
                ledger_event_required=False,
            ),
        )

    def test_unknown_task_id_raises(self) -> None:
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="T-000999"):
            reset_task(plan, "T-000999")


class TestClearFailed:
    """``clear_failed`` bulk-resets FAILED tasks without settlements."""

    def test_phase_task_failed_to_todo(self) -> None:
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed,)),))
        new_plan = clear_failed(plan)
        assert new_plan.phases[0].tasks[0].status == TaskStatus.TODO

    def test_subsection_task_failed_to_todo(self) -> None:
        failed = _task(task_id="T-000010", status=TaskStatus.FAILED)
        subsection = Subsection(
            title="Manual",
            prose="",
            tasks=(failed,),
            line_number=9,
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(), subsections=(subsection,)),))
        new_plan = clear_failed(plan)
        assert new_plan.phases[0].subsections[0].tasks[0].status == TaskStatus.TODO

    def test_bugs_section_task_failed_to_todo(self) -> None:
        bug = _task(task_id="T-000900", status=TaskStatus.FAILED)
        plan = _plan(bugs=BugsSection(tasks=(bug,), line_number=20))
        new_plan = clear_failed(plan)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_nested_subtask_failed_to_todo(self) -> None:
        failed_child = _task(
            task_id="T-000011",
            status=TaskStatus.FAILED,
            indent_level=2,
            line_number=2,
        )
        parent = _task(task_id="T-000001", children=(failed_child,))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan = clear_failed(plan)
        assert new_plan.phases[0].tasks[0].status == TaskStatus.TODO
        assert new_plan.phases[0].tasks[0].children[0].status == TaskStatus.TODO

    def test_mixed_statuses_only_failed_flip(self) -> None:
        todo = _task(task_id="T-000001", status=TaskStatus.TODO, line_number=1)
        done = _task(task_id="T-000002", status=TaskStatus.DONE, line_number=2)
        failed = _task(task_id="T-000003", status=TaskStatus.FAILED, line_number=3)
        plan = _plan(phases=(_phase_with_tasks(tasks=(todo, done, failed)),))
        new_plan = clear_failed(plan)
        statuses = tuple(task.status for task in new_plan.phases[0].tasks)
        assert statuses == (TaskStatus.TODO, TaskStatus.DONE, TaskStatus.TODO)

    def test_idempotent(self) -> None:
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed,)),))
        once = clear_failed(plan)
        twice = clear_failed(once)
        assert twice == once

    def test_input_plan_not_mutated(self) -> None:
        failed = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(failed,)),))
        clear_failed(plan)
        assert plan.phases[0].tasks[0].status == TaskStatus.FAILED

    def test_no_failed_no_op_returns_equivalent_new_plan(self) -> None:
        todo = _task(task_id="T-000001", status=TaskStatus.TODO)
        done = _task(task_id="T-000002", status=TaskStatus.DONE, line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(todo, done)),))
        new_plan = clear_failed(plan)
        assert new_plan == plan
        assert new_plan is not plan


class TestPurgeDoneBugTasks:
    """``purge_done_bug_tasks`` removes DONE bug tasks without settlements."""

    def test_empty_bugs_section_no_op(self) -> None:
        plan = _plan(bugs=BugsSection(tasks=(), line_number=10))
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan == plan
        assert new_plan is not plan

    def test_all_done_bugs_removed(self) -> None:
        first = _task(task_id="T-000900", status=TaskStatus.DONE, text="fixed one")
        second = _task(task_id="T-000901", status=TaskStatus.DONE, text="fixed two")
        plan = _plan(bugs=BugsSection(tasks=(first, second), line_number=20))
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks == ()

    def test_mixed_statuses_only_done_filtered(self) -> None:
        done = _task(task_id="T-000900", status=TaskStatus.DONE, text="fixed")
        todo = _task(task_id="T-000901", status=TaskStatus.TODO, text="open")
        failed = _task(task_id="T-000902", status=TaskStatus.FAILED, text="failed")
        plan = _plan(bugs=BugsSection(tasks=(done, todo, failed), line_number=20))
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks == (todo, failed)

    def test_non_done_bug_retains_parent_and_filters_done_child(self) -> None:
        done_child = _task(
            task_id="T-000901",
            status=TaskStatus.DONE,
            text="fixed child",
            indent_level=1,
        )
        open_child = _task(
            task_id="T-000902",
            status=TaskStatus.TODO,
            text="open child",
            indent_level=1,
        )
        parent = _task(
            task_id="T-000900",
            status=TaskStatus.TODO,
            text="open parent",
            children=(done_child, open_child),
        )
        plan = _plan(bugs=BugsSection(tasks=(parent,), line_number=20))
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].text == "open parent"
        assert new_plan.bugs.tasks[0].children == (open_child,)

    def test_done_parent_drops_subtree(self) -> None:
        open_child = _task(
            task_id="T-000901",
            status=TaskStatus.TODO,
            text="unreachable open child",
            indent_level=1,
        )
        parent = _task(
            task_id="T-000900",
            status=TaskStatus.DONE,
            text="done parent",
            children=(open_child,),
        )
        plan = _plan(bugs=BugsSection(tasks=(parent,), line_number=20))
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks == ()

    def test_idempotent(self) -> None:
        done = _task(task_id="T-000900", status=TaskStatus.DONE, text="fixed")
        todo = _task(task_id="T-000901", status=TaskStatus.TODO, text="open")
        plan = _plan(bugs=BugsSection(tasks=(done, todo), line_number=20))
        once = purge_done_bug_tasks(plan)
        twice = purge_done_bug_tasks(once)
        assert twice == once

    def test_phase_tasks_untouched(self) -> None:
        done_phase = _task(task_id="T-000001", status=TaskStatus.DONE, text="phase")
        done_bug = _task(task_id="T-000900", status=TaskStatus.DONE, text="bug")
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(done_phase,)),),
            bugs=BugsSection(tasks=(done_bug,), line_number=20),
        )
        new_plan = purge_done_bug_tasks(plan)
        assert new_plan.phases[0].tasks == (done_phase,)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks == ()


class TestAddTask:
    """``add_task`` appends new tasks with globally-unique sequential IDs."""

    def test_appends_to_phase_root_tasks(self) -> None:
        existing = _task(task_id="T-000001", text="first")
        plan = _plan(phases=(_phase_with_tasks(tasks=(existing,)),))
        new_plan = add_task(plan, "phase_001", text="second")
        added = new_plan.phases[0].tasks[1]
        assert added.text == "second"
        assert added.status == TaskStatus.TODO
        # Existing task untouched.
        assert new_plan.phases[0].tasks[0].task_id == "T-000001"

    def test_assigns_next_sequential_id(self) -> None:
        a = _task(task_id="T-000003")
        b = _task(task_id="T-000007", line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(a, b)),))
        new_plan = add_task(plan, "phase_001", text="next")
        assert new_plan.phases[0].tasks[-1].task_id == "T-000008"

    def test_assigns_first_id_when_plan_is_empty(self) -> None:
        # An empty phase (no existing T-NNNNNN tasks): the first id is
        # T-000001, not T-000000.
        plan = _plan(phases=(_phase_with_tasks(tasks=()),))
        new_plan = add_task(plan, "phase_001", text="first")
        assert new_plan.phases[0].tasks[0].task_id == "T-000001"

    def test_id_search_covers_nested_and_bug_tasks(self) -> None:
        # Max-id scan must descend into children and the bugs section,
        # otherwise a deeply nested or bug-scope id would be re-issued
        # to the new task and break global uniqueness.
        deep = _task(task_id="T-000050", indent_level=2, line_number=10)
        parent = _task(task_id="T-000005", children=(deep,))
        bug = _task(task_id="T-000200", line_number=100)
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(parent,)),),
            bugs=BugsSection(tasks=(bug,), line_number=99),
        )
        new_plan = add_task(plan, "phase_001", text="next")
        assert new_plan.phases[0].tasks[-1].task_id == "T-000201"

    def test_nests_under_parent_id(self) -> None:
        parent = _task(task_id="T-000001", text="parent")
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan = add_task(plan, "phase_001", text="child", parent_id="T-000001")
        children = new_plan.phases[0].tasks[0].children
        assert len(children) == 1
        assert children[0].text == "child"
        assert children[0].task_id == "T-000002"

    def test_nests_under_subsection_parent(self) -> None:
        # parent_id may resolve inside a subsection; add_task must fall
        # through phase root tasks before searching subsections.
        sub_parent = _task(task_id="T-000010", line_number=20, text="sub parent")
        sub = Subsection(title="Manual", prose="", tasks=(sub_parent,), line_number=19)
        phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), subsections=(sub,)
        )
        plan = _plan(phases=(phase,))
        new_plan = add_task(
            plan, "phase_001", text="manual sub-step", parent_id="T-000010"
        )
        assert (
            new_plan.phases[0].subsections[0].tasks[0].children[-1].text
            == "manual sub-step"
        )

    def test_unknown_phase_raises(self) -> None:
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="phase_999"):
            add_task(plan, "phase_999", text="anywhere")

    def test_unknown_parent_in_phase_raises(self) -> None:
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="T-000999"):
            add_task(plan, "phase_001", text="orphan", parent_id="T-000999")

    def test_deps_propagated_to_new_task(self) -> None:
        dep = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(dep,)),))
        new_plan = add_task(plan, "phase_001", text="next", deps=("T-000001",))
        assert new_plan.phases[0].tasks[-1].deps == ("T-000001",)


class TestAddPhaseTask:
    """``add_phase_task`` per v4 Contract 6.

    Pins root append, parent-id nesting (including subsection parents),
    subsection-title append, sequential id assignment, caller-supplied
    id honoring, the ``(plan, assigned_id)`` return shape, and the
    PlanValidationError surface (unknown phase, parent, or subsection;
    invalid task; duplicate id; unknown deps; mutually exclusive
    placement keywords).
    """

    def _plan_constructed(self, *phases: Phase) -> Plan:
        return Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=phases,
            bugs=None,
            source_path=None,
        )

    def _task(self, text: str, **kwargs: Any) -> Task:
        annotations = kwargs.pop("annotations", (("accept", "pytest"),))
        return make_task(text, annotations=annotations, **kwargs)

    def test_appends_to_phase_root_when_no_placement_given(self) -> None:
        existing = self._task("first", task_id="T-000001")
        plan = self._plan_constructed(_phase_with_tasks(tasks=(existing,)))
        new_plan, assigned_id = add_phase_task(plan, "phase_001", self._task("second"))
        assert assigned_id == "T-000002"
        assert [t.text for t in new_plan.phases[0].tasks] == ["first", "second"]
        assert new_plan.phases[0].tasks[1].task_id == "T-000002"

    def test_nests_under_parent_id_in_phase_root(self) -> None:
        parent = self._task("parent", task_id="T-000001")
        plan = self._plan_constructed(_phase_with_tasks(tasks=(parent,)))
        new_plan, assigned_id = add_phase_task(
            plan, "phase_001", self._task("child"), parent_id="T-000001"
        )
        assert assigned_id == "T-000002"
        nested = new_plan.phases[0].tasks[0].children
        assert len(nested) == 1
        assert nested[0].text == "child"
        assert nested[0].task_id == "T-000002"

    def test_nests_under_parent_id_in_subsection(self) -> None:
        # parent_id resolution must fall through to subsection tasks
        # after exhausting phase root tasks.
        sub_parent = self._task("sub parent", task_id="T-000010")
        sub = Subsection(title="Manual", prose="", tasks=(sub_parent,), line_number=0)
        phase = _phase_with_tasks(
            tasks=(self._task("root", task_id="T-000001"),),
            subsections=(sub,),
        )
        plan = self._plan_constructed(phase)
        new_plan, assigned_id = add_phase_task(
            plan, "phase_001", self._task("manual step"), parent_id="T-000010"
        )
        assert assigned_id == "T-000011"
        sub_after = new_plan.phases[0].subsections[0]
        assert sub_after.tasks[0].children[-1].text == "manual step"
        assert sub_after.tasks[0].children[-1].task_id == "T-000011"

    def test_appends_to_named_subsection_root(self) -> None:
        sub_existing = self._task("sub first", task_id="T-000010")
        sub = Subsection(title="Manual", prose="", tasks=(sub_existing,), line_number=0)
        phase = _phase_with_tasks(
            tasks=(self._task("root", task_id="T-000001"),),
            subsections=(sub,),
        )
        plan = self._plan_constructed(phase)
        new_plan, assigned_id = add_phase_task(
            plan,
            "phase_001",
            self._task("sub second"),
            subsection_title="Manual",
        )
        assert assigned_id == "T-000011"
        sub_after = new_plan.phases[0].subsections[0]
        assert [t.text for t in sub_after.tasks] == ["sub first", "sub second"]
        assert sub_after.tasks[-1].task_id == "T-000011"
        # Phase root tasks untouched.
        assert [t.text for t in new_plan.phases[0].tasks] == ["root"]

    def test_returns_tuple_of_new_plan_and_assigned_id(self) -> None:
        plan = self._plan_constructed(_phase_with_tasks(tasks=()))
        result = add_phase_task(plan, "phase_001", self._task("only"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        new_plan, assigned_id = result
        assert isinstance(new_plan, Plan)
        assert isinstance(assigned_id, str)
        assert new_plan is not plan

    def test_assigns_id_based_on_global_max_plus_one(self) -> None:
        a = self._task("a", task_id="T-000003")
        b = self._task("b", task_id="T-000007")
        plan = self._plan_constructed(_phase_with_tasks(tasks=(a, b)))
        _, assigned_id = add_phase_task(plan, "phase_001", self._task("c"))
        assert assigned_id == "T-000008"

    def test_caller_supplied_task_id_is_honored(self) -> None:
        plan = self._plan_constructed(_phase_with_tasks(tasks=()))
        task = self._task("explicit", task_id="T-000777")
        new_plan, assigned_id = add_phase_task(plan, "phase_001", task)
        assert assigned_id == "T-000777"
        assert new_plan.phases[0].tasks[0].task_id == "T-000777"

    def test_unknown_phase_raises_validation_error(self) -> None:
        plan = self._plan_constructed(_phase_with_tasks(tasks=()))
        with pytest.raises(PlanValidationError) as exc_info:
            add_phase_task(plan, "phase_999", self._task("x"))
        assert any("phase_999" in m for m in exc_info.value.messages)

    def test_unknown_parent_id_raises_validation_error(self) -> None:
        plan = self._plan_constructed(
            _phase_with_tasks(tasks=(self._task("only", task_id="T-000001"),))
        )
        with pytest.raises(PlanValidationError) as exc_info:
            add_phase_task(
                plan,
                "phase_001",
                self._task("orphan"),
                parent_id="T-000999",
            )
        assert any("T-000999" in m for m in exc_info.value.messages)

    def test_unknown_subsection_raises_validation_error(self) -> None:
        plan = self._plan_constructed(_phase_with_tasks(tasks=()))
        with pytest.raises(PlanValidationError) as exc_info:
            add_phase_task(
                plan,
                "phase_001",
                self._task("x"),
                subsection_title="No Such Section",
            )
        assert any("No Such Section" in m for m in exc_info.value.messages)

    def test_parent_id_and_subsection_title_mutually_exclusive(self) -> None:
        sub = Subsection(
            title="Manual",
            prose="",
            tasks=(self._task("inside", task_id="T-000010"),),
            line_number=0,
        )
        phase = _phase_with_tasks(
            tasks=(self._task("root", task_id="T-000001"),),
            subsections=(sub,),
        )
        plan = self._plan_constructed(phase)
        with pytest.raises(PlanValidationError):
            add_phase_task(
                plan,
                "phase_001",
                self._task("x"),
                parent_id="T-000010",
                subsection_title="Manual",
            )

    def test_caller_supplied_duplicate_id_raises_validation_error(self) -> None:
        plan = self._plan_constructed(
            _phase_with_tasks(tasks=(self._task("first", task_id="T-000001"),))
        )
        with pytest.raises(PlanValidationError) as exc_info:
            add_phase_task(plan, "phase_001", self._task("dup", task_id="T-000001"))
        assert any("T-000001" in m for m in exc_info.value.messages)

    def test_unknown_dep_raises_validation_error(self) -> None:
        plan = self._plan_constructed(
            _phase_with_tasks(tasks=(self._task("first", task_id="T-000001"),))
        )
        bad = self._task("with dep", deps=("T-009999",))
        with pytest.raises(PlanValidationError) as exc_info:
            add_phase_task(plan, "phase_001", bad)
        assert any("T-009999" in m for m in exc_info.value.messages)

    def test_invalid_task_raises_validation_error(self) -> None:
        # A task hand-built with an embedded newline in its text breaks
        # Stage 10 field-stability; add_phase_task must reject it.
        bad = Task(
            task_id=None,
            text="bad\ntext",
            status=TaskStatus.TODO,
            flag_tags=(),
            action_tag=None,
            annotations=(("accept", "pytest"),),
            deps=(),
            children=(),
            ruled_out=(),
            indent_level=0,
            line_number=0,
        )
        plan = self._plan_constructed(_phase_with_tasks(tasks=()))
        with pytest.raises(PlanValidationError):
            add_phase_task(plan, "phase_001", bad)


class TestCreatedAt:
    """``Task.created_at`` round-trips and is populated by add operations.

    The field is serialized on the task line as an HTML-comment
    annotation (``<!-- created_at: ... -->``) after any bracketed
    annotations. Round-trip equality through ``render_plan`` and
    ``parse_plan`` is the parser/renderer contract; ``add_task`` and
    ``add_phase_task`` stamp newly-added tasks with the current UTC
    instant in ISO 8601 form so the timestamp is available to
    downstream consumers without each caller threading its own clock.
    """

    _ISO_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"

    def test_parser_extracts_created_at_html_comment(self) -> None:
        source = (
            "# Project\n\n"
            "## Stage 1: Core\n"
            "<!-- phase_id: phase_001 -->\n\n"
            "- [ ] T-000001: hello <!-- created_at: 2026-05-26T12:34:56Z -->\n"
        )
        from bob_tools.planfile.parser import parse_plan

        plan = parse_plan(source)
        task = plan.phases[0].tasks[0]
        assert task.created_at == "2026-05-26T12:34:56Z"
        assert task.text == "hello"

    def test_round_trip_preserves_created_at(self) -> None:
        task = make_task(
            "hello",
            annotations=(("accept", "pytest"),),
            created_at="2026-05-26T12:34:56Z",
        )
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(
                _phase_with_tasks(
                    tasks=(
                        make_task(
                            "anchor",
                            task_id="T-000001",
                            annotations=(("accept", "pytest"),),
                        ),
                    ),
                ),
            ),
            bugs=None,
            source_path=None,
        )
        new_plan, _ = add_phase_task(plan, "phase_001", task)
        rendered = render_plan(new_plan)
        assert "<!-- created_at: 2026-05-26T12:34:56Z -->" in rendered
        from bob_tools.planfile.parser import parse_plan

        reparsed = parse_plan(rendered)
        appended = reparsed.phases[0].tasks[1]
        assert appended.created_at == "2026-05-26T12:34:56Z"
        assert appended.text == "hello"

    def test_round_trip_with_annotation_and_created_at(self) -> None:
        task = make_task(
            "build menu",
            annotations=(("feat", "menu wired"),),
            created_at="2026-05-26T12:34:56Z",
        )
        # field-stability oracle already ran inside make_task; this
        # asserts the rendered ordering puts the annotation before the
        # HTML comment so the parser's right-to-left annotation scan
        # sees an unobstructed closing bracket.
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(_phase_with_tasks(tasks=(task,)),),
            bugs=None,
            source_path=None,
        )
        rendered = render_plan(plan)
        line = next(ln for ln in rendered.splitlines() if "build menu" in ln)
        assert "[feat: menu wired]" in line
        assert "<!-- created_at: 2026-05-26T12:34:56Z -->" in line
        assert line.index("[feat: menu wired]") < line.index("<!-- created_at:")

    def test_add_task_populates_created_at(self) -> None:
        import re as _re

        plan = _plan(phases=(_phase_with_tasks(tasks=()),))
        new_plan = add_task(plan, "phase_001", text="fresh")
        appended = new_plan.phases[0].tasks[-1]
        assert appended.created_at is not None
        assert _re.fullmatch(self._ISO_RE, appended.created_at) is not None

    def test_add_phase_task_populates_created_at_when_unset(self) -> None:
        import re as _re

        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(_phase_with_tasks(tasks=()),),
            bugs=None,
            source_path=None,
        )
        new_plan, _ = add_phase_task(
            plan,
            "phase_001",
            make_task("fresh", annotations=(("accept", "pytest"),)),
        )
        appended = new_plan.phases[0].tasks[-1]
        assert appended.created_at is not None
        assert _re.fullmatch(self._ISO_RE, appended.created_at) is not None

    def test_add_phase_task_preserves_caller_supplied_created_at(self) -> None:
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(_phase_with_tasks(tasks=()),),
            bugs=None,
            source_path=None,
        )
        task = make_task(
            "imported",
            annotations=(("accept", "pytest"),),
            created_at="2024-01-01T00:00:00Z",
        )
        new_plan, _ = add_phase_task(plan, "phase_001", task)
        appended = new_plan.phases[0].tasks[-1]
        assert appended.created_at == "2024-01-01T00:00:00Z"


class TestCompletedAt:
    """``Task.completed_at`` round-trips and is stamped at checkoff.

    The field is serialized on the task line as an HTML-comment
    annotation (``<!-- completed_at: ... -->``) after ``created_at``.
    ``complete_task`` stamps it with the current UTC instant on the
    TODO/FAILED -> DONE transition (including derived parent completion);
    ``fail_task`` and ``reset_task`` clear it. Re-completing an already
    DONE task preserves the original checkoff time.
    """

    _ISO_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
    _FIXED = "2026-05-29T08:00:00Z"

    @pytest.fixture
    def _frozen_clock(self, monkeypatch: pytest.MonkeyPatch) -> str:
        # complete_task looks up _now_iso_utc in the status module
        # namespace; patch it there so the stamped value is deterministic.
        monkeypatch.setattr(
            "bob_tools.planfile.status._now_iso_utc", lambda: self._FIXED
        )
        return self._FIXED

    def test_parser_extracts_completed_at_html_comment(self) -> None:
        source = (
            "# Project\n\n"
            "## Stage 1: Core\n"
            "<!-- phase_id: phase_001 -->\n\n"
            "- [x] T-000001: hello "
            "<!-- created_at: 2026-05-26T12:34:56Z --> "
            "<!-- completed_at: 2026-05-28T09:00:00Z -->\n"
        )
        from bob_tools.planfile.parser import parse_plan

        plan = parse_plan(source)
        task = plan.phases[0].tasks[0]
        assert task.created_at == "2026-05-26T12:34:56Z"
        assert task.completed_at == "2026-05-28T09:00:00Z"
        assert task.text == "hello"

    def test_round_trip_preserves_both_timestamps_and_order(self) -> None:
        task = make_task(
            "ship it",
            task_id="T-000001",
            status=TaskStatus.DONE,
            created_at="2026-05-26T12:34:56Z",
            completed_at="2026-05-28T09:00:00Z",
        )
        plan = Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=(_phase_with_tasks(tasks=(task,)),),
            bugs=None,
            source_path=None,
        )
        rendered = render_plan(plan)
        line = next(ln for ln in rendered.splitlines() if "ship it" in ln)
        assert "<!-- created_at: 2026-05-26T12:34:56Z -->" in line
        assert "<!-- completed_at: 2026-05-28T09:00:00Z -->" in line
        # completed_at renders after created_at so canonical form is stable.
        assert line.index("<!-- created_at:") < line.index("<!-- completed_at:")
        from bob_tools.planfile.parser import parse_plan

        reparsed = parse_plan(rendered)
        rt = reparsed.phases[0].tasks[0]
        assert rt.created_at == "2026-05-26T12:34:56Z"
        assert rt.completed_at == "2026-05-28T09:00:00Z"

    def test_completed_at_alone_round_trips(self) -> None:
        # A DONE task with completed_at but no created_at (the
        # completed_at comment is then the trailing-most) must still
        # parse cleanly.
        task = make_task(
            "loose", status=TaskStatus.DONE, completed_at="2026-05-28T09:00:00Z"
        )
        assert task.completed_at == "2026-05-28T09:00:00Z"
        assert task.created_at is None

    def test_complete_task_stamps_completed_at(self, _frozen_clock: str) -> None:
        import re as _re

        task = _task(task_id="T-000001")
        plan = _plan(phases=(_phase_with_tasks(tasks=(task,)),))
        new_plan, _ = complete_task(plan, "T-000001")
        done = new_plan.phases[0].tasks[0]
        assert done.status == TaskStatus.DONE
        assert done.completed_at == _frozen_clock
        assert _re.fullmatch(self._ISO_RE, done.completed_at) is not None

    def test_parent_auto_completion_stamps_completed_at(
        self, _frozen_clock: str
    ) -> None:
        done_child = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
            # An already-DONE child predating the field stays null until
            # touched; completing its sibling must not retro-stamp it.
        )
        live_child = _task(task_id="T-000011", indent_level=2, line_number=3)
        parent = _task(task_id="T-000001", children=(done_child, live_child))
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        new_plan, settlements = complete_task(plan, "T-000011")
        assert len(settlements) == 2
        new_parent = new_plan.phases[0].tasks[0]
        assert new_parent.status == TaskStatus.DONE
        # The parent auto-completed, so it carries the checkoff stamp.
        assert new_parent.completed_at == _frozen_clock
        # The directly-completed child is stamped too.
        completed_child = new_parent.children[1]
        assert completed_child.task_id == "T-000011"
        assert completed_child.completed_at == _frozen_clock

    def test_recompletion_preserves_original_completed_at(self) -> None:
        original = make_task(
            "already done",
            task_id="T-000001",
            status=TaskStatus.DONE,
            completed_at="2020-01-01T00:00:00Z",
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(original,)),))
        new_plan, _ = complete_task(plan, "T-000001")
        # Idempotent: the original checkoff instant is not overwritten.
        assert new_plan.phases[0].tasks[0].completed_at == "2020-01-01T00:00:00Z"

    def test_backfill_on_touch_stamps_done_task_missing_completed_at(
        self, _frozen_clock: str
    ) -> None:
        # A DONE task with no completed_at gets stamped when re-completed.
        done = _task(task_id="T-000001", status=TaskStatus.DONE)
        assert done.completed_at is None
        plan = _plan(phases=(_phase_with_tasks(tasks=(done,)),))
        new_plan, _ = complete_task(plan, "T-000001")
        assert new_plan.phases[0].tasks[0].completed_at == _frozen_clock

    def test_fail_task_clears_completed_at(self) -> None:
        done = make_task(
            "regressed",
            task_id="T-000001",
            status=TaskStatus.DONE,
            completed_at="2026-05-28T09:00:00Z",
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(done,)),))
        new_plan, _ = fail_task(plan, "T-000001", reason="broke")
        failed = new_plan.phases[0].tasks[0]
        assert failed.status == TaskStatus.FAILED
        assert failed.completed_at is None

    def test_reset_task_clears_completed_at(self) -> None:
        done = make_task(
            "redo",
            task_id="T-000001",
            status=TaskStatus.DONE,
            completed_at="2026-05-28T09:00:00Z",
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(done,)),))
        new_plan, _ = reset_task(plan, "T-000001")
        reset = new_plan.phases[0].tasks[0]
        assert reset.status == TaskStatus.TODO
        assert reset.completed_at is None


class TestReplacePhase:
    """``replace_phase`` substitutes a whole Phase object by id."""

    def test_replaces_named_phase_with_new_phase_object(self) -> None:
        original = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000050", text="reauthored"),),
            phase_id="phase_001",
        )
        new_plan = replace_phase(_plan(phases=(original,)), "phase_001", new_phase)
        assert new_plan.phases[0] is new_phase

    def test_other_phases_preserved(self) -> None:
        keep_first = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        replace_target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        keep_last = _phase_with_tasks(
            tasks=(_task(task_id="T-000003"),),
            phase_id="phase_003",
            ordinal=3,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000099", text="fresh"),),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(keep_first, replace_target, keep_last))
        new_plan = replace_phase(plan, "phase_002", new_phase)
        # Untouched phases keep identity.
        assert new_plan.phases[0] is keep_first
        assert new_plan.phases[2] is keep_last
        assert new_plan.phases[1] is new_phase

    def test_bugs_section_unchanged(self) -> None:
        bugs = BugsSection(
            tasks=(_task(task_id="T-000900", line_number=99),),
            line_number=98,
        )
        plan = _plan(
            phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),),
            bugs=bugs,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),), phase_id="phase_001"
        )
        new_plan = replace_phase(plan, "phase_001", new_phase)
        assert new_plan.bugs is bugs

    def test_unknown_phase_raises(self) -> None:
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_999"
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(_task(task_id="T-000001"),)),))
        with pytest.raises(ValueError, match="phase_999"):
            replace_phase(plan, "phase_999", new_phase)


class TestReplacePhaseValidated:
    """``replace_phase_validated`` per v4 Contract 3."""

    def test_replaces_in_place_preserving_other_phases(self) -> None:
        keep_first = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        replace_target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        keep_last = _phase_with_tasks(
            tasks=(_task(task_id="T-000003"),),
            phase_id="phase_003",
            ordinal=3,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000099", text="fresh"),),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(keep_first, replace_target, keep_last))
        new_plan = replace_phase_validated(plan, "phase_002", new_phase)
        assert new_plan.phases[0] is keep_first
        assert new_plan.phases[2] is keep_last
        assert new_plan.phases[1].phase_id == "phase_002"
        assert new_plan.phases[1].tasks[0].task_id == "T-000099"

    def test_assigns_missing_phase_id_above_existing_suffixes(self) -> None:
        keep = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_005",
            ordinal=1,
        )
        replace_target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        new_phase = Phase(
            phase_id=None,
            phase_id_source="none",
            ordinal=2,
            keyword="Stage",
            title="Stage",
            prose="",
            subsections=(),
            tasks=(_task(task_id="T-000010", text="fresh"),),
            line_number=5,
        )
        plan = _plan(phases=(keep, replace_target))
        new_plan = replace_phase_validated(plan, "phase_002", new_phase)
        assert new_plan.phases[1].phase_id == "phase_006"
        assert new_plan.phases[1].phase_id_source == "explicit_comment"

    def test_assigns_missing_task_ids_global_sequential(self) -> None:
        existing = _phase_with_tasks(
            tasks=(_task(task_id="T-000007"),),
            phase_id="phase_001",
            ordinal=1,
        )
        replace_target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        child_without_id = _task(task_id=None, indent_level=2)
        parent_with_id = _task(task_id="T-000020", children=(child_without_id,))
        anonymous_root = _task(task_id=None, text="anon root")
        new_phase = _phase_with_tasks(
            tasks=(parent_with_id, anonymous_root),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(existing, replace_target))
        new_plan = replace_phase_validated(plan, "phase_002", new_phase)
        replaced = new_plan.phases[1]
        assert replaced.tasks[0].task_id == "T-000020"
        # Sequential ids start above the max existing suffix (T-000020).
        assert replaced.tasks[0].children[0].task_id == "T-000021"
        assert replaced.tasks[1].task_id == "T-000022"

    def test_preserve_position_normalizes_ordinal(self) -> None:
        first = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000003", text="fresh"),),
            phase_id="phase_002",
            ordinal=99,
        )
        plan = _plan(phases=(first, target))
        new_plan = replace_phase_validated(plan, "phase_002", new_phase)
        assert new_plan.phases[1].ordinal == 2

    def test_preserve_position_false_with_non_contiguous_ordinal_raises(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002", text="fresh"),),
            phase_id="phase_001",
            ordinal=99,
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError) as exc_info:
            replace_phase_validated(
                plan, "phase_001", new_phase, preserve_position=False
            )
        assert any(
            "contiguous" in msg or "ordinals" in msg for msg in exc_info.value.messages
        )

    def test_no_match_raises_plan_validation_error(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),), phase_id="phase_001"
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError, match="phase_999"):
            replace_phase_validated(plan, "phase_999", new_phase)

    def test_multi_match_raises_plan_validation_error(self) -> None:
        first_dup = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        second_dup = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_001",
            ordinal=2,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000003", text="fresh"),),
            phase_id="phase_001",
            ordinal=1,
        )
        plan = _plan(phases=(first_dup, second_dup))
        with pytest.raises(PlanValidationError, match="multiple phases"):
            replace_phase_validated(plan, "phase_001", new_phase)

    def test_invalid_dep_raises(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002", deps=("T-000999",), text="x"),),
            phase_id="phase_001",
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError, match="T-000999"):
            replace_phase_validated(plan, "phase_001", new_phase)

    def test_assign_missing_ids_false_rejects_missing_task_id(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id=None, text="anon"),), phase_id="phase_001"
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError, match="task_id is missing"):
            replace_phase_validated(
                plan, "phase_001", new_phase, assign_missing_ids=False
            )

    def test_assign_missing_ids_false_rejects_missing_phase_id(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = Phase(
            phase_id=None,
            phase_id_source="none",
            ordinal=1,
            keyword="Stage",
            title="Stage",
            prose="",
            subsections=(),
            tasks=(_task(task_id="T-000002", text="fresh"),),
            line_number=5,
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError, match="phase_id is missing"):
            replace_phase_validated(
                plan, "phase_001", new_phase, assign_missing_ids=False
            )

    def test_assign_missing_ids_false_with_all_ids_present_succeeds(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000050", text="fresh"),),
            phase_id="phase_001",
        )
        plan = _plan(phases=(target,))
        new_plan = replace_phase_validated(
            plan, "phase_001", new_phase, assign_missing_ids=False
        )
        assert new_plan.phases[0].tasks[0].task_id == "T-000050"

    def test_duplicate_task_id_against_other_phase_raises(self) -> None:
        keep = _phase_with_tasks(
            tasks=(_task(task_id="T-000007"),),
            phase_id="phase_001",
            ordinal=1,
        )
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        # new_phase reuses T-000007 from phase_001 → duplicate id.
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000007", text="dup"),),
            phase_id="phase_002",
            ordinal=2,
        )
        plan = _plan(phases=(keep, target))
        with pytest.raises(PlanValidationError, match="duplicate task id T-000007"):
            replace_phase_validated(plan, "phase_002", new_phase)

    def test_duplicate_phase_id_in_result_raises(self) -> None:
        keep = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),),
            phase_id="phase_001",
            ordinal=1,
        )
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000002"),),
            phase_id="phase_002",
            ordinal=2,
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000003", text="fresh"),),
            phase_id="phase_001",
            ordinal=2,
        )
        plan = _plan(phases=(keep, target))
        with pytest.raises(PlanValidationError, match="duplicate phase_id phase_001"):
            replace_phase_validated(plan, "phase_002", new_phase)

    def test_field_stability_violation_raises(self) -> None:
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002", text="bad\ntext"),),
            phase_id="phase_001",
        )
        plan = _plan(phases=(target,))
        with pytest.raises(PlanValidationError, match="embedded newline"):
            replace_phase_validated(plan, "phase_001", new_phase)

    def test_bugs_section_preserved(self) -> None:
        bugs = BugsSection(
            tasks=(_task(task_id="T-000900", line_number=99),),
            line_number=98,
        )
        target = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), phase_id="phase_001"
        )
        new_phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000002", text="fresh"),),
            phase_id="phase_001",
        )
        plan = _plan(phases=(target,), bugs=bugs)
        new_plan = replace_phase_validated(plan, "phase_001", new_phase)
        assert new_plan.bugs is bugs


@dataclass(frozen=True)
class _StubEvent:
    """Minimal structural fixture satisfying the :class:`_LedgerEvent` protocol.

    Carries only the fields :func:`check_consistency` reads
    (``event_id``, ``type``, ``payload``). The real
    :class:`bob_tools.ledger.events.Event` carries additional envelope
    fields (``seq``, ``ts``, ``writer_id``, etc.); leaving them off
    here keeps the tests focused on what consistency-checking
    actually consumes and avoids dragging the ledger schema into the
    planfile test surface.
    """

    event_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


def _test_failed_event(event_id: str, *, test_id: str | None) -> _StubEvent:
    return _StubEvent(
        event_id=event_id,
        type="test_failed",
        payload={"test_id": test_id} if test_id is not None else {},
    )


def _commit_landed_event(
    event_id: str, *, attributed_task_id: str | None
) -> _StubEvent:
    payload: dict[str, Any] = {}
    if attributed_task_id is not None:
        payload["attributed_task_id"] = attributed_task_id
    return _StubEvent(event_id=event_id, type="commit_landed", payload=payload)


def _work_observed_event(
    event_id: str, *, attributed_task_id: str | None
) -> _StubEvent:
    payload: dict[str, Any] = {}
    if attributed_task_id is not None:
        payload["attributed_task_id"] = attributed_task_id
    return _StubEvent(event_id=event_id, type="work_observed", payload=payload)


class TestCheckConsistency:
    """``check_consistency`` flags only contradictions, per design doc §5.

    The rules being pinned:

    * For each task with a stable id, the most recent task-attributed
      lifecycle event determines the expected checkbox state
      (``test_failed`` → FAILED, ``commit_landed`` /
      ``work_observed`` → DONE). A direct disagreement raises
      :class:`PlanInconsistencyError`.
    * Reset (TODO after ``test_failed``) is intentional and silent.
    * Derived parent completion and tasks with no events stay silent.
    * Multiple events for the same task: the lexicographically largest
      ``event_id`` (UUIDv7 time-prefix) wins, matching the ledger's
      replay-order key.
    """

    def test_empty_plan_and_events_is_consistent(self) -> None:
        check_consistency(_plan(), [])

    def test_done_task_with_no_events_is_silent(self) -> None:
        # Hand-edits and derived parent completion both land here:
        # no task-attributed event exists for the task, so there is
        # no evidence to contradict. Silent.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        check_consistency(plan, [])

    def test_done_task_with_matching_commit_landed_is_consistent(self) -> None:
        # Forward-compatible path: once ``attributed_task_id`` lands
        # on ``commit_landed`` (design doc §10), DONE plus a
        # commit_landed event for the same task is the canonical
        # consistent shape.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_commit_landed_event("0001", attributed_task_id="T-000001")]
        check_consistency(plan, events)

    def test_failed_task_with_test_failed_event_is_consistent(self) -> None:
        target = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_test_failed_event("0001", test_id="T-000001")]
        check_consistency(plan, events)

    def test_done_but_most_recent_is_test_failed_raises(self) -> None:
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_test_failed_event("0001", test_id="T-000001")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is DONE but most recent "
            "lifecycle event is test_failed",
        ]

    def test_failed_but_most_recent_is_commit_landed_raises(self) -> None:
        target = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_commit_landed_event("0001", attributed_task_id="T-000001")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is FAILED but most recent "
            "lifecycle event is commit_landed",
        ]

    def test_todo_but_most_recent_is_work_observed_raises(self) -> None:
        # A checkoff that regressed back to unchecked is a contradiction,
        # not a reset. Reset is only the FAILED → TODO transition.
        target = _task(task_id="T-000001", status=TaskStatus.TODO)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_work_observed_event("0001", attributed_task_id="T-000001")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is TODO but most recent "
            "lifecycle event is work_observed",
        ]

    def test_failed_but_most_recent_is_work_observed_raises(self) -> None:
        # Symmetric variant of the commit_landed case: a USER/AUTO task
        # whose work was observed but whose checkbox was later flipped
        # to FAILED contradicts the ledger. Pinned separately so the
        # work_observed → DONE row of `_EVENT_TYPE_TO_EXPECTED_STATUS`
        # cannot regress without a test failure.
        target = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_work_observed_event("0001", attributed_task_id="T-000001")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is FAILED but most recent "
            "lifecycle event is work_observed",
        ]

    def test_todo_but_most_recent_is_commit_landed_raises(self) -> None:
        # Symmetric variant of the work_observed case: a commit landed
        # for the task but PLAN.md no longer reflects it. Pinned
        # separately so the commit_landed → DONE row of
        # `_EVENT_TYPE_TO_EXPECTED_STATUS` cannot regress without a
        # test failure.
        target = _task(task_id="T-000001", status=TaskStatus.TODO)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_commit_landed_event("0001", attributed_task_id="T-000001")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is TODO but most recent "
            "lifecycle event is commit_landed",
        ]

    def test_todo_after_test_failed_is_intentional_reset(self) -> None:
        # Per design doc §5: "Resetting [!] to [ ] via retry → no
        # ledger event; it is an operator decision to retry existing
        # work." The checker must accept TODO after test_failed.
        target = _task(task_id="T-000001", status=TaskStatus.TODO)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_test_failed_event("0001", test_id="T-000001")]
        check_consistency(plan, events)

    def test_most_recent_event_wins_when_multiple_for_same_task(self) -> None:
        # An older test_failed followed by a newer commit_landed:
        # the task has been re-tried and now succeeded. DONE checkbox
        # is consistent with the most recent event.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [
            _test_failed_event("0001", test_id="T-000001"),
            _commit_landed_event("0002", attributed_task_id="T-000001"),
        ]
        check_consistency(plan, events)

    def test_event_order_in_iterable_does_not_matter(self) -> None:
        # event_id (UUIDv7) is the ordering key, not iteration order.
        # Same as the previous test with the events reversed; the
        # commit_landed still wins.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [
            _commit_landed_event("0002", attributed_task_id="T-000001"),
            _test_failed_event("0001", test_id="T-000001"),
        ]
        check_consistency(plan, events)

    def test_old_commit_landed_then_recent_test_failed_flags_done(self) -> None:
        # Now the failure is most recent; a DONE checkbox contradicts it.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [
            _commit_landed_event("0001", attributed_task_id="T-000001"),
            _test_failed_event("0002", test_id="T-000001"),
        ]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is DONE but most recent "
            "lifecycle event is test_failed",
        ]

    def test_derived_parent_completion_not_flagged(self) -> None:
        # Parent is DONE but no event references it directly
        # (auto-checked because all children are DONE; the derived
        # Settlement carries ledger_event_required=False). The checker
        # must not invent a contradiction for it.
        child = _task(
            task_id="T-000010",
            status=TaskStatus.DONE,
            indent_level=2,
            line_number=2,
        )
        parent = _task(
            task_id="T-000001",
            status=TaskStatus.DONE,
            children=(child,),
        )
        plan = _plan(phases=(_phase_with_tasks(tasks=(parent,)),))
        events = [_commit_landed_event("0001", attributed_task_id="T-000010")]
        check_consistency(plan, events)

    def test_commit_landed_without_attributed_task_id_is_ignored(self) -> None:
        # Today's commit_landed schema attributes only to a phase
        # (SCHEMA.md). Events without ``attributed_task_id`` carry no
        # per-task claim and must not flag any task as inconsistent —
        # forward-compatible behavior pending the §10 schema bump.
        target = _task(task_id="T-000001", status=TaskStatus.FAILED)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_commit_landed_event("0001", attributed_task_id=None)]
        check_consistency(plan, events)

    def test_unrelated_event_types_are_ignored(self) -> None:
        # phase_started, finding_observed, threshold_crossed, etc. do
        # not carry a per-task state claim. Even payloads that happen
        # to contain a ``test_id`` key under an unrelated event type
        # are skipped by the type filter.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [
            _StubEvent("0001", "phase_started", {"phase_id": "phase_001"}),
            _StubEvent("0002", "finding_observed", {"summary": "x"}),
            _StubEvent("0003", "threshold_crossed", {"rule_id": "r"}),
            _StubEvent("0004", "test_failed", {}),  # missing test_id
        ]
        check_consistency(plan, events)

    def test_compat_mode_task_without_id_is_skipped(self) -> None:
        # A task with task_id=None cannot be matched against an event's
        # task_id field; the checker has nothing to compare and stays
        # silent regardless of checkbox state.
        target = _task(task_id=None, status=TaskStatus.DONE, line_number=42)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_test_failed_event("0001", test_id="T-000001")]
        check_consistency(plan, events)

    def test_event_for_unknown_task_is_silently_skipped(self) -> None:
        # The ledger may carry events for tasks no longer in PLAN.md
        # (a phase reauthor dropped them, e.g.). check_consistency
        # only walks tasks in the plan and ignores orphan events;
        # nothing to raise.
        target = _task(task_id="T-000001", status=TaskStatus.DONE)
        plan = _plan(phases=(_phase_with_tasks(tasks=(target,)),))
        events = [_test_failed_event("0001", test_id="T-000999")]
        check_consistency(plan, events)

    def test_multiple_contradictions_collected_in_one_raise(self) -> None:
        # Validation-style behavior: every contradiction surfaces in
        # the single PlanInconsistencyError so the user sees them all.
        # Messages are sorted by task_id for deterministic output.
        a = _task(task_id="T-000001", status=TaskStatus.DONE, line_number=1)
        b = _task(task_id="T-000002", status=TaskStatus.FAILED, line_number=2)
        plan = _plan(phases=(_phase_with_tasks(tasks=(a, b)),))
        events = [
            _test_failed_event("0001", test_id="T-000001"),
            _commit_landed_event("0002", attributed_task_id="T-000002"),
        ]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000001 checkbox is DONE but most recent "
            "lifecycle event is test_failed",
            "task T-000002 checkbox is FAILED but most recent "
            "lifecycle event is commit_landed",
        ]

    def test_bug_section_task_is_checked(self) -> None:
        # Bug tasks live outside any phase but are still tracked in the
        # plan; their checkbox state must reconcile against task-
        # attributed events the same way phase tasks do.
        bug = _task(task_id="T-000900", status=TaskStatus.DONE, line_number=100)
        plan = _plan(bugs=BugsSection(tasks=(bug,), line_number=99))
        events = [_test_failed_event("0001", test_id="T-000900")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(plan, events)
        assert exc_info.value.messages == [
            "task T-000900 checkbox is DONE but most recent "
            "lifecycle event is test_failed",
        ]

    def test_subsection_task_is_checked(self) -> None:
        # Tasks inside ### subsections share their containing phase's
        # phase_id (design doc §11 q5) but are still individually
        # task-identified; the checker walks them via _iter_plan_tasks.
        sub_task = _task(task_id="T-000010", status=TaskStatus.DONE, line_number=20)
        sub = Subsection(title="Manual", prose="", tasks=(sub_task,), line_number=19)
        phase = _phase_with_tasks(
            tasks=(_task(task_id="T-000001"),), subsections=(sub,)
        )
        events = [_test_failed_event("0001", test_id="T-000010")]
        with pytest.raises(PlanInconsistencyError) as exc_info:
            check_consistency(_plan(phases=(phase,)), events)
        assert exc_info.value.messages == [
            "task T-000010 checkbox is DONE but most recent "
            "lifecycle event is test_failed",
        ]


class TestAddBugTask:
    """Tests for ``add_bug_task`` per v4 Contract 2.

    Pins the three outcome strings (``appended`` / ``reopened`` /
    ``unchanged``), Bugs-section creation, dedup-key matching across
    each source (explicit, ``fix`` annotation, normalized text),
    id assignment, status forcing, and the field-stability rejection
    that gates the input.
    """

    def _plan_no_bugs(self, phases: tuple[Phase, ...] = ()) -> Plan:
        return Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=phases,
            bugs=None,
            source_path=None,
        )

    def _plan_with_bugs(self, *bugs: Task, phases: tuple[Phase, ...] = ()) -> Plan:
        return Plan(
            magic_version=1,
            project_title="Project",
            preamble="",
            phases=phases,
            bugs=BugsSection(tasks=bugs, line_number=0),
            source_path=None,
        )

    def test_creates_bugs_section_when_absent(self) -> None:
        plan = self._plan_no_bugs()
        task = make_task("Login form rejects unicode")
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert len(new_plan.bugs.tasks) == 1
        assert new_plan.bugs.tasks[0].text == "Login form rejects unicode"
        assert new_plan.bugs.tasks[0].task_id == "T-000001"
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_appends_to_existing_empty_bugs_section(self) -> None:
        # An empty parsed BugsSection is preserved, not re-created;
        # the task is appended into it.
        plan = self._plan_with_bugs()
        task = make_task("first bug")
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert len(new_plan.bugs.tasks) == 1

    def test_appends_after_existing_bug_tasks(self) -> None:
        existing = make_task("first bug", task_id="T-000900")
        plan = self._plan_with_bugs(existing)
        task = make_task("second bug")
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert [t.text for t in new_plan.bugs.tasks] == ["first bug", "second bug"]
        # Position preserved: existing first, new second.
        assert new_plan.bugs.tasks[0].task_id == "T-000900"

    def test_id_assignment_uses_global_max_plus_one(self) -> None:
        # The new id starts at max+1 across the whole plan, including
        # phase tasks, so a bug never collides with a phase task id.
        existing_phase_task = Task(
            task_id="T-000050",
            text="phase task",
            status=TaskStatus.TODO,
            flag_tags=(),
            action_tag=None,
            annotations=(),
            deps=(),
            children=(),
            ruled_out=(),
            indent_level=0,
            line_number=0,
        )
        phase = Phase(
            phase_id="phase_001",
            phase_id_source="explicit_comment",
            ordinal=1,
            keyword="Stage",
            title="S",
            prose="",
            subsections=(),
            tasks=(existing_phase_task,),
            line_number=0,
        )
        plan = self._plan_no_bugs(phases=(phase,))
        new_plan, _ = add_bug_task(plan, make_task("a new bug"))
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].task_id == "T-000051"

    def test_caller_supplied_task_id_is_honored(self) -> None:
        plan = self._plan_no_bugs()
        task = make_task("a bug", task_id="T-000777")
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].task_id == "T-000777"

    def test_append_forces_status_to_todo(self) -> None:
        # An incoming task whose status is DONE (a meaningless state
        # for a brand-new bug) must be coerced to TODO before insertion.
        plan = self._plan_no_bugs()
        task = make_task("done in advance", status=TaskStatus.DONE)
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_todo_match_returns_unchanged(self) -> None:
        existing = make_task("repeated bug", task_id="T-000900")
        plan = self._plan_with_bugs(existing)
        task = make_task("repeated bug")
        new_plan, outcome = add_bug_task(plan, task)
        assert outcome == "unchanged"
        assert new_plan is plan

    def test_done_match_reopens_in_place(self) -> None:
        existing = make_task(
            "regression came back",
            task_id="T-000900",
            status=TaskStatus.DONE,
        )
        plan = self._plan_with_bugs(existing)
        new_plan, outcome = add_bug_task(plan, make_task("regression came back"))
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        reopened = new_plan.bugs.tasks[0]
        # Same id, same position; status flipped to TODO.
        assert reopened.task_id == "T-000900"
        assert reopened.status == TaskStatus.TODO

    def test_failed_match_reopens_in_place(self) -> None:
        existing = make_task(
            "flaky bug",
            task_id="T-000900",
            status=TaskStatus.FAILED,
        )
        plan = self._plan_with_bugs(existing)
        new_plan, outcome = add_bug_task(plan, make_task("flaky bug"))
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_reopen_preserves_children_annotations_deps_ruled_out(self) -> None:
        # Children, annotations, deps, ruled_out, id, and position must
        # be preserved when reopening — the incoming task contributes
        # only its dedup keys, not its content.
        child = make_task("nested step", task_id="T-000901")
        ruled = RuledOut(text="approach A", line_number=0)
        existing = make_task(
            "rich bug",
            task_id="T-000900",
            status=TaskStatus.DONE,
            annotations=(("fix", "issue-42"),),
            deps=("T-000901",),
            children=(child,),
            ruled_out=(ruled,),
        )
        # The reopen target sits in position 1 so position preservation
        # is observable (it must not move to position 0 or end).
        other = make_task("unrelated bug", task_id="T-000910")
        plan = self._plan_with_bugs(other, existing)
        new_plan, outcome = add_bug_task(plan, make_task("rich bug"))
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        kept = new_plan.bugs.tasks[1]
        assert kept.task_id == "T-000900"
        assert kept.status == TaskStatus.TODO
        assert kept.annotations == (("fix", "issue-42"),)
        assert kept.deps == ("T-000901",)
        assert kept.children == (child,)
        assert kept.ruled_out == (ruled,)
        # The unrelated bug at position 0 is untouched.
        assert new_plan.bugs.tasks[0].task_id == "T-000910"

    def test_reopens_earliest_match(self) -> None:
        # Two existing bugs share the same dedup key. The reopen must
        # land on the earlier one; the second match stays untouched.
        first = make_task(
            "same text",
            task_id="T-000900",
            status=TaskStatus.DONE,
        )
        second = make_task(
            "same text",
            task_id="T-000901",
            status=TaskStatus.DONE,
        )
        plan = self._plan_with_bugs(first, second)
        new_plan, outcome = add_bug_task(plan, make_task("same text"))
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO
        assert new_plan.bugs.tasks[1].status == TaskStatus.DONE

    def test_explicit_dedup_key_matches_against_fix_annotation(self) -> None:
        existing = make_task(
            "old phrasing",
            task_id="T-000900",
            status=TaskStatus.DONE,
            annotations=(("fix", "issue-42"),),
        )
        plan = self._plan_with_bugs(existing)
        # The caller knows this is the same issue but the text drifted;
        # the explicit dedup_keys keeps them collapsed.
        new_plan, outcome = add_bug_task(
            plan,
            make_task("entirely new phrasing"),
            dedup_keys=("issue-42",),
        )
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_fix_annotation_value_matches_normalized_text(self) -> None:
        # The fix-annotation value on the incoming task matches the
        # normalized text on an existing entry — cross-source dedup.
        existing = make_task(
            "issue-42",
            task_id="T-000900",
            status=TaskStatus.FAILED,
        )
        plan = self._plan_with_bugs(existing)
        incoming = make_task(
            "something completely different",
            annotations=(("fix", "issue-42"),),
        )
        new_plan, outcome = add_bug_task(plan, incoming)
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_normalized_text_dedup_absorbs_whitespace_differences(self) -> None:
        existing = make_task(
            "trim   me",
            task_id="T-000900",
            status=TaskStatus.DONE,
        )
        plan = self._plan_with_bugs(existing)
        # Different internal spacing — same normalized text.
        new_plan, outcome = add_bug_task(plan, make_task("trim me"))
        assert outcome == "reopened"
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[0].status == TaskStatus.TODO

    def test_no_match_when_dedup_keys_disjoint(self) -> None:
        existing = make_task(
            "first",
            task_id="T-000900",
            status=TaskStatus.DONE,
            annotations=(("fix", "issue-A"),),
        )
        plan = self._plan_with_bugs(existing)
        incoming = make_task(
            "second",
            annotations=(("fix", "issue-B"),),
        )
        new_plan, outcome = add_bug_task(plan, incoming, dedup_keys=("issue-C",))
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert [t.task_id for t in new_plan.bugs.tasks] == ["T-000900", "T-000901"]
        # The existing DONE bug is untouched.
        assert new_plan.bugs.tasks[0].status == TaskStatus.DONE

    def test_non_fix_annotations_do_not_seed_dedup_keys(self) -> None:
        # Only the ``fix`` annotation contributes; a ``note`` annotation
        # with the same value must not collapse two distinct bugs.
        existing = make_task(
            "alpha",
            task_id="T-000900",
            status=TaskStatus.DONE,
            annotations=(("note", "shared-value"),),
        )
        plan = self._plan_with_bugs(existing)
        incoming = make_task(
            "beta",
            annotations=(("note", "shared-value"),),
        )
        new_plan, outcome = add_bug_task(plan, incoming)
        assert outcome == "appended"
        assert new_plan.bugs is not None
        assert len(new_plan.bugs.tasks) == 2

    def test_hand_built_task_with_trailing_lines_accepted(self) -> None:
        # Trailing lines on a hand-built Task are accepted and preserved
        # through add_bug_task: they are lossless renderable content,
        # not a contract violation (the old rejection existed only to
        # catch parsed-from-disk lines, where they are legitimate).
        carried = Task(
            task_id=None,
            text="bug body",
            status=TaskStatus.TODO,
            flag_tags=(),
            action_tag=None,
            annotations=(),
            deps=(),
            children=(),
            ruled_out=(),
            indent_level=0,
            line_number=0,
            trailing_lines=("  oops",),
        )
        plan = self._plan_no_bugs()
        new_plan, _task_id = add_bug_task(plan, carried)
        assert new_plan.bugs is not None
        assert new_plan.bugs.tasks[-1].trailing_lines == ("  oops",)
