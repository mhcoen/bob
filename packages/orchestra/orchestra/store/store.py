"""SQLite-backed artifact store.

Implements the contract from ``design/orchestra-runner.md``:

  store.declare(name, type, qualifiers) -> ()
  store.read_latest(name)               -> StoredVersion | None
  store.read_version(name, version_id)  -> StoredVersion | None
  store.tentative_write(name, value)    -> tentative_handle
  store.commit_tentative(handles)       -> [version_id]
  store.discard_tentative(handles)      -> ()
  store.list_versions(name)             -> [VersionRecord]

There is no public unconditional ``write``: all artifact mutation goes
through ``tentative_write`` followed by ``commit_tentative``. This is
the chokepoint through which postcondition checks, parser failure
rollback, log emission, and resume reconstruction all flow.

Slice 1 stores only inline types (``text``, ``json``, ``messages``,
``prompt``, ``schema``, ``document``). File, directory, and
git-workspace storage is slice 2 and beyond.

Versioning model: each call to ``tentative_write`` produces a fresh
row with a monotonically increasing ``seq``. ``read_latest`` orders
by ``seq DESC`` so that rewriting an artifact to a previously-written
content (A -> B -> A) correctly returns the most recent commit, not
the older row with matching content hash. Version IDs remain
content-addressed (SHA-256 of the canonicalized value).

Visibility (Slice A of the real-council plan): every version row
carries a ``producer_kind`` (one of ``state_invocation``, ``external``,
``initial``, or ``legacy``) and, when ``state_invocation``, an
``invocation_id``. ``read_latest`` and the snapshot constructor
return only versions whose visibility rule passes:

- ``external``, ``initial``, and ``legacy`` rows are always visible.
- ``state_invocation`` rows are visible only when the producing
  invocation has status ``success`` in the VisibilityIndex.

The store consults the VisibilityIndex through a thread-safe
interface (``VisibilityIndexProtocol``) registered at construction
time. The store itself never parses logs.

A single store-level Python lock guards every read, write, tentative
stage, and commit. The connection is opened with
``check_same_thread=False`` so worker threads share it under the
Python lock. The lock is the inner lock in the LogWriter-then-store
ordering rule used during snapshot capture.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from orchestra.errors import StoreError

# Inline types are serialized to JSON and hashed by SHA-256 to produce
# stable, content-addressable version IDs. Other types are slice-2.
_INLINE_TYPES = {"text", "json", "messages", "prompt", "schema", "document"}


ProducerKind = Literal["state_invocation", "external", "initial", "legacy"]
VisibilityStatus = Literal["pending", "success", "error"]


class VisibilityIndexProtocol(Protocol):
    """The thread-safe surface the store consults for visibility.

    The executor and replay layer maintain the index. The store does
    not own it. This protocol keeps the store decoupled from the
    executor's internals so a test can inject a stub index.
    """

    def status(self, invocation_id: str) -> VisibilityStatus | None:
        """Return the producing invocation's status, or None if unknown.

        An ``invocation_id`` that is unknown to the index is treated
        the same as ``pending``: not yet visible.
        """
        ...


class _AlwaysVisibleIndex:
    """Default visibility index used when no real one is provided.

    Used during slice-1-style runs that have no fan-out and want
    legacy-equivalent semantics: every version row is visible. The
    runtime executor injects a real index for Slice A workflows.
    """

    def status(self, invocation_id: str) -> VisibilityStatus | None:
        return "success"


@dataclass(frozen=True)
class StoredVersion:
    """A read result: the value of an artifact at a particular version.

    ``invocation_id`` carries the producing per-state invocation key
    (``run_id::state::attempt_seq``) when the row was written by a
    state invocation; it is ``None`` for ``external``, ``initial``,
    and ``legacy`` rows. Callers that need to filter by producer
    (e.g. resume's snapshot reconstruction excluding completed
    fan-out siblings) read this field to identify the source.
    """

    name: str
    type: str
    version_id: str
    value: Any
    invocation_id: str | None = None


@dataclass(frozen=True)
class VersionRecord:
    """A history-listing entry; lighter than StoredVersion because it
    omits the value blob."""

    name: str
    version_id: str
    written_at: str
    written_by: str
    producer_kind: ProducerKind = "legacy"
    invocation_id: str | None = None


def _canonicalize(value: Any) -> bytes:
    """Produce a canonical byte representation for hashing.

    JSON with sorted keys is deterministic for any value the inline
    artifact types can hold (text is wrapped in JSON's string encoding;
    json/messages/prompt/schema/document are themselves JSON-able).
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonicalize(value)).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    qualifiers  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS versions (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact         TEXT NOT NULL REFERENCES artifacts(name),
    version_id       TEXT NOT NULL,
    value            BLOB NOT NULL,
    written_at       TEXT NOT NULL,
    written_by       TEXT NOT NULL,
    is_tentative     INTEGER NOT NULL,
    tentative_handle TEXT,
    producer_kind    TEXT NOT NULL DEFAULT 'legacy',
    invocation_id    TEXT
);

