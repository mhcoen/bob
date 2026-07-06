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


def test_prose_verify_with_feat_hard_stops_never_deleted() -> None:
    # The two T-000003-class failure modes, both closed: the task is
    # NEVER silently deleted (the original bug), and its feat NEVER
    # counts as built (the over-corrected fix let a plan of pure
    # verification text pass the gate clean). With no real builder the
    # gate hard-stops, plan untouched, for a human decision.
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: Verify the exporter handles empty input [feat: exporter]",
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "hard_stop"
    assert "Verify the exporter handles empty input" in outcome.plan_text


def test_prose_verify_with_feat_passes_when_feature_built() -> None:
    # The legitimate T-000003 shape survives untouched when a real
    # builder delivers the feature.
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Implement the exporter module [feat: "exporter"]\n'
            "- [ ] T-000002: Verify the exporter handles empty input [feat: exporter]",
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "clean"
    assert outcome.plan_text == plan


def test_repair_drops_only_plain_orphan_not_feat_annotated() -> None:
    # A repair pass triggered by a plain machine-rendered orphan
    # ("Verify: ...") must not take a feat-annotated verify line with
    # it: feat-carrying lines are excluded from the droppable set. Here
    # the feat-annotated line's feature IS built, so after the orphan is
    # dropped the plan is clean.
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Implement the exporter module [feat: "exporter"]\n'
            "- [ ] T-000002: Verify the exporter handles empty input [feat: exporter]\n"
            '- [ ] T-000003: Verify: currency converts [feat: "Currency exchange"]',
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "repaired"
    assert check_plan_sanity(outcome.plan_text).ok
    assert "Verify the exporter handles empty input" in outcome.plan_text
    assert "currency converts" not in outcome.plan_text


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
    assert outcome.stop_report is not None
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


# --- Regression: repairs known, stops on unknown, never loops -------------
#
# T-000008. The gate's whole point is a bounded policy: known mechanical
# defects are repaired in a single pass; anything else hard-stops with a
# report and is NOT retried. These tests pin that end-to-end and, crucially,
# count how many times the plan is re-validated so a future change that
# reintroduces a retry loop cannot pass silently.


def _counting_check(monkeypatch) -> list[int]:
    """Wrap plan_gate.check_plan_sanity to count its invocations.

    Returns a single-element list holding the call count. run_plan_sanity_gate
    validates once up front and, on the repair path, exactly once more after
    the single repair pass -- so the count is the gate's loop bound made
    observable: 1 on a hard stop with no repair, 2 on a repair pass, never more.
    """
    from duplo.plan_sanity import check_plan_sanity as real_check

    calls = [0]

    def counted(*args, **kwargs):
        calls[0] += 1
        return real_check(*args, **kwargs)

    monkeypatch.setattr(plan_gate, "check_plan_sanity", counted)
    return calls


def test_regression_known_bad_plan_repaired_clean_in_one_pass(monkeypatch) -> None:
    # A single plan carrying BOTH repairable defect classes at once:
    # a verify-without-build orphan task AND duplicate phase_ids.
    calls = _counting_check(monkeypatch)
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency converts [feat: "Currency exchange"]',
            phase_id=1,
        ),
        _phase(2, "More", '- [ ] T-000003: Add export [feat: "Export"]', phase_id=1),
    )
    outcome = run_plan_sanity_gate(plan)

    # One repair pass produced a clean plan.
    assert outcome.status == "repaired"
    assert check_plan_sanity(outcome.plan_text).ok
    assert outcome.report_after is not None and outcome.report_after.ok
    # Both repairs are logged, loudly and specifically.
    assert any("verify-without-build" in c for c in outcome.changes)
    assert any(c.startswith("phase_ids:") for c in outcome.changes)
    # The orphan verify task is gone; both build tasks survive.
    assert "currency converts" not in outcome.plan_text
    assert "Add dark mode" in outcome.plan_text
    assert "Add export" in outcome.plan_text
    assert "<!-- phase_id: phase_002 -->" in outcome.plan_text
    # Never loops: validate once, repair once, re-validate once -> exactly two.
    assert calls[0] == 2


def test_regression_unrepairable_hard_stops_with_report_no_retry(monkeypatch) -> None:
    # A scope include item built by no phase is unrepairable: the gate cannot
    # synthesize the missing build, so it must stop with an actionable report
    # and must NOT run a repair or a second validation.
    calls = _counting_check(monkeypatch)
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    outcome = run_plan_sanity_gate(plan, scope_include=["Unit conversion", "Currency exchange"])

    assert outcome.status == "hard_stop"
    assert outcome.changes == []
    assert outcome.plan_text == plan
    assert outcome.report_after is None  # no repair pass was attempted
    assert outcome.stop_report is not None
    assert "Currency exchange" in outcome.stop_report
    assert "does not retry" in outcome.stop_report
    # No retry: a single validation, then a hard stop. Never a second pass.
    assert calls[0] == 1


