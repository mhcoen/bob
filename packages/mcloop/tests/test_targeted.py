"""Tests for mcloop.targeted — source-to-test file mapping."""

import subprocess
from unittest.mock import patch

from mcloop.targeted import (
    account_changed_inputs,
    is_test_command,
    map_to_tests,
    targeted_pytest_command,
)


def test_map_basic(tmp_path):
    """Source file maps to test file by naming convention."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    result = map_to_tests(["mcloop/checks.py"], tmp_path)
    assert result == ["tests/test_checks.py"]


def test_map_multiple(tmp_path):
    """Multiple source files map to their test files."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    (tmp_path / "tests" / "test_runner.py").write_text("")
    result = map_to_tests(
        ["mcloop/checks.py", "mcloop/runner.py"],
        tmp_path,
    )
    assert result == ["tests/test_checks.py", "tests/test_runner.py"]


def test_map_no_matching_test(tmp_path):
    """Source file with no corresponding test returns empty."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["mcloop/main.py"], tmp_path)
    assert result == []


def test_map_skips_non_python(tmp_path):
    """Non-Python files are ignored."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["README.md", "mcloop.json"], tmp_path)
    assert result == []


def test_map_includes_changed_test_files(tmp_path):
    """Changed test files are included directly in the targeted set."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    result = map_to_tests(["tests/test_foo.py"], tmp_path)
    assert result == ["tests/test_foo.py"]


def test_map_skips_nonexistent_test_files(tmp_path):
    """Changed test files that don't exist are not included."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["tests/test_foo.py"], tmp_path)
    assert result == []


def test_map_skips_dunder_files(tmp_path):
    """__init__.py and similar are skipped."""
    (tmp_path / "tests").mkdir()
    result = map_to_tests(["mcloop/__init__.py"], tmp_path)
    assert result == []


def test_map_deduplicates(tmp_path):
    """Same test file from different source paths appears once."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    result = map_to_tests(
        ["mcloop/checks.py", "src/checks.py"],
        tmp_path,
    )
    assert result == ["tests/test_checks.py"]


def test_map_no_tests_dir(tmp_path):
    """Missing tests/ directory returns empty."""
    result = map_to_tests(["mcloop/checks.py"], tmp_path)
    assert result == []


def test_map_finds_subdir_test(tmp_path):
    """A test living in a tests/ subdirectory is found, not just the flat
    tests/test_<name>.py convention."""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_widget.py").write_text("")
    result = map_to_tests(["mcloop/widget.py"], tmp_path)
    assert result == ["tests/unit/test_widget.py"]


def test_account_mixed_batch_reports_unmapped(tmp_path):
    """A mixed batch (one file with a test, one without) reports the
    unmapped file rather than dropping it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")
    accounts = account_changed_inputs(
        ["mcloop/checks.py", "mcloop/orphan.py"],
        tmp_path,
    )
    by_source = {a.source: a for a in accounts}
    assert set(by_source) == {"mcloop/checks.py", "mcloop/orphan.py"}

    mapped = by_source["mcloop/checks.py"]
    assert mapped.mapped
    assert mapped.test_files == ("tests/test_checks.py",)

    unmapped = by_source["mcloop/orphan.py"]
    assert unmapped.unmapped
    assert not unmapped.mapped
    assert unmapped.test_files == ()
    assert "orphan" in unmapped.reason


def test_account_finds_subdir_test(tmp_path):
    """Accounting locates a test in a tests/ subdirectory."""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_widget.py").write_text("")
    accounts = account_changed_inputs(["mcloop/widget.py"], tmp_path)
    assert len(accounts) == 1
    assert accounts[0].mapped
    assert accounts[0].test_files == ("tests/unit/test_widget.py",)


def test_account_non_python_behavior_input(tmp_path):
    """A non-.py behavior input (pyproject.toml) is accounted for, not
    silently dropped, even though it has no name-based test mapping."""
    (tmp_path / "tests").mkdir()
    accounts = account_changed_inputs(["pyproject.toml"], tmp_path)
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.source == "pyproject.toml"
    assert acc.unmapped
    assert acc.test_files == ()
    assert acc.reason


def test_account_omits_pure_docs(tmp_path):
    """Pure documentation changes are not accounted (cannot affect
    behavior) so they do not force a fallback test run."""
    (tmp_path / "tests").mkdir()
    accounts = account_changed_inputs(["README.md", "docs/guide.rst"], tmp_path)
    assert accounts == []


