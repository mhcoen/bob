"""B3 R2: ID-bearing canonical-plan precondition at run_loop entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcloop._planfile_precondition import PlanNotCanonicalError
from mcloop.main import _enforce_canonical_inputs, run_loop

_ID_LESS_PHASE_PLAN = (
    "# Demo\n\n## Stage 1: Setup\n<!-- phase_id: phase_001 -->\n\n- [ ] only task\n"
)

_CANONICAL_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Stage 1: Setup\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: only task\n"
)

_CANONICAL_BUGS = "## Bugs\n\n"


def _init_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir(exist_ok=True)


def test_helper_rejects_phase_bearing_plan_without_task_ids(tmp_path: Path) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_ID_LESS_PHASE_PLAN)
    current_plan = tmp_path / "CURRENT_PLAN.md"
    bugs = tmp_path / "BUGS.md"

    with pytest.raises(PlanNotCanonicalError) as ei:
        _enforce_canonical_inputs(master, current_plan, bugs)

    assert "stable T-NNNNNN ids" in str(ei.value)
    assert ei.value.source_path == master


def test_run_loop_rejects_id_less_phase_plan_before_retry_mutation(tmp_path: Path) -> None:
    _init_git(tmp_path)
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)
    current_plan = tmp_path / "CURRENT_PLAN.md"
    current_text = (
        "## Stage 1: Setup\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "- [ ] pending task\n"
        "- [!] previously failed task\n"
    )
    current_plan.write_text(current_text)
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(_CANONICAL_BUGS)

    with pytest.raises(PlanNotCanonicalError) as ei:
        run_loop(master, retry=True, no_audit=True)

    assert ei.value.source_path == current_plan
    assert "stable T-NNNNNN ids" in str(ei.value)
    assert current_plan.read_text() == current_text


def test_helper_accepts_phase_bearing_plan_with_task_ids(tmp_path: Path) -> None:
    master = tmp_path / "PLAN.md"
    master.write_text(_CANONICAL_PLAN)
    current_plan = tmp_path / "CURRENT_PLAN.md"
    bugs = tmp_path / "BUGS.md"
    bugs.write_text(_CANONICAL_BUGS)

    _enforce_canonical_inputs(master, current_plan, bugs)
