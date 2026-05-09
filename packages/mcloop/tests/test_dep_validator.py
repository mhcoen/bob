"""Tests for mcloop.dep_validator."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mcloop.dep_validator import (
    MissingDependenciesError,
    _dep_name,
    _read_declared_dependencies,
    validate_project_dependencies,
)


def _make_venv(project_dir: Path) -> Path:
    """Create a real .venv under project_dir. Returns the venv python path."""
    venv_dir = project_dir / ".venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        check=True,
    )
    return venv_dir / "bin" / "python"


def _make_fake_venv(project_dir: Path) -> Path:
    """Create a fake .venv layout without invoking python -m venv.

    Used when the test does not need a working pip/python; it just
    needs the path-shape so ``_resolve_project_venv_python`` finds it.
    A pip stub script is dropped in alongside.
    """
    bin_dir = project_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    py = bin_dir / "python"
    py.write_text("#!/bin/sh\nexit 1\n")
    py.chmod(0o755)
    return py


def _drop_stub_pip(venv_python: Path, *, installed: set[str]) -> None:
    """Replace the venv's ``pip`` with a shell stub that knows a fixed
    install set.

    The stub returns 0 from ``pip show <name>`` when ``name`` is in
    ``installed`` and 1 otherwise. No network access, no real pip.
    """
    pip_path = venv_python.parent / "pip"
    listed = " ".join(sorted(installed))
    pip_path.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            # stub pip used by tests/test_dep_validator.py
            if [ "$1" = "show" ]; then
                for name in {listed}; do
                    if [ "$name" = "$2" ]; then
                        exit 0
                    fi
                done
                exit 1
            fi
            exit 2
            """
        )
    )
    pip_path.chmod(0o755)


# -- _dep_name ---------------------------------------------------------------


def test_dep_name_simple() -> None:
    assert _dep_name("pytest") == "pytest"


def test_dep_name_with_version() -> None:
    assert _dep_name("pytest>=8.0") == "pytest"


def test_dep_name_with_extras() -> None:
    assert _dep_name("requests[security]>=2") == "requests"


def test_dep_name_with_environment_marker() -> None:
    assert _dep_name('pytest>=8 ; python_version >= "3.11"') == "pytest"


def test_dep_name_empty() -> None:
    assert _dep_name("") == ""


# -- _read_declared_dependencies ---------------------------------------------


def test_read_returns_none_when_no_pyproject(tmp_path: Path) -> None:
    assert _read_declared_dependencies(tmp_path) is None


def test_read_returns_none_on_malformed_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("not a [valid toml file")
    assert _read_declared_dependencies(tmp_path) is None


def test_read_returns_empty_when_no_deps_declared(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
    )
    assert _read_declared_dependencies(tmp_path) == []


def test_read_collects_main_and_dev_deps(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"
            dependencies = ["watchdog>=3", "click"]

            [project.optional-dependencies]
            dev = ["pytest>=8", "pytest-xdist>=3", "ruff"]
            """
        )
    )
    out = _read_declared_dependencies(tmp_path)
    assert out == ["watchdog", "click", "pytest", "pytest-xdist", "ruff"]


def test_read_dedupes(tmp_path: Path) -> None:
    """A package listed in main and dev should appear once."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"
            dependencies = ["pytest>=8"]

            [project.optional-dependencies]
            dev = ["pytest>=8", "ruff"]
            """
        )
    )
    out = _read_declared_dependencies(tmp_path)
    assert out == ["pytest", "ruff"]


# -- validate_project_dependencies -------------------------------------------


def test_no_pyproject_is_noop(tmp_path: Path) -> None:
    validate_project_dependencies(tmp_path)


def test_no_venv_is_noop(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        'dependencies = ["pytest>=8"]\n'
    )
    validate_project_dependencies(tmp_path)


def test_no_declared_deps_is_noop(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
    )
    _make_fake_venv(tmp_path)
    validate_project_dependencies(tmp_path)


def test_passes_when_all_deps_installed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"
            dependencies = ["watchdog>=3"]

            [project.optional-dependencies]
            dev = ["pytest>=8", "pytest-xdist>=3"]
            """
        )
    )
    venv_python = _make_fake_venv(tmp_path)
    _drop_stub_pip(venv_python, installed={"watchdog", "pytest", "pytest-xdist"})
    validate_project_dependencies(tmp_path)


def test_raises_when_a_single_dep_is_missing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"

            [project.optional-dependencies]
            dev = ["pytest>=8", "pytest-xdist>=3"]
            """
        )
    )
    venv_python = _make_fake_venv(tmp_path)
    _drop_stub_pip(venv_python, installed={"pytest"})
    with pytest.raises(MissingDependenciesError) as ei:
        validate_project_dependencies(tmp_path)
    msg = str(ei.value)
    assert "pytest-xdist" in msg
    assert "pytest" not in msg.split(":", 1)[1].split(".")[0] or "pytest-xdist" in msg
    assert "install -e '.[dev]'" in msg


def test_raises_listing_every_missing_dep(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "demo"
            version = "0.1.0"

            [project.optional-dependencies]
            dev = ["pytest>=8", "pytest-xdist>=3", "pytest-timeout>=2", "ruff"]
            """
        )
    )
    venv_python = _make_fake_venv(tmp_path)
    _drop_stub_pip(venv_python, installed=set())
    with pytest.raises(MissingDependenciesError) as ei:
        validate_project_dependencies(tmp_path)
    msg = str(ei.value)
    for name in ("pytest", "pytest-xdist", "pytest-timeout", "ruff"):
        assert name in msg


def test_error_message_names_venv_dir(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n'
    )
    venv_python = _make_fake_venv(tmp_path)
    _drop_stub_pip(venv_python, installed=set())
    with pytest.raises(MissingDependenciesError) as ei:
        validate_project_dependencies(tmp_path)
    assert str(tmp_path / ".venv") in str(ei.value)


def test_real_venv_with_pip_show(tmp_path: Path) -> None:
    """End-to-end with a real venv (no stubs)."""
    if os.environ.get("MCLOOP_SKIP_REAL_VENV"):
        pytest.skip("MCLOOP_SKIP_REAL_VENV set")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\n'
    )
    venv_python = _make_venv(tmp_path)
    # No pip in this --without-pip venv. _pip_show_check should fall
    # back to ``python -m pip show`` and find no pip module either,
    # returning False -> validator raises.
    _ = venv_python
    with pytest.raises(MissingDependenciesError):
        validate_project_dependencies(tmp_path)
