"""Tests for duplo.plan_gate: the bounded post-assembly corrective gate.

Covers T-000007: repair the deterministically-repairable defect classes
(verify-without-build; duplicate / non-sequential phase_ids), re-validate
exactly once, proceed if clean; hard stop (no retry) on a persistent
failure or any unrepairable class (scope item built by no phase).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duplo import plan_gate
from duplo.plan_gate import (
    PlanSanityHardStop,
    enforce_plan_sanity,
    run_plan_sanity_gate,
)
from duplo.plan_sanity import check_plan_sanity


def _plan(*phases: str) -> str:
    return "<!-- bob-plan-format: 1 -->\n\n# MyApp\n\n" + "\n".join(phases)


def _phase(n: int, title: str, body: str, *, phase_id: int | None = None) -> str:
    pid = phase_id if phase_id is not None else n
    return f"## Phase {n}: {title}\n<!-- phase_id: phase_{pid:03d} -->\n\n{body}\n"


# --- Clean ----------------------------------------------------------------


def test_clean_plan_passes_unchanged() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]'),
        _phase(2, "Polish", '- [ ] T-000002: Add dark mode [feat: "Dark mode"]'),
    )
    outcome = run_plan_sanity_gate(plan, scope_include=["Unit conversion", "Dark mode"])
    assert outcome.status == "clean"
    assert outcome.plan_text == plan
    assert outcome.changes == []


# --- Repairable: one pass yields a clean plan -----------------------------


def test_verify_without_build_repaired_in_one_pass() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency converts [feat: "Currency exchange"]',
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "repaired"
    assert check_plan_sanity(outcome.plan_text).ok
    # The orphan verify task is gone; the build task survives.
    assert "currency converts" not in outcome.plan_text
    assert "Add dark mode" in outcome.plan_text
    assert any("verify-without-build" in c for c in outcome.changes)
    assert outcome.report_after is not None and outcome.report_after.ok


def test_duplicate_phase_ids_repaired_in_one_pass() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: a [feat: "A"]', phase_id=1),
        _phase(2, "More", '- [ ] T-000002: b [feat: "B"]', phase_id=1),
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "repaired"
    assert check_plan_sanity(outcome.plan_text).ok
    assert "<!-- phase_id: phase_002 -->" in outcome.plan_text
    assert any(c.startswith("phase_ids:") for c in outcome.changes)


def test_both_repairable_classes_repaired_together() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency [feat: "Currency"]',
            phase_id=1,
        ),
        _phase(2, "More", "- [ ] T-000003: scaffold the project", phase_id=1),
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "repaired"
    assert check_plan_sanity(outcome.plan_text).ok
    assert len(outcome.changes) == 2


# --- Unrepairable: hard stop, no repair attempted -------------------------


def test_uncovered_scope_hard_stops_without_repair() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    outcome = run_plan_sanity_gate(plan, scope_include=["Unit conversion", "Currency exchange"])
    assert outcome.status == "hard_stop"
    assert outcome.changes == []
    assert outcome.plan_text == plan
    assert outcome.report_after is None  # no repair was attempted
    assert outcome.stop_report is not None
    assert "Currency exchange" in outcome.stop_report
    assert "does not retry" in outcome.stop_report


def test_mixed_repairable_and_unrepairable_hard_stops_without_repair() -> None:
    # Scope-uncovered (unrepairable) alongside duplicate phase_ids
    # (repairable): the unrepairable class forces an immediate stop and the
    # repairable one is NOT silently fixed.
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add dark mode [feat: "Dark mode"]', phase_id=1),
        _phase(2, "More", "- [ ] T-000002: scaffold", phase_id=1),
    )
    outcome = run_plan_sanity_gate(plan, scope_include=["Offline sync"])
    assert outcome.status == "hard_stop"
    assert outcome.changes == []
    assert outcome.plan_text == plan


# --- Persistent failure: repaired once, still dirty, no retry -------------


def test_persistent_failure_hard_stops_after_single_repair(monkeypatch) -> None:
    # Force the repair to be a no-op so the plan stays dirty after the one
    # allowed pass; the gate must hard-stop, not loop.
    monkeypatch.setattr(plan_gate, "_repair", lambda text, kinds: (text, []))
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency [feat: "Currency exchange"]',
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "hard_stop"
    assert outcome.report_after is not None  # a repair pass DID run
    assert not outcome.report_after.ok
    assert "single deterministic repair pass" in outcome.stop_report


# --- enforce_plan_sanity: filesystem-facing wrapper -----------------------


def _write_plan(tmp_path: Path, text: str) -> Path:
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(text, encoding="utf-8")
    return plan_path


def test_enforce_clean_leaves_file_untouched(tmp_path: Path) -> None:
    plan = _plan(_phase(1, "Core", "- [ ] T-000001: scaffold the project"))
    plan_path = _write_plan(tmp_path, plan)
    outcome = enforce_plan_sanity(target_dir=tmp_path)
    assert outcome.status == "clean"
    assert plan_path.read_text(encoding="utf-8") == plan


def test_enforce_repaired_writes_back_and_commits(tmp_path: Path, monkeypatch) -> None:
    committed: list[str] = []
    monkeypatch.setattr(plan_gate, "_commit_repair", lambda path: committed.append(str(path)))
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency converts [feat: "Currency exchange"]',
        )
    )
    plan_path = _write_plan(tmp_path, plan)
    outcome = enforce_plan_sanity(target_dir=tmp_path)
    assert outcome.status == "repaired"
    on_disk = plan_path.read_text(encoding="utf-8")
    assert "currency converts" not in on_disk
    assert check_plan_sanity(on_disk).ok
    assert committed == [str(plan_path)]


def test_enforce_hard_stop_raises_and_records(tmp_path: Path) -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    plan_path = _write_plan(tmp_path, plan)

    class FakeSpec:
        scope_include = ["Unit conversion", "Currency exchange"]

    with pytest.raises(PlanSanityHardStop) as excinfo:
        enforce_plan_sanity(FakeSpec(), target_dir=tmp_path)

    assert "Currency exchange" in excinfo.value.report_text
    # The plan was not rewritten on a hard stop.
    assert plan_path.read_text(encoding="utf-8") == plan
    # A diagnostics record was written under the target's .duplo/.
    errors = tmp_path / ".duplo" / "errors.jsonl"
    assert errors.exists()
    assert "plan_sanity_gate" in errors.read_text(encoding="utf-8")


def test_enforce_missing_plan_is_clean(tmp_path: Path) -> None:
    outcome = enforce_plan_sanity(target_dir=tmp_path)
    assert outcome.status == "clean"
