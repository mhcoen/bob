"""Tests for bob_tools.planfile.parser building blocks.

Stage 2 task-line recognizers and tag extractors:

  - ``_CHECKBOX_RE`` matches indented/bare checkboxes for every status
    marker (space, ``x``, ``X``, ``!``); rejects malformed forms.
  - ``_parse_task_line`` returns a raw record on checkbox lines and
    ``None`` on anything else.
  - ``_parse_ruledout_line`` recognizes leading-position ``[RULEDOUT]``
    sibling lines and returns indent, body, and source line number.
  - ``_attach_ruledout`` resolves the attachment target for a RULEDOUT
    line: nearest strictly-less-indented open ancestor, with fallback
    to the most recent root task (mcloop's ``parse`` parity). The
    parse-then-attach composition test confirms multiple RULEDOUT
    lines on one task are collected in source order.
  - ``_DEPS_RE`` recognizes ``@deps`` sibling lines, captures indent
    and the whitespace-separated tail of bare ``T-NNNNNN`` IDs, and
    enforces that at least one ID follows the keyword.
  - ``_attach_deps`` resolves the attachment target for an ``@deps``
    sibling line: the innermost open ancestor at strictly-less indent
    is the strict-form parent; an ancestor at equal indent is the
    lenient-form parent (caller should warn). No root-task fallback.
    A composition test pairs ``_DEPS_RE`` parsing with ``_attach_deps``
    to confirm a deeply-indented ``@deps`` lands on a nested subtask.
  - ``_extract_flag_tags`` consumes leading ``[USER]`` / ``[BATCH]``
    tokens, in isolation and combination, and leaves non-leading
    occurrences as prose (design doc section 4.3).
  - ``_extract_action_tag`` consumes a leading ``[AUTO:<action>]`` and
    treats the rest of the line as its argument string; non-leading
    occurrences are prose.
  - ``_extract_annotations`` consumes trailing ``[key: value]``
    annotations, handles multiple annotations and nested brackets in
    values, and leaves bracketed prose that is not separated from
    preceding text by whitespace in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bob_tools.planfile.model import PlanSyntaxError, TaskStatus
from bob_tools.planfile.parser import (
    _CHECKBOX_RE,
    _DEPS_RE,
    _attach_deps,
    _attach_ruledout,
    _extract_action_tag,
    _extract_annotations,
    _extract_flag_tags,
    _parse_ruledout_line,
    _parse_task_line,
    parse_plan,
)


@dataclass
class _FakeTask:
    """Minimal indent-bearing stand-in for the attachment-logic tests."""

    indent_level: int
    name: str = ""


class TestCheckboxRe:
    @pytest.mark.parametrize(
        ("line", "indent", "marker", "text"),
        [
            ("- [ ] do thing", "", " ", "do thing"),
            ("  - [x] done thing", "  ", "x", "done thing"),
            ("    - [X] also done", "    ", "X", "also done"),
            ("- [!] failed thing", "", "!", "failed thing"),
            ("   - [ ] three-space indent", "   ", " ", "three-space indent"),
        ],
    )
    def test_matches(self, line: str, indent: str, marker: str, text: str) -> None:
        m = _CHECKBOX_RE.match(line)
        assert m is not None
        assert m.group(1) == indent
        assert m.group(2) == marker
        assert m.group(3) == text

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "## Stage 1: Core",
            "- [] missing space inside brackets",
            "- [y] unsupported marker",
            "- [ ]",
            "* [ ] wrong bullet",
            "Plain prose line",
        ],
    )
    def test_non_matches(self, line: str) -> None:
        assert _CHECKBOX_RE.match(line) is None


class TestParseTaskLine:
    def test_match_returns_raw_record(self) -> None:
        rec = _parse_task_line("  - [x] foo", 7)
        assert rec is not None
        assert rec.indent == "  "
        assert rec.status_char == "x"
        assert rec.text == "foo"
        assert rec.line_number == 7

    def test_match_failed_marker(self) -> None:
        rec = _parse_task_line("- [!] bad", 12)
        assert rec is not None
        assert rec.status_char == "!"
        assert rec.line_number == 12

    def test_non_task_returns_none(self) -> None:
        assert _parse_task_line("## Stage 1: Core", 1) is None
        assert _parse_task_line("", 2) is None
        assert _parse_task_line("Some prose", 3) is None


class TestParseRuledOutLine:
    def test_indented_with_text(self) -> None:
        rec = _parse_ruledout_line("  [RULEDOUT] tried restart", 5)
        assert rec is not None
        assert rec.indent == "  "
        assert rec.text == "tried restart"
        assert rec.line_number == 5

    def test_top_level_with_text(self) -> None:
        rec = _parse_ruledout_line("[RULEDOUT] orphan approach", 1)
        assert rec is not None
        assert rec.indent == ""
        assert rec.text == "orphan approach"

    def test_empty_body(self) -> None:
        rec = _parse_ruledout_line("    [RULEDOUT]", 9)
        assert rec is not None
        assert rec.indent == "    "
        assert rec.text == ""

    def test_trailing_whitespace_stripped(self) -> None:
        rec = _parse_ruledout_line("  [RULEDOUT] foo   ", 3)
        assert rec is not None
        assert rec.text == "foo"

    def test_non_leading_token_is_not_match(self) -> None:
        # A RULEDOUT token that appears mid-line is prose, not a
        # RULEDOUT line. Only the leading-position form is recognized.
        assert _parse_ruledout_line("- [ ] talk about [RULEDOUT] later", 1) is None

    def test_similar_token_does_not_match(self) -> None:
        # `startswith("[RULEDOUT]")` semantics: the bracket must close
        # immediately after the keyword.
        assert _parse_ruledout_line("[RULEDOUT_OTHER] foo", 1) is None

    def test_non_ruledout_lines_return_none(self) -> None:
        assert _parse_ruledout_line("- [ ] regular task", 1) is None
        assert _parse_ruledout_line("", 2) is None
        assert _parse_ruledout_line("## Stage 1", 3) is None


class TestAttachRuledOut:
    def test_nearest_strict_less_indent_wins(self) -> None:
        root = _FakeTask(indent_level=0, name="root")
        child = _FakeTask(indent_level=2, name="child")
        grand = _FakeTask(indent_level=4, name="grand")
        # RULEDOUT at indent 4 sits as a sibling under `child`
        # (indent 2): it attaches to `child`, not to `grand` even
        # though `grand` is deeper in the stack.
        attached = _attach_ruledout(4, [root, child, grand], [root])
        assert attached is child

    def test_skips_equal_indent_in_stack(self) -> None:
        # Equal indent is not "strictly less", so the equal-indent
        # entry is skipped and the search continues outward.
        root = _FakeTask(indent_level=0, name="root")
        same = _FakeTask(indent_level=2, name="same")
        attached = _attach_ruledout(2, [root, same], [root])
        assert attached is root

    def test_top_level_falls_back_to_most_recent_root(self) -> None:
        # A column-0 RULEDOUT after the stack has been popped (e.g.
        # following a same-indent sibling) finds no strictly-less
        # ancestor, so it attaches to the last root task.
        a = _FakeTask(indent_level=0, name="a")
        b = _FakeTask(indent_level=0, name="b")
        attached = _attach_ruledout(0, [], [a, b])
        assert attached is b

    def test_no_ancestors_no_roots_returns_none(self) -> None:
        # A stray [RULEDOUT] before any task in the phase has nothing
        # to attach to; the caller is expected to drop it.
        assert _attach_ruledout(0, [], []) is None

    def test_empty_stack_uses_root_fallback(self) -> None:
        root = _FakeTask(indent_level=0, name="root")
        attached = _attach_ruledout(2, [], [root])
        assert attached is root

    def test_multiple_ruledouts_on_one_task_collected_in_order(self) -> None:
        # Compose the building blocks the orchestration layer will use:
        # parse each [RULEDOUT] line, route it to its parent via
        # _attach_ruledout, then append to the parent's list. Three
        # sibling RULEDOUTs at the child indent must all land on the
        # same parent and stay in source order.
        root = _FakeTask(indent_level=0, name="root")
        child = _FakeTask(indent_level=2, name="child")
        stack = [root, child]
        roots = [root]

        lines = [
            ("    [RULEDOUT] first approach", 10),
            ("    [RULEDOUT] second approach", 11),
            ("    [RULEDOUT] third approach", 12),
        ]
        collected: dict[str, list[tuple[str, int]]] = {}
        for raw_line, line_number in lines:
            rec = _parse_ruledout_line(raw_line, line_number)
            assert rec is not None
            target = _attach_ruledout(len(rec.indent), stack, roots)
            assert target is child
            collected.setdefault(target.name, []).append((rec.text, rec.line_number))

        assert collected == {
            "child": [
                ("first approach", 10),
                ("second approach", 11),
                ("third approach", 12),
            ],
        }


class TestDepsRe:
    def test_single_id(self) -> None:
        m = _DEPS_RE.match("@deps T-000001")
        assert m is not None
        assert m.group(1) == ""
        assert m.group(2).split() == ["T-000001"]

    def test_multiple_ids(self) -> None:
        m = _DEPS_RE.match("@deps T-000001 T-000002 T-000003")
        assert m is not None
        assert m.group(1) == ""
        assert m.group(2).split() == ["T-000001", "T-000002", "T-000003"]

    def test_indented_captures_indent(self) -> None:
        m = _DEPS_RE.match("    @deps T-000001 T-000002")
        assert m is not None
        assert m.group(1) == "    "
        assert m.group(2).split() == ["T-000001", "T-000002"]

    def test_extra_inter_id_whitespace_collapses_on_split(self) -> None:
        # Variable spacing between IDs is normalized by str.split().
        m = _DEPS_RE.match("@deps T-000001    T-000002")
        assert m is not None
        assert m.group(2).split() == ["T-000001", "T-000002"]

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "- [ ] regular task",
            "@dep T-000001",
            "@deps",
            "@deps ",
            " @deps_ T-000001",
        ],
    )
    def test_non_deps_lines(self, line: str) -> None:
        assert _DEPS_RE.match(line) is None


class TestAttachDeps:
    def test_strict_form_deps_indented_under_parent(self) -> None:
        # Canonical form: @deps is indented strictly more than the
        # task it references. Parent's indent is strictly less than
        # the deps line's, so attachment is strict (no warning).
        parent = _FakeTask(indent_level=0, name="parent")
        target, lenient = _attach_deps(2, [parent])
        assert target is parent
        assert lenient is False

    def test_lenient_form_same_indent_warns(self) -> None:
        # "Forgot to indent" form: @deps at the same indent as its
        # task. Still attaches, but flagged as lenient so callers can
        # emit a validation warning.
        parent = _FakeTask(indent_level=0, name="parent")
        target, lenient = _attach_deps(0, [parent])
        assert target is parent
        assert lenient is True

    def test_strict_attaches_to_innermost_ancestor(self) -> None:
        # With multiple open ancestors the innermost strictly-less-
        # indented one wins — not the outermost.
        outer = _FakeTask(indent_level=0, name="outer")
        inner = _FakeTask(indent_level=2, name="inner")
        target, lenient = _attach_deps(4, [outer, inner])
        assert target is inner
        assert lenient is False

    def test_lenient_uses_top_of_stack_at_same_indent(self) -> None:
        # Top of stack is at deps' indent — lenient attachment to the
        # immediately preceding task.
        outer = _FakeTask(indent_level=0, name="outer")
        inner = _FakeTask(indent_level=2, name="inner")
        target, lenient = _attach_deps(2, [outer, inner])
        assert target is inner
        assert lenient is True

    def test_outdented_deps_walks_past_deeper_top(self) -> None:
        # The most recent task is more indented than @deps, so it is
        # skipped; the search continues outward and lands on the
        # equal-indent ancestor as a lenient match.
        outer = _FakeTask(indent_level=0, name="outer")
        inner = _FakeTask(indent_level=2, name="inner")
        target, lenient = _attach_deps(0, [outer, inner])
        assert target is outer
        assert lenient is True

    def test_empty_stack_returns_none(self) -> None:
        # A @deps line before any task in scope has nothing to attach
        # to — the caller is expected to drop it. No root-task fallback
        # is provided, unlike _attach_ruledout.
        target, lenient = _attach_deps(0, [])
        assert target is None
        assert lenient is False

    def test_all_ancestors_more_indented_returns_none(self) -> None:
        # Pathological: deps outdented past every known ancestor.
        # Stack-walk finds no candidate at lesser-or-equal indent.
        deep = _FakeTask(indent_level=4, name="deep")
        target, lenient = _attach_deps(2, [deep])
        assert target is None
        assert lenient is False

    def test_parse_and_attach_to_nested_subtask(self) -> None:
        # Compose _DEPS_RE with _attach_deps: a deeply-indented @deps
        # line resolves to the innermost open ancestor at strictly-less
        # indent — i.e. the nested subtask, not the root task. This is
        # the end-to-end shape the higher-level parser will use.
        root = _FakeTask(indent_level=0, name="root")
        child = _FakeTask(indent_level=2, name="child")
        grand = _FakeTask(indent_level=4, name="grand")
        stack = [root, child, grand]

        m = _DEPS_RE.match("      @deps T-000001 T-000002")
        assert m is not None
        indent = len(m.group(1))
        ids = m.group(2).split()

        target, lenient = _attach_deps(indent, stack)
        assert target is grand
        assert lenient is False
        assert ids == ["T-000001", "T-000002"]


class TestExtractFlagTags:
    def test_no_tags(self) -> None:
        tags, rest = _extract_flag_tags("plain task text")
        assert tags == ()
        assert rest == "plain task text"

    def test_user(self) -> None:
        tags, rest = _extract_flag_tags("[USER] verify the menu")
        assert tags == ("USER",)
        assert rest == "verify the menu"

    def test_batch(self) -> None:
        tags, rest = _extract_flag_tags("[BATCH] sub-task group")
        assert tags == ("BATCH",)
        assert rest == "sub-task group"

    def test_user_and_batch_combined(self) -> None:
        tags, rest = _extract_flag_tags("[USER] [BATCH] both flags")
        assert tags == ("USER", "BATCH")
        assert rest == "both flags"

    def test_adjacent_tags_without_separator(self) -> None:
        tags, rest = _extract_flag_tags("[USER][BATCH] no gap")
        assert tags == ("USER", "BATCH")
        assert rest == "no gap"

    def test_non_leading_tag_is_prose(self) -> None:
        tags, rest = _extract_flag_tags("Document the [USER] tag")
        assert tags == ()
        assert rest == "Document the [USER] tag"

    def test_only_first_run_is_tags(self) -> None:
        tags, rest = _extract_flag_tags("[USER] middle [BATCH] later")
        assert tags == ("USER",)
        assert rest == "middle [BATCH] later"

    def test_empty_input(self) -> None:
        tags, rest = _extract_flag_tags("")
        assert tags == ()
        assert rest == ""


class TestExtractActionTag:
    def test_no_action_tag(self) -> None:
        tag, rest = _extract_action_tag("plain task text")
        assert tag is None
        assert rest == "plain task text"

    def test_action_with_args(self) -> None:
        tag, rest = _extract_action_tag("[AUTO:run_cli] mcloop --dry-run")
        assert tag == ("run_cli", "mcloop --dry-run")
        assert rest == ""

    def test_action_without_args(self) -> None:
        tag, rest = _extract_action_tag("[AUTO:noop]")
        assert tag == ("noop", "")
        assert rest == ""

    def test_args_extend_to_end_of_line(self) -> None:
        # Argument string is text from closing bracket to end of line;
        # bracketed-looking content in args stays in args.
        tag, rest = _extract_action_tag("[AUTO:run] do [feat: x]")
        assert tag == ("run", "do [feat: x]")
        assert rest == ""

    def test_non_leading_auto_is_prose(self) -> None:
        tag, rest = _extract_action_tag("Document the [AUTO:run] tag")
        assert tag is None
        assert rest == "Document the [AUTO:run] tag"


class TestExtractAnnotations:
    def test_no_annotation(self) -> None:
        ann, rest = _extract_annotations("plain task text")
        assert ann == ()
        assert rest == "plain task text"

    def test_single_feat(self) -> None:
        ann, rest = _extract_annotations('do thing [feat: "menu wired"]')
        assert ann == (("feat", '"menu wired"'),)
        assert rest == "do thing"

    def test_single_fix(self) -> None:
        ann, rest = _extract_annotations('fixed it [fix: "race"]')
        assert ann == (("fix", '"race"'),)
        assert rest == "fixed it"

    def test_multiple_annotations_preserve_order(self) -> None:
        ann, rest = _extract_annotations('do thing [feat: "first"] [fix: "second"]')
        assert ann == (
            ("feat", '"first"'),
            ("fix", '"second"'),
        )
        assert rest == "do thing"

    def test_nested_brackets_in_value(self) -> None:
        # Balanced brackets inside the value are stepped over by the
        # right-to-left depth scan and stay inside the annotation.
        ann, rest = _extract_annotations('do thing [feat: "see [issue #42]"]')
        assert ann == (("feat", '"see [issue #42]"'),)
        assert rest == "do thing"

    def test_unquoted_nested_brackets_in_value(self) -> None:
        ann, rest = _extract_annotations("do [feat: a [b] c]")
        assert ann == (("feat", "a [b] c"),)
        assert rest == "do"

    def test_prose_bracket_without_separator_is_text(self) -> None:
        # A `[` abutting a non-whitespace character is task text, not
        # the start of an annotation.
        ann, rest = _extract_annotations("see config[feat: x]")
        assert ann == ()
        assert rest == "see config[feat: x]"

    def test_no_whitespace_after_colon_is_not_annotation(self) -> None:
        # `[AUTO:run]` has no whitespace after its colon and so is not
        # an annotation under this extractor's rules.
        ann, rest = _extract_annotations("[AUTO:run] cmd")
        assert ann == ()
        assert rest == "[AUTO:run] cmd"

    def test_unmatched_open_bracket(self) -> None:
        ann, rest = _extract_annotations("dangling brackets feat ]")
        assert ann == ()
        assert rest == "dangling brackets feat ]"

    def test_annotation_alone_consumes_to_empty(self) -> None:
        ann, rest = _extract_annotations('[feat: "lone"]')
        assert ann == (("feat", '"lone"'),)
        assert rest == ""


class TestTagInteractions:
    def test_full_pipeline_with_flag_action_and_annotation(self) -> None:
        text = '[BATCH] [AUTO:run_cli] mcloop --dry-run [feat: "done"]'
        # Annotation must be stripped before action tag, because action
        # tag args span to end of line.
        ann, after_ann = _extract_annotations(text)
        assert ann == (("feat", '"done"'),)
        tags, after_flags = _extract_flag_tags(after_ann)
        assert tags == ("BATCH",)
        action, after_action = _extract_action_tag(after_flags)
        assert action == ("run_cli", "mcloop --dry-run")
        assert after_action == ""

    def test_flags_and_plain_text(self) -> None:
        ann, after_ann = _extract_annotations("[USER] [BATCH] verify thing")
        assert ann == ()
        tags, after_flags = _extract_flag_tags(after_ann)
        assert tags == ("USER", "BATCH")
        action, after_action = _extract_action_tag(after_flags)
        assert action is None
        assert after_action == "verify thing"

    def test_tag_like_substrings_in_prose_are_unchanged(self) -> None:
        text = "Implement [USER] and [AUTO:run] tokenization"
        ann, after_ann = _extract_annotations(text)
        tags, after_flags = _extract_flag_tags(after_ann)
        action, after_action = _extract_action_tag(after_flags)
        assert ann == ()
        assert tags == ()
        assert action is None
        assert after_action == text


class TestParsePlanStateMachine:
    """End-to-end tests for the parse_plan walker.

    The state machine in 2.5.2 is responsible for the section-vs-task
    bookkeeping that earlier building blocks do not own: routing tasks
    into the right phase or subsection, opening/closing the indent
    stack at phase/subsection/bugs boundaries, and dispatching ``@deps``
    and ``[RULEDOUT]`` siblings to the parent task. Tests below
    exercise each of those responsibilities independently and then in
    combination on a small but representative plan.
    """

    def test_empty_text_returns_empty_plan(self) -> None:
        plan = parse_plan("")
        assert plan.phases == ()
        assert plan.bugs is None
        assert plan.project_title == ""

    def test_single_phase_with_tasks(self) -> None:
        text = "## Stage 1: Core\n\n- [ ] first task\n- [x] second task\n"
        plan = parse_plan(text)
        assert len(plan.phases) == 1
        phase = plan.phases[0]
        assert phase.ordinal == 1
        assert phase.keyword == "Stage"
        assert phase.title == "Core"
        assert phase.line_number == 1
        assert len(phase.tasks) == 2
        assert phase.tasks[0].text == "first task"
        assert phase.tasks[0].status is TaskStatus.TODO
        assert phase.tasks[0].line_number == 3
        assert phase.tasks[1].status is TaskStatus.DONE

    def test_phase_heading_keyword_normalized(self) -> None:
        # "Phase" and "Stage" are accepted interchangeably and the
        # keyword captures which form was used (capitalized).
        plan = parse_plan("# Phase 2: Implementation\n- [ ] x\n")
        assert plan.phases[0].keyword == "Phase"
        assert plan.phases[0].ordinal == 2
        assert plan.phases[0].title == "Implementation"

    def test_phase_with_non_bare_digit_id_is_not_a_phase_heading(self) -> None:
        # `## Phase phase_001:` is the legacy strict-form heading; per
        # design doc section 2.5 it does NOT match the bare-digit
        # STAGE_RE and so is invisible to the compat parser. The tasks
        # that follow have no enclosing phase and are dropped.
        text = "## Phase phase_001: Core\n- [ ] orphan task\n"
        plan = parse_plan(text)
        assert plan.phases == ()
        assert plan.bugs is None

    def test_indent_stack_builds_task_tree(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] parent\n"
            "  - [ ] child a\n"
            "    - [ ] grandchild\n"
            "  - [ ] child b\n"
            "- [ ] second root\n"
        )
        plan = parse_plan(text)
        phase = plan.phases[0]
        assert len(phase.tasks) == 2
        parent, second_root = phase.tasks
        assert parent.text == "parent"
        assert second_root.text == "second root"
        assert len(parent.children) == 2
        child_a, child_b = parent.children
        assert child_a.text == "child a"
        assert child_b.text == "child b"
        assert len(child_a.children) == 1
        assert child_a.children[0].text == "grandchild"

    def test_subsection_captures_following_tasks(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] phase task\n"
            "\n"
            "### Manual verification\n"
            "- [ ] sub task one\n"
            "- [ ] sub task two\n"
        )
        plan = parse_plan(text)
        phase = plan.phases[0]
        assert len(phase.tasks) == 1
        assert phase.tasks[0].text == "phase task"
        assert len(phase.subsections) == 1
        sub = phase.subsections[0]
        assert sub.title == "Manual verification"
        assert tuple(t.text for t in sub.tasks) == ("sub task one", "sub task two")

    def test_new_phase_resets_indent_stack(self) -> None:
        # An indented task in the second phase must be treated as a
        # root of that phase, not as a child of the previous phase's
        # last task. (mcloop's parser does this via stack.clear().)
        text = (
            "## Stage 1: A\n- [ ] one\n  - [ ] one-child\n## Stage 2: B\n  - [ ] two\n"
        )
        plan = parse_plan(text)
        assert len(plan.phases) == 2
        assert len(plan.phases[0].tasks) == 1
        assert plan.phases[0].tasks[0].children[0].text == "one-child"
        assert len(plan.phases[1].tasks) == 1
        assert plan.phases[1].tasks[0].text == "two"
        assert plan.phases[1].tasks[0].children == ()

    def test_bugs_section_collects_tasks(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] phase task\n"
            "## Bugs\n"
            "- [ ] crash on empty input\n"
            "- [x] fixed memory leak\n"
        )
        plan = parse_plan(text)
        assert plan.bugs is not None
        assert tuple(t.text for t in plan.bugs.tasks) == (
            "crash on empty input",
            "fixed memory leak",
        )
        # The phase still owns its own task; bugs is a peer section.
        assert plan.phases[0].tasks[0].text == "phase task"

    def test_task_body_classification_is_applied(self) -> None:
        text = (
            "## Stage 1: Core\n"
            '- [ ] T-000001: [BATCH] do thing [feat: "x"]\n'
            "- [ ] T-000002: [AUTO:run_cli] mcloop --dry-run\n"
        )
        plan = parse_plan(text)
        first, second = plan.phases[0].tasks
        assert first.task_id == "T-000001"
        assert first.flag_tags == ("BATCH",)
        assert first.annotations == (("feat", '"x"'),)
        assert first.text == "do thing"
        assert second.task_id == "T-000002"
        assert second.action_tag == ("run_cli", "mcloop --dry-run")
        assert second.text == ""

    def test_ruledout_attaches_to_parent_task(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] parent\n"
            "  [RULEDOUT] tried restart\n"
            "  [RULEDOUT] tried reinstall\n"
        )
        plan = parse_plan(text)
        parent = plan.phases[0].tasks[0]
        assert tuple(r.text for r in parent.ruled_out) == (
            "tried restart",
            "tried reinstall",
        )

    def test_deps_attaches_to_preceding_task(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] T-000001: first\n"
            "- [ ] T-000002: second\n"
            "  @deps T-000001\n"
        )
        plan = parse_plan(text)
        first, second = plan.phases[0].tasks
        assert first.deps == ()
        assert second.deps == ("T-000001",)

    def test_combined_phase_subsection_bugs_round_trip_structure(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] T-000001: [BATCH] parent\n"
            "  - [ ] T-000002: child a\n"
            "    [RULEDOUT] tried polling\n"
            "  - [ ] T-000003: child b\n"
            "    @deps T-000002\n"
            "\n"
            "### Manual verification\n"
            "- [ ] T-000004: [USER] run smoke test\n"
            "\n"
            "## Bugs\n"
            "- [ ] T-000009: crash on empty PLAN.md\n"
        )
        plan = parse_plan(text)
        assert len(plan.phases) == 1
        phase = plan.phases[0]
        parent = phase.tasks[0]
        assert parent.task_id == "T-000001"
        assert parent.flag_tags == ("BATCH",)
        child_a, child_b = parent.children
        assert child_a.task_id == "T-000002"
        assert tuple(r.text for r in child_a.ruled_out) == ("tried polling",)
        assert child_b.deps == ("T-000002",)
        assert len(phase.subsections) == 1
        manual = phase.subsections[0]
        assert manual.title == "Manual verification"
        assert manual.tasks[0].flag_tags == ("USER",)
        assert plan.bugs is not None
        assert plan.bugs.tasks[0].task_id == "T-000009"

    def test_tasks_before_any_section_are_dropped_in_compat_mode(self) -> None:
        # No phase or bugs heading has been seen yet, so a task line is
        # an orphan. Compat mode drops silently to match mcloop's
        # parser, which assigns ``stage=""`` rather than erroring.
        # Strict mode (Stage 3) will surface this as a PlanSyntaxError.
        plan = parse_plan("- [ ] orphan\n## Stage 1: Core\n- [ ] real\n")
        assert len(plan.phases) == 1
        assert tuple(t.text for t in plan.phases[0].tasks) == ("real",)

    def test_source_path_passes_through(self) -> None:
        path = Path("/tmp/PLAN.md")
        plan = parse_plan("## Stage 1: Core\n- [ ] x\n", source_path=path)
        assert plan.source_path == path


class TestParsePlanProse:
    """Tests for project title, preamble, phase prose, and subsection prose.

    Per design doc section 4.1 grammar, prose is allowed in three places:
    after the H1 (the preamble), after a phase heading (phase prose),
    and after a ``###`` subsection heading (subsection prose). Each
    region ends at the next structural boundary — phase/bugs heading
    for the preamble, first task or subsection for phase prose, and
    first task for subsection prose.
    """

    def test_h1_sets_project_title(self) -> None:
        plan = parse_plan("# My Project\n## Stage 1: Core\n")
        assert plan.project_title == "My Project"
        assert plan.preamble == ""

    def test_no_h1_means_empty_title(self) -> None:
        plan = parse_plan("## Stage 1: Core\n- [ ] x\n")
        assert plan.project_title == ""
        assert plan.preamble == ""

    def test_h2_with_stage_keyword_does_not_match_h1(self) -> None:
        # `## Stage 1: Core` is consumed by the phase regex before the
        # H1 check runs, so it does not leak into the title slot.
        plan = parse_plan("## Stage 1: Core\n- [ ] x\n")
        assert plan.project_title == ""
        assert len(plan.phases) == 1

    def test_preamble_between_h1_and_phase(self) -> None:
        text = "# Project\n\nIntro paragraph.\n\n## Stage 1: Core\n- [ ] task\n"
        plan = parse_plan(text)
        assert plan.project_title == "Project"
        assert plan.preamble == "Intro paragraph."

    def test_preamble_multi_paragraph_preserves_blank_lines(self) -> None:
        text = (
            "# Project\n\nFirst paragraph.\n\nSecond paragraph.\n\n## Stage 1: Core\n"
        )
        plan = parse_plan(text)
        assert plan.preamble == "First paragraph.\n\nSecond paragraph."

    def test_preamble_ends_at_bugs_heading(self) -> None:
        # Per the grammar, the preamble is followed by ``PhaseOrBugs+`` —
        # a bugs section is just as valid a terminator as a phase.
        text = "# Project\nIntro.\n## Bugs\n- [ ] crash\n"
        plan = parse_plan(text)
        assert plan.preamble == "Intro."
        assert plan.bugs is not None
        assert plan.bugs.tasks[0].text == "crash"

    def test_phase_prose_between_heading_and_first_task(self) -> None:
        text = "## Stage 1: Core\n\nThe goal is X.\n\n- [ ] task\n"
        plan = parse_plan(text)
        assert plan.phases[0].prose == "The goal is X."

    def test_phase_prose_ends_at_subsection(self) -> None:
        text = (
            "## Stage 1: Core\n\nPhase intro.\n\n### Manual verification\n- [ ] check\n"
        )
        plan = parse_plan(text)
        phase = plan.phases[0]
        assert phase.prose == "Phase intro."
        assert phase.subsections[0].prose == ""

    def test_subsection_prose_between_heading_and_first_task(self) -> None:
        text = (
            "## Stage 1: Core\n"
            "- [ ] phase task\n"
            "### Manual verification\n"
            "\n"
            "Run by hand.\n"
            "\n"
            "- [ ] check\n"
        )
        plan = parse_plan(text)
        sub = plan.phases[0].subsections[0]
        assert sub.prose == "Run by hand."

    def test_no_prose_when_task_immediately_follows_heading(self) -> None:
        text = "# Project\n## Stage 1: Core\n- [ ] task\n"
        plan = parse_plan(text)
        assert plan.preamble == ""
        assert plan.phases[0].prose == ""

    def test_lines_before_h1_are_dropped(self) -> None:
        # Outside any active prose region, prose is dropped — matches the
        # grammar (Preamble requires an H1 anchor).
        text = "noise line\n# Project\nIntro.\n## Stage 1: Core\n"
        plan = parse_plan(text)
        assert plan.project_title == "Project"
        assert plan.preamble == "Intro."

    def test_prose_after_first_task_in_phase_is_dropped(self) -> None:
        # Phase prose is "between the phase heading and the first task
        # or subsection". Lines after the first task close the
        # accumulator and are dropped in compat mode.
        text = "## Stage 1: Core\n- [ ] task\nstray prose\n"
        plan = parse_plan(text)
        assert plan.phases[0].prose == ""
        assert plan.phases[0].tasks[0].text == "task"

    def test_multiple_phases_each_get_own_prose(self) -> None:
        text = (
            "## Stage 1: A\n"
            "Prose A.\n"
            "- [ ] task A\n"
            "## Stage 2: B\n"
            "Prose B.\n"
            "- [ ] task B\n"
        )
        plan = parse_plan(text)
        assert plan.phases[0].prose == "Prose A."
        assert plan.phases[1].prose == "Prose B."

    def test_phase_prose_at_end_of_file_without_tasks(self) -> None:
        # Final phase has prose but no task to close the accumulator —
        # the end-of-input close handler must still finalize it.
        text = "## Stage 1: Core\n\nLone prose.\n"
        plan = parse_plan(text)
        assert plan.phases[0].prose == "Lone prose."
        assert plan.phases[0].tasks == ()


class TestParsePlanMinimalValidPlan:
    """End-to-end tests on hand-crafted whole-document inputs.

    Earlier classes drive individual responsibilities of ``parse_plan``
    (state machine, prose accumulators, syntax errors). This class
    closes the loop with realistic minimal plans that exercise every
    structural region together — title, preamble, phase prose, phase
    tasks, subsection, Bugs section — and pins the compat-mode
    tolerances we keep for parity with mcloop's ``parse``.

    Two of the cases listed in the task description for this increment
    (missing H1 raises; tasks before any phase land in an implicit
    phase zero) describe behavior the compat parser does **not**
    implement. Stage 2 is compat mode and stays permissive in those
    cases; Stage 3's strict mode will tighten both. The tests below
    pin the actual compat-mode behavior so a future strict-mode
    refactor cannot silently change it. See NOTES.md [2.5.5].
    """

    def test_minimal_valid_plan_parses_correctly(self) -> None:
        text = (
            "# Demo Project\n"
            "\n"
            "Intro paragraph.\n"
            "\n"
            "## Stage 1: Core\n"
            "\n"
            "The goal is X.\n"
            "\n"
            "- [ ] T-000001: first task\n"
            "- [x] T-000002: second task\n"
            "\n"
            "### Manual verification\n"
            "\n"
            "- [ ] T-000003: [USER] verify by hand\n"
            "\n"
            "## Bugs\n"
            "\n"
            "- [ ] T-000009: crash on empty input\n"
        )
        plan = parse_plan(text)

        assert plan.project_title == "Demo Project"
        assert plan.preamble == "Intro paragraph."
        assert plan.magic_version is None
        assert plan.source_path is None

        assert len(plan.phases) == 1
        phase = plan.phases[0]
        assert phase.ordinal == 1
        assert phase.keyword == "Stage"
        assert phase.title == "Core"
        assert phase.prose == "The goal is X."
        assert tuple(t.text for t in phase.tasks) == ("first task", "second task")
        assert phase.tasks[0].task_id == "T-000001"
        assert phase.tasks[0].status is TaskStatus.TODO
        assert phase.tasks[1].task_id == "T-000002"
        assert phase.tasks[1].status is TaskStatus.DONE

        assert len(phase.subsections) == 1
        manual = phase.subsections[0]
        assert manual.title == "Manual verification"
        assert tuple(t.text for t in manual.tasks) == ("verify by hand",)
        assert manual.tasks[0].flag_tags == ("USER",)

        assert plan.bugs is not None
        assert tuple(t.text for t in plan.bugs.tasks) == ("crash on empty input",)
        assert plan.bugs.tasks[0].task_id == "T-000009"

    def test_missing_h1_does_not_raise_in_compat_mode(self) -> None:
        # Compat mode follows mcloop's ``parse``, which has no H1 concept
        # and never errors on its absence. ``project_title`` falls back
        # to the empty string. Strict mode (Stage 3) will require an H1
        # and raise PlanSyntaxError when it is missing.
        plan = parse_plan("## Stage 1: Core\n- [ ] task\n")
        assert plan.project_title == ""
        assert plan.preamble == ""
        assert len(plan.phases) == 1
        assert plan.phases[0].tasks[0].text == "task"

    def test_tasks_before_any_phase_dropped_not_implicit_phase_zero(self) -> None:
        # The Stage 2 task description floats "implicit phase zero" as
        # the compat-mode home for orphan tasks. The typed model has no
        # phase-zero slot, and ``Phase`` requires an ordinal heading;
        # the current implementation drops these lines silently to match
        # mcloop's effective ``stage=""`` behavior (the parsed tasks are
        # discarded by downstream consumers that expect a stage). Pinned
        # here so that strict mode (Stage 3) — which will raise — is a
        # deliberate change rather than an accidental one.
        text = "- [ ] orphan one\n- [ ] orphan two\n## Stage 1: Core\n- [ ] real\n"
        plan = parse_plan(text)
        assert len(plan.phases) == 1
        assert tuple(t.text for t in plan.phases[0].tasks) == ("real",)

    def test_bugs_section_after_phases_is_recognized(self) -> None:
        # The Bugs heading at any heading level closes the active phase
        # and opens the bugs section; subsequent tasks live there until
        # EOF (no further phase heading is expected after Bugs per the
        # grammar, but the parser does not enforce that in compat mode).
        text = (
            "## Stage 1: Core\n"
            "- [ ] phase one task\n"
            "## Stage 2: Polish\n"
            "- [ ] phase two task\n"
            "## Bugs\n"
            "- [ ] bug one\n"
            "- [x] bug two\n"
        )
        plan = parse_plan(text)
        assert len(plan.phases) == 2
        assert tuple(t.text for t in plan.phases[0].tasks) == ("phase one task",)
        assert tuple(t.text for t in plan.phases[1].tasks) == ("phase two task",)
        assert plan.bugs is not None
        assert tuple(t.text for t in plan.bugs.tasks) == ("bug one", "bug two")
        assert plan.bugs.tasks[0].status is TaskStatus.TODO
        assert plan.bugs.tasks[1].status is TaskStatus.DONE

    def test_multiple_subsections_each_preserve_their_tasks(self) -> None:
        # Each ``###`` opens a fresh subsection scope and captures the
        # following tasks until the next subsection or phase boundary.
        # Indent inheritance is per-subsection: the indent stack is
        # cleared at the subsection boundary so child tasks in one
        # subsection do not bleed into the next.
        text = (
            "## Stage 1: Core\n"
            "- [ ] direct phase task\n"
            "### First sub\n"
            "- [ ] sub-1-a\n"
            "  - [ ] sub-1-a-child\n"
            "- [x] sub-1-b\n"
            "### Second sub\n"
            "- [ ] sub-2-a\n"
        )
        plan = parse_plan(text)
        phase = plan.phases[0]
        assert tuple(t.text for t in phase.tasks) == ("direct phase task",)
        assert len(phase.subsections) == 2

        first, second = phase.subsections
        assert first.title == "First sub"
        assert tuple(t.text for t in first.tasks) == ("sub-1-a", "sub-1-b")
        assert tuple(c.text for c in first.tasks[0].children) == ("sub-1-a-child",)
        assert first.tasks[1].status is TaskStatus.DONE

        assert second.title == "Second sub"
        assert tuple(t.text for t in second.tasks) == ("sub-2-a",)
        assert second.tasks[0].children == ()


class TestParsePlanCompatModeSyntaxErrors:
    """Compat-mode raises :class:`PlanSyntaxError` on genuine syntax breakage.

    Compat mode is intentionally lenient about cases mcloop's parser
    accepted (orphan tasks before any phase, prose outside accumulators,
    etc.). But a ``@deps`` line with no preceding task to attach to has
    no semantic interpretation — the keyword means "this task depends
    on" and there is no task. mcloop never recognized ``@deps`` at all,
    so there is no compat behavior to preserve. The parser raises
    :class:`PlanSyntaxError` with the offending line quoted in the
    message and the source line/column populated.
    """

    def test_orphan_deps_line_before_any_task_raises(self) -> None:
        text = "## Stage 1: Core\n  @deps T-000001\n"
        with pytest.raises(PlanSyntaxError) as exc_info:
            parse_plan(text)
        err = exc_info.value
        assert err.line == 2
        assert err.column == 3
        assert "no preceding task" in err.message
        assert "`  @deps T-000001`" in err.message

    def test_orphan_deps_with_no_phase_raises(self) -> None:
        # Even before any section, a stray @deps is malformed: there is
        # no plausible target. The orphan-task tolerance does not extend
        # here because @deps has no mcloop precedent.
        with pytest.raises(PlanSyntaxError) as exc_info:
            parse_plan("@deps T-000001\n")
        assert exc_info.value.line == 1
        assert exc_info.value.column == 1

    def test_syntax_error_carries_source_path(self) -> None:
        path = Path("/tmp/PLAN.md")
        with pytest.raises(PlanSyntaxError) as exc_info:
            parse_plan("@deps T-000001\n", source_path=path)
        assert exc_info.value.path == path

    def test_syntax_error_str_format_matches_design_doc(self) -> None:
        # Section 9 contract: "PLAN.md invalid at line N, column M: ..."
        with pytest.raises(PlanSyntaxError) as exc_info:
            parse_plan("@deps T-000001\n")
        rendered = str(exc_info.value)
        assert rendered.startswith("PLAN.md invalid at line 1, column 1: ")
        assert "@deps T-000001" in rendered
