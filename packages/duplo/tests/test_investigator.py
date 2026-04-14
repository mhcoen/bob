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
from duplo.spec_reader import BehaviorContract, ReferenceEntry, SourceEntry


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


def _base_context(**overrides):
    ctx = {
        "reference_images": [],
        "current_screenshot": None,
        "frame_descriptions": [],
        "design_requirements": {},
        "features": [],
        "code_examples": [],
        "issues": [],
    }
    ctx.update(overrides)
    return ctx


def _base_gather_context(**overrides):
    return _base_context(app_name="Test", source_url="", **overrides)


class TestBuildPrompt:
    def test_includes_complaints(self):
        prompt = _build_prompt(["app crashes on startup"], _base_context())
        assert "app crashes on startup" in prompt
        assert "USER BUG REPORT:" in prompt

    def test_includes_frame_descriptions(self):
        context = _base_context(
            frame_descriptions=[
                {"filename": "demo_0003.png", "state": "Main view", "detail": "Shows calculator"}
            ],
        )
        prompt = _build_prompt(["bug"], context)
        assert "demo_0003.png" in prompt
        assert "Main view" in prompt
        assert "FRAME DESCRIPTIONS" in prompt

    def test_includes_design_requirements(self):
        context = _base_context(
            design_requirements={"colors": {"background": "#2b2b2b"}},
        )
        prompt = _build_prompt(["bug"], context)
        assert "#2b2b2b" in prompt
        assert "DESIGN REQUIREMENTS" in prompt

    def test_includes_feature_list(self):
        context = _base_context(
            features=[
                {
                    "name": "Variables",
                    "description": "Assignment and reference",
                    "status": "implemented",
                }
            ],
        )
        prompt = _build_prompt(["bug"], context)
        assert "Variables" in prompt
        assert "[implemented]" in prompt

    def test_includes_image_legend(self):
        ref1 = Path(".duplo/references/frame_001.png")
        context = _base_context(
            reference_images=[ref1],
            current_screenshot=Path("screenshots/current/main.png"),
        )
        prompt = _build_prompt(["bug"], context)
        assert "REFERENCE IMAGES" in prompt
        assert "frame_001.png" in prompt
        assert "CURRENT APP SCREENSHOT" in prompt
        assert "main.png" in prompt

    def test_includes_open_issues(self):
        context = _base_context(
            issues=[{"description": "Colors don't match reference"}],
        )
        prompt = _build_prompt(["bug"], context)
        assert "Colors don't match reference" in prompt
        assert "KNOWN OPEN ISSUES" in prompt

    def test_code_examples_truncated(self):
        context = _base_context(
            code_examples=[{"input": "x" * 300, "expected_output": "y" * 300}],
        )
        prompt = _build_prompt(["bug"], context)
        assert "CODE EXAMPLES" in prompt
        assert "\u2026" in prompt  # Truncation marker.

    def test_empty_context(self):
        prompt = _build_prompt(["something is broken"], _base_context())
        assert "something is broken" in prompt
        assert "Analyze all the evidence" in prompt

    def test_includes_spec_text(self):
        context = _base_context(spec_text="Must support `2+3` -> `5`.")
        prompt = _build_prompt(["bug"], context)
        assert "PRODUCT SPECIFICATION" in prompt
        assert "Must support" in prompt

    def test_spec_text_empty_not_in_prompt(self):
        context = _base_context(spec_text="")
        prompt = _build_prompt(["bug"], context)
        assert "PRODUCT SPECIFICATION" not in prompt

    def test_includes_user_screenshot_legend(self):
        user_img = Path("/tmp/bug_screenshot.png")
        with patch.object(Path, "exists", return_value=True):
            prompt = _build_prompt(["bug"], _base_context(), user_screenshots=[user_img])
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
            "frame_descriptions": [{"filename": "f.png", "state": "Main", "detail": "Detail"}],
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

        mock_gather.return_value = _base_gather_context(
            reference_images=[ref_img],
            current_screenshot=current,
        )
        mock_query.return_value = '{"diagnosis": [], "summary": "ok"}'

        investigate(["test bug"])
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

        mock_gather.return_value = _base_gather_context(
            reference_images=[ref_img],
        )
        mock_query.return_value = '{"diagnosis": [], "summary": "ok"}'

        investigate(["test"], user_screenshots=[user_img])
        call_args = mock_query.call_args
        image_paths = call_args[0][1]
        assert user_img in image_paths

    @patch("duplo.investigator._gather_context")
    def test_falls_back_to_text_query(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
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
        mock_gather.return_value = _base_gather_context(
            reference_images=[ref_img],
        )
        mock_query.side_effect = ClaudeCliError("timeout")
        result = investigate(["bug"])
        assert "failed" in result.summary.lower()
        assert result.diagnoses == []

    @patch("duplo.investigator._gather_context")
    def test_spec_text_included_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], spec_text="Must produce $28 for Price: $7 x 4.")
        prompt = mock_text_query.call_args[0][0]
        assert "Must produce $28" in prompt
        assert "PRODUCT SPECIFICATION" in prompt

    @patch("duplo.investigator._gather_context")
    def test_spec_text_empty_not_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], spec_text="")
        prompt = mock_text_query.call_args[0][0]
        assert "PRODUCT SPECIFICATION" not in prompt

    @patch("duplo.investigator._gather_context")
    def test_counter_examples_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        ce = ReferenceEntry(
            path=Path("ref/bad_design.png"),
            roles=["counter-example"],
            notes="Cluttered layout to avoid",
        )
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], counter_examples=[ce])
        prompt = mock_text_query.call_args[0][0]
        assert "COUNTER-EXAMPLES" in prompt
        assert "AVOID" in prompt
        assert "bad_design.png" in prompt
        assert "Cluttered layout to avoid" in prompt

    @patch("duplo.investigator._gather_context")
    def test_counter_example_sources_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        ces = SourceEntry(
            url="https://bad-example.com",
            role="counter-example",
            scrape="none",
            notes="Avoid this UI pattern",
        )
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], counter_example_sources=[ces])
        prompt = mock_text_query.call_args[0][0]
        assert "COUNTER-EXAMPLE URLS" in prompt
        assert "AVOID" in prompt
        assert "https://bad-example.com" in prompt
        assert "Avoid this UI pattern" in prompt

    @patch("duplo.investigator._gather_context")
    def test_counter_example_sources_not_fetched(self, mock_gather):
        """Counter-example source URLs are declarative — never fetched."""
        mock_gather.return_value = _base_gather_context()
        ces = SourceEntry(
            url="https://bad-example.com",
            role="counter-example",
            scrape="none",
        )
        with (
            patch("duplo.investigator.query") as mock_text_query,
            patch("duplo.investigator.query_with_images") as mock_img,
        ):
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            mock_img.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], counter_example_sources=[ces])
        # The URL should appear in the prompt text, not be fetched.
        prompt = mock_text_query.call_args[0][0]
        assert "https://bad-example.com" in prompt
        # No fetch calls should have been made for the URL.
        # (investigate never fetches — it delegates to query/query_with_images)

    @patch("duplo.investigator._gather_context")
    def test_docs_text_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(
                ["bug"],
                docs_text="=== api_guide.md ===\nUse POST /api/v1/data",
            )
        prompt = mock_text_query.call_args[0][0]
        assert "SUPPLEMENTARY DOCUMENTATION" in prompt
        assert "api_guide.md" in prompt
        assert "POST /api/v1/data" in prompt

    @patch("duplo.investigator._gather_context")
    def test_behavior_contracts_in_prompt(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        contracts = [
            BehaviorContract(input="2+3", expected="5"),
            BehaviorContract(input="Price: $7 × 4", expected="$28"),
        ]
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], behavior_contracts=contracts)
        prompt = mock_text_query.call_args[0][0]
        assert "BEHAVIOR CONTRACTS" in prompt
        assert "ground-truth" in prompt
        assert "`2+3`" in prompt
        assert "`5`" in prompt
        assert "`Price: $7 × 4`" in prompt
        assert "`$28`" in prompt

    @patch("duplo.investigator._gather_context")
    def test_empty_new_context_not_in_prompt(self, mock_gather):
        """Empty lists/strings for new context types are not in prompt."""
        mock_gather.return_value = _base_gather_context()
        with patch("duplo.investigator.query") as mock_text_query:
            mock_text_query.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(
                ["bug"],
                counter_examples=[],
                counter_example_sources=[],
                docs_text="",
                behavior_contracts=[],
            )
        prompt = mock_text_query.call_args[0][0]
        assert "COUNTER-EXAMPLES" not in prompt
        assert "COUNTER-EXAMPLE URLS" not in prompt
        assert "SUPPLEMENTARY DOCUMENTATION" not in prompt
        assert "BEHAVIOR CONTRACTS" not in prompt


