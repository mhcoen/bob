"""Tests for the runtime-mutation preflight (``preflight_runtime_plan``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bob_tools.planfile import (
    PlanPreflightError,
    load,
    validate_plan,
)
from bob_tools.planfile.preflight import preflight_runtime_plan

_LEGACY_PLAN = (
    "# Demo\n"
    "\n"
    "## Phase 1: Core\n"
    "\n"
    "- [ ] Build the widget\n"
    "- [x] Wire it up\n"
    "\n"
    "## Phase 2: Polish\n"
    "\n"
    "- [ ] Buff it\n"
)

# Already constructed: magic line, ids, explicit phase_id comments.
_CONSTRUCTED_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Phase 1: Core\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: Build the widget\n"
)

# Corrupt: duplicate task ids — structural normalization cannot resolve.
_CORRUPT_DUP_IDS = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Phase 1: Core\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: Build the widget\n"
    "- [ ] T-000001: Duplicate id task\n"
)


def test_clean_legacy_plan_is_migrated_and_mutatable(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(_LEGACY_PLAN)
    notices: list[str] = []

    plan = preflight_runtime_plan(path, notice=notices.append, label="PLAN.md")

    # Returned plan and the on-disk file are both constructed-structurally
    # valid (acceptance deferred), and a one-line migration notice fired.
    validate_plan(plan, constructed=True, require_acceptance=False)
    validate_plan(load(path), constructed=True, require_acceptance=False)
    assert load(path).magic_version == 1
    assert len(notices) == 1
    assert "migrating legacy PLAN.md to canonical form" in notices[0]


def test_already_constructed_plan_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(_CONSTRUCTED_PLAN)
    before = path.read_text()
    notices: list[str] = []

    preflight_runtime_plan(path, notice=notices.append)

    # No migration: no notice, bytes unchanged.
    assert notices == []
    assert path.read_text() == before


def test_corrupt_plan_is_refused_and_left_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(_CORRUPT_DUP_IDS)
    before = path.read_text()

    with pytest.raises(PlanPreflightError) as excinfo:
        preflight_runtime_plan(path, notice=lambda _m: None)

    # Diagnostic names the unresolved problem; the file is untouched.
    assert any("duplicate task id T-000001" in e for e in excinfo.value.errors)
    assert excinfo.value.path == path
    assert path.read_text() == before
