"""Tests for duplo.reauthor_assemble after the T-000192 migration.

Pin the contract:
  - ``normalize_lineage_for_preservation`` adds preserve-default
    entries for prior ids the synthesizer didn't touch, without
    disturbing the synthesizer's explicit non-preserve declarations.
  - ``assemble_reauthored_plan`` walks prior phases in order and
    emits each one verbatim (preserve), replaces with a synth phase
    (supersede / split / merge), or skips (abandoned). New phases
    append at the end. Returns a :class:`bob_tools.planfile.Plan`;
    ordinals are renumbered 1..N by position. Substitutions route
    through :func:`bob_tools.planfile.replace_phase_validated` so the
    per-phase canonical checks run at the swap site.

These tests exercise the assembly module in isolation. The
integration tests in ``test_reauthor.py`` check the same contract
end-to-end via ``reauthor_plan``.
"""

from __future__ import annotations

from typing import Any

import pytest

from bob_tools.planfile import (
    Phase,
    Plan,
    Task,
    TaskStatus,
    assert_mcloop_canonical,
    make_task,
    migrate,
    render_plan,
)
from duplo.reauthor_assemble import (
    ReauthorAssemblyError,
    assemble_reauthored_plan,
    normalize_lineage_for_preservation,
    rebuild_phase_constructed,
)


def _task(text: str) -> Task:
    """Return a constructed-mode Task for assembly fixtures."""
    return make_task(text, status=TaskStatus.TODO)


def _phase(phase_id: str, title: str, *tasks: Task) -> Phase:
    """Return a constructed-mode Phase with the given tasks."""
    base = Phase(
        phase_id=phase_id,
        phase_id_source="explicit_comment",
        ordinal=1,
        keyword="Phase",
        title=title,
        prose="",
        subsections=(),
        tasks=tasks or (_task(f"task for {phase_id}"),),
        line_number=0,
    )
    # Route through rebuild_phase_constructed to ensure tasks pass the
    # constructed-mode field-stability harness; ordinals get renumbered
    # by the assembler.
    return rebuild_phase_constructed(base, ordinal=base.ordinal)


def _plan(*phases: Phase, project_title: str = "proj", preamble: str = "") -> Plan:
    return Plan(
        magic_version=1,
        project_title=project_title,
        preamble=preamble,
        phases=tuple(
            Phase(
                phase_id=p.phase_id,
                phase_id_source=p.phase_id_source,
                ordinal=index + 1,
                keyword=p.keyword,
                title=p.title,
                prose=p.prose,
                subsections=p.subsections,
                tasks=p.tasks,
                line_number=p.line_number,
            )
            for index, p in enumerate(phases)
        ),
        bugs=None,
        source_path=None,
    )


# ----------------------------------------------------------------------
# normalize_lineage_for_preservation
# ----------------------------------------------------------------------


def test_normalize_adds_preserve_for_each_unaccounted_prior() -> None:
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
    assert ("phase_002b", "supersede") in pids_actions
    for pid in ("phase_001", "phase_003", "phase_004", "phase_005"):
        assert (pid, "preserve") in pids_actions
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
    assert ("phase_002", "preserve") not in pids_actions
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
    assert ("phase_002", "preserve") not in pids_actions


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
# assemble_reauthored_plan (operates on bob_tools.planfile.Phase tuples)
# ----------------------------------------------------------------------


def test_assemble_preserve_keeps_phases_intact() -> None:
    prior = _plan(
        _phase("phase_001", "A"),
        _phase("phase_002", "B"),
        preamble="header",
    )
    prior = migrate(prior)
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=(),
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == ["phase_001", "phase_002"]
    text = assert_mcloop_canonical(assembled)
    assert "## Phase 1: A" in text
    assert "## Phase 2: B" in text
    assert "<!-- phase_id: phase_001 -->" in text
    assert "<!-- phase_id: phase_002 -->" in text