def test_regression_unknown_violation_kind_hard_stops_without_repair(monkeypatch) -> None:
    # Guard the REPAIRABLE_KINDS dispatch itself: a violation kind the gate
    # does not recognize (e.g. one added later) must hard-stop without any
    # repair attempt -- the gate never guesses at an unknown defect class.
    from duplo.plan_sanity import PlanSanityReport, SanityViolation

    bogus = PlanSanityReport(
        violations=[SanityViolation(kind="some_future_kind", message="unknown defect")]
    )
    repair_called = [False]

    def fake_check(*args, **kwargs):
        return bogus

    def fake_repair(text, kinds):
        repair_called[0] = True
        return text, []

    monkeypatch.setattr(plan_gate, "check_plan_sanity", fake_check)
    monkeypatch.setattr(plan_gate, "_repair", fake_repair)

    outcome = run_plan_sanity_gate(_plan(_phase(1, "Core", "- [ ] T-000001: scaffold")))

    assert outcome.status == "hard_stop"
    assert outcome.changes == []
    assert outcome.report_after is None
    assert repair_called[0] is False  # never tried to repair an unknown class
    assert outcome.stop_report is not None
    assert "some_future_kind" in outcome.stop_report


# --- Regression: the observable end-to-end contract through enforce_plan_sanity
#
# The pure-core tests above assert the GateOutcome (status, changes list, call
# count). But the task's wording is about observable behavior: a known-bad plan
# "triggers a logged repair", an unrepairable one yields "a hard stop with a
# report". Those are emitted by enforce_plan_sanity (the filesystem wrapper):
# a loud change log to stdout on repair, the actionable report to stdout plus a
# raised PlanSanityHardStop on stop. Pin that wiring so a refactor that drops the
# logging -- or, worse, swallows the hard stop -- cannot pass silently.


def test_regression_enforce_logs_repair_for_known_bad_plan(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(plan_gate, "_commit_repair", lambda path: None)
    # Same known-bad plan as the pure-core regression: verify-without-build
    # orphan AND duplicate phase_ids, both repairable in one pass.
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency converts [feat: "Currency exchange"]',
            phase_id=1,
        ),
        _phase(2, "More", '- [ ] T-000003: Add export [feat: "Export"]', phase_id=1),
    )
    plan_path = _write_plan(tmp_path, plan)

    outcome = enforce_plan_sanity(target_dir=tmp_path)

    assert outcome.status == "repaired"
    on_disk = plan_path.read_text(encoding="utf-8")
    assert check_plan_sanity(on_disk).ok
    # The repair is logged loudly and specifically to stdout, naming both
    # mechanical fixes -- not just buried in the returned changes list.
    out = capsys.readouterr().out
    assert "repaired known defect(s) in PLAN.md" in out
    assert "verify-without-build" in out
    assert "phase_ids:" in out


def test_regression_enforce_hard_stops_loudly_for_unrepairable(tmp_path, capsys) -> None:
    # An unrepairable scope-uncovered defect must reach the user as a printed,
    # actionable report AND raise PlanSanityHardStop -- never a silent return,
    # never a rewrite of PLAN.md, never a retry.
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    plan_path = _write_plan(tmp_path, plan)

    class FakeSpec:
        scope_include = ["Unit conversion", "Currency exchange"]

    with pytest.raises(PlanSanityHardStop) as excinfo:
        enforce_plan_sanity(FakeSpec(), target_dir=tmp_path)

    out = capsys.readouterr().out
    assert "failed the post-assembly sanity gate" in out
    assert "Currency exchange" in out
    assert "does not retry" in out
    # The raised error carries the same report and the plan is left untouched.
    assert "Currency exchange" in excinfo.value.report_text
    assert plan_path.read_text(encoding="utf-8") == plan


def test_trailing_punctuation_does_not_defeat_feat_protection() -> None:
    # Regression: `[feat: exporter].` (trailing period) parsed feats=()
    # and the line was silently deleted by the repair pass -- the
    # original T-000003 silent-delete resurrected by one character.
    for suffix in [".", ",", " (critical)", ")"]:
        plan = _plan(
            _phase(
                1,
                "Core",
                f"- [ ] T-000001: Verify the exporter handles empty input"
                f" [feat: exporter]{suffix}",
            )
        )
        outcome = run_plan_sanity_gate(plan)
        assert outcome.status == "hard_stop", f"suffix {suffix!r}"
        assert "Verify the exporter handles empty input" in outcome.plan_text


def test_repair_that_empties_the_plan_hard_stops() -> None:
    # A plan consisting solely of orphan strict-form verification lines
    # would be "repaired" to zero tasks, validate clean, and let mcloop
    # declare the phase instantly complete. Emptying is a human call.
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: Verify: type text and press enter\n"
            "- [ ] T-000002: Verify: the counter increments",
        )
    )
    outcome = run_plan_sanity_gate(plan)
    assert outcome.status == "hard_stop"
    # The original plan is returned untouched, not the emptied body.
    assert "T-000001" in outcome.plan_text
    assert "T-000002" in outcome.plan_text
