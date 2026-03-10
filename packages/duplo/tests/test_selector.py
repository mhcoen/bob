"""Tests for duplo.selector."""

from __future__ import annotations

from duplo.extractor import Feature
from duplo.selector import (
    _parse_selection,
    _print_features,
    _recommended_indices,
    select_features,
)


def make_features(*specs: tuple[str, str, str]) -> list[Feature]:
    return [Feature(name=n, description=d, category=c) for n, d, c in specs]


SAMPLE = make_features(
    ("Search", "Full-text search.", "core"),
    ("REST API", "CRUD via JSON API.", "api"),
    ("SSO", "Single sign-on.", "security"),
    ("Dark mode", "Dark UI theme.", "ui"),
    ("Webhooks", "Event webhooks.", "integrations"),
)


# ---------------------------------------------------------------------------
# _parse_selection
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_single_number(self):
        assert _parse_selection("2", 5) == {1}

    def test_comma_separated(self):
        assert _parse_selection("1,3,5", 5) == {0, 2, 4}

    def test_range(self):
        assert _parse_selection("2-4", 5) == {1, 2, 3}

    def test_range_and_single(self):
        assert _parse_selection("1-2,5", 5) == {0, 1, 4}

    def test_out_of_range_ignored(self):
        assert _parse_selection("0,6", 5) == set()

    def test_invalid_token_ignored(self):
        assert _parse_selection("abc", 5) == set()

    def test_empty_string(self):
        assert _parse_selection("", 5) == set()

    def test_spaces_around_tokens(self):
        assert _parse_selection(" 1 , 3 ", 5) == {0, 2}

    def test_invalid_range_ignored(self):
        assert _parse_selection("a-b", 5) == set()

    def test_range_reversed_still_works(self):
        # "4-2" should select features 2, 3, 4 (indices 1, 2, 3)
        assert _parse_selection("4-2", 5) == {1, 2, 3}


# ---------------------------------------------------------------------------
# _print_features
# ---------------------------------------------------------------------------


class TestPrintFeatures:
    def test_prints_all_features(self):
        lines: list[str] = []
        _print_features(SAMPLE, lines.append)
        combined = "\n".join(lines)
        for f in SAMPLE:
            assert f.name in combined
            assert f.description in combined

    def test_groups_by_category(self):
        lines: list[str] = []
        _print_features(SAMPLE, lines.append)
        combined = "\n".join(lines)
        assert "[CORE]" in combined
        assert "[API]" in combined
        assert "[SECURITY]" in combined

    def test_category_order(self):
        lines: list[str] = []
        _print_features(SAMPLE, lines.append)
        combined = "\n".join(lines)
        # core should appear before api
        assert combined.index("[CORE]") < combined.index("[API]")

    def test_unknown_category_shown_as_other(self):
        features = make_features(("Widget", "A widget.", "experimental"))
        lines: list[str] = []
        _print_features(features, lines.append)
        assert "[OTHER]" in "\n".join(lines)

    def test_recommended_features_marked(self):
        lines: list[str] = []
        _print_features(SAMPLE, lines.append, recommended={"Search", "SSO"})
        combined = "\n".join(lines)
        assert "Search *" in combined
        assert "SSO *" in combined
        # Non-recommended should not have marker.
        assert "REST API *" not in combined

    def test_no_marker_without_recommended(self):
        lines: list[str] = []
        _print_features(SAMPLE, lines.append)
        combined = "\n".join(lines)
        assert " *" not in combined


# ---------------------------------------------------------------------------
# select_features
# ---------------------------------------------------------------------------


