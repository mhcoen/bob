"""Tests for duplo.verification_extractor."""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.verification_extractor import (
    VerificationCase,
    _parse_cases,
    extract_verification_cases,
    format_verification_tasks,
    load_frame_descriptions,
)


# ---------------------------------------------------------------------------
# _parse_cases
# ---------------------------------------------------------------------------


class TestParseCases:
    def test_parses_valid_json(self):
        raw = json.dumps(
            [
                {
                    "input": "Price: $10",
                    "expected": "$10",
                    "frame": "demo_0003.png",
                },
            ]
        )
        cases = _parse_cases(raw)
        assert len(cases) == 1
        assert cases[0].input == "Price: $10"
        assert cases[0].expected == "$10"
        assert cases[0].frame == "demo_0003.png"

    def test_parses_multiple_cases(self):
        raw = json.dumps(
            [
                {"input": "1 + 1", "expected": "2", "frame": "f1.png"},
                {"input": "2 * 3", "expected": "6", "frame": "f2.png"},
            ]
        )
        cases = _parse_cases(raw)
        assert len(cases) == 2

    def test_strips_code_fences(self):
        inner = json.dumps([{"input": "5 + 5", "expected": "10", "frame": "f.png"}])
        raw = f"```json\n{inner}\n```"
        cases = _parse_cases(raw)
        assert len(cases) == 1
        assert cases[0].input == "5 + 5"

    def test_returns_empty_on_invalid_json(self):
        assert _parse_cases("not json at all") == []

    def test_returns_empty_on_non_array(self):
        assert _parse_cases('{"input": "x"}') == []

    def test_skips_items_missing_input(self):
        raw = json.dumps([{"expected": "10", "frame": "f.png"}])
        assert _parse_cases(raw) == []

    def test_skips_items_missing_expected(self):
        raw = json.dumps([{"input": "5 + 5", "frame": "f.png"}])
        assert _parse_cases(raw) == []

    def test_skips_non_dict_items(self):
        raw = json.dumps([{"input": "1+1", "expected": "2", "frame": "f.png"}, "bad"])
        cases = _parse_cases(raw)
        assert len(cases) == 1

    def test_empty_array(self):
        assert _parse_cases("[]") == []

    def test_missing_frame_defaults_to_empty(self):
        raw = json.dumps([{"input": "1+1", "expected": "2"}])
        cases = _parse_cases(raw)
        assert len(cases) == 1
        assert cases[0].frame == ""

    def test_deduplicates_by_input_and_expected(self):
        raw = json.dumps(
            [
                {"input": "Price: $7 x 4", "expected": "$28", "frame": "f1.png"},
                {"input": "Price: $7 x 4", "expected": "$28", "frame": "f2.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "f3.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "f4.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "f5.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "f6.png"},
            ]
        )
        cases = _parse_cases(raw)
        assert len(cases) == 2
        pairs = [(c.input, c.expected) for c in cases]
        assert pairs == [
            ("Price: $7 x 4", "$28"),
            ("today + 17 days", "8/9/15"),
        ]
        assert cases[0].frame == "f1.png"
        assert cases[1].frame == "f3.png"

    def test_deduplication_is_sensitive_to_expected(self):
        raw = json.dumps(
            [
                {"input": "1+1", "expected": "2", "frame": "f1.png"},
                {"input": "1+1", "expected": "11", "frame": "f2.png"},
            ]
        )
        cases = _parse_cases(raw)
        assert len(cases) == 2


# ---------------------------------------------------------------------------
# extract_verification_cases
# ---------------------------------------------------------------------------


