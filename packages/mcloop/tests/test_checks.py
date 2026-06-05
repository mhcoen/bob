"""Tests for loop.checks."""

import json
import subprocess
from unittest.mock import patch

from mcloop.checks import (
    _classify_run_command,
    _detect_commands,
    _load_config,
    _normalize_pytest,
    detect_app_type,
    get_check_commands,
    run_autofix,
    run_checks,
)


def test_detect_commands_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" in cmds


def test_detect_commands_pyproject_ruff_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" not in cmds


def test_detect_commands_mypy_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = false\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds


def test_detect_commands_mypy_from_mypy_ini(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = False\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds


def test_detect_commands_mypy_absent(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." not in cmds


def test_detect_commands_mypy_with_ruff_and_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\n[tool.pytest.ini_options]\n[tool.mypy]\nstrict = false\n"
    )
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "pytest" in cmds
    assert "mypy ." in cmds


def test_detect_commands_mypy_ini_without_pyproject(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\n")
    cmds = _detect_commands(tmp_path, {})
    assert "mypy ." in cmds
    # No pyproject.toml means no ruff/pytest detection
    assert "ruff check ." not in cmds
    assert "pytest" not in cmds


def test_detect_commands_mypy_not_double_added(tmp_path):
    """Both [tool.mypy] and mypy.ini present should still only add one 'mypy .'."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
    (tmp_path / "mypy.ini").write_text("[mypy]\n")
    cmds = _detect_commands(tmp_path, {})
    assert cmds.count("mypy .") == 1


def test_detect_commands_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    cmds = _detect_commands(tmp_path, {})
    assert "npm test" in cmds


def test_detect_commands_package_json_no_test(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}')
    cmds = _detect_commands(tmp_path, {})
    assert "npm test" not in cmds


def test_detect_commands_makefile(tmp_path):
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path, {})
    assert "make check" in cmds


def test_detect_commands_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift package\n")
    cmds = _detect_commands(tmp_path, {})
    assert "swift build --disable-sandbox" in cmds


def test_detect_commands_empty(tmp_path):
    cmds = _detect_commands(tmp_path, {})
    assert cmds == []


def test_detect_commands_multiple(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    (tmp_path / "Makefile").write_text("check:\n\techo ok\n")
    cmds = _detect_commands(tmp_path, {})
    assert "ruff check ." in cmds
    assert "make check" in cmds


def test_load_config_no_file(tmp_path):
    assert _load_config(tmp_path) == {}


def test_load_config_with_checks(tmp_path):
    data = {"checks": ["ruff check .", "pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert _load_config(tmp_path) == data


def test_load_config_no_checks_key(tmp_path):
    data = {"other": "value"}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert _load_config(tmp_path) == data


def test_load_config_invalid_json(tmp_path):
    (tmp_path / "mcloop.json").write_text("not json")
    assert _load_config(tmp_path) == {}


def test_get_check_commands_explicit(tmp_path):
    data = {"checks": ["ruff check .", "pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert get_check_commands(tmp_path) == [
        "ruff check .",
        "pytest",
    ]


def test_get_check_commands_fallback(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    cmds = get_check_commands(tmp_path)
    assert "ruff check ." in cmds


def test_get_check_commands_checks_not_list(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": "not a list"}))
    # Falls back to detect since checks is not a list
    assert get_check_commands(tmp_path) == []


# --- _normalize_pytest tests ---


def test_normalize_pytest_python_m_pytest():
    assert _normalize_pytest("python -m pytest") == "pytest"


def test_normalize_pytest_python3_m_pytest():
    assert _normalize_pytest("python3 -m pytest") == "pytest"


def test_normalize_pytest_python_m_pytest_with_args():
    assert _normalize_pytest("python -m pytest tests/ -x -q") == "pytest tests/ -x -q"


def test_normalize_pytest_venv_bin():
    assert _normalize_pytest(".venv/bin/pytest") == "pytest"


def test_normalize_pytest_venv_bin_with_args():
    assert _normalize_pytest(".venv/bin/pytest tests/ -v") == "pytest tests/ -v"


def test_normalize_pytest_already_normalized():
    assert _normalize_pytest("pytest") == "pytest"


def test_normalize_pytest_non_pytest():
    assert _normalize_pytest("ruff check .") == "ruff check ."


def test_get_check_commands_normalizes_python_m_pytest(tmp_path):
    data = {"checks": ["ruff check .", "python -m pytest"]}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    assert get_check_commands(tmp_path) == ["ruff check .", "pytest"]


@patch("mcloop.checks.subprocess.run")
def test_run_checks_falls_back_when_check_timeout_is_non_integer(mock_run, tmp_path):
    """A non-integer check_timeout in mcloop.json must not crash run_checks.
    The default timeout (300s) is used instead.
    """
    data = {"checks": ["echo ok"], "check_timeout": "not a number"}
    (tmp_path / "mcloop.json").write_text(json.dumps(data))
    mock_run.return_value = subprocess.CompletedProcess(
        args="", returncode=0, stdout="ok\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed is True
    # Confirm the default timeout reached subprocess.run.
    assert mock_run.call_args.kwargs["timeout"] == 300


@patch("mcloop.checks.subprocess.run")
def test_run_checks_falls_back_to_autodetect_when_no_config(mock_run, tmp_path):
    # No mcloop.json present; pyproject.toml should trigger auto-detection
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=0, stdout="All good\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed
    # run_checks is now side-effect-free: ruff check + ruff format --check, no autofix
    assert mock_run.call_count == 2
    # Both commands run when all pass; assert as a set (order asserted
    # separately by test_run_checks_runs_commands_in_order_until_failure).
    calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
    assert ("ruff", "check", ".") in calls
    assert ("ruff", "format", "--check", ".") in calls


@patch("mcloop.checks.subprocess.run")
def test_run_checks_uses_config_commands(mock_run, tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["echo hello"]}))
    # Also add a pyproject.toml to ensure auto-detect is NOT used
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="echo hello", returncode=0, stdout="hello\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed
    assert mock_run.call_count == 1
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd == ["echo", "hello"]


def test_run_checks_config_overrides_autodetect(tmp_path):
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["true"]}))
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    result = run_checks(tmp_path)
    assert result.passed


def test_run_checks_resolves_bare_config_command_to_project_venv(tmp_path, monkeypatch):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    pytest_path = venv_bin / "pytest"
    pytest_path.write_text("#!/bin/sh\nprintf 'venv pytest\\n1 passed in 0.01s\\n'\n")
    pytest_path.chmod(0o755)
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["pytest -q"]}))
    monkeypatch.setenv("PATH", "/nonexistent")

    result = run_checks(tmp_path)

    assert result.passed
    assert "venv pytest" in result.output


def test_run_checks_no_commands(tmp_path):
    result = run_checks(tmp_path)
    assert result.passed
    assert result.command == "(none)"


@patch("mcloop.checks.subprocess.run")
def test_run_checks_all_pass(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=0, stdout="All good\n", stderr=""
    )
    result = run_checks(tmp_path)
    assert result.passed


@patch("mcloop.checks.subprocess.run")
def test_run_checks_first_fails(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="ruff check .", returncode=1, stdout="", stderr="E501 line too long\n"
    )
    result = run_checks(tmp_path)
    assert not result.passed
    assert result.command == "ruff check ."
    assert "E501" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_timeout(mock_run, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="ruff check .", timeout=300)
    result = run_checks(tmp_path)
    assert not result.passed
    assert "TIMEOUT" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_second_command_fails(mock_run, tmp_path):
    """An earlier failing command short-circuits later ones.

    Configured order is: ruff check . -> ruff format --check . -> pytest.
    The first command fails; run_checks must not invoke the later
    commands at all (sequential fail-fast acceptance-gate contract).
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")

    invoked: list[tuple[str, ...]] = []

    def record(args, **kwargs):
        invoked.append(tuple(args))
        # First command (ruff check .) fails.
        if tuple(args[:2]) == ("ruff", "check"):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="E501\n")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    mock_run.side_effect = record
    result = run_checks(tmp_path)

    assert not result.passed
    assert result.command == "ruff check ."
    # Short-circuit means only the first command was ever invoked.
    assert invoked == [("ruff", "check", ".")]
    assert not any(a[:1] == ("pytest",) for a in invoked)


# --- _classify_run_command tests ---


def test_run_checks_runs_commands_in_order_until_failure(tmp_path):
    """Commands run in listed order; a failure stops the rest.

    run_checks is an acceptance gate: once a command fails, no later
    command may start (no later side effects). This replaces an older
    test that asserted concurrent launch -- an optimization stated as
    a semantic requirement, incompatible with fail-fast side-effect
    isolation. The contract under test here is order + short-circuit,
    not the execution strategy.
    """
    (tmp_path / "mcloop.json").write_text(
        json.dumps({"checks": ["cmd-one", "cmd-two", "cmd-three"]})
    )

    # All commands pass, so every command runs in listed order.
    order: list[str] = []

    def all_pass(args, **kwargs):
        order.append(args[0])
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    with patch("mcloop.checks.subprocess.run", side_effect=all_pass):
        result = run_checks(tmp_path)
    assert result.passed
    assert order == ["cmd-one", "cmd-two", "cmd-three"]

    # When cmd-two fails, cmd-one and cmd-two run and cmd-three never starts.
    order.clear()

    def fail_second(args, **kwargs):
        order.append(args[0])
        rc = 1 if args[0] == "cmd-two" else 0
        return subprocess.CompletedProcess(args=args, returncode=rc, stdout="", stderr="")

    with patch("mcloop.checks.subprocess.run", side_effect=fail_second):
        result = run_checks(tmp_path)
    assert not result.passed
    assert result.command == "cmd-two"
    assert order == ["cmd-one", "cmd-two"]
    assert "cmd-three" not in order


# --- pytest signal predicate wired into run_checks ---


@patch("mcloop.checks.subprocess.run")
def test_run_checks_fails_exit0_all_skipped_pytest(mock_run, tmp_path):
    """An exit-0 pytest run where every test was skipped is no signal."""
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["pytest"]}))
    mock_run.return_value = subprocess.CompletedProcess(
        args="pytest",
        returncode=0,
        stdout="collected 3 items\n\n=== 3 skipped in 0.12s ===\n",
        stderr="",
    )
    result = run_checks(tmp_path)
    assert not result.passed
    assert "all skipped" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_fails_closed_on_unparseable_pytest(mock_run, tmp_path):
    """An exit-0 pytest run with an unparseable summary fails closed."""
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["pytest"]}))
    mock_run.return_value = subprocess.CompletedProcess(
        args="pytest",
        returncode=0,
        stdout="some unrelated output with no parseable summary\n",
        stderr="",
    )
    result = run_checks(tmp_path)
    assert not result.passed
    assert "pytest summary unparseable" in result.output


