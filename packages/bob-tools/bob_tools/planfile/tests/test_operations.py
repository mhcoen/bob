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
    TaskContext,
    TaskStatus,
)
from bob_tools.planfile.operations import (
    _find_task_by_id,
    bug_count,
    resolve_task_context,
    validate_plan,
)


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

    def test_phase_with_none_source_propagates_none(self) -> None:
        phase = self._phase_with_id(
            tasks=(_task(task_id="T-000001"),),
            phase_id=None,
            phase_id_source="none",
        )
        ctx = resolve_task_context(_plan(phases=(phase,)), "T-000001")
        assert ctx.phase_id is None
        assert ctx.phase_id_source == "none"
        # The match still happened — task_id is populated even though
        # the phase has no id.
        assert ctx.task_id == "T-000001"

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
