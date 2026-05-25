"""Calibration scenario lint: verify criterion coverage in task.md.

A calibration scenario is a directory containing:

  - ``task.md``: free-form prose describing the task and the
    acceptance criteria.
  - ``expected.txt``: classifier token (positive | negative |
    ambiguous) on the first nonempty line.
  - ``.orchestra/config.json``: role bindings plus a top-level
    ``criteria`` array (F2.5a).

This module checks that every criterion id declared in
``.orchestra/config.json`` appears as a whole-word token in
``task.md``. Substring matching against short ids (e.g. ``length``,
``n``) would produce false-positive passes when the prose uses the
same word for unrelated reasons; word-boundary matching is the
right balance.

When ``criteria`` is absent or empty, the lint passes (back-compat).
This is intentional: scenarios without criteria are pre-F2.5a and
do not yet enforce structured compliance.

CLI::

    python -m orchestra.calibration.lint_scenario <scenario_dir>

Returns shell exit code 0 on pass, 2 on lint failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from orchestra.config import CriterionDecl


@dataclass(frozen=True)
class LintResult:
    """Outcome of a single scenario lint pass.

    Attributes:
        ok: True iff every configured criterion id appears as a
            whole-word token in task.md. Trivially True when the
            config has no criteria.
        scenario_dir: the scenario directory the lint ran against.
        configured_ids: ids declared in .orchestra/config.json.
        missing_in_task_md: ids absent from task.md.
        warnings: non-fatal observations (e.g., missing criteria field).
    """

    ok: bool
    scenario_dir: Path
    configured_ids: tuple[str, ...]
    missing_in_task_md: tuple[str, ...]
    warnings: tuple[str, ...]


def _read_criteria(cfg_path: Path) -> tuple[CriterionDecl, ...]:
    """Parse .orchestra/config.json's criteria array, or return empty."""
    if not cfg_path.is_file():
        return ()
    raw = json.loads(cfg_path.read_text())
    criteria_raw = raw.get("criteria") or []
    if not isinstance(criteria_raw, list):
        return ()
    return tuple(CriterionDecl.from_dict(item) for item in criteria_raw)


def lint_scenario(scenario_dir: Path) -> LintResult:
    """Verify every configured criterion id appears in task.md.

    Match precision: regex word-boundary (``\\b<id>\\b``). The id must
    appear as a whole token in the prose, not as a substring inside
    another word. Loose enough to keep task.md prose natural; tight
    enough to reject accidental collisions.
    """
    cfg_path = scenario_dir / ".orchestra" / "config.json"
    task_path = scenario_dir / "task.md"
    warnings: list[str] = []

    if not task_path.is_file():
        return LintResult(
            ok=False,
            scenario_dir=scenario_dir,
            configured_ids=(),
            missing_in_task_md=(),
            warnings=(f"task.md missing at {task_path}",),
        )

    if not cfg_path.is_file():
        warnings.append(f"config missing at {cfg_path}; skipping criteria check")
        return LintResult(
            ok=True,
            scenario_dir=scenario_dir,
            configured_ids=(),
            missing_in_task_md=(),
            warnings=tuple(warnings),
        )

    criteria = _read_criteria(cfg_path)
    if not criteria:
        warnings.append(
            f"no criteria configured in {cfg_path}; "
            "pre-F2.5a scenario, lint passes trivially"
        )
        return LintResult(
            ok=True,
            scenario_dir=scenario_dir,
            configured_ids=(),
            missing_in_task_md=(),
            warnings=tuple(warnings),
        )

    task_text = task_path.read_text()
    missing: list[str] = []
    for crit in criteria:
        pattern = rf"\b{re.escape(crit.id)}\b"
        if re.search(pattern, task_text) is None:
            missing.append(crit.id)
    return LintResult(
        ok=not missing,
        scenario_dir=scenario_dir,
        configured_ids=tuple(c.id for c in criteria),
        missing_in_task_md=tuple(missing),
        warnings=tuple(warnings),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns shell exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario_dir", type=Path)
    args = parser.parse_args(argv)
    result = lint_scenario(args.scenario_dir)
    for w in result.warnings:
        sys.stderr.write(f"warning: {w}\n")
    if result.ok:
        if result.configured_ids:
            sys.stderr.write(
                f"lint OK: {len(result.configured_ids)} criteria "
                f"all present in task.md\n"
            )
        return 0
    sys.stderr.write(
        f"lint FAILED for {result.scenario_dir}: criterion ids missing "
        f"from task.md: {sorted(result.missing_in_task_md)}\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
