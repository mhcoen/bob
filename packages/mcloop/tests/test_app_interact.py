"""Tests for mcloop.app_interact."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mcloop import app_interact


class TestRunOsascript:
    @patch("subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
        result = app_interact._run_osascript('return "hello"')
        assert result == "hello"

    @patch("subprocess.run")
    def test_raises_on_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with pytest.raises(RuntimeError, match="osascript failed"):
            app_interact._run_osascript("bad script")

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_raises_on_missing_osascript(self, mock_run):
        with pytest.raises(RuntimeError, match="not found"):
            app_interact._run_osascript("anything")

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=10),
    )
    def test_raises_on_timeout(self, mock_run):
        with pytest.raises(RuntimeError, match="timed out"):
            app_interact._run_osascript("slow script")


class TestClickButton:
    @patch("mcloop.app_interact._run_osascript")
    def test_calls_osascript_with_correct_script(self, mock_osa):
        app_interact.click_button("MyApp", "OK")
        mock_osa.assert_called_once()
        script = mock_osa.call_args[0][0]
        assert 'process "MyApp"' in script
        assert 'click button "OK" of window 1' in script


class TestSelectMenuItem:
    @patch("mcloop.app_interact._run_osascript")
    def test_two_level_menu(self, mock_osa):
        app_interact.select_menu_item("TextEdit", "File", "Save")
        script = mock_osa.call_args[0][0]
        assert 'menu item "Save"' in script
        assert 'menu "File"' in script
        assert 'menu bar item "File"' in script

    @patch("mcloop.app_interact._run_osascript")
    def test_three_level_menu(self, mock_osa):
        app_interact.select_menu_item("App", "Edit", "Find", "Find...")
        script = mock_osa.call_args[0][0]
        assert 'menu item "Find..."' in script
        assert 'menu item "Find"' in script
        assert 'menu "Edit"' in script

    def test_too_few_path_elements(self):
        with pytest.raises(ValueError, match="at least 2"):
            app_interact.select_menu_item("App", "File")


class TestTypeText:
    @patch("mcloop.app_interact._run_osascript")
    def test_types_simple_text(self, mock_osa):
        app_interact.type_text("hello")
        script = mock_osa.call_args[0][0]
        assert 'keystroke "hello"' in script

    @patch("mcloop.app_interact._run_osascript")
    def test_escapes_quotes(self, mock_osa):
        app_interact.type_text('say "hi"')
        script = mock_osa.call_args[0][0]
        assert 'keystroke "say \\"hi\\""' in script


class TestReadValue:
    @patch("mcloop.app_interact._run_osascript", return_value="John")
    def test_returns_value(self, mock_osa):
        result = app_interact.read_value("MyApp", "text field", "Name")
        assert result == "John"
        script = mock_osa.call_args[0][0]
        assert 'value of text field "Name" of window 1' in script


class TestListElements:
    @patch(
        "mcloop.app_interact._run_osascript",
        return_value="button OK, text field Name",
    )
    def test_returns_element_list(self, mock_osa):
        result = app_interact.list_elements("MyApp")
        assert "button OK" in result
        script = mock_osa.call_args[0][0]
        assert "entire contents of window 1" in script


class TestWindowExists:
    @patch("mcloop.app_interact._run_osascript", return_value="2")
    def test_true_when_windows_exist(self, mock_osa):
        assert app_interact.window_exists("MyApp") is True

    @patch("mcloop.app_interact._run_osascript", return_value="0")
    def test_false_when_no_windows(self, mock_osa):
        assert app_interact.window_exists("MyApp") is False

    @patch(
        "mcloop.app_interact._run_osascript",
        side_effect=RuntimeError("no process"),
    )
    def test_false_on_error(self, mock_osa):
        assert app_interact.window_exists("NoApp") is False


class TestScreenshotWindow:
    @patch("subprocess.run")
    @patch("mcloop.app_interact._run_osascript", return_value="12345")
    def test_captures_window(self, mock_osa, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        app_interact.screenshot_window("MyApp", "/tmp/shot.png")
        mock_run.assert_called_once_with(
            ["screencapture", "-l", "12345", "/tmp/shot.png"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("subprocess.run", side_effect=FileNotFoundError)
    @patch("mcloop.app_interact._run_osascript", return_value="12345")
    def test_raises_on_missing_screencapture(self, mock_osa, mock_run):
        with pytest.raises(RuntimeError, match="screencapture not found"):
            app_interact.screenshot_window("MyApp", "/tmp/shot.png")

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="screencapture", timeout=10),
    )
    @patch("mcloop.app_interact._run_osascript", return_value="12345")
    def test_raises_on_timeout(self, mock_osa, mock_run):
        with pytest.raises(RuntimeError, match="timed out"):
            app_interact.screenshot_window("MyApp", "/tmp/shot.png")
