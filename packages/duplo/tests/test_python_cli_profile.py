"""Tests for the macOS Python CLI platform profile scaffold."""

from __future__ import annotations

import tomllib
from pathlib import Path

import duplo.platforms.macos.python_cli as python_cli
from duplo.canonical_consistency import (
    SpecPyprojectInconsistencyError,
    validate_spec_pyproject_runsh_consistency,
)
from duplo.platforms.scaffold import (
    project_name_to_package_name,
    write_scaffold,
)


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


# ---------------------------------------------------------------------
# Hyphen->underscore identifier conversion
# ---------------------------------------------------------------------


class TestProjectNameToPackageName:
    def test_no_change_for_underscore_name(self):
        assert project_name_to_package_name("fswatch_run") == "fswatch_run"

    def test_no_change_for_lowercase_word(self):
        assert project_name_to_package_name("myapp") == "myapp"

    def test_single_hyphen_becomes_underscore(self):
        assert project_name_to_package_name("fswatch-run") == "fswatch_run"

    def test_multi_hyphen_becomes_underscores(self):
        assert (
            project_name_to_package_name("fswatch-run-smoke")
            == "fswatch_run_smoke"
        )

    def test_three_hyphens_my_cool_cli(self):
        assert project_name_to_package_name("my-cool-cli") == "my_cool_cli"


# ---------------------------------------------------------------------
# Hyphenated project name produces consistent scaffold
# ---------------------------------------------------------------------


