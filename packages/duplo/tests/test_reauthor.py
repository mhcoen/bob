"""Tests for duplo.reauthor and duplo.reauthor_phase_ids.

Coverage targets:

  - PLAN.md phase header parsing: well-formed headers, lineage
    metadata (supersedes / split_from / merge_from), pre-Slice C
    plans (no phase ids).
  - validate_lineage: preserved id, single-claim per phase,
    referenced prior id exists, duplicate id detection.
  - compute_lineage_diff: deterministic ordering, elision detection.
  - reauthor_plan happy path: triggering crossing lookup, lifecycle
    events emitted FIRST, plan_reauthored last with the right
    ledger_slice_event_ids ordering.
  - reauthor_plan validates synthesizer output: a returned plan with
    fabricated lineage raises LineageValidationError BEFORE any
    events are emitted.
  - reauthor_plan failure modes: missing crossing, wrong-type
    crossing, missing PLAN.md.
  - ledger_slice and design_context shape checks: triggering
    crossing first, per-phase sections, since boundary, fallback
    extraction marker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from duplo.reauthor_phase_ids import (
    LineageValidationError,
    ParsedPhase,
    compute_lineage_diff,
    parse_plan_phases,
    validate_lineage,
)

# The integration tests below need bob_tools (storage, projector,
# etc.) at runtime. Skip those classes when bob_tools is not
# importable in the current interpreter; the parser/validator
# tests above this guard run unconditionally.
try:
    import bob_tools.ledger  # noqa: F401
    _BOB_TOOLS_AVAILABLE = True
except ImportError:
    _BOB_TOOLS_AVAILABLE = False

needs_bob_tools = pytest.mark.skipif(
    not _BOB_TOOLS_AVAILABLE,
    reason="reauthor tests require the 'bob_tools' package",
)


# ---------------------------------------------------------------------
# Phase-ID parser
# ---------------------------------------------------------------------


class TestParsePlanPhases:
    def test_single_header_no_lineage(self) -> None:
        text = "## Phase phase_001: Bring up scaffold\n"
        phases = parse_plan_phases(text)
        assert len(phases) == 1
        assert phases[0].id == "phase_001"
        assert phases[0].title == "Bring up scaffold"
        assert phases[0].supersedes == []
        assert phases[0].split_from == []
        assert phases[0].merge_from == []

    def test_supersedes_metadata(self) -> None:
        text = (
            "## Phase phase_002b: Refactored auth\n"
            "<!-- supersedes: phase_002 -->\n"
        )
        phases = parse_plan_phases(text)
        assert phases[0].supersedes == ["phase_002"]

    def test_split_from_two_branches(self) -> None:
        text = (
            "## Phase phase_002a: Auth foundation (split)\n"
            "<!-- split_from: phase_002 -->\n"
            "\n"
            "## Phase phase_002b: Token refresh (split)\n"
            "<!-- split_from: phase_002 -->\n"
        )
        phases = parse_plan_phases(text)
        assert len(phases) == 2
        assert phases[0].split_from == ["phase_002"]
        assert phases[1].split_from == ["phase_002"]

    def test_merge_from_multiple_parents(self) -> None:
        text = (
            "## Phase phase_merged_x: Combined feature flag\n"
            "<!-- merge_from: phase_003, phase_004 -->\n"
        )
        phases = parse_plan_phases(text)
        assert phases[0].merge_from == ["phase_003", "phase_004"]

    def test_pre_slice_c_headers_ignored(self) -> None:
        # Pre-Slice C plans use `# stopwatch -- Phase 1: ...`. The
        # parser deliberately recognizes only the strict format so
        # the validator can treat such plans as "no phase ids" and
        # fall through to fresh-id labeling.
        text = (
            "# stopwatch -- Phase 1: Stopwatch core\n"
            "\n"
            "- [ ] do thing\n"
        )
        phases = parse_plan_phases(text)
        assert phases == []

    def test_lineage_comment_after_blank_line(self) -> None:
        # The metadata block is permissive on blank lines but
        # non-blank, non-comment content closes it.
        text = (
            "## Phase phase_002: x\n"
            "\n"
            "<!-- supersedes: phase_001 -->\n"
        )
        phases = parse_plan_phases(text)
        assert phases[0].supersedes == ["phase_001"]

    def test_intervening_body_closes_metadata_block(self) -> None:
        text = (
            "## Phase phase_002: x\n"
            "Some body text.\n"
            "<!-- supersedes: phase_001 -->\n"
        )
        phases = parse_plan_phases(text)
        assert phases[0].supersedes == []

    def test_multiple_phases(self) -> None:
        text = (
            "## Phase phase_001: First\n"
            "\n"
            "body\n"
            "\n"
            "## Phase phase_002: Second\n"
            "<!-- supersedes: phase_001 -->\n"
        )
        phases = parse_plan_phases(text)
        assert [p.id for p in phases] == ["phase_001", "phase_002"]
        assert phases[1].supersedes == ["phase_001"]


# ---------------------------------------------------------------------
# Lineage validation
# ---------------------------------------------------------------------


def _phase(
    pid: str,
    *,
    supersedes: list[str] | None = None,
    split_from: list[str] | None = None,
    merge_from: list[str] | None = None,
) -> ParsedPhase:
    return ParsedPhase(
        id=pid,
        title=pid,
        header_line_index=0,
        supersedes=list(supersedes or []),
        split_from=list(split_from or []),
        merge_from=list(merge_from or []),
    )


class TestValidateLineage:
    def test_preserved_id_passes(self) -> None:
        old = [_phase("phase_001"), _phase("phase_002")]
        new = [_phase("phase_001"), _phase("phase_002")]
        validate_lineage(old, new)

    def test_supersedes_passes(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_002", supersedes=["phase_001"])]
        validate_lineage(old, new)

    def test_split_from_passes(self) -> None:
        old = [_phase("phase_001")]
        new = [
            _phase("phase_001a", split_from=["phase_001"]),
            _phase("phase_001b", split_from=["phase_001"]),
        ]
        validate_lineage(old, new)

    def test_merge_from_passes(self) -> None:
        old = [_phase("a"), _phase("b")]
        new = [_phase("c", merge_from=["a", "b"])]
        validate_lineage(old, new)

    def test_brand_new_id_without_metadata_rejected(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_001"), _phase("phase_999")]
        with pytest.raises(LineageValidationError, match="phase_999"):
            validate_lineage(old, new)

    def test_unknown_predecessor_rejected(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_002", supersedes=["ghost"])]
        with pytest.raises(LineageValidationError, match="ghost"):
            validate_lineage(old, new)

    def test_mixed_lineage_metadata_rejected(self) -> None:
        old = [_phase("phase_001"), _phase("phase_002")]
        new = [
            _phase(
                "phase_003",
                supersedes=["phase_001"],
                split_from=["phase_002"],
            )
        ]
        with pytest.raises(LineageValidationError, match="more than one"):
            validate_lineage(old, new)

    def test_preserved_id_with_extra_metadata_rejected(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_001", supersedes=["phase_001"])]
        with pytest.raises(LineageValidationError, match="preserved"):
            validate_lineage(old, new)

    def test_duplicate_id_in_new_plan_rejected(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_001"), _phase("phase_001")]
        with pytest.raises(
            LineageValidationError, match="more than once"
        ):
            validate_lineage(old, new)

    def test_elision_does_not_raise(self) -> None:
        # A prior id with no successor is recorded as
        # phase_abandoned by the emitter; the validator does not
        # reject it.
        old = [_phase("phase_001"), _phase("phase_002")]
        new = [_phase("phase_001")]
        validate_lineage(old, new)


# ---------------------------------------------------------------------
# Lineage diff computation
# ---------------------------------------------------------------------


class TestComputeLineageDiff:
    def test_supersession(self) -> None:
        old = [_phase("phase_001")]
        new = [_phase("phase_001b", supersedes=["phase_001"])]
        diff = compute_lineage_diff(old, new)
        assert diff.superseded == [("phase_001", "phase_001b")]
        assert diff.split == []
        assert diff.merged == []
        assert diff.elided == []

    def test_split_groups_branches(self) -> None:
        old = [_phase("phase_001")]
        new = [
            _phase("phase_001b", split_from=["phase_001"]),
            _phase("phase_001a", split_from=["phase_001"]),
        ]
        diff = compute_lineage_diff(old, new)
        # Branches sorted within the bucket.
        assert diff.split == [("phase_001", ["phase_001a", "phase_001b"])]

    def test_merge(self) -> None:
        old = [_phase("a"), _phase("b")]
        new = [_phase("c", merge_from=["b", "a"])]
        diff = compute_lineage_diff(old, new)
        assert diff.merged == [(["b", "a"], "c")]

    def test_elision(self) -> None:
        old = [_phase("phase_001"), _phase("phase_002")]
        new = [_phase("phase_001")]
        diff = compute_lineage_diff(old, new)
        assert diff.elided == ["phase_002"]
        assert diff.superseded == []

    def test_deterministic_sorted_output(self) -> None:
        old = [_phase("a"), _phase("b"), _phase("c"), _phase("d")]
        new = [
            _phase("z", supersedes=["c"]),
            _phase("y", supersedes=["a"]),
        ]
        diff = compute_lineage_diff(old, new)
        # Sorted by (old_id, new_id).
        assert diff.superseded == [("a", "y"), ("c", "z")]
        # b and d are elided -- sorted ascending.
        assert diff.elided == ["b", "d"]


# ---------------------------------------------------------------------
# reauthor_plan integration tests (orchestra-dependent)
# ---------------------------------------------------------------------


@needs_bob_tools
class TestReauthorPlan:
    def _seed_ledger(
        self,
        ledger_dir: Path,
        plan_phases: list[str],
    ) -> tuple[str, str, str]:
        """Seed a ledger with phases from `plan_phases`, an
        unattributable_commit, and a recorded threshold_crossed
        event for that crossing. Returns
        (crossing_event_id, plan_started_id, commit_event_id)."""
        from bob_tools.ledger import (
            EventType,
            Storage,
            evaluate_thresholds,
            project,
            record_crossings,
        )
        from bob_tools.ledger.events import (
            make_commit_landed_payload,
            make_phase_started_payload,
        )
        from bob_tools.ledger.thresholds import ThresholdParams
        from bob_tools.ledger import CommitChangeClass

        storage = Storage(ledger_dir, writer_id="seed")
        first_phase_id: str | None = None
        commit_id: str | None = None
        for phase_id in plan_phases:
            ev = storage.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(
                    phase_id=phase_id, title=f"Phase {phase_id}"
                ),
                run_id="seed",
            )
            if first_phase_id is None:
                first_phase_id = ev.event_id

        # Add an unattributable commit so threshold rule 1 fires.
        commit_ev = storage.append(
            event_type=EventType.COMMIT_LANDED,
            payload=make_commit_landed_payload(
                commit="abc12345",
                parent_commits=[],
                branch=None,
                author="m",
                subject="ad hoc",
                attributed_phase_id=None,
                files_changed=1,
                lines_added=1,
                lines_removed=0,
                change_class=CommitChangeClass.CODE,
            ),
            run_id="seed",
        )
        commit_id = commit_ev.event_id

        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        emitted = record_crossings(storage, crossings, run_id="seed")
        assert emitted, "expected at least one threshold_crossed event"

        return emitted[0], first_phase_id or "", commit_id

    def _write_old_plan(self, plan_path: Path, phase_ids: list[str]) -> None:
        body_lines: list[str] = []
        for pid in phase_ids:
            body_lines.append(f"## Phase {pid}: {pid} title")
            body_lines.append("")
            body_lines.append(f"- [ ] do {pid} thing")
            body_lines.append("")
        plan_path.write_text("\n".join(body_lines), encoding="utf-8")

    def _patch_council(
        self, monkeypatch: pytest.MonkeyPatch, new_plan_text: str
    ) -> None:
        """Replace the council invocation with a fake that returns
        the supplied plan text without calling any LLM."""
        from duplo import reauthor

        def fake_invoke(**kwargs: Any) -> str:
            return new_plan_text + ("\n" if not new_plan_text.endswith("\n") else "")

        monkeypatch.setattr(reauthor, "_invoke_council_for_reauthor", fake_invoke)

    def test_happy_path_emits_lifecycle_then_plan_reauthored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bob_tools.ledger import EventType, Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])

        new_plan = (
            "## Phase phase_001: Phase phase_001 title (preserved)\n"
            "\n"
            "- [ ] retained\n"
            "\n"
            "## Phase phase_002b: Refactored phase_002\n"
            "<!-- supersedes: phase_002 -->\n"
            "\n"
            "- [ ] new work\n"
        )
        self._patch_council(monkeypatch, new_plan)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        # PLAN.md was overwritten with the synthesizer output.
        assert plan_path.read_text() == new_plan

        # Lineage diff captured the supersession and no elision.
        assert result.lineage_diff.superseded == [
            ("phase_002", "phase_002b")
        ]
        assert result.lineage_diff.elided == []

        # Ledger state: lifecycle events appended FIRST, then
        # plan_reauthored last, in event-id order.
        reader = Storage(ledger_dir, writer_id="check")
        all_events = reader.read_all()
        all_events.sort(key=lambda e: e.event_id)
        # The most-recent two events emitted by the duplo-reauthor
        # writer should be one phase_superseded then one
        # plan_reauthored.
        reauthor_writer_events = [
            e for e in all_events if e.writer_id.startswith("duplo-reauthor")
        ]
        types = [e.type for e in reauthor_writer_events]
        assert types == [
            EventType.PHASE_SUPERSEDED,
            EventType.PLAN_REAUTHORED,
        ]

        # plan_reauthored payload references the lifecycle event +
        # the triggering crossing.
        plan_reauthored_ev = reauthor_writer_events[-1]
        ledger_slice = plan_reauthored_ev.payload["ledger_slice_event_ids"]
        assert ledger_slice[0] == crossing_id
        assert ledger_slice[1] == result.lifecycle_event_ids[0]
        assert plan_reauthored_ev.payload["trigger_event_id"] == crossing_id

    def test_elision_emits_phase_abandoned(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bob_tools.ledger import EventType, Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])

        new_plan = (
            "## Phase phase_001: Phase phase_001 title (preserved)\n"
            "\n"
            "- [ ] retained\n"
        )
        self._patch_council(monkeypatch, new_plan)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        assert result.lineage_diff.elided == ["phase_002"]

        reader = Storage(ledger_dir, writer_id="check")
        all_events = reader.read_all()
        reauthor_writer_events = [
            e
            for e in sorted(all_events, key=lambda e: e.event_id)
            if e.writer_id.startswith("duplo-reauthor")
        ]
        # phase_abandoned (for phase_002) emitted before plan_reauthored.
        assert [e.type for e in reauthor_writer_events] == [
            EventType.PHASE_ABANDONED,
            EventType.PLAN_REAUTHORED,
        ]
        abandon = reauthor_writer_events[0]
        assert abandon.payload["phase_id"] == "phase_002"
        assert abandon.payload["reason"] == "elided in re-author"

    def test_validation_failure_emits_no_events(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001"])

        # Synthesizer returns a brand-new id without lineage metadata.
        bad_plan = "## Phase phase_002: New phase\n\n- [ ] x\n"
        self._patch_council(monkeypatch, bad_plan)

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )

        from duplo.reauthor import reauthor_plan

        with pytest.raises(LineageValidationError):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        # Ledger unchanged: no lifecycle events, no plan_reauthored
        # appended after the seed.
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)
        # And PLAN.md was not overwritten.
        assert "phase_002" not in plan_path.read_text()

    def test_missing_crossing_event_raises(
        self,
        tmp_path: Path,
    ) -> None:
        from duplo.reauthor import ReauthorError, reauthor_plan

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True)
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("## Phase phase_001: x\n\n- [ ] x\n")

        with pytest.raises(ReauthorError, match="not found"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id="00000000-0000-7000-8000-000000000000",
                project_dir=tmp_path,
            )

    def test_wrong_event_type_raises(
        self,
        tmp_path: Path,
    ) -> None:
        from bob_tools.ledger import EventType, Storage
        from bob_tools.ledger.events import make_phase_started_payload
        from duplo.reauthor import ReauthorError, reauthor_plan

        ledger_dir = tmp_path / "ledger"
        storage = Storage(ledger_dir, writer_id="seed")
        ev = storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="seed",
        )
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("## Phase p1: x\n\n- [ ] x\n")

        with pytest.raises(ReauthorError, match="threshold_crossed"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=ev.event_id,
                project_dir=tmp_path,
            )

    def test_missing_plan_path_raises(
        self,
        tmp_path: Path,
    ) -> None:
        from duplo.reauthor import ReauthorError, reauthor_plan

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        plan_path = tmp_path / "DOES_NOT_EXIST.md"

        with pytest.raises(ReauthorError, match="plan not found"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id="00000000-0000-7000-8000-000000000000",
                project_dir=tmp_path,
            )


# ---------------------------------------------------------------------
# ledger_slice + design_context shape
# ---------------------------------------------------------------------


@needs_bob_tools
class TestLedgerSliceShape:
    def test_ledger_slice_starts_with_triggering_crossing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

        def fake_invoke(**kwargs: Any) -> str:
            captured["ledger_slice"] = kwargs["ledger_slice_md"]
            captured["design_context"] = kwargs["design_context_md"]
            return (
                "## Phase phase_001: Phase phase_001 title (preserved)\n"
                "\n"
                "- [ ] x\n"
            )

        # Seed and patch.
        from duplo.reauthor import reauthor_plan
        from duplo import reauthor

        # Build the test data inline so we can patch easily.
        from bob_tools.ledger import (
            CommitChangeClass,
            EventType,
            Storage,
            evaluate_thresholds,
            project,
            record_crossings,
        )
        from bob_tools.ledger.events import (
            make_commit_landed_payload,
            make_phase_started_payload,
        )
        from bob_tools.ledger.thresholds import ThresholdParams

        ledger_dir = tmp_path / "ledger"
        storage = Storage(ledger_dir, writer_id="seed")
        storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(
                phase_id="phase_001", title="t"
            ),
            run_id="seed",
        )
        storage.append(
            event_type=EventType.COMMIT_LANDED,
            payload=make_commit_landed_payload(
                commit="abc12345",
                parent_commits=[],
                branch=None,
                author="m",
                subject="x",
                attributed_phase_id=None,
                files_changed=1,
                lines_added=1,
                lines_removed=0,
                change_class=CommitChangeClass.CODE,
            ),
            run_id="seed",
        )
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        emitted = record_crossings(storage, crossings, run_id="seed")

        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "## Phase phase_001: Phase phase_001 title\n\n- [ ] x\n"
        )

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=emitted[0],
            project_dir=tmp_path,
        )

        slice_md = captured["ledger_slice"]
        # Triggering crossing comes first.
        assert (
            slice_md.find("Triggering threshold crossing")
            < slice_md.find("Since boundary")
            < slice_md.find("Phases (current)")
        )
        assert f"crossing_event_id: {emitted[0]}" in slice_md
        # Per-phase section includes the phase id.
        assert "Phase phase_001" in slice_md

    def test_design_context_marks_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

        def fake_invoke(**kwargs: Any) -> str:
            captured["design_context"] = kwargs["design_context_md"]
            return (
                "## Phase phase_001: Phase phase_001 title\n"
                "\n- [ ] x\n"
            )

        from duplo import reauthor
        from bob_tools.ledger import (
            CommitChangeClass,
            EventType,
            Storage,
            evaluate_thresholds,
            project,
            record_crossings,
        )
        from bob_tools.ledger.events import (
            make_commit_landed_payload,
            make_phase_started_payload,
        )
        from bob_tools.ledger.thresholds import ThresholdParams
        from duplo.reauthor import reauthor_plan

        ledger_dir = tmp_path / "ledger"
        storage = Storage(ledger_dir, writer_id="seed")
        storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(
                phase_id="phase_001", title="t"
            ),
            run_id="seed",
        )
        storage.append(
            event_type=EventType.COMMIT_LANDED,
            payload=make_commit_landed_payload(
                commit="abc12345",
                parent_commits=[],
                branch=None,
                author="m",
                subject="x",
                attributed_phase_id=None,
                files_changed=1,
                lines_added=1,
                lines_removed=0,
                change_class=CommitChangeClass.CODE,
            ),
            run_id="seed",
        )
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        emitted = record_crossings(storage, crossings, run_id="seed")

        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "## Phase phase_001: Phase phase_001 title\n"
            "\n"
            "## Constraints\n"
            "\n"
            "Network access is restricted.\n"
        )

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )
        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=emitted[0],
            project_dir=tmp_path,
        )

        ctx = captured["design_context"]
        # No design_reasoning_recorded events were emitted, so the
        # plan-text fallback section is the one source of context.
        assert "plan_text_best_effort" in ctx
        assert "Constraints" in ctx