@patch("mcloop.checks.subprocess.run")
def test_run_checks_passes_normal_pytest(mock_run, tmp_path):
    """A normal pytest run with real passes is valid signal and passes."""
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["pytest"]}))
    mock_run.return_value = subprocess.CompletedProcess(
        args="pytest",
        returncode=0,
        stdout="collected 5 items\n\n=== 5 passed in 1.23s ===\n",
        stderr="",
    )
    result = run_checks(tmp_path)
    assert result.passed


@patch("mcloop.checks.subprocess.run")
def test_run_checks_non_pytest_command_unaffected_by_predicate(mock_run, tmp_path):
    """The signal predicate only applies to pytest commands.

    A non-test command (here ``echo``) that exits 0 but prints no
    pytest summary must still pass -- it is judged by exit code alone.
    """
    (tmp_path / "mcloop.json").write_text(json.dumps({"checks": ["echo hi"]}))
    mock_run.return_value = subprocess.CompletedProcess(
        args="echo hi",
        returncode=0,
        stdout="hi\n",
        stderr="",
    )
    result = run_checks(tmp_path)
    assert result.passed


# --- behavioral gate: unaccounted unmapped changes (T-000389) ---


def _gate_project(root, rel_path, new_content):
    """Set up a project whose only changed input is *rel_path*.

    A real ``.git`` is created so ``read_file_at_head`` reports the repo
    as present; the gate's ``git show`` baseline read and the check
    commands are both supplied through the test's ``subprocess.run``
    side_effect (the mock patches the shared subprocess module, so even
    git_ops's reads are intercepted).
    """
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    (root / "tests").mkdir()
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content)
    return target


