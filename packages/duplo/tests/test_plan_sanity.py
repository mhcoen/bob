"""Tests for duplo.plan_sanity.check_plan_sanity.

Covers the T-000006 whole-plan verifier: scope-include coverage,
behavior/verification task mapping, and unique/sequential phase_ids.
"""

from __future__ import annotations

from duplo.plan_sanity import (
    KIND_PHASE_IDS,
    KIND_SCOPE_UNCOVERED,
    KIND_VERIFY_WITHOUT_BUILD,
    check_plan_sanity,
)


def _plan(*phases: str) -> str:
    """Assemble a minimal PLAN.md envelope around phase blocks."""
    return "<!-- bob-plan-format: 1 -->\n\n# MyApp\n\n" + "\n".join(phases)


def _phase(n: int, title: str, body: str, *, phase_id: int | None = None) -> str:
    pid = phase_id if phase_id is not None else n
    return f"## Phase {n}: {title}\n<!-- phase_id: phase_{pid:03d} -->\n\n{body}\n"


# --- Clean plans -----------------------------------------------------------


def test_clean_plan_passes() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]\n'
            '- [ ] T-000002: Verify: convert km to mi [feat: "Unit conversion"]',
        ),
        _phase(2, "Polish", '- [ ] T-000003: Add dark mode [feat: "Dark mode"]'),
    )
    report = check_plan_sanity(plan, scope_include=["Unit conversion", "Dark mode"])
    assert report.ok
    assert report.violations == []


def test_no_scope_no_phase_ids_is_clean() -> None:
    plan = _plan(_phase(1, "Core", "- [ ] T-000001: scaffold the project"))
    report = check_plan_sanity(plan)
    assert report.ok


# --- Scope coverage --------------------------------------------------------


def test_uncovered_scope_item_flagged() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    report = check_plan_sanity(plan, scope_include=["Unit conversion", "Currency exchange"])
    assert not report.ok
    assert report.kinds() == {KIND_SCOPE_UNCOVERED}
    assert "Currency exchange" in report.violations[0].message


def test_scope_item_covered_by_task_text() -> None:
    plan = _plan(_phase(1, "Core", "- [ ] T-000001: Implement currency exchange rates"))
    report = check_plan_sanity(plan, scope_include=["currency exchange"])
    assert report.ok


def test_scope_item_covered_by_feat_substring() -> None:
    plan = _plan(
        _phase(
            1, "Core", '- [ ] T-000001: Add metric/imperial conversion [feat: "Unit conversion"]'
        )
    )
    report = check_plan_sanity(plan, scope_include=["Unit conversion (metric and imperial)"])
    assert report.ok


def test_scope_item_covered_by_paraphrased_feature_name() -> None:
    # Scope says "Offline synchronization"; the builder paraphrases it as
    # "offline sync". Token-overlap with stem-prefix matching covers it.
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add offline sync of notes [feat: "Offline sync"]')
    )
    report = check_plan_sanity(plan, scope_include=["Offline synchronization"])
    assert report.ok


def test_scope_item_covered_by_leading_label_match() -> None:
    # The scope item carries a "Label: description" form; a task that builds
    # only the label noun still covers it.
    plan = _plan(_phase(1, "Core", "- [ ] T-000001: Implement note search"))
    report = check_plan_sanity(plan, scope_include=["Search: full-text over all notes"])
    assert report.ok


def test_umbrella_scope_item_covered_when_constituents_built() -> None:
    # An umbrella scope line decomposed across several finer features is
    # covered when each listed constituent is built by some task.
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: Implement the init subcommand\n"
            "- [ ] T-000002: Implement the run subcommand\n"
            "- [ ] T-000003: Implement the next subcommand",
        )
    )
    report = check_plan_sanity(plan, scope_include=["Subcommands (init, run, next)"])
    assert report.ok


def test_umbrella_scope_item_flagged_when_a_constituent_missing() -> None:
    # Decomposition does not paper over a genuinely missing constituent.
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: Implement the export subcommand\n"
            "- [ ] T-000002: Implement the inspect subcommand",
        )
    )
    report = check_plan_sanity(plan, scope_include=["Subcommands (export, inspect, teleport)"])
    assert not report.ok
    assert report.kinds() == {KIND_SCOPE_UNCOVERED}
    assert "teleport" in report.violations[0].message


