"""Unit tests for mcloop.plan_split.

Covers extract_next_phase, mark_phase_complete, transition_phase,
ensure_current_plan, get_current_phase_name across phased plans,
flat plans, and edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcloop.plan_split import (
    ensure_bugs_file,
    ensure_current_plan,
    extract_next_phase,
    get_current_phase_name,
    mark_phase_complete,
    transition_phase,
)


# ---------------------------------------------------------------------------
# extract_next_phase
# ---------------------------------------------------------------------------


def test_extract_next_phase_first_unchecked_phase(tmp_path: Path) -> None:
    """First incomplete phase is returned with full content."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "# Project\n"
        "\n"
        "## Phase 1: Bootstrap\n"
        "- [x] Task A\n"
        "- [x] Task B\n"
        "\n"
        "## Phase 2: Core\n"
        "- [ ] Task C\n"
        "- [ ] Task D\n"
        "\n"
        "## Phase 3: Polish\n"
        "- [ ] Task E\n"
    )

    result = extract_next_phase(master)
    assert result is not None
    name, content = result
    assert name == "Phase 2: Core"
    assert "## Phase 2: Core" in content
    assert "- [ ] Task C" in content
    assert "- [ ] Task D" in content
    # Should not include later phases
    assert "Phase 3" not in content
    assert "Task E" not in content


def test_extract_next_phase_skips_fully_checked_phases(tmp_path: Path) -> None:
    """Phases with all tasks checked are skipped."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: Done\n"
        "- [x] A\n"
        "## Phase 2: Also done\n"
        "- [x] B\n"
        "## Phase 3: Active\n"
        "- [ ] C\n"
    )
    result = extract_next_phase(master)
    assert result is not None
    assert result[0] == "Phase 3: Active"


def test_extract_next_phase_all_complete_returns_none(tmp_path: Path) -> None:
    """When every phase is fully checked, returns None."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n- [x] task\n## Phase 2: B\n- [x] task\n"
    )
    assert extract_next_phase(master) is None


def test_extract_next_phase_excludes_bugs_section(tmp_path: Path) -> None:
    """The Bugs section is not treated as a phase and is excluded."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: Active\n"
        "- [ ] real task\n"
        "\n"
        "## Bugs\n"
        "- [ ] bug item\n"
    )
    result = extract_next_phase(master)
    assert result is not None
    name, content = result
    assert name == "Phase 1: Active"
    assert "Bugs" not in content
    assert "bug item" not in content


def test_extract_next_phase_flat_plan_returns_empty_name(tmp_path: Path) -> None:
    """A plan with no phase/stage headers returns ('', content)."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "# Project\n"
        "\n"
        "- [x] Already done\n"
        "- [ ] Still to do\n"
        "- [ ] Also pending\n"
    )
    result = extract_next_phase(master)
    assert result is not None
    name, content = result
    assert name == ""
    assert "Still to do" in content
    assert "Also pending" in content


def test_extract_next_phase_flat_plan_all_done_returns_none(tmp_path: Path) -> None:
    """Flat plan with every task checked returns None."""
    master = tmp_path / "PLAN.md"
    master.write_text("- [x] one\n- [x] two\n")
    assert extract_next_phase(master) is None


def test_extract_next_phase_no_tasks_returns_none(tmp_path: Path) -> None:
    """Master with no checkbox lines at all returns None."""
    master = tmp_path / "PLAN.md"
    master.write_text("# Project\n\nJust prose, no tasks.\n")
    assert extract_next_phase(master) is None


def test_extract_next_phase_stage_keyword_also_recognized(tmp_path: Path) -> None:
    """Stage N headers behave the same as Phase N headers."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Stage 1: Foundation\n- [x] done\n## Stage 2: Build\n- [ ] todo\n"
    )
    result = extract_next_phase(master)
    assert result is not None
    assert result[0] == "Stage 2: Build"


# ---------------------------------------------------------------------------
# mark_phase_complete
# ---------------------------------------------------------------------------


def test_mark_phase_complete_bulk_checks_target_phase(tmp_path: Path) -> None:
    """All [ ] in the named phase become [x]; other phases untouched."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n"
        "- [ ] a1\n"
        "- [ ] a2\n"
        "## Phase 2: B\n"
        "- [ ] b1\n"
    )
    mark_phase_complete(master, "Phase 1: A")
    text = master.read_text()
    assert "- [x] a1" in text
    assert "- [x] a2" in text
    assert "- [ ] b1" in text  # untouched


