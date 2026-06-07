"""Tests for mcloop.coverage_verify (coverage-proven verification)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.coverage_verify import (
    CoverageVerdict,
    _build_module_index,
    _iter_py_files,
    _module_dotted,
    _ModuleNameCollision,
    _parse_coverage_json,
    _parse_diff_new_lines,
    changed_new_lines,
    dependent_test_files,
    is_coverage_exempt_python,
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


# --- module/package dotted-name collision -----------------------------------


def _make_collision_project(root: Path) -> None:
    """A module/package twin: ``pkg/lint.py`` and ``pkg/lint/__init__.py``
    both resolve to the dotted name ``pkg.lint``."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lint.py").write_text("def check():\n    return 1\n")
    lint_pkg = pkg / "lint"
    lint_pkg.mkdir()
    (lint_pkg / "__init__.py").write_text("def run():\n    return 2\n")


def test_build_module_index_detects_collision(tmp_path: Path) -> None:
    """Two distinct files sharing a dotted name raise _ModuleNameCollision
    with both backing paths, sorted/stable -- not rglob-order dependent."""
    _make_collision_project(tmp_path)
    py_files = _iter_py_files(tmp_path)
    with pytest.raises(_ModuleNameCollision) as exc:
        _build_module_index(py_files, tmp_path)
    assert exc.value.module_name == "pkg.lint"
    assert len(exc.value.paths) == 2
    # Stable, deterministic ordering regardless of filesystem iteration order.
    assert exc.value.paths == sorted(exc.value.paths)
    assert any(p.endswith("pkg/lint.py") for p in exc.value.paths)
    assert any(p.endswith("pkg/lint/__init__.py") for p in exc.value.paths)


def test_build_module_index_no_collision_returns_map(tmp_path: Path) -> None:
    """A clean project (no dotted-name twins) builds a full module index."""
    _make_dependent_project(tmp_path)
    index = _build_module_index(_iter_py_files(tmp_path), tmp_path)
    assert index["pkg.widget"] == tmp_path / "pkg" / "widget.py"
    assert index["pkg.engine"] == tmp_path / "pkg" / "engine.py"


def test_verify_collision_blocks_with_reason_and_no_coverage(tmp_path: Path) -> None:
    """A module/package collision in the import graph fails the verdict with a
    distinguishing reason naming the dotted name and both files, and the raw
    exception never escapes -- no coverage run is attempted."""
    _make_collision_project(tmp_path)
    with (
        patch("mcloop.coverage_verify.changed_new_lines") as changed,
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        # Nonempty changed lines so we get past line resolution into the
        # candidate-test graph build where the collision is detected.
        changed.return_value = {1}
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/lint.py", [])
    assert verdict.proven is False
    assert "collision" in verdict.reason
    assert "pkg.lint" in verdict.reason
    assert "pkg/lint.py" in verdict.reason
    assert "pkg/lint/__init__.py" in verdict.reason
    assert verdict.candidate_nodes == ()
    # The collision short-circuits before any coverage subprocess.
    run_cov.assert_not_called()


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


def test_run_coverage_emits_cov_instrumentation_scoped_to_change(tmp_path: Path) -> None:
    """The scoped per-task path is the ONLY place coverage instrumentation
    is built: ``_run_coverage`` adds ``--cov=<module>`` and
    ``--cov-report=json:...`` for the changed module's dotted name and the
    explicit candidate nodes -- never a bare full-suite invocation. This is
    the producing side of the T-000392 placement invariant (the consuming
    full-suite path is pinned in test_checks.py)."""
    from mcloop.coverage_verify import _run_coverage

    captured: list[tuple[str, ...]] = []

    def fake(args, **kwargs):
        captured.append(tuple(args))
        for a in args:
            if isinstance(a, str) and a.startswith("--cov-report=json:"):
                Path(a.split(":", 1)[1]).write_text(
                    json.dumps({"files": {"pkg/widget.py": {"executed_lines": [1]}}})
                )
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="collected 1 item\n\n1 passed in 0.10s\n", stderr=""
        )

    with patch("mcloop.coverage_verify.subprocess.run", side_effect=fake):
        _run_coverage(tmp_path, ["tests/test_engine.py"], "pkg/widget.py", 60)

    assert len(captured) == 1
    cmd = captured[0]
    # Coverage is scoped to the changed module, not the whole project.
    assert "--cov=pkg.widget" in cmd
    assert any(a.startswith("--cov-report=json:") for a in cmd)
    # The explicit candidate node is included; no bare "run everything".
    assert any("test_engine.py" in a for a in cmd)


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


