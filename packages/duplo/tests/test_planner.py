"""Tests for duplo.planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import re

import pytest

from duplo.extractor import Feature
from duplo.planner import (
    _NEXT_PHASE_SYSTEM,
    _PHASE_SYSTEM,
    _PLAN_FILENAME,
    _detect_next_phase_number,
    append_test_tasks,
    generate_next_phase_plan,
    generate_phase_plan,
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


class TestPhaseSystemPromptAnnotations:
    def test_system_prompt_requires_feat_annotation(self):
        assert '[feat: "Feature Name"]' in _PHASE_SYSTEM

    def test_system_prompt_requires_multi_feature_annotation(self):
        assert "comma-separated" in _PHASE_SYSTEM

    def test_system_prompt_requires_fix_annotation(self):
        assert '[fix: "description"]' in _PHASE_SYSTEM

    def test_system_prompt_no_annotation_for_scaffolding(self):
        assert "no annotation" in _PHASE_SYSTEM.lower()

    def test_system_prompt_shows_feat_example_in_format(self):
        assert '[feat: "User authentication"]' in _PHASE_SYSTEM

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
        assert path.read_text(encoding="utf-8") == content + "\n"

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