CREATE INDEX IF NOT EXISTS versions_by_artifact ON versions(artifact, seq);
CREATE INDEX IF NOT EXISTS versions_by_handle ON versions(tentative_handle);
-- versions_by_invocation is created by _migrate_schema after the
-- invocation_id column has been added to pre-existing tables.

CREATE TABLE IF NOT EXISTS tentative_handles (
    handle      TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL REFERENCES versions(seq)
);
"""


class ArtifactStore:
    """SQLite-backed artifact store.

    A store is bound to one workflow run. It owns its database file and
    closes the connection on ``close()``. Mutations execute as SQLite
    transactions so that ``commit_tentative`` is atomic.

    A single Python ``threading.Lock`` guards every operation. The
    SQLite connection is opened with ``check_same_thread=False`` so
    worker threads can share it under the Python lock; SQLite-level
    locking is irrelevant under this discipline.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        visibility_index: VisibilityIndexProtocol | None = None,
    ) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # RLock so the snapshot-capture critical section in
        # Executor._run_fan_out_group can re-enter the store via
        # read_latest while still holding the outer store lock.
        self._lock: threading.RLock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        # Disable Python's auto-wrapping of DML statements in implicit
        # transactions. Explicit BEGIN IMMEDIATE / commit() / rollback()
        # under the store-level RLock manages transaction lifecycle.
        # Without this, mixing implicit transactions with explicit
        # BEGINs across worker threads (fan-out path) produces
        # "cannot start a transaction within a transaction" errors.
        self._conn.isolation_level = None
        # Synchronous=FULL plus a single-connection model gives durability
        # at the cost of throughput. Slice 1 favors durability.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate_schema()
        self._conn.commit()
        self._visibility: VisibilityIndexProtocol = (
            visibility_index if visibility_index is not None else _AlwaysVisibleIndex()
        )

    @property
    def lock(self) -> threading.RLock:
        """Return the store-level lock for callers that need to pair
        store ops with other state under a single critical section.

        The lock is an ``RLock`` so the snapshot-capture path can
        re-enter the store via ``read_latest`` while still holding
        the outer store lock acquired by the fan-out controller.
        """
        return self._lock

    def set_visibility_index(self, index: VisibilityIndexProtocol) -> None:
        """Swap in a different visibility index after construction.

        The executor uses this to attach the run's VisibilityIndex
        once it is constructed and the store has already been built.
        """
        with self._lock:
            self._visibility = index

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- schema migration ----------------------------------------

    def _migrate_schema(self) -> None:
        """Forward-only migration to add ``producer_kind`` and
        ``invocation_id`` to existing version rows.

        Slice A change. Stores opened from runs created before Slice A
        had no ``producer_kind`` column. CREATE TABLE IF NOT EXISTS
        adds the column for newly-created tables; ALTER TABLE catches
        the pre-existing-table case. After the migration runs, every
        existing row is tagged ``legacy`` and any new row is written
        with the strict producer_kind and invocation_id keying.
        """
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(versions)").fetchall()
        }
        if "producer_kind" not in cols:
            self._conn.execute(
                "ALTER TABLE versions ADD COLUMN producer_kind TEXT "
                "NOT NULL DEFAULT 'legacy'"
            )
        if "invocation_id" not in cols:
            self._conn.execute(
                "ALTER TABLE versions ADD COLUMN invocation_id TEXT"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS versions_by_invocation "
            "ON versions(invocation_id)"
        )

    # ----- declare -------------------------------------------------

    def declare(
        self,
        name: str,
        type: str,
        qualifiers: dict[str, Any] | None = None,
    ) -> None:
        """Register an artifact with the store.

        Re-declaration of the same artifact with the same type is a
        no-op. Re-declaration with a different type is a StoreError.

        If ``qualifiers`` contains the ``initial`` key, an initial
        committed version is written with ``producer_kind='initial'``.
        The key being present is the existence test, not the value
        being non-None: ``initial: null`` produces a row whose value
        is JSON null.
        """
        qualifiers = qualifiers or {}
        if type not in _INLINE_TYPES:
            raise StoreError(
                f"slice 1 does not handle artifact type {type!r}; only inline types are supported"
            )
        with self._lock:
            existing = self._conn.execute(
                "SELECT type FROM artifacts WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                (existing_type,) = existing
                if existing_type != type:
                    raise StoreError(
                        f"artifact {name!r} already declared with type {existing_type!r}, cannot redeclare as {type!r}"
                    )
                return
            self._conn.execute(
                "INSERT INTO artifacts (name, type, qualifiers) VALUES (?, ?, ?)",
                (name, type, json.dumps(qualifiers, sort_keys=True)),
            )
            self._conn.commit()
            if "initial" in qualifiers:
                self._write_initial(name, qualifiers["initial"])

    def write_external(self, name: str, value: Any) -> str:
        """Write an external (workflow-entry) artifact version.

        External versions bypass the tentative path because they
        exist before any state has run. They are tagged
        ``producer_kind='external'`` and are always visible.
        """
        with self._lock:
            self._artifact_type_unlocked(name)
            version_id = _hash_value(value)
            self._conn.execute(
                """
                INSERT INTO versions
                    (artifact, version_id, value, written_at, written_by,
                     is_tentative, tentative_handle, producer_kind, invocation_id)
                VALUES (?, ?, ?, ?, ?, 0, NULL, 'external', NULL)
                """,
                (
                    name,
                    version_id,
                    _canonicalize(value),
                    _now_iso(),
                    "<external>",
                ),
            )
            self._conn.commit()
            return version_id

    def _write_initial(self, name: str, value: Any) -> None:
        # Initial values bypass the tentative path because they exist
        # before any state has run; they are part of declaration, not
        # mutation by an invocation.
        version_id = _hash_value(value)
        self._conn.execute(
            """
            INSERT INTO versions
                (artifact, version_id, value, written_at, written_by,
                 is_tentative, tentative_handle, producer_kind, invocation_id)
            VALUES (?, ?, ?, ?, ?, 0, NULL, 'initial', NULL)
            """,
            (
                name,
                version_id,
                _canonicalize(value),
                _now_iso(),
                "<initial>",
            ),
        )
        self._conn.commit()

    # ----- read ----------------------------------------------------

    def _artifact_type_unlocked(self, name: str) -> str:
        row = self._conn.execute(
            "SELECT type FROM artifacts WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown artifact: {name!r}")
        return str(row[0])

    def _is_visible(
        self, producer_kind: str, invocation_id: str | None
    ) -> bool:
        if producer_kind in ("external", "initial", "legacy"):
            return True
        if producer_kind == "state_invocation":
            if invocation_id is None:
                # state_invocation row missing its invocation_id is
                # malformed; hide it conservatively.
                return False
            return self._visibility.status(invocation_id) == "success"
        # Unknown producer_kind: hide.
        return False

    def read_latest(self, name: str) -> StoredVersion | None:
        with self._lock:
            type = self._artifact_type_unlocked(name)
            rows = self._conn.execute(
                """
                SELECT version_id, value, producer_kind, invocation_id
                FROM versions
                WHERE artifact = ? AND is_tentative = 0
                ORDER BY seq DESC
                """,
                (name,),
            ).fetchall()
            for row in rows:
                version_id, value_blob, producer_kind, invocation_id = row
                if not self._is_visible(producer_kind, invocation_id):
                    continue
                return StoredVersion(
                    name=name,
                    type=type,
                    version_id=str(version_id),
                    value=json.loads(value_blob),
                    invocation_id=invocation_id,
                )
            return None

    def read_version(self, name: str, version_id: str) -> StoredVersion | None:
        with self._lock:
            type = self._artifact_type_unlocked(name)
            row = self._conn.execute(
                """
                SELECT value, producer_kind, invocation_id FROM versions
                WHERE artifact = ? AND version_id = ? AND is_tentative = 0
                ORDER BY seq DESC
                LIMIT 1
                """,
                (name, version_id),
            ).fetchone()
            if row is None:
                return None
            value_blob, producer_kind, invocation_id = row
            if not self._is_visible(producer_kind, invocation_id):
                return None
            return StoredVersion(
                name=name,
                type=type,
                version_id=version_id,
                value=json.loads(value_blob),
                invocation_id=invocation_id,
            )

    def list_versions(self, name: str) -> list[VersionRecord]:
        """Return the full version history for ``name``.

        Returns every committed version regardless of visibility (the
        history listing is for diagnostic and replay purposes; callers
        that want the visible-only view use ``read_latest``).
        """
        with self._lock:
            self._artifact_type_unlocked(name)
            rows = self._conn.execute(
                """
                SELECT version_id, written_at, written_by,
                       producer_kind, invocation_id
                FROM versions
                WHERE artifact = ? AND is_tentative = 0
                ORDER BY seq ASC
                """,
                (name,),
            ).fetchall()
        return [
            VersionRecord(
                name=name,
                version_id=str(row[0]),
                written_at=str(row[1]),
                written_by=str(row[2]),
                producer_kind=row[3] or "legacy",
                invocation_id=row[4],
            )
            for row in rows
        ]

    def list_committed_by_invocation(
        self, invocation_id: str
    ) -> list[VersionRecord]:
        """Return committed (non-tentative) versions tagged with the
        given ``invocation_id``.

        Resume uses this to detect the pre-artifact_write crash window:
        ``commit_tentative`` writes the row to the store before the
        executor logs ``artifact_write``, so a crash between the two
        leaves the store with a committed version the log does not
        mention. The pass-2 refusal logic only consulted the log; this
        method gives resume a store-side authoritative answer.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT artifact, version_id, written_at, written_by,
                       producer_kind, invocation_id
                FROM versions
                WHERE invocation_id = ? AND is_tentative = 0
                ORDER BY seq ASC
                """,
                (invocation_id,),
            ).fetchall()
        return [
            VersionRecord(
                name=str(row[0]),
                version_id=str(row[1]),
                written_at=str(row[2]),
                written_by=str(row[3]),
                producer_kind=row[4] or "legacy",
                invocation_id=row[5],
            )
            for row in rows
        ]

    # ----- tentative writes ---------------------------------------

    def tentative_write(
        self,
        name: str,
        value: Any,
        *,
        written_by: str,
        invocation_id: str | None = None,
    ) -> str:
        """Stage a write. Returns a tentative handle that
        ``commit_tentative`` or ``discard_tentative`` can act on.

        ``invocation_id`` is the producing per-state invocation key.
        Slice A workflows pass the invocation_id minted at
        ``state_enter``; the row is tagged
        ``producer_kind='state_invocation'`` and the visibility index
        gates its visibility on the invocation's outcome. Callers
        that omit ``invocation_id`` (e.g. legacy slice-1 tests, mock
        adapters) get rows tagged ``producer_kind='legacy'`` that
        remain always visible.
        """
        with self._lock:
            type = self._artifact_type_unlocked(name)
            if type not in _INLINE_TYPES:
                raise StoreError(f"slice 1 does not handle artifact type {type!r}")
            handle = str(uuid.uuid4())
            version_id = _hash_value(value)
            producer_kind: ProducerKind = (
                "state_invocation" if invocation_id is not None else "legacy"
            )
            # The two inserts must commit atomically: if the
            # ``tentative_handles`` insert fails after the ``versions``
            # insert succeeds, the version row is orphaned (no handle
            # to commit or discard it). The store runs with
            # ``isolation_level=None``, so wrap in explicit
            # ``BEGIN IMMEDIATE`` / commit / rollback under the
            # store-level RLock, mirroring the discipline already
            # used by ``commit_tentative``, ``discard_tentative``,
            # and ``purge``.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.cursor()
                cur.execute(
                    """
                    INSERT INTO versions
                        (artifact, version_id, value, written_at, written_by,
                         is_tentative, tentative_handle, producer_kind, invocation_id)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        name,
                        version_id,
                        _canonicalize(value),
                        _now_iso(),
                        written_by,
                        handle,
                        producer_kind,
                        invocation_id,
                    ),
                )
                seq = cur.lastrowid
                cur.execute(
                    "INSERT INTO tentative_handles (handle, seq) VALUES (?, ?)",
                    (handle, seq),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return handle

    def commit_tentative(self, handles: list[str]) -> list[str]:
        """Promote tentative writes to committed versions, atomically.

        Either every handle becomes a committed version, or none does.
        Returns version IDs in the same order as ``handles``.

        Uses explicit ``BEGIN IMMEDIATE`` / ``commit`` / ``rollback``
        under the store RLock so concurrent workers (fan-out path)
        serialize cleanly.
        """
        if not handles:
            return []
        with self._lock:
            committed_ids: list[str] = []
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.cursor()
                for handle in handles:
                    row = cur.execute(
                        "SELECT seq FROM tentative_handles WHERE handle = ?",
                        (handle,),
                    ).fetchone()
                    if row is None:
                        raise StoreError(f"unknown tentative handle: {handle!r}")
                    (seq,) = row
                    vrow = cur.execute(
                        "SELECT version_id FROM versions "
                        "WHERE seq = ? AND is_tentative = 1",
                        (seq,),
                    ).fetchone()
                    if vrow is None:
                        raise StoreError(
                            f"tentative handle {handle!r} points at no tentative row"
                        )
                    (version_id,) = vrow
                    cur.execute(
                        "UPDATE versions SET is_tentative = 0, "
                        "tentative_handle = NULL WHERE seq = ?",
                        (seq,),
                    )
                    cur.execute(
                        "DELETE FROM tentative_handles WHERE handle = ?",
                        (handle,),
                    )
                    committed_ids.append(str(version_id))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return committed_ids

    def discard_tentative(self, handles: list[str]) -> None:
        if not handles:
            return
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.cursor()
                for handle in handles:
                    row = cur.execute(
                        "SELECT seq FROM tentative_handles WHERE handle = ?",
                        (handle,),
                    ).fetchone()
                    if row is None:
                        continue
                    (seq,) = row
                    # Delete the handle row first: tentative_handles.seq
                    # has a foreign key into versions(seq), so deleting
                    # the version row before the handle row violates the
                    # constraint.
                    cur.execute(
                        "DELETE FROM tentative_handles WHERE handle = ?",
                        (handle,),
                    )
                    cur.execute(
                        "DELETE FROM versions WHERE seq = ? AND is_tentative = 1",
                        (seq,),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ----- post-fan-out cleanup -----------------------------------

    def purge_invisible_state_invocation_versions(self) -> int:
        """Delete committed ``state_invocation`` rows whose producing
        invocation is not ``success`` per the visibility index.

        Used by the post-fan-out cleanup pass after ``fan_out_end`` is
        durable. Idempotent: re-running against an already-clean store
        deletes zero rows.

        Returns the number of rows deleted.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT seq, invocation_id FROM versions
                WHERE is_tentative = 0
                  AND producer_kind = 'state_invocation'
                """
            ).fetchall()
            to_delete: list[int] = []
            for seq, inv_id in rows:
                if inv_id is None:
                    to_delete.append(int(seq))
                    continue
                if self._visibility.status(inv_id) != "success":
                    to_delete.append(int(seq))
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.cursor()
                for seq in to_delete:
                    cur.execute("DELETE FROM versions WHERE seq = ?", (seq,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return len(to_delete)