class TestExtractVerificationCases:
    def test_returns_cases_from_frame_descriptions(self):
        descs = [
            {
                "filename": "demo_0003.png",
                "state": "Main view",
                "detail": "'Price: $7 × 4' with result '$28'",
            },
        ]
        response = json.dumps(
            [
                {
                    "input": "Price: $7 × 4",
                    "expected": "$28",
                    "frame": "demo_0003.png",
                },
            ]
        )
        with patch("duplo.verification_extractor.query", return_value=response):
            cases = extract_verification_cases(descs)
        assert len(cases) == 1
        assert cases[0].input == "Price: $7 × 4"
        assert cases[0].expected == "$28"

    def test_returns_empty_for_empty_descriptions(self):
        assert extract_verification_cases([]) == []

    def test_returns_empty_for_descriptions_without_detail(self):
        descs = [{"filename": "f.png", "state": "unknown", "detail": ""}]
        assert extract_verification_cases(descs) == []

    def test_passes_descriptions_to_prompt(self):
        descs = [
            {
                "filename": "f.png",
                "state": "Main view",
                "detail": "Shows 1+1 = 2",
            },
        ]
        with patch("duplo.verification_extractor.query", return_value="[]") as mock_q:
            extract_verification_cases(descs)
        prompt = mock_q.call_args[0][0]
        assert "Shows 1+1 = 2" in prompt
        assert "f.png" in prompt

    def test_deduplicates_cases_from_duplicated_frame_descriptions(self):
        descs = [
            {
                "filename": "demo_0003.png",
                "state": "Main view",
                "detail": "'Price: $7 x 4' with result '$28'",
            },
            {
                "filename": "demo_0004.png",
                "state": "Main view",
                "detail": "'Price: $7 x 4' with result '$28'",
            },
            {
                "filename": "demo_0005.png",
                "state": "Main view",
                "detail": "'today + 17 days' with result '8/9/15'",
            },
            {
                "filename": "demo_0006.png",
                "state": "Main view",
                "detail": "'today + 17 days' with result '8/9/15'",
            },
            {
                "filename": "demo_0007.png",
                "state": "Main view",
                "detail": "'today + 17 days' with result '8/9/15'",
            },
            {
                "filename": "demo_0008.png",
                "state": "Main view",
                "detail": "'today + 17 days' with result '8/9/15'",
            },
        ]
        response = json.dumps(
            [
                {"input": "Price: $7 x 4", "expected": "$28", "frame": "demo_0003.png"},
                {"input": "Price: $7 x 4", "expected": "$28", "frame": "demo_0004.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "demo_0005.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "demo_0006.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "demo_0007.png"},
                {"input": "today + 17 days", "expected": "8/9/15", "frame": "demo_0008.png"},
            ]
        )
        with patch("duplo.verification_extractor.query", return_value=response):
            cases = extract_verification_cases(descs)
        pairs = [(c.input, c.expected) for c in cases]
        assert pairs.count(("Price: $7 x 4", "$28")) == 1
        assert pairs.count(("today + 17 days", "8/9/15")) == 1
        assert len(cases) == 2

    def test_handles_bad_response(self):
        descs = [
            {
                "filename": "f.png",
                "state": "Main view",
                "detail": "Some expression",
            },
        ]
        with patch(
            "duplo.verification_extractor.query",
            return_value="I cannot parse this",
        ):
            cases = extract_verification_cases(descs)
        assert cases == []


# ---------------------------------------------------------------------------
# format_verification_tasks
# ---------------------------------------------------------------------------


class TestFormatVerificationTasks:
    def test_empty_returns_empty_string(self):
        assert format_verification_tasks([]) == ""

    def test_formats_single_case(self):
        cases = [
            VerificationCase(input="Price: $10", expected="$10", frame="f.png"),
        ]
        text = format_verification_tasks(cases)
        assert "## Functional verification from demo video" in text
        assert "- [ ] Verify: type `Price: $10`, expect result `$10`" in text

    def test_formats_multiple_cases(self):
        cases = [
            VerificationCase(input="1+1", expected="2", frame="f1.png"),
            VerificationCase(input="4 GBP in Euro", expected="5.71 EUR", frame="f2.png"),
        ]
        text = format_verification_tasks(cases)
        task_lines = [line for line in text.splitlines() if line.startswith("- [ ]")]
        assert len(task_lines) == 2
        assert "`1+1`" in task_lines[0]
        assert "`4 GBP in Euro`" in task_lines[1]

    def test_all_tasks_are_unchecked(self):
        cases = [
            VerificationCase(input="x", expected="y", frame="f.png"),
        ]
        text = format_verification_tasks(cases)
        for line in text.splitlines():
            if line.startswith("- "):
                assert line.startswith("- [ ]")


# ---------------------------------------------------------------------------
# load_frame_descriptions
# ---------------------------------------------------------------------------


class TestLoadFrameDescriptions:
    def test_loads_from_duplo_json(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        data = {
            "frame_descriptions": [
                {
                    "filename": "f.png",
                    "state": "Main view",
                    "detail": "Shows stuff",
                },
            ],
        }
        (duplo_dir / "duplo.json").write_text(json.dumps(data))
        descs = load_frame_descriptions(target_dir=str(tmp_path))
        assert len(descs) == 1
        assert descs[0]["filename"] == "f.png"

    def test_returns_empty_when_no_file(self, tmp_path):
        assert load_frame_descriptions(target_dir=str(tmp_path)) == []

    def test_returns_empty_when_no_frame_descriptions_key(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        assert load_frame_descriptions(target_dir=str(tmp_path)) == []

    def test_returns_empty_on_invalid_json(self, tmp_path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("NOT JSON")
        assert load_frame_descriptions(target_dir=str(tmp_path)) == []