def test_mark_phase_complete_does_not_touch_bugs_section(tmp_path: Path) -> None:
    """Bugs section after target phase is preserved."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n"
        "- [ ] task\n"
        "## Bugs\n"
        "- [ ] bug\n"
    )
    mark_phase_complete(master, "Phase 1: A")
    text = master.read_text()
    assert "- [x] task" in text
    assert "- [ ] bug" in text


def test_mark_phase_complete_preserves_already_checked(tmp_path: Path) -> None:
    """Already-checked tasks stay [x] (no double-check artifact)."""
    master = tmp_path / "PLAN.md"
    master.write_text("## Phase 1: A\n- [x] done\n- [ ] pending\n")
    mark_phase_complete(master, "Phase 1: A")
    text = master.read_text()
    assert text.count("- [x] done") == 1
    assert "- [x] pending" in text


def test_mark_phase_complete_unknown_phase_is_noop(tmp_path: Path) -> None:
    """Calling with a phase name not in the file changes nothing."""
    master = tmp_path / "PLAN.md"
    original = "## Phase 1: A\n- [ ] task\n"
    master.write_text(original)
    mark_phase_complete(master, "Phase 99: Does Not Exist")
    assert master.read_text() == original


def test_mark_phase_complete_flat_plan_checks_all(tmp_path: Path) -> None:
    """For phase_name='', every non-bug task is checked."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "- [ ] one\n"
        "- [ ] two\n"
        "## Bugs\n"
        "- [ ] bug\n"
    )
    mark_phase_complete(master, "")
    text = master.read_text()
    assert "- [x] one" in text
    assert "- [x] two" in text
    assert "- [ ] bug" in text


# ---------------------------------------------------------------------------
# get_current_phase_name
# ---------------------------------------------------------------------------


def test_get_current_phase_name_reads_first_stage_header(tmp_path: Path) -> None:
    """Returns the stage/phase header text from the file."""
    plan = tmp_path / "CURRENT_PLAN.md"
    plan.write_text(
        "## Phase 2: Core\n- [ ] task\n"
    )
    assert get_current_phase_name(plan) == "Phase 2: Core"


def test_get_current_phase_name_empty_for_flat_plan(tmp_path: Path) -> None:
    """A file with no stage header returns ''."""
    plan = tmp_path / "CURRENT_PLAN.md"
    plan.write_text("- [ ] just a task\n- [ ] another\n")
    assert get_current_phase_name(plan) == ""


def test_get_current_phase_name_uses_first_header_only(tmp_path: Path) -> None:
    """When multiple stage headers exist, the first is returned."""
    plan = tmp_path / "CURRENT_PLAN.md"
    plan.write_text(
        "## Phase 1: First\n- [ ] a\n## Phase 2: Second\n- [ ] b\n"
    )
    assert get_current_phase_name(plan) == "Phase 1: First"


# ---------------------------------------------------------------------------
# ensure_current_plan
# ---------------------------------------------------------------------------


def test_ensure_current_plan_extracts_when_missing(tmp_path: Path) -> None:
    """Missing CURRENT_PLAN.md is created from master's next unchecked phase."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n- [x] done\n## Phase 2: B\n- [ ] todo\n"
    )
    current = tmp_path / "CURRENT_PLAN.md"
    assert not current.exists()

    result = ensure_current_plan(master, current)
    assert result is True
    assert current.exists()
    assert "Phase 2: B" in current.read_text()
    assert "todo" in current.read_text()


def test_ensure_current_plan_leaves_existing_untouched(tmp_path: Path) -> None:
    """If CURRENT_PLAN.md already exists, its contents are preserved."""
    master = tmp_path / "PLAN.md"
    master.write_text("## Phase 1: A\n- [ ] from master\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Phase 7: Pre-existing\n- [ ] do not overwrite\n")

    result = ensure_current_plan(master, current)
    assert result is True
    assert "Pre-existing" in current.read_text()
    assert "from master" not in current.read_text()


def test_ensure_current_plan_returns_false_when_master_complete(tmp_path: Path) -> None:
    """All phases done and no CURRENT_PLAN.md → returns False, file not created."""
    master = tmp_path / "PLAN.md"
    master.write_text("## Phase 1: A\n- [x] done\n")
    current = tmp_path / "CURRENT_PLAN.md"

    result = ensure_current_plan(master, current)
    assert result is False
    assert not current.exists()


# ---------------------------------------------------------------------------
# transition_phase
# ---------------------------------------------------------------------------


def test_transition_phase_marks_complete_and_extracts_next(tmp_path: Path) -> None:
    """Current phase is bulk-checked in master; next phase written to current."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n"
        "- [ ] a1\n"
        "- [ ] a2\n"
        "## Phase 2: B\n"
        "- [ ] b1\n"
    )
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Phase 1: A\n- [x] a1\n- [x] a2\n")

    next_name = transition_phase(master, current)
    assert next_name == "Phase 2: B"

    master_text = master.read_text()
    assert "- [x] a1" in master_text
    assert "- [x] a2" in master_text
    assert "- [ ] b1" in master_text  # phase 2 still pending in master

    current_text = current.read_text()
    assert "Phase 2: B" in current_text
    assert "- [ ] b1" in current_text
    assert "Phase 1" not in current_text  # phase 1 not in new current plan


