"""Tests for duplo.runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from duplo.runner import run_mcloop


class TestRunMcloop:
    def test_runs_mcloop_sync_in_target_dir(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.runner.subprocess.run", return_value=mock_result) as mock_run:
            code = run_mcloop(tmp_path)

        mock_run.assert_called_once_with(
            ["mcloop"],
            cwd=str(tmp_path.resolve()),
        )
        assert code == 0

    def test_default_target_dir_is_current_directory(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.runner.subprocess.run", return_value=mock_result) as mock_run:
            run_mcloop()

        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == str(Path(".").resolve())

    def test_returns_nonzero_exit_code_on_failure(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("duplo.runner.subprocess.run", return_value=mock_result):
            code = run_mcloop(tmp_path)

        assert code == 1

    def test_accepts_string_path(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("duplo.runner.subprocess.run", return_value=mock_result) as mock_run:
            run_mcloop(str(tmp_path))

        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == str(tmp_path.resolve())