def _make_side_effect(baseline):
    """Return a subprocess.run stand-in: git show -> *baseline*, else pass."""

    def side_effect(args, **kwargs):
        if list(args[:2]) == ["git", "show"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=baseline, stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    return side_effect


def test_run_checks_unmapped_behavioral_change_fails_gate(tmp_path):
    """An unmapped Python change the classifier proves behavioral (a changed
    value vs the HEAD baseline) fails the gate without running any check
    command."""
    _gate_project(tmp_path, "pkg/zzwidget.py", "x = 2\n")  # baseline was x = 1

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.side_effect = _make_side_effect("x = 1\n")
        result = run_checks(tmp_path, changed_files=["pkg/zzwidget.py"])

    assert not result.passed
    assert "pkg/zzwidget.py" in result.output
    # Only the baseline read happened; no check command was launched.
    check_calls = [
        tuple(c[0][0]) for c in mock_run.call_args_list if tuple(c[0][0][:2]) != ("git", "show")
    ]
    assert check_calls == []


def test_run_checks_unmapped_non_behavioral_change_passes(tmp_path):
    """An unmapped Python change the classifier proves inert (comment-only)
    does NOT fail the gate. With no mapped test, pytest is skipped while the
    scoped linter still runs over the changed file."""
    _gate_project(tmp_path, "pkg/zzwidget.py", "x = 1  # explanatory comment only\n")

    with patch("mcloop.checks.subprocess.run") as mock_run:
        mock_run.side_effect = _make_side_effect("x = 1\n")
        result = run_checks(tmp_path, changed_files=["pkg/zzwidget.py"])

    assert result.passed
    calls = {tuple(c[0][0]) for c in mock_run.call_args_list}
    # Linter scoped to the changed file; no pytest because nothing maps and
    # the change is provably inert.
    assert ("ruff", "check", "pkg/zzwidget.py") in calls
    assert ("ruff", "format", "--check", "pkg/zzwidget.py") in calls
    assert not any(c and c[0] == "pytest" for c in calls)


# --- coverage-proven verification fallback + waivers (T-000391) ---


def test_run_checks_logic_bearing_non_python_cannot_pass_via_coverage(tmp_path):
    """A flagged logic-bearing non-Python input (a template) has no
    executable coverage lines and is not the no-test-needed class, so the
    coverage fallback can never clear it -- it must fail the gate even with
    a task baseline present."""
    _gate_project(tmp_path, "templates/email.j2", "Hello {{ name }} now\n")
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")

    result = run_checks(tmp_path, changed_files=["templates/email.j2"])

    assert not result.passed
    assert "templates/email.j2" in result.output


def test_run_checks_non_code_input_passes_without_test_or_waiver(tmp_path):
    """A non-code input (dependency manifest, tool config, lock, or plain
    data file) carries no executable logic, so the gate clears it with no
    mapped test and no waiver. This generalizes beyond pyproject.toml."""
    for i, rel in enumerate(("ruff.toml", "config.yaml", "requirements.txt", "uv.lock")):
        root = tmp_path / f"proj{i}"
        root.mkdir()
        _gate_project(root, rel, "changed contents\n")
        (root / ".mcloop").mkdir(exist_ok=True)
        (root / ".mcloop" / "task-baseline").write_text("base-sha\n")

        result = run_checks(root, changed_files=[rel])

        assert result.passed, rel


def test_run_checks_waiver_clears_flagged_input(tmp_path):
    """An explicit, logged waiver for the changed input at the task's
    baseline clears the gate for an otherwise-blocking change."""
    from mcloop.waivers import record_waiver

    _gate_project(tmp_path, "templates/email.j2", "Hello {{ name }} now\n")
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")
    record_waiver(
        tmp_path,
        task_label="T-000391",
        changed_input="templates/email.j2",
        baseline_sha="base-sha",
        reason="template reviewed by hand",
    )

    result = run_checks(tmp_path, changed_files=["templates/email.j2"])

    # No mapped tests and no Python files: with the flagged input waived,
    # there is nothing left to run and the gate does not block.
    assert result.passed


def test_run_checks_coverage_proven_python_change_passes(tmp_path):
    """An unmapped behavioral Python change whose lines are proven executed
    by a scoped dependent test passes the gate via coverage."""
    from mcloop.coverage_verify import CoverageVerdict

    _gate_project(tmp_path, "pkg/zzwidget.py", "x = 2\n")  # baseline x = 1
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")

    proven = CoverageVerdict(True, "changed lines executed", ("tests/test_engine.py",))
    with (
        patch("mcloop.checks.subprocess.run", side_effect=_make_side_effect("x = 1\n")),
        patch("mcloop.coverage_verify.verify_change_covered", return_value=proven),
    ):
        result = run_checks(tmp_path, changed_files=["pkg/zzwidget.py"])

    assert result.passed


def test_run_checks_coverage_unproven_python_change_fails(tmp_path):
    """When coverage cannot prove the change is exercised and no waiver
    exists, the gate fails closed."""
    from mcloop.coverage_verify import CoverageVerdict

    _gate_project(tmp_path, "pkg/zzwidget.py", "x = 2\n")
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")

    unproven = CoverageVerdict(False, "changed lines were not executed", ())
    with (
        patch("mcloop.checks.subprocess.run", side_effect=_make_side_effect("x = 1\n")),
        patch("mcloop.coverage_verify.verify_change_covered", return_value=unproven),
    ):
        result = run_checks(tmp_path, changed_files=["pkg/zzwidget.py"])

    assert not result.passed
    assert "pkg/zzwidget.py" in result.output


# --- regression: non-code inputs exempt, executable code still gated (T-000002) ---
#
# These pin the two halves of the no-test-needed change class together so a
# future edit cannot relax one without the other catching it: a non-code
# input (manifest / tool config / data) clears the gate with NO mapped test
# and NO logged waiver, while a behavioral .py change with no exercising test
# still fails. The exemption must never leak onto executable source.


def test_regression_pyproject_dependency_edit_passes_unwaived(tmp_path):
    """A pyproject.toml dependency edit clears the gate with no mapped test
    and no waiver: a manifest carries no executable line to cover, so it must
    not be treated as a behavioral change and blocked."""
    _gate_project(
        tmp_path,
        "pyproject.toml",
        '[project]\ndependencies = ["requests>=2.32", "httpx"]\n'
        "[tool.ruff]\n[tool.pytest.ini_options]\n",
    )
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")

    result = run_checks(tmp_path, changed_files=["pyproject.toml"])

    assert result.passed


def test_regression_ruff_and_mypy_config_edits_pass(tmp_path):
    """Tool-configuration edits (ruff.toml, mypy.ini) are non-code inputs and
    clear the gate with no mapped test and no waiver."""
    for i, rel in enumerate(("ruff.toml", "mypy.ini")):
        root = tmp_path / f"cfg{i}"
        root.mkdir()
        _gate_project(root, rel, "line-length = 100\n")
        (root / ".mcloop").mkdir(exist_ok=True)
        (root / ".mcloop" / "task-baseline").write_text("base-sha\n")

        result = run_checks(root, changed_files=[rel])

        assert result.passed, rel


def test_regression_data_file_edit_passes(tmp_path):
    """A plain data-file edit (CSV) carries no executable logic and clears the
    gate with no mapped test and no waiver."""
    _gate_project(tmp_path, "data/cities.csv", "name,pop\nBoston,700000\n")
    (tmp_path / ".mcloop").mkdir(exist_ok=True)
    (tmp_path / ".mcloop" / "task-baseline").write_text("base-sha\n")

    result = run_checks(tmp_path, changed_files=["data/cities.csv"])

    assert result.passed


def test_regression_behavioral_py_without_test_still_gated(tmp_path):
    """The non-code exemption must NOT leak onto executable source. A
    behavioral .py change with no namesake test and no dependent test that
    exercises it still fails the gate -- proven end-to-end against a real git
    baseline, with no mocked classifier or coverage verdict."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "widget.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    (tmp_path / ".mcloop").mkdir()
    (tmp_path / ".mcloop" / "task-baseline").write_text(base + "\n")
    # Behavioral edit with no test_widget.py and no dependent test.
    (pkg / "widget.py").write_text("def f():\n    return 2\n")

    result = run_checks(tmp_path, changed_files=["pkg/widget.py"])

    assert not result.passed
    assert "pkg/widget.py" in result.output
    assert "no mapped test" in result.output


def test_phase_boundary_full_suite_has_no_coverage_instrumentation(tmp_path):
    """Coverage instrumentation lives only on the scoped per-task path.

    The phase-boundary full-suite call uses ``run_checks(project_dir)``
    (``changed_files=None``; see ``main.py`` ~1435). That branch must run
    the plain configured commands -- it must NEVER consult the
    coverage-proven verification fallback and must NEVER emit ``--cov``
    instrumentation. Coverage is a per-task fallback gated entirely behind
    the ``changed_files is not None`` branch; the full suite is never run
    under coverage. This pins the T-000392 placement invariant.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")

    invoked: list[tuple[str, ...]] = []

    def record(args, **kwargs):
        invoked.append(tuple(args))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="collected 5 items\n\n=== 5 passed in 1.23s ===\n",
            stderr="",
        )

    with (
        patch("mcloop.checks.subprocess.run", side_effect=record),
        patch("mcloop.coverage_verify.verify_change_covered") as mock_verify,
        patch("mcloop.coverage_verify._run_coverage") as mock_run_cov,
    ):
        result = run_checks(tmp_path)  # changed_files=None -> phase-boundary full suite

    assert result.passed
    # The coverage fallback is never consulted on the full-suite path.
    mock_verify.assert_not_called()
    mock_run_cov.assert_not_called()
    # No executed command carries coverage instrumentation.
    assert invoked  # the full suite did run real commands
    for cmd in invoked:
        assert not any(a.startswith("--cov") for a in cmd), cmd


