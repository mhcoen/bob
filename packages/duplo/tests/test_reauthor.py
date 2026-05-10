"""Tests for duplo.reauthor and duplo.reauthor_phase_ids.

Coverage targets after the JSON-sidecar refactor:

  - PLAN.md phase header parsing: well-formed headers (id + title
    only), pre-Slice C plans (no phase ids), multiple phases.
  - validate_lineage: each of the 11 semantic rules has a positive
    and a negative test (per-action constraints, exactly-once
    accounting, abandoned-not-also-preserved, no-preserved-in-from,
    plan-headers vs phases[] one-to-one).
  - compute_lineage_diff: every action type produces the right
    LineageDiff entry; output is sorted deterministically.
  - reauthor_plan happy path: triggering crossing lookup, lifecycle
    events emitted FIRST, plan_reauthored last with the right
    ledger_slice_event_ids ordering.
  - reauthor_plan validates synthesizer output: a verdict missing
    its lineage object, or a sidecar with bad lineage, raises
    LineageValidationError BEFORE any events are emitted.
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
    ParsedHeader,
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
# Phase-header parser
# ---------------------------------------------------------------------


class TestParsePlanPhases:
    def test_single_header(self) -> None:
        text = "## Phase phase_001: Bring up scaffold\n"
        phases = parse_plan_phases(text)
        assert len(phases) == 1
        assert phases[0].id == "phase_001"
        assert phases[0].title == "Bring up scaffold"
        assert phases[0].header_line_index == 0

    def test_multiple_headers_in_order(self) -> None:
        text = (
            "## Phase phase_001: First\n"
            "\n"
            "body\n"
            "\n"
            "## Phase phase_002: Second\n"
            "\n"
            "## Phase phase_003: Third\n"
        )
        phases = parse_plan_phases(text)
        assert [p.id for p in phases] == ["phase_001", "phase_002", "phase_003"]
        assert phases[0].title == "First"
        assert phases[2].title == "Third"

    def test_pre_slice_c_headers_ignored(self) -> None:
        text = (
            "# stopwatch -- Phase 1: Stopwatch core\n"
            "\n"
            "- [ ] do thing\n"
        )
        phases = parse_plan_phases(text)
        assert phases == []

    def test_html_comments_no_longer_parsed(self) -> None:
        # The HTML-comment lineage protocol is gone. The parser
        # ignores any inline comments and returns only header info;
        # lineage lives in the verdict JSON now.
        text = (
            "## Phase phase_002b: Refactored auth\n"
            "<!-- supersedes: phase_002 -->\n"
        )
        phases = parse_plan_phases(text)
        assert len(phases) == 1
        assert phases[0].id == "phase_002b"
        # No supersedes / split_from / merge_from on the parsed
        # header (the dataclass does not carry them anymore).
        assert not hasattr(phases[0], "supersedes")
        assert not hasattr(phases[0], "split_from")
        assert not hasattr(phases[0], "merge_from")

    def test_header_with_trailing_whitespace(self) -> None:
        text = "## Phase phase_001: First   \n"
        phases = parse_plan_phases(text)
        assert phases[0].id == "phase_001"
        assert phases[0].title == "First"


# ---------------------------------------------------------------------
# Sidecar fixtures and helpers
# ---------------------------------------------------------------------


def _phase_entry(
    pid: str, action: str, *, from_: list[str] | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": pid, "action": action}
    if from_ is not None:
        entry["from"] = list(from_)
    return entry


def _abandoned_entry(pid: str, reason: str) -> dict[str, Any]:
    return {"id": pid, "reason": reason}


def _sidecar(
    *,
    phases: list[dict[str, Any]],
    abandoned: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"phases": phases}
    if abandoned is not None:
        out["abandoned"] = abandoned
    return out


# ---------------------------------------------------------------------
# Lineage validation
# ---------------------------------------------------------------------


class TestValidateLineagePositive:
    def test_all_preserved(self) -> None:
        validate_lineage(
            ["phase_001", "phase_002"],
            ["phase_001", "phase_002"],
            _sidecar(
                phases=[
                    _phase_entry("phase_001", "preserve"),
                    _phase_entry("phase_002", "preserve"),
                ]
            ),
        )

    def test_supersede(self) -> None:
        validate_lineage(
            ["phase_001"],
            ["phase_001b"],
            _sidecar(
                phases=[
                    _phase_entry(
                        "phase_001b", "supersede", from_=["phase_001"]
                    )
                ]
            ),
        )

    def test_split_two_branches(self) -> None:
        validate_lineage(
            ["phase_001"],
            ["phase_001a", "phase_001b"],
            _sidecar(
                phases=[
                    _phase_entry("phase_001a", "split", from_=["phase_001"]),
                    _phase_entry("phase_001b", "split", from_=["phase_001"]),
                ]
            ),
        )

    def test_merge_two_priors(self) -> None:
        validate_lineage(
            ["a", "b"],
            ["c"],
            _sidecar(
                phases=[_phase_entry("c", "merge", from_=["a", "b"])]
            ),
        )

    def test_new_phase(self) -> None:
        validate_lineage(
            ["phase_001"],
            ["phase_001", "phase_002"],
            _sidecar(
                phases=[
                    _phase_entry("phase_001", "preserve"),
                    _phase_entry("phase_002", "new"),
                ]
            ),
        )

    def test_abandoned_prior(self) -> None:
        validate_lineage(
            ["phase_001", "phase_002"],
            ["phase_001"],
            _sidecar(
                phases=[_phase_entry("phase_001", "preserve")],
                abandoned=[_abandoned_entry("phase_002", "out of scope")],
            ),
        )

    def test_fresh_authoring_all_new(self) -> None:
        validate_lineage(
            [],
            ["phase_001", "phase_002"],
            _sidecar(
                phases=[
                    _phase_entry("phase_001", "new"),
                    _phase_entry("phase_002", "new"),
                ]
            ),
        )


class TestValidateLineageRejections:
    def test_preserve_with_unknown_id(self) -> None:
        with pytest.raises(LineageValidationError, match="not in the prior plan"):
            validate_lineage(
                ["phase_001"],
                ["phase_999"],
                _sidecar(
                    phases=[_phase_entry("phase_999", "preserve")]
                ),
            )

    def test_preserve_with_from_field(self) -> None:
        with pytest.raises(LineageValidationError, match="must not include"):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[
                        _phase_entry(
                            "phase_001", "preserve", from_=["phase_001"]
                        )
                    ]
                ),
            )

    def test_new_with_existing_id(self) -> None:
        with pytest.raises(LineageValidationError, match="already exists"):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "new")]
                ),
            )

    def test_new_with_from_field(self) -> None:
        with pytest.raises(LineageValidationError, match="must not include"):
            validate_lineage(
                ["phase_001"],
                ["phase_001", "phase_002"],
                _sidecar(
                    phases=[
                        _phase_entry("phase_001", "preserve"),
                        _phase_entry("phase_002", "new", from_=["phase_001"]),
                    ]
                ),
            )

    def test_supersede_without_from(self) -> None:
        with pytest.raises(LineageValidationError, match="non-empty 'from'"):
            validate_lineage(
                ["phase_001"],
                ["phase_002"],
                _sidecar(
                    phases=[_phase_entry("phase_002", "supersede")]
                ),
            )

    def test_supersede_with_unknown_predecessor(self) -> None:
        with pytest.raises(LineageValidationError, match="unknown prior plan"):
            validate_lineage(
                ["phase_001"],
                ["phase_002"],
                _sidecar(
                    phases=[
                        _phase_entry("phase_002", "supersede", from_=["ghost"])
                    ]
                ),
            )

    def test_supersede_reusing_existing_id(self) -> None:
        with pytest.raises(LineageValidationError, match="already exists"):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[
                        _phase_entry(
                            "phase_001", "supersede", from_=["phase_001"]
                        )
                    ]
                ),
            )

    def test_split_without_from(self) -> None:
        with pytest.raises(LineageValidationError, match="non-empty 'from'"):
            validate_lineage(
                ["phase_001"],
                ["phase_002"],
                _sidecar(
                    phases=[_phase_entry("phase_002", "split")]
                ),
            )

    def test_merge_with_only_one_prior(self) -> None:
        with pytest.raises(LineageValidationError, match="at least two"):
            validate_lineage(
                ["a"],
                ["c"],
                _sidecar(
                    phases=[_phase_entry("c", "merge", from_=["a"])]
                ),
            )

    def test_duplicate_id_in_phases(self) -> None:
        with pytest.raises(LineageValidationError, match="duplicate id"):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[
                        _phase_entry("phase_001", "preserve"),
                        _phase_entry("phase_001", "preserve"),
                    ]
                ),
            )

    def test_duplicate_id_in_plan_headers(self) -> None:
        with pytest.raises(LineageValidationError, match="duplicate phase id in plan"):
            validate_lineage(
                ["phase_001"],
                ["phase_001", "phase_001"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "preserve")]
                ),
            )

    def test_phases_missing_for_plan_header(self) -> None:
        with pytest.raises(
            LineageValidationError,
            match="missing an entry for plan header",
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_001", "phase_002"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "preserve")]
                ),
            )

    def test_phases_has_entry_with_no_plan_header(self) -> None:
        with pytest.raises(
            LineageValidationError,
            match="entries with no plan header",
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[
                        _phase_entry("phase_001", "preserve"),
                        _phase_entry("phantom", "new"),
                    ]
                ),
            )

    def test_unknown_action(self) -> None:
        with pytest.raises(LineageValidationError, match="not one of"):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[
                        {"id": "phase_001", "action": "fork"}
                    ]
                ),
            )

    def test_abandoned_id_not_in_prior(self) -> None:
        with pytest.raises(
            LineageValidationError, match="not a prior plan id"
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "preserve")],
                    abandoned=[_abandoned_entry("ghost", "n/a")],
                ),
            )

    def test_abandoned_also_preserved(self) -> None:
        with pytest.raises(
            LineageValidationError,
            match="appears as a preserved id",
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_001"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "preserve")],
                    abandoned=[_abandoned_entry("phase_001", "n/a")],
                ),
            )

    def test_abandoned_also_in_from(self) -> None:
        with pytest.raises(
            LineageValidationError,
            match="from",
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_001b"],
                _sidecar(
                    phases=[
                        _phase_entry(
                            "phase_001b", "supersede", from_=["phase_001"]
                        )
                    ],
                    abandoned=[_abandoned_entry("phase_001", "n/a")],
                ),
            )

    def test_prior_id_not_accounted_for(self) -> None:
        with pytest.raises(
            LineageValidationError, match="not accounted for"
        ):
            validate_lineage(
                ["phase_001", "phase_002"],
                ["phase_001"],
                _sidecar(
                    phases=[_phase_entry("phase_001", "preserve")]
                ),
            )

    def test_prior_id_double_superseded(self) -> None:
        # Two supersede entries claiming the same prior is a
        # contradiction (one phase fully replaces another, not two).
        # Multiple split entries sharing a prior is the natural
        # split case and remains allowed -- see
        # TestValidateLineagePositive.test_split_two_branches.
        with pytest.raises(
            LineageValidationError, match="multiple entries"
        ):
            validate_lineage(
                ["phase_001"],
                ["phase_002", "phase_003"],
                _sidecar(
                    phases=[
                        _phase_entry(
                            "phase_002", "supersede", from_=["phase_001"]
                        ),
                        _phase_entry(
                            "phase_003", "supersede", from_=["phase_001"]
                        ),
                    ]
                ),
            )

    def test_lineage_phases_must_be_list(self) -> None:
        with pytest.raises(
            LineageValidationError, match="phases must be a list"
        ):
            validate_lineage(
                [], [], {"phases": "not a list"}
            )

    def test_lineage_must_be_object(self) -> None:
        with pytest.raises(
            LineageValidationError, match="must be a JSON object"
        ):
            validate_lineage([], [], "not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# Lineage diff computation
# ---------------------------------------------------------------------


class TestComputeLineageDiff:
    def test_supersede(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[
                    _phase_entry(
                        "phase_001b", "supersede", from_=["phase_001"]
                    )
                ]
            )
        )
        assert diff.superseded == [("phase_001", "phase_001b")]
        assert diff.split == []
        assert diff.merged == []
        assert diff.abandoned == []

    def test_split_groups_branches(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[
                    _phase_entry("phase_001b", "split", from_=["phase_001"]),
                    _phase_entry("phase_001a", "split", from_=["phase_001"]),
                ]
            )
        )
        # Branches sorted within the bucket.
        assert diff.split == [("phase_001", ["phase_001a", "phase_001b"])]

    def test_merge(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[_phase_entry("c", "merge", from_=["b", "a"])]
            )
        )
        # The synthesizer's declared 'from' order is preserved on the
        # merged entry; the audit trail matches what the synthesizer
        # wrote even though sorting is used between merged entries.
        assert diff.merged == [(["b", "a"], "c")]

    def test_abandoned(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[],
                abandoned=[
                    _abandoned_entry("phase_002", "out of scope"),
                    _abandoned_entry("phase_001", "deferred"),
                ],
            )
        )
        # Sorted by id.
        assert diff.abandoned == [
            ("phase_001", "deferred"),
            ("phase_002", "out of scope"),
        ]

    def test_deterministic_supersede_sort(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[
                    _phase_entry("z", "supersede", from_=["c"]),
                    _phase_entry("y", "supersede", from_=["a"]),
                ]
            )
        )
        assert diff.superseded == [("a", "y"), ("c", "z")]

    def test_preserve_and_new_produce_no_diff_entries(self) -> None:
        diff = compute_lineage_diff(
            _sidecar(
                phases=[
                    _phase_entry("phase_001", "preserve"),
                    _phase_entry("phase_002", "new"),
                ]
            )
        )
        assert diff.superseded == []
        assert diff.split == []
        assert diff.merged == []
        assert diff.abandoned == []


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
        self,
        monkeypatch: pytest.MonkeyPatch,
        new_plan_text: str,
        verdict: dict[str, Any],
    ) -> None:
        """Replace the council invocation with a fake that returns
        the supplied (plan_text, verdict) tuple without calling any
        LLM. The fake captures the prompt inputs so call-site tests
        can also peek at ledger_slice / design_context."""
        from duplo import reauthor

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            text = new_plan_text + (
                "\n" if not new_plan_text.endswith("\n") else ""
            )
            return text, dict(verdict)

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

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
            "\n"
            "- [ ] new work\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {"id": "phase_001", "action": "preserve"},
                    {
                        "id": "phase_002b",
                        "action": "supersede",
                        "from": ["phase_002"],
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, new_plan, verdict)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        # Preserve-by-default assembly: Duplo wraps the synthesizer's
        # output in the deterministic envelope. The PRESERVED phase's
        # markdown comes from the prior plan verbatim (the synthesizer's
        # reproduction of phase_001 is discarded — preserve means
        # preserve, not "model rewrites it again"); the SUPERSEDED
        # phase's markdown comes from the synthesizer's new section.
        assembled = plan_path.read_text()
        # Prior phase_001 content survives.
        assert "## Phase phase_001: phase_001 title" in assembled
        assert "- [ ] do phase_001 thing" in assembled
        # The synthesizer's reproduction of phase_001 was discarded.
        assert "(preserved)" not in assembled
        assert "- [ ] retained" not in assembled
        # The new phase_002b section came from synth.
        assert "## Phase phase_002b: Refactored phase_002" in assembled
        assert "- [ ] new work" in assembled
        # And the prior phase_002 content was replaced.
        assert "- [ ] do phase_002 thing" not in assembled

        assert result.lineage_diff.superseded == [
            ("phase_002", "phase_002b")
        ]
        assert result.lineage_diff.abandoned == []

        reader = Storage(ledger_dir, writer_id="check")
        all_events = reader.read_all()
        all_events.sort(key=lambda e: e.event_id)
        reauthor_writer_events = [
            e for e in all_events if e.writer_id.startswith("duplo-reauthor")
        ]
        types = [e.type for e in reauthor_writer_events]
        assert types == [
            EventType.PHASE_SUPERSEDED,
            EventType.PLAN_REAUTHORED,
        ]

        plan_reauthored_ev = reauthor_writer_events[-1]
        ledger_slice = plan_reauthored_ev.payload["ledger_slice_event_ids"]
        assert ledger_slice[0] == crossing_id
        assert ledger_slice[1] == result.lifecycle_event_ids[0]
        assert plan_reauthored_ev.payload["trigger_event_id"] == crossing_id

    def test_abandoned_emits_phase_abandoned_with_synth_reason(
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
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [{"id": "phase_001", "action": "preserve"}],
                "abandoned": [
                    {
                        "id": "phase_002",
                        "reason": "out of scope after assumption falsified",
                    }
                ],
            },
        }
        self._patch_council(monkeypatch, new_plan, verdict)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        assert result.lineage_diff.abandoned == [
            ("phase_002", "out of scope after assumption falsified")
        ]

        reader = Storage(ledger_dir, writer_id="check")
        all_events = reader.read_all()
        reauthor_writer_events = [
            e
            for e in sorted(all_events, key=lambda e: e.event_id)
            if e.writer_id.startswith("duplo-reauthor")
        ]
        assert [e.type for e in reauthor_writer_events] == [
            EventType.PHASE_ABANDONED,
            EventType.PLAN_REAUTHORED,
        ]
        abandon = reauthor_writer_events[0]
        assert abandon.payload["phase_id"] == "phase_002"
        # Synthesizer-supplied reason carries through to the event
        # payload (vs the prior protocol's hardcoded "elided in
        # re-author"). Better fidelity for the audit trail.
        assert (
            abandon.payload["reason"]
            == "out of scope after assumption falsified"
        )

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

        # Synthesizer claims a brand-new id is "preserved"; the
        # validator catches the mismatch with the prior plan.
        bad_plan = "## Phase phase_002: New phase\n\n- [ ] x\n"
        bad_verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [{"id": "phase_002", "action": "preserve"}]
            },
        }
        self._patch_council(monkeypatch, bad_plan, bad_verdict)

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

        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)
        assert "phase_002" not in plan_path.read_text()

    def test_missing_lineage_object_fails_closed(
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

        new_plan = "## Phase phase_001: Preserved\n\n- [ ] x\n"
        verdict_no_lineage = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            # Note: no 'lineage' key.
        }
        self._patch_council(monkeypatch, new_plan, verdict_no_lineage)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError, match="lineage"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)
        assert "Preserved" not in plan_path.read_text()

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

    # ---- preserve-by-default reauthor assembly (the directive's bug) ----

    def test_partial_synthesizer_output_is_preserved_by_runtime(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Synthesizer returns ONLY the changed phase (phase_002b
        supersedes phase_002), with lineage that names only that
        change. Prior plan has phase_001 through phase_005.

        Without runtime-side preservation, validate_lineage would
        reject (4 priors unaccounted) and the run would fail closed
        without progress.

        With preserve-by-default assembly, Duplo:
          - Adds preserve entries for phase_001/003/004/005 to lineage
          - Preserves their sections from prior PLAN.md verbatim
          - Substitutes phase_002b for phase_002 at the same position
          - Writes a full PLAN.md
        """
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir,
            plan_phases=[
                "phase_001",
                "phase_002",
                "phase_003",
                "phase_004",
                "phase_005",
            ],
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path,
            [
                "phase_001",
                "phase_002",
                "phase_003",
                "phase_004",
                "phase_005",
            ],
        )

        # Synthesizer returns ONLY phase_002b.
        partial_plan = (
            "## Phase phase_002b: Refactored phase_002\n\n"
            "- [ ] new b work\n"
        )
        # Lineage accounts ONLY for phase_002. The other priors are
        # left for Duplo to preserve.
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {
                        "id": "phase_002b",
                        "action": "supersede",
                        "from": ["phase_002"],
                    }
                ]
            },
        }
        self._patch_council(monkeypatch, partial_plan, verdict)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        # Assembled PLAN.md has all 5 prior ids represented (4
        # preserved from prior + phase_002b in place of phase_002).
        text = plan_path.read_text()
        assert "## Phase phase_001: phase_001 title" in text
        assert "## Phase phase_002:" not in text  # superseded
        assert "## Phase phase_002b: Refactored phase_002" in text
        assert "## Phase phase_003: phase_003 title" in text
        assert "## Phase phase_004: phase_004 title" in text
        assert "## Phase phase_005: phase_005 title" in text

        # Order matches prior order with phase_002 replaced.
        idxs = [
            text.find(f"## Phase {pid}:")
            for pid in (
                "phase_001",
                "phase_002b",
                "phase_003",
                "phase_004",
                "phase_005",
            )
        ]
        assert idxs == sorted(idxs)

        # Preserved phase content survives verbatim from prior.
        assert "- [ ] do phase_001 thing" in text
        assert "- [ ] do phase_003 thing" in text
        # Synthesized phase_002b content lands.
        assert "- [ ] new b work" in text

        # Lineage diff reflects only the synthesizer's actions
        # (preserves are not lifecycle events).
        assert result.lineage_diff.superseded == [
            ("phase_002", "phase_002b")
        ]
        assert result.lineage_diff.abandoned == []

        # Single phase_superseded event emitted (one per consumed
        # prior); no events for the four preserves.
        reader = Storage(ledger_dir, writer_id="probe")
        all_events = reader.read_all()
        reauthor_events = [
            e
            for e in all_events
            if e.writer_id.startswith("duplo-reauthor")
        ]
        from bob_tools.ledger import EventType

        types = [e.type for e in reauthor_events]
        assert types == [
            EventType.PHASE_SUPERSEDED,
            EventType.PLAN_REAUTHORED,
        ]

    def test_contradictory_lineage_still_raises_after_normalization(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Normalization fills in preserve-defaults; it does NOT
        repair explicit contradictions. A lineage that names the
        same prior id under both ``preserve`` and ``supersede.from``
        still raises, with no write to PLAN.md and no events emitted.
        """
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])
        original_plan_text = plan_path.read_text()

        synth_plan = (
            "## Phase phase_001: First\n\n- [ ] preserved a\n\n"
            "## Phase phase_002b: Refactored\n\n- [ ] new b\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    # Both preserve AND supersede.from name phase_001.
                    {"id": "phase_001", "action": "preserve"},
                    {
                        "id": "phase_002b",
                        "action": "supersede",
                        "from": ["phase_001", "phase_002"],
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, synth_plan, verdict)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_full_plan_reauthor_path_still_works(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backward compat: a synthesizer that DOES emit the full
        plan plus fully-accounting lineage still produces a valid
        re-author. Normalization is a no-op when no priors are
        unaccounted; assembly walks priors and emits replaced /
        preserved sections per lineage."""
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002", "phase_003"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_002", "phase_003"]
        )

        # Synthesizer writes the FULL plan: preserves 1 and 3,
        # supersedes 2.
        full_plan = (
            "## Phase phase_001: First\n\n- [ ] keep a\n\n"
            "## Phase phase_002b: Refactored Second\n\n- [ ] new b\n\n"
            "## Phase phase_003: Third\n\n- [ ] keep c\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {"id": "phase_001", "action": "preserve"},
                    {
                        "id": "phase_002b",
                        "action": "supersede",
                        "from": ["phase_002"],
                    },
                    {"id": "phase_003", "action": "preserve"},
                ]
            },
        }
        self._patch_council(monkeypatch, full_plan, verdict)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        text = plan_path.read_text()
        assert "## Phase phase_001:" in text
        assert "## Phase phase_002:" not in text
        assert "## Phase phase_002b:" in text
        assert "## Phase phase_003:" in text
        # Preserved sections come from PRIOR (not synth's reproduction).
        assert "- [ ] do phase_001 thing" in text
        assert "- [ ] do phase_003 thing" in text
        # Synth's reproductions are discarded (preserve = preserve).
        assert "- [ ] keep a" not in text
        assert "- [ ] keep c" not in text
        # Synth's supersede content lands.
        assert "- [ ] new b" in text

        assert result.lineage_diff.superseded == [
            ("phase_002", "phase_002b")
        ]

        reader = Storage(ledger_dir, writer_id="probe")
        from bob_tools.ledger import EventType

        all_events = reader.read_all()
        reauthor_events = [
            e
            for e in all_events
            if e.writer_id.startswith("duplo-reauthor")
        ]
        types = [e.type for e in reauthor_events]
        assert types == [
            EventType.PHASE_SUPERSEDED,
            EventType.PLAN_REAUTHORED,
        ]

    def test_synth_section_missing_for_lineage_target_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lineage names a supersede target that the synthesizer
        didn't write a section for. Assembly fails before write."""
        from bob_tools.ledger import Storage

        from duplo.reauthor_assemble import ReauthorAssemblyError

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])
        original_plan_text = plan_path.read_text()

        # Synthesizer's body has no section at all.
        empty_plan = ""
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {
                        "id": "phase_002b",
                        "action": "supersede",
                        "from": ["phase_002"],
                    }
                ]
            },
        }
        self._patch_council(monkeypatch, empty_plan, verdict)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(ReauthorAssemblyError, match="phase_002b"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)


