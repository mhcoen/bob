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

import json
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


def _wrap_synth_plan(text: str) -> str:
    """Prepend a canonical H1 envelope before every ``## Phase ...``
    line in ``text``.

    Reauthor's synthesizer template requires phases to be wrapped in
    H1 envelopes (``# {project} — Phase N: {title}``); the
    plan_document parser rejects bare H2s. Pre-plan_document tests
    used H2-only synth fixtures; this helper migrates them by
    prepending a canonical H1 before each H2 in source order.

    The H1 ordinal here is irrelevant — assembly's renderer assigns
    ordinals by final position in the assembled plan, so any value
    here is renumbered when the prior plan and synth are stitched
    together.
    """
    import re

    h2_re = re.compile(r"^(##\s+Phase\s+(\S+):.*)$", re.MULTILINE)
    counter = [0]

    def repl(match: "re.Match[str]") -> str:
        pid = match.group(2)
        idx = counter[0]
        counter[0] += 1
        return f"# proj — Phase {idx}: {pid} envelope\n{match.group(1)}"

    return h2_re.sub(repl, text)


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
        for ordinal, pid in enumerate(phase_ids):
            body_lines.append(f"# proj — Phase {ordinal}: {pid} envelope")
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

        new_plan = _wrap_synth_plan(
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

    def test_reauthor_accepts_bare_h2_synth_fragment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])

        new_plan = (
            "The council rationale can precede the changed units.\n"
            "\n"
            "## Phase phase_006: Adopted CLI Surface\n"
            "\n"
            "- [ ] pin parser smoke\n"
            "\n"
            "## Phase phase_007: Runtime Loop\n"
            "\n"
            "- [ ] wire observer\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {"id": "phase_006", "action": "new"},
                    {
                        "id": "phase_007",
                        "action": "supersede",
                        "from": ["phase_002"],
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, new_plan, verdict)

        from duplo.reauthor import reauthor_plan

        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        assembled = plan_path.read_text()
        assert "## Phase phase_001: phase_001 title" in assembled
        assert "## Phase phase_006: Adopted CLI Surface" in assembled
        assert "## Phase phase_007: Runtime Loop" in assembled
        assert "# proj — Phase 1: Runtime Loop" in assembled
        assert "# proj — Phase 2: Adopted CLI Surface" in assembled
        assert "The council rationale" not in assembled

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

        new_plan = _wrap_synth_plan(
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
        bad_plan = _wrap_synth_plan(
            "## Phase phase_002: New phase\n\n- [ ] x\n"
        )
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

        new_plan = _wrap_synth_plan(
            "## Phase phase_001: Preserved\n\n- [ ] x\n"
        )
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
        plan_path.write_text(
            "# proj — Phase 0: env\n## Phase phase_001: x\n\n- [ ] x\n"
        )

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
        plan_path.write_text(
            "# proj — Phase 0: env\n## Phase p1: x\n\n- [ ] x\n"
        )

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
        partial_plan = _wrap_synth_plan(
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

        synth_plan = _wrap_synth_plan(
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
        full_plan = _wrap_synth_plan(
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

    def test_target_phase_id_threads_into_council_question(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When reauthor_plan is called with target_phase_id, the
        council brief carries an explicit scope clause naming the
        target. The synthesizer is told the re-author is
        phase-scoped and unchanged priors must be preserved. Used
        by mcloop's auto_reauthor to honor recommended_action ==
        reauthor_phase without escalating to plan-wide synthesis."""
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002", "phase_003"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_002", "phase_003"]
        )

        captured: dict[str, Any] = {}

        from duplo import reauthor

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured["question"] = kwargs["question"]
            text = _wrap_synth_plan(
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
                        {
                            "id": "phase_002b",
                            "action": "supersede",
                            "from": ["phase_002"],
                        },
                    ]
                },
            }
            return text, verdict

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        from duplo.reauthor import reauthor_plan

        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
            target_phase_id="phase_002",
        )

        question = captured["question"]
        assert "SCOPE" in question
        assert "phase_002" in question
        assert "preserve" in question.lower()

    def test_target_phase_id_unknown_raises_reauthor_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """target_phase_id MUST refer to a current prior plan id.
        An unknown id is a caller error and raises ReauthorError
        (not LineageValidationError) before the council is invoked."""
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])

        from duplo import reauthor

        def fake_invoke_should_not_run(**kwargs: Any) -> Any:
            raise AssertionError("council should not be invoked")

        monkeypatch.setattr(
            reauthor,
            "_invoke_council_for_reauthor",
            fake_invoke_should_not_run,
        )

        from duplo.reauthor import ReauthorError, reauthor_plan

        with pytest.raises(ReauthorError, match="phase_999"):
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
                target_phase_id="phase_999",
            )

    def test_default_target_phase_id_omits_scope_clause(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backward compat: when target_phase_id is None (the
        default and the existing behavior), the question contains
        no scope clause and the synthesizer authors plan-wide as
        before."""
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001", "phase_002"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001", "phase_002"])

        captured: dict[str, Any] = {}

        from duplo import reauthor

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured["question"] = kwargs["question"]
            return (
                _wrap_synth_plan(
                    "## Phase phase_001: First\n\n- [ ] x\n\n"
                    "## Phase phase_002: Second\n\n- [ ] y\n",
                ),
                {
                    "decision": "accept",
                    "feedback": "ok",
                    "agreements": [],
                    "disagreements": [],
                    "rejected_options": [],
                    "lineage": {
                        "phases": [
                            {"id": "phase_001", "action": "preserve"},
                            {"id": "phase_002", "action": "preserve"},
                        ]
                    },
                },
            )

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        from duplo.reauthor import reauthor_plan

        reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )
        assert "SCOPE" not in captured["question"]

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

    # -----------------------------------------------------------------
    # Structural lineage validator (load-bearing diagnostic layer).
    #
    # The smoke run that motivated these tests had a prior plan of
    # phase_001 + phase_006-009 (gap from earlier reauthor passes).
    # The synthesizer wrote phase_006-009 as new supersede ids
    # (collision) and phase_002-005 in `from` (historical, not
    # current). The structural validator catches both with explicit
    # error messages naming the floor (phase_010) and the current
    # prior id list, so the operator sees what to do next.
    # -----------------------------------------------------------------

    def test_structural_validator_rejects_collision_with_current_prior_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exact collision case: prior ids = phase_006-009, the
        synthesizer emits phase_006 as a new supersede id. Rejected
        with a message naming the current prior id list and the
        floor (phase_010). PLAN.md unchanged; no events emitted."""
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        prior_phase_ids = [
            "phase_006",
            "phase_007",
            "phase_008",
            "phase_009",
        ]
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=prior_phase_ids
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, prior_phase_ids)
        original_plan_text = plan_path.read_text()

        synth_plan = _wrap_synth_plan(
            "## Phase phase_006: Refactored\n\n- [ ] new\n\n"
            "## Phase phase_007: Refactored\n\n- [ ] new\n\n"
            "## Phase phase_008: Refactored\n\n- [ ] new\n\n"
            "## Phase phase_009: Refactored\n\n- [ ] new\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {
                        "id": "phase_006",
                        "action": "supersede",
                        "from": ["phase_006"],
                    },
                    {
                        "id": "phase_007",
                        "action": "supersede",
                        "from": ["phase_007"],
                    },
                    {
                        "id": "phase_008",
                        "action": "supersede",
                        "from": ["phase_008"],
                    },
                    {
                        "id": "phase_009",
                        "action": "supersede",
                        "from": ["phase_009"],
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, synth_plan, verdict)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError) as exc_info:
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        msg = str(exc_info.value)
        assert "phase_006" in msg
        # Floor (highest + 1) is named.
        assert "phase_010" in msg
        # Current prior id list is named.
        for pid in prior_phase_ids:
            assert pid in msg

        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_structural_validator_rejects_unknown_from_historical_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`from` references an id from an earlier reauthor's
        history that is no longer in the current PLAN.md. Rejected
        with a message naming the offending entry and the current
        prior id list."""
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        prior_phase_ids = [
            "phase_006",
            "phase_007",
            "phase_008",
            "phase_009",
        ]
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=prior_phase_ids
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, prior_phase_ids)
        original_plan_text = plan_path.read_text()

        synth_plan = _wrap_synth_plan(
            "## Phase phase_010: Derived from history\n\n- [ ] x\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {
                        "id": "phase_010",
                        "action": "supersede",
                        "from": ["phase_002"],  # historical, not current
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, synth_plan, verdict)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError) as exc_info:
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        msg = str(exc_info.value)
        assert "phase_002" in msg
        # Current prior id list is named.
        for pid in prior_phase_ids:
            assert pid in msg

        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_structural_validator_accepts_correct_floor_and_from(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Positive: prior ids = phase_006-009, synthesizer uses
        phase_010 with action=supersede + from=['phase_006'],
        preserves phase_007/008/009. Reauthor accepts and writes."""
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        prior_phase_ids = [
            "phase_006",
            "phase_007",
            "phase_008",
            "phase_009",
        ]
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=prior_phase_ids
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, prior_phase_ids)

        synth_plan = _wrap_synth_plan(
            "## Phase phase_010: Refactored\n\n- [ ] new b\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {
                "phases": [
                    {
                        "id": "phase_010",
                        "action": "supersede",
                        "from": ["phase_006"],
                    },
                ]
            },
        }
        self._patch_council(monkeypatch, synth_plan, verdict)

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )
        # PLAN.md was written.
        new_text = plan_path.read_text()
        assert "phase_010" in new_text
        # phase_007/008/009 preserved by the runtime.
        assert "phase_007" in new_text
        assert "phase_008" in new_text
        assert "phase_009" in new_text
        # phase_006 is replaced (not preserved as a header).
        assert "## Phase phase_006:" not in new_text
        # Lifecycle event was emitted.
        assert result.lifecycle_event_ids
        events = list(Storage(ledger_dir, writer_id="probe").read_all())
        assert any(
            "phase_010" in repr(ev.payload) for ev in events
        )

    def test_normalize_does_not_remap_historical_to_current_ids(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """normalize_lineage_for_preservation must NOT silently map
        historical ids to current ids. It passes them through
        unchanged and the structural validator rejects."""
        from bob_tools.ledger import Storage

        from duplo.reauthor_assemble import (
            normalize_lineage_for_preservation,
        )

        # Direct unit-level check first: pass-through, no remapping.
        prior_ids = ["phase_006", "phase_007"]
        synth_lineage = {
            "phases": [
                {
                    "id": "phase_010",
                    "action": "supersede",
                    "from": ["phase_002"],  # historical id
                }
            ]
        }
        normalized = normalize_lineage_for_preservation(
            prior_ids, synth_lineage
        )
        # The supersede entry's `from` is unchanged.
        supersede = next(
            p for p in normalized["phases"] if p.get("id") == "phase_010"
        )
        assert supersede["from"] == ["phase_002"]
        # Preserve defaults are appended for unaccounted prior ids
        # (phase_006 and phase_007), but no remapping happened.
        preserve_ids = sorted(
            p["id"]
            for p in normalized["phases"]
            if p.get("action") == "preserve"
        )
        assert preserve_ids == ["phase_006", "phase_007"]

        # End-to-end: the historical `from` must reach the structural
        # validator and trigger a rejection. Atomic on failure.
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=prior_ids
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, prior_ids)
        original_plan_text = plan_path.read_text()

        synth_plan = _wrap_synth_plan(
            "## Phase phase_010: Refactor\n\n- [ ] x\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": synth_lineage,
        }
        self._patch_council(monkeypatch, synth_plan, verdict)

        from duplo.reauthor import reauthor_plan

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError) as exc_info:
            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        # The historical id is named in the message.
        assert "phase_002" in str(exc_info.value)

        # Atomicity preserved on rejection.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    # -----------------------------------------------------------------
    # Plan-document structural integration (regression for the
    # fswatch-run-smoke corruption shape)
    # -----------------------------------------------------------------

    def test_reauthor_rejects_synth_plan_carrying_verdict_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The synthesizer occasionally embeds a fenced JSON verdict
        block inside the plan artifact alongside the verdict
        artifact. That landed verbatim in PLAN.md across reauthor
        passes and amplified into the fswatch-run-smoke corruption
        shape. sanitize_plan_artifact in plan_document rejects this;
        reauthor_plan wraps the rejection in a
        ReauthorError('plan_artifact_contained_verdict_json...').
        Mid-body verdicts (not the documented trailing-fence shape)
        are still rejected; trailing-verdict shape is the canonical
        case and is exercised by the sibling success test below."""
        from bob_tools.ledger import Storage

        from duplo.reauthor import ReauthorError

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001"])
        original_plan_text = plan_path.read_text()

        # Synth output has a verdict-shaped fenced JSON block in the
        # MIDDLE of the plan body (non-whitespace content follows
        # it). That violates the trailing-fence contract.
        plan_text_with_verdict = (
            "# proj — Phase 0: env\n"
            "## Phase phase_001: First\n\n"
            "- [ ] task\n\n"
            "```json\n"
            '{"decision": "accept", "lineage": {"phases": []}}\n'
            "```\n"
            "\n"
            "more body text after the verdict fence\n"
        )
        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {"phases": [{"id": "phase_001", "action": "preserve"}]},
        }
        # Patch _invoke_council_for_reauthor at the layer ABOVE
        # sanitize so the sanitize call inside it can fire. Using
        # the stock _patch_council that bypasses sanitize would mask
        # the contract.
        from duplo import reauthor as reauthor_mod

        class _FakeArtifactView:
            def __init__(self, value: Any) -> None:
                self.value = value

        class _FakeResult:
            terminal = "done"
            run_id = "fake-run"
            artifacts = {
                "plan": _FakeArtifactView(plan_text_with_verdict),
                "judge_verdict": _FakeArtifactView(verdict),
            }

        def fake_run_workflow(*args: Any, **kwargs: Any) -> Any:
            return _FakeResult()

        # The real _invoke_council_for_reauthor calls
        # orchestra.run_workflow + sanitize_plan_artifact. Patching
        # run_workflow exercises the sanitize step. Patch
        # _resolve_orchestra_config too so it doesn't error trying
        # to load real config in tmp_path.
        import orchestra

        monkeypatch.setattr(orchestra, "run_workflow", fake_run_workflow)
        monkeypatch.setattr(
            reauthor_mod,
            "_resolve_orchestra_config",
            lambda council, project_dir: object(),
        )

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(
            ReauthorError, match="plan_artifact_contained_verdict_json"
        ):
            reauthor_mod.reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        # Atomicity: PLAN.md unchanged, no new events, no
        # plan_reauthored event.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_reauthor_accepts_trailing_verdict_fence_canonical_shape(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The canonical synthesizer output: plan body followed by a
        single trailing fenced ``json`` verdict block. The text
        adapter captures the full response into the plan artifact;
        sanitize_plan_artifact extracts the trailing verdict and
        returns the plan body without the fence. Reauthor reconciles
        the extracted verdict against the judge_verdict artifact
        (they match) and proceeds. Regression for FIX A."""
        from bob_tools.ledger import EventType, Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001"])

        verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {"phases": [{"id": "phase_001", "action": "preserve"}]},
        }
        plan_body = (
            "# proj — Phase 0: env\n"
            "## Phase phase_001: phase_001 title\n\n"
            "- [ ] do phase_001 thing\n"
        )
        # The full text adapter response: plan body + trailing
        # fenced verdict.
        plan_artifact_value = (
            plan_body
            + "\n"
            + "```json\n"
            + json.dumps(verdict, sort_keys=True)
            + "\n"
            + "```\n"
        )

        from duplo import reauthor as reauthor_mod

        class _FakeArtifactView:
            def __init__(self, value: Any) -> None:
                self.value = value

        class _FakeResult:
            terminal = "done"
            run_id = "fake-run"
            artifacts = {
                "plan": _FakeArtifactView(plan_artifact_value),
                "judge_verdict": _FakeArtifactView(verdict),
            }

        def fake_run_workflow(*args: Any, **kwargs: Any) -> Any:
            return _FakeResult()

        import orchestra

        monkeypatch.setattr(orchestra, "run_workflow", fake_run_workflow)
        monkeypatch.setattr(
            reauthor_mod,
            "_resolve_orchestra_config",
            lambda council, project_dir: object(),
        )

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        # Reauthor succeeded end-to-end: PLAN.md was rewritten
        # (preserve carried the prior phase forward), lifecycle
        # events fired.
        new_text = plan_path.read_text()
        assert "## Phase phase_001:" in new_text
        # The trailing verdict fence must NOT appear in the rewritten
        # PLAN.md — sanitize stripped it before assembly.
        assert "```json" not in new_text
        assert '"decision": "accept"' not in new_text
        # plan_reauthored event was emitted.
        events = list(Storage(ledger_dir, writer_id="probe").read_all())
        assert any(e.type is EventType.PLAN_REAUTHORED for e in events)
        assert result.plan_reauthored_event_id

    def test_reauthor_rejects_when_extracted_verdict_disagrees_with_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the trailing-fenced verdict in the plan artifact
        disagrees with orchestra's judge_verdict artifact value, the
        two parsers don't agree on the model output. Fail closed
        with reason 'plan_artifact_verdict_mismatch' rather than
        silently picking one."""
        from bob_tools.ledger import Storage

        from duplo.reauthor import ReauthorError

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001"]
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(plan_path, ["phase_001"])
        original_plan_text = plan_path.read_text()

        artifact_verdict = {
            "decision": "accept",
            "feedback": "ok",
            "agreements": [],
            "disagreements": [],
            "rejected_options": [],
            "lineage": {"phases": [{"id": "phase_001", "action": "preserve"}]},
        }
        embedded_verdict = {
            "decision": "reframe",
            "feedback": "different",
        }
        plan_artifact_value = (
            "# proj — Phase 0: env\n"
            "## Phase phase_001: First\n\n- [ ] x\n\n"
            "```json\n"
            + json.dumps(embedded_verdict)
            + "\n"
            + "```\n"
        )

        from duplo import reauthor as reauthor_mod

        class _FakeArtifactView:
            def __init__(self, value: Any) -> None:
                self.value = value

        class _FakeResult:
            terminal = "done"
            run_id = "fake-run"
            artifacts = {
                "plan": _FakeArtifactView(plan_artifact_value),
                "judge_verdict": _FakeArtifactView(artifact_verdict),
            }

        def fake_run_workflow(*args: Any, **kwargs: Any) -> Any:
            return _FakeResult()

        import orchestra

        monkeypatch.setattr(orchestra, "run_workflow", fake_run_workflow)
        monkeypatch.setattr(
            reauthor_mod,
            "_resolve_orchestra_config",
            lambda council, project_dir: object(),
        )

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(
            ReauthorError, match="plan_artifact_verdict_mismatch"
        ):
            reauthor_mod.reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_reauthor_rejects_corrupt_prior_plan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prior PLAN.md has the fswatch-run-smoke corruption shape:
        one H1 envelope under which sit multiple H2 sections plus
        embedded fenced JSON verdict blocks. The new strict parser
        rejects it at the boundary; reauthor_plan wraps the
        ParseError in ReauthorError so mcloop sees a clear pause
        reason rather than amplifying corruption across passes."""
        from bob_tools.ledger import Storage

        from duplo.reauthor import ReauthorError

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir, plan_phases=["phase_001"]
        )
        plan_path = tmp_path / "PLAN.md"
        # The corruption shape: one H1 envelope, multiple H2s, plus
        # embedded verdict JSON.
        plan_path.write_text(
            "# proj — Phase 1: Watch and run\n"
            "## Phase phase_018: First subsection\n\n"
            "- [ ] task\n\n"
            "## Phase phase_015: Second subsection\n\n"
            "- [ ] task\n\n"
            "## Phase phase_019: Third subsection\n\n"
            "- [ ] task\n",
            encoding="utf-8",
        )
        original_plan_text = plan_path.read_text()

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(
            ReauthorError, match="cannot be parsed as a canonical plan document"
        ):
            from duplo.reauthor import reauthor_plan

            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )
        # Atomicity preserved.
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
        plan_text = _wrap_synth_plan(
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
            "# proj — Phase 0: env\n"
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
            "# proj — Phase 0: env\n"
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


# ---------------------------------------------------------------------
# next-available phase id helper + state-blob enrichment
# ---------------------------------------------------------------------


class TestNextAvailablePhaseIdFromPriors:
    """Mirrors duplo.council.compute_required_phase_id's safe rule
    (highest + 1, NOT the smallest gap). Reauthor needs this to
    inject an explicit start value into the synthesizer brief so
    the model doesn't have to guess; gap-filled prior plans were
    making it pick colliding ids."""

    def test_no_priors_returns_phase_001(self) -> None:
        from duplo.reauthor import _next_available_phase_id_from_priors

        assert _next_available_phase_id_from_priors([]) == "phase_001"

    def test_single_prior_returns_next(self) -> None:
        from duplo.reauthor import _next_available_phase_id_from_priors

        priors = [ParsedHeader(id="phase_001", title="t", header_line_index=0)]
        assert _next_available_phase_id_from_priors(priors) == "phase_002"

    def test_contiguous_returns_max_plus_one(self) -> None:
        from duplo.reauthor import _next_available_phase_id_from_priors

        priors = [
            ParsedHeader(id=f"phase_{i:03d}", title="t", header_line_index=i)
            for i in range(1, 6)
        ]
        assert _next_available_phase_id_from_priors(priors) == "phase_006"

    def test_gap_returns_highest_plus_one_not_gap(self) -> None:
        """The actual scenario from the smoke fixture: prior plan
        had phase_001 + phase_006-009 (gap at 002-005 from earlier
        reauthor runs). The next-available value MUST be phase_010,
        NOT phase_002 (the smallest gap)."""
        from duplo.reauthor import _next_available_phase_id_from_priors

        priors = [
            ParsedHeader(id="phase_001", title="a", header_line_index=0),
            ParsedHeader(id="phase_006", title="b", header_line_index=1),
            ParsedHeader(id="phase_007", title="c", header_line_index=2),
            ParsedHeader(id="phase_008", title="d", header_line_index=3),
            ParsedHeader(id="phase_009", title="e", header_line_index=4),
        ]
        assert _next_available_phase_id_from_priors(priors) == "phase_010"

    def test_non_strict_ids_skipped_from_max(self) -> None:
        """Ids that don't match phase_NNN don't participate in the
        max computation. ``phase_xyz`` is ignored; ``phase_004``
        sets the max."""
        from duplo.reauthor import _next_available_phase_id_from_priors

        priors = [
            ParsedHeader(id="phase_004", title="a", header_line_index=0),
            ParsedHeader(id="phase_xyz", title="b", header_line_index=1),
        ]
        assert _next_available_phase_id_from_priors(priors) == "phase_005"

    def test_zero_padding(self) -> None:
        from duplo.reauthor import _next_available_phase_id_from_priors

        priors = [
            ParsedHeader(id="phase_099", title="t", header_line_index=0),
        ]
        # 100 is no longer 3-digit-needs-padding but the format
        # specifier is :03d so 100 stays as phase_100.
        assert _next_available_phase_id_from_priors(priors) == "phase_100"


class TestStateBlobIncludesNextAvailableId:
    """The state blob carries the prior phase id list AND an
    explicit ``Next available phase id`` value. The synthesizer
    template instructs the model to use the supplied value
    verbatim; this test pins that the runtime actually surfaces
    it."""

    def test_state_blob_lists_priors_and_next_available(self) -> None:
        from duplo.reauthor import _build_state_blob

        plan_text = (
            "## Phase phase_001: Scaffold\n- [ ] x\n"
            "## Phase phase_006: After-gap\n- [ ] y\n"
        )
        priors = [
            ParsedHeader(id="phase_001", title="Scaffold", header_line_index=0),
            ParsedHeader(id="phase_006", title="After-gap", header_line_index=2),
        ]
        blob = _build_state_blob(plan_text, priors, state=None)
        # Prior list is present.
        assert "phase_001: Scaffold" in blob
        assert "phase_006: After-gap" in blob
        # Next-available is computed from highest+1 (gap is NOT
        # filled — the smoke-fixture bug case).
        assert "Next available phase id" in blob
        assert "phase_007" in blob
        # The "use VERBATIM" instruction is present.
        assert "VERBATIM" in blob

    def test_state_blob_no_priors_starts_at_phase_001(self) -> None:
        from duplo.reauthor import _build_state_blob

        blob = _build_state_blob("", [], state=None)
        assert "Next available phase id" in blob
        assert "phase_001" in blob


# ---------------------------------------------------------------------
# Structural lineage validator unit tests (no bob_tools dependency)
# ---------------------------------------------------------------------


class TestValidateLineageStructural:
    """Direct unit-level coverage of _validate_lineage_structural.

    These do not exercise the full reauthor_plan path (see the
    @needs_bob_tools tests above for that), so they run in any
    environment that can import duplo.reauthor."""

    def test_collision_with_current_prior_id_raises_naming_floor(self) -> None:
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007", "phase_008", "phase_009"]
        floor = "phase_010"
        lineage = {
            "phases": [
                {
                    "id": "phase_006",
                    "action": "supersede",
                    "from": ["phase_006"],
                }
            ]
        }
        with pytest.raises(LineageValidationError) as exc_info:
            _validate_lineage_structural(prior, floor, lineage)
        msg = str(exc_info.value)
        assert "phase_006" in msg
        assert "phase_010" in msg
        assert "phase_007" in msg  # current prior id list rendered

    def test_below_floor_raises(self) -> None:
        """Strict phase_NNN id whose suffix is less than the floor's
        suffix is rejected even when not colliding with any current
        prior id."""
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007", "phase_008", "phase_009"]
        floor = "phase_010"
        lineage = {
            "phases": [
                {
                    "id": "phase_005",  # not a prior; below floor
                    "action": "new",
                }
            ]
        }
        with pytest.raises(LineageValidationError) as exc_info:
            _validate_lineage_structural(prior, floor, lineage)
        msg = str(exc_info.value)
        assert "phase_005" in msg
        assert "below" in msg.lower()
        assert "phase_010" in msg

    def test_unknown_from_raises(self) -> None:
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007", "phase_008", "phase_009"]
        floor = "phase_010"
        lineage = {
            "phases": [
                {
                    "id": "phase_010",
                    "action": "supersede",
                    "from": ["phase_002"],  # historical, not current
                }
            ]
        }
        with pytest.raises(LineageValidationError) as exc_info:
            _validate_lineage_structural(prior, floor, lineage)
        msg = str(exc_info.value)
        assert "phase_002" in msg
        for pid in prior:
            assert pid in msg

    def test_accumulates_multiple_violations(self) -> None:
        """All violations surface in one raise, matching the pattern
        of validate_lineage's accumulating errors."""
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007"]
        floor = "phase_008"
        lineage = {
            "phases": [
                {
                    "id": "phase_006",  # collision
                    "action": "supersede",
                    "from": ["phase_002"],  # unknown
                },
                {
                    "id": "phase_005",  # below floor
                    "action": "new",
                },
            ]
        }
        with pytest.raises(LineageValidationError) as exc_info:
            _validate_lineage_structural(prior, floor, lineage)
        msg = str(exc_info.value)
        assert "phase_006" in msg  # collision
        assert "phase_002" in msg  # unknown
        assert "phase_005" in msg  # below floor

    def test_valid_lineage_passes(self) -> None:
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007", "phase_008", "phase_009"]
        floor = "phase_010"
        lineage = {
            "phases": [
                {
                    "id": "phase_010",
                    "action": "supersede",
                    "from": ["phase_006"],
                },
                {"id": "phase_007", "action": "preserve"},
            ]
        }
        # No raise.
        _validate_lineage_structural(prior, floor, lineage)

    def test_no_priors_falls_back_to_collision_only(self) -> None:
        """Floor is phase_001 when there are no priors. The
        below-floor check collapses (any strict id is >= 001), so
        only the collision rule applies (vacuously, since there are
        no priors). New ids in any range are accepted."""
        from duplo.reauthor import _validate_lineage_structural

        lineage = {
            "phases": [
                {"id": "phase_001", "action": "new"},
                {"id": "phase_999", "action": "new"},
            ]
        }
        _validate_lineage_structural([], "phase_001", lineage)

    def test_non_strict_id_skips_floor_check(self) -> None:
        """An id that doesn't match strict phase_NNN form (e.g.
        phase_002b, label-style) skips the floor check but still
        gets the collision check."""
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007"]
        floor = "phase_008"
        # phase_002b is non-strict; it's also not a current prior;
        # collision check passes; floor check skipped.
        lineage = {
            "phases": [{"id": "phase_002b", "action": "new"}]
        }
        _validate_lineage_structural(prior, floor, lineage)

        # But a non-strict id that collides with a current prior is
        # still rejected.
        lineage = {
            "phases": [{"id": "phase_006", "action": "new"}]
        }
        with pytest.raises(LineageValidationError):
            _validate_lineage_structural(prior, floor, lineage)

    def test_preserve_action_is_not_subject_to_floor_or_collision(
        self,
    ) -> None:
        """preserve entries reuse prior ids by definition; the
        structural validator only fires on supersede/split/merge/new."""
        from duplo.reauthor import _validate_lineage_structural

        prior = ["phase_006", "phase_007"]
        floor = "phase_008"
        lineage = {
            "phases": [
                {"id": "phase_006", "action": "preserve"},
                {"id": "phase_007", "action": "preserve"},
            ]
        }
        _validate_lineage_structural(prior, floor, lineage)


# ---------------------------------------------------------------------
# PlanArtifactError exception class
# ---------------------------------------------------------------------


class TestPlanArtifactError:
    def test_is_subclass_of_reauthor_error(self) -> None:
        """PlanArtifactError MUST be a subclass of ReauthorError so
        callers (notably mcloop's auto_reauthor) that catch
        ReauthorError continue to do the right thing during the
        transition. mcloop's specific handler matches the subclass
        first to surface the distinct 'plan_artifact_invalid' pause
        reason; falling back to the generic ReauthorError handler is
        the safe default."""
        from duplo.reauthor import PlanArtifactError, ReauthorError

        assert issubclass(PlanArtifactError, ReauthorError)
        # Instantiation works the same as the parent.
        err = PlanArtifactError("test")
        assert isinstance(err, ReauthorError)
        assert str(err) == "test"

    def test_is_exported_from_module(self) -> None:
        """The exception is in __all__ so static analysis and
        public-API consumers can find it without resorting to
        private-name imports."""
        import duplo.reauthor as reauthor_mod

        assert "PlanArtifactError" in reauthor_mod.__all__


# ---------------------------------------------------------------------
# Bounded retry with validator feedback
# ---------------------------------------------------------------------


class TestLineageFeedbackFormatter:
    """Unit-level coverage of _format_lineage_feedback_for_retry.

    The formatter renders the retry-feedback block that gets injected
    into the next council brief. It must surface ALL violations
    (not just the first), plus the whitelist and floor explicitly so
    the model is reminded of the constraint regardless of which
    specific violation tripped this attempt.
    """

    def test_lists_all_violations(self) -> None:
        from duplo.reauthor import _format_lineage_feedback_for_retry

        err = LineageValidationError(
            "structural lineage violations:\n"
            "  - new phase id 'phase_006' collides with prior\n"
            "  - lineage entry 'phase_009' names 'phase_002' in 'from'\n"
            "  - new phase id 'phase_004' is below the floor"
        )
        out = _format_lineage_feedback_for_retry(
            error=err,
            prior_plan_ids=["phase_001", "phase_006", "phase_007"],
            next_new_phase_id_floor="phase_010",
            attempt=1,
            max_attempts=2,
        )
        assert "new phase id 'phase_006' collides" in out
        assert "'phase_009' names 'phase_002'" in out
        assert "'phase_004' is below the floor" in out

    def test_includes_whitelist_and_floor(self) -> None:
        """The formatter ALWAYS includes the allowed-prior-id
        whitelist and the floor, regardless of the specific
        violations. The model needs the constraint surfaced every
        retry, not just told what failed this time."""
        from duplo.reauthor import _format_lineage_feedback_for_retry

        err = LineageValidationError(
            "structural lineage violations:\n"
            "  - one minimal violation only"
        )
        out = _format_lineage_feedback_for_retry(
            error=err,
            prior_plan_ids=["phase_001", "phase_008", "phase_006"],
            next_new_phase_id_floor="phase_011",
            attempt=1,
            max_attempts=2,
        )
        # Whitelist appears with all ids comma-separated.
        assert "phase_001" in out
        assert "phase_008" in out
        assert "phase_006" in out
        # Floor appears with the explicit value.
        assert "phase_011" in out
        # Wording that surfaces the architectural reason.
        assert "ledger slice" in out.lower()
        assert "historical" in out.lower()

    def test_handles_non_structured_error_message(self) -> None:
        """When the error message isn't the standard 'structural
        lineage violations:\\n  - ...' shape (e.g., a validate_lineage
        failure without bullets), the message becomes a single
        bullet so the model still sees the named cause."""
        from duplo.reauthor import _format_lineage_feedback_for_retry

        err = LineageValidationError(
            "lineage.phases has duplicate id 'phase_002b'"
        )
        out = _format_lineage_feedback_for_retry(
            error=err,
            prior_plan_ids=["phase_001"],
            next_new_phase_id_floor="phase_002",
            attempt=1,
            max_attempts=2,
        )
        assert "duplicate id 'phase_002b'" in out


@needs_bob_tools
class TestReauthorBoundedRetry(TestReauthorPlan):
    """End-to-end retry coverage. Inherits from TestReauthorPlan to
    reuse the _seed_ledger / _write_old_plan / _patch_council
    fixture helpers."""

    def test_reauthor_retries_once_on_lineage_validation_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First synthesizer attempt produces a lineage with an
        historical 'from' (phase_002 no longer in priors). Runtime
        rejects via _validate_lineage_structural and retries once
        with feedback. Second attempt produces valid lineage.
        Reauthor completes successfully. _invoke_council_for_reauthor
        was called TWICE; the second call received a
        previous_attempt_error string naming the violations."""
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir,
            plan_phases=["phase_001", "phase_006", "phase_007"],
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_006", "phase_007"]
        )

        from duplo import reauthor

        captured: list[dict[str, Any]] = []

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured.append(dict(kwargs))
            attempt_num = len(captured)
            if attempt_num == 1:
                plan_text = _wrap_synth_plan(
                    "## Phase phase_010: Refactored\n\n- [ ] x\n"
                )
                verdict: dict[str, Any] = {
                    "decision": "accept",
                    "feedback": "ok",
                    "agreements": [],
                    "disagreements": [],
                    "rejected_options": [],
                    "lineage": {
                        "phases": [
                            {
                                "id": "phase_010",
                                "action": "supersede",
                                "from": ["phase_002"],  # historical
                            }
                        ]
                    },
                }
                return plan_text, verdict
            plan_text = _wrap_synth_plan(
                "## Phase phase_010: Refactored from 006\n\n- [ ] y\n"
            )
            verdict = {
                "decision": "accept",
                "feedback": "ok",
                "agreements": [],
                "disagreements": [],
                "rejected_options": [],
                "lineage": {
                    "phases": [
                        {
                            "id": "phase_010",
                            "action": "supersede",
                            "from": ["phase_006"],
                        }
                    ]
                },
            }
            return plan_text, verdict

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        assert len(captured) == 2
        # First call had no feedback.
        assert captured[0].get("previous_attempt_error") is None
        # Second call had feedback that names the historical id and
        # the whitelist + floor.
        feedback = captured[1].get("previous_attempt_error")
        assert isinstance(feedback, str) and feedback
        assert "phase_002" in feedback
        assert "phase_001" in feedback
        assert "phase_006" in feedback
        assert "phase_007" in feedback
        # Reauthor succeeded; assembled plan contains phase_010.
        assert "phase_010" in plan_path.read_text()
        assert result.plan_reauthored_event_id

    def test_reauthor_fails_closed_after_one_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both attempts raise LineageValidationError (synthesizer
        keeps emitting bad lineage even with feedback). Reauthor
        raises after exhausting retries. PLAN.md unchanged. No
        plan_reauthored / lifecycle events emitted."""
        from bob_tools.ledger import Storage

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir,
            plan_phases=["phase_001", "phase_006", "phase_007"],
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_006", "phase_007"]
        )
        original_plan_text = plan_path.read_text()

        from duplo import reauthor

        captured: list[dict[str, Any]] = []

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured.append(dict(kwargs))
            plan_text = _wrap_synth_plan(
                "## Phase phase_010: Refactored\n\n- [ ] x\n"
            )
            verdict: dict[str, Any] = {
                "decision": "accept",
                "feedback": "ok",
                "agreements": [],
                "disagreements": [],
                "rejected_options": [],
                "lineage": {
                    "phases": [
                        {
                            "id": "phase_010",
                            "action": "supersede",
                            "from": ["phase_002"],  # historical, always
                        }
                    ]
                },
            }
            return plan_text, verdict

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(LineageValidationError):
            from duplo.reauthor import reauthor_plan

            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        # Two attempts ran (the retry budget is exhausted).
        assert len(captured) == 2
        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)