# --- is_coverage_exempt_python (AST interface/re-export detection) ----------


def test_exempt_pure_reexport_module() -> None:
    src = (
        '"""Public API surface."""\n'
        "from __future__ import annotations\n\n"
        "from pkg.widget import Widget\n"
        "from pkg.engine import Engine\n\n"
        '__all__ = ["Widget", "Engine"]\n'
    )
    assert is_coverage_exempt_python(src) is True


def test_exempt_annotated_all_reexport() -> None:
    src = 'from pkg.a import A\n\n__all__: list[str] = ["A"]\n'
    assert is_coverage_exempt_python(src) is True


def test_exempt_protocol_with_ellipsis_bodies() -> None:
    src = (
        "from typing import Protocol\n\n\n"
        "class Reader(Protocol):\n"
        '    """A reader."""\n'
        "    name: str\n\n"
        "    def read(self, n: int) -> bytes: ...\n\n"
        "    def close(self) -> None: ...\n"
    )
    assert is_coverage_exempt_python(src) is True


def test_exempt_typing_dotted_protocol_and_generic() -> None:
    src = (
        "import typing\n\n\n"
        "class Store(typing.Protocol[typing.TypeVar('T')]):\n"
        "    def get(self, k: str): ...\n"
    )
    assert is_coverage_exempt_python(src) is True


def test_exempt_abc_with_abstractmethods() -> None:
    src = (
        "import abc\nfrom abc import ABC, abstractmethod\n\n\n"
        "class Base(ABC):\n"
        "    @abstractmethod\n"
        "    def run(self) -> int:\n"
        "        raise NotImplementedError\n\n"
        "    @abstractmethod\n"
        "    def stop(self) -> None:\n"
        "        pass\n"
    )
    assert is_coverage_exempt_python(src) is True


def test_exempt_abcmeta_metaclass() -> None:
    src = "from abc import ABCMeta\n\n\nclass Base(metaclass=ABCMeta):\n    def go(self): ...\n"
    assert is_coverage_exempt_python(src) is True


def test_not_exempt_module_with_function_def() -> None:
    src = "def helper(x):\n    return x + 1\n"
    assert is_coverage_exempt_python(src) is False


def test_not_exempt_module_level_executable_statement() -> None:
    src = "import os\n\nVALUE = os.getcwd()\n"
    assert is_coverage_exempt_python(src) is False


def test_not_exempt_plain_class_without_interface_base() -> None:
    # A concrete (non-Protocol/ABC) class is gated even with a trivial body.
    src = "class Thing:\n    def do(self): ...\n"
    assert is_coverage_exempt_python(src) is False


def test_not_exempt_protocol_with_concrete_method() -> None:
    src = (
        "from typing import Protocol\n\n\n"
        "class Calc(Protocol):\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
    )
    assert is_coverage_exempt_python(src) is False


def test_not_exempt_syntax_error() -> None:
    assert is_coverage_exempt_python("def (:\n") is False


def test_not_exempt_empty_module() -> None:
    # Nothing inert to point at -- fall through to the normal gate.
    assert is_coverage_exempt_python("") is False
    assert is_coverage_exempt_python('"""docs only."""\n') is False


def test_not_exempt_conditional_import_block() -> None:
    # A try/except around imports is executable control flow -> gated.
    src = (
        "try:\n    from fast import Thing\n"
        "except ImportError:\n    from slow import Thing\n\n"
        '__all__ = ["Thing"]\n'
    )
    assert is_coverage_exempt_python(src) is False


