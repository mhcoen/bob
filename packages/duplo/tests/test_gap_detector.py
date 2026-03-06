"""Tests for duplo.gap_detector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from duplo.doc_examples import CodeExample
from duplo.extractor import Feature
from duplo.gap_detector import (
    DesignRefinement,
    GapResult,
    MissingExample,
    MissingFeature,
    _format_examples,
    _format_features,
    _parse_result,
    detect_design_gaps,
    detect_gaps,
    format_gap_tasks,
)


def _feat(name: str, desc: str = "Does something.", cat: str = "core") -> Feature:
    return Feature(name=name, description=desc, category=cat)


def _example(inp: str = "print('hi')", out: str = "hi", url: str = "") -> CodeExample:
    return CodeExample(input=inp, expected_output=out, source_url=url, language="python")


# ---------------------------------------------------------------------------
# _format_features
# ---------------------------------------------------------------------------


class TestFormatFeatures:
    def test_numbered_list(self):
        features = [_feat("Search"), _feat("Auth", cat="security")]
        text = _format_features(features)
        assert "1. [core] Search:" in text
        assert "2. [security] Auth:" in text

    def test_empty(self):
        assert _format_features([]) == ""


# ---------------------------------------------------------------------------
# _format_examples
# ---------------------------------------------------------------------------


class TestFormatExamples:
    def test_numbered_from_zero(self):
        examples = [_example("code1"), _example("code2")]
        text = _format_examples(examples)
        assert text.startswith("0. code1")
        assert "1. code2" in text

    def test_truncates_long_input(self):
        ex = _example("x" * 300)
        text = _format_examples([ex])
        assert "…" in text
        assert len(text) < 300

    def test_includes_source_url(self):
        ex = _example(url="https://example.com/docs")
        text = _format_examples([ex])
        assert "(from https://example.com/docs)" in text


# ---------------------------------------------------------------------------
# _parse_result
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_parses_valid_response(self):
        raw = json.dumps(
            {
                "missing_features": [
                    {"name": "Dark mode", "reason": "Not in plan"},
                ],
                "missing_examples": [
                    {"index": 0, "summary": "hello world", "reason": "Not tested"},
                ],
            }
        )
        features = [_feat("Dark mode")]
        examples = [_example()]
        result = _parse_result(raw, features, examples)
        assert len(result.missing_features) == 1
        assert result.missing_features[0].name == "Dark mode"
        assert len(result.missing_examples) == 1
        assert result.missing_examples[0].index == 0

    def test_strips_code_fence(self):
        inner = json.dumps(
            {"missing_features": [{"name": "X", "reason": "missing"}], "missing_examples": []}
        )
        raw = f"```json\n{inner}\n```"
        result = _parse_result(raw, [_feat("X")], [])
        assert len(result.missing_features) == 1

    def test_returns_empty_on_invalid_json(self):
        result = _parse_result("not json", [], [])
        assert result.missing_features == []
        assert result.missing_examples == []

    def test_returns_empty_on_non_dict(self):
        result = _parse_result("[1, 2, 3]", [], [])
        assert result.missing_features == []

    def test_skips_example_with_out_of_range_index(self):
        raw = json.dumps(
            {
                "missing_features": [],
                "missing_examples": [
                    {"index": 99, "summary": "bad", "reason": "out of range"},
                ],
            }
        )
        result = _parse_result(raw, [], [_example()])
        assert result.missing_examples == []

    def test_skips_example_with_negative_index(self):
        raw = json.dumps(
            {
                "missing_features": [],
                "missing_examples": [
                    {"index": -1, "summary": "bad", "reason": "negative"},
                ],
            }
        )
        result = _parse_result(raw, [], [_example()])
        assert result.missing_examples == []

    def test_skips_feature_without_name(self):
        raw = json.dumps({"missing_features": [{"reason": "no name"}], "missing_examples": []})
        result = _parse_result(raw, [], [])
        assert result.missing_features == []

    def test_skips_non_dict_items(self):
        raw = json.dumps({"missing_features": ["not a dict"], "missing_examples": [42]})
        result = _parse_result(raw, [], [])
        assert result.missing_features == []
        assert result.missing_examples == []

    def test_empty_arrays(self):
        raw = json.dumps({"missing_features": [], "missing_examples": []})
        result = _parse_result(raw, [], [])
        assert result.missing_features == []
        assert result.missing_examples == []


# ---------------------------------------------------------------------------
# format_gap_tasks
# ---------------------------------------------------------------------------


class TestFormatGapTasks:
    def test_empty_result_returns_empty_string(self):
        result = GapResult(missing_features=[], missing_examples=[])
        assert format_gap_tasks(result) == ""

    def test_formats_missing_features(self):
        result = GapResult(
            missing_features=[MissingFeature(name="Dark mode", reason="Not in plan")],
            missing_examples=[],
        )
        text = format_gap_tasks(result)
        assert "- [ ] Implement Dark mode (Not in plan)" in text
        assert "## Gaps detected" in text

    def test_formats_missing_examples(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[
                MissingExample(index=0, summary="hello world demo", reason="No test")
            ],
        )
        text = format_gap_tasks(result)
        assert "- [ ] Add test/implementation for hello world demo (No test)" in text

    def test_example_without_summary_uses_index(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[MissingExample(index=3, summary="", reason="missing")],
        )
        text = format_gap_tasks(result)
        assert "example #3" in text

    def test_feature_without_reason(self):
        result = GapResult(
            missing_features=[MissingFeature(name="Export", reason="")],
            missing_examples=[],
        )
        text = format_gap_tasks(result)
        assert "- [ ] Implement Export\n" in text
        assert "()" not in text


# ---------------------------------------------------------------------------
# detect_gaps
# ---------------------------------------------------------------------------


class TestDetectGaps:
    def _make_client(self, json_response: str) -> MagicMock:
        content_block = MagicMock()
        content_block.text = json_response
        message = MagicMock()
        message.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = message
        return client

    def test_returns_empty_when_no_features_or_examples(self):
        result = detect_gaps("# Plan", [], None)
        assert result.missing_features == []
        assert result.missing_examples == []

    def test_calls_api_with_plan_and_features(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        client = self._make_client(response)
        features = [_feat("Search")]
        detect_gaps("# Plan\n- [ ] Build search", features, client=client)
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Plan" in user_msg
        assert "Search" in user_msg

    def test_includes_examples_in_prompt(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        client = self._make_client(response)
        examples = [_example("print('test')")]
        detect_gaps("# Plan", [_feat("X")], examples, client=client)
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "print('test')" in user_msg

    def test_returns_parsed_gaps(self):
        response = json.dumps(
            {
                "missing_features": [{"name": "Export", "reason": "Not in plan"}],
                "missing_examples": [],
            }
        )
        client = self._make_client(response)
        result = detect_gaps("# Plan", [_feat("Export")], client=client)
        assert len(result.missing_features) == 1
        assert result.missing_features[0].name == "Export"

    def test_creates_default_client_when_none(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        mock_client = self._make_client(response)
        with patch("duplo.gap_detector.anthropic.Anthropic", return_value=mock_client):
            result = detect_gaps("# Plan", [_feat("X")])
        assert result.missing_features == []


# ---------------------------------------------------------------------------
# detect_design_gaps
# ---------------------------------------------------------------------------


class TestDetectDesignGaps:
    def test_returns_empty_for_empty_design(self):
        assert detect_design_gaps("# Plan", {}) == []

    def test_detects_missing_color(self):
        design = {"colors": {"primary": "#1a73e8"}}
        gaps = detect_design_gaps("# Plan\n- [ ] Build UI", design)
        assert len(gaps) == 1
        assert gaps[0].category == "color"
        assert "#1a73e8" in gaps[0].detail

    def test_skips_color_already_in_plan(self):
        design = {"colors": {"primary": "#1a73e8"}}
        plan = "# Plan\nUse primary color #1a73e8 for buttons."
        gaps = detect_design_gaps(plan, design)
        assert len(gaps) == 0

    def test_case_insensitive_matching(self):
        design = {"colors": {"primary": "#AABBCC"}}
        plan = "Use color #aabbcc for the header."
        gaps = detect_design_gaps(plan, design)
        assert len(gaps) == 0

    def test_detects_missing_font(self):
        design = {"fonts": {"body": "Inter, sans-serif"}}
        gaps = detect_design_gaps("# Plan\n- [ ] Style text", design)
        assert len(gaps) == 1
        assert gaps[0].category == "font"
        assert "Inter, sans-serif" in gaps[0].detail

    def test_skips_font_already_in_plan(self):
        design = {"fonts": {"body": "Inter"}}
        plan = "Use Inter for body text."
        gaps = detect_design_gaps(plan, design)
        assert len(gaps) == 0

    def test_detects_missing_component(self):
        design = {"components": [{"name": "card", "style": "rounded corners, shadow"}]}
        gaps = detect_design_gaps("# Plan\n- [ ] Build UI", design)
        assert len(gaps) == 1
        assert gaps[0].category == "component"
        assert "card" in gaps[0].detail

    def test_skips_component_already_in_plan(self):
        design = {"components": [{"name": "card", "style": "rounded"}]}
        plan = "# Plan\n- [ ] Implement card component with rounded corners."
        gaps = detect_design_gaps(plan, design)
        assert len(gaps) == 0

    def test_skips_non_dict_components(self):
        design = {"components": ["not a dict", 42]}
        gaps = detect_design_gaps("# Plan", design)
        assert len(gaps) == 0

    def test_skips_component_without_name(self):
        design = {"components": [{"style": "rounded"}]}
        gaps = detect_design_gaps("# Plan", design)
        assert len(gaps) == 0

    def test_multiple_categories(self):
        design = {
            "colors": {"primary": "#ff0000"},
            "fonts": {"body": "Roboto"},
            "components": [{"name": "button", "style": "pill shape"}],
        }
        gaps = detect_design_gaps("# Plan\nEmpty plan", design)
        assert len(gaps) == 3
        categories = {g.category for g in gaps}
        assert categories == {"color", "font", "component"}

    def test_component_without_style(self):
        design = {"components": [{"name": "modal"}]}
        gaps = detect_design_gaps("# Plan", design)
        assert len(gaps) == 1
        assert gaps[0].detail == "modal"


# ---------------------------------------------------------------------------
# format_gap_tasks with design refinements
# ---------------------------------------------------------------------------


class TestFormatGapTasksDesign:
    def test_includes_design_refinements(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[],
            design_refinements=[
                DesignRefinement(
                    category="color",
                    detail="primary: #ff0000",
                    reason="Color #ff0000 not in plan",
                )
            ],
        )
        text = format_gap_tasks(result)
        assert "- [ ] Update design: primary: #ff0000" in text
        assert "## Gaps detected" in text

    def test_empty_when_no_gaps_at_all(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[],
            design_refinements=[],
        )
        assert format_gap_tasks(result) == ""

    def test_mixed_features_and_design(self):
        result = GapResult(
            missing_features=[MissingFeature(name="Export", reason="Not in plan")],
            missing_examples=[],
            design_refinements=[
                DesignRefinement(
                    category="font",
                    detail="body: Roboto",
                    reason="Font not in plan",
                )
            ],
        )
        text = format_gap_tasks(result)
        assert "Implement Export" in text
        assert "Update design: body: Roboto" in text


# ---------------------------------------------------------------------------
# format_gap_tasks append-only invariant
# ---------------------------------------------------------------------------


class TestFormatGapTasksAppendOnly:
    """Verify that gap tasks can be appended without altering existing content."""

    def test_appending_preserves_original_plan(self):
        """Simulates the append operation and checks original content survives."""
        original = "# Phase 1\n- [x] Completed task\n- [ ] Pending task\n"
        result = GapResult(
            missing_features=[MissingFeature(name="Search", reason="Missing")],
            missing_examples=[],
        )
        gap_text = format_gap_tasks(result)
        updated = original.rstrip() + "\n" + gap_text

        # Every original line preserved.
        for line in original.strip().splitlines():
            assert line in updated
        # New task present.
        assert "- [ ] Implement Search" in updated
        # Original is a prefix.
        assert updated.startswith(original.rstrip())

    def test_gap_tasks_only_contain_unchecked_items(self):
        """Gap tasks must never contain checked items that could mask originals."""
        result = GapResult(
            missing_features=[MissingFeature(name="A", reason="r")],
            missing_examples=[MissingExample(index=0, summary="ex", reason="r")],
            design_refinements=[DesignRefinement(category="color", detail="x", reason="r")],
        )
        text = format_gap_tasks(result)
        # All task lines are unchecked.
        for line in text.splitlines():
            if line.startswith("- "):
                assert line.startswith("- [ ]"), f"Non-unchecked task: {line!r}"
