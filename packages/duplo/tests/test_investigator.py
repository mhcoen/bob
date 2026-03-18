"""Tests for duplo.investigator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from duplo.claude_cli import ClaudeCliError
from duplo.investigator import (
    Diagnosis,
    InvestigationResult,
    _build_prompt,
    _gather_context,
    _parse_result,
    format_investigation,
    investigate,
    investigation_to_fix_tasks,
)


class TestParseResult:
    def test_parses_valid_json(self):
        raw = """{
  "diagnosis": [
    {
      "symptom": "Labels don't evaluate",
      "expected": "Should show result (ref: demo_0003.png)",
      "severity": "critical",
      "area": "parser",
      "evidence_sources": ["demo_0003.png"]
    }
  ],
  "summary": "Parser ignores label prefixes."
}"""
        result = _parse_result(raw)
        assert len(result.diagnoses) == 1
        assert result.diagnoses[0].symptom == "Labels don't evaluate"
        assert result.diagnoses[0].severity == "critical"
        assert result.diagnoses[0].area == "parser"
        assert result.diagnoses[0].evidence_sources == ["demo_0003.png"]
        assert result.summary == "Parser ignores label prefixes."

    def test_parses_fenced_json(self):
        raw = """Here is my analysis:
```json
{
  "diagnosis": [
    {"symptom": "Wrong color", "expected": "#a8d860", "severity": "minor", "area": "theme"}
  ],
  "summary": "Color mismatch."
}
```"""
        result = _parse_result(raw)
        assert len(result.diagnoses) == 1
        assert result.diagnoses[0].symptom == "Wrong color"
        assert result.summary == "Color mismatch."

    def test_extracts_json_from_surrounding_text(self):
        raw = """I found the following issues:
{"diagnosis": [{"symptom": "Crash", "expected": "No crash", "severity": "critical", "area": "main"}], "summary": "App crashes."}
That's all."""
        result = _parse_result(raw)
        assert len(result.diagnoses) == 1
        assert result.diagnoses[0].symptom == "Crash"

    def test_handles_empty_diagnosis(self):
        raw = '{"diagnosis": [], "summary": "Everything looks fine."}'
        result = _parse_result(raw)
        assert result.diagnoses == []
        assert result.summary == "Everything looks fine."

    def test_handles_missing_optional_fields(self):
        raw = '{"diagnosis": [{"symptom": "Bug", "severity": "major"}], "summary": "One bug."}'
        result = _parse_result(raw)
        assert len(result.diagnoses) == 1
        assert result.diagnoses[0].expected == ""
        assert result.diagnoses[0].area == ""
        assert result.diagnoses[0].evidence_sources == []

    def test_handles_garbage_input(self):
        result = _parse_result("This is not JSON at all.")
        assert result.diagnoses == []
        assert "Failed to parse" in result.summary

    def test_handles_empty_input(self):
        result = _parse_result("")
        assert result.diagnoses == []

    def test_handles_non_dict_json(self):
        result = _parse_result("[1, 2, 3]")
        assert result.diagnoses == []
        assert "Unexpected response format" in result.summary

    def test_preserves_raw_response(self):
        raw = '{"diagnosis": [], "summary": "ok"}'
        result = _parse_result(raw)
        assert result.raw_response == raw

    def test_multiple_diagnoses(self):
        raw = """{
  "diagnosis": [
    {"symptom": "Bug A", "expected": "Fix A", "severity": "critical", "area": "parser"},
    {"symptom": "Bug B", "expected": "Fix B", "severity": "minor", "area": "UI"}
  ],
  "summary": "Two bugs found."
}"""
        result = _parse_result(raw)
        assert len(result.diagnoses) == 2
        assert result.diagnoses[0].severity == "critical"
        assert result.diagnoses[1].severity == "minor"

    def test_skips_non_dict_items_in_diagnosis(self):
        raw = '{"diagnosis": ["not a dict", {"symptom": "Real bug", "severity": "major"}], "summary": "ok"}'
        result = _parse_result(raw)
        assert len(result.diagnoses) == 1
        assert result.diagnoses[0].symptom == "Real bug"


