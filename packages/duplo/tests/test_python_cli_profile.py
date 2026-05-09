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


def _get_run_sh_scaffold():
    for sf in _get_profile().scaffold_files:
        if sf.path == "run.sh":
            return sf
    raise AssertionError("run.sh scaffold entry missing from python_cli profile")


class TestRunShScaffoldTemplate:
    """Pin the run.sh template's installation behavior.

    The previous template ran:

        pip install -e ".[dev]" --quiet 2>/dev/null \\
            || pip install -e . --quiet

    That silently fell back to a non-dev install when the dev
    install failed (e.g., a transient pypi issue, a TLS problem,
    or a missing source package directory) and suppressed stderr,
    producing a venv that lacked pytest-xdist / pytest-timeout /
    ruff. Downstream pytest then failed with
    ``unrecognized arguments: -n`` on every retry. These tests pin
    the corrected shape so a future template edit cannot silently
    re-introduce the bug.
    """

    def test_template_installs_with_dev_extra(self):
        content = _get_run_sh_scaffold().content
        assert 'install -e ".[dev]"' in content

    @staticmethod
    def _executable_install_lines(content: str) -> list[str]:
        """Return install -e lines that are actual shell commands,
        skipping bash comments. The template's comment block names
        the prior buggy shape as documentation, which would
        otherwise trip the assertions below.
        """
        out: list[str] = []
        for line in content.splitlines():
            if "install -e" not in line:
                continue
            if line.lstrip().startswith("#"):
                continue
            out.append(line)
        return out

    def test_template_does_not_silently_fall_back_to_non_dev_install(self):
        content = _get_run_sh_scaffold().content
        lines = self._executable_install_lines(content)
        assert lines, (
            "expected at least one executable pip install -e line "
            "in run.sh template"
        )
        for line in lines:
            assert ".[dev]" in line, (
                "pip install line in run.sh template must request "
                f"the [dev] extra: {line!r}"
            )
            assert "||" not in line, (
                "pip install line must not chain a fallback "
                f"to a non-dev install: {line!r}"
            )

    def test_template_does_not_redirect_pip_stderr_to_devnull(self):
        """``2>/dev/null`` on the pip install line was the second
        half of the original bug: it hid every failure mode."""
        content = _get_run_sh_scaffold().content
        for line in self._executable_install_lines(content):
            assert "2>/dev/null" not in line, (
                "pip install line must not redirect stderr to "
                f"/dev/null: {line!r}"
            )

    def test_template_uses_set_pipefail(self):
        """``set -euo pipefail`` makes the pip install failure
        surface as a non-zero run.sh exit. Without it, the
        silent-fallback fix above would still hide errors via shell
        quirks."""
        content = _get_run_sh_scaffold().content
        assert "set -euo pipefail" in content


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


class TestRunShWrittenByScaffold:
    """The end-to-end render: a fresh project gets a run.sh on disk
    that uses the dev install and surfaces failures."""

    def test_write_scaffold_emits_run_sh_with_dev_install(self, tmp_path: Path):
        write_scaffold([_get_profile()], "myapp", target_dir=tmp_path)
        run_sh = tmp_path / "run.sh"
        assert run_sh.is_file(), "run.sh not written by scaffold"
        text = run_sh.read_text(encoding="utf-8")
        assert 'install -e ".[dev]"' in text
        assert "2>/dev/null" not in text
        assert "set -euo pipefail" in text
        # Non-dev fallback shape must not appear in any install line.
        for line in text.splitlines():
            if "install -e" in line:
                assert "||" not in line, line
