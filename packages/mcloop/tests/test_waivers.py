"""Tests for mcloop.waivers (append-only test-verification waivers)."""

from __future__ import annotations

import json
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
    # A missing baseline must never be treated as a waivable state.
    assert has_waiver(tmp_path, "x.py", "") is False


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
