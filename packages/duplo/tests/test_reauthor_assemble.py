"""Tests for duplo.reauthor_assemble.

Pin the contract:
  - parse_plan_sections splits PLAN.md into preamble + per-phase
    units via :mod:`duplo.plan_document`. Bare H2 sections without
    H1 envelopes raise; the canonical structure has each H2 wrapped
    in an H1 envelope.
  - normalize_lineage_for_preservation adds preserve-default entries
    for prior ids the synthesizer didn't touch, without disturbing
    the synthesizer's explicit non-preserve declarations.
  - assemble_reauthored_plan walks prior units in order and emits
    each one verbatim (preserve), replaces with synth unit (supersede
    / split / merge), or skips (abandoned). New phases append at the
    end. Returns a :class:`Plan`; render emits the canonical text.

These tests exercise the assembly module in isolation. The
integration tests in test_reauthor.py check the same contract
end-to-end via reauthor_plan.
"""

from __future__ import annotations

from typing import Any

import pytest

from duplo.plan_document import (
    ParseError,
    PhaseUnit,
    Plan,
    render,
)
from duplo.reauthor_assemble import (
    PhaseSection,
    ReauthorAssemblyError,
    assemble_reauthored_plan,
    normalize_lineage_for_preservation,
    parse_plan_sections,
)


# ----------------------------------------------------------------------
# parse_plan_sections (thin wrapper around plan_document.parse_plan)
# ----------------------------------------------------------------------


def test_parse_sections_empty_plan() -> None:
    preamble, sections = parse_plan_sections("")
    assert preamble == ""
    assert sections == []


def test_parse_sections_no_phase_headers_is_all_preamble() -> None:
    text = "# Project\n\nSome prose.\n"
    preamble, sections = parse_plan_sections(text)
    assert preamble == text
    assert sections == []


def test_parse_sections_single_phase_with_envelope() -> None:
    text = (
        "# proj\n\n"
        "# proj — Phase 0: Title\n"
        "## Phase phase_001: First\n"
        "\n"
        "- [ ] do thing\n"
    )
    preamble, sections = parse_plan_sections(text)
    assert preamble == "# proj\n\n"
    assert len(sections) == 1
    assert sections[0].phase_id == "phase_001"
    assert sections[0].h2_title == "First"
    assert sections[0].h1_envelope == "Title"
    assert "- [ ] do thing" in sections[0].body


def test_parse_sections_multiple_phases_with_envelopes() -> None:
    text = (
        "preamble\n\n"
        "# proj — Phase 0: A\n"
        "## Phase phase_001: A title\n"
        "\n"
        "- [ ] a-task\n"
        "\n"
        "# proj — Phase 1: B\n"
        "## Phase phase_002: B title\n"
        "\n"
        "- [ ] b-task\n"
        "\n"
        "# proj — Phase 2: C\n"
        "## Phase phase_003: C title\n"
        "\n"
        "- [ ] c-task\n"
    )
    preamble, sections = parse_plan_sections(text)
    assert preamble == "preamble\n\n"
    assert [s.phase_id for s in sections] == [
        "phase_001",
        "phase_002",
        "phase_003",
    ]
    assert [s.h1_envelope for s in sections] == ["A", "B", "C"]


def test_parse_sections_bare_h2_without_envelope_raises() -> None:
    """The plan_document parser is strict: every H2 must sit under
    an H1 envelope. Reauthor's old H2-only parser is gone; bare H2
    inputs (left over from pre-canonical plans) raise rather than
    being silently misinterpreted."""
    text = "## Phase phase_001: bare\n- [ ] task\n"
    with pytest.raises(ParseError):
        parse_plan_sections(text)


# ----------------------------------------------------------------------
# normalize_lineage_for_preservation
# ----------------------------------------------------------------------


def test_normalize_adds_preserve_for_each_unaccounted_prior() -> None:
    """The bug case: synthesizer's lineage covers only phase_002, the
    other priors must be preserved by default."""
    prior_ids = [
        "phase_001",
        "phase_002",
        "phase_003",
        "phase_004",
        "phase_005",
    ]
    lineage = {
        "phases": [
            {
                "id": "phase_002b",
                "action": "supersede",
                "from": ["phase_002"],
            }
        ]
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    pids_actions = [(e["id"], e["action"]) for e in out["phases"]]
    # Synthesizer's entry survives.
    assert ("phase_002b", "supersede") in pids_actions
    # Each unaccounted prior gets a preserve default.
    for pid in ("phase_001", "phase_003", "phase_004", "phase_005"):
        assert (pid, "preserve") in pids_actions
    # phase_002 itself should NOT be added as a preserve default
    # (it was consumed by supersede.from).
    assert ("phase_002", "preserve") not in pids_actions


def test_normalize_does_not_duplicate_explicit_preserve() -> None:
    prior_ids = ["phase_001", "phase_002"]
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
        ]
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    preserve_count = sum(
        1
        for e in out["phases"]
        if e["action"] == "preserve" and e["id"] == "phase_001"
    )
    assert preserve_count == 1


