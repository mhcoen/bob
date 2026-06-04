"""Tests for duplo.reauthor_phase_ids.stamp_sequential_phase_ids.

Covers the T-000004 fix: the phase_id stamping site must assign a
unique, sequential id (phase_001..phase_NNN) tracking each phase's
position, never a constant phase_001.
"""

from __future__ import annotations

import re

from bob_tools.planfile import parse_plan, render_plan

from duplo.reauthor_phase_ids import parse_plan_phases, stamp_sequential_phase_ids

_PHASE_ID_COMMENT = re.compile(r"<!-- phase_id: (?P<phase_id>\S+) -->")


def _phase_id_comments(plan_text: str) -> list[str]:
    """Return every ``<!-- phase_id: ... -->`` comment id in source order."""
    return _PHASE_ID_COMMENT.findall(plan_text)


def test_constant_phase_001_becomes_sequential() -> None:
    """Every phase sharing phase_001 is re-stamped to track position."""
    plan = (
        "## Phase 1: Scaffold\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "- [ ] T-000001: scaffold\n"
        "\n"
        "## Phase 2: Core\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "- [ ] T-000002: core\n"
        "\n"
        "## Phase 3: Polish\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "- [ ] T-000003: polish\n"
    )

    result = stamp_sequential_phase_ids(plan)

    ids = [h.id for h in parse_plan_phases(result)]
    assert ids == ["phase_001", "phase_002", "phase_003"]
    assert len(ids) == len(set(ids))


def test_ids_are_unique_and_sequential() -> None:
    """parse_plan_phases on a stamped multi-phase plan yields 1..N ids."""
    plan = "".join(
        f"## Phase {n}: Phase {n}\n<!-- phase_id: phase_001 -->\n\n- [ ] T: t{n}\n\n"
        for n in range(1, 8)
    )

    result = stamp_sequential_phase_ids(plan)

    ids = [h.id for h in parse_plan_phases(result)]
    assert ids == [f"phase_{n:03d}" for n in range(1, 8)]


def test_legacy_inline_header_id_is_rewritten_and_comment_inserted() -> None:
    """Legacy ``## Phase phase_NNN:`` headers get a matching comment.

    When no phase_id comment follows the header, one is inserted; the
    legacy inline token is rewritten so header and comment agree.
    """
    plan = (
        "## Phase phase_001: Scaffold\n"
        "\n"
        "- [ ] T-000001: scaffold\n"
        "\n"
        "## Phase phase_001: Core\n"
        "\n"
        "- [ ] T-000002: core\n"
    )

    result = stamp_sequential_phase_ids(plan)

    assert "## Phase phase_001: Scaffold" in result
    assert "## Phase phase_002: Core" in result
    assert "<!-- phase_id: phase_001 -->" in result
    assert "<!-- phase_id: phase_002 -->" in result
    ids = [h.id for h in parse_plan_phases(result)]
    assert ids == ["phase_001", "phase_002"]


def test_display_ordinal_header_keeps_ordinal() -> None:
    """An ``## Phase 1:`` ordinal header keeps its ordinal; only the
    comment is the id of record and gets stamped sequentially."""
    plan = (
        "## Phase 1: Scaffold\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "## Phase 2: Core\n"
        "<!-- phase_id: phase_001 -->\n"
    )

    result = stamp_sequential_phase_ids(plan)

    assert "## Phase 1: Scaffold" in result
    assert "## Phase 2: Core" in result
    assert "<!-- phase_id: phase_002 -->" in result


def test_idempotent_on_already_sequential_plan() -> None:
    """Stamping an already-sequential plan returns equal text."""
    plan = (
        "## Phase 1: Scaffold\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "## Phase 2: Core\n"
        "<!-- phase_id: phase_002 -->\n"
    )

    once = stamp_sequential_phase_ids(plan)
    twice = stamp_sequential_phase_ids(once)

    assert once == twice
    assert [h.id for h in parse_plan_phases(once)] == ["phase_001", "phase_002"]


def test_stage_keyword_supported() -> None:
    """``## Stage N:`` headers are stamped like ``Phase`` headers."""
    plan = (
        "## Stage 1: Foundations\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "## Stage 2: Build\n"
        "<!-- phase_id: phase_001 -->\n"
    )

    result = stamp_sequential_phase_ids(plan)

    ids = [h.id for h in parse_plan_phases(result)]
    # parse_plan_phases only recognizes Phase headers, so assert on the
    # comment text directly for the Stage case.
    assert "<!-- phase_id: phase_001 -->" in result
    assert "<!-- phase_id: phase_002 -->" in result
    assert ids == []


def test_non_header_lines_and_trailing_newline_preserved() -> None:
    """Prose, tasks, the H1 envelope, and the trailing newline survive."""
    plan = (
        "# MyApp -- Phase 0\n"
        "\n"
        "## Phase 1: Scaffold\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        "Some prose about the phase.\n"
        "\n"
        "- [ ] T-000001: do the thing\n"
    )

    result = stamp_sequential_phase_ids(plan)

    assert result.startswith("# MyApp -- Phase 0\n")
    assert "Some prose about the phase." in result
    assert "- [ ] T-000001: do the thing" in result
    assert result.endswith("\n")


def test_empty_and_no_phase_text_unchanged() -> None:
    """Text with no phase headers round-trips verbatim."""
    assert stamp_sequential_phase_ids("") == ""
    plain = "# Just a title\n\nsome notes\n"
    assert stamp_sequential_phase_ids(plain) == plain


def test_generated_saved_plan_has_unique_sequential_phase_id_comments() -> None:
    """Regression: a generated, then saved, multi-phase plan must carry
    unique, sequential ``<!-- phase_id: ... -->`` comments across all
    phases.

    Exercises the full generate -> stamp -> save path. The generator's
    pre-stamp output reproduces the original bug, where every phase
    shares a constant ``phase_001``. After stamping (the fix) the plan
    is round-tripped through the canonical save path
    (``parse_plan`` -> ``render_plan``) that writes PLAN.md, and the
    saved artifact's phase_id comment lines are asserted unique and
    sequential ``phase_001``..``phase_NNN`` across every phase.
    """
    phase_count = 6
    generated = "<!-- bob-plan-format: 1 -->\n\n# MyApp\n\n"
    for n in range(1, phase_count + 1):
        generated += (
            f"## Phase {n}: Phase {n}\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            f"- [ ] T-{n:06d}: do thing {n}\n"
            "\n"
        )

    # The unstamped generator output exhibits the bug: a constant id.
    assert _phase_id_comments(generated) == ["phase_001"] * phase_count

    stamped = stamp_sequential_phase_ids(generated)
    saved = render_plan(parse_plan(stamped))

    ids = _phase_id_comments(saved)
    expected = [f"phase_{n:03d}" for n in range(1, phase_count + 1)]
    assert ids == expected
    assert len(ids) == phase_count
    assert len(ids) == len(set(ids))