class TestBuildPrompt:
    def test_includes_complaints(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["app crashes on startup"], context)
        assert "app crashes on startup" in prompt
        assert "USER BUG REPORT:" in prompt

    def test_includes_frame_descriptions(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [
                {"filename": "demo_0003.png", "state": "Main view", "detail": "Shows calculator"}
            ],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["bug"], context)
        assert "demo_0003.png" in prompt
        assert "Main view" in prompt
        assert "FRAME DESCRIPTIONS" in prompt

    def test_includes_design_requirements(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {"colors": {"background": "#2b2b2b"}},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["bug"], context)
        assert "#2b2b2b" in prompt
        assert "DESIGN REQUIREMENTS" in prompt

    def test_includes_feature_list(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [
                {"name": "Variables", "description": "Assignment and reference", "status": "implemented"}
            ],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["bug"], context)
        assert "Variables" in prompt
        assert "[implemented]" in prompt

    def test_includes_image_legend(self):
        ref1 = Path(".duplo/references/frame_001.png")
        context = {
            "reference_images": [ref1],
            "current_screenshot": Path("screenshots/current/main.png"),
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["bug"], context)
        assert "REFERENCE IMAGES" in prompt
        assert "frame_001.png" in prompt
        assert "CURRENT APP SCREENSHOT" in prompt
        assert "main.png" in prompt

    def test_includes_open_issues(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [{"description": "Colors don't match reference"}],
        }
        prompt = _build_prompt(["bug"], context)
        assert "Colors don't match reference" in prompt
        assert "KNOWN OPEN ISSUES" in prompt

    def test_code_examples_truncated(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [
                {"input": "x" * 300, "expected_output": "y" * 300}
            ],
            "issues": [],
        }
        prompt = _build_prompt(["bug"], context)
        assert "CODE EXAMPLES" in prompt
        assert "\u2026" in prompt  # Truncation marker.

    def test_empty_context(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        prompt = _build_prompt(["something is broken"], context)
        assert "something is broken" in prompt
        assert "Analyze all the evidence" in prompt

    def test_includes_spec_text(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "spec_text": "Must support `2+3` -> `5`.",
        }
        prompt = _build_prompt(["bug"], context)
        assert "PRODUCT SPECIFICATION" in prompt
        assert "Must support" in prompt

    def test_spec_text_empty_not_in_prompt(self):
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "spec_text": "",
        }
        prompt = _build_prompt(["bug"], context)
        assert "PRODUCT SPECIFICATION" not in prompt

    def test_includes_user_screenshot_legend(self):
        user_img = Path("/tmp/bug_screenshot.png")
        context = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
        }
        with patch.object(Path, "exists", return_value=True):
            prompt = _build_prompt(["bug"], context, user_screenshots=[user_img])
        assert "USER-SUPPLIED SCREENSHOTS" in prompt
        assert "bug_screenshot.png" in prompt


class TestGatherContext:
    def test_no_duplo_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        context = _gather_context()
        assert context["reference_images"] == []
        assert context["current_screenshot"] is None
        assert context["features"] == []

    def test_reads_duplo_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        data = {
            "app_name": "TestApp",
            "source_url": "https://test.app",
            "features": [{"name": "Feat1", "status": "implemented"}],
            "frame_descriptions": [
                {"filename": "f.png", "state": "Main", "detail": "Detail"}
            ],
            "design_requirements": {"colors": {"bg": "#000"}},
            "issues": [
                {"description": "Bug A", "status": "open"},
                {"description": "Bug B", "status": "resolved"},
            ],
        }
        (duplo_dir / "duplo.json").write_text(json.dumps(data))
        context = _gather_context()
        assert context["app_name"] == "TestApp"
        assert context["source_url"] == "https://test.app"
        assert len(context["features"]) == 1
        assert len(context["frame_descriptions"]) == 1
        assert len(context["issues"]) == 1  # Only open issues.
        assert context["issues"][0]["description"] == "Bug A"

    def test_finds_reference_images(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        refs_dir = duplo_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "frame_001.png").write_bytes(b"fake png")
        (refs_dir / "frame_002.png").write_bytes(b"fake png")
        (refs_dir / "notes.txt").write_text("not an image")
        context = _gather_context()
        assert len(context["reference_images"]) == 2

    def test_finds_current_screenshot(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}")
        shots_dir = tmp_path / "screenshots" / "current"
        shots_dir.mkdir(parents=True)
        (shots_dir / "main.png").write_bytes(b"fake png")
        context = _gather_context()
        assert context["current_screenshot"] is not None
        assert context["current_screenshot"].name == "main.png"


