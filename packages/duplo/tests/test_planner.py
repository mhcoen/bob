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
    _inject_bugs_section,
    _strip_fences,
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
        # First write injects ## Bugs section.
        assert "## Bugs" in text

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


class TestInjectBugsSection:
    """Tests for _inject_bugs_section()."""

    def test_bugs_after_checklist(self):
        content = (
            "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project\n- [ ] Add login\n"
        )
        result = _inject_bugs_section(content)
        assert "## Bugs" in result
        bugs_pos = result.index("## Bugs")
        task_pos = result.index("- [ ] Set up project")
        assert bugs_pos > task_pos

    def test_bugs_after_second_heading(self):
        content = "# MyApp — Phase 1: Core\n\nBuild the app.\n\n## Implementation\n\n- [ ] Task\n"
        result = _inject_bugs_section(content)
        bugs_pos = result.index("## Bugs")
        impl_pos = result.index("## Implementation")
        assert bugs_pos > impl_pos

    def test_no_heading(self):
        content = "Just some text\n- [ ] Task\n"
        result = _inject_bugs_section(content)
        assert result.endswith("## Bugs\n")
        assert "Just some text" in result

    def test_preserves_all_content(self):
        content = "# MyApp — Phase 1: Core\n\nDescription.\n\n- [ ] First\n- [ ] Second\n"
        result = _inject_bugs_section(content)
        assert "- [ ] First" in result
        assert "- [ ] Second" in result
        assert "# MyApp — Phase 1: Core" in result
        assert "Description." in result

    def test_llm_bugs_heading_removed_tasks_moved_above(self):
        content = "# MyApp — Phase 1: Core\n\n## Bugs\n\n- [ ] Set up project\n- [ ] Add login\n"
        result = _inject_bugs_section(content)
        # Only one ## Bugs heading, at the end.
        assert result.count("## Bugs") == 1
        assert result.endswith("## Bugs\n")
        # Feature tasks are above ## Bugs.
        bugs_pos = result.index("## Bugs")
        assert result.index("- [ ] Set up project") < bugs_pos
        assert result.index("- [ ] Add login") < bugs_pos

    def test_llm_bugs_heading_empty_section(self):
        content = "# MyApp — Phase 1: Core\n\n- [ ] Task\n\n## Bugs\n"
        result = _inject_bugs_section(content)
        assert result.count("## Bugs") == 1
        assert result.endswith("## Bugs\n")
        assert result.index("- [ ] Task") < result.index("## Bugs")


class TestSavePlanBugsSection:
    """Tests that save_plan injects ## Bugs on first write."""

    def test_first_write_injects_bugs_section(self, tmp_path):
        content = "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project"
        save_plan(content, target_dir=tmp_path)
        result = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" in result

    def test_append_does_not_inject_bugs(self, tmp_path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("# Phase 1\n\n- [ ] Existing\n", encoding="utf-8")
        save_plan("- [ ] New task", target_dir=tmp_path)
        result = plan_path.read_text(encoding="utf-8")
        # No ## Bugs injected on append.
        assert "## Bugs" not in result


class TestPlanStructureForMcloop:
    """Verify that save_plan produces PLAN.md with correct structure for mcloop.

    Mcloop treats tasks under the H1 phase heading as feature work and
    tasks under ## Bugs as bug-fix work.  The regression was: LLM output
    placed feature tasks under ## Bugs, so mcloop saw zero feature tasks.
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

    def _bugs_section_tasks(self, text: str) -> list[str]:
        """Return task lines that appear under the ## Bugs heading."""
        lines = text.splitlines()
        in_bugs = False
        tasks: list[str] = []
        for line in lines:
            if line.strip().lower() == "## bugs":
                in_bugs = True
                continue
            if in_bugs and re.match(r"^#{1,2}\s", line):
                break
            if in_bugs and line.lstrip().startswith("- ["):
                tasks.append(line)
        return tasks

    def _feature_section_tasks(self, text: str) -> list[str]:
        """Return task lines that appear between the H1 heading and ## Bugs."""
        lines = text.splitlines()
        past_h1 = False
        tasks: list[str] = []
        for line in lines:
            if line.startswith("# "):
                past_h1 = True
                continue
            if past_h1 and line.strip().lower() == "## bugs":
                break
            if past_h1 and line.lstrip().startswith("- ["):
                tasks.append(line)
        return tasks

    def test_good_llm_output_has_feature_tasks_above_bugs(self, tmp_path):
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert len(self._feature_section_tasks(text)) == 3
        assert self._bugs_section_tasks(text) == []

    def test_bad_llm_output_rescued_by_inject(self, tmp_path):
        """When LLM puts tasks under ## Bugs, save_plan moves them above."""
        save_plan(self._LLM_BAD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert len(self._feature_section_tasks(text)) == 3
        assert self._bugs_section_tasks(text) == []

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

    def test_bugs_section_empty_after_first_write(self, tmp_path):
        """First write always produces an empty ## Bugs section at the end."""
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" in text
        bugs_idx = text.index("## Bugs")
        after_bugs = text[bugs_idx + len("## Bugs") :].strip()
        # Nothing after ## Bugs except whitespace.
        assert after_bugs == ""


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
