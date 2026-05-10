"""Tests for duplo.reauthor_assemble.

Pin the contract:
  - parse_plan_sections splits PLAN.md into preamble + per-phase
    sections keyed by phase_id.
  - normalize_lineage_for_preservation adds preserve-default entries
    for prior ids the synthesizer didn't touch, without disturbing
    the synthesizer's explicit non-preserve declarations.
  - assemble_reauthored_plan walks prior phases in order and emits
    each one verbatim (preserve), replaces with synth section
    (supersede / split / merge), or skips (abandoned). New phases
    append at the end.

These tests exercise the assembly module in isolation. The
integration tests in test_reauthor.py check the same contract
end-to-end via reauthor_plan.
"""

from __future__ import annotations

from typing import Any

import pytest

from duplo.reauthor_assemble import (
    PhaseSection,
    ReauthorAssemblyError,
    assemble_reauthored_plan,
    normalize_lineage_for_preservation,
    parse_plan_sections,
)


# ----------------------------------------------------------------------
# parse_plan_sections
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


def test_parse_sections_single_phase() -> None:
    text = (
        "# Project\n\n"
        "## Phase phase_001: First\n\n"
        "- [ ] do thing\n"
    )
    preamble, sections = parse_plan_sections(text)
    assert preamble == "# Project\n\n"
    assert len(sections) == 1
    assert sections[0].id == "phase_001"
    assert sections[0].title == "First"
    assert sections[0].text == "## Phase phase_001: First\n\n- [ ] do thing\n"


def test_parse_sections_multiple_phases() -> None:
    text = (
        "preamble\n\n"
        "## Phase phase_001: A\n\n"
        "- [ ] a-task\n\n"
        "## Phase phase_002: B\n\n"
        "- [ ] b-task\n\n"
        "## Phase phase_003: C\n\n"
        "- [ ] c-task\n"
    )
    preamble, sections = parse_plan_sections(text)
    assert preamble == "preamble\n\n"
    assert [s.id for s in sections] == ["phase_001", "phase_002", "phase_003"]
    # Each section ends with newline; concatenation reconstructs the body.
    assert sections[0].text.startswith("## Phase phase_001: A\n")
    assert sections[1].text.startswith("## Phase phase_002: B\n")
    assert sections[2].text.startswith("## Phase phase_003: C\n")
    # Section text always ends with a newline.
    for s in sections:
        assert s.text.endswith("\n")


def test_parse_sections_concatenation_round_trips() -> None:
    """preamble + sum(section.text) == original text (modulo trailing
    newline normalization)."""
    text = (
        "header\n\n"
        "## Phase phase_001: A\n"
        "- [ ] a\n"
        "## Phase phase_002: B\n"
        "- [ ] b\n"
    )
    preamble, sections = parse_plan_sections(text)
    rebuilt = preamble + "".join(s.text for s in sections)
    assert rebuilt == text


# ----------------------------------------------------------------------
# normalize_lineage_for_preservation
# ----------------------------------------------------------------------


def test_normalize_adds_preserve_for_each_unaccounted_prior() -> None:
    """The bug case: synthesizer's lineage covers only phase_002, the
    other priors must be preserved by default."""
    prior_ids = ["phase_001", "phase_002", "phase_003", "phase_004", "phase_005"]
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
        1 for e in out["phases"] if e["action"] == "preserve" and e["id"] == "phase_001"
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
            {
                "id": "phase_002a",
                "action": "split",
                "from": ["phase_002"],
            },
            {
                "id": "phase_002b",
                "action": "split",
                "from": ["phase_002"],
            },
        ]
    }
    out = normalize_lineage_for_preservation(prior_ids, lineage)
    pids_actions = [(e["id"], e["action"]) for e in out["phases"]]
    assert ("phase_002", "preserve") not in pids_actions
    assert ("phase_001", "preserve") in pids_actions


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
    assert ("phase_001", "preserve") not in pids_actions
    assert ("phase_002", "preserve") not in pids_actions
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


# ----------------------------------------------------------------------
# assemble_reauthored_plan
# ----------------------------------------------------------------------


def _section(pid: str, title: str, body_line: str = "- [ ] task") -> PhaseSection:
    return PhaseSection(
        id=pid,
        title=title,
        text=f"## Phase {pid}: {title}\n\n{body_line}\n\n",
    )


def test_assemble_preserve_emits_prior_section_verbatim() -> None:
    prior = [_section("phase_001", "A"), _section("phase_002", "B")]
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "preserve"},
        ]
    }
    out = assemble_reauthored_plan(
        prior_preamble="header\n\n",
        prior_sections=prior,
        synth_sections=[],
        normalized_lineage=lineage,
    )
    assert out.startswith("header\n\n")
    assert prior[0].text in out
    assert prior[1].text in out


def test_assemble_supersede_replaces_at_prior_position() -> None:
    prior = [
        _section("phase_001", "A", "- [ ] a-task"),
        _section("phase_002", "B", "- [ ] b-task"),
        _section("phase_003", "C", "- [ ] c-task"),
    ]
    new_section = _section("phase_002b", "Refactored", "- [ ] new b")
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    out = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior,
        synth_sections=[new_section],
        normalized_lineage=lineage,
    )
    # Order: phase_001, phase_002b (in place of phase_002), phase_003.
    idx_001 = out.find("## Phase phase_001:")
    idx_002b = out.find("## Phase phase_002b:")
    idx_003 = out.find("## Phase phase_003:")
    assert idx_001 < idx_002b < idx_003
    assert "## Phase phase_002:" not in out
    assert "- [ ] b-task" not in out
    assert "- [ ] new b" in out