class TestBuildPromptRoleFilteredContext:
    """Tests for new role-filtered context sections in _build_prompt."""

    def test_counter_examples_with_notes(self):
        ce = ReferenceEntry(
            path=Path("ref/avoid.png"),
            roles=["counter-example"],
            notes="Too cluttered",
        )
        context = _base_context(counter_examples=[ce])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLES" in prompt
        assert "avoid.png" in prompt
        assert "Too cluttered" in prompt

    def test_counter_examples_without_notes(self):
        ce = ReferenceEntry(
            path=Path("ref/avoid.png"),
            roles=["counter-example"],
        )
        context = _base_context(counter_examples=[ce])
        prompt = _build_prompt(["bug"], context)
        assert "avoid.png" in prompt
        assert " — " not in prompt.split("avoid.png")[1].split("\n")[0]

    def test_counter_example_sources_with_notes(self):
        ces = SourceEntry(
            url="https://bad.com",
            role="counter-example",
            scrape="none",
            notes="Bloated UI",
        )
        context = _base_context(counter_example_sources=[ces])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLE URLS" in prompt
        assert "https://bad.com" in prompt
        assert "Bloated UI" in prompt

    def test_counter_example_sources_without_notes(self):
        ces = SourceEntry(
            url="https://bad.com",
            role="counter-example",
            scrape="none",
        )
        context = _base_context(counter_example_sources=[ces])
        prompt = _build_prompt(["bug"], context)
        assert "https://bad.com" in prompt

    def test_docs_text(self):
        context = _base_context(docs_text="Full API reference here.")
        prompt = _build_prompt(["bug"], context)
        assert "SUPPLEMENTARY DOCUMENTATION" in prompt
        assert "Full API reference here." in prompt

    def test_behavior_contracts(self):
        bc = BehaviorContract(input="2+3", expected="5")
        context = _base_context(behavior_contracts=[bc])
        prompt = _build_prompt(["bug"], context)
        assert "BEHAVIOR CONTRACTS" in prompt
        assert "`2+3`" in prompt
        assert "`5`" in prompt

    def test_empty_counter_examples_not_in_prompt(self):
        context = _base_context(counter_examples=[])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLES" not in prompt

    def test_empty_counter_example_sources_not_in_prompt(self):
        context = _base_context(counter_example_sources=[])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLE URLS" not in prompt

    def test_empty_docs_text_not_in_prompt(self):
        context = _base_context(docs_text="")
        prompt = _build_prompt(["bug"], context)
        assert "SUPPLEMENTARY DOCUMENTATION" not in prompt

    def test_empty_behavior_contracts_not_in_prompt(self):
        context = _base_context(behavior_contracts=[])
        prompt = _build_prompt(["bug"], context)
        assert "BEHAVIOR CONTRACTS" not in prompt

    def test_counter_example_images_in_legend(self, tmp_path):
        img = tmp_path / "avoid.png"
        img.write_bytes(b"\x89PNG")
        ce = ReferenceEntry(
            path=img,
            roles=["counter-example"],
            notes="Too cluttered",
        )
        context = _base_context(counter_examples=[ce])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLE IMAGES" in prompt
        assert "AVOID this pattern" in prompt
        assert "Image 1: avoid.png" in prompt
        assert "Too cluttered" in prompt

    def test_counter_example_images_legend_without_notes(self, tmp_path):
        img = tmp_path / "bad.png"
        img.write_bytes(b"\x89PNG")
        ce = ReferenceEntry(
            path=img,
            roles=["counter-example"],
        )
        context = _base_context(counter_examples=[ce])
        prompt = _build_prompt(["bug"], context)
        assert "bad.png" in prompt
        # No notes separator after filename.
        legend_line = [
            line for line in prompt.splitlines() if "bad.png" in line and "Image" in line
        ][0]
        assert " — " not in legend_line

    def test_counter_example_images_nonexistent_excluded_from_legend(self):
        ce = ReferenceEntry(
            path=Path("/nonexistent/avoid.png"),
            roles=["counter-example"],
        )
        context = _base_context(counter_examples=[ce])
        prompt = _build_prompt(["bug"], context)
        assert "COUNTER-EXAMPLE IMAGES" not in prompt

    def test_counter_example_image_index_follows_other_images(self, tmp_path):
        """Counter-example image indices continue after reference/user images."""
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"\x89PNG")
        ce_img = tmp_path / "avoid.png"
        ce_img.write_bytes(b"\x89PNG")
        ce = ReferenceEntry(
            path=ce_img,
            roles=["counter-example"],
        )
        context = _base_context(
            reference_images=[ref_img],
            counter_examples=[ce],
        )
        prompt = _build_prompt(["bug"], context)
        assert "Image 1: ref.png" in prompt
        assert "Image 2: avoid.png" in prompt