class TestHyphenatedProjectNameScaffold:
    """A project named with hyphens (legal PyPI distribution name)
    must produce a scaffold that uses the underscore-form identifier
    everywhere a Python module reference is required: package
    directory, [project.scripts] value module path,
    [tool.setuptools.packages.find].include, and run.sh's
    `python -m` invocation. The hyphenated name itself stays in
    [project].name and as the [project.scripts] key (both legal
    with hyphens).
    """

    PROJECT_NAME = "fswatch-run-smoke"
    PACKAGE_NAME = "fswatch_run_smoke"

    def test_pyproject_distribution_name_keeps_hyphens(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        assert data["project"]["name"] == self.PROJECT_NAME

    def test_pyproject_scripts_key_keeps_hyphens(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        assert self.PROJECT_NAME in data["project"]["scripts"]

    def test_pyproject_scripts_value_uses_underscore_module(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        value = data["project"]["scripts"][self.PROJECT_NAME]
        assert value == f"{self.PACKAGE_NAME}.__main__:main"
        assert "-" not in value.split(":")[0], (
            "script value's module path must not contain hyphens"
        )

    def test_packages_find_include_uses_underscore_pattern(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        include = data["tool"]["setuptools"]["packages"]["find"]["include"]
        assert include == [f"{self.PACKAGE_NAME}*"]

    def test_run_sh_python_m_uses_underscore_module(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        text = (tmp_path / "run.sh").read_text()
        assert f"-m {self.PACKAGE_NAME}" in text
        assert f"-m {self.PROJECT_NAME}" not in text

    def test_package_directory_uses_underscore_name(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        pkg_dir = tmp_path / self.PACKAGE_NAME
        hyphen_dir = tmp_path / self.PROJECT_NAME
        assert pkg_dir.is_dir()
        assert not hyphen_dir.exists(), (
            "scaffold must NOT create a hyphenated package directory"
        )
        assert (pkg_dir / "__init__.py").is_file()
        assert (pkg_dir / "__main__.py").is_file()

    def test_package_main_py_runnable(self, tmp_path: Path):
        """The scaffolded __main__.py must be syntactically valid
        Python, define a `main` callable, and tolerate --help."""
        import ast
        import sys

        write_scaffold(
            [_get_profile()], self.PROJECT_NAME, target_dir=tmp_path
        )
        main_py = tmp_path / self.PACKAGE_NAME / "__main__.py"
        source = main_py.read_text()
        tree = ast.parse(source)  # raises SyntaxError on bad code
        assert any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in ast.walk(tree)
        ), "__main__.py must define a `main` function"

        # Smoke-execute via subprocess to confirm --help works.
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", self.PACKAGE_NAME, "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"python -m {self.PACKAGE_NAME} --help should exit 0; "
            f"got {result.returncode}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

    def test_underscore_only_name_passes_through_unchanged(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], "fswatch_run", target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        assert data["project"]["name"] == "fswatch_run"
        assert data["project"]["scripts"]["fswatch_run"] == (
            "fswatch_run.__main__:main"
        )
        assert (
            data["tool"]["setuptools"]["packages"]["find"]["include"]
            == ["fswatch_run*"]
        )
        assert (tmp_path / "fswatch_run").is_dir()


# ---------------------------------------------------------------------
# Scaffold passes BC1 + BC2 validators
# ---------------------------------------------------------------------


class TestScaffoldPassesValidators:
    """Regression: scaffolds produced by the new generator must pass
    the canonical-consistency validator (BC2) and contain only
    Python-identifier-valid package names (BC1's check). These
    tests fail by construction if the scaffold ever reverts to
    propagating hyphens into module references.
    """

    def test_hyphenated_name_scaffold_passes_bc2_consistency(self, tmp_path: Path):
        write_scaffold(
            [_get_profile()], "fswatch-run-smoke", target_dir=tmp_path
        )
        # No SPEC.md present; BC2 best-effort skips that source and
        # only validates pyproject<->run.sh consistency, which is
        # what the scaffold is responsible for.
        validate_spec_pyproject_runsh_consistency(tmp_path)

    def test_underscore_name_scaffold_passes_bc2_consistency(self, tmp_path: Path):
        write_scaffold([_get_profile()], "fswatch_run", target_dir=tmp_path)
        validate_spec_pyproject_runsh_consistency(tmp_path)

    def test_scaffold_with_matching_spec_passes_bc2(self, tmp_path: Path):
        """End-to-end: scaffold + SPEC.md mentioning the script
        name should pass BC2 in full."""
        write_scaffold(
            [_get_profile()], "fswatch-run-smoke", target_dir=tmp_path
        )
        (tmp_path / "SPEC.md").write_text(
            "# SPEC\n\n## Done definition\n\n"
            "- `pip install -e .` installs `fswatch-run-smoke` "
            "as an entry point.\n"
            "- `fswatch-run-smoke --help` prints usage.\n"
        )
        validate_spec_pyproject_runsh_consistency(tmp_path)

    def test_scaffold_with_mismatched_spec_raises_bc2(self, tmp_path: Path):
        """Sanity check that the validator still fires when SPEC
        deliberately disagrees with the scaffold output."""
        import pytest

        write_scaffold(
            [_get_profile()], "fswatch-run-smoke", target_dir=tmp_path
        )
        (tmp_path / "SPEC.md").write_text(
            "# SPEC\n\n## Done definition\n\n"
            "- `pip install -e .` installs `different-name` "
            "as an entry point.\n"
        )
        with pytest.raises(SpecPyprojectInconsistencyError):
            validate_spec_pyproject_runsh_consistency(tmp_path)

    def test_scaffold_pyproject_module_paths_are_python_identifiers(
        self, tmp_path: Path
    ):
        """BC1 regression: every Python package name in the
        scaffolded pyproject MUST be a valid Python identifier."""
        write_scaffold(
            [_get_profile()], "fswatch-run-smoke", target_dir=tmp_path
        )
        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        # script-value module path
        for value in data["project"]["scripts"].values():
            module_path = value.split(":", 1)[0]
            for segment in module_path.split("."):
                assert segment.isidentifier(), (
                    f"script module segment {segment!r} is not a "
                    "valid Python identifier"
                )
        # packages.find.include patterns (sans trailing *)
        for pat in data["tool"]["setuptools"]["packages"]["find"]["include"]:
            stripped = pat.rstrip("*")
            assert stripped.isidentifier(), (
                f"packages.find.include segment {stripped!r} is not "
                "a valid Python identifier"
            )