def test_account_module_name_k_matching(tmp_path):
    """When no test_<name>.py exists, a test that references the module
    name as a word is matched (pytest -k style)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_behaviors.py").write_text(
        "from mcloop import widget\n\n\ndef test_widget_runs():\n    assert widget\n",
    )
    accounts = account_changed_inputs(["mcloop/widget.py"], tmp_path)
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.mapped
    assert acc.test_files == ("tests/test_behaviors.py",)
    assert acc.k_module == "widget"


def test_account_test_support_file_under_tests(tmp_path):
    """A non-.py fixture/data file under tests/ maps to the test files in
    its directory rather than being dropped."""
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "test_uses_data.py").write_text("")
    (tmp_path / "tests" / "fixtures" / "sample.json").write_text("{}")
    accounts = account_changed_inputs(["tests/fixtures/sample.json"], tmp_path)
    assert len(accounts) == 1
    assert accounts[0].mapped
    assert accounts[0].test_files == ("tests/fixtures/test_uses_data.py",)


def test_targeted_pytest_command():
    cmd = targeted_pytest_command(["tests/test_checks.py"])
    assert cmd == "pytest tests/test_checks.py"


def test_targeted_pytest_command_multiple():
    cmd = targeted_pytest_command(
        ["tests/test_checks.py", "tests/test_runner.py"],
    )
    assert cmd == "pytest tests/test_checks.py tests/test_runner.py"


def test_is_test_command():
    assert is_test_command("pytest")
    assert is_test_command("pytest tests/test_foo.py")
    assert not is_test_command("ruff check .")
    assert not is_test_command("npm test")
    assert not is_test_command("make check")


def test_run_checks_with_targeted(tmp_path):
    """run_checks narrows pytest to targeted test files."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(
            tmp_path,
            changed_files=["mcloop/checks.py"],
        )
        # run_checks is side-effect-free: ruff is scoped to the changed
        # .py files, pytest is scoped to their mapped tests. Parallel
        # execution means order is non-deterministic.
        calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
        assert ("ruff", "check", "mcloop/checks.py") in calls
        assert ("ruff", "format", "--check", "mcloop/checks.py") in calls
        assert ("pytest", "tests/test_checks.py") in calls


def test_run_checks_targeted_only_test_files_changed(tmp_path):
    """When only test files changed, pytest runs those test files."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(
            tmp_path,
            changed_files=["tests/test_foo.py"],
        )
        # Test file included directly: ruff scoped to it, pytest scoped to it.
        # Parallel execution means order is non-deterministic.
        assert mock_run.call_count == 3
        calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
        assert ("ruff", "check", "tests/test_foo.py") in calls
        assert ("ruff", "format", "--check", "tests/test_foo.py") in calls
        assert ("pytest", "tests/test_foo.py") in calls


def test_run_checks_unmapped_behavioral_change_fails_gate(tmp_path):
    """A Python change with no mapped test that cannot be proven inert
    fails the gate rather than falling back to a (possibly vacuous) full
    pytest run. Here main.py is unmapped and not provably non-behavioral
    (no baseline, no file content), so the gate fails closed and no check
    command is ever launched."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()

    with patch("mcloop.checks.subprocess.run") as mock_run:
        result = run_checks(
            tmp_path,
            changed_files=["mcloop/main.py"],
        )
        # Fail closed before running any command.
        assert not result.passed
        assert "mcloop/main.py" in result.output
        assert mock_run.call_count == 0


def test_run_checks_mixed_batch_unmapped_behavioral_fails_gate(tmp_path):
    """A mixed batch where one file maps to a test and another is an
    unmapped behavioral change fails the gate: the unmapped change must
    not ship untested under a green targeted run."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checks.py").write_text("")

    with patch("mcloop.checks.subprocess.run") as mock_run:
        result = run_checks(
            tmp_path,
            changed_files=["mcloop/checks.py", "mcloop/orphan.py"],
        )
        # orphan.py is unmapped and not provably inert -> gate fails closed.
        assert not result.passed
        assert "mcloop/orphan.py" in result.output
        # No targeted or full pytest ran; the gate short-circuits.
        assert mock_run.call_count == 0


def test_run_checks_targeted_no_python_changes(tmp_path):
    """When only non-Python files change, pytest is correctly skipped."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / "tests").mkdir()

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(
            tmp_path,
            changed_files=["README.md", "docs/guide.md"],
        )
        # Only docs changed, no Python: ruff and pytest both skipped.
        assert mock_run.call_count == 0


def test_run_checks_no_changed_files_runs_full(tmp_path):
    """Without changed_files, full test suite runs."""
    from mcloop.checks import run_checks

    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args="",
            returncode=0,
            stdout="ok\n",
            stderr="",
        )
        run_checks(tmp_path)
        # Side-effect-free: ruff check + ruff format --check + pytest (full),
        # no autofix. Parallel execution means order is non-deterministic.
        calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
        assert ("ruff", "check", ".") in calls
        assert ("ruff", "format", "--check", ".") in calls
        assert ("pytest",) in calls
