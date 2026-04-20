"""Tests for duplo.planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import re

import pytest

from duplo.extractor import Feature
from duplo.planner import (
    CompletedTask,
    _NEXT_PHASE_SYSTEM,
    _PHASE_SYSTEM,
    _PLAN_FILENAME,
    _detect_next_phase_number,
    _ensure_h1_heading,
    _strip_bugs_section,
    _strip_fences,
    _strip_trailing_commentary,
    append_test_tasks,
    generate_next_phase_plan,
    generate_phase_plan,
    parse_completed_tasks,
    save_plan,
)
from duplo.questioner import BuildPreferences


def _sample_features() -> list[Feature]:
    return [
        Feature(name="User auth", description="Sign up and log in.", category="core"),
        Feature(name="Dashboard", description="Overview of activity.", category="ui"),
    ]


def _sample_prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="web",
        language="Python/FastAPI",
        constraints=["PostgreSQL only"],
        preferences=["Use pytest"],
    )


_SAMPLE_PLAN = "# Phase 1: Core Auth\n\n## Objective\nMinimal working app."


class TestGeneratePhasePlan:
    def test_returns_string(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, str)
        assert result == _SAMPLE_PLAN

    def test_passes_source_url_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://acme.io",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "https://acme.io" in prompt

    def test_passes_features_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "User auth" in prompt
        assert "Dashboard" in prompt

    def test_passes_preferences_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Python/FastAPI" in prompt
        assert "PostgreSQL only" in prompt

    def test_passes_platform_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "web" in prompt

    def test_handles_empty_features(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                [],
                _sample_prefs(),
            )
        assert isinstance(result, str)

    def test_handles_empty_constraints_and_preferences(self):
        prefs = BuildPreferences(platform="cli", language="Go", constraints=[], preferences=[])
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                prefs,
            )
        prompt = mock_query.call_args[0][0]
        assert "(none)" in prompt
        assert isinstance(result, str)

    def test_spec_text_injected_into_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="Build a calculator app.",
            )
        prompt = mock_query.call_args[0][0]
        assert "Build a calculator app." in prompt
        assert "authoritative" in prompt.lower()

    def test_spec_text_empty_not_in_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="",
            )
        prompt = mock_query.call_args[0][0]
        assert "Product specification" not in prompt

    def test_includes_issues_in_prompt(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Fix known issues",
            "features": ["Dashboard"],
            "test": "All issues resolved",
            "issues": ["Sidebar overlaps on mobile", "Login timeout too short"],
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Sidebar overlaps on mobile" in prompt
        assert "Login timeout too short" in prompt
        assert "Known issues to fix" in prompt

    def test_no_issues_block_when_empty(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Add features",
            "features": ["Dashboard"],
            "test": "",
            "issues": [],
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Known issues to fix" not in prompt

    def test_no_issues_block_when_no_phase(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Known issues to fix" not in prompt

    def test_phase_number_overrides_phase_dict(self):
        phase = {
            "phase": 0,
            "title": "Core",
            "goal": "Build core",
            "features": ["Auth"],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                phase_number=3,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 3:" in prompt
        assert "Phase 0:" not in prompt

    def test_phase_number_used_without_phase_dict(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase_number=5,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 5:" in prompt

    def test_phase_number_defaults_to_phase_dict(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Polish it",
            "features": ["Dashboard"],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 2:" in prompt


class TestGeneratePhasePlanH1Heading:
    """Verify generate_phase_plan() always returns content starting with '# '."""

    def test_returned_content_starts_with_h1(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert result.startswith("# ")

    def test_prepends_h1_when_missing(self):
        no_h1 = "Some preamble describing the phase.\n\n- [ ] Build thing"
        with patch("duplo.planner.query", return_value=no_h1):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="Numi",
                phase_number=0,
            )
        assert result.startswith("# Numi")
        first_line = result.split("\n", 1)[0]
        assert "Phase 0:" in first_line
        assert "Some preamble describing the phase." in result

    def test_prepends_h1_when_h2_at_start(self):
        h2_only = "## Subsection heading\n\n- [ ] Task"
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Polish it",
            "features": ["Dashboard"],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=h2_only):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                project_name="MyApp",
            )
        assert result.startswith("# MyApp")
        first_line = result.split("\n", 1)[0]
        assert "Phase 2:" in first_line
        assert "Polish" in first_line
        assert "## Subsection heading" in result

    def test_preserves_existing_h1(self):
        with_h1 = "# LLM Heading — Phase 1: Core\n\n- [ ] Task"
        with patch("duplo.planner.query", return_value=with_h1):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="Different",
            )
        assert result == with_h1

    def test_strips_llm_preamble_before_h1(self):
        """generate_phase_plan() strips LLM meta-commentary before the H1.

        Reproduces the numi Phase 4 regression: the LLM prefixed the plan
        with 'The PLAN.md content is ready. Here it is...' followed by
        a '---' separator before the real heading. After _strip_fences(),
        that preamble must be discarded so mcloop parses a single clean
        phase heading.
        """
        with_preamble = (
            "The PLAN.md content is ready. Here it is for you to append to PLAN.md:\n"
            "\n"
            "---\n"
            "\n"
            "# Numi — Phase 4: Advanced\n"
            "\n"
            "Python/SwiftUI calculator app.\n"
            "\n"
            "- [ ] Build advanced scientific functions\n"
        )
        with patch("duplo.planner.query", return_value=with_preamble):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="Numi",
                phase_number=4,
            )
        assert result.startswith("# Numi — Phase 4: Advanced")
        assert "The PLAN.md content is ready" not in result
        assert "append to PLAN.md" not in result
        # Only one H1 heading in the result.
        h1_lines = [ln for ln in result.splitlines() if ln.startswith("# ")]
        assert len(h1_lines) == 1

    def test_prepended_heading_uses_phase_number_and_title(self):
        no_h1 = "- [ ] Task"
        phase = {
            "phase": 5,
            "title": "Integrations",
            "goal": "Wire it up",
            "features": [],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=no_h1):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                project_name="Widget",
            )
        first_line = result.split("\n", 1)[0]
        assert first_line == "# Widget — Phase 5: Integrations"


class TestEnsureH1Heading:
    def test_content_with_h1_stripped_of_leading_ws(self):
        assert _ensure_h1_heading("\n\n# App — Phase 1: Core\n", "X", 1, "Core") == (
            "# App — Phase 1: Core\n"
        )

    def test_prepends_when_no_heading(self):
        result = _ensure_h1_heading("plain text\n", "Widget", 2, "Polish")
        assert result.startswith("# Widget — Phase 2: Polish\n\nplain text")

    def test_prepends_when_h2_only(self):
        result = _ensure_h1_heading("## sub\n\n- [ ] x", "App", 0, "Scaffold")
        assert result.startswith("# App — Phase 0: Scaffold\n\n## sub")

    def test_empty_content_produces_heading_only(self):
        assert _ensure_h1_heading("", "App", 0, "Scaffold") == "# App — Phase 0: Scaffold\n"

    def test_empty_project_name_uses_fallback(self):
        result = _ensure_h1_heading("- [ ] Task", "", 1, "Core")
        assert result.startswith("# App — Phase 1: Core")

    def test_hash_without_space_is_not_h1(self):
        # "#foo" is not a valid markdown heading in CommonMark.
        result = _ensure_h1_heading("#foo\n", "App", 1, "Core")
        assert result.startswith("# App — Phase 1: Core\n\n#foo")

    def test_empty_h1_line_is_not_accepted(self):
        # "# \n" (hash + space + newline, no heading text) should not qualify.
        result = _ensure_h1_heading("# \n- [ ] Task", "App", 1, "Core")
        assert result.startswith("# App — Phase 1: Core")

    def test_strips_preamble_before_h1(self):
        # Regression: numi Phase 4 output had LLM meta-commentary before the
        # real phase heading. Previously _ensure_h1_heading would prepend a
        # new heading while leaving the original intact (two H1s, preamble
        # garbage in between). It must discard everything up to the H1 line.
        content = (
            "The PLAN.md content is ready. Here it is for you to append to PLAN.md:\n"
            "\n"
            "---\n"
            "\n"
            "# Numi — Phase 4: Advanced\n"
            "\n"
            "- [ ] First task\n"
        )
        result = _ensure_h1_heading(content, "Ignored", 99, "Ignored")
        assert result.startswith("# Numi — Phase 4: Advanced")
        assert "The PLAN.md content is ready" not in result
        assert "---" not in result
        assert result.count("# ") == 1  # no duplicate H1 heading

    def test_strips_preamble_with_separator_only(self):
        content = "---\n\n# App — Phase 2: Core\n\n- [ ] Task"
        result = _ensure_h1_heading(content, "Ignored", 99, "Ignored")
        assert result == "# App — Phase 2: Core\n\n- [ ] Task"


class TestPhaseSystemPromptAnnotations:
    def test_system_prompt_requires_feat_annotation(self):
        assert '[feat: "Feature Name"]' in _PHASE_SYSTEM

    def test_system_prompt_requires_multi_feature_annotation(self):
        assert "comma-separated" in _PHASE_SYSTEM

    def test_system_prompt_requires_fix_annotation(self):
        assert '[fix: "description"]' in _PHASE_SYSTEM

    def test_system_prompt_no_annotation_for_scaffolding(self):
        assert "no annotation" in _PHASE_SYSTEM.lower()

    def test_system_prompt_orders_fixes_before_dependent_features(self):
        assert "fix tasks before new feature work" in _PHASE_SYSTEM.lower()

    def test_system_prompt_shows_feat_example_in_format(self):
        assert '[feat: "User authentication"]' in _PHASE_SYSTEM

    def test_system_prompt_heading_format(self):
        assert "# <AppName> — Phase N: <Title>" in _PHASE_SYSTEM

    def test_system_prompt_shows_fix_example_in_format(self):
        assert '[fix: "email format not checked"]' in _PHASE_SYSTEM

    def test_system_prompt_forbids_platform_boilerplate_paragraph(self):
        assert (
            "Do NOT include a platform, language, prerequisites, or\n"
            "  build-system description paragraph at the top of the phase.\n"
            "  That information is written once in the PLAN.md project\n"
            "  header and must not be repeated per phase. Start the phase\n"
            "  content with the H1 phase heading line, then go directly to\n"
            "  task checkboxes."
        ) in _PHASE_SYSTEM


class TestNextPhaseSystemPromptAnnotations:
    def test_system_prompt_requires_feat_annotation(self):
        assert '[feat: "Feature Name"]' in _NEXT_PHASE_SYSTEM

    def test_system_prompt_requires_multi_feature_annotation(self):
        assert "comma-separated" in _NEXT_PHASE_SYSTEM

    def test_system_prompt_requires_fix_annotation(self):
        assert '[fix: "description"]' in _NEXT_PHASE_SYSTEM

    def test_system_prompt_no_annotation_for_scaffolding(self):
        assert "no annotation" in _NEXT_PHASE_SYSTEM.lower()


_SAMPLE_CURRENT_PLAN = "# Phase 1: Core Auth\n\n## Objective\nMinimal app."

_ANNOTATED_PHASE_PLAN = """\
# MyApp