def test_normalize_skips_priors_in_abandoned() -> None:
    prior_ids = ["phase_001", "phase_002"]
    lineage = {
        "phases": [],
        "abandoned": [{"id": "phase_002", "reason": "scope cut"}],
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    pids_actions = [(e["id"], e["action"]) for e in out["phases"]]
    assert ("phase_001", "preserve") in pids_actions
    # phase_002 stays abandoned, not preserve-defaulted.
    assert ("phase_002", "preserve") not in pids_actions
    # abandoned entry preserved.
    assert out["abandoned"] == [{"id": "phase_002", "reason": "scope cut"}]


def test_normalize_skips_priors_in_split_from() -> None:
    prior_ids = ["phase_001", "phase_002"]
    lineage = {
        "phases": [
            {"id": "phase_002a", "action": "split", "from": ["phase_002"]},
            {"id": "phase_002b", "action": "split", "from": ["phase_002"]},
        ]
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    pids_actions = [(e["id"], e["action"]) for e in out["phases"]]
    assert ("phase_001", "preserve") in pids_actions
    assert ("phase_002", "preserve") not in pids_actions  # consumed by split


def test_normalize_skips_priors_in_merge_from() -> None:
    prior_ids = ["phase_001", "phase_002", "phase_003"]
    lineage = {
        "phases": [
            {
                "id": "phase_merged",
                "action": "merge",
                "from": ["phase_001", "phase_002"],
            }
        ]
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    pids_actions = [(e["id"], e["action"]) for e in out["phases"]]
    # phase_001 and phase_002 consumed by merge, not preserve-defaulted.
    assert ("phase_001", "preserve") not in pids_actions
    assert ("phase_002", "preserve") not in pids_actions
    # phase_003 untouched, gets preserve default.
    assert ("phase_003", "preserve") in pids_actions


def test_normalize_idempotent() -> None:
    prior_ids = ["phase_001", "phase_002"]
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
        ]
    }
    once = normalize_lineage_for_preservation(prior_ids, lineage)
    twice = normalize_lineage_for_preservation(prior_ids, once)
    assert once == twice


def test_normalize_does_not_mutate_input() -> None:
    prior_ids = ["phase_001"]
    original: dict[str, Any] = {"phases": []}
    normalize_lineage_for_preservation(prior_ids, original)
    assert original == {"phases": []}


def test_phase_section_alias_points_to_phase_unit() -> None:
    """PhaseSection is a backward-compat alias for PhaseUnit so
    callers that imported the old name continue to work."""
    assert PhaseSection is PhaseUnit


# ----------------------------------------------------------------------
# assemble_reauthored_plan (operates on Plan + PhaseUnit)
# ----------------------------------------------------------------------


def _unit(
    phase_id: str,
    h2_title: str = "title",
    body: str = "- [ ] task\n",
    h1_envelope: str | None = None,
) -> PhaseUnit:
    return PhaseUnit(
        h1_envelope=h1_envelope or h2_title,
        phase_id=phase_id,
        h2_title=h2_title,
        body=body,
    )


def _plan(*units: PhaseUnit, preamble: str = "") -> Plan:
    return Plan(
        project_name="proj",
        preamble=preamble,
        units=tuple(units),
    )


def test_assemble_preserve_keeps_units_intact() -> None:
    prior = _plan(
        _unit("phase_001", "A"),
        _unit("phase_002", "B"),
        preamble="header\n\n",
    )
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=(),
        normalized_lineage=lineage,
    )
    assert assembled.units == prior.units
    text = render(assembled)
    assert text.startswith("header\n\n")
    # Both H1 envelopes present.
    assert "Phase 0: A" in text
    assert "Phase 1: B" in text


def test_assemble_supersede_replaces_h1_and_h2_at_prior_position() -> None:
    """Supersede replaces the WHOLE unit (H1 envelope + H2 phase
    header + body), so the H1 ordinal of the new unit follows from
    its position in the assembled plan. The renderer renumbers
    ordinals deterministically."""
    prior = _plan(
        _unit("phase_001", "A"),
        _unit("phase_002", "B-old"),
        _unit("phase_003", "C"),
    )
    new_unit = _unit("phase_002b", "B-new", body="- [ ] new b\n")
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=[new_unit],
        normalized_lineage=lineage,
    )
    assert [u.phase_id for u in assembled.units] == [
        "phase_001",
        "phase_002b",
        "phase_003",
    ]
    text = render(assembled)
    # H1 ordinals follow the new positions, not the old ones.
    assert "Phase 0: A" in text
    assert "Phase 1: B-new" in text
    assert "Phase 2: C" in text
    # The replaced unit's H2 and body are gone.
    assert "## Phase phase_002:" not in text
    assert "## Phase phase_002b: B-new" in text
    assert "- [ ] new b" in text


