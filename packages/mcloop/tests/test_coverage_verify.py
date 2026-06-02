"""Tests for mcloop.coverage_verify (coverage-proven verification)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from mcloop.coverage_verify import (
    CoverageVerdict,
    _module_dotted,
    _parse_coverage_json,
    _parse_diff_new_lines,
    changed_new_lines,
    dependent_test_files,
    verify_change_covered,
)

# --- pure parsers -----------------------------------------------------------


def test_parse_diff_new_lines_added_and_context() -> None:
    diff = (
        "diff --git a/pkg/widget.py b/pkg/widget.py\n"
        "--- a/pkg/widget.py\n"
        "+++ b/pkg/widget.py\n"
        "@@ -1,3 +1,5 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    x = 2\n"
        "+    return x\n"
        " # tail\n"
        " # tail2\n"
    )
    # New side: line 1 ' def f():' (context), 2 '+    x = 2', 3 '+    return x'.
    assert _parse_diff_new_lines(diff) == {2, 3}


def test_parse_diff_new_lines_multiple_hunks() -> None:
    diff = "@@ -1,1 +1,1 @@\n-old\n+new\n@@ -10,0 +11,2 @@\n+a\n+b\n"
    assert _parse_diff_new_lines(diff) == {1, 11, 12}


def test_parse_diff_new_lines_ignores_no_newline_marker() -> None:
    diff = "@@ -1,1 +1,1 @@\n+only\n\\ No newline at end of file\n"
    assert _parse_diff_new_lines(diff) == {1}


def test_module_dotted() -> None:
    assert _module_dotted("pkg/widget.py") == "pkg.widget"
    assert _module_dotted("pkg/__init__.py") == "pkg"
    assert _module_dotted("top.py") == "top"


def test_parse_coverage_json_matches_file(tmp_path: Path) -> None:
    payload = {
        "files": {
            "pkg/widget.py": {"executed_lines": [1, 2, 5]},
            "pkg/other.py": {"executed_lines": [9]},
        }
    }
    executed = _parse_coverage_json(json.dumps(payload), "pkg/widget.py", tmp_path)
    assert executed == {1, 2, 5}


def test_parse_coverage_json_no_match_returns_empty(tmp_path: Path) -> None:
    payload = {"files": {"pkg/other.py": {"executed_lines": [9]}}}
    assert _parse_coverage_json(json.dumps(payload), "pkg/widget.py", tmp_path) == set()


# --- changed_new_lines (real git) -------------------------------------------


def test_changed_new_lines_empty_baseline_is_none(tmp_path: Path) -> None:
    assert changed_new_lines(tmp_path, "", "pkg/widget.py") is None


def test_changed_new_lines_against_real_baseline(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    src = tmp_path / "mod.py"
    src.write_text("a = 1\nb = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    # Edit line 2.
    src.write_text("a = 1\nb = 3\n")

    changed = changed_new_lines(tmp_path, base, "mod.py")
    assert changed == {2}


# --- dependent_test_files (transitive import graph) -------------------------


def _make_dependent_project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "widget.py").write_text("def widget():\n    return 42\n")
    (pkg / "engine.py").write_text(
        "from pkg import widget\n\n\ndef run():\n    return widget.widget()\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_engine.py").write_text(
        "from pkg import engine\n\n\ndef test_run():\n    assert engine.run() == 42\n"
    )
    (tests / "test_unrelated.py").write_text("def test_nothing():\n    assert True\n")


def test_dependent_test_files_finds_transitive_importer(tmp_path: Path) -> None:
    _make_dependent_project(tmp_path)
    # widget.py has no test_widget.py and test_engine does not name "widget",
    # but it transitively imports it through engine.
    deps = dependent_test_files(tmp_path, "pkg/widget.py")
    assert deps == ["tests/test_engine.py"]


def test_dependent_test_files_unknown_module(tmp_path: Path) -> None:
    _make_dependent_project(tmp_path)
    assert dependent_test_files(tmp_path, "pkg/nonexistent.py") == []


# --- _run_coverage (subprocess mocked, JSON written) ------------------------


def _cov_run_writing(
    executed: list[int],
    src: str,
    returncode: int = 0,
    summary: str | None = None,
):
    """Return a subprocess.run stand-in that writes a coverage JSON and a
    pytest summary, mimicking a scoped coverage run."""
    out = summary if summary is not None else "collected 1 item\n\n1 passed in 0.10s\n"

    def fake_run(args, **kwargs):
        # Find the --cov-report=json:<path> argument and write the report.
        for a in args:
            if isinstance(a, str) and a.startswith("--cov-report=json:"):
                json_path = Path(a.split(":", 1)[1])
                json_path.write_text(json.dumps({"files": {src: {"executed_lines": executed}}}))
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=out, stderr="")

    return fake_run


def test_run_coverage_returns_executed_lines(tmp_path: Path) -> None:
    from mcloop.coverage_verify import _run_coverage

    fake = _cov_run_writing([1, 2, 3], "mod.py")
    with patch("mcloop.coverage_verify.subprocess.run", side_effect=fake):
        executed, reason = _run_coverage(tmp_path, ["tests/test_mod.py"], "mod.py", 60)
    assert executed == {1, 2, 3}
    assert reason == ""


def test_run_coverage_no_signal_returns_none(tmp_path: Path) -> None:
    from mcloop.coverage_verify import _run_coverage

    fake = _cov_run_writing([], "mod.py", summary="collected 0 items\n\nno tests ran in 0.01s\n")
    with patch("mcloop.coverage_verify.subprocess.run", side_effect=fake):
        executed, reason = _run_coverage(tmp_path, ["tests/test_mod.py"], "mod.py", 60)
    assert executed is None
    assert "no valid passing signal" in reason


def test_run_coverage_failing_tests_returns_none(tmp_path: Path) -> None:
    from mcloop.coverage_verify import _run_coverage

    fake = _cov_run_writing(
        [1], "mod.py", returncode=1, summary="collected 1 item\n\n1 failed in 0.1s\n"
    )
    with patch("mcloop.coverage_verify.subprocess.run", side_effect=fake):
        executed, reason = _run_coverage(tmp_path, ["tests/test_mod.py"], "mod.py", 60)
    assert executed is None


# --- verify_change_covered (orchestrator) -----------------------------------


def test_verify_passes_when_change_exercised(tmp_path: Path) -> None:
    deps = ["tests/test_engine.py"]
    with (
        patch("mcloop.coverage_verify.changed_new_lines", return_value={3, 4}),
        patch("mcloop.coverage_verify.dependent_test_files", return_value=deps),
        patch("mcloop.coverage_verify._run_coverage", return_value=({3, 9}, "")),
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/widget.py", [])
    assert isinstance(verdict, CoverageVerdict)
    assert verdict.proven is True
    assert verdict.candidate_nodes == ("tests/test_engine.py",)


def test_verify_fails_when_change_not_exercised(tmp_path: Path) -> None:
    deps = ["tests/test_engine.py"]
    with (
        patch("mcloop.coverage_verify.changed_new_lines", return_value={3, 4}),
        patch("mcloop.coverage_verify.dependent_test_files", return_value=deps),
        patch("mcloop.coverage_verify._run_coverage", return_value=({99}, "")),
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/widget.py", [])
    assert verdict.proven is False
    assert "not executed" in verdict.reason


def test_verify_fails_when_no_candidate_tests(tmp_path: Path) -> None:
    with (
        patch("mcloop.coverage_verify.changed_new_lines", return_value={3}),
        patch("mcloop.coverage_verify.dependent_test_files", return_value=[]),
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/widget.py", [])
    assert verdict.proven is False
    assert "no scoped candidate" in verdict.reason
    # The full suite is never run when there is no scoped candidate.
    run_cov.assert_not_called()


def test_verify_non_python_cannot_pass(tmp_path: Path) -> None:
    verdict = verify_change_covered(tmp_path, "base-sha", "config/data.yaml", [])
    assert verdict.proven is False
    assert "non-Python" in verdict.reason


def test_verify_missing_baseline_fails_closed(tmp_path: Path) -> None:
    # Empty baseline -> changed_new_lines returns None -> cannot prove.
    verdict = verify_change_covered(tmp_path, "", "pkg/widget.py", [])
    assert verdict.proven is False
    assert "could not resolve changed lines" in verdict.reason
