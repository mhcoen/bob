"""Integration tests for run_checks: exercises real subprocesses."""

import textwrap

import pytest

from mcloop.checks import run_checks


@pytest.mark.integration
def test_run_checks_passes_on_clean_project(tmp_path):
    """run_checks returns passed=True for a project with ruff-clean, passing pytest."""
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
    import json

    (tmp_path / "mcloop.json").write_text(
        json.dumps({"checks": ["python -c \"print('custom check ran')\""]})
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
    import json

    marker = tmp_path / "second_ran.txt"
    (tmp_path / "mcloop.json").write_text(
        json.dumps(
            {
                "checks": [
                    "python -c \"raise SystemExit(1)\"",
                    f"python -c \"open('{marker}', 'w').close()\"",
                ]
            }
        )
    )

    result = run_checks(tmp_path)

    assert not result.passed
    assert not marker.exists(), "Second command should not have run after first failed"