# ---------------------------------------------------------------------
# commit_attributions validator (Fix 1)
# ---------------------------------------------------------------------


class TestCommitAttributionsValidator:
    """Unit-level coverage of _validate_commit_attributions.

    Schema validation runs upstream (orchestra) and catches shape
    errors. This validator enforces the semantic contract: every
    commit_sha must prefix-match an unattributable commit in the
    triggering crossing's slice; every phase_id must be in the
    union of prior and new ids; rationale must be non-empty.
    """

    def test_accepts_valid_attribution(self) -> None:
        from duplo.reauthor import _validate_commit_attributions

        # No raise.
        _validate_commit_attributions(
            attributions=[
                {
                    "commit_sha": "abc1234",
                    "phase_id": "phase_004",
                    "rationale": "touches files added in phase_004",
                }
            ],
            triggering_unattributable_commits=[
                "abc1234def5678901234567890abcdef"
            ],
            prior_plan_ids=["phase_001", "phase_004"],
            new_plan_ids=["phase_005"],
        )

    def test_rejects_unknown_commit_sha(self) -> None:
        from duplo.reauthor import (
            CommitAttributionError,
            _validate_commit_attributions,
        )

        with pytest.raises(CommitAttributionError) as exc_info:
            _validate_commit_attributions(
                attributions=[
                    {
                        "commit_sha": "deadbee",
                        "phase_id": "phase_004",
                        "rationale": "x",
                    }
                ],
                triggering_unattributable_commits=[
                    "abc1234def5678901234567890abcdef"
                ],
                prior_plan_ids=["phase_001", "phase_004"],
                new_plan_ids=[],
            )
        msg = str(exc_info.value)
        assert "deadbee" in msg
        assert "abc1234def" in msg  # known commit list named

    def test_rejects_unknown_phase_id(self) -> None:
        from duplo.reauthor import (
            CommitAttributionError,
            _validate_commit_attributions,
        )

        with pytest.raises(CommitAttributionError) as exc_info:
            _validate_commit_attributions(
                attributions=[
                    {
                        "commit_sha": "abc1234",
                        "phase_id": "phase_999",
                        "rationale": "x",
                    }
                ],
                triggering_unattributable_commits=["abc1234def"],
                prior_plan_ids=["phase_001"],
                new_plan_ids=["phase_002"],
            )
        msg = str(exc_info.value)
        assert "phase_999" in msg
        assert "phase_001" in msg  # whitelist named
        assert "phase_002" in msg

    def test_rejects_empty_rationale(self) -> None:
        from duplo.reauthor import (
            CommitAttributionError,
            _validate_commit_attributions,
        )

        with pytest.raises(CommitAttributionError, match="rationale"):
            _validate_commit_attributions(
                attributions=[
                    {
                        "commit_sha": "abc1234",
                        "phase_id": "phase_001",
                        "rationale": "   ",  # whitespace-only
                    }
                ],
                triggering_unattributable_commits=["abc1234def"],
                prior_plan_ids=["phase_001"],
                new_plan_ids=[],
            )

    def test_accumulates_violations(self) -> None:
        """All errors surface in one raise so the synthesizer or a
        human debugger sees the full picture, matching the pattern
        of _validate_lineage_structural."""
        from duplo.reauthor import (
            CommitAttributionError,
            _validate_commit_attributions,
        )

        with pytest.raises(CommitAttributionError) as exc_info:
            _validate_commit_attributions(
                attributions=[
                    {
                        "commit_sha": "deadbee",  # unknown commit
                        "phase_id": "phase_999",  # unknown phase
                        "rationale": "",  # empty
                    }
                ],
                triggering_unattributable_commits=["abc1234def"],
                prior_plan_ids=["phase_001"],
                new_plan_ids=[],
            )
        msg = str(exc_info.value)
        assert "deadbee" in msg
        assert "phase_999" in msg
        assert "rationale" in msg

    def test_empty_attributions_is_noop(self) -> None:
        """An empty or missing commit_attributions array is a
        no-op — the synthesizer doesn't have to emit attributions
        unless the triggering crossing is unattributable_commit."""
        from duplo.reauthor import _validate_commit_attributions

        _validate_commit_attributions(
            attributions=[],
            triggering_unattributable_commits=[],
            prior_plan_ids=["phase_001"],
            new_plan_ids=[],
        )

    def test_prefix_match_short_sha_against_long_sha(self) -> None:
        """The schema allows 7-64 char commit_sha. Most synthesizers
        will pick the short form (first 7 chars). The validator
        accepts prefix match in either direction so the synthesizer
        can use whatever the ledger slice happens to show."""
        from duplo.reauthor import _validate_commit_attributions

        # No raise — abc1234 (7 chars) prefix-matches the full sha.
        _validate_commit_attributions(
            attributions=[
                {
                    "commit_sha": "abc1234",
                    "phase_id": "phase_001",
                    "rationale": "ok",
                }
            ],
            triggering_unattributable_commits=[
                "abc1234def5678901234567890abcdef0123"
            ],
            prior_plan_ids=["phase_001"],
            new_plan_ids=[],
        )

    def test_is_exported(self) -> None:
        """CommitAttributionError is in __all__ so static analysis
        and public-API consumers can find it without resorting to
        private-name imports."""
        import duplo.reauthor as reauthor_mod

        assert "CommitAttributionError" in reauthor_mod.__all__
        from duplo.reauthor import CommitAttributionError, ReauthorError

        assert issubclass(CommitAttributionError, ReauthorError)


