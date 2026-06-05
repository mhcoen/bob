"""Tests for mcloop.waivers (append-only test-verification waivers)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mcloop.waivers import (
    REQUIRED_FIELDS,
    WAIVERS_REL,
    has_waiver,
    load_waivers,
    record_waiver,
)


def test_record_waiver_writes_all_required_fields(tmp_path: Path) -> None:
    record = record_waiver(
        tmp_path,
        task_label="T-000391",
        changed_input="pkg/widget.py",
        baseline_sha="abc123def456",
        reason="data-file change verified by manual inspection",
    )
    # The returned record carries every required field...
    for field in REQUIRED_FIELDS:
        assert field in record and record[field]
    assert record["task_label"] == "T-000391"
    assert record["changed_input"] == "pkg/widget.py"
    assert record["baseline_sha"] == "abc123def456"
    assert record["reason"] == "data-file change verified by manual inspection"

    # ...and the persisted JSONL line carries them too.
    path = tmp_path / WAIVERS_REL
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    for field in REQUIRED_FIELDS:
        assert field in stored
    # The timestamp is a UTC ISO-8601 string.
    assert stored["timestamp"].endswith("+00:00") or "T" in stored["timestamp"]


def test_record_waiver_explicit_timestamp(tmp_path: Path) -> None:
    record = record_waiver(
        tmp_path,
        task_label="T-1",
        changed_input="a.py",
        baseline_sha="sha",
        reason="r",
        timestamp="2026-06-01T00:00:00+00:00",
    )
    assert record["timestamp"] == "2026-06-01T00:00:00+00:00"


def test_record_waiver_is_append_only(tmp_path: Path) -> None:
    record_waiver(tmp_path, task_label="T", changed_input="a.py", baseline_sha="s1", reason="r1")
    record_waiver(tmp_path, task_label="T", changed_input="b.py", baseline_sha="s2", reason="r2")
    records = load_waivers(tmp_path)
    assert len(records) == 2
    assert {r["changed_input"] for r in records} == {"a.py", "b.py"}


def test_has_waiver_matches_input_and_baseline(tmp_path: Path) -> None:
    record_waiver(
        tmp_path,
        task_label="T",
        changed_input="pkg/x.py",
        baseline_sha="base-sha",
        reason="r",
    )
    assert has_waiver(tmp_path, "pkg/x.py", "base-sha") is True
    # Different baseline does not match -- a waiver for one snapshot does
    # not carry forward to a later edit.
    assert has_waiver(tmp_path, "pkg/x.py", "other-sha") is False
    # Different input does not match.
    assert has_waiver(tmp_path, "pkg/y.py", "base-sha") is False


def test_has_waiver_empty_baseline_never_matches(tmp_path: Path) -> None:
    record_waiver(tmp_path, task_label="T", changed_input="x.py", baseline_sha="", reason="r")
    # A missing baseline and a missing task identity together never match.
    assert has_waiver(tmp_path, "x.py", "") is False


def test_has_waiver_survives_baseline_change_via_task_identity(tmp_path: Path) -> None:
    # A user records a waiver for this task's work on x.py at baseline A.
    record_waiver(
        tmp_path,
        task_label="T-000003",
        changed_input="x.py",
        baseline_sha="base-A",
        reason="manual inspection",
    )
    # A commit/checkpoint lands mid-task, advancing the pre-edit baseline
    # to a different SHA. The waiver must NOT be silently nullified: it is
    # keyed on the task identity, so it still matches under the new SHA.
    assert has_waiver(tmp_path, "x.py", "base-B", task_label="T-000003") is True
    # It even matches with no baseline at all, purely on task identity.
    assert has_waiver(tmp_path, "x.py", "", task_label="T-000003") is True


def test_has_waiver_does_not_carry_across_tasks(tmp_path: Path) -> None:
    record_waiver(
        tmp_path,
        task_label="T-000003",
        changed_input="x.py",
        baseline_sha="base-A",
        reason="r",
    )
    # A different task identity at a different baseline must not inherit
    # the waiver -- task scoping is preserved.
    assert has_waiver(tmp_path, "x.py", "base-B", task_label="T-000004") is False
    # A different input under the same task does not match either.
    assert has_waiver(tmp_path, "y.py", "base-A", task_label="T-000003") is False


def test_has_waiver_empty_task_label_falls_back_to_baseline(tmp_path: Path) -> None:
    # When no task identity is available (e.g. native models leave
    # MCLOOP_TASK_LABEL unset), exact baseline matching still works.
    record_waiver(
        tmp_path,
        task_label="",
        changed_input="x.py",
        baseline_sha="base-A",
        reason="r",
    )
    assert has_waiver(tmp_path, "x.py", "base-A") is True
    assert has_waiver(tmp_path, "x.py", "base-A", task_label="") is True
    # A different baseline with no task identity still does not match.
    assert has_waiver(tmp_path, "x.py", "base-B") is False


def test_has_waiver_matches_after_intervening_commit_changes_baseline(
    tmp_path: Path,
) -> None:
    """Regression: an intervening commit advances the baseline SHA, but a
    waiver recorded under the task identity still matches.

    This exercises the real git scenario the synthetic-SHA test stands in
    for: the user records a waiver for a task's changed input at the SHA
    that was HEAD at record time, then a checkpoint/commit lands and HEAD
    moves on. ``has_waiver`` must still match on the task identity even
    though the current pre-edit baseline SHA no longer equals the recorded
    one, so the gate is not silently re-armed by an unrelated commit.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    src = tmp_path / "widget.py"
    src.write_text("a = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    baseline_at_record = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()

    # The waiver is recorded against the SHA that was HEAD when it was taken.
    record_waiver(
        tmp_path,
        task_label="T-000005",
        changed_input="widget.py",
        baseline_sha=baseline_at_record,
        reason="data-file change verified by manual inspection",
    )

    # An intervening commit lands, advancing HEAD to a new SHA.
    (tmp_path / "other.py").write_text("b = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "checkpoint"], cwd=tmp_path, check=True)
    new_baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert new_baseline != baseline_at_record

    # The recorded waiver still covers the task at the advanced baseline.
    assert has_waiver(tmp_path, "widget.py", new_baseline, task_label="T-000005") is True
    # And without the (now-stale) recorded SHA matching, it would have been
    # nullified if identity were not the durable key.
    assert has_waiver(tmp_path, "widget.py", new_baseline) is False


def test_load_waivers_missing_file(tmp_path: Path) -> None:
    assert load_waivers(tmp_path) == []


def test_load_waivers_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / WAIVERS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"changed_input": "a.py", "baseline_sha": "s"}\n'
        "this is not json\n"
        '{"changed_input": "b.py", "baseline_sha": "s"}\n'
    )
    records = load_waivers(tmp_path)
    assert {r["changed_input"] for r in records} == {"a.py", "b.py"}