def test_verify_exempt_interface_file_passes_without_coverage(tmp_path: Path) -> None:
    """A changed Protocol/ABC/re-export file is proven without a coverage run
    and without resolving changed lines."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ports.py").write_text(
        "from typing import Protocol\n\n\n"
        "class Port(Protocol):\n"
        "    def send(self, data: bytes) -> None: ...\n"
    )
    with (
        patch("mcloop.coverage_verify.changed_new_lines") as changed,
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/ports.py", [])
    assert verdict.proven is True
    assert "interface-only" in verdict.reason
    assert verdict.candidate_nodes == ()
    # The exemption short-circuits before any line/coverage work.
    changed.assert_not_called()
    run_cov.assert_not_called()


def test_verify_exempt_abc_only_file_passes_without_coverage(tmp_path: Path) -> None:
    """A changed ABC-only file (abstract stub methods) is proven exempt with
    no mapped test and no waiver -- no coverage run, no line resolution."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "base.py").write_text(
        "from abc import ABC, abstractmethod\n\n\n"
        "class Base(ABC):\n"
        "    @abstractmethod\n"
        "    def run(self) -> int:\n"
        "        raise NotImplementedError\n\n"
        "    @abstractmethod\n"
        "    def stop(self) -> None: ...\n"
    )
    with (
        patch("mcloop.coverage_verify.changed_new_lines") as changed,
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/base.py", [])
    assert verdict.proven is True
    assert "interface-only" in verdict.reason
    assert verdict.candidate_nodes == ()
    changed.assert_not_called()
    run_cov.assert_not_called()


def test_verify_exempt_reexport_init_passes_without_coverage(tmp_path: Path) -> None:
    """A changed import-and-re-export-only ``__init__.py`` is proven exempt
    with no mapped test and no waiver -- it has no executable line to cover."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        '"""Public API surface."""\n'
        "from __future__ import annotations\n\n"
        "from pkg.widget import Widget\n"
        "from pkg.engine import Engine\n\n"
        '__all__ = ["Widget", "Engine"]\n'
    )
    with (
        patch("mcloop.coverage_verify.changed_new_lines") as changed,
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/__init__.py", [])
    assert verdict.proven is True
    assert "interface-only" in verdict.reason
    assert verdict.candidate_nodes == ()
    changed.assert_not_called()
    run_cov.assert_not_called()


def test_verify_protocol_mixed_with_function_still_gated(tmp_path: Path) -> None:
    """A file mixing a Protocol with one real executable function is NOT
    exempt: the function carries logic, so the gate still runs the coverage
    path and fails closed when nothing exercises the change."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mixed.py").write_text(
        "from typing import Protocol\n\n\n"
        "class Reader(Protocol):\n"
        "    def read(self, n: int) -> bytes: ...\n\n\n"
        "def helper(x):\n"
        "    return x + 1\n"
    )
    # Confirm the exemption check itself rejects the mixed module.
    assert is_coverage_exempt_python((pkg / "mixed.py").read_text()) is False
    with (
        patch("mcloop.coverage_verify.changed_new_lines", return_value={7}) as changed,
        patch("mcloop.coverage_verify.dependent_test_files", return_value=[]),
        patch("mcloop.coverage_verify._run_coverage") as run_cov,
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/mixed.py", [])
    assert verdict.proven is False
    assert "no scoped candidate" in verdict.reason
    # The mixed file went through the normal gate, not the exemption.
    changed.assert_called_once()
    run_cov.assert_not_called()


def test_verify_logic_bearing_py_file_still_gated(tmp_path: Path) -> None:
    """An ordinary .py file with executable logic is not exempt and still
    goes through the coverage path."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "widget.py").write_text("def widget():\n    return 42\n")
    with (
        patch("mcloop.coverage_verify.changed_new_lines", return_value={1}),
        patch("mcloop.coverage_verify.dependent_test_files", return_value=[]),
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", "pkg/widget.py", [])
    assert verdict.proven is False
    assert "no scoped candidate" in verdict.reason


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


def test_verify_logic_bearing_non_python_cannot_pass(tmp_path: Path) -> None:
    # A template embeds behavior but has no executable coverage line here
    # and is not the no-test-needed class, so it cannot be proven.
    verdict = verify_change_covered(tmp_path, "base-sha", "templates/email.j2", [])
    assert verdict.proven is False
    assert "non-Python" in verdict.reason


def test_verify_non_code_input_passes_without_test(tmp_path: Path) -> None:
    # Dependency manifests, tool config, lock, and plain data files carry
    # no executable logic: the gate exempts them with no coverage run.
    for src in (
        "pyproject.toml",
        "config/data.yaml",
        "requirements.txt",
        "poetry.lock",
    ):
        verdict = verify_change_covered(tmp_path, "base-sha", src, [])
        assert verdict.proven is True, src
        assert "needs no test" in verdict.reason
        assert verdict.candidate_nodes == ()


def test_verify_missing_baseline_fails_closed(tmp_path: Path) -> None:
    # Empty baseline -> changed_new_lines returns None -> cannot prove.
    verdict = verify_change_covered(tmp_path, "", "pkg/widget.py", [])
    assert verdict.proven is False
    assert "could not resolve changed lines" in verdict.reason


# --- untracked-aware changed_new_lines (real git) ---------------------------


def _init_repo_committing_all(root: Path) -> str:
    """git init + config + commit everything currently on disk; return HEAD.

    Mirrors the inline real-git setup used by
    ``test_changed_new_lines_against_real_baseline``.
    """
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()


def test_changed_new_lines_untracked_file_returns_all_lines(tmp_path: Path) -> None:
    """A brand-new UNTRACKED file is invisible to ``git diff <baseline>``;
    the untracked fallback models it as a new-file diff so every physical
    line 1..N is reported as added."""
    (tmp_path / "base.py").write_text("x = 1\n")
    base = _init_repo_committing_all(tmp_path)
    pkg = tmp_path / "writer" / "lint" / "checks"
    pkg.mkdir(parents=True)
    # 5 physical lines, including a def and a module-level call.
    (pkg / "__init__.py").write_text(
        "import os\n\n\ndef go():\n    return os.getpid()\n"
    )
    # splitlines() -> ['import os','','','def go():','    return os.getpid()'] = 5
    changed = changed_new_lines(tmp_path, base, "writer/lint/checks/__init__.py")
    assert changed == {1, 2, 3, 4, 5}


def test_changed_new_lines_untracked_empty_file_still_empty(tmp_path: Path) -> None:
    """An untracked but EMPTY file has nothing to prove: the fallback yields
    an empty set, so the gate still fails it (no free pass)."""
    (tmp_path / "base.py").write_text("x = 1\n")
    base = _init_repo_committing_all(tmp_path)
    pkg = tmp_path / "writer" / "lint" / "checks"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    assert changed_new_lines(tmp_path, base, "writer/lint/checks/__init__.py") == set()


def test_verify_change_covered_untracked_file_with_uncovered_lines_fails(
    tmp_path: Path,
) -> None:
    """An untracked file is made VISIBLE, not EXEMPT: its lines are resolved
    via the fallback, but if no candidate test executes them the gate still
    fails (coverage is still required)."""
    _make_dependent_project(tmp_path)
    base = _init_repo_committing_all(tmp_path)
    # New untracked module with real executable content (2 lines).
    (tmp_path / "pkg" / "newcheck.py").write_text("def check():\n    return 7\n")
    with patch("mcloop.coverage_verify._run_coverage", return_value=({999}, "")):
        verdict = verify_change_covered(
            tmp_path, base, "pkg/newcheck.py", ["tests/test_engine.py"]
        )
    assert verdict.proven is False
    assert "not executed" in verdict.reason


def test_verify_change_covered_untracked_file_import_executed_passes_DOCUMENTED_LIMITATION(
    tmp_path: Path,
) -> None:
    """An untracked file whose changed lines are (partly) executed passes.

    DOCUMENTED LIMITATION: the gate passes when ANY changed line executed
    (``covered = changed & executed``, coverage_verify.py:619); import/def-line
    execution suffices. Tightening this to require behavioral-line coverage is
    deferred gate-hardening (rationale doc §9), not closed here.
    """
    _make_dependent_project(tmp_path)
    base = _init_repo_committing_all(tmp_path)
    (tmp_path / "pkg" / "newcheck.py").write_text("def check():\n    return 7\n")
    # Only line 1 (the def line) is reported executed -- enough to pass.
    with patch("mcloop.coverage_verify._run_coverage", return_value=({1}, "")):
        verdict = verify_change_covered(
            tmp_path, base, "pkg/newcheck.py", ["tests/test_engine.py"]
        )
    assert verdict.proven is True
