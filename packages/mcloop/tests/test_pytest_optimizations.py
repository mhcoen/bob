"""Tests for mcloop.pytest_optimizations."""

from __future__ import annotations

import tomllib
from pathlib import Path

from mcloop.pytest_optimizations import ensure_pytest_optimizations


def _parse(p: Path) -> dict:
    return tomllib.loads(p.read_text())


def test_no_pyproject_is_noop(tmp_path: Path) -> None:
    assert ensure_pytest_optimizations(tmp_path) is False
    assert not (tmp_path / "pyproject.toml").exists()


def test_pyproject_without_pytest_config_gets_config_added(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is True

    data = _parse(pp)
    ini = data["tool"]["pytest"]["ini_options"]
    assert "-n auto" in ini["addopts"]
    assert ini["timeout"] == 60

    dev = data["project"]["optional-dependencies"]["dev"]
    names = [d.split(">")[0].split("=")[0].strip() for d in dev]
    assert "pytest-xdist" in names
    assert "pytest-timeout" in names


def test_existing_pytest_config_missing_xdist_dep(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "\n"
        "[project.optional-dependencies]\n"
        "dev = [\n"
        '    "pytest>=8.0",\n'
        "]\n"
        "\n"
        "[tool.pytest.ini_options]\n"
        'addopts = "-n auto"\n'
        "timeout = 60\n"
    )

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is True

    data = _parse(pp)
    dev = data["project"]["optional-dependencies"]["dev"]
    names = [d.split(">")[0].split("=")[0].strip() for d in dev]
    assert "pytest-xdist" in names
    assert "pytest-timeout" in names
    # Existing entries preserved.
    assert any(d.startswith("pytest>") or d == "pytest" for d in dev)
    # Pytest section was already complete; untouched.
    ini = data["tool"]["pytest"]["ini_options"]
    assert ini["addopts"] == "-n auto"
    assert ini["timeout"] == 60


def test_fully_configured_is_noop(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    original = (
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "\n"
        "[project.optional-dependencies]\n"
        "dev = [\n"
        '    "pytest>=8.0",\n'
        '    "pytest-xdist>=3.5",\n'
        '    "pytest-timeout>=2.3",\n'
        "]\n"
        "\n"
        "[tool.pytest.ini_options]\n"
        'addopts = "-n auto"\n'
        "timeout = 60\n"
    )
    pp.write_text(original)

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is False
    assert pp.read_text() == original


def test_idempotent_second_call_is_noop(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')

    first = ensure_pytest_optimizations(tmp_path)
    after_first = pp.read_text()
    second = ensure_pytest_optimizations(tmp_path)
    after_second = pp.read_text()

    assert first is True
    assert second is False
    assert after_first == after_second


def test_pytest_config_missing_timeout_only(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        'dependencies = ["pytest-xdist>=3.5", "pytest-timeout>=2.3"]\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'addopts = "-n auto"\n'
    )

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is True

    data = _parse(pp)
    ini = data["tool"]["pytest"]["ini_options"]
    assert "-n auto" in ini["addopts"]
    assert ini["timeout"] == 60


def test_pytest_config_existing_addopts_gets_parallel_flag_appended(
    tmp_path: Path,
) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        'dependencies = ["pytest-xdist>=3.5", "pytest-timeout>=2.3"]\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'addopts = "--strict-markers"\n'
        "timeout = 30\n"
    )

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is True

    data = _parse(pp)
    ini = data["tool"]["pytest"]["ini_options"]
    assert "-n auto" in ini["addopts"]
    assert "--strict-markers" in ini["addopts"]
    # Existing timeout not clobbered.
    assert ini["timeout"] == 30


def test_deps_added_to_existing_main_dependencies_when_no_dev_group(
    tmp_path: Path,
) -> None:
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'addopts = "-n auto"\n'
        "timeout = 60\n"
    )

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is True

    data = _parse(pp)
    dev = data["project"]["optional-dependencies"]["dev"]
    names = [d.split(">")[0].split("=")[0].strip() for d in dev]
    assert "pytest-xdist" in names
    assert "pytest-timeout" in names


def test_malformed_toml_is_noop(tmp_path: Path) -> None:
    pp = tmp_path / "pyproject.toml"
    original = "this is not = valid toml ["
    pp.write_text(original)

    changed = ensure_pytest_optimizations(tmp_path)
    assert changed is False
    assert pp.read_text() == original
