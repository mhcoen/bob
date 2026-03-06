"""Tests for duplo.appshot."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from duplo.appshot import capture_appshot


class TestCaptureAppshot:
    def test_runs_appshot_with_app_name_and_output(self, tmp_path):
        output = tmp_path / "screenshots" / "current" / "main.png"
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result) as mock_run:
            code = capture_appshot("MyApp", output)

        mock_run.assert_called_once_with(["appshot", "MyApp", str(output), "--wait", "2"])
        assert code == 0

    def test_creates_output_directory(self, tmp_path):
        output = tmp_path / "screenshots" / "current" / "main.png"
        assert not output.parent.exists()
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result):
            capture_appshot("MyApp", output)

        assert output.parent.exists()

    def test_includes_launch_flag_when_provided(self, tmp_path):
        output = tmp_path / "main.png"
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result) as mock_run:
            capture_appshot("MyApp", output, launch="./run.sh")

        args = mock_run.call_args[0][0]
        assert "--launch" in args
        assert "./run.sh" in args

    def test_no_launch_flag_when_not_provided(self, tmp_path):
        output = tmp_path / "main.png"
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result) as mock_run:
            capture_appshot("MyApp", output)

        args = mock_run.call_args[0][0]
        assert "--launch" not in args

    def test_custom_wait(self, tmp_path):
        output = tmp_path / "main.png"
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result) as mock_run:
            capture_appshot("MyApp", output, wait=5)

        args = mock_run.call_args[0][0]
        assert "--wait" in args
        assert "5" in args

    def test_returns_nonzero_on_failure(self, tmp_path):
        output = tmp_path / "main.png"
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("duplo.appshot.subprocess.run", return_value=mock_result):
            code = capture_appshot("MyApp", output)

        assert code == 1

    def test_accepts_string_path(self, tmp_path):
        output = str(tmp_path / "main.png")
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.appshot.subprocess.run", return_value=mock_result) as mock_run:
            capture_appshot("MyApp", output)

        args = mock_run.call_args[0][0]
        assert output in args