Web app built with Python/FastAPI and PostgreSQL.

- [ ] Set up project structure and build system
- [ ] Add user login form [feat: "User auth"]
  - [ ] Create login page template
  - [ ] Wire up authentication backend [feat: "User auth"]
- [ ] Build activity overview [feat: "Dashboard"]
- [ ] Fix email validation on signup [fix: "email format not checked"]
"""

_ANNOTATED_NEXT_PLAN = """\
# Phase 2: Search

## Objective
Add full-text search across the application.

## Implementation steps
1. Set up search index infrastructure
2. Add search bar component [feat: "Full-text search"]
3. Implement result ranking [feat: "Full-text search", "Relevance scoring"]
4. Fix broken layout on mobile [fix: "sidebar overlaps content on small screens"]
"""

_ANNOTATION_RE = re.compile(r"\[(feat|fix):\s*\"[^\"]+\"(?:,\s*\"[^\"]+\")*\]")


class TestPlanAnnotationOutput:
    """Verify that generated plans contain [feat:] or [fix:] annotations."""

    def test_phase_plan_contains_feat_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        feat_matches = re.findall(r'\[feat: "[^"]+"\]', result)
        assert len(feat_matches) >= 1

    def test_phase_plan_contains_fix_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        fix_matches = re.findall(r'\[fix: "[^"]+"\]', result)
        assert len(fix_matches) >= 1

    def test_phase_plan_annotations_on_task_lines(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        for line in result.splitlines():
            match = _ANNOTATION_RE.search(line)
            if match:
                stripped = line.lstrip()
                assert stripped.startswith("- [ ]") or stripped.startswith("- [x]")

    def test_next_phase_plan_contains_feat_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        feat_matches = re.findall(r'\[feat: "[^"]+"\]', result)
        assert len(feat_matches) >= 1

    def test_next_phase_plan_contains_fix_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        fix_matches = re.findall(r'\[fix: "[^"]+"\]', result)
        assert len(fix_matches) >= 1

    def test_next_phase_plan_multi_feature_annotation(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        multi = re.findall(r'\[feat: "[^"]+",\s*"[^"]+"\]', result)
        assert len(multi) >= 1

    def test_scaffolding_lines_have_no_annotation(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        for line in result.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("- [ ]") and "project structure" in stripped:
                assert not _ANNOTATION_RE.search(line)


class TestDetectNextPhaseNumber:
    def test_extracts_phase_number(self):
        plan = "# Phase 1: Core Auth\n\n## Objective\nMinimal app."
        assert _detect_next_phase_number(plan) == 2

    def test_extracts_higher_phase_number(self):
        plan = "# Phase 3: Dashboard\n\n## Objective\nAdd dashboard."
        assert _detect_next_phase_number(plan) == 4

    def test_defaults_to_two_when_no_phase_heading(self):
        assert _detect_next_phase_number("No heading here.") == 2

    def test_case_insensitive(self):
        assert _detect_next_phase_number("# phase 2: Foo") == 3

    def test_prefixed_heading(self):
        plan = "# McWhisper — Phase 3: Dashboard\n\n## Objective\nAdd dashboard."
        assert _detect_next_phase_number(plan) == 4

    def test_stage_heading(self):
        plan = "# Stage 1: Core\n\n## Objective\nMinimal app."
        assert _detect_next_phase_number(plan) == 2

    def test_stage_higher_number(self):
        plan = "## Stage 2: Features\n\n- [ ] Add search"
        assert _detect_next_phase_number(plan) == 3

    def test_prefixed_stage_heading(self):
        plan = "# MyApp — Stage 4: Polish\n\n## Objective\nFinal pass."
        assert _detect_next_phase_number(plan) == 5


_SAMPLE_NEXT_PLAN = "# Phase 2: Search\n\n## Objective\nAdd search."


class TestGenerateNextPhasePlan:
    def test_returns_string(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search feature.")
        assert isinstance(result, str)
        assert result == _SAMPLE_NEXT_PLAN

    def test_passes_current_plan_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "Phase 1: Core Auth" in prompt

    def test_passes_feedback_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Needs dark mode.")
        prompt = mock_query.call_args[0][0]
        assert "Needs dark mode." in prompt

    def test_passes_issues_text_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", "- Layout broken")
        prompt = mock_query.call_args[0][0]
        assert "Layout broken" in prompt

    def test_next_phase_number_in_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "Phase 2" in prompt

    def test_no_issues_text_shows_no_issues_message(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "No visual issues reported" in prompt

    def test_empty_issues_text_shows_no_issues_message(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", "")
        prompt = mock_query.call_args[0][0]
        assert "No visual issues reported" in prompt


_PLATFORM_ADDENDUM = (
    "\n## Platform-specific rules (from duplo platform knowledge)\n"
    "\n- Use Swift Package Manager for dependencies\n"
)


class TestPlatformAddendum:
    def test_phase_plan_appends_addendum_to_system_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                platform_addendum=_PLATFORM_ADDENDUM,
            )
        system = mock_query.call_args.kwargs["system"]
        assert _PHASE_SYSTEM in system
        assert _PLATFORM_ADDENDUM in system

    def test_phase_plan_empty_addendum_leaves_system_unchanged(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                platform_addendum="",
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _PHASE_SYSTEM

    def test_phase_plan_default_has_no_addendum(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _PHASE_SYSTEM

    def test_next_phase_plan_appends_addendum_to_system_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(
                _SAMPLE_CURRENT_PLAN,
                "feedback",
                platform_addendum=_PLATFORM_ADDENDUM,
            )
        system = mock_query.call_args.kwargs["system"]
        assert _NEXT_PHASE_SYSTEM in system
        assert _PLATFORM_ADDENDUM in system

    def test_next_phase_plan_empty_addendum_leaves_system_unchanged(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(
                _SAMPLE_CURRENT_PLAN,
                "feedback",
                platform_addendum="",
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _NEXT_PHASE_SYSTEM

    def test_next_phase_plan_default_has_no_addendum(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        system = mock_query.call_args.kwargs["system"]
        assert system == _NEXT_PHASE_SYSTEM


class TestAppendTestTasks:
    def test_appends_tasks_to_plan(self):
        plan = "# Phase 1\n- [ ] Build core"
        tasks = ["- [ ] Wire up tests", "  - [ ] Replace stub"]
        result = append_test_tasks(plan, tasks)
        assert "Build core" in result
        assert result == (
            "# Phase 1\n- [ ] Wire up tests\n  - [ ] Replace stub\n- [ ] Build core\n"
        )

    def test_returns_plan_unchanged_when_no_tasks(self):
        plan = "# Phase 1\n- [ ] Build core\n"
        assert append_test_tasks(plan, []) == plan


class TestSavePlan:
    def test_writes_file(self, tmp_path: Path):
        content = "# Phase 1\n"
        path = save_plan(content, target_dir=tmp_path)
        assert path.name == _PLAN_FILENAME
        text = path.read_text(encoding="utf-8")
        assert "# Phase 1" in text
        # duplo must never emit a ## Bugs section.
        assert "## Bugs" not in text

    def test_returns_absolute_path(self, tmp_path: Path):
        path = save_plan("# Plan", target_dir=tmp_path)
        assert path.is_absolute()

    def test_appends_to_existing_file(self, tmp_path: Path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("- [x] Done task\n- [ ] Open task\n", encoding="utf-8")
        save_plan("- [ ] New task", target_dir=tmp_path)
        text = plan_path.read_text(encoding="utf-8")
        assert "- [x] Done task" in text
        assert "- [ ] Open task" in text
        assert "- [ ] New task" in text

    def test_append_preserves_existing_content_exactly(self, tmp_path: Path):
        plan_path = tmp_path / _PLAN_FILENAME
        original = "# Phase 1\n\n- [x] First\n- [ ] Second\n"
        plan_path.write_text(original, encoding="utf-8")
        save_plan("- [ ] Third", target_dir=tmp_path)
        text = plan_path.read_text(encoding="utf-8")
        assert text.startswith("# Phase 1\n\n- [x] First\n- [ ] Second")
        assert text.endswith("- [ ] Third\n")

    def test_append_separates_with_blank_line(self, tmp_path: Path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("- [ ] Existing\n", encoding="utf-8")
        save_plan("- [ ] New", target_dir=tmp_path)
        text = plan_path.read_text(encoding="utf-8")
        assert "\n\n- [ ] New\n" in text

    def test_default_target_dir_is_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        path = save_plan("# Plan")
        assert path.parent == tmp_path.resolve()


class TestParseCompletedTasks:
    def test_empty_content(self):
        assert parse_completed_tasks("") == []

    def test_no_checked_items(self):
        plan = "# Phase 1\n- [ ] Not done\n- [ ] Also not done\n"
        assert parse_completed_tasks(plan) == []

    def test_basic_checked_items(self):
        plan = "- [x] Set up project\n- [x] Add login form\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2
        assert tasks[0].text == "Set up project"
        assert tasks[1].text == "Add login form"

    def test_uppercase_x(self):
        plan = "- [X] Done task\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Done task"

    def test_mixed_checked_and_unchecked(self):
        plan = "- [x] Done\n- [ ] Not done\n- [x] Also done\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2
        assert tasks[0].text == "Done"
        assert tasks[1].text == "Also done"

    def test_feat_annotation(self):
        plan = '- [x] Add login form [feat: "User auth"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Add login form"
        assert tasks[0].features == ["User auth"]
        assert tasks[0].fixes == []

    def test_multi_feat_annotation(self):
        plan = '- [x] Add recording [feat: "Push-to-talk", "Keyboard shortcuts"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Push-to-talk", "Keyboard shortcuts"]

    def test_fix_annotation(self):
        plan = '- [x] Fix email check [fix: "email format not validated"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Fix email check"
        assert tasks[0].fixes == ["email format not validated"]
        assert tasks[0].features == []

    def test_no_annotation(self):
        plan = "- [x] Set up project structure\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == []
        assert tasks[0].fixes == []

    def test_indented_subtask(self):
        plan = "- [x] Main task\n  - [x] Subtask one\n    - [x] Deep subtask\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert tasks[0].indent == 0
        assert tasks[1].indent == 2
        assert tasks[2].indent == 4

    def test_skips_non_task_lines(self):
        plan = (
            "# Phase 1: Core\n"
            "\n"
            "## Objective\n"
            "Build the core.\n"
            "\n"
            "- [x] First task\n"
            "Some text.\n"
            "- [x] Second task\n"
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2

    def test_full_plan(self):
        plan = """\