def test_assemble_split_emits_all_branches_at_prior_position() -> None:
    prior = _plan(
        _unit("phase_001", "A"),
        _unit("phase_002", "B"),
        _unit("phase_003", "C"),
    )
    branch_a = _unit("phase_002a", "Split A")
    branch_b = _unit("phase_002b", "Split B")
    lineage = {
        "phases": [
            {"id": "phase_002a", "action": "split", "from": ["phase_002"]},
            {"id": "phase_002b", "action": "split", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=[branch_a, branch_b],
        normalized_lineage=lineage,
    )
    assert [u.phase_id for u in assembled.units] == [
        "phase_001",
        "phase_002a",
        "phase_002b",
        "phase_003",
    ]
    text = render(assembled)
    assert "## Phase phase_002:" not in text


def test_assemble_merge_emits_target_at_first_source_position() -> None:
    prior = _plan(
        _unit("phase_001", "A"),
        _unit("phase_002", "B"),
        _unit("phase_003", "C"),
    )
    merged = _unit("phase_merged", "Merged")
    lineage = {
        "phases": [
            {
                "id": "phase_merged",
                "action": "merge",
                "from": ["phase_001", "phase_002"],
            },
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=[merged],
        normalized_lineage=lineage,
    )
    assert [u.phase_id for u in assembled.units] == [
        "phase_merged",
        "phase_003",
    ]


def test_assemble_abandoned_skips_unit_including_h1() -> None:
    """Abandoned removes BOTH the H1 envelope AND the H2 phase
    header. The old assembly path operated on H2-only sections;
    leaving a stale H1 envelope above an abandoned phase was the
    fswatch-run-smoke corruption shape this fix exists to prevent."""
    prior = _plan(_unit("phase_001", "A"), _unit("phase_002", "B"))
    lineage = {
        "phases": [{"id": "phase_001", "action": "preserve"}],
        "abandoned": [{"id": "phase_002", "reason": "out of scope"}],
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=(),
        normalized_lineage=lineage,
    )
    assert [u.phase_id for u in assembled.units] == ["phase_001"]
    text = render(assembled)
    # No trace of phase_002 in either H1 or H2 form.
    assert "phase_002" not in text
    # Only one H1 envelope (Phase 0: A).
    assert text.count("# proj — Phase ") == 1


def test_assemble_new_appends_at_end() -> None:
    prior = _plan(_unit("phase_001", "A"))
    new_unit = _unit("phase_002", "New work")
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "new"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=[new_unit],
        normalized_lineage=lineage,
    )
    assert [u.phase_id for u in assembled.units] == ["phase_001", "phase_002"]


def test_assemble_raises_on_duplicate_synth_phase_id() -> None:
    """The council might emit two ``## Phase phase_010: ...`` units
    in one synthesis. Last-write-wins on the dict would silently
    drop the first unit's body, and validate_structure on the
    assembled plan couldn't see the duplicate because only one
    survives. Catch the duplicate at synth_by_id construction time
    so the model-output error is visible."""
    prior = _plan(_unit("phase_001", "A"))
    dup_a = _unit("phase_010", "First", body="- [ ] first body\n")
    dup_b = _unit("phase_010", "Second", body="- [ ] second body\n")
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_010", "action": "new"},
        ]
    }
    with pytest.raises(
        ReauthorAssemblyError, match="duplicate phase id 'phase_010'"
    ):
        assemble_reauthored_plan(
            prior_plan=prior,
            synth_units=[dup_a, dup_b],
            normalized_lineage=lineage,
        )


def test_assemble_raises_when_lineage_references_missing_synth_unit() -> None:
    """Lineage declares phase_002b supersedes phase_002, but the
    synthesizer didn't write a unit for phase_002b. Assembly fails
    fast with ReauthorAssemblyError."""
    prior = _plan(_unit("phase_001", "A"), _unit("phase_002", "B"))
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
        ]
    }
    with pytest.raises(ReauthorAssemblyError, match="phase_002b"):
        assemble_reauthored_plan(
            prior_plan=prior,
            synth_units=(),  # no unit for phase_002b
            normalized_lineage=lineage,
        )


def test_assemble_preamble_carries_through() -> None:
    prior = _plan(
        _unit("phase_001", "A"),
        preamble="# Project\n\nDescription.\n\n",
    )
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=(),
        normalized_lineage=lineage,
    )
    assert assembled.preamble == "# Project\n\nDescription.\n\n"


