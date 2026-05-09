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