def test_assemble_split_emits_all_branches_at_prior_position() -> None:
    prior = [_section("phase_001", "A"), _section("phase_002", "B"), _section("phase_003", "C")]
    branch_a = _section("phase_002a", "Split A")
    branch_b = _section("phase_002b", "Split B")
    lineage = {
        "phases": [
            {"id": "phase_002a", "action": "split", "from": ["phase_002"]},
            {"id": "phase_002b", "action": "split", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    out = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior,
        synth_sections=[branch_a, branch_b],
        normalized_lineage=lineage,
    )
    idx_001 = out.find("## Phase phase_001:")
    idx_002a = out.find("## Phase phase_002a:")
    idx_002b = out.find("## Phase phase_002b:")
    idx_003 = out.find("## Phase phase_003:")
    assert idx_001 < idx_002a < idx_003
    assert idx_001 < idx_002b < idx_003
    assert "## Phase phase_002:" not in out


def test_assemble_merge_emits_target_at_first_source_position() -> None:
    prior = [
        _section("phase_001", "A"),
        _section("phase_002", "B"),
        _section("phase_003", "C"),
    ]
    merged = _section("phase_merged", "Merged")
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
    out = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior,
        synth_sections=[merged],
        normalized_lineage=lineage,
    )
    # phase_merged appears at phase_001's position; phase_002 is gone;
    # phase_003 follows.
    assert out.count("## Phase phase_merged:") == 1
    idx_merged = out.find("## Phase phase_merged:")
    idx_003 = out.find("## Phase phase_003:")
    assert idx_merged < idx_003
    assert "## Phase phase_001:" not in out
    assert "## Phase phase_002:" not in out


def test_assemble_abandoned_skips_prior_section() -> None:
    prior = [_section("phase_001", "A"), _section("phase_002", "B")]
    lineage = {
        "phases": [{"id": "phase_001", "action": "preserve"}],
        "abandoned": [{"id": "phase_002", "reason": "out of scope"}],
    }
    out = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior,
        synth_sections=[],
        normalized_lineage=lineage,
    )
    assert "## Phase phase_001:" in out
    assert "## Phase phase_002:" not in out


def test_assemble_new_appends_at_end() -> None:
    prior = [_section("phase_001", "A")]
    new_section = _section("phase_002", "New work")
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "new"},
        ]
    }
    out = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior,
        synth_sections=[new_section],
        normalized_lineage=lineage,
    )
    idx_001 = out.find("## Phase phase_001:")
    idx_002 = out.find("## Phase phase_002:")
    assert idx_001 < idx_002


def test_assemble_raises_when_lineage_references_missing_synth_section() -> None:
    """Lineage declares phase_002b supersedes phase_002, but the
    synthesizer didn't write a section for phase_002b. Assembly
    fails fast."""
    prior = [_section("phase_001", "A"), _section("phase_002", "B")]
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
        ]
    }
    with pytest.raises(ReauthorAssemblyError) as ei:
        assemble_reauthored_plan(
            prior_preamble="",
            prior_sections=prior,
            synth_sections=[],  # no section for phase_002b
            normalized_lineage=lineage,
        )
    assert "phase_002b" in str(ei.value)


def test_assemble_preamble_is_emitted_first() -> None:
    prior = [_section("phase_001", "A")]
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    out = assemble_reauthored_plan(
        prior_preamble="# Project\n\nDescription.\n\n",
        prior_sections=prior,
        synth_sections=[],
        normalized_lineage=lineage,
    )
    assert out.startswith("# Project\n\nDescription.\n\n")


# ----------------------------------------------------------------------
# End-to-end: the bug scenario
# ----------------------------------------------------------------------


def test_bug_scenario_partial_synth_with_normalized_lineage_assembles() -> None:
    """Reproduce the failure from the directive:

      prior = phase_001..phase_005, synthesizer wrote ONLY phase_002b
      with lineage {phases: [{id: phase_002b, action: supersede,
      from: [phase_002]}]}.

    With normalize + assemble, the result has all 5 phases (4
    preserved + phase_002b in place of phase_002) and lineage
    accounts for every prior."""
    prior_text = (
        "## Phase phase_001: First\n- [ ] a\n"
        "## Phase phase_002: Second\n- [ ] b\n"
        "## Phase phase_003: Third\n- [ ] c\n"
        "## Phase phase_004: Fourth\n- [ ] d\n"
        "## Phase phase_005: Fifth\n- [ ] e\n"
    )
    _, prior_sections = parse_plan_sections(prior_text)

    synth_text = (
        "## Phase phase_002b: Refactored Second\n- [ ] new b\n"
    )
    _, synth_sections = parse_plan_sections(synth_text)

    synth_lineage = {
        "phases": [
            {
                "id": "phase_002b",
                "action": "supersede",
                "from": ["phase_002"],
            }
        ]
    }
    prior_ids = [s.id for s in prior_sections]
    normalized = normalize_lineage_for_preservation(prior_ids, synth_lineage)
    assembled = assemble_reauthored_plan(
        prior_preamble="",
        prior_sections=prior_sections,
        synth_sections=synth_sections,
        normalized_lineage=normalized,
    )

    # All 5 prior ids represented (4 preserved + phase_002b in
    # place of phase_002).
    assert "## Phase phase_001:" in assembled
    assert "## Phase phase_002:" not in assembled  # superseded
    assert "## Phase phase_002b:" in assembled
    assert "## Phase phase_003:" in assembled
    assert "## Phase phase_004:" in assembled
    assert "## Phase phase_005:" in assembled

    # Order matches prior order with phase_002 replaced.
    indices = [
        assembled.find(f"## Phase {pid}:")
        for pid in (
            "phase_001",
            "phase_002b",
            "phase_003",
            "phase_004",
            "phase_005",
        )
    ]
    assert indices == sorted(indices)

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
