"""Pre-flight consistency check across SPEC.md, pyproject.toml, run.sh.

Canonical-mode plan authoring depends on four declarations agreeing
on the project's script and package identity:

  A. SPEC.md's claimed console script name (the user's intent).
  B. pyproject.toml ``[project.scripts]`` keys (the declared entry
     point).
  C. The module path inside each ``[project.scripts]`` value.
  D. ``[tool.setuptools.packages.find].include`` patterns.
  E. ``run.sh``'s ``python -m <pkg>`` invocation.

When these drift, the synthesizer "papers over" the disagreement by
writing tasks that paper over the drift (e.g., switching the
verification command from ``<spec_name> --help`` to ``./run.sh --help``
because the pyproject declares a different script). The runtime
accepts the workaround. The user does not get told their SPEC's
contract is unsatisfiable as written.

This module raises ``SpecPyprojectInconsistencyError`` when any drift
is detected, naming every offending field. The caller (canonical
author_phase_plan) blocks the council invocation until the drift is
reconciled. Same fail-closed shape as Slice C lineage validation.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConsistencyDrift:
    """One mismatch between sources."""

    field: str
    value: str
    expected: str


class SpecPyprojectInconsistencyError(RuntimeError):
    """SPEC.md, pyproject.toml, and run.sh disagree on identifiers."""

    def __init__(self, drifts: list[ConsistencyDrift]) -> None:
        self.drifts = drifts
        lines = [
            "SPEC.md, pyproject.toml, and run.sh disagree on the "
            "project's script and package identifiers:"
        ]
        for d in drifts:
            lines.append(f"  - {d.field} = {d.value!r}, expected {d.expected!r}")
        lines.append("Reconcile the declarations before re-running canonical plan authoring.")
        super().__init__("\n".join(lines))


def _extract_spec_script_name(spec_text: str) -> str | None:
    """Find the script name SPEC.md claims to install.

    Three matchable patterns, tried in order:

      1. ``<name>`` as an entry point  (canonical phrasing)
      2. ``<name> --help``             (verification snippet)
      3. ``pip install -e .`` ... ``<name>`` (Done definition)

    Returns the first matching identifier or ``None`` if no
    recognizable pattern is present.
    """
    pat1 = re.search(r"`([A-Za-z0-9_\-]+)`\s+as an entry point", spec_text)
    if pat1:
        return pat1.group(1)
    pat2 = re.search(r"`([A-Za-z0-9_\-]+)\s+--help`", spec_text)
    if pat2:
        return pat2.group(1)
    pat3 = re.search(
        r"`pip install -e \.`[^\n]*?`([A-Za-z0-9_\-]+)`",
        spec_text,
    )
    if pat3:
        return pat3.group(1)
    return None


def _script_value_module(value: str) -> str | None:
    """``fswatch_run.__main__:main`` -> ``fswatch_run``."""
    if ":" not in value:
        return None
    module_path = value.split(":", 1)[0].strip()
    if not module_path:
        return None
    return module_path.split(".", 1)[0]


def _strip_glob(pattern: str) -> str:
    """``fswatch_run*`` -> ``fswatch_run``."""
    return pattern.rstrip("*").rstrip(".")


_RUNSH_BOOTSTRAP_MODULES = frozenset({"venv", "pip", "ensurepip", "build"})


def _extract_runsh_module(run_sh_text: str) -> str | None:
    """Find the project module run.sh invokes via ``python -m``.

    Skips stdlib bootstrap invocations (``python3 -m venv``,
    ``python -m pip``, etc.) so they cannot pose as the project's
    module. Returns the module from the LAST non-bootstrap match,
    matching the convention that run.sh's bootstrap setup happens
    early and the project invocation is the final command.
    """
    matches: list[str] = re.findall(
        r"-m\s+([A-Za-z0-9_\-][A-Za-z0-9_\-.]*)",
        run_sh_text,
    )
    for raw in reversed(matches):
        candidate = raw.split(".", 1)[0]
        if candidate in _RUNSH_BOOTSTRAP_MODULES:
            continue
        return candidate
    return None


def validate_spec_pyproject_runsh_consistency(project_dir: Path) -> None:
    """Fail-closed consistency check across SPEC, pyproject, run.sh.

    Raises ``SpecPyprojectInconsistencyError`` when any of the four
    cross-references drift, with every offending field enumerated in
    a single error message.

    No-op when:
      - ``pyproject.toml`` is absent (non-Python project, or
        pre-init state).
      - ``pyproject.toml`` is malformed (do not compound an existing
        problem).
      - ``[project.scripts]`` is absent or empty (no console script
        to cross-check).

    SPEC.md and run.sh are best-effort: missing or unparseable
    values are skipped with no drift recorded for that source.
    """
    pyproject_path = project_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        return

    try:
        pyproject_data = tomllib.loads(pyproject_path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return

    project = pyproject_data.get("project", {}) or {}
    scripts = dict(project.get("scripts", {}) or {})
    if not scripts:
        return

    drifts: list[ConsistencyDrift] = []

    spec_path = project_dir / "SPEC.md"
    spec_name: str | None = None
    if spec_path.is_file():
        spec_name = _extract_spec_script_name(spec_path.read_text())

    pyproject_script_names = set(scripts.keys())
    if spec_name is not None and spec_name not in pyproject_script_names:
        drifts.append(
            ConsistencyDrift(
                field="SPEC.md console script",
                value=spec_name,
                expected=(", ".join(sorted(pyproject_script_names)) or "<none>"),
            )
        )

    script_modules: dict[str, str | None] = {
        name: _script_value_module(value) for name, value in scripts.items()
    }

    find_section = (
        pyproject_data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    )
    include_patterns = list(find_section.get("include", []) or [])
    include_packages = {_strip_glob(str(p)) for p in include_patterns if p}

    if include_packages:
        for script_name, mod in script_modules.items():
            if mod is None:
                continue
            if mod not in include_packages:
                drifts.append(
                    ConsistencyDrift(
                        field=(f"pyproject [project.scripts].{script_name} module"),
                        value=mod,
                        expected=", ".join(sorted(include_packages)),
                    )
                )

    runsh_path = project_dir / "run.sh"
    relevant_modules = {m for m in script_modules.values() if m}
    if runsh_path.is_file() and relevant_modules:
        runsh_module = _extract_runsh_module(runsh_path.read_text())
        if runsh_module is not None and runsh_module not in relevant_modules:
            drifts.append(
                ConsistencyDrift(
                    field="run.sh `python -m` module",
                    value=runsh_module,
                    expected=", ".join(sorted(relevant_modules)),
                )
            )

    if drifts:
        raise SpecPyprojectInconsistencyError(drifts)


__all__ = [
    "ConsistencyDrift",
    "SpecPyprojectInconsistencyError",
    "validate_spec_pyproject_runsh_consistency",
]
