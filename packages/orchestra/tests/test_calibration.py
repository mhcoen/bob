"""Tests for orchestra.calibration: helpers, retag, and extract_labels.

These tests exercise the data-integrity guarantees promoted from the
Phase 2 sandbox (REPORT.md Addendum 6):

- ``clean_stale_versioned_artifacts``: removes per-cycle artifact
  dumps from prior runs while preserving non-versioned files.
- ``read_expected_classifier``: enforces a strict first-nonempty-line
  contract on expected.txt and rejects (rather than warns) on
  malformed input.
- ``retag_polluted_meta``: overwrites a polluted ``tags.expected_stuck``
  with the correct token while recording the prior length and the
  correction timestamp.
- ``extract_labels``: per-judge-call extraction reproduces the
  classifier outputs Phase 2 relied on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.calibration.extract_labels import (
    ScenarioSpec,
    classify,
    parse_verdict,
    scenario_rows,
)
from orchestra.calibration.helpers import (
    VALID_EXPECTED,
    ExpectedClassifierError,
    clean_stale_versioned_artifacts,
    read_expected_classifier,
)
from orchestra.calibration.lint_scenario import lint_scenario
from orchestra.calibration.retag import retag_polluted_meta

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "calibration"

# --------------------------------------------------------------------
# clean_stale_versioned_artifacts
# --------------------------------------------------------------------


def _seed_logs(logs: Path) -> tuple[set[Path], set[Path]]:
    """Seed a logs/ dir with mixed per-cycle and non-versioned files.

    Returns (per_cycle, preserved): paths that should be removed and
    preserved respectively.
    """
    logs.mkdir()
    per_cycle = {
        logs / "verdict_1.json",
        logs / "verdict_2.json",
        logs / "judge_verdict_1.json",
        logs / "judge_verdict_2.json",
        logs / "proposal_1.txt",
        logs / "proposal_2.txt",
        logs / "review_output_1.txt",
        logs / "review_output_2.txt",
        logs / "judge_decision_1.txt",
        logs / "judge_decision_2.txt",
        logs / "judge_decision_3.txt",
        logs / "judge_feedback_1.txt",
        logs / "judge_feedback_2.txt",
        logs / "judge_feedback_3.txt",
        logs / "fix_instructions_1.txt",
        logs / "fix_instructions_2.txt",
        logs / "implementer_output_1.txt",
        logs / "implementer_output_2.txt",
        logs / "framing_1.txt",
        logs / "implement_1.diff",
        logs / "implement_2.diff",
    }
    preserved = {
        logs / "log.jsonl",
        logs / "run_meta.json",
        logs / "config.json",
        logs / "task.md",
        logs / "history.md",
        logs / "summary.md",
        logs / "progress.jsonl",
        # implement_<N>_stat.txt has _stat after _<digits>, so the
        # versioned-artifact regex does NOT match. This is the
        # close-pattern edge case that makes the regex worth pinning.
        logs / "implement_1_stat.txt",
    }
    for p in per_cycle | preserved:
        p.write_text("payload")
    return per_cycle, preserved


def test_clean_removes_per_cycle_files(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    per_cycle, preserved = _seed_logs(logs)
    removed = clean_stale_versioned_artifacts(logs)
    assert removed == len(per_cycle)
    for p in per_cycle:
        assert not p.exists()
    for p in preserved:
        assert p.exists()


def test_clean_is_idempotent(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    _seed_logs(logs)
    first = clean_stale_versioned_artifacts(logs)
    second = clean_stale_versioned_artifacts(logs)
    assert first > 0
    assert second == 0


def test_clean_no_op_on_missing_dir(tmp_path: Path) -> None:
    assert clean_stale_versioned_artifacts(tmp_path / "absent") == 0


# --------------------------------------------------------------------
# read_expected_classifier
# --------------------------------------------------------------------


def test_expected_simple(tmp_path: Path) -> None:
    p = tmp_path / "expected.txt"
    p.write_text("negative\n")
    assert read_expected_classifier(p) == "negative"


def test_expected_classifier_then_prose(tmp_path: Path) -> None:
    """Iter-anchor shape: classifier on line 1, prose comments after."""
    p = tmp_path / "expected.txt"
    p.write_text(
        "positive\n\n# Expected behavior under T1 + T2:\n"
        "# Title-only task with five coordination-heavy ...\n"
    )
    assert read_expected_classifier(p) == "positive"


def test_expected_blank_prefix_and_indent(tmp_path: Path) -> None:
    p = tmp_path / "expected.txt"
    p.write_text("\n\n  ambiguous  \n# notes\n")
    assert read_expected_classifier(p) == "ambiguous"


def test_expected_missing_rejects(tmp_path: Path) -> None:
    with pytest.raises(ExpectedClassifierError, match="missing"):
        read_expected_classifier(tmp_path / "absent.txt")


def test_expected_empty_rejects(tmp_path: Path) -> None:
    p = tmp_path / "expected.txt"
    p.write_text("")
    with pytest.raises(ExpectedClassifierError, match="no nonempty line"):
        read_expected_classifier(p)


def test_expected_invalid_first_line_rejects(tmp_path: Path) -> None:
    p = tmp_path / "expected.txt"
    p.write_text("nope\n# prose\n")
    with pytest.raises(ExpectedClassifierError, match="must be exactly one of"):
        read_expected_classifier(p)


def test_expected_valid_set_is_three_tokens() -> None:
    assert set(VALID_EXPECTED) == {"positive", "negative", "ambiguous"}


# --------------------------------------------------------------------
# retag_polluted_meta
# --------------------------------------------------------------------


def test_retag_overwrites_polluted_classifier(tmp_path: Path) -> None:
    """Polluted (whole-file) classifier overwritten with the correct token."""
    polluted = "negative\n\n# 1400-char prose ...\n" * 20
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"tags": {"expected_stuck": polluted}}) + "\n")

    result = retag_polluted_meta(meta, "negative")
    assert result.changed is True
    assert result.prior_len == len(polluted)
    assert result.new_len == len("negative")

    refreshed = json.loads(meta.read_text())
    tags = refreshed["tags"]
    assert tags["expected_stuck"] == "negative"
    assert tags["expected_stuck_pollution_len"] == len(polluted)
    assert "expected_stuck_corrected_at" in tags


def test_retag_noop_when_already_correct(tmp_path: Path) -> None:
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"tags": {"expected_stuck": "positive"}}) + "\n")
    result = retag_polluted_meta(meta, "positive")
    assert result.changed is False
    refreshed = json.loads(meta.read_text())
    assert "expected_stuck_corrected_at" not in refreshed["tags"]


def test_retag_missing_meta_returns_no_change(tmp_path: Path) -> None:
    result = retag_polluted_meta(tmp_path / "absent.json", "ambiguous")
    assert result.changed is False


def test_retag_rejects_invalid_classifier(tmp_path: Path) -> None:
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"tags": {}}) + "\n")
    with pytest.raises(ValueError, match="classifier must be one of"):
        retag_polluted_meta(meta, "bogus")  # type: ignore[arg-type]


# --------------------------------------------------------------------
# extract_labels
# --------------------------------------------------------------------


def test_parse_verdict_clean_json(tmp_path: Path) -> None:
    p = tmp_path / "verdict_1.json"
    p.write_text('{"decision": "accept", "feedback": "ok"}')
    parsed = parse_verdict(p)
    assert parsed == {"decision": "accept", "feedback": "ok"}


def test_parse_verdict_wrapped_object(tmp_path: Path) -> None:
    """Tolerant fallback: wrapped JSON like F1 unwraps."""
    p = tmp_path / "verdict_2.json"
    p.write_text('leading prose\n{"decision": "iterate", "feedback": "more"}\ntrailing\n')
    parsed = parse_verdict(p)
    assert parsed == {"decision": "iterate", "feedback": "more"}


def test_parse_verdict_unparseable_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "verdict_3.json"
    p.write_text("definitely not json")
    assert parse_verdict(p) is None


def test_classify_outcomes() -> None:
    assert classify("positive", ["stuck"]) == "match"
    assert classify("positive", ["accept"]) == "under-trigger"
    assert classify("negative", ["accept"]) == "match"
    assert classify("negative", ["iterate", "stuck"]) == "over-trigger"
    assert classify("ambiguous", ["accept"]) == "ambiguous-non-stuck"
    assert classify("ambiguous", ["stuck"]) == "ambiguous-stuck"
    assert classify("unknown", ["accept"]) == "unknown"


def test_scenario_rows_two_cycle_iterate(tmp_path: Path) -> None:
    """Reproduce the iter-stuck-neg post-fix shape: iterate then stuck."""
    scenario = tmp_path / "iter-fake"
    scenario.mkdir()
    (scenario / "expected.txt").write_text("negative\n")
    logs = scenario / "logs"
    logs.mkdir()
    (logs / "verdict_1.json").write_text(json.dumps({"decision": "iterate", "feedback": "first"}))
    (logs / "verdict_2.json").write_text(
        json.dumps({"decision": "stuck", "feedback": "still first"})
    )
    (logs / "run_meta.json").write_text(json.dumps({"terminal": "stop"}))

    spec = ScenarioSpec(
        scenario_id="iter-fake",
        scenario_dir=scenario,
        workflow="iterate_until_acceptable",
    )
    rows, summary = scenario_rows(spec)
    assert summary["judge_calls"] == 2
    assert summary["decision_trajectory"] == ["iterate", "stuck"]
    assert summary["observed_stuck"] is True
    assert summary["classification"] == "over-trigger"
    assert summary["terminal"] == "stop"
    assert len(rows) == 2
    assert rows[0]["prior_decision"] == ""
    assert rows[0]["current_decision"] == "iterate"
    assert rows[0]["outcome_changed_after_next_cycle"] is True
    assert rows[1]["prior_decision"] == "iterate"
    assert rows[1]["current_decision"] == "stuck"
    assert rows[1]["outcome_changed_after_next_cycle"] is None


def test_scenario_rows_skips_stale_higher_numbered_only_when_logs_pristine(
    tmp_path: Path,
) -> None:
    """Sanity check: extract_labels reads exactly the verdict files
    present in logs/, and clean_stale_versioned_artifacts is what makes
    the directory pristine before each run. This pins the integration
    contract that motivated the cleanup helper."""
    scenario = tmp_path / "scn"
    scenario.mkdir()
    (scenario / "expected.txt").write_text("positive\n")
    logs = scenario / "logs"
    logs.mkdir()
    # Pre-populate as a 2-cycle prior-run shape.
    (logs / "verdict_1.json").write_text(json.dumps({"decision": "iterate", "feedback": "f1"}))
    (logs / "verdict_2.json").write_text(json.dumps({"decision": "stuck", "feedback": "f2"}))
    spec = ScenarioSpec(
        scenario_id="scn", scenario_dir=scenario, workflow="iterate_until_acceptable"
    )
    _, summary_before = scenario_rows(spec)
    assert summary_before["judge_calls"] == 2

    # Now simulate a re-run with fewer cycles by cleaning then writing
    # only verdict_1.json. Without the cleanup the prior verdict_2.json
    # would survive and pollute the count.
    clean_stale_versioned_artifacts(logs)
    (logs / "verdict_1.json").write_text(json.dumps({"decision": "accept", "feedback": "done"}))
    _, summary_after = scenario_rows(spec)
    assert summary_after["judge_calls"] == 1
    assert summary_after["decision_trajectory"] == ["accept"]


# --------------------------------------------------------------------
# lint_scenario: criterion id appears in task.md
# --------------------------------------------------------------------


def _seed_scenario(
    scenario_dir: Path,
    *,
    task_text: str,
    config: dict[str, object],
    expected: str = "negative",
) -> None:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "task.md").write_text(task_text)
    (scenario_dir / "expected.txt").write_text(expected + "\n")
    (scenario_dir / ".orchestra").mkdir(exist_ok=True)
    (scenario_dir / ".orchestra" / "config.json").write_text(json.dumps(config))


def test_lint_passes_when_all_ids_present(tmp_path: Path) -> None:
    _seed_scenario(
        tmp_path / "ok",
        task_text=("Task with criteria.\n  1. exact_12_words: ...\n  2. ends_question: ...\n"),
        config={
            "criteria": [
                {"id": "exact_12_words", "description": "d", "required": True},
                {"id": "ends_question", "description": "d", "required": True},
            ]
        },
    )
    result = lint_scenario(tmp_path / "ok")
    assert result.ok
    assert result.missing_in_task_md == ()
    assert set(result.configured_ids) == {"exact_12_words", "ends_question"}


def test_lint_fails_when_id_missing_from_task_md(tmp_path: Path) -> None:
    _seed_scenario(
        tmp_path / "bad",
        task_text="Task mentions exact_12_words but nothing else.\n",
        config={
            "criteria": [
                {"id": "exact_12_words", "description": "d", "required": True},
                {"id": "ends_question", "description": "d", "required": True},
            ]
        },
    )
    result = lint_scenario(tmp_path / "bad")
    assert not result.ok
    assert result.missing_in_task_md == ("ends_question",)


def test_lint_word_boundary_rejects_substring_match(tmp_path: Path) -> None:
    """\\b boundary: id 'len' must not match 'length'."""
    _seed_scenario(
        tmp_path / "boundary",
        task_text="Title length must be 12. The word len does not appear elsewhere.\n",
        config={
            "criteria": [
                {"id": "len", "description": "d", "required": True},
            ]
        },
    )
    # 'len' as a whole word IS in the prose, so this passes.
    result = lint_scenario(tmp_path / "boundary")
    assert result.ok

    _seed_scenario(
        tmp_path / "no-boundary",
        task_text="Title length must be 12. No standalone token elsewhere.\n",
        config={
            "criteria": [
                {"id": "len", "description": "d", "required": True},
            ]
        },
    )
    result2 = lint_scenario(tmp_path / "no-boundary")
    # 'len' as substring of 'length' should NOT count.
    assert not result2.ok
    assert result2.missing_in_task_md == ("len",)


def test_lint_skips_when_criteria_absent(tmp_path: Path) -> None:
    """Pre-F2.5a scenarios without criteria field pass with a warning."""
    _seed_scenario(
        tmp_path / "preF25",
        task_text="Pre-F2.5a task. No criteria configured.\n",
        config={"roles": {}, "workflows": {}},
    )
    result = lint_scenario(tmp_path / "preF25")
    assert result.ok
    assert any("no criteria configured" in w for w in result.warnings)


def test_lint_fails_when_task_md_missing(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "no-task" / ".orchestra"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text("{}")
    result = lint_scenario(tmp_path / "no-task")
    assert not result.ok
    assert any("task.md missing" in w for w in result.warnings)


# --------------------------------------------------------------------
# Parametric: every fixture in tests/fixtures/calibration/ lints clean.
# --------------------------------------------------------------------


def _all_scenario_dirs() -> list[Path]:
    if not FIXTURES_ROOT.is_dir():
        return []
    return sorted(p for p in FIXTURES_ROOT.glob("*/*") if p.is_dir())


@pytest.mark.parametrize(
    "scenario_dir",
    _all_scenario_dirs(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_fixture_scenarios_lint_clean(scenario_dir: Path) -> None:
    """Every scenario under tests/fixtures/calibration/ passes lint.

    Scenarios with no criteria field pass with a warning. Scenarios
    that DO declare criteria must have all ids present in task.md.
    """
    result = lint_scenario(scenario_dir)
    assert result.ok, f"lint failed for {scenario_dir}: missing ids {result.missing_in_task_md}"
