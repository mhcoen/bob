"""Tests for duplo.gap_detector."""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.doc_examples import CodeExample
from duplo.extractor import Feature
from duplo.gap_detector import (
    DesignRefinement,
    GapResult,
    MissingExample,
    MissingFeature,
    _format_examples,
    _format_features,
    _parse_design_markdown,
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
        assert "\u2026" in text
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


class TestParseResultRobustness:
    """Parser round-trips across fenced, prose-prefixed, and trailing-whitespace inputs."""

    _PAYLOAD = {
        "missing_features": [{"name": "Search", "reason": "Not in plan"}],
        "missing_examples": [],
    }

    def _assert_round_trips(self, raw: str) -> None:
        result = _parse_result(raw, [_feat("Search")], [])
        assert len(result.missing_features) == 1
        assert result.missing_features[0].name == "Search"
        assert result.missing_features[0].reason == "Not in plan"

    def test_fenced_json_round_trips(self):
        raw = f"```json\n{json.dumps(self._PAYLOAD)}\n```"
        self._assert_round_trips(raw)

    def test_prose_prefixed_round_trips(self):
        raw = f"Here is my assessment of the gaps:\n\n{json.dumps(self._PAYLOAD)}"
        self._assert_round_trips(raw)

    def test_trailing_whitespace_round_trips(self):
        raw = f"{json.dumps(self._PAYLOAD)}\n  \t\n"
        self._assert_round_trips(raw)


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
    def test_returns_empty_when_no_features_or_examples(self):
        result = detect_gaps("# Plan", [], None)
        assert result.missing_features == []
        assert result.missing_examples == []

    def test_calls_query_with_plan_and_features(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        with patch("duplo.gap_detector.query", return_value=response) as mock_query:
            features = [_feat("Search")]
            detect_gaps("# Plan\n- [ ] Build search", features)
        prompt = mock_query.call_args[0][0]
        assert "Plan" in prompt
        assert "Search" in prompt

    def test_includes_examples_in_prompt(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        with patch("duplo.gap_detector.query", return_value=response) as mock_query:
            examples = [_example("print('test')")]
            detect_gaps("# Plan", [_feat("X")], examples)
        prompt = mock_query.call_args[0][0]
        assert "print('test')" in prompt

    def test_includes_platform_in_prompt(self):
        response = json.dumps({"missing_features": [], "missing_examples": []})
        with patch("duplo.gap_detector.query", return_value=response) as mock_query:
            detect_gaps("# Plan", [_feat("X")], platform="macOS", language="Swift")
        prompt = mock_query.call_args[0][0]
        assert "macOS" in prompt
        assert "Swift" in prompt

    def test_returns_parsed_gaps(self):
        response = json.dumps(
            {
                "missing_features": [{"name": "Export", "reason": "Not in plan"}],
                "missing_examples": [],
            }
        )
        with patch("duplo.gap_detector.query", return_value=response):
            result = detect_gaps("# Plan", [_feat("Export")])
        assert len(result.missing_features) == 1
        assert result.missing_features[0].name == "Export"


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
    def test_includes_design_refinements_grouped_by_category(self):
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
        assert "- [ ] Update color palette: primary: #ff0000" in text
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
        assert "Update typography: body: Roboto" in text

    def test_consolidates_multiple_colors_into_one_task(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[],
            design_refinements=[
                DesignRefinement(
                    category="color",
                    detail="primary: #ff0000",
                    reason="not in plan",
                ),
                DesignRefinement(
                    category="color",
                    detail="secondary: #00ff00",
                    reason="not in plan",
                ),
                DesignRefinement(
                    category="color",
                    detail="accent: #0000ff",
                    reason="not in plan",
                ),
            ],
        )
        text = format_gap_tasks(result)
        # All colors in one task, not three separate tasks.
        color_tasks = [line for line in text.splitlines() if "color palette" in line.lower()]
        assert len(color_tasks) == 1
        assert "primary: #ff0000" in color_tasks[0]
        assert "secondary: #00ff00" in color_tasks[0]
        assert "accent: #0000ff" in color_tasks[0]

    def test_groups_by_category_separately(self):
        result = GapResult(
            missing_features=[],
            missing_examples=[],
            design_refinements=[
                DesignRefinement(category="color", detail="bg: #fff", reason=""),
                DesignRefinement(category="font", detail="body: Inter", reason=""),
                DesignRefinement(category="color", detail="fg: #000", reason=""),
            ],
        )
        text = format_gap_tasks(result)
        task_lines = [line for line in text.splitlines() if line.startswith("- [ ]")]
        assert len(task_lines) == 2  # one color task, one font task


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

        for line in original.strip().splitlines():
            assert line in updated
        assert "- [ ] Implement Search" in updated
        assert updated.startswith(original.rstrip())

    def test_gap_tasks_only_contain_unchecked_items(self):
        """Gap tasks must never contain checked items that could mask originals."""
        result = GapResult(
            missing_features=[MissingFeature(name="A", reason="r")],
            missing_examples=[MissingExample(index=0, summary="ex", reason="r")],
            design_refinements=[DesignRefinement(category="color", detail="x", reason="r")],
        )
        text = format_gap_tasks(result)
        for line in text.splitlines():
            if line.startswith("- "):
                assert line.startswith("- [ ]"), f"Non-unchecked task: {line!r}"


# ---------------------------------------------------------------------------
# _parse_design_markdown
# ---------------------------------------------------------------------------


class TestParseDesignMarkdown:
    def test_empty_string(self):
        assert _parse_design_markdown("") == {}

    def test_whitespace_only(self):
        assert _parse_design_markdown("   \n  ") == {}

    def test_parses_colors_with_backtick_values(self):
        text = "### Colors\n- **primary**: `#1a73e8`\n- **background**: `#ffffff`"
        result = _parse_design_markdown(text)
        assert result["colors"] == {"primary": "#1a73e8", "background": "#ffffff"}

    def test_parses_fonts(self):
        text = "### Typography\n- **body**: Inter, sans-serif ~16px"
        result = _parse_design_markdown(text)
        assert result["fonts"] == {"body": "Inter, sans-serif ~16px"}

    def test_parses_spacing(self):
        text = "### Spacing\n- **content_padding**: 16px"
        result = _parse_design_markdown(text)
        assert result["spacing"] == {"content_padding": "16px"}

    def test_parses_layout(self):
        text = "### Layout\n- **navigation**: top\n- **sidebar**: left"
        result = _parse_design_markdown(text)
        assert result["layout"] == {"navigation": "top", "sidebar": "left"}

    def test_parses_components(self):
        text = (
            "### Component Styles\n- **card**: rounded corners, shadow\n- **button**: pill shape"
        )
        result = _parse_design_markdown(text)
        assert len(result["components"]) == 2
        assert result["components"][0] == {"name": "card", "style": "rounded corners, shadow"}
        assert result["components"][1] == {"name": "button", "style": "pill shape"}

    def test_component_without_style(self):
        text = "### Component Styles\n- **modal**:"
        result = _parse_design_markdown(text)
        assert result["components"] == [{"name": "modal"}]

    def test_multiple_sections(self):
        text = (
            "### Colors\n- **primary**: `#ff0000`\n\n"
            "### Typography\n- **headings**: Roboto\n\n"
            "### Component Styles\n- **card**: rounded"
        )
        result = _parse_design_markdown(text)
        assert result["colors"] == {"primary": "#ff0000"}
        assert result["fonts"] == {"headings": "Roboto"}
        assert result["components"] == [{"name": "card", "style": "rounded"}]

    def test_ignores_unknown_heading(self):
        text = "### Unknown Section\n- **key**: value"
        result = _parse_design_markdown(text)
        assert result == {}

    def test_ignores_non_bold_lines(self):
        text = "### Colors\n- plain line without bold\n- **primary**: `#000`"
        result = _parse_design_markdown(text)
        assert result["colors"] == {"primary": "#000"}

    def test_roundtrip_with_format_design_block(self):
        """Parsing the output of format_design_block recovers the same data."""
        from duplo.design_extractor import DesignRequirements, format_design_block

        original = DesignRequirements(
            colors={"primary": "#1a73e8", "bg": "#ffffff"},
            fonts={"body": "Inter"},
            spacing={"gap": "16px"},
            layout={"navigation": "top"},
            components=[{"name": "card", "style": "rounded"}],
        )
        block = format_design_block(original)
        parsed = _parse_design_markdown(block)
        assert parsed["colors"] == original.colors
        assert parsed["fonts"] == original.fonts
        assert parsed["spacing"] == original.spacing
        assert parsed["layout"] == original.layout
        assert parsed["components"] == original.components


# ---------------------------------------------------------------------------
# Pipeline: _parse_design_markdown + detect_design_gaps (SPEC.md only)
# ---------------------------------------------------------------------------


class TestDesignGapsSpecOnly:
    """Verify detect_design_gaps finds gaps from SPEC.md AUTO-GENERATED block only."""

    def test_gaps_from_spec_markdown(self):
        """Items from spec markdown produce gaps when not in the plan."""
        spec_markdown = "### Colors\n- **accent**: `#0000ff`\n\n### Typography\n- **body**: Roboto"
        spec_design = _parse_design_markdown(spec_markdown)
        plan = "# Phase 0\n- [ ] Build basic UI\n"
        gaps = detect_design_gaps(plan, spec_design)
        categories = {g.category for g in gaps}
        details = {g.detail for g in gaps}
        assert "color" in categories
        assert any("#0000ff" in d for d in details)
        assert "font" in categories
        assert any("Roboto" in d for d in details)

    def test_no_gaps_when_plan_covers_spec(self):
        """No gaps reported when the plan mentions items from the spec."""
        spec_markdown = "### Colors\n- **accent**: `#0000ff`"
        spec_design = _parse_design_markdown(spec_markdown)
        plan = "Use #0000ff for accent."
        gaps = detect_design_gaps(plan, spec_design)
        assert gaps == []

    def test_empty_spec_produces_no_gaps(self):
        """Empty spec markdown means no design gaps."""
        spec_design = _parse_design_markdown("")
        gaps = detect_design_gaps("# Plan", spec_design)
        assert gaps == []

    def test_spec_colors_and_components(self):
        """Spec with colors and components both produce gaps."""
        spec_markdown = (
            "### Colors\n- **bg**: `#fafafa`\n\n### Component Styles\n- **card**: rounded corners"
        )
        spec_design = _parse_design_markdown(spec_markdown)
        gaps = detect_design_gaps("# Plan", spec_design)
        assert len(gaps) == 2
        categories = {g.category for g in gaps}
        assert "color" in categories
        assert "component" in categories