# ---------------------------------------------------------------------
# Schema-failure classifier (Fix 2)
#
# The classifier maps orchestra's validation_errors strings to a
# fixed enum so the retry feedback can name the failure kind. The
# strings orchestra emits are stable; see orchestra/schema.py and
# the smoke-test that pins those formats in the orchestra suite.
# ---------------------------------------------------------------------


class TestSchemaFailureClassifier:
    """Unit coverage for classify_schema_failure: maps validator
    error strings to SchemaFailureKind."""

    def test_classifies_additional_properties(self) -> None:
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            [
                "lineage.phases[3].attributed_commits: additional "
                "property not permitted"
            ]
        )
        assert result.primary is SchemaFailureKind.ADDITIONAL_PROPERTIES
        assert SchemaFailureKind.ADDITIONAL_PROPERTIES in result.kinds

    def test_classifies_enum_mismatch(self) -> None:
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            ["decision: value 'maybe' is not in enum ['accept', 'reframe', 'stuck']"]
        )
        assert result.primary is SchemaFailureKind.ENUM_MISMATCH

    def test_classifies_missing_required(self) -> None:
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            ["feedback: required field missing"]
        )
        assert result.primary is SchemaFailureKind.MISSING_REQUIRED

    def test_classifies_malformed_array(self) -> None:
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            ["agreements: expected array, got str"]
        )
        assert result.primary is SchemaFailureKind.MALFORMED_ARRAY

    def test_classifies_json_parse_outcome(self) -> None:
        """outcome='parse_error' short-circuits the per-error classifier
        and returns JSON_PARSE regardless of the error strings (which
        for a parse failure are jsonschema-irrelevant chunks)."""
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            ["Expecting value: line 1 column 1 (char 0)"],
            outcome="parse_error",
        )
        assert result.primary is SchemaFailureKind.JSON_PARSE

    def test_aggregates_multiple_kinds(self) -> None:
        """A verdict commonly accumulates several violations of
        different kinds; the classifier surfaces them all so the
        retry feedback can name each."""
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            [
                "lineage.phases[0].status: additional property not permitted",
                "feedback: required field missing",
                "decision: value 'maybe' is not in enum ['accept']",
            ]
        )
        assert result.primary is SchemaFailureKind.ADDITIONAL_PROPERTIES
        assert SchemaFailureKind.ADDITIONAL_PROPERTIES in result.kinds
        assert SchemaFailureKind.MISSING_REQUIRED in result.kinds
        assert SchemaFailureKind.ENUM_MISMATCH in result.kinds

    def test_unrecognized_error_falls_back_to_other(self) -> None:
        from duplo.schema_classification import (
            SchemaFailureKind,
            classify_schema_failure,
        )

        result = classify_schema_failure(
            ["some_path: unknown structural rejection from a future schema"]
        )
        assert result.primary is SchemaFailureKind.OTHER

    def test_read_schema_validation_failures_from_log(
        self, tmp_path: Path
    ) -> None:
        """read_schema_validation_failures scans log.jsonl, surfaces
        schema_validation records whose outcome is not 'valid', and
        skips a truncated final line (single-fsync crash window
        described in orchestra/log/log.py)."""
        import json as _json

        from duplo.schema_classification import (
            read_schema_validation_failures,
        )

        log_path = tmp_path / "log.jsonl"
        lines = [
            _json.dumps(
                {
                    "ts": "2026-05-11T00:00:00.000Z",
                    "run_id": "r1",
                    "seq": 1,
                    "event": "state_entry",
                    "state_id": "synth",
                    "attempt": 1,
                }
            ),
            _json.dumps(
                {
                    "ts": "2026-05-11T00:00:01.000Z",
                    "run_id": "r1",
                    "seq": 2,
                    "event": "schema_validation",
                    "state_id": "synth",
                    "attempt": 1,
                    "artifact": "judge_verdict",
                    "outcome": "schema_error",
                    "decision": None,
                    "validation_errors": [
                        "lineage.phases[3].status: additional "
                        "property not permitted"
                    ],
                    "payload_ref": None,
                    "invocation_id": "i1",
                }
            ),
            _json.dumps(
                {
                    "ts": "2026-05-11T00:00:02.000Z",
                    "run_id": "r1",
                    "seq": 3,
                    "event": "schema_validation",
                    "state_id": "synth",
                    "attempt": 1,
                    "artifact": "judge_verdict",
                    "outcome": "valid",
                    "decision": "accept",
                    "validation_errors": [],
                    "payload_ref": None,
                    "invocation_id": "i2",
                }
            ),
        ]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        failures = read_schema_validation_failures(log_path)
        assert len(failures) == 1
        assert failures[0].outcome == "schema_error"
        assert failures[0].state_id == "synth"
        assert failures[0].artifact == "judge_verdict"
        assert "additional property" in failures[0].validation_errors[0]


