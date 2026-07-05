"""Tests for the Plan Ledger storage layer.

Coverage:

  - Storage.append validates and writes one JSONL line; bad payload
    raises and produces no partial write.
  - Per-writer seq increments monotonically and persists across
    Storage instances. Two writers' seq state is independent.
  - read_all / iter_events round-trip a multi-writer log.
  - Concurrent appends from multiple threads produce a parseable
    file with all events present (no torn lines).
  - allocate_writer_id returns a sufficiently distinct string per
    invocation.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from bob_tools.ledger import (
    AssumptionConfidence,
    CommitChangeClass,
    Event,
    EventSchemaError,
    EventType,
    Storage,
    allocate_writer_id,
)
from bob_tools.ledger.events import (
    make_assumption_declared_payload,
    make_commit_landed_payload,
    make_phase_started_payload,
)
from bob_tools.ledger.storage import EVENTS_FILENAME, WRITERS_DIRNAME


def _commit_payload(attributed_phase_id: str | None = "p1") -> dict[str, object]:
    return make_commit_landed_payload(
        commit="abc12345",
        parent_commits=[],
        branch="main",
        author="m",
        subject="s",
        attributed_phase_id=attributed_phase_id,
        files_changed=1,
        lines_added=1,
        lines_removed=0,
        change_class=CommitChangeClass.CODE,
    )


# ---------------------------------------------------------------------
# Append + read
# ---------------------------------------------------------------------


class TestAppendAndRead:
    def test_append_writes_one_jsonl_line(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        events_path = tmp_path / EVENTS_FILENAME
        text = events_path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line]
        assert len(lines) == 1
        decoded = json.loads(lines[0])
        assert decoded["type"] == "phase_started"
        assert decoded["writer_id"] == "w-1"
        assert decoded["seq"] == 0

    def test_append_returns_event(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        ev = s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        assert isinstance(ev, Event)
        assert ev.writer_id == "w-1" and ev.seq == 0

    def test_read_all_round_trip(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        e1 = s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        e2 = s.append(
            event_type=EventType.COMMIT_LANDED,
            payload=_commit_payload(attributed_phase_id="p1"),
            run_id="r-1",
        )
        events = s.read_all()
        assert events == [e1, e2]

    def test_iter_events_streams(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        for i in range(5):
            s.append(
                event_type=EventType.ASSUMPTION_DECLARED,
                payload=make_assumption_declared_payload(
                    assumption_id=f"a-{i}",
                    statement="x",
                    confidence=AssumptionConfidence.LOW,
                ),
                run_id="r-1",
            )
        seen = list(s.iter_events())
        assert len(seen) == 5

    def test_read_all_empty(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        assert s.read_all() == []

    def test_read_all_skips_blank_lines(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        # Manually insert blank lines into the file.
        path = tmp_path / EVENTS_FILENAME
        with path.open("a", encoding="utf-8") as f:
            f.write("\n\n   \n")
        s2 = Storage(tmp_path, writer_id="w-2")
        s2.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p2", title="y"),
            run_id="r-2",
        )
        evs = s2.read_all()
        assert len(evs) == 2


# ---------------------------------------------------------------------
# Validation behavior
# ---------------------------------------------------------------------


class TestValidationOnAppend:
    def test_bad_payload_raises_event_schema_error(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        with pytest.raises(EventSchemaError):
            s.append(
                event_type=EventType.PHASE_STARTED,
                payload={"phase_id": "p1"},  # missing title and other fields
                run_id="r-1",
            )

    def test_bad_payload_does_not_write_to_log(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        with pytest.raises(EventSchemaError):
            s.append(
                event_type=EventType.PHASE_STARTED,
                payload={"phase_id": "p1"},
                run_id="r-1",
            )
        events_path = tmp_path / EVENTS_FILENAME
        # File never created when no successful append happened.
        assert not events_path.exists()

    def test_bad_payload_does_not_advance_seq(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        with pytest.raises(EventSchemaError):
            s.append(
                event_type=EventType.PHASE_STARTED,
                payload={"phase_id": "p1"},
                run_id="r-1",
            )
        # The next valid append should still get seq=0.
        ev = s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        assert ev.seq == 0

    def test_empty_run_id_raises(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        with pytest.raises(ValueError, match="run_id"):
            s.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id="p1", title="x"),
                run_id="",
            )

    def test_empty_writer_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="writer_id"):
            Storage(tmp_path, writer_id="")


# ---------------------------------------------------------------------
# Per-writer seq persistence
# ---------------------------------------------------------------------


class TestSeqPersistence:
    def test_seq_increments_monotonically(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        seqs = []
        for i in range(4):
            ev = s.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id=f"p-{i}", title="x"),
                run_id="r-1",
            )
            seqs.append(ev.seq)
        assert seqs == [0, 1, 2, 3]

    def test_seq_persists_across_storage_instances(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p2", title="x"),
            run_id="r-1",
        )
        # Drop the first instance, simulate a process restart.
        s2 = Storage(tmp_path, writer_id="w-1")
        ev3 = s2.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p3", title="x"),
            run_id="r-1",
        )
        assert ev3.seq == 2

    def test_seq_independent_per_writer(self, tmp_path: Path) -> None:
        a = Storage(tmp_path, writer_id="w-A")
        b = Storage(tmp_path, writer_id="w-B")
        for _ in range(3):
            a.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id=f"a-{_}", title="x"),
                run_id="r-1",
            )
        for _ in range(2):
            b.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id=f"b-{_}", title="y"),
                run_id="r-1",
            )
        # Per-writer files exist with their last used seq.
        assert (tmp_path / WRITERS_DIRNAME / "w-A.seq").read_text() == "2"
        assert (tmp_path / WRITERS_DIRNAME / "w-B.seq").read_text() == "1"

    def test_seq_file_is_atomic_via_replace(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        seq_path = tmp_path / WRITERS_DIRNAME / "w-1.seq"
        tmp_seq_path = seq_path.with_suffix(".seq.tmp")
        # The temporary file should not linger after a successful append.
        assert seq_path.exists()
        assert not tmp_seq_path.exists()


# ---------------------------------------------------------------------
# Concurrent appends
# ---------------------------------------------------------------------


class TestConcurrentAppend:
    def test_two_writers_thread_appends(self, tmp_path: Path) -> None:
        per_writer = 25
        a = Storage(tmp_path, writer_id="w-A")
        b = Storage(tmp_path, writer_id="w-B")

        def emit(s: Storage, prefix: str) -> None:
            for i in range(per_writer):
                s.append(
                    event_type=EventType.PHASE_STARTED,
                    payload=make_phase_started_payload(
                        phase_id=f"{prefix}-{i}", title="x"
                    ),
                    run_id="r-1",
                )

        t1 = threading.Thread(target=emit, args=(a, "a"))
        t2 = threading.Thread(target=emit, args=(b, "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # All events present, file is parseable, no torn lines.
        events_path = tmp_path / EVENTS_FILENAME
        with events_path.open("r", encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line]
        assert len(lines) == per_writer * 2
        decoded = [json.loads(line) for line in lines]
        a_count = sum(1 for d in decoded if d["writer_id"] == "w-A")
        b_count = sum(1 for d in decoded if d["writer_id"] == "w-B")
        assert a_count == per_writer
        assert b_count == per_writer

        # Per-writer seq values are dense and monotonic on each side.
        a_seqs = sorted(d["seq"] for d in decoded if d["writer_id"] == "w-A")
        b_seqs = sorted(d["seq"] for d in decoded if d["writer_id"] == "w-B")
        assert a_seqs == list(range(per_writer))
        assert b_seqs == list(range(per_writer))


# ---------------------------------------------------------------------
# allocate_writer_id helper
# ---------------------------------------------------------------------


class TestAllocateWriterId:
    def test_returns_distinct_strings(self) -> None:
        seen = {allocate_writer_id() for _ in range(50)}
        assert len(seen) == 50

    def test_prefix_default(self) -> None:
        assert allocate_writer_id().startswith("writer-")

    def test_prefix_overridable(self) -> None:
        assert allocate_writer_id(prefix="mcloop").startswith("mcloop-")


# ---------------------------------------------------------------------
# Seq corruption: refuse to reset to 0
# ---------------------------------------------------------------------


class TestSeqCorruption:
    """A missing seq file is a legitimately-new writer (start at 0). An
    *existing* but corrupt file (empty, non-numeric, negative) must NOT
    silently reset to 0 -- doing so re-issues seq values already used,
    colliding the (writer_id, seq) projection tiebreaker.
    """

    def _seq_path(self, tmp_path: Path) -> Path:
        seq_dir = tmp_path / WRITERS_DIRNAME
        seq_dir.mkdir(parents=True, exist_ok=True)
        return seq_dir / "w-1.seq"

    def test_missing_seq_file_starts_at_zero(self, tmp_path: Path) -> None:
        s = Storage(tmp_path, writer_id="w-1")
        ev = s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        assert ev.seq == 0

    def test_empty_seq_file_raises(self, tmp_path: Path) -> None:
        from bob_tools.ledger.storage import SeqStateError

        self._seq_path(tmp_path).write_text("")
        with pytest.raises(SeqStateError):
            Storage(tmp_path, writer_id="w-1")

    def test_whitespace_seq_file_raises(self, tmp_path: Path) -> None:
        from bob_tools.ledger.storage import SeqStateError

        self._seq_path(tmp_path).write_text("   \n")
        with pytest.raises(SeqStateError):
            Storage(tmp_path, writer_id="w-1")

    def test_non_numeric_seq_file_raises(self, tmp_path: Path) -> None:
        from bob_tools.ledger.storage import SeqStateError

        self._seq_path(tmp_path).write_text("not-a-number")
        with pytest.raises(SeqStateError):
            Storage(tmp_path, writer_id="w-1")

    def test_negative_seq_file_raises(self, tmp_path: Path) -> None:
        from bob_tools.ledger.storage import SeqStateError

        self._seq_path(tmp_path).write_text("-3")
        with pytest.raises(SeqStateError):
            Storage(tmp_path, writer_id="w-1")

    def test_valid_seq_file_resumes(self, tmp_path: Path) -> None:
        self._seq_path(tmp_path).write_text("4")
        s = Storage(tmp_path, writer_id="w-1")
        ev = s.append(
            event_type=EventType.PHASE_STARTED,
            payload=make_phase_started_payload(phase_id="p1", title="x"),
            run_id="r-1",
        )
        assert ev.seq == 5


# ---------------------------------------------------------------------
# Cross-writer locking on append
# ---------------------------------------------------------------------


class TestAppendLocking:
    def test_large_payload_concurrent_appends_not_torn(self, tmp_path: Path) -> None:
        # A commit_landed with a large touched_paths list exceeds
        # PIPE_BUF, so O_APPEND alone cannot guarantee atomicity. The
        # exclusive lock must still serialize writers cleanly.
        big_paths = [f"src/module_{i}/file_{i}.py" for i in range(600)]
        per_writer = 15
        a = Storage(tmp_path, writer_id="w-A")
        b = Storage(tmp_path, writer_id="w-B")

        def emit(s: Storage, prefix: str) -> None:
            for _ in range(per_writer):
                payload = make_commit_landed_payload(
                    commit="abc12345",
                    parent_commits=[],
                    branch="main",
                    author="m",
                    subject="s",
                    attributed_phase_id="p1",
                    files_changed=1,
                    lines_added=1,
                    lines_removed=0,
                    change_class=CommitChangeClass.CODE,
                    touched_paths=big_paths,
                )
                s.append(
                    event_type=EventType.COMMIT_LANDED,
                    payload=payload,
                    run_id="r-1",
                )

        t1 = threading.Thread(target=emit, args=(a, "a"))
        t2 = threading.Thread(target=emit, args=(b, "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        events_path = tmp_path / EVENTS_FILENAME
        with events_path.open("r", encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line]
        assert len(lines) == per_writer * 2
        # Every line parses -- no interleaved/torn JSON.
        decoded = [json.loads(line) for line in lines]
        assert all(len(d["payload"]["touched_paths"]) == 600 for d in decoded)

    def test_exclusive_span_allows_nested_append(self, tmp_path: Path) -> None:
        # exclusive() must be reentrant: append re-enters the same lock
        # without deadlocking.
        s = Storage(tmp_path, writer_id="w-1")
        with s.exclusive():
            ev = s.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id="p1", title="x"),
                run_id="r-1",
            )
        assert ev.seq == 0
        assert len(s.read_all()) == 1

    def test_exclusive_releases_lock_on_exit(self, tmp_path: Path) -> None:
        # After the span exits, a separate Storage in this process can
        # still acquire the lock (no lingering hold).
        s = Storage(tmp_path, writer_id="w-1")
        with s.exclusive():
            pass
        other = Storage(tmp_path, writer_id="w-2")
        with other.exclusive():
            ev = other.append(
                event_type=EventType.PHASE_STARTED,
                payload=make_phase_started_payload(phase_id="p2", title="y"),
                run_id="r-1",
            )
        assert ev.seq == 0
