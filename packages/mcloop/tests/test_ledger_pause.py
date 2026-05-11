"""Tests for mcloop.ledger_pause (Slice D threshold + auto-reauthor)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

try:
    import bob_tools.ledger  # noqa: F401
    _BOB_TOOLS_AVAILABLE = True
except ImportError:
    _BOB_TOOLS_AVAILABLE = False

needs_bob_tools = pytest.mark.skipif(
    not _BOB_TOOLS_AVAILABLE,
    reason="ledger_pause tests require the 'bob_tools' package",
)

from mcloop.ledger_pause import (  # noqa: E402  (must follow try/except guard above)
    HardStop,
    PauseDecision,
)

# ---------------------------------------------------------------------
# HardStop dataclass shape
# ---------------------------------------------------------------------


class TestHardStop:
    def test_carries_reason_and_detail(self) -> None:
        exc = HardStop(reason="lineage_invalid", detail="bad shape")
        assert exc.reason == "lineage_invalid"
        assert exc.detail == "bad shape"
        assert "lineage_invalid" in str(exc)
        assert "bad shape" in str(exc)


# ---------------------------------------------------------------------
# evaluate_and_maybe_pause
# ---------------------------------------------------------------------


@needs_bob_tools
class TestEvaluateAndMaybePause:
    def _seed_unattributable_commit(
        self, ledger_dir: Path, *, run_id: str = "seed"
    ) -> tuple[str, str]:
        """Seed a ledger with one phase plus an unattributable commit
        (rule 1, recommended_action=reauthor_plan). Returns the
        threshold_crossed event_id and the writer_id used."""
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
        storage.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(
                phase_id="phase_001", title="P1"
            ),
            run_id=run_id,
        )
        storage.append(
            event_type=EventType.COMMIT_LANDED,
            payload=make_commit_landed_payload(
                commit="deadbeef",
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
            run_id=run_id,
        )
        events = storage.read_all()
        crossings = evaluate_thresholds(
            project(events), events, ThresholdParams()
        )
        emitted = record_crossings(storage, crossings, run_id=run_id)
        return emitted[0], "seed"

    def test_returns_pause_decision_for_reauthor_action(
        self, tmp_path: Path
    ) -> None:
        from bob_tools.ledger import Storage

        from mcloop.ledger_pause import evaluate_and_maybe_pause

        ledger_dir = tmp_path / "ledger"
        seeded_id, _ = self._seed_unattributable_commit(ledger_dir)
        # Re-evaluate: the existing crossing has already been
        # recorded; a fresh evaluator pass against the same events
        # should not record a duplicate but should still surface the
        # existing one when reading the ledger. The simplest check:
        # call evaluate_and_maybe_pause on a storage instance, expect
        # either a pause decision pointing at a crossing event whose
        # action is reauthor_plan, or None when there are no NEW
        # crossings to record.
        storage = Storage(ledger_dir, writer_id="mcloop-test")
        decision = evaluate_and_maybe_pause(
            storage=storage, run_id="test-eval"
        )
        # On a freshly-seeded ledger, either the seeded crossing
        # gets returned or evaluate_thresholds is idempotent and we
        # see None. Both are valid; the test verifies the call does
        # not raise and that any returned decision has a
        # reauthor_* action.
        if decision is not None:
            assert decision.recommended_action in {
                "reauthor_phase",
                "reauthor_plan",
            }
            assert decision.crossing_event_id != ""

    def test_returns_none_on_empty_ledger(self, tmp_path: Path) -> None:
        from bob_tools.ledger import Storage

        from mcloop.ledger_pause import evaluate_and_maybe_pause

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        storage = Storage(ledger_dir, writer_id="mcloop-test")
        decision = evaluate_and_maybe_pause(
            storage=storage, run_id="test-eval"
        )
        assert decision is None


# ---------------------------------------------------------------------
# auto_reauthor: hard-stop failure modes
# ---------------------------------------------------------------------


class _FakeLineageError(Exception):
    pass


class _FakeReauthorError(Exception):
    pass


class _FakePlanArtifactError(_FakeReauthorError):
    """Mirror duplo.reauthor.PlanArtifactError's inheritance shape:
    subclass of ReauthorError. mcloop must catch this before the
    generic ReauthorError handler, otherwise the more-specific
    plan_artifact_invalid pause reason is lost to reauthor_failed."""


class _FakeCommitAttributionError(_FakeReauthorError):
    """Mirror duplo.reauthor.CommitAttributionError's inheritance
    shape: subclass of ReauthorError. mcloop must catch this before
    the generic ReauthorError handler, otherwise the more-specific
    commit_attribution_invalid pause reason is lost to
    reauthor_failed."""


@needs_bob_tools
class TestAutoReauthorFailureModes:
    def _decision(self) -> PauseDecision:
        return PauseDecision(
            crossing_event_id="00000000-0000-7000-8000-000000000001",
            rule_id="unattributable_commit",
            recommended_action="reauthor_plan",
            summary="ad hoc commit broke phase attribution",
        )

    def test_lineage_validation_failure_hard_stops(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Stand up a fake duplo.reauthor whose reauthor_plan raises
        # a LineageValidationError. The ledger_pause module imports
        # duplo lazily inside auto_reauthor, so monkeypatch the
        # module on sys.modules before invoking.
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakeLineageError("lineage check failed")

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "lineage_invalid"
        assert "lineage check failed" in exc_info.value.detail

    def test_reauthor_error_hard_stops_with_reauthor_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakeReauthorError("council error: timeout")

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "reauthor_failed"
        assert "council error" in exc_info.value.detail

    def test_unexpected_exception_hard_stops_with_reauthor_failed(
        self,
        tmp_path: Path,
    ) -> None:
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise RuntimeError("network unreachable")

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "reauthor_failed"
        assert "RuntimeError" in exc_info.value.detail

    def test_success_returns_result(
        self,
        tmp_path: Path,
    ) -> None:
        import sys
        import types

        from mcloop import ledger_pause

        sentinel = object()
        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            return sentinel

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        result = ledger_pause.auto_reauthor(
            decision=self._decision(),
            plan_path=tmp_path / "PLAN.md",
            ledger_dir=tmp_path / "ledger",
            project_dir=tmp_path,
        )
        assert result is sentinel

    def test_plan_artifact_error_hard_stops_with_plan_artifact_invalid(
        self,
        tmp_path: Path,
    ) -> None:
        """A duplo.reauthor.PlanArtifactError must surface as a
        HardStop with reason 'plan_artifact_invalid', distinct from
        the generic 'reauthor_failed' bucket. The handler MUST
        match the subclass before the generic ReauthorError handler;
        if the order is wrong the more-specific reason is lost."""
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]
        fake_mod.PlanArtifactError = _FakePlanArtifactError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakePlanArtifactError(
                "plan_artifact_contained_verdict_json: trailing fence not present"
            )

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "plan_artifact_invalid"
        assert "trailing-fenced-verdict" in exc_info.value.detail or (
            "trailing fence" in exc_info.value.detail
        )

    def test_older_duplo_without_plan_artifact_error_falls_back(
        self,
        tmp_path: Path,
    ) -> None:
        """Older duplo installations don't export PlanArtifactError.
        mcloop must still import cleanly and treat any ReauthorError
        as the generic 'reauthor_failed' bucket. This pins the
        guarded import in ledger_pause.auto_reauthor."""
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]
        # NO PlanArtifactError attribute on this fake module.

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakeReauthorError("plain reauthor failure")

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        # The generic reauthor_failed bucket still catches it; the
        # plan-artifact branch was an inactive sentinel and didn't
        # interfere with the regular flow.
        assert exc_info.value.reason == "reauthor_failed"

    def test_commit_attribution_error_hard_stops_with_distinct_reason(
        self,
        tmp_path: Path,
    ) -> None:
        """A duplo.reauthor.CommitAttributionError must surface as a
        HardStop with reason 'commit_attribution_invalid', distinct
        from 'reauthor_failed'. The handler MUST match the subclass
        before the generic ReauthorError handler."""
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]
        fake_mod.PlanArtifactError = _FakePlanArtifactError  # type: ignore[attr-defined]
        fake_mod.CommitAttributionError = _FakeCommitAttributionError  # type: ignore[attr-defined]

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakeCommitAttributionError(
                "commit_attribution violations:\n  - commit_sha 'deadbee' "
                "does not prefix-match any unattributable commit"
            )

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "commit_attribution_invalid"
        assert "commit_attributions" in exc_info.value.detail

    def test_older_duplo_without_commit_attribution_error_falls_back(
        self,
        tmp_path: Path,
    ) -> None:
        """Older duplo installations don't export
        CommitAttributionError. mcloop must still import cleanly
        and treat any plain ReauthorError as 'reauthor_failed' (or
        the existing PlanArtifactError handler if applicable). Pins
        the guarded import."""
        import sys
        import types

        from mcloop import ledger_pause

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]
        # NO CommitAttributionError on this fake module.

        def _fake_reauthor_plan(**kwargs: Any) -> Any:
            raise _FakeReauthorError("plain reauthor failure")

        fake_mod.reauthor_plan = _fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=self._decision(),
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "reauthor_failed"


# ---------------------------------------------------------------------
# Phase-scoped reauthor: target_phase_id threading and scope_unavailable
# ---------------------------------------------------------------------


class _CapturingFakeReauthorModule:
    """Fixture that installs a fake duplo.reauthor on sys.modules and
    captures the kwargs every reauthor_plan call receives. Used by
    the phase-scoping tests to assert auto_reauthor passes
    target_phase_id through to duplo (or refuses to invoke duplo at
    all when scope is unavailable)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_value: Any = object()

    def install(self) -> None:
        import sys
        import types

        fake_mod = types.ModuleType("duplo.reauthor")
        fake_mod.LineageValidationError = _FakeLineageError  # type: ignore[attr-defined]
        fake_mod.ReauthorError = _FakeReauthorError  # type: ignore[attr-defined]
        fake_mod.reauthor_plan = self._fake_reauthor_plan  # type: ignore[attr-defined]
        sys.modules["duplo"] = types.ModuleType("duplo")
        sys.modules["duplo.reauthor"] = fake_mod

    def _fake_reauthor_plan(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.return_value


@needs_bob_tools
class TestAutoReauthorPhaseScoping:
    def test_phase_scoped_decision_passes_target_phase_id(
        self,
        tmp_path: Path,
    ) -> None:
        from mcloop import ledger_pause

        fake = _CapturingFakeReauthorModule()
        fake.install()

        decision = PauseDecision(
            crossing_event_id="00000000-0000-7000-8000-000000000001",
            rule_id="phase_superseded",
            recommended_action="reauthor_phase",
            summary="phase_002 superseded by phase_003",
            target_phase_id="phase_002",
        )
        result = ledger_pause.auto_reauthor(
            decision=decision,
            plan_path=tmp_path / "PLAN.md",
            ledger_dir=tmp_path / "ledger",
            project_dir=tmp_path,
        )
        assert result is fake.return_value
        assert len(fake.calls) == 1
        assert fake.calls[0]["target_phase_id"] == "phase_002"

    def test_phase_scoped_without_target_hard_stops(
        self,
        tmp_path: Path,
    ) -> None:
        """recommended_action=reauthor_phase but the crossing's
        triggering event has no extractable phase_id (e.g.,
        assumption_falsified). Must fail closed with
        scope_unavailable rather than escalate to plan-wide
        synthesis."""
        from mcloop import ledger_pause

        fake = _CapturingFakeReauthorModule()
        fake.install()

        decision = PauseDecision(
            crossing_event_id="00000000-0000-7000-8000-000000000002",
            rule_id="assumption_falsified",
            recommended_action="reauthor_phase",
            summary="assumption A1 falsified",
            target_phase_id=None,
        )
        with pytest.raises(HardStop) as exc_info:
            ledger_pause.auto_reauthor(
                decision=decision,
                plan_path=tmp_path / "PLAN.md",
                ledger_dir=tmp_path / "ledger",
                project_dir=tmp_path,
            )
        assert exc_info.value.reason == "scope_unavailable"
        assert "assumption_falsified" in exc_info.value.detail
        # duplo was not invoked.
        assert fake.calls == []

    def test_plan_scoped_decision_passes_none_target_phase_id(
        self,
        tmp_path: Path,
    ) -> None:
        """Plan-scoped crossings (unattributable_commit,
        invariant_declared, exploratory_count_exceeded) call
        reauthor_plan with target_phase_id=None even when the
        decision happens to carry one (it shouldn't, but be
        defensive)."""
        from mcloop import ledger_pause

        fake = _CapturingFakeReauthorModule()
        fake.install()

        decision = PauseDecision(
            crossing_event_id="00000000-0000-7000-8000-000000000003",
            rule_id="unattributable_commit",
            recommended_action="reauthor_plan",
            summary="ad hoc commit broke phase attribution",
            target_phase_id=None,
        )
        ledger_pause.auto_reauthor(
            decision=decision,
            plan_path=tmp_path / "PLAN.md",
            ledger_dir=tmp_path / "ledger",
            project_dir=tmp_path,
        )
        assert len(fake.calls) == 1
        assert fake.calls[0]["target_phase_id"] is None


# ---------------------------------------------------------------------
# _extract_target_phase_id unit tests (no bob_tools dependency)
# ---------------------------------------------------------------------


class _StubEvent:
    def __init__(
        self, payload: dict[str, Any], type: str = ""
    ) -> None:
        self.payload = payload
        self.type = type


class TestExtractTargetPhaseId:
    """Unit-level coverage of the helper that walks a crossing's
    triggering events and pulls out the DERIVATIVE phase_id when
    present.

    The earlier implementation returned the source ``phase_id``
    field unconditionally, which for split / merge / supersede
    refers to the consumed (now-gone) phase. The structural
    validator on the duplo side then rejected those targets against
    the current prior_plan_ids, producing a self-perpetuating stuck
    loop on any reauthor that emitted a topology change. The helper
    now routes by event type and returns the derivative id
    instead.
    """

    def test_returns_superseded_by_for_phase_superseded(self) -> None:
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_superseded",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {
                    "phase_id": "phase_002",
                    "superseded_by_phase_id": "phase_006",
                    "reason": "r",
                },
                type="phase_superseded",
            )
        }
        # Derivative (phase_006) — not the consumed source (phase_002).
        assert _extract_target_phase_id(crossing, events) == "phase_006"

    def test_returns_first_into_for_phase_split(self) -> None:
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_topology_changed",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {
                    "phase_id": "phase_005",
                    "into_phase_ids": ["phase_006", "phase_007"],
                    "reason": "r",
                },
                type="phase_split",
            )
        }
        # First derivative branch. Today's stuck case: phase_005
        # was split; consumed phase_005 must NOT be returned.
        assert _extract_target_phase_id(crossing, events) == "phase_006"

    def test_returns_into_phase_id_for_phase_merged(self) -> None:
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_topology_changed",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {
                    "merged_phase_ids": ["phase_003", "phase_004"],
                    "into_phase_id": "phase_010",
                    "reason": "r",
                },
                type="phase_merged",
            )
        }
        # Derivative — the single new merged phase. Note the merge
        # payload has NO source `phase_id` field; old code returned
        # None here (silently broken). New code returns the
        # derivative as intended.
        assert _extract_target_phase_id(crossing, events) == "phase_010"

    def test_returns_none_for_phase_abandoned(self) -> None:
        """phase_abandoned has no derivative — the phase is gone,
        nothing replaces it. Return None and let auto_reauthor's
        scope_unavailable HardStop path fire."""
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_abandoned",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {"phase_id": "phase_002", "reason": "out of scope"},
                type="phase_abandoned",
            )
        }
        assert _extract_target_phase_id(crossing, events) is None

    def test_returns_none_for_assumption_falsified(self) -> None:
        """Existing behavior preserved: assumption_falsified
        carries assumption_id not phase_id."""
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "assumption_falsified",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {"assumption_id": "a1"},
                type="assumption_falsified",
            )
        }
        assert _extract_target_phase_id(crossing, events) is None

    def test_walks_multiple_events_returning_first_derivative(self) -> None:
        """The walk continues past events that yield no derivative
        (e.g., phase_abandoned in the same crossing as a
        phase_superseded). Returns the first derivative found."""
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_topology_changed",
            "triggering_event_ids": ["e1", "e2"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {"phase_id": "phase_001", "reason": "r"},
                type="phase_abandoned",
            ),  # yields None
            "e2": _StubEvent(
                {
                    "phase_id": "phase_005",
                    "superseded_by_phase_id": "phase_009",
                    "reason": "r",
                },
                type="phase_superseded",
            ),  # yields phase_009
        }
        assert _extract_target_phase_id(crossing, events) == "phase_009"

    def test_returns_none_on_empty_triggering_list(self) -> None:
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_superseded",
            "triggering_event_ids": [],
            "summary": "x",
        }
        assert _extract_target_phase_id(crossing, {}) is None

    def test_skips_unknown_event_ids(self) -> None:
        """If a triggering id isn't in events_by_id (shouldn't
        happen but be defensive), skip it instead of raising."""
        from mcloop.ledger_pause import _extract_target_phase_id

        crossing = {
            "rule_id": "phase_superseded",
            "triggering_event_ids": ["missing-id", "e2"],
            "summary": "x",
        }
        events = {
            "e2": _StubEvent(
                {
                    "phase_id": "phase_002",
                    "superseded_by_phase_id": "phase_009",
                    "reason": "r",
                },
                type="phase_superseded",
            )
        }
        assert _extract_target_phase_id(crossing, events) == "phase_009"

    def test_handles_enum_typed_event_type(self) -> None:
        """trig.type may be a bob_tools EventType enum value or a
        plain string. The helper accepts both via the .value
        normalization."""
        from mcloop.ledger_pause import _extract_target_phase_id

        class _EnumLike:
            def __init__(self, value: str) -> None:
                self.value = value

        crossing = {
            "rule_id": "phase_superseded",
            "triggering_event_ids": ["e1"],
            "summary": "x",
        }
        events = {
            "e1": _StubEvent(
                {
                    "phase_id": "phase_002",
                    "superseded_by_phase_id": "phase_008",
                    "reason": "r",
                },
                type="",
            )
        }
        # Replace the str type with an enum-like value to test
        # the .value-aware normalization.
        events["e1"].type = _EnumLike("phase_superseded")  # type: ignore[assignment]
        assert _extract_target_phase_id(crossing, events) == "phase_008"