# ---------------------------------------------------------------------
# Schema-failure retry (Fix 2)
#
# SchemaValidationError shares the bounded-retry budget with
# LineageValidationError. max_attempts=2 means the run gets ONE
# retry total across both error classes. The retry feedback for
# schema failures is kind-aware (additional_properties names the
# lineage.phases[] / commit_attributions split, etc.).
# ---------------------------------------------------------------------


class TestReauthorSchemaRetry(TestReauthorPlan):
    """End-to-end retry coverage for schema-validation failures."""

    def _make_schema_error(
        self,
        validation_errors: list[str],
        *,
        outcome: str = "schema_error",
    ) -> Any:
        from duplo.reauthor import SchemaValidationError
        from duplo.schema_classification import (
            SchemaFailure,
            classify_schema_failure,
        )

        cls = classify_schema_failure(validation_errors, outcome=outcome)
        failure = SchemaFailure(
            state_id="synth",
            attempt=1,
            outcome=outcome,
            artifact="judge_verdict",
            validation_errors=tuple(validation_errors),
        )
        return SchemaValidationError(
            "fake schema error for test",
            classification=cls,
            failures=(failure,),
        )

    def test_retries_once_on_schema_validation_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First attempt fails schema validation; runtime retries
        once with kind-named feedback; second attempt succeeds.
        Both attempts share the same budget that lineage failures
        consume."""
        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir,
            plan_phases=["phase_001", "phase_006", "phase_007"],
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_006", "phase_007"]
        )

        from duplo import reauthor

        captured: list[dict[str, Any]] = []

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured.append(dict(kwargs))
            attempt_num = len(captured)
            if attempt_num == 1:
                raise self._make_schema_error(
                    [
                        "lineage.phases[3].attributed_commits: "
                        "additional property not permitted"
                    ]
                )
            plan_text = _wrap_synth_plan(
                "## Phase phase_010: Refactored from 006\n\n- [ ] y\n"
            )
            verdict = {
                "decision": "accept",
                "feedback": "ok",
                "agreements": [],
                "disagreements": [],
                "rejected_options": [],
                "lineage": {
                    "phases": [
                        {
                            "id": "phase_010",
                            "action": "supersede",
                            "from": ["phase_006"],
                        }
                    ]
                },
            }
            return plan_text, verdict

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        from duplo.reauthor import reauthor_plan

        result = reauthor_plan(
            plan_path=plan_path,
            ledger_dir=ledger_dir,
            crossing_event_id=crossing_id,
            project_dir=tmp_path,
        )

        assert len(captured) == 2
        assert captured[0].get("previous_attempt_error") is None
        feedback = captured[1].get("previous_attempt_error")
        assert isinstance(feedback, str) and feedback
        # Kind name surfaces in the feedback.
        assert "additional_properties" in feedback
        # Raw error string is included verbatim.
        assert "attributed_commits" in feedback
        # Retry-attempt header present.
        assert "RETRY ATTEMPT 2 of 2" in feedback
        # Reauthor succeeded.
        assert "phase_010" in plan_path.read_text()
        assert result.plan_reauthored_event_id

    def test_shared_budget_lineage_then_schema_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Attempt 1 fails with LineageValidationError, attempt 2
        fails with SchemaValidationError. The shared budget caps at
        ONE retry total, so reauthor raises after the second failure.
        No third attempt. PLAN.md unchanged."""
        from bob_tools.ledger import Storage

        from duplo.reauthor import SchemaValidationError

        ledger_dir = tmp_path / "ledger"
        crossing_id, _, _ = self._seed_ledger(
            ledger_dir,
            plan_phases=["phase_001", "phase_006", "phase_007"],
        )
        plan_path = tmp_path / "PLAN.md"
        self._write_old_plan(
            plan_path, ["phase_001", "phase_006", "phase_007"]
        )
        original_plan_text = plan_path.read_text()

        from duplo import reauthor

        captured: list[dict[str, Any]] = []

        def fake_invoke(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            captured.append(dict(kwargs))
            attempt_num = len(captured)
            if attempt_num == 1:
                # Attempt 1: produces lineage with historical 'from'
                # — _validate_lineage_structural will raise
                # LineageValidationError after _invoke returns.
                plan_text = _wrap_synth_plan(
                    "## Phase phase_010: Bad\n\n- [ ] x\n"
                )
                verdict: dict[str, Any] = {
                    "decision": "accept",
                    "feedback": "ok",
                    "agreements": [],
                    "disagreements": [],
                    "rejected_options": [],
                    "lineage": {
                        "phases": [
                            {
                                "id": "phase_010",
                                "action": "supersede",
                                "from": ["phase_002"],  # historical
                            }
                        ]
                    },
                }
                return plan_text, verdict
            # Attempt 2: orchestra rejects the verdict outright.
            raise self._make_schema_error(
                ["decision: value 'unknown' is not in enum [...]"]
            )

        monkeypatch.setattr(
            reauthor, "_invoke_council_for_reauthor", fake_invoke
        )

        events_before = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        with pytest.raises(SchemaValidationError):
            from duplo.reauthor import reauthor_plan

            reauthor_plan(
                plan_path=plan_path,
                ledger_dir=ledger_dir,
                crossing_event_id=crossing_id,
                project_dir=tmp_path,
            )

        # Two attempts total (budget exhausted after attempt 2).
        assert len(captured) == 2
        # First call had no feedback; second received lineage feedback.
        assert captured[0].get("previous_attempt_error") is None
        assert "phase_002" in (
            captured[1].get("previous_attempt_error") or ""
        )
        # Atomicity: PLAN.md unchanged, no new events.
        assert plan_path.read_text() == original_plan_text
        events_after = list(
            Storage(ledger_dir, writer_id="probe").read_all()
        )
        assert len(events_after) == len(events_before)

    def test_schema_validation_error_is_exported(self) -> None:
        """SchemaValidationError is in __all__ and subclasses
        ReauthorError so callers catching the parent class continue
        to do the right thing."""
        import duplo.reauthor as reauthor_mod

        assert "SchemaValidationError" in reauthor_mod.__all__
        assert "SchemaFailureKind" in reauthor_mod.__all__
        from duplo.reauthor import ReauthorError, SchemaValidationError

        assert issubclass(SchemaValidationError, ReauthorError)
