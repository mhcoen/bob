"""Tests for the macOS Python CLI platform profile scaffold."""

from __future__ import annotations

from pathlib import Path

import duplo.platforms.macos.python_cli as python_cli
from duplo.platforms.scaffold import write_scaffold


def _get_profile():
    return python_cli._PROFILE


def _get_pyproject_scaffold():
    for sf in _get_profile().scaffold_files:
        if sf.path == "pyproject.toml":
            return sf
    raise AssertionError("pyproject.toml scaffold entry missing from python_cli profile")


class TestPyprojectScaffoldTemplate:
    def test_profile_includes_pyproject_scaffold_file(self):
        paths = [sf.path for sf in _get_profile().scaffold_files]
        assert "pyproject.toml" in paths

    def test_template_declares_pytest_xdist_dev_dep(self):
        content = _get_pyproject_scaffold().content
        assert "pytest-xdist" in content

    def test_template_declares_pytest_timeout_dev_dep(self):
        content = _get_pyproject_scaffold().content
        assert "pytest-timeout" in content

    def test_template_declares_pytest_randomly_dev_dep(self):
        content = _get_pyproject_scaffold().content
        assert "pytest-randomly" in content

    def test_template_sets_pytest_addopts_parallel(self):
        content = _get_pyproject_scaffold().content
        assert "[tool.pytest.ini_options]" in content
        assert 'addopts = "-n auto"' in content

    def test_template_sets_pytest_timeout(self):
        content = _get_pyproject_scaffold().content
        assert "[tool.pytest.ini_options]" in content
        assert "timeout = 60" in content


class TestPyprojectWrittenByScaffold:
    def test_write_scaffold_emits_pyproject_with_pytest_config(self, tmp_path: Path):
        write_scaffold([_get_profile()], "myapp", target_dir=tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        assert pyproject.is_file(), "pyproject.toml not written by scaffold"
        text = pyproject.read_text(encoding="utf-8")

        assert "pytest-xdist" in text
        assert "pytest-timeout" in text
        assert "pytest-randomly" in text

        assert "[tool.pytest.ini_options]" in text
        assert 'addopts = "-n auto"' in text
        assert "timeout = 60" in text

        assert "myapp" in text
        assert "{project_name}" not in text