def test_assemble_supersede_replaces_phase_at_prior_position() -> None:
    prior = _plan(
        _phase("phase_001", "A"),
        _phase("phase_002", "B-old"),
        _phase("phase_003", "C"),
    )
    prior = migrate(prior)
    new_phase = _phase("phase_002b", "B-new", _task("new b"))
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_003", "action": "preserve"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=[new_phase],
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == [
        "phase_001",
        "phase_002b",
        "phase_003",
    ]
    assert [p.ordinal for p in assembled.phases] == [1, 2, 3]
    text = assert_mcloop_canonical(assembled)
    # The replaced phase's heading and body are gone.
    assert "<!-- phase_id: phase_002 -->" not in text
    assert "<!-- phase_id: phase_002b -->" in text
    assert "new b" in text


def test_assemble_split_emits_all_branches_at_prior_position() -> None:
    prior = _plan(
        _phase("phase_001", "A"),
        _phase("phase_002", "B"),
        _phase("phase_003", "C"),
    )
    prior = migrate(prior)
    branch_a = _phase("phase_002a", "Split A")
    branch_b = _phase("phase_002b", "Split B")
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
        synth_phases=[branch_a, branch_b],
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == [
        "phase_001",
        "phase_002a",
        "phase_002b",
        "phase_003",
    ]
    text = render_plan(assembled)
    assert "<!-- phase_id: phase_002 -->" not in text


def test_assemble_merge_emits_target_at_first_source_position() -> None:
    prior = _plan(
        _phase("phase_001", "A"),
        _phase("phase_002", "B"),
        _phase("phase_003", "C"),
    )
    prior = migrate(prior)
    merged = _phase("phase_merged", "Merged")
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
        synth_phases=[merged],
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == [
        "phase_merged",
        "phase_003",
    ]


def test_assemble_abandoned_skips_phase() -> None:
    prior = _plan(_phase("phase_001", "A"), _phase("phase_002", "B"))
    prior = migrate(prior)
    lineage = {
        "phases": [{"id": "phase_001", "action": "preserve"}],
        "abandoned": [{"id": "phase_002", "reason": "out of scope"}],
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=(),
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == ["phase_001"]
    text = render_plan(assembled)
    assert "<!-- phase_id: phase_002 -->" not in text


def test_assemble_new_appends_at_end() -> None:
    prior = _plan(_phase("phase_001", "A"))
    prior = migrate(prior)
    new_phase = _phase("phase_002", "New work")
    lineage = {
        "phases": [
            {"id": "phase_001", "action": "preserve"},
            {"id": "phase_002", "action": "new"},
        ]
    }
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=[new_phase],
        normalized_lineage=lineage,
    )
    assert [p.phase_id for p in assembled.phases] == ["phase_001", "phase_002"]


def test_assemble_raises_on_duplicate_synth_phase_id() -> None:
    prior = _plan(_phase("phase_001", "A"))
    prior = migrate(prior)
    dup_a = _phase("phase_010", "First", _task("first body"))
    dup_b = _phase("phase_010", "Second", _task("second body"))
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
            synth_phases=[dup_a, dup_b],
            normalized_lineage=lineage,
        )


def test_assemble_raises_when_lineage_references_missing_synth_phase() -> None:
    prior = _plan(_phase("phase_001", "A"), _phase("phase_002", "B"))
    prior = migrate(prior)
    lineage = {
        "phases": [
            {"id": "phase_002b", "action": "supersede", "from": ["phase_002"]},
            {"id": "phase_001", "action": "preserve"},
        ]
    }
    with pytest.raises(ReauthorAssemblyError, match="phase_002b"):
        assemble_reauthored_plan(
            prior_plan=prior,
            synth_phases=(),
            normalized_lineage=lineage,
        )


def test_assemble_preamble_carries_through() -> None:
    prior = _plan(_phase("phase_001", "A"), preamble="Description.")
    prior = migrate(prior)
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=(),
        normalized_lineage=lineage,
    )
    assert assembled.preamble == "Description."