@patch("mcloop.checks.subprocess.run")
def test_run_autofix_calls_ruff_fix_and_format(mock_run, tmp_path):
    """run_autofix runs both ruff check --fix and ruff format."""
    ok = subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr="")
    mock_run.return_value = ok
    run_autofix(tmp_path)
    assert mock_run.call_count == 2
    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert cmds[0] == ["ruff", "check", "--fix", "."]
    assert cmds[1] == ["ruff", "format", "."]


def test_try_salvage_does_not_extend_noqa_like_comment(tmp_path):
    """A comment that contains "noqa" as a substring (e.g. "noqa-like
    workaround") must not be misclassified as an existing # noqa pragma
    and corrupted. The fresh pragma must be appended, not spliced into
    the existing comment.
    """
    from mcloop.checks import try_salvage_style_failures

    src = tmp_path / "mod.py"
    long_value = "a" * 70
    comment = "# This is a noqa-like workaround"
    line = f"x = '{long_value}'  {comment}\n"
    src.write_text(line)
    failure_output = "mod.py:1:100: E501 Line too long (130 > 100)\n"

    salvaged, patched = try_salvage_style_failures(tmp_path, failure_output)

    assert salvaged is True
    assert patched == ["mod.py"]
    new_line = src.read_text()
    # Original "noqa-like" must remain intact in the comment.
    assert "noqa-like workaround" in new_line
    # A fresh E501 noqa pragma must have been appended.
    assert "noqa: E501" in new_line