# ---------------------------------------------------------------------
# ledger_slice + design_context shape
# ---------------------------------------------------------------------


def _captured_invoke(captured: dict[str, str]) -> Any:
    """Return a fake _invoke_council_for_reauthor that captures the
    prompt inputs and returns a minimal valid (plan_text, verdict)
    tuple referencing one preserved phase."""

    def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        captured["ledger_slice"] = kwargs["ledger_slice_md"]
        captured["design_context"] = kwargs["design_context_md"]
        plan_text = (
            "## Phase phase_001: Phase phase_001 title (preserved)\n"
            "\n"
            "- [ ] x\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [{"id": "phase_001", "action": "preserve"}]
            },
        }
        return plan_text, verdict

    return fake_invoke


@needs_bob_tools
class TestLedgerSliceShape:
    def test_ledger_slice_starts_with_triggering_crossing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

        from duplo.reauthor import reauthor_plan
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
            reauthor, "_invoke_council_for_reauthor", _captured_invoke(captured)
        )

        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=emitted[0],
            project_dir=tmp_path,
        )

        slice_md = captured["ledger_slice"]
        assert (
            slice_md.find("Triggering threshold crossing")
            < slice_md.find("Since boundary")
            < slice_md.find("Phases (current)")
        )
        assert f"crossing_event_id: {emitted[0]}" in slice_md
        assert "Phase phase_001" in slice_md

    def test_design_context_marks_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

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
            reauthor, "_invoke_council_for_reauthor", _captured_invoke(captured)
        )
        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=emitted[0],
            project_dir=tmp_path,
        )

        ctx = captured["design_context"]
        assert "plan_text_best_effort" in ctx
        assert "Constraints" in ctx


# ---------------------------------------------------------------------
# Sanity: ParsedHeader is a frozen dataclass with the expected fields
# ---------------------------------------------------------------------


def test_parsed_header_shape() -> None:
    h = ParsedHeader(id="phase_001", title="t", header_line_index=0)
    assert h.id == "phase_001"
    assert h.title == "t"
    assert h.header_line_index == 0
