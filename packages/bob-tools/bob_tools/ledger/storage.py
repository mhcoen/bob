"""Plan Ledger storage layer.

Append-only writer for ``PLAN.events.jsonl``. One event per line. The
storage layer is responsible for:

  - Allocating monotonic per-writer ``seq`` values, persisted to
    ``<ledger_dir>/.writers/<writer_id>.seq`` so a crashed writer does
    not reset on restart.
  - Validating each event against ``bob_tools.ledger.schema`` BEFORE
    writing, so a malformed payload raises and produces no partial
    write.
  - Performing the append under an exclusive cross-process advisory
    lock (``flock`` on ``<ledger_dir>/.writers/.lock``) so two
    concurrent writers can never interleave a line. O_APPEND alone
    only guarantees atomicity for writes at most ``PIPE_BUF`` bytes;
    a ``commit_landed`` event with a large ``touched_paths`` list can
    exceed that, so the lock is mandatory rather than optional.

Reading is read-only and unsorted by default; consumers (the
projector) sort by ``event_id`` themselves. The storage layer does
not maintain any read index.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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
LOCK_FILENAME = ".lock"


class SeqStateError(RuntimeError):
    """Raised when a writer's persisted seq file is present but corrupt.

    A missing seq file is normal (a brand-new writer starts at 0). An
    *existing* file that is empty, non-numeric, or negative is
    corruption: silently resetting to 0 would re-issue seq values the
    writer has already used, colliding the ``(writer_id, seq)``
    projection tiebreaker. We refuse to reset and surface the problem
    instead.
    """


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
        self._lock_path = self._writers_dir / LOCK_FILENAME
        # Reentrant so ``exclusive()`` can span a read+append and the
        # nested ``append`` re-enters the same held lock without
        # deadlocking. Held for the whole critical section, so the
        # ``_flock_depth`` counter is only ever touched by one thread
        # at a time.
        self._rlock = threading.RLock()
        self._flock_fd: int | None = None
        self._flock_depth = 0
        self._next_seq: int = self._read_next_seq()

    # -- cross-process locking ----------------------------------------------

    @contextmanager
    def _interprocess_lock(self) -> Iterator[None]:
        """Hold an exclusive advisory lock over the ledger directory.

        Serializes appends across every process (and thread) writing to
        this ledger, so a line can never interleave regardless of size.
        Reentrant within a single process: nested acquisitions bump a
        depth counter and only the outermost one touches ``flock``.
        """
        self._rlock.acquire()
        try:
            if self._flock_depth == 0:
                self._writers_dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(
                    str(self._lock_path),
                    os.O_RDWR | os.O_CREAT,
                    0o644,
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                self._flock_fd = fd
            self._flock_depth += 1
            try:
                yield
            finally:
                self._flock_depth -= 1
                if self._flock_depth == 0 and self._flock_fd is not None:
                    try:
                        fcntl.flock(self._flock_fd, fcntl.LOCK_UN)
                    finally:
                        os.close(self._flock_fd)
                        self._flock_fd = None
        finally:
            self._rlock.release()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Public span lock for read-then-write sequences.

        A caller that must read the current log and append derived
        events atomically with respect to other writers (e.g.
        ``record_crossings`` checking for existing crossings before
        emitting) wraps the whole read+append span in ``with
        storage.exclusive():``. ``append`` re-enters the same lock, so
        nesting is safe.
        """
        with self._interprocess_lock():
            yield

    # -- seq persistence -----------------------------------------------------

    def _read_next_seq(self) -> int:
        try:
            raw = self._seq_path.read_text().strip()
        except FileNotFoundError:
            # No persisted state: a brand-new writer legitimately
            # starts at seq 0.
            return 0
        if not raw:
            raise SeqStateError(
                f"seq file for writer {self.writer_id!r} is empty "
                f"({self._seq_path}); refusing to reset to 0"
            )
        try:
            last = int(raw)
        except ValueError as exc:
            raise SeqStateError(
                f"seq file for writer {self.writer_id!r} is not an "
                f"integer ({self._seq_path}, contents {raw!r}); "
                f"refusing to reset to 0"
            ) from exc
        if last < 0:
            raise SeqStateError(
                f"seq file for writer {self.writer_id!r} holds a "
                f"negative seq ({last}, {self._seq_path}); refusing "
                f"to reset to 0"
            )
        return last + 1

    def _persist_seq(self, used_seq: int) -> None:
        self._writers_dir.mkdir(parents=True, exist_ok=True)
        # Atomic + durable update via write-fsync-rename-dirfsync. The
        # rename alone avoids a torn file, but without fsyncing the temp
        # file and then the directory a power loss can drop the rename
        # while an already-appended event line survives -- the restarted
        # writer then re-issues the same seq under a fresh event_id,
        # producing exactly the (writer_id, seq) duplicate class that
        # SeqStateError exists to prevent.
        tmp_path = self._seq_path.with_suffix(".seq.tmp")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, str(used_seq).encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, self._seq_path)
        dir_fd = os.open(str(self._writers_dir), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

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

        with self._interprocess_lock():
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
            created = not self._events_path.exists()
            # Single write() call; O_APPEND on POSIX serializes appends
            # whose payload is at most PIPE_BUF (larger appends are
            # serialized by the interprocess lock held here).
            fd = os.open(
                str(self._events_path),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o644,
            )
            try:
                os.write(fd, line)
                # The ledger is the durable history; an unfsync'd append
                # can vanish in a power loss AFTER the seq file recorded
                # the emit, leaving a seq gap the projector cannot
                # explain. fsync inside the lock keeps append durability
                # aligned with the seq file's.
                os.fsync(fd)
            finally:
                os.close(fd)
            if created:
                # fsync(file) makes the CONTENT durable, but the very
                # first append also creates the directory entry, and
                # that lives in the directory inode. Without a dir
                # fsync a power loss can drop the whole events file
                # while the seq file (whose writes do fsync their dir)
                # survives at seq N -- an unexplainable gap-from-zero.
                dir_fd = os.open(str(self._events_path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

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
    "LOCK_FILENAME",
    "WRITERS_DIRNAME",
    "SeqStateError",
    "Storage",
    "allocate_writer_id",
]
