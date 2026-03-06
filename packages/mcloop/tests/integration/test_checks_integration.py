"""Integration tests for run_checks: exercises real subprocesses."""

import json
import shutil
import sys
import textwrap

import pytest

from mcloop.checks import run_checks


def _ruff_path():
    return shutil.which("ruff")


def _pytest_path():
    return shutil.which("pytest") or f"{sys.executable} -m pytest"


@pytest.mark.integration
def test_run_checks_passes_on_clean_project(tmp_path):
    """run_checks returns passed=True for a project with ruff-clean, passing pytest."""
    ruff = _ruff_path()
    pt = _pytest_path()
    if not ruff:
        pytest.skip("ruff not found on PATH")

    checks = [f"{ruff} check ."]
    checks.append(f"{pt}")
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": checks}))

    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
        [tool.ruff]
        target-version = "py311"

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """)
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    result = run_checks(tmp_path)

    assert result.passed, f"Expected checks to pass, got:\n{result.output}"


@pytest.mark.integration
def test_run_checks_fails_on_ruff_error(tmp_path):
    """run_checks returns passed=False when ruff finds a lint error."""
    ruff = _ruff_path()
    if not ruff:
        pytest.skip("ruff not found on PATH")

    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": [f"{ruff} check ."]}))
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
        [tool.ruff]
        target-version = "py311"

        [tool.ruff.lint]
        select = ["F"]
        """)
    )
    # F401: unused import
    (tmp_path / "bad.py").write_text("import os\n")

    result = run_checks(tmp_path)

    assert not result.passed
    assert "bad.py" in result.output or "F401" in result.output


@pytest.mark.integration
def test_run_checks_fails_on_pytest_failure(tmp_path):
    """run_checks returns passed=False when a pytest test fails."""
    pt = _pytest_path()
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": [pt]}))
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """)
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_bad.py").write_text("def test_fail():\n    assert False\n")

    result = run_checks(tmp_path)

    assert not result.passed
    assert "test_fail" in result.output or "failed" in result.output.lower()


@pytest.mark.integration
def test_run_checks_uses_mcloop_json_commands(tmp_path):
    """run_checks uses commands from mcloop.json when present."""
    (tmp_path / "mcloop.json").write_text(
        json.dumps({"checks": [f"{sys.executable} -c \"print('custom check ran')\""]})
    )

    result = run_checks(tmp_path)

    assert result.passed
    assert "custom check ran" in result.output


@pytest.mark.integration
def test_run_checks_no_config_no_commands(tmp_path):
    """run_checks returns passed=True with a no-check message for an empty project."""
    result = run_checks(tmp_path)

    assert result.passed
    assert "No check commands detected" in result.output


@pytest.mark.integration
def test_run_checks_stops_at_first_failure(tmp_path):
    """run_checks stops after the first failing command and doesn't run subsequent ones."""
    marker = tmp_path / "second_ran.txt"
    (tmp_path / "mcloop.json").write_text(
        json.dumps(
            {
                "checks": [
                    f'{sys.executable} -c "raise SystemExit(1)"',
                    f"""{sys.executable} -c "open('{marker}', 'w').close()" """,
                ]
            }
        )
    )

    result = run_checks(tmp_path)

    assert not result.passed
    assert not marker.exists(), "Second command should not have run after first failed"
