"""Tests for duplo.runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from duplo.runner import run_mcloop


class TestRunMcloop:
    def test_runs_mcloop_in_target_dir(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with patch("duplo.runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
            code = run_mcloop(tmp_path)

        mock_popen.assert_called_once_with(
            ["mcloop"],
            cwd=str(tmp_path.resolve()),
            start_new_session=True,
        )
        mock_proc.wait.assert_called_once()
        assert code == 0

    def test_default_target_dir_is_current_directory(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with patch("duplo.runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
            run_mcloop()

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == str(Path(".").resolve())

    def test_returns_nonzero_exit_code_on_failure(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        with patch("duplo.runner.subprocess.Popen", return_value=mock_proc):
            code = run_mcloop(tmp_path)

        assert code == 1

    def test_accepts_string_path(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with patch("duplo.runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
            run_mcloop(str(tmp_path))

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == str(tmp_path.resolve())

    def test_returns_1_when_mcloop_not_found(self, tmp_path):
        with patch("duplo.runner.subprocess.Popen", side_effect=FileNotFoundError):
            code = run_mcloop(tmp_path)

        assert code == 1