class TestInvestigateCounterExampleImages:
    """Tests that counter-example image files are passed to the vision call."""

    @patch("duplo.investigator._gather_context")
    def test_counter_example_images_passed_to_vision(self, mock_gather, tmp_path):
        img = tmp_path / "avoid.png"
        img.write_bytes(b"\x89PNG")
        mock_gather.return_value = _base_gather_context()
        ce = ReferenceEntry(
            path=img,
            roles=["counter-example"],
            notes="Bad pattern",
        )
        with patch("duplo.investigator.query_with_images") as mock_img:
            mock_img.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], counter_examples=[ce])
        image_paths = mock_img.call_args[0][1]
        assert img in image_paths

    @patch("duplo.investigator._gather_context")
    def test_counter_example_nonexistent_not_passed(self, mock_gather):
        mock_gather.return_value = _base_gather_context()
        ce = ReferenceEntry(
            path=Path("/nonexistent/avoid.png"),
            roles=["counter-example"],
        )
        with patch("duplo.investigator.query") as mock_text:
            mock_text.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(["bug"], counter_examples=[ce])
        # Falls back to text-only since no images exist.
        mock_text.assert_called_once()

    @patch("duplo.investigator._gather_context")
    def test_counter_example_images_after_other_images(self, mock_gather, tmp_path):
        """Counter-example images appear after reference/user images."""
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"\x89PNG")
        ce_img = tmp_path / "avoid.png"
        ce_img.write_bytes(b"\x89PNG")
        user_img = tmp_path / "user.png"
        user_img.write_bytes(b"\x89PNG")
        mock_gather.return_value = _base_gather_context(
            reference_images=[ref_img],
        )
        ce = ReferenceEntry(
            path=ce_img,
            roles=["counter-example"],
        )
        with patch("duplo.investigator.query_with_images") as mock_img:
            mock_img.return_value = '{"diagnosis": [], "summary": "ok"}'
            investigate(
                ["bug"],
                counter_examples=[ce],
                user_screenshots=[user_img],
            )
        image_paths = mock_img.call_args[0][1]
        # Order: reference, user, counter-example.
        assert image_paths.index(ref_img) < image_paths.index(ce_img)
        assert image_paths.index(user_img) < image_paths.index(ce_img)