def test_transition_phase_returns_none_and_unlinks_when_done(tmp_path: Path) -> None:
    """When the just-completed phase was the last, returns None and removes file."""
    master = tmp_path / "PLAN.md"
    master.write_text("## Phase 1: Final\n- [ ] last task\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("## Phase 1: Final\n- [x] last task\n")

    result = transition_phase(master, current)
    assert result is None
    assert not current.exists()
    # Master should reflect the bulk-check
    assert "- [x] last task" in master.read_text()


def test_transition_phase_flat_plan_completes_master(tmp_path: Path) -> None:
    """Flat plan transition checks off everything and returns None."""
    master = tmp_path / "PLAN.md"
    master.write_text("- [ ] one\n- [ ] two\n")
    current = tmp_path / "CURRENT_PLAN.md"
    current.write_text("- [x] one\n- [x] two\n")

    result = transition_phase(master, current)
    assert result is None
    master_text = master.read_text()
    assert "- [x] one" in master_text
    assert "- [x] two" in master_text
    assert not current.exists()


# ---------------------------------------------------------------------------
# ensure_bugs_file
# ---------------------------------------------------------------------------


def test_ensure_bugs_file_creates_when_missing(tmp_path: Path) -> None:
    """Missing BUGS.md is created with an empty Bugs header."""
    bugs = tmp_path / "BUGS.md"
    assert not bugs.exists()
    ensure_bugs_file(bugs)
    assert bugs.exists()
    assert "Bugs" in bugs.read_text()


def test_ensure_bugs_file_preserves_existing(tmp_path: Path) -> None:
    """Existing BUGS.md is not overwritten."""
    bugs = tmp_path / "BUGS.md"
    bugs.write_text("## Bugs\n\n- [ ] existing bug\n")
    ensure_bugs_file(bugs)
    assert "existing bug" in bugs.read_text()


# ---------------------------------------------------------------------------
# Round-trip: ensure_current_plan + transition_phase
# ---------------------------------------------------------------------------


def test_full_lifecycle_three_phases(tmp_path: Path) -> None:
    """Walk through three phases by repeated ensure + transition cycles."""
    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n- [ ] a\n"
        "## Phase 2: B\n- [ ] b\n"
        "## Phase 3: C\n- [ ] c\n"
    )
    current = tmp_path / "CURRENT_PLAN.md"

    # Cycle 1: extract phase 1
    assert ensure_current_plan(master, current) is True
    assert get_current_phase_name(current) == "Phase 1: A"

    # User "completes" phase 1 by checking the box in CURRENT_PLAN.md
    current.write_text("## Phase 1: A\n- [x] a\n")
    next_name = transition_phase(master, current)
    assert next_name == "Phase 2: B"

    # Cycle 2: phase 2 is now active
    assert get_current_phase_name(current) == "Phase 2: B"
    current.write_text("## Phase 2: B\n- [x] b\n")
    next_name = transition_phase(master, current)
    assert next_name == "Phase 3: C"

    # Cycle 3: phase 3 is the last
    current.write_text("## Phase 3: C\n- [x] c\n")
    next_name = transition_phase(master, current)
    assert next_name is None
    assert not current.exists()

    # Master should now have everything checked
    master_text = master.read_text()
    assert master_text.count("- [x]") == 3
    assert "- [ ]" not in master_text


def test_extract_then_ensure_is_idempotent(tmp_path: Path) -> None:
    """Calling ensure_current_plan twice is a no-op on the second call."""
    master = tmp_path / "PLAN.md"
    master.write_text("## Phase 1: A\n- [ ] a\n")
    current = tmp_path / "CURRENT_PLAN.md"

    ensure_current_plan(master, current)
    first_content = current.read_text()
    ensure_current_plan(master, current)
    assert current.read_text() == first_content


# ---------------------------------------------------------------------------
# Corruption / structural sanity
# ---------------------------------------------------------------------------


def test_extract_next_phase_raises_on_corrupted_master(tmp_path: Path) -> None:
    """extract_next_phase calls parse(check_structure=True) and surfaces errors."""
    from mcloop.checklist import PlanCorruptionError

    master = tmp_path / "PLAN.md"
    master.write_text(
        "## Phase 1: A\n- [ ] a\n## Phase 1: A duplicate\n- [ ] b\n"
    )
    with pytest.raises(PlanCorruptionError):
        extract_next_phase(master)