def test_unrelated_scope_item_still_flagged() -> None:
    # Robust matching must not collapse into "always covered": an item that
    # shares no key tokens with any builder is still reported.
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]')
    )
    report = check_plan_sanity(plan, scope_include=["Push notifications"])
    assert not report.ok
    assert report.kinds() == {KIND_SCOPE_UNCOVERED}


def test_scope_resolved_from_spec_object() -> None:
    class FakeSpec:
        scope_include = ["Dark mode"]

    plan = _plan(_phase(1, "Core", "- [ ] T-000001: scaffold"))
    report = check_plan_sanity(plan, spec=FakeSpec())
    assert not report.ok
    assert report.kinds() == {KIND_SCOPE_UNCOVERED}


def test_verification_task_does_not_cover_scope() -> None:
    # A scope item is only satisfied by a build task, not a verify task.
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Verify: dark mode toggles [feat: "Dark mode"]')
    )
    report = check_plan_sanity(plan, scope_include=["Dark mode"])
    assert not report.ok
    assert KIND_SCOPE_UNCOVERED in report.kinds()


# --- Regression: real writer cases (T-000010) ------------------------------


def test_scope_item_covered_by_parenthetical_qualified_feat_name() -> None:
    # Real writer case: the planner builds a feature whose [feat: ...] name
    # carries a parenthetical qualifier ("init (starter SPEC.md + ref/)"),
    # while the scope item names only the bare command. The build task's
    # own description does not mention the scope token, so coverage must
    # come from the qualified feat name (the bare item is a substring of it).
    plan = _plan(
        _phase(
            1,
            "Bootstrapping",
            "- [ ] T-000001: Build the project bootstrap command "
            '[feat: "init (starter SPEC.md + ref/)"]',
        )
    )
    report = check_plan_sanity(plan, scope_include=["init"])
    assert report.ok
    assert report.violations == []


def test_umbrella_cli_scope_item_covered_by_per_subcommand_features() -> None:
    # Real writer case: a single umbrella scope line names the CLI's
    # subcommands, but the planner decomposes it into one finer feature per
    # subcommand. The umbrella item is covered because each listed
    # constituent (init, run, fix) is built by some task.
    plan = _plan(
        _phase(
            1,
            "CLI",
            '- [ ] T-000001: Implement the init command [feat: "init command"]\n'
            '- [ ] T-000002: Implement the default run command [feat: "run command"]\n'
            '- [ ] T-000003: Implement the fix command [feat: "fix command"]',
        )
    )
    report = check_plan_sanity(plan, scope_include=["CLI subcommands (init, run, fix)"])
    assert report.ok
    assert report.violations == []


def test_genuine_scope_gap_still_flagged_amid_covered_items() -> None:
    # True negative guard: robust matching must not collapse into
    # "always covered". A scope item that no task builds under any strategy
    # (substring, feat name, paraphrase, or decomposition) is still reported,
    # even alongside an item that IS covered by a qualified feat name.
    plan = _plan(
        _phase(
            1,
            "CLI",
            '- [ ] T-000001: Implement the init command [feat: "init command"]\n'
            '- [ ] T-000002: Implement the fix command [feat: "fix command"]',
        )
    )
    report = check_plan_sanity(plan, scope_include=["init", "Continuous background watch mode"])
    assert not report.ok
    assert report.kinds() == {KIND_SCOPE_UNCOVERED}
    assert len(report.violations) == 1
    assert "Continuous background watch mode" in report.violations[0].message


# --- Verification mapping --------------------------------------------------


def test_verify_without_build_flagged() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency converts [feat: "Currency exchange"]',
        )
    )
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_VERIFY_WITHOUT_BUILD}
    assert "Currency exchange" in report.violations[0].message


def test_unannotated_verify_ok_when_plan_builds_something() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]\n'
            "- [ ] T-000002: Verify: type `5km`, expect result `3.1mi`",
        )
    )
    report = check_plan_sanity(plan)
    assert report.ok


def test_unannotated_verify_flagged_when_plan_builds_nothing() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: scaffold the project\n"
            "- [ ] T-000002: Verify: type `5km`, expect result `3.1mi`",
        )
    )
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_VERIFY_WITHOUT_BUILD}