def test_assemble_project_title_carries_through() -> None:
    prior = _plan(
        _phase("phase_001", "A"), project_title="my-special-project"
    )
    prior = migrate(prior)
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=(),
        normalized_lineage=lineage,
    )
    assert assembled.project_title == "my-special-project"
    text = render_plan(assembled)
    assert "# my-special-project" in text


def test_assemble_sets_magic_version_one() -> None:
    """The reauthor output is a constructed plan; the magic line must
    be present so strict-mode parse on read-back accepts the file."""
    prior = _plan(_phase("phase_001", "A"))
    prior = migrate(prior)
    # Force prior to legacy (no magic).
    from dataclasses import replace as _dc_replace

    prior_no_magic = _dc_replace(prior, magic_version=None)
    lineage = {"phases": [{"id": "phase_001", "action": "preserve"}]}
    assembled = assemble_reauthored_plan(
        prior_plan=prior_no_magic,
        synth_phases=(),
        normalized_lineage=lineage,
    )
    assert assembled.magic_version == 1


def test_assemble_renumbers_ordinals_after_substitution() -> None:
    prior = _plan(
        _phase("phase_001", "A"),
        _phase("phase_002", "B-old"),
        _phase("phase_003", "C"),
    )
    prior = migrate(prior)
    new_phase = _phase("phase_002b", "B-new")
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
        synth_phases=[new_phase],
        normalized_lineage=normalized,
    )
    assert [p.ordinal for p in assembled.phases] == [1, 2, 3]
    text = render_plan(assembled)
    assert "## Phase 1: A" in text
    assert "## Phase 2: B-new" in text
    assert "## Phase 3: C" in text
    assert "B-old" not in text


# ----------------------------------------------------------------------
# End-to-end: the partial-synth bug scenario
# ----------------------------------------------------------------------


def test_bug_scenario_partial_synth_with_normalized_lineage_assembles() -> None:
    """Prior plan has 5 phases, synthesizer wrote ONLY phase_002b with
    lineage {phases: [{id: phase_002b, action: supersede, from:
    [phase_002]}]}. With normalize + assemble, the result has all 5
    phases (4 preserved + phase_002b in place of phase_002) and lineage
    accounts for every prior."""
    prior = _plan(
        _phase("phase_001", "F"),
        _phase("phase_002", "S"),
        _phase("phase_003", "T"),
        _phase("phase_004", "Fo"),
        _phase("phase_005", "Fi"),
    )
    prior = migrate(prior)

    new_phase = _phase("phase_002b", "Refactored Second", _task("new b"))
    synth_lineage = {
        "phases": [
            {
                "id": "phase_002b",
                "action": "supersede",
                "from": ["phase_002"],
            }
        ]
    }
    prior_ids = [p.phase_id for p in prior.phases if p.phase_id is not None]
    normalized = normalize_lineage_for_preservation(prior_ids, synth_lineage)
    assembled = assemble_reauthored_plan(
        prior_plan=prior,
        synth_phases=[new_phase],
        normalized_lineage=normalized,
    )
    assembled_text = assert_mcloop_canonical(assembled)

    # All 5 prior ids represented (4 preserved + phase_002b in
    # place of phase_002).
    assert "<!-- phase_id: phase_001 -->" in assembled_text
    assert "<!-- phase_id: phase_002 -->" not in assembled_text
    assert "<!-- phase_id: phase_002b -->" in assembled_text
    assert "<!-- phase_id: phase_003 -->" in assembled_text
    assert "<!-- phase_id: phase_004 -->" in assembled_text
    assert "<!-- phase_id: phase_005 -->" in assembled_text

    # Ordinals follow the new positions (1..5, contiguous).
    for ordinal in range(1, 6):
        assert f"## Phase {ordinal}:" in assembled_text

    # Lineage accounts for every prior id.
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
