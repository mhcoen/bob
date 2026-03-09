"""Tests for mcloop.formatting."""

from __future__ import annotations

import os
from unittest.mock import patch

from mcloop import formatting


def _force_no_color():
    """Patch _use_color to return False."""
    return patch.object(formatting, "_use_color", return_value=False)


def _force_color():
    """Patch _use_color to return True."""
    return patch.object(formatting, "_use_color", return_value=True)


class TestUseColor:
    def test_no_color_env(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert formatting._use_color() is False

    def test_no_isatty(self):
        """Non-tty stdout returns False."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                assert formatting._use_color() is False

    def test_tty_stdout(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert formatting._use_color() is True


class TestUserBanner:
    def test_no_color_contains_label(self):
        with _force_no_color():
            result = formatting.user_banner("3.1", "Click the button")
            assert "USER ACTION REQUIRED" in result
            assert "Task 3.1" in result
            assert "Click the button" in result
            assert "Press Enter on an empty line" in result

    def test_no_color_has_separators(self):
        with _force_no_color():
            result = formatting.user_banner("1", "Do something")
            assert "=" * 60 in result
            assert "-" * 60 in result

    def test_color_has_ansi_codes(self):
        with _force_color():
            result = formatting.user_banner("1", "Do something")
            assert formatting.BOLD in result
            assert formatting.REVERSE in result
            assert formatting.RESET in result

    def test_distinct_from_auto_banner(self):
        """User banner should look different from auto banner."""
        with _force_no_color():
            user = formatting.user_banner("1", "test")
            auto = formatting.auto_banner("1", "run_cli", "ls")
            # User banner uses = signs, auto uses ─ characters
            assert "=" * 60 in user
            assert "─" in auto
            assert "USER ACTION REQUIRED" in user
            assert "AUTO OBSERVATION" in auto


class TestAutoBanner:
    def test_no_color_contains_info(self):
        with _force_no_color():
            result = formatting.auto_banner("2", "screenshot", "MyApp")
            assert "AUTO OBSERVATION" in result
            assert "Task 2" in result
            assert "Action: screenshot" in result
            assert "Args: MyApp" in result

    def test_color_has_cyan(self):
        with _force_color():
            result = formatting.auto_banner("2", "run_cli", "ls")
            assert formatting.CYAN in result

    def test_uses_light_separators(self):
        with _force_no_color():
            result = formatting.auto_banner("1", "run_cli", "ls")
            assert "─" in result


class TestTaskHeader:
    def test_no_color(self):
        with _force_no_color():
            result = formatting.task_header("3", "Implement feature", "claude")
            assert result == "\n>>> Task 3) Implement feature (using claude)"

    def test_color_has_bold(self):
        with _force_color():
            result = formatting.task_header("3", "Implement feature", "claude")
            assert formatting.BOLD in result
            assert "Task 3) Implement feature" in result


class TestTaskComplete:
    def test_no_color(self):
        with _force_no_color():
            result = formatting.task_complete("3", "2m 15s")
            assert result == "\n>>> Completed 3) [2m 15s]"

    def test_color_has_green(self):
        with _force_color():
            result = formatting.task_complete("3", "2m 15s")
            assert formatting.GREEN in result


class TestErrorMsg:
    def test_no_color(self):
        with _force_no_color():
            result = formatting.error_msg("Something broke")
            assert result == "\n!!! Something broke"

    def test_color_has_red_bold(self):
        with _force_color():
            result = formatting.error_msg("Something broke")
            assert formatting.RED in result
            assert formatting.BOLD in result


class TestSystemMsg:
    def test_no_color(self):
        with _force_no_color():
            result = formatting.system_msg("Running audit...")
            assert result == "\n>>> Running audit..."

    def test_color_has_dim(self):
        with _force_color():
            result = formatting.system_msg("Running audit...")
            assert formatting.DIM in result


class TestSummary:
    def test_header_no_color(self):
        with _force_no_color():
            result = formatting.summary_header()
            assert "=" * 40 in result
            assert "McLoop Summary" in result

    def test_footer_no_color(self):
        with _force_no_color():
            result = formatting.summary_footer()
            assert result == "=" * 40

    def test_header_color_has_bold(self):
        with _force_color():
            result = formatting.summary_header()
            assert formatting.BOLD in result

    def test_footer_color_has_bold(self):
        with _force_color():
            result = formatting.summary_footer()
            assert formatting.BOLD in result
