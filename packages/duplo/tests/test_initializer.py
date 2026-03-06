"""Tests for duplo.initializer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from duplo.initializer import create_project_dir, project_name_from_url


class TestProjectNameFromUrl:
    def test_simple_hostname(self):
        assert project_name_from_url("https://linear.app") == "linear-app"

    def test_subdomain(self):
        assert project_name_from_url("https://app.example.com") == "app-example-com"

    def test_with_path(self):
        assert project_name_from_url("https://notion.so/product") == "notion-so"

    def test_plain_domain(self):
        assert project_name_from_url("https://github.com") == "github-com"

    def test_no_hostname_fallback(self):
        assert project_name_from_url("not-a-url") == "project"


class TestCreateProjectDir:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = create_project_dir(target)
        assert target.exists()
        assert target.is_dir()
        assert result == target

    def test_creates_duplo_dir(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            create_project_dir(target)
        assert (target / ".duplo").exists()
        assert (target / ".duplo").is_dir()

    def test_creates_gitignore_with_duplo(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            create_project_dir(target)
        gitignore = target / ".gitignore"
        assert gitignore.exists()
        assert ".duplo/" in gitignore.read_text()

    def test_returns_resolved_path(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = create_project_dir(target)
        assert result == target.resolve()

    def test_raises_if_already_exists(self, tmp_path):
        target = tmp_path / "existing"
        target.mkdir()
        with pytest.raises(FileExistsError, match="already exists"):
            create_project_dir(target)

    def test_calls_git_init(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            create_project_dir(target)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "init"
        assert str(target) in args

    def test_raises_on_git_init_failure(self, tmp_path):
        target = tmp_path / "my-project"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stderr="fatal: something went wrong")
            with pytest.raises(RuntimeError, match="git init failed"):
                create_project_dir(target)

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            create_project_dir(target)
        assert target.exists()

    def test_accepts_string_path(self, tmp_path):
        target = str(tmp_path / "my-project")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = create_project_dir(target)
        assert isinstance(result, Path)
        assert result.exists()

    def test_git_init_actually_works(self, tmp_path):
        """Integration test: real git init."""
        target = tmp_path / "real-project"
        result = create_project_dir(target)
        assert (result / ".git").exists()
