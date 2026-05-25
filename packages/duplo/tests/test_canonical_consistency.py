"""Tests for duplo.canonical_consistency."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from duplo.canonical_consistency import (
    ConsistencyDrift,
    SpecPyprojectInconsistencyError,
    _extract_runsh_module,
    _extract_spec_script_name,
    _script_value_module,
    _strip_glob,
    validate_spec_pyproject_runsh_consistency,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _spec_with(script: str) -> str:
    return textwrap.dedent(
        f"""\
        # SPEC

        ## Purpose

        A small CLI.

        ## Done definition

        - `pip install -e .` installs `{script}` as an entry point.
        - `{script} --help` prints usage.
        """
    )


def _pyproject(
    script_name: str,
    script_value: str,
    include_pattern: str | None,
) -> str:
    parts = [
        "[build-system]",
        'requires = ["setuptools"]',
        'build-backend = "setuptools.build_meta"',
        "",
        "[project]",
        f'name = "{script_name}"',
        'version = "0.1.0"',
        "",
        "[project.scripts]",
        f'{script_name} = "{script_value}"',
        "",
    ]
    if include_pattern is not None:
        parts.extend(
            [
                "[tool.setuptools.packages.find]",
                'where = ["."]',
                f'include = ["{include_pattern}"]',
                "",
            ]
        )
    return "\n".join(parts)


def _runsh(module: str) -> str:
    return textwrap.dedent(
        f"""\
        #!/bin/bash
        set -euo pipefail
        VENV_DIR=".venv"
        PYTHON="$VENV_DIR/bin/python"
        "$PYTHON" -m {module} "$@"
        """
    )


# ---------------------------------------------------------------------
# Helper-fn unit tests
# ---------------------------------------------------------------------


def test_extract_spec_script_name_entry_point_phrase() -> None:
    spec = "- `pip install -e .` installs `fswatch_run` as an entry point.\n"
    assert _extract_spec_script_name(spec) == "fswatch_run"


def test_extract_spec_script_name_help_pattern() -> None:
    spec = "Verify with `mytool --help` after install."
    assert _extract_spec_script_name(spec) == "mytool"


def test_extract_spec_script_name_pip_install_pattern() -> None:
    spec = "- `pip install -e .` and run `cli` to start.\n"
    assert _extract_spec_script_name(spec) == "cli"


def test_extract_spec_script_name_returns_none_when_absent() -> None:
    assert _extract_spec_script_name("no script anywhere here") is None


def test_script_value_module_simple() -> None:
    assert _script_value_module("fswatch_run.__main__:main") == "fswatch_run"


def test_script_value_module_no_colon() -> None:
    assert _script_value_module("not_a_real_value") is None


def test_strip_glob_removes_trailing_star_and_dot() -> None:
    assert _strip_glob("fswatch_run*") == "fswatch_run"
    assert _strip_glob("fswatch_run.*") == "fswatch_run"
    assert _strip_glob("fswatch_run") == "fswatch_run"


def test_extract_runsh_module() -> None:
    assert _extract_runsh_module('"$PYTHON" -m fswatch_run "$@"') == "fswatch_run"


def test_extract_runsh_module_strips_submodule() -> None:
    assert (
        _extract_runsh_module('"$PYTHON" -m fswatch_run.cli "$@"')
        == "fswatch_run"
    )


def test_extract_runsh_module_returns_none_when_absent() -> None:
    assert _extract_runsh_module("#!/bin/bash\necho hello\n") is None


# ---------------------------------------------------------------------
# validate_spec_pyproject_runsh_consistency — happy path
# ---------------------------------------------------------------------


def test_no_pyproject_is_noop(tmp_path: Path) -> None:
    validate_spec_pyproject_runsh_consistency(tmp_path)


def test_malformed_pyproject_is_noop(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "not [valid toml")
    validate_spec_pyproject_runsh_consistency(tmp_path)


def test_pyproject_without_scripts_is_noop(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
    )
    validate_spec_pyproject_runsh_consistency(tmp_path)


def test_passes_when_all_four_agree(tmp_path: Path) -> None:
    _write(tmp_path / "SPEC.md", _spec_with("fswatch_run"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "fswatch_run",
            "fswatch_run.__main__:main",
            "fswatch_run*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("fswatch_run"))
    validate_spec_pyproject_runsh_consistency(tmp_path)


def test_passes_when_spec_absent(tmp_path: Path) -> None:
    """SPEC.md absence should not block — best-effort source."""
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "fswatch_run",
            "fswatch_run.__main__:main",
            "fswatch_run*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("fswatch_run"))
    validate_spec_pyproject_runsh_consistency(tmp_path)


def test_passes_when_runsh_absent(tmp_path: Path) -> None:
    """run.sh absence should not block — best-effort source."""
    _write(tmp_path / "SPEC.md", _spec_with("fswatch_run"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "fswatch_run",
            "fswatch_run.__main__:main",
            "fswatch_run*",
        ),
    )
    validate_spec_pyproject_runsh_consistency(tmp_path)


# ---------------------------------------------------------------------
# Negative cases — single-violation
# ---------------------------------------------------------------------


def test_raises_on_spec_vs_pyproject_script_drift(tmp_path: Path) -> None:
    """SPEC says `fswatch-run`, pyproject script is `fswatch-run-smoke`.
    The smoke's actual failure mode."""
    _write(tmp_path / "SPEC.md", _spec_with("fswatch-run"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "fswatch-run-smoke",
            "fswatch_run_smoke.__main__:main",
            "fswatch_run_smoke*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("fswatch_run_smoke"))
    with pytest.raises(SpecPyprojectInconsistencyError) as ei:
        validate_spec_pyproject_runsh_consistency(tmp_path)
    msg = str(ei.value)
    assert "fswatch-run" in msg
    assert "fswatch-run-smoke" in msg
    assert "SPEC.md" in msg


def test_raises_on_script_module_vs_include_drift(tmp_path: Path) -> None:
    """pyproject [project.scripts] value's module path doesn't match
    [tool.setuptools.packages.find].include."""
    _write(tmp_path / "SPEC.md", _spec_with("mytool"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "mytool",
            "mytool_pkg.__main__:main",
            "different_pkg*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("different_pkg"))
    with pytest.raises(SpecPyprojectInconsistencyError) as ei:
        validate_spec_pyproject_runsh_consistency(tmp_path)
    msg = str(ei.value)
    assert "mytool_pkg" in msg
    assert "different_pkg" in msg


def test_raises_on_runsh_module_drift(tmp_path: Path) -> None:
    """run.sh's `python -m <pkg>` invocation doesn't match the
    pyproject script module."""
    _write(tmp_path / "SPEC.md", _spec_with("mytool"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "mytool",
            "mytool.__main__:main",
            "mytool*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("wrong_module"))
    with pytest.raises(SpecPyprojectInconsistencyError) as ei:
        validate_spec_pyproject_runsh_consistency(tmp_path)
    msg = str(ei.value)
    assert "wrong_module" in msg
    assert "run.sh" in msg


# ---------------------------------------------------------------------
# Multi-violation
# ---------------------------------------------------------------------


def test_raises_with_all_four_drifts_enumerated(tmp_path: Path) -> None:
    """All four sources disagree. Single error names every drift so
    the user can fix all in one pass."""
    _write(tmp_path / "SPEC.md", _spec_with("intent_name"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "declared_name",
            "module_a.__main__:main",
            "module_b*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("module_c"))
    with pytest.raises(SpecPyprojectInconsistencyError) as ei:
        validate_spec_pyproject_runsh_consistency(tmp_path)
    msg = str(ei.value)
    assert "intent_name" in msg
    assert "declared_name" in msg
    assert "module_a" in msg
    assert "module_b" in msg
    assert "module_c" in msg


def test_drifts_attribute_lists_every_drift(tmp_path: Path) -> None:
    _write(tmp_path / "SPEC.md", _spec_with("intent_name"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "declared_name",
            "module_a.__main__:main",
            "module_b*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("module_c"))
    with pytest.raises(SpecPyprojectInconsistencyError) as ei:
        validate_spec_pyproject_runsh_consistency(tmp_path)
    drifts = ei.value.drifts
    assert len(drifts) >= 3
    fields = {d.field for d in drifts}
    assert any("SPEC.md" in f for f in fields)
    assert any("pyproject" in f for f in fields)
    assert any("run.sh" in f for f in fields)


# ---------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------


def test_validation_failure_does_not_write_plan_or_ledger(tmp_path: Path) -> None:
    """The validator only reads files. A raised
    SpecPyprojectInconsistencyError must leave the filesystem
    unchanged: no PLAN.md, no .duplo/ledger events, no scratch files
    in project_dir."""
    _write(tmp_path / "SPEC.md", _spec_with("fswatch-run"))
    _write(
        tmp_path / "pyproject.toml",
        _pyproject(
            "fswatch-run-smoke",
            "fswatch_run_smoke.__main__:main",
            "fswatch_run_smoke*",
        ),
    )
    _write(tmp_path / "run.sh", _runsh("fswatch_run_smoke"))
    before = sorted(p.name for p in tmp_path.iterdir())

    with pytest.raises(SpecPyprojectInconsistencyError):
        validate_spec_pyproject_runsh_consistency(tmp_path)

    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
    assert not (tmp_path / "PLAN.md").exists()
    assert not (tmp_path / ".duplo").exists()


def test_consistency_drift_repr_contains_field_and_values() -> None:
    """The drift dataclass surfaces field/value/expected for the
    error formatter to consume; this pins the public attribute
    names so downstream consumers can rely on them."""
    drift = ConsistencyDrift(
        field="SPEC.md console script",
        value="fswatch-run",
        expected="fswatch-run-smoke",
    )
    assert drift.field == "SPEC.md console script"
    assert drift.value == "fswatch-run"
    assert drift.expected == "fswatch-run-smoke"