@patch("mcloop.checks.subprocess.run")
def test_run_autofix_handles_missing_ruff(mock_run, tmp_path):
    """run_autofix must not crash if ruff is not installed (FileNotFoundError)."""
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory: 'ruff'")
    # Should not raise.
    run_autofix(tmp_path)
    # Both autofix commands attempted.
    assert mock_run.call_count == 2


@patch("mcloop.checks.subprocess.run")
def test_run_checks_no_autofix_side_effects(mock_run, tmp_path):
    """run_checks does not invoke ruff --fix or ruff format."""
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n[tool.pytest.ini_options]\n")
    mock_run.return_value = subprocess.CompletedProcess(
        args="", returncode=0, stdout="ok\n", stderr=""
    )
    run_checks(tmp_path)
    # Only the gate commands, no autofix
    cmds = [call[0][0] for call in mock_run.call_args_list]
    for cmd in cmds:
        assert cmd != ["ruff", "check", "--fix", "."]
        assert cmd != ["ruff", "format", "."]


# --- _classify_run_command tests ---


def test_classify_open_app():
    assert _classify_run_command("open MyApp.app") == "gui"


def test_classify_open_app_with_path():
    assert _classify_run_command("open /Applications/MyApp.app") == "gui"


def test_classify_open_non_app():
    """open without .app is not GUI (e.g. open a URL)."""
    assert _classify_run_command("open http://localhost:3000") == "cli"