class TestSelectFeatures:
    def _run(self, features, user_input):
        lines: list[str] = []
        result = select_features(features, input_fn=lambda _: user_input, print_fn=lines.append)
        return result, lines

    def test_all_keyword(self):
        result, _ = self._run(SAMPLE, "all")
        assert result == SAMPLE

    def test_blank_input_returns_all(self):
        result, _ = self._run(SAMPLE, "")
        assert result == SAMPLE

    def test_none_keyword_returns_empty(self):
        result, _ = self._run(SAMPLE, "none")
        assert result == []

    def test_numeric_selection(self):
        result, _ = self._run(SAMPLE, "1,3")
        assert len(result) == 2
        assert result[0].name == "Search"
        assert result[1].name == "SSO"

    def test_range_selection(self):
        result, _ = self._run(SAMPLE, "1-3")
        assert len(result) == 3

    def test_empty_feature_list(self):
        result, lines = self._run([], "all")
        assert result == []
        assert any("No features" in line for line in lines)

    def test_prints_summary_count(self):
        _, lines = self._run(SAMPLE, "1,2")
        summary = "\n".join(lines)
        assert "2 of 5" in summary

    def test_selected_features_are_sorted_by_original_order(self):
        # selecting "3,1" should still return in order [1, 3]
        result, _ = self._run(SAMPLE, "3,1")
        assert result[0] == SAMPLE[0]
        assert result[1] == SAMPLE[2]

    def test_case_insensitive_all(self):
        result, _ = self._run(SAMPLE, "ALL")
        assert result == SAMPLE

    def test_case_insensitive_none(self):
        result, _ = self._run(SAMPLE, "NONE")
        assert result == []


# ---------------------------------------------------------------------------
# _recommended_indices
# ---------------------------------------------------------------------------


class TestRecommendedIndices:
    def test_returns_none_for_empty_set(self):
        assert _recommended_indices(SAMPLE, set()) is None

    def test_returns_none_when_no_match(self):
        assert _recommended_indices(SAMPLE, {"Nonexistent"}) is None

    def test_returns_matching_indices(self):
        result = _recommended_indices(SAMPLE, {"Search", "SSO"})
        assert result == [0, 2]

    def test_preserves_order(self):
        result = _recommended_indices(SAMPLE, {"Dark mode", "Search"})
        assert result == [0, 3]


# ---------------------------------------------------------------------------
# select_features with recommended
# ---------------------------------------------------------------------------


class TestSelectFeaturesRecommended:
    def _run(self, features, user_input, recommended=None):
        lines: list[str] = []
        result = select_features(
            features,
            recommended=recommended,
            input_fn=lambda _: user_input,
            print_fn=lines.append,
        )
        return result, lines

    def test_blank_input_selects_recommended(self):
        result, _ = self._run(SAMPLE, "", recommended=["Search", "SSO"])
        assert len(result) == 2
        assert result[0].name == "Search"
        assert result[1].name == "SSO"

    def test_all_overrides_recommended(self):
        result, _ = self._run(SAMPLE, "all", recommended=["Search"])
        assert result == SAMPLE

    def test_none_overrides_recommended(self):
        result, _ = self._run(SAMPLE, "none", recommended=["Search"])
        assert result == []

    def test_numeric_overrides_recommended(self):
        result, _ = self._run(SAMPLE, "2", recommended=["Search"])
        assert len(result) == 1
        assert result[0].name == "REST API"

    def test_blank_with_no_matching_recommended_selects_all(self):
        result, _ = self._run(SAMPLE, "", recommended=["Nonexistent"])
        assert result == SAMPLE

    def test_prompt_shows_recommended_numbers(self):
        prompts: list[str] = []

        def capture_prompt(p: str) -> str:
            prompts.append(p)
            return ""

        select_features(
            SAMPLE,
            recommended=["Search", "SSO"],
            input_fn=capture_prompt,
            print_fn=lambda _: None,
        )
        assert len(prompts) == 1
        assert "1,3" in prompts[0]

    def test_shows_roadmap_hint(self):
        _, lines = self._run(SAMPLE, "", recommended=["Search"])
        combined = "\n".join(lines)
        assert "recommended by roadmap: 1" in combined

    def test_shows_phase_label_in_recommendation(self):
        lines: list[str] = []
        select_features(
            SAMPLE,
            recommended=["Search", "SSO"],
            phase_label="Phase 2: API Integration",
            input_fn=lambda _: "",
            print_fn=lines.append,
        )
        combined = "\n".join(lines)
        assert "Recommended for Phase 2: API Integration: 1, 3" in combined

    def test_no_phase_label_shows_generic_hint(self):
        lines: list[str] = []
        select_features(
            SAMPLE,
            recommended=["Search", "SSO"],
            input_fn=lambda _: "",
            print_fn=lines.append,
        )
        combined = "\n".join(lines)
        assert "recommended by roadmap: 1, 3" in combined
