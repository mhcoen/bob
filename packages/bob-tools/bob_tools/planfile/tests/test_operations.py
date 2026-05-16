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
    Outcome,
    Phase,
    Plan,
    PlanValidationError,
    Settlement,
    Subsection,
    Task,
    TaskContext,
    TaskStatus,
)
from bob_tools.planfile.operations import (
    _find_task_by_id,
    add_task,
    bug_count,
    complete_task,
    fail_task,
    next_tasks,
    replace_phase,
    reset_task,
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
    status: TaskStatus = TaskStatus.TODO,
    flag_tags: tuple[str, ...] = (),
    action_tag: tuple[str, str] | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        text=text,
        status=status,
        flag_tags=flag_tags,
        action_tag=action_tag,
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
