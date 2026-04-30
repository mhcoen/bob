"""Tests for ``orchestra.visibility`` (Slice A of the real-council plan).

Covers the in-memory ``VisibilityIndex`` plus the ``rebuild_from_records``
helper used by replay. The integration with the artifact store's
visibility rule is tested in ``tests/test_store.py``; these tests
exercise the index in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from orchestra.visibility import (
    VisibilityIndex,
    make_invocation_id,
    parse_invocation_id,
    rebuild_from_records,
)


def test_make_invocation_id_round_trip() -> None:
    inv = make_invocation_id("run-abc", "advise_a", 3)
    assert inv == "run-abc::advise_a::3"
    parts = parse_invocation_id(inv)
    assert parts.run_id == "run-abc"
    assert parts.state_name == "advise_a"
    assert parts.attempt_seq == 3


def test_make_invocation_id_rejects_separator_in_inputs() -> None:
    with pytest.raises(ValueError):
        make_invocation_id("run::1", "state", 1)
    with pytest.raises(ValueError):
        make_invocation_id("run", "state::a", 1)


def test_make_invocation_id_rejects_zero_attempt_seq() -> None:
    with pytest.raises(ValueError):
        make_invocation_id("run", "state", 0)


def test_index_status_unknown_returns_none() -> None:
    idx = VisibilityIndex()
    assert idx.status("nope") is None


def test_index_pending_then_success() -> None:
    idx = VisibilityIndex()
    inv = make_invocation_id("r", "s", 1)
    idx.insert_pending(inv)
    assert idx.status(inv) == "pending"
    idx.mark_success(inv)
    assert idx.status(inv) == "success"


def test_index_pending_then_error() -> None:
    idx = VisibilityIndex()
    inv = make_invocation_id("r", "s", 1)
    idx.insert_pending(inv)
    idx.mark_error(inv)
    assert idx.status(inv) == "error"


def test_index_replace_from() -> None:
    idx = VisibilityIndex()
    idx.insert_pending(make_invocation_id("r", "a", 1))
    idx.replace_from(
        {
            make_invocation_id("r", "a", 1): "success",
            make_invocation_id("r", "b", 1): "error",
        }
    )
    snap = idx.snapshot()
    assert snap[make_invocation_id("r", "a", 1)] == "success"
    assert snap[make_invocation_id("r", "b", 1)] == "error"


def test_index_persists_across_processes(tmp_path: Path) -> None:
    """A second process opening the index reads back the persisted
    statuses without re-running the log replay."""
    persist = tmp_path / "visibility.json"
    idx1 = VisibilityIndex(persist_path=persist)
    a = make_invocation_id("r", "a", 1)
    b = make_invocation_id("r", "b", 1)
    idx1.insert_pending(a)
    idx1.mark_success(a)
    idx1.insert_pending(b)
    idx1.mark_error(b)
    # Fresh index instance reads the file.
    idx2 = VisibilityIndex(persist_path=persist)
    assert idx2.status(a) == "success"
    assert idx2.status(b) == "error"


def test_index_persistence_handles_corrupt_file(tmp_path: Path) -> None:
    persist = tmp_path / "visibility.json"
    persist.write_text("not valid json")
    idx = VisibilityIndex(persist_path=persist)
    # Corrupt file is treated as empty; replay rebuilds from log.
    assert idx.status(make_invocation_id("r", "a", 1)) is None


# --------------------------------------------------------------------
# rebuild_from_records: replay reconstructs the index from log records
# --------------------------------------------------------------------


@dataclass
class _FakeRecord:
    event: str
    fields: dict[str, Any] = field(default_factory=dict)


def test_rebuild_inserts_pending_on_state_enter() -> None:
    inv = make_invocation_id("r", "a", 1)
    statuses = rebuild_from_records(
        [_FakeRecord(event="state_enter", fields={"invocation_id": inv})]
    )
    assert statuses == {inv: "pending"}


def test_rebuild_marks_success_on_state_exit_success() -> None:
    inv = make_invocation_id("r", "a", 1)
    statuses = rebuild_from_records(
        [
            _FakeRecord(event="state_enter", fields={"invocation_id": inv}),
            _FakeRecord(
                event="state_exit",
                fields={"invocation_id": inv, "status": "ok"},
            ),
        ]
    )
    assert statuses == {inv: "success"}


def test_rebuild_marks_error_on_state_exit_error() -> None:
    inv = make_invocation_id("r", "a", 1)
    statuses = rebuild_from_records(
        [
            _FakeRecord(event="state_enter", fields={"invocation_id": inv}),
            _FakeRecord(
                event="state_exit",
                fields={"invocation_id": inv, "status": "error"},
            ),
        ]
    )
    assert statuses == {inv: "error"}


def test_rebuild_legacy_outcome_only_maps_to_success() -> None:
    """A pre-Slice-A log may carry only ``outcome`` on state_exit
    (no ``status`` field). Back-compat: treat outcome=complete or
    outcome=success as success."""
    inv = make_invocation_id("r", "a", 1)
    statuses = rebuild_from_records(
        [
            _FakeRecord(event="state_enter", fields={"invocation_id": inv}),
            _FakeRecord(
                event="state_exit",
                fields={"invocation_id": inv, "outcome": "complete"},
            ),
        ]
    )
    assert statuses == {inv: "success"}


def test_rebuild_handles_two_invocations_of_same_state() -> None:
    """The replay path keys by invocation_id, not state name. Two
    invocations of the same state get independent statuses."""
    inv1 = make_invocation_id("r", "a", 1)
    inv2 = make_invocation_id("r", "a", 2)
    statuses = rebuild_from_records(
        [
            _FakeRecord(event="state_enter", fields={"invocation_id": inv1}),
            _FakeRecord(
                event="state_exit",
                fields={"invocation_id": inv1, "status": "ok"},
            ),
            _FakeRecord(event="state_enter", fields={"invocation_id": inv2}),
            _FakeRecord(
                event="state_exit",
                fields={"invocation_id": inv2, "status": "error"},
            ),
        ]
    )
    assert statuses[inv1] == "success"
    assert statuses[inv2] == "error"


def test_rebuild_ignores_records_without_invocation_id() -> None:
    statuses = rebuild_from_records(
        [
            _FakeRecord(event="run_start"),
            _FakeRecord(event="some_event", fields={"foo": "bar"}),
        ]
    )
    assert statuses == {}
