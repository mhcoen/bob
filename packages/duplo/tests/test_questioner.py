"""Tests for duplo.questioner.

The interactive-prompt helpers (``ask_preferences``, ``_ask_platform``,
``_ask_language``, ``_ask_list``) were removed in Phase 7.4.4 as dead
code. The test classes below cover those removed helpers; they are
preserved under ``pytest.mark.skip`` rather than deleted (per the
no-file-delete rule documented in NOTES.md [7.4.3]). The
``BuildPreferences`` dataclass survives and is still importable from
``duplo.questioner``.
"""

from __future__ import annotations

import pytest

from duplo.questioner import BuildPreferences

pytestmark = pytest.mark.skip(
    reason="Phase 7.4.4: ask_preferences / _ask_* helpers removed as dead code."
)


def make_input(*answers: str):
    """Return an input_fn that yields *answers* in order."""
    it = iter(answers)
    return lambda _prompt: next(it)


# ---------------------------------------------------------------------------
# _ask_platform
# ---------------------------------------------------------------------------


class TestAskPlatform:
    def _run(self, *answers):
        from duplo.questioner import _ask_platform  # noqa: F401 — removed; tests skipped

        lines = []
        result = _ask_platform(make_input(*answers), lines.append)
        return result, lines

    def test_exact_match(self):
        result, _ = self._run("web")
        assert result == "web"

    def test_prefix_match(self):
        result, _ = self._run("mob")
        assert result == "mobile-ios"

    def test_prefix_match_desktop(self):
        result, _ = self._run("desk")
        assert result == "desktop"

    def test_prefix_match_cli(self):
        result, _ = self._run("cl")
        assert result == "cli"

    def test_unknown_value_returned_as_is(self):
        result, _ = self._run("flutter")
        assert result == "flutter"

    def test_empty_then_valid(self):
        result, lines = self._run("", "api")
        assert result == "api"
        assert any("Please enter" in line for line in lines)

    def test_case_insensitive(self):
        result, _ = self._run("WEB")
        assert result == "web"


# ---------------------------------------------------------------------------
# _ask_list
# ---------------------------------------------------------------------------


class TestAskList:
    def _run(self, *answers):
        from duplo.questioner import _ask_list  # noqa: F401 — removed; tests skipped

        lines = []
        result = _ask_list("Prompt", "Item", make_input(*answers), lines.append)
        return result, lines

    def test_blank_immediately_returns_empty(self):
        result, _ = self._run("")
        assert result == []

    def test_single_item(self):
        result, _ = self._run("use PostgreSQL", "")
        assert result == ["use PostgreSQL"]

    def test_multiple_items(self):
        result, _ = self._run("must use AWS", "no vendor lock-in", "")
        assert result == ["must use AWS", "no vendor lock-in"]


# ---------------------------------------------------------------------------
# ask_preferences
# ---------------------------------------------------------------------------


class TestAskPreferences:
    def _run(self, *answers):
        from duplo.questioner import ask_preferences  # noqa: F401 — removed; tests skipped

        lines = []
        result = ask_preferences(input_fn=make_input(*answers), print_fn=lines.append)
        return result, lines

    def test_returns_build_preferences(self):
        result, _ = self._run("web", "Python/FastAPI", "", "")
        assert isinstance(result, BuildPreferences)

    def test_platform_and_language_captured(self):
        result, _ = self._run("web", "TypeScript/React", "", "")
        assert result.platform == "web"
        assert result.language == "TypeScript/React"

    def test_no_constraints_or_preferences(self):
        result, _ = self._run("cli", "Go", "", "")
        assert result.constraints == []
        assert result.preferences == []

    def test_constraints_captured(self):
        result, _ = self._run("api", "Python", "must use Redis", "", "")
        assert result.constraints == ["must use Redis"]

    def test_preferences_captured(self):
        result, _ = self._run("web", "React", "", "use TDD", "")
        assert result.preferences == ["use TDD"]

    def test_summary_printed(self):
        _, lines = self._run("desktop", "Rust", "", "")
        combined = "\n".join(lines)
        assert "desktop" in combined
        assert "Rust" in combined

    def test_platform_prefix_resolved(self):
        result, _ = self._run("mob", "Swift", "", "")
        assert result.platform == "mobile-ios"

    def test_multiple_constraints_and_preferences(self):
        result, _ = self._run(
            "web",
            "Python",
            "constraint A",
            "constraint B",
            "",
            "pref X",
            "pref Y",
            "",
        )
        assert result.constraints == ["constraint A", "constraint B"]
        assert result.preferences == ["pref X", "pref Y"]