# MyApp

Web app built with Python/FastAPI.

- [x] Set up project structure and build system
- [x] Add user login form [feat: "User auth"]
  - [x] Create login page template
  - [x] Wire up auth backend [feat: "User auth"]
- [x] Build activity overview [feat: "Dashboard"]
- [x] Fix email validation [fix: "email format not checked"]
"""
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 6
        assert tasks[0].text == "Set up project structure and build system"
        assert tasks[0].features == []
        assert tasks[1].features == ["User auth"]
        assert tasks[4].features == ["Dashboard"]
        assert tasks[5].fixes == ["email format not checked"]

    def test_returns_completed_task_dataclass(self):
        plan = "- [x] Task one\n"
        tasks = parse_completed_tasks(plan)
        assert isinstance(tasks[0], CompletedTask)

    def test_multi_fix_annotation(self):
        plan = '- [x] Fix layout bugs [fix: "sidebar overlap", "footer gap"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Fix layout bugs"
        assert tasks[0].fixes == ["sidebar overlap", "footer gap"]
        assert tasks[0].features == []

    def test_annotation_like_text_midline_not_parsed(self):
        plan = '- [x] Update [feat: "old"] handler to use new API\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        # The regex only matches annotations at end of line, so mid-line
        # bracket text is kept as part of the task description.
        assert tasks[0].text == 'Update [feat: "old"] handler to use new API'
        assert tasks[0].features == []
        assert tasks[0].fixes == []

    def test_annotation_with_extra_spaces(self):
        plan = '- [x] Task with spacing [feat:  "Spaced feature"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Spaced feature"]

    def test_all_lines_annotated(self):
        plan = (
            '- [x] Add login [feat: "Auth"]\n'
            '- [x] Add dashboard [feat: "Dashboard"]\n'
            '- [x] Fix crash [fix: "null pointer on empty input"]\n'
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert all(t.features or t.fixes for t in tasks)
        assert tasks[0].features == ["Auth"]
        assert tasks[1].features == ["Dashboard"]
        assert tasks[2].fixes == ["null pointer on empty input"]

    def test_all_lines_unannotated(self):
        plan = "- [x] Set up project structure\n- [x] Configure CI pipeline\n- [x] Add README\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert all(t.features == [] and t.fixes == [] for t in tasks)

    def test_mixed_feat_fix_and_bare(self):
        plan = (
            "- [x] Scaffold project\n"
            '- [x] Add search [feat: "Full-text search"]\n'
            "- [x] Refactor utils\n"
            '- [x] Fix timeout [fix: "request hangs after 30s"]\n'
            '- [x] Add export [feat: "CSV export", "PDF export"]\n'
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 5
        assert tasks[0].features == [] and tasks[0].fixes == []
        assert tasks[1].features == ["Full-text search"]
        assert tasks[2].features == [] and tasks[2].fixes == []
        assert tasks[3].fixes == ["request hangs after 30s"]
        assert tasks[4].features == ["CSV export", "PDF export"]

    def test_indented_subtask_with_annotation(self):
        plan = (
            '- [x] Build auth module [feat: "Auth"]\n'
            '  - [x] Add password hashing [feat: "Auth"]\n'
            "  - [x] Write migration script\n"
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert tasks[0].indent == 0 and tasks[0].features == ["Auth"]
        assert tasks[1].indent == 2 and tasks[1].features == ["Auth"]
        assert tasks[2].indent == 2 and tasks[2].features == []

    def test_trailing_whitespace_after_annotation(self):
        plan = '- [x] Add feature [feat: "Foo"]   \n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Foo"]
        assert tasks[0].text == "Add feature"


class TestStripBugsSection:
    """Tests for _strip_bugs_section()."""

    def test_no_bugs_heading_unchanged(self):
        content = (
            "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project\n- [ ] Add login\n"
        )
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_strips_empty_bugs_heading(self):
        content = "# MyApp — Phase 1: Core\n\n- [ ] Task\n\n## Bugs\n"
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        assert "- [ ] Task" in result

    def test_strips_bugs_heading_and_keeps_tasks(self):
        content = "# MyApp — Phase 1: Core\n\n## Bugs\n\n- [ ] Set up project\n- [ ] Add login\n"
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        # Tasks that were under the LLM's ## Bugs are preserved.
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_preserves_other_content(self):
        content = "# MyApp — Phase 1: Core\n\nDescription.\n\n- [ ] First\n- [ ] Second\n"
        result = _strip_bugs_section(content)
        assert "- [ ] First" in result
        assert "- [ ] Second" in result
        assert "# MyApp — Phase 1: Core" in result
        assert "Description." in result


class TestSavePlanNeverEmitsBugsSection:
    """save_plan output must never contain a ## Bugs section."""

    def test_first_write_does_not_inject_bugs_section(self, tmp_path):
        content = "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project"
        save_plan(content, target_dir=tmp_path)
        result = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in result

    def test_append_does_not_inject_bugs(self, tmp_path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("# Phase 1\n\n- [ ] Existing\n", encoding="utf-8")
        save_plan("- [ ] New task", target_dir=tmp_path)
        result = plan_path.read_text(encoding="utf-8")
        assert "## Bugs" not in result

    def test_llm_bugs_heading_stripped_on_first_write(self, tmp_path):
        content = "# MyApp — Phase 1: Core\n\n## Bugs\n\n- [ ] Set up project\n- [ ] Add login\n"
        save_plan(content, target_dir=tmp_path)
        result = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in result
        # Tasks survive the strip.
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_llm_bugs_heading_stripped_on_append(self, tmp_path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("# Phase 1\n\n- [ ] Existing\n", encoding="utf-8")
        appended = "## Bugs\n\n- [ ] New task\n"
        save_plan(appended, target_dir=tmp_path)
        result = plan_path.read_text(encoding="utf-8")
        assert "## Bugs" not in result
        assert "- [ ] New task" in result
        assert "- [ ] Existing" in result


class TestPlanStructureForMcloop:
    """Verify that save_plan produces PLAN.md with correct structure for mcloop.

    Mcloop treats tasks under the H1 phase heading as feature work.
    duplo-generated PLAN.md must never contain a ``## Bugs`` section;
    that is an mcloop-internal convention added at runtime.
    """

    # Realistic LLM output: feature tasks under H1, no ## Bugs heading.
    _LLM_GOOD = (
        "# MyApp — Phase 1: Core\n"
        "\n"
        "Python/FastAPI web app with PostgreSQL.\n"
        "\n"
        '- [ ] Set up project structure [feat: "User auth"]\n'
        '- [ ] Add login form [feat: "User auth"]\n'
        '- [ ] Build dashboard [feat: "Dashboard"]\n'
    )

    # Broken LLM output: feature tasks placed under ## Bugs.
    _LLM_BAD = (
        "# MyApp — Phase 1: Core\n"
        "\n"
        "Python/FastAPI web app with PostgreSQL.\n"
        "\n"
        "## Bugs\n"
        "\n"
        '- [ ] Set up project structure [feat: "User auth"]\n'
        '- [ ] Add login form [feat: "User auth"]\n'
        '- [ ] Build dashboard [feat: "Dashboard"]\n'
    )

    def _feature_section_tasks(self, text: str) -> list[str]:
        """Return task lines that appear after the H1 heading."""
        lines = text.splitlines()
        past_h1 = False
        tasks: list[str] = []
        for line in lines:
            if line.startswith("# "):
                past_h1 = True
                continue
            if past_h1 and line.lstrip().startswith("- ["):
                tasks.append(line)
        return tasks

    def test_good_llm_output_preserves_feature_tasks(self, tmp_path):
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert len(self._feature_section_tasks(text)) == 3
        assert "## Bugs" not in text

    def test_bad_llm_output_has_bugs_heading_stripped(self, tmp_path):
        """When LLM includes ## Bugs, save_plan strips the heading and
        keeps the tasks that were under it."""
        save_plan(self._LLM_BAD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in text
        assert len(self._feature_section_tasks(text)) == 3

    def test_parse_completed_tasks_sees_feature_work(self, tmp_path):
        """After save_plan, checked tasks are parsed as feature work."""
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        # Simulate mcloop checking off tasks.
        checked = text.replace("- [ ]", "- [x]")
        tasks = parse_completed_tasks(checked)
        assert len(tasks) == 3
        feat_names = [n for t in tasks for n in t.features]
        assert "User auth" in feat_names
        assert "Dashboard" in feat_names
        # None are parsed as fixes (bugs).
        assert all(t.fixes == [] for t in tasks)

    def test_save_plan_output_never_contains_bugs_section(self, tmp_path):
        """Regression: duplo must never emit ``## Bugs`` via save_plan,
        whether the content lacks the heading, has an empty one, or has
        tasks placed beneath it."""
        inputs = [
            self._LLM_GOOD,
            self._LLM_BAD,
            "# Phase 1\n\n- [ ] Task\n\n## Bugs\n",
            "# Phase 1\n\n- [ ] Task\n",
        ]
        for i, content in enumerate(inputs):
            subdir = tmp_path / f"case_{i}"
            subdir.mkdir()
            save_plan(content, target_dir=subdir)
            text = (subdir / _PLAN_FILENAME).read_text(encoding="utf-8")
            assert "## Bugs" not in text, f"case {i} leaked ## Bugs"


class TestStripTrailingCommentary:
    """Tests for _strip_trailing_commentary() and its integration with
    generate_phase_plan() when the LLM wraps the plan in code fences AND
    adds meta-commentary after the closing fence.
    """

    def test_truncates_after_last_task_with_fence_and_commentary(self):
        # _strip_fences() cannot remove the fence because _FENCE_RE requires
        # the closing fence at end-of-string, and trailing commentary breaks
        # that. _ensure_h1_heading() strips the opening fence. The fix
        # function must then drop the closing fence and everything after it.
        llm_output = (
            "```markdown\n"
            "# MyApp — Phase 1: Core\n"
            "\n"
            "- [ ] First task\n"
            "- [ ] Second task\n"
            "- [ ] Third task\n"
            "```\n"
            "\n"
            "---\n"
            "\n"
            "**Structure:** The plan has three tasks.\n"
            "\n"
            "Want me to write it?\n"
        )
        with patch("duplo.planner.query", return_value=llm_output):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="MyApp",
                phase_number=1,
            )
        assert result.endswith("- [ ] Third task\n")
        assert "```" not in result
        assert "---" not in result
        assert "**Structure:**" not in result
        assert "Want me to write it?" not in result

    def test_keeps_content_unchanged_when_no_trailing_garbage(self):
        content = "# Phase 1: Core\n\n- [ ] Task one\n- [ ] Task two\n"
        assert _strip_trailing_commentary(content).endswith("- [ ] Task two\n")

    def test_truncates_after_indented_subtask(self):
        content = (
            "# Phase 1\n"
            "\n"
            "- [ ] Parent\n"
            "  - [ ] Nested subtask\n"
            "\n"
            "Trailing prose that should be dropped.\n"
        )
        result = _strip_trailing_commentary(content)
        assert result.endswith("  - [ ] Nested subtask\n")
        assert "Trailing prose" not in result

    def test_no_task_lines_returns_content_unchanged(self):
        content = "# Phase 1\n\nNo tasks here.\n"
        assert _strip_trailing_commentary(content) == content


class TestStripFences:
    """Tests for _strip_fences() removing LLM code-fence wrapping."""

    def test_strips_markdown_fence(self):
        wrapped = "```markdown\n# Phase 1: Core\n\n- [ ] Task\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_bare_fence(self):
        wrapped = "```\n# Phase 1: Core\n\n- [ ] Task\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_md_fence(self):
        wrapped = "```md\n# Phase 1: Core\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core"

    def test_no_fence_unchanged(self):
        plain = "# Phase 1: Core\n\n- [ ] Task"
        assert _strip_fences(plain) == plain

    def test_inner_fences_preserved(self):
        content = "# Phase 1\n\n```python\nprint('hi')\n```\n\n- [ ] Task"
        assert _strip_fences(content) == content

    def test_strips_tilde_fence(self):
        wrapped = "~~~markdown\n# Phase 1: Core\n\n- [ ] Task\n~~~"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_bare_tilde_fence(self):
        wrapped = "~~~\n# Phase 1: Core\n~~~"
        assert _strip_fences(wrapped) == "# Phase 1: Core"

    def test_leading_trailing_whitespace(self):
        wrapped = "  ```markdown\n# Phase 1\n```  "
        assert _strip_fences(wrapped) == "# Phase 1"

    def test_generate_phase_plan_strips_fences(self):
        fenced = "```markdown\n# MyApp — Phase 1: Core\n\n- [ ] Task\n```"
        with patch("duplo.planner.query", return_value=fenced):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert not result.startswith("```")
        assert result.startswith("# MyApp")

    def test_generate_next_phase_plan_strips_fences(self):
        fenced = "```markdown\n# Phase 2: Search\n\n- [ ] Task\n```"
        with patch("duplo.planner.query", return_value=fenced):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        assert not result.startswith("```")
        assert result.startswith("# Phase 2")
