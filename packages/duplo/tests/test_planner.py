"""Tests for duplo.planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from duplo.extractor import Feature
from duplo.planner import (
    _PLAN_FILENAME,
    _detect_next_phase_number,
    append_test_tasks,
    generate_next_phase_plan,
    generate_phase_plan,
    save_plan,
)
from duplo.questioner import BuildPreferences


def _make_client(response_text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = response_text
    message = MagicMock()
    message.content = [content_block]
    client = MagicMock()
    client.messages.create.return_value = message
    return client


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


_SAMPLE_PLAN = "# Phase 1: Core Auth\n\n## Objective\nMinimal working app.\n"


class TestGeneratePhasePlan:
    def test_returns_string(self):
        client = _make_client(_SAMPLE_PLAN)
        result = generate_phase_plan(
            "https://example.com",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        assert isinstance(result, str)
        assert result == _SAMPLE_PLAN.strip()

    def test_passes_source_url_to_api(self):
        client = _make_client(_SAMPLE_PLAN)
        generate_phase_plan(
            "https://acme.io",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "https://acme.io" in user_content

    def test_passes_features_to_api(self):
        client = _make_client(_SAMPLE_PLAN)
        generate_phase_plan(
            "https://example.com",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "User auth" in user_content
        assert "Dashboard" in user_content

    def test_passes_preferences_to_api(self):
        client = _make_client(_SAMPLE_PLAN)
        generate_phase_plan(
            "https://example.com",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Python/FastAPI" in user_content
        assert "PostgreSQL only" in user_content

    def test_passes_platform_to_api(self):
        client = _make_client(_SAMPLE_PLAN)
        generate_phase_plan(
            "https://example.com",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "web" in user_content

    def test_uses_expected_model(self):
        client = _make_client(_SAMPLE_PLAN)
        generate_phase_plan(
            "https://example.com",
            _sample_features(),
            _sample_prefs(),
            client=client,
        )
        call_args = client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_creates_default_client_when_none(self):
        mock_client = _make_client(_SAMPLE_PLAN)
        with patch("duplo.planner.anthropic.Anthropic", return_value=mock_client):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert result == _SAMPLE_PLAN.strip()

    def test_handles_empty_features(self):
        client = _make_client(_SAMPLE_PLAN)
        result = generate_phase_plan(
            "https://example.com",
            [],
            _sample_prefs(),
            client=client,
        )
        assert isinstance(result, str)

    def test_handles_empty_constraints_and_preferences(self):
        client = _make_client(_SAMPLE_PLAN)
        prefs = BuildPreferences(platform="cli", language="Go", constraints=[], preferences=[])
        result = generate_phase_plan(
            "https://example.com",
            _sample_features(),
            prefs,
            client=client,
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "(none)" in user_content
        assert isinstance(result, str)


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


_SAMPLE_NEXT_PLAN = "# Phase 2: Search\n\n## Objective\nAdd search.\n"
_SAMPLE_CURRENT_PLAN = "# Phase 1: Core Auth\n\n## Objective\nMinimal app.\n"


class TestGenerateNextPhasePlan:
    def test_returns_string(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        result = generate_next_phase_plan(
            _SAMPLE_CURRENT_PLAN, "Add search feature.", client=client
        )
        assert isinstance(result, str)
        assert result == _SAMPLE_NEXT_PLAN.strip()

    def test_passes_current_plan_to_api(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", client=client)
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Phase 1: Core Auth" in user_content

    def test_passes_feedback_to_api(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Needs dark mode.", client=client)
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Needs dark mode." in user_content

    def test_passes_issues_text_to_api(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(
            _SAMPLE_CURRENT_PLAN, "feedback", "- Layout broken", client=client
        )
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Layout broken" in user_content

    def test_next_phase_number_in_prompt(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", client=client)
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Phase 2" in user_content

    def test_no_issues_text_shows_no_issues_message(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", client=client)
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "No visual issues reported" in user_content

    def test_empty_issues_text_shows_no_issues_message(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", "", client=client)
        call_args = client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "No visual issues reported" in user_content

    def test_uses_expected_model(self):
        client = _make_client(_SAMPLE_NEXT_PLAN)
        generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", client=client)
        call_args = client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_creates_default_client_when_none(self):
        mock_client = _make_client(_SAMPLE_NEXT_PLAN)
        with patch("duplo.planner.anthropic.Anthropic", return_value=mock_client):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        assert result == _SAMPLE_NEXT_PLAN.strip()


class TestAppendTestTasks:
    def test_appends_tasks_to_plan(self):
        plan = "# Phase 1\n- [ ] Build core"
        tasks = ["- [ ] Wire up tests", "  - [ ] Replace stub"]
        result = append_test_tasks(plan, tasks)
        assert result.endswith("- [ ] Wire up tests\n  - [ ] Replace stub\n")
        assert "Build core" in result

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
