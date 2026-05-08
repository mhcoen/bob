"""Plan Ledger storage layer.

Append-only writer for ``PLAN.events.jsonl``. One event per line. The
storage layer is responsible for:

  - Allocating monotonic per-writer ``seq`` values, persisted to
    ``<ledger_dir>/.writers/<writer_id>.seq`` so a crashed writer does
    not reset on restart.
  - Validating each event against ``bob_tools.ledger.schema`` BEFORE
    writing, so a malformed payload raises and produces no partial
    write.
  - Performing the append as a single ``write()`` call, taking
    advantage of POSIX line-buffered append serialization between
    concurrent writers (events under ``PIPE_BUF`` are atomic without
    file locking; this is the Slice A discipline). File locking is
    deferred to Slice B if needed.

Reading is read-only and unsorted by default; consumers (the
projector) sort by ``event_id`` themselves. The storage layer does
not maintain any read index.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bob_tools.ledger.events import (
    SCHEMA_VERSION,
    Event,
    EventType,
    GitSnapshot,
)
from bob_tools.ledger.schema import validate_event

EVENTS_FILENAME = "PLAN.events.jsonl"
WRITERS_DIRNAME = ".writers"


def _now_iso() -> str:
    """Return the current UTC time as a microsecond ISO-8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def allocate_writer_id(prefix: str = "writer") -> str:
    """Default writer-id allocator.

    Combines hostname, PID, and a random UUID4 tail. Stable enough to
    survive process restarts within a session if the caller persists
    the value, distinct enough across hosts and processes that two
    writers never collide. Callers may pass any stable string instead.
    """
    host = socket.gethostname() or "unknown-host"
    pid = os.getpid()
    tail = uuid.uuid4().hex[:8]
    return f"{prefix}-{host}-{pid}-{tail}"


class Storage:
    """Append-only writer + simple reader for one ledger directory.

    Construction: ``Storage(ledger_dir, writer_id)``. Creates the
    directory tree on first append. Per-writer seq state is persisted
    so a process can stop and resume without colliding seq values.

    ``append`` validates and writes a single event. ``read_all``
    returns every event currently in ``PLAN.events.jsonl`` (no
    sorting, no deduplication). Concurrent ``Storage`` instances may
    write to the same directory; their writer_ids must be distinct.
    """

    def __init__(self, ledger_dir: str | os.PathLike[str], writer_id: str) -> None:
        if not writer_id:
            raise ValueError("writer_id must be non-empty")
        self.ledger_dir = Path(ledger_dir)
        self.writer_id = writer_id
        self._writers_dir = self.ledger_dir / WRITERS_DIRNAME
        self._seq_path = self._writers_dir / f"{writer_id}.seq"
        self._events_path = self.ledger_dir / EVENTS_FILENAME
        self._lock = threading.Lock()
        self._next_seq: int = self._read_next_seq()

    # -- seq persistence -----------------------------------------------------

    def _read_next_seq(self) -> int:
        try:
            raw = self._seq_path.read_text().strip()
        except FileNotFoundError:
            return 0
        try:
            last = int(raw)
        except ValueError:
            return 0
        return last + 1

    def _persist_seq(self, used_seq: int) -> None:
        self._writers_dir.mkdir(parents=True, exist_ok=True)
        # Atomic update via write-then-rename. Avoids a torn file if the
        # process is killed mid-write; readers either see the old value
        # or the new one.
        tmp_path = self._seq_path.with_suffix(".seq.tmp")
        tmp_path.write_text(str(used_seq))
        os.replace(tmp_path, self._seq_path)

    # -- public API ----------------------------------------------------------

    def append(
        self,
        *,
        event_type: EventType,
        payload: Mapping[str, Any],
        run_id: str,
        git: GitSnapshot | None = None,
        ts: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        """Validate and append one event. Returns the constructed
        ``Event``. Raises ``EventSchemaError`` if the resulting
        envelope+payload does not conform; the file is unchanged when
        validation fails.

        ``event_id`` defaults to a fresh UUIDv7 from
        ``bob_tools.ledger._uuid7.uuid7``. Callers may pass an explicit
        value (e.g., when constructing test fixtures); the schema
        validator still enforces UUIDv7 shape.
        """
        # Local import to avoid a hard dependency on _uuid7 at module
        # import time for callers that only read.
        from bob_tools.ledger._uuid7 import uuid7

        if not run_id:
            raise ValueError("run_id must be non-empty")

        with self._lock:
            seq = self._next_seq
            event = Event(
                event_id=event_id or uuid7(),
                seq=seq,
                ts=ts or _now_iso(),
                writer_id=self.writer_id,
                run_id=run_id,
                schema_version=SCHEMA_VERSION,
                type=event_type,
                git=git or GitSnapshot.empty(),
                payload=dict(payload),
            )
            validate_event(event.to_json())
            # Persist seq before write so a crash between persist and
            # write at worst leaves a gap (next emit increments past
            # the unused seq); never produces a duplicate seq for the
            # same writer_id.
            self._persist_seq(seq)
            self._next_seq = seq + 1

            self._events_path.parent.mkdir(parents=True, exist_ok=True)
            line = (event.to_jsonl() + "\n").encode("utf-8")
            # Single write() call; O_APPEND on POSIX serializes appends
            # whose payload is at most PIPE_BUF.
            fd = os.open(
                str(self._events_path),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o644,
            )
            try:
                os.write(fd, line)
            finally:
                os.close(fd)

        return event

    def read_all(self) -> list[Event]:
        """Read every event in ``PLAN.events.jsonl``.

        No sorting; the projector sorts by ``event_id``. Blank lines
        are tolerated (in case a writer emitted a stray newline).
        Returns an empty list if the file does not exist yet.
        """
        return list(self.iter_events())

    def iter_events(self) -> Iterator[Event]:
        """Stream events one at a time."""
        if not self._events_path.exists():
            return
        with self._events_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                yield Event.from_json(json.loads(stripped))


__all__ = [
    "EVENTS_FILENAME",
    "WRITERS_DIRNAME",
    "Storage",
    "allocate_writer_id",
]