class TestFormatInvestigation:
    def test_formats_diagnoses(self):
        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Labels broken",
                    expected="Should work",
                    severity="critical",
                    area="parser",
                    evidence_sources=["demo.png"],
                ),
            ],
            summary="Parser needs work.",
        )
        text = format_investigation(result)
        assert "[CRITICAL]" in text
        assert "Labels broken" in text
        assert "Should work" in text
        assert "parser" in text
        assert "demo.png" in text
        assert "Parser needs work." in text

    def test_empty_diagnoses(self):
        result = InvestigationResult(diagnoses=[], summary="All good.")
        text = format_investigation(result)
        assert "All good." in text

    def test_no_summary_no_diagnoses(self):
        result = InvestigationResult(diagnoses=[], summary="")
        text = format_investigation(result)
        assert "No specific bugs diagnosed" in text


class TestInvestigationToFixTasks:
    def test_generates_fix_tasks(self):
        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Labels broken",
                    expected="Should show result",
                    severity="critical",
                    area="parser",
                ),
                Diagnosis(
                    symptom="Wrong color",
                    expected="#a8d860",
                    severity="minor",
                    area="theme",
                ),
            ],
            summary="Two bugs.",
        )
        tasks = investigation_to_fix_tasks(result)
        assert len(tasks) == 2
        assert tasks[0].startswith("- [ ] Fix:")
        assert "Labels broken" in tasks[0]
        assert "parser" in tasks[0]
        assert '[fix: "Labels broken"]' in tasks[0]

    def test_empty_diagnoses(self):
        result = InvestigationResult(diagnoses=[], summary="Fine.")
        tasks = investigation_to_fix_tasks(result)
        assert tasks == []


class TestInvestigate:
    @patch("duplo.investigator.query_with_images")
    @patch("duplo.investigator._gather_context")
    def test_calls_llm_with_images(self, mock_gather, mock_query, tmp_path):
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"png")
        current = tmp_path / "current.png"
        current.write_bytes(b"png")

        mock_gather.return_value = {
            "reference_images": [ref_img],
            "current_screenshot": current,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        mock_query.return_value = '{"diagnosis": [], "summary": "ok"}'

        result = investigate(["test bug"])
        assert mock_query.called
        # Should have passed both images.
        call_args = mock_query.call_args
        image_paths = call_args[0][1]  # Second positional arg.
        assert len(image_paths) == 2

    @patch("duplo.investigator.query_with_images")
    @patch("duplo.investigator._gather_context")
    def test_includes_user_screenshots(self, mock_gather, mock_query, tmp_path):
        user_img = tmp_path / "user_bug.png"
        user_img.write_bytes(b"png")
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"png")

        mock_gather.return_value = {
            "reference_images": [ref_img],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        mock_query.return_value = '{"diagnosis": [], "summary": "ok"}'

        result = investigate(["test"], user_screenshots=[user_img])
        call_args = mock_query.call_args
        image_paths = call_args[0][1]
        assert user_img in image_paths

    @patch("duplo.investigator._gather_context")
    def test_falls_back_to_text_query(self, mock_gather):
        mock_gather.return_value = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "text only"}'
            result = investigate(["bug"])
            assert mock_text_query.called
            assert result.summary == "text only"

    @patch("duplo.investigator.query_with_images")
    @patch("duplo.investigator._gather_context")
    def test_handles_cli_error(self, mock_gather, mock_query, tmp_path):
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"png")
        mock_gather.return_value = {
            "reference_images": [ref_img],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        mock_query.side_effect = ClaudeCliError("timeout")
        result = investigate(["bug"])
        assert "failed" in result.summary.lower()
        assert result.diagnoses == []

    @patch("duplo.investigator._gather_context")
    def test_spec_text_included_in_prompt(self, mock_gather):
        mock_gather.return_value = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], spec_text="Must produce $28 for Price: $7 x 4.")
        prompt = mock_text_query.call_args[0][0]
        assert "Must produce $28" in prompt
        assert "PRODUCT SPECIFICATION" in prompt

    @patch("duplo.investigator._gather_context")
    def test_spec_text_empty_not_in_prompt(self, mock_gather):
        mock_gather.return_value = {
            "reference_images": [],
            "current_screenshot": None,
            "frame_descriptions": [],
            "design_requirements": {},
            "features": [],
            "code_examples": [],
            "issues": [],
            "app_name": "Test",
            "source_url": "",
        }
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], spec_text="")
        prompt = mock_text_query.call_args[0][0]
        assert "PRODUCT SPECIFICATION" not in prompt