class TestParseResultNewFields:
    """Tests for parsing contradicts and avoids_pattern fields."""

    def test_parses_contradicts(self):
        raw = json.dumps(
            {
                "diagnosis": [
                    {
                        "symptom": "Wrong result",
                        "expected": "5",
                        "severity": "critical",
                        "area": "parser",
                        "contradicts": "behavior contract: `2+3` → `5`",
                    }
                ],
                "summary": "Contract violation.",
            }
        )
        result = _parse_result(raw)
        assert result.diagnoses[0].contradicts == ("behavior contract: `2+3` → `5`")

    def test_parses_avoids_pattern(self):
        raw = json.dumps(
            {
                "diagnosis": [
                    {
                        "symptom": "Cluttered layout",
                        "expected": "Clean layout",
                        "severity": "major",
                        "area": "UI",
                        "avoids_pattern": "counter-example: bad_design.png",
                    }
                ],
                "summary": "Pattern issue.",
            }
        )
        result = _parse_result(raw)
        assert result.diagnoses[0].avoids_pattern == ("counter-example: bad_design.png")

    def test_missing_new_fields_default_empty(self):
        raw = json.dumps(
            {
                "diagnosis": [
                    {
                        "symptom": "Bug",
                        "severity": "major",
                    }
                ],
                "summary": "ok",
            }
        )
        result = _parse_result(raw)
        assert result.diagnoses[0].contradicts == ""
        assert result.diagnoses[0].avoids_pattern == ""

    def test_null_new_fields_default_empty(self):
        raw = json.dumps(
            {
                "diagnosis": [
                    {
                        "symptom": "Bug",
                        "severity": "major",
                        "contradicts": None,
                        "avoids_pattern": None,
                    }
                ],
                "summary": "ok",
            }
        )
        result = _parse_result(raw)
        assert result.diagnoses[0].contradicts == ""
        assert result.diagnoses[0].avoids_pattern == ""


class TestFormatInvestigationNewFields:
    """Tests for formatting contradicts and avoids_pattern."""

    def test_shows_contradicts(self):
        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Wrong result",
                    expected="5",
                    severity="critical",
                    area="parser",
                    contradicts="behavior contract: `2+3` → `5`",
                ),
            ],
            summary="Contract violation.",
        )
        text = format_investigation(result)
        assert "Contradicts:" in text
        assert "behavior contract" in text

    def test_shows_avoids_pattern(self):
        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Cluttered",
                    expected="Clean",
                    severity="minor",
                    area="UI",
                    avoids_pattern="counter-example: bad.png",
                ),
            ],
            summary="Pattern issue.",
        )
        text = format_investigation(result)
        assert "Avoids pattern:" in text
        assert "bad.png" in text

    def test_omits_empty_new_fields(self):
        result = InvestigationResult(
            diagnoses=[
                Diagnosis(
                    symptom="Bug",
                    expected="Fix",
                    severity="major",
                    area="core",
                ),
            ],
            summary="One bug.",
        )
        text = format_investigation(result)
        assert "Contradicts:" not in text
        assert "Avoids pattern:" not in text
