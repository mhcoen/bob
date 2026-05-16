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

import pytest

from bob_tools.planfile.parser import (
    _CHECKBOX_RE,
    _attach_ruledout,
    _extract_action_tag,
    _extract_annotations,
    _extract_flag_tags,
    _parse_ruledout_line,
    _parse_task_line,
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