def test_classify_run_sh():
    assert _classify_run_command("./run.sh") == "gui"


def test_classify_launch_sh():
    assert _classify_run_command("./launch.sh") == "gui"


def test_classify_path_sh():
    assert _classify_run_command("/usr/local/bin/run.sh") == "gui"


def test_classify_bare_binary():
    assert _classify_run_command("./myapp") == "cli"


def test_classify_build_binary():
    assert _classify_run_command(".build/debug/MyApp") == "cli"


def test_classify_python_script():
    assert _classify_run_command("python main.py") == "cli"


def test_classify_python3_script():
    assert _classify_run_command("python3 app.py") == "cli"


def test_classify_cargo_run():
    assert _classify_run_command("cargo run") == "cli"


def test_classify_go_run():
    assert _classify_run_command("go run .") == "cli"


def test_classify_swift_run():
    assert _classify_run_command("swift run MyApp") == "cli"


def test_classify_npm_start():
    assert _classify_run_command("npm start") == "web"


def test_classify_npm_run_dev():
    assert _classify_run_command("npm run dev") == "web"


def test_classify_flask_run():
    assert _classify_run_command("flask run") == "web"


def test_classify_uvicorn():
    assert _classify_run_command("uvicorn app:main") == "web"


def test_classify_gunicorn():
    assert _classify_run_command("gunicorn app:app") == "web"


def test_classify_python_m_flask():
    assert _classify_run_command("python -m flask run") == "web"


def test_classify_python_m_http_server():
    assert _classify_run_command("python -m http.server") == "web"


def test_classify_python_m_uvicorn():
    assert _classify_run_command("python3 -m uvicorn app:main") == "web"


def test_classify_empty():
    assert _classify_run_command("") == "cli"


# --- detect_app_type integration tests ---


def test_detect_app_type_from_config(tmp_path):
    config = {"run": "open MyApp.app"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "gui"


def test_detect_app_type_web_from_config(tmp_path):
    config = {"run": "npm start"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "web"


def test_detect_app_type_cli_from_config(tmp_path):
    config = {"run": "cargo run"}
    (tmp_path / "mcloop.json").write_text(json.dumps(config))
    assert detect_app_type(tmp_path) == "cli"


def test_detect_app_type_no_run_command(tmp_path):
    assert detect_app_type(tmp_path) == "cli"


def test_detect_app_type_autodetected_npm(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node ."}}')
    assert detect_app_type(tmp_path) == "web"