def test_prose_verify_task_with_feat_is_build_work_not_flagged() -> None:
    # T-000003 regression: a real build task phrased "Verify ..." that
    # carries a [feat: ...] annotation is a builder, not a verification
    # task. Its feature counts as built and nothing is flagged.
    plan = _plan(
        _phase(
            1,
            "Core",
            "- [ ] T-000001: Verify the exporter handles empty input [feat: exporter]",
        )
    )
    report = check_plan_sanity(plan)
    assert report.ok
    assert report.violations == []


def test_prose_verify_feat_backs_a_strict_verification_task() -> None:
    # The prose-verify builder's feature is in built_features, so a
    # machine-rendered "Verify:" task mapping to it is not an orphan.
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Verify the exporter handles empty input [feat: "exporter"]\n'
            '- [ ] T-000002: Verify: export an empty file [feat: "exporter"]',
        )
    )
    report = check_plan_sanity(plan)
    assert report.ok


def test_test_that_phrasing_with_feat_is_build_work() -> None:
    # The exemption covers every verify-ish prose prefix, not just "Verify".
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Test that exports round-trip losslessly [feat: "Export"]',
        )
    )
    report = check_plan_sanity(plan)
    assert report.ok


def test_prose_verify_without_feat_still_flagged_when_plan_builds_nothing() -> None:
    # The exemption requires a feat annotation: an unannotated prose
    # verify task keeps its verification classification.
    plan = _plan(_phase(1, "Core", "- [ ] T-000001: Verify the exporter handles empty input"))
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_VERIFY_WITHOUT_BUILD}


def test_strict_colon_verify_with_orphan_feat_still_flagged() -> None:
    # The machine-rendered "Verify:" form is always verification, feat
    # annotation or not -- the T-000003 exemption must not silence it.
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: exporter output [feat: "exporter"]',
        )
    )
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_VERIFY_WITHOUT_BUILD}


def test_verify_mapping_ok_when_feature_built_elsewhere() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: Add unit conversion [feat: "Unit conversion"]'),
        _phase(2, "Tests", '- [ ] T-000002: Verify: conversion works [feat: "Unit conversion"]'),
    )
    report = check_plan_sanity(plan)
    assert report.ok


# --- phase_ids -------------------------------------------------------------


def test_duplicate_phase_ids_flagged() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: a [feat: "A"]', phase_id=1),
        _phase(2, "More", '- [ ] T-000002: b [feat: "B"]', phase_id=1),
    )
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_PHASE_IDS}
    assert "phase_001" in report.violations[0].message
    assert "Duplicate" in report.violations[0].message


def test_non_sequential_phase_ids_flagged() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: a [feat: "A"]', phase_id=1),
        _phase(2, "More", '- [ ] T-000002: b [feat: "B"]', phase_id=3),
    )
    report = check_plan_sanity(plan)
    assert not report.ok
    assert report.kinds() == {KIND_PHASE_IDS}
    assert "sequential" in report.violations[0].message


def test_sequential_phase_ids_pass() -> None:
    plan = _plan(
        _phase(1, "Core", '- [ ] T-000001: a [feat: "A"]'),
        _phase(2, "More", '- [ ] T-000002: b [feat: "B"]'),
        _phase(3, "Done", '- [ ] T-000003: c [feat: "C"]'),
    )
    report = check_plan_sanity(plan)
    assert report.ok


# --- Combined --------------------------------------------------------------


def test_multiple_violation_classes_reported_together() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [ ] T-000001: Add dark mode [feat: "Dark mode"]\n'
            '- [ ] T-000002: Verify: currency [feat: "Currency"]',
            phase_id=1,
        ),
        _phase(2, "More", "- [ ] T-000003: scaffold", phase_id=1),
    )
    report = check_plan_sanity(plan, scope_include=["Offline sync"])
    assert report.kinds() == {
        KIND_SCOPE_UNCOVERED,
        KIND_VERIFY_WITHOUT_BUILD,
        KIND_PHASE_IDS,
    }


def test_checked_tasks_count_as_built() -> None:
    plan = _plan(
        _phase(
            1,
            "Core",
            '- [x] T-000001: Add unit conversion [feat: "Unit conversion"]\n'
            '- [x] T-000002: Verify: conversion [feat: "Unit conversion"]',
        )
    )
    report = check_plan_sanity(plan, scope_include=["Unit conversion"])
    assert report.ok