def test_assemble_project_name_carries_through() -> None:
    """The assembled Plan inherits project_name from the prior plan;
    the renderer uses it to emit consistent H1 envelopes across the
    re-authored output."""
    prior = _plan(_unit("phase_001", "A"))
    prior = Plan(
        project_name="my-special-project",
        preamble=prior.preamble,
        units=prior.units,
    )
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=(),
        normalized_lineage=lineage,
    )
    assert assembled.project_name == "my-special-project"
    text = render(assembled)
    assert "# my-special-project — Phase 0: A" in text


def test_assemble_renumbers_h1_ordinals_after_substitution() -> None:
    """Substituting a unit between two preserved units leaves the
    surrounding units' positions unchanged. The renderer assigns H1
    ordinals 0, 1, 2 by position; the new unit's H1 takes ordinal 1
    automatically."""
    prior = _plan(
        _unit("phase_001", "A"),
        _unit("phase_002", "B-old"),
        _unit("phase_003", "C"),
    )
    new_unit = _unit("phase_002b", "B-new")
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
        ]
    }
    normalized = normalize_lineage_for_preservation(
        ["phase_001", "phase_002", "phase_003"], lineage
    )
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_units=[new_unit],
        normalized_lineage=normalized,
    )
    text = render(assembled)
    # Three H1 envelopes, ordinals 0/1/2 by position.
    assert "Phase 0: A" in text
    assert "Phase 1: B-new" in text
    assert "Phase 2: C" in text
    # No leftover stale "Phase 1: B-old".
    assert "B-old" not in text


# ----------------------------------------------------------------------
# End-to-end: the bug scenario
# ----------------------------------------------------------------------


def test_bug_scenario_partial_synth_with_normalized_lineage_assembles() -> None:
    """Reproduce the directive's bug case: prior plan has 5 phases,
    synthesizer wrote ONLY phase_002b with lineage {phases: [{id:
    phase_002b, action: supersede, from: [phase_002]}]}. With
    normalize + assemble, the result has all 5 phases (4 preserved +
    phase_002b in place of phase_002) and lineage accounts for every
    prior. H1 envelopes are present and renumbered to match new
    positions."""
    prior_text = (
        "# proj — Phase 0: First\n## Phase phase_001: F\n- [ ] a\n"
        "# proj — Phase 1: Second\n## Phase phase_002: S\n- [ ] b\n"
        "# proj — Phase 2: Third\n## Phase phase_003: T\n- [ ] c\n"
        "# proj — Phase 3: Fourth\n## Phase phase_004: Fo\n- [ ] d\n"
        "# proj — Phase 4: Fifth\n## Phase phase_005: Fi\n- [ ] e\n"
    )
    preamble, prior_units = parse_plan_sections(prior_text)
    prior_plan = Plan(
        project_name="proj", preamble=preamble, units=tuple(prior_units)
    )

    synth_text = (
        "# proj — Phase 0: Refactored\n"
        "## Phase phase_002b: Refactored Second\n"
        "- [ ] new b\n"
    )
    _, synth_units = parse_plan_sections(synth_text)

    synth_lineage = {
        "phases": [
            {
                "id": "phase_002b",
                "action": "supersede",
                "from": ["phase_002"],
            }
        ]
    }
    prior_ids = [u.phase_id for u in prior_units]
    normalized = normalize_lineage_for_preservation(prior_ids, synth_lineage)
    assembled = assemble_reauthored_plan(
        prior_plan=prior_plan,
        synth_units=synth_units,
        normalized_lineage=normalized,
    )
    assembled_text = render(assembled)

    # All 5 prior ids represented (4 preserved + phase_002b in
    # place of phase_002), with H1 envelopes at the canonical
    # positions.
    assert "## Phase phase_001:" in assembled_text
    assert "## Phase phase_002:" not in assembled_text  # superseded
    assert "## Phase phase_002b:" in assembled_text
    assert "## Phase phase_003:" in assembled_text
    assert "## Phase phase_004:" in assembled_text
    assert "## Phase phase_005:" in assembled_text

    # H1 ordinals follow the new positions (0..4, contiguous).
    for ordinal in range(5):
        assert f"# proj — Phase {ordinal}:" in assembled_text

    # Lineage now accounts for every prior id.
    accounted: set[str] = set()
    for entry in normalized["phases"]:
        if entry["action"] == "preserve":
            accounted.add(entry["id"])
        elif entry["action"] in ("supersede", "split", "merge"):
            for fid in entry.get("from", []):
                accounted.add(fid)
    for entry in normalized.get("abandoned") or []:
        accounted.add(entry["id"])
    assert accounted == set(prior_ids)
