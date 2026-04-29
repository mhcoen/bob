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
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestra.errors import StoreError

# Inline types are serialized to JSON and hashed by SHA-256 to produce
# stable, content-addressable version IDs. Other types are slice-2.
_INLINE_TYPES = {"text", "json", "messages", "prompt", "schema", "document"}


@dataclass(frozen=True)
class StoredVersion:
    """A read result: the value of an artifact at a particular version."""

    name: str
    type: str
    version_id: str
    value: Any


@dataclass(frozen=True)
class VersionRecord:
    """A history-listing entry; lighter than StoredVersion because it
    omits the value blob."""

    name: str
    version_id: str
    written_at: str
    written_by: str


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
    artifact         TEXT NOT NULL REFERENCES artifacts(name),
    version_id       TEXT NOT NULL,
    value            BLOB NOT NULL,
    written_at       TEXT NOT NULL,
    written_by       TEXT NOT NULL,
    is_tentative     INTEGER NOT NULL,
    tentative_handle TEXT,
    PRIMARY KEY (artifact, version_id)
);

CREATE INDEX IF NOT EXISTS versions_by_artifact ON versions(artifact, written_at);
CREATE INDEX IF NOT EXISTS versions_by_handle ON versions(tentative_handle);

CREATE TABLE IF NOT EXISTS tentative_handles (
    handle      TEXT PRIMARY KEY,
    artifact    TEXT NOT NULL,
    version_id  TEXT NOT NULL
);
"""


class ArtifactStore:
    """SQLite-backed artifact store.

    A store is bound to one workflow run. It owns its database file and
    closes the connection on ``close()``. Mutations execute as SQLite
    transactions so that ``commit_tentative`` is atomic.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        # Synchronous=FULL plus a single-connection model gives durability
        # at the cost of throughput. Slice 1 favors durability.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
        """
        qualifiers = qualifiers or {}
        if type not in _INLINE_TYPES:
            raise StoreError(
                f"slice 1 does not handle artifact type {type!r}; only inline types are supported"
            )
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
        # Apply ``initial`` if present. This produces a committed version
        # with a synthetic written_by tag distinguishing it in listings.
        if "initial" in qualifiers:
            self._write_initial(name, qualifiers["initial"])

    def _write_initial(self, name: str, value: Any) -> None:
        # Initial values bypass the tentative path because they exist
        # before any state has run; they are part of declaration, not
        # mutation by an invocation.
        version_id = _hash_value(value)
        existing = self._conn.execute(
            "SELECT 1 FROM versions WHERE artifact = ? AND version_id = ?",
            (name, version_id),
        ).fetchone()
        if existing is not None:
            return
        self._conn.execute(
            """
            INSERT INTO versions
                (artifact, version_id, value, written_at, written_by, is_tentative, tentative_handle)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
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

    def _artifact_type(self, name: str) -> str:
        row = self._conn.execute(
            "SELECT type FROM artifacts WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown artifact: {name!r}")
        return str(row[0])

    def read_latest(self, name: str) -> StoredVersion | None:
        type = self._artifact_type(name)
        row = self._conn.execute(
            """
            SELECT version_id, value FROM versions
            WHERE artifact = ? AND is_tentative = 0
            ORDER BY written_at DESC, rowid DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row is None:
            return None
        version_id, value_blob = row
        return StoredVersion(
            name=name,
            type=type,
            version_id=str(version_id),
            value=json.loads(value_blob),
        )

    def read_version(self, name: str, version_id: str) -> StoredVersion | None:
        type = self._artifact_type(name)
        row = self._conn.execute(
            """
            SELECT value FROM versions
            WHERE artifact = ? AND version_id = ? AND is_tentative = 0
            """,
            (name, version_id),
        ).fetchone()
        if row is None:
            return None
        return StoredVersion(
            name=name,
            type=type,
            version_id=version_id,
            value=json.loads(row[0]),
        )

    def list_versions(self, name: str) -> list[VersionRecord]:
        self._artifact_type(name)  # validate the name exists
        rows = self._conn.execute(
            """
            SELECT version_id, written_at, written_by FROM versions
            WHERE artifact = ? AND is_tentative = 0
            ORDER BY written_at ASC, rowid ASC
            """,
            (name,),
        ).fetchall()
        return [
            VersionRecord(
                name=name,
                version_id=str(row[0]),
                written_at=str(row[1]),
                written_by=str(row[2]),
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
    ) -> str:
        """Stage a write. Returns a tentative handle that
        ``commit_tentative`` or ``discard_tentative`` can act on.

        Versions are content-addressed. Two tentative writes of the
        same value to the same artifact share a version_id but get
        distinct handles; commit_tentative on either handle promotes
        the row.
        """
        type = self._artifact_type(name)
        if type not in _INLINE_TYPES:
            raise StoreError(f"slice 1 does not handle artifact type {type!r}")
        handle = str(uuid.uuid4())
        version_id = _hash_value(value)
        # If no row exists for this artifact+version_id yet, insert a
        # tentative one. If one exists (committed or tentative), leave
        # it; the handle just records the intent to materialize this
        # content as part of an invocation's writes.
        existing = self._conn.execute(
            "SELECT is_tentative FROM versions WHERE artifact = ? AND version_id = ?",
            (name, version_id),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO versions
                    (artifact, version_id, value, written_at, written_by, is_tentative, tentative_handle)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    name,
                    version_id,
                    _canonicalize(value),
                    _now_iso(),
                    written_by,
                    handle,
                ),
            )
        self._conn.execute(
            """
            INSERT OR REPLACE INTO tentative_handles (handle, artifact, version_id)
            VALUES (?, ?, ?)
            """,
            (handle, name, version_id),
        )
        self._conn.commit()
        return handle

    def commit_tentative(self, handles: list[str]) -> list[str]:
        """Promote tentative writes to committed versions, atomically.

        Either every handle becomes a committed version, or none does.
        Returns version IDs in the same order as ``handles``.
        """
        if not handles:
            return []
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            committed_ids: list[str] = []
            for handle in handles:
                row = cur.execute(
                    "SELECT artifact, version_id FROM tentative_handles WHERE handle = ?",
                    (handle,),
                ).fetchone()
                if row is None:
                    raise StoreError(f"unknown tentative handle: {handle!r}")
                artifact, version_id = row
                cur.execute(
                    """
                    UPDATE versions SET is_tentative = 0, tentative_handle = NULL
                    WHERE artifact = ? AND version_id = ? AND is_tentative = 1
                    """,
                    (artifact, version_id),
                )
                cur.execute(
                    "DELETE FROM tentative_handles WHERE handle = ?",
                    (handle,),
                )
                committed_ids.append(str(version_id))
            cur.execute("COMMIT")
            return committed_ids
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def discard_tentative(self, handles: list[str]) -> None:
        if not handles:
            return
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            for handle in handles:
                row = cur.execute(
                    "SELECT artifact, version_id FROM tentative_handles WHERE handle = ?",
                    (handle,),
                ).fetchone()
                if row is None:
                    # Idempotent: discarding an unknown handle is fine.
                    continue
                artifact, version_id = row
                # Only delete the row if it is still tentative; another
                # handle may have already committed it.
                cur.execute(
                    """
                    DELETE FROM versions
                    WHERE artifact = ? AND version_id = ? AND is_tentative = 1
                    """,
                    (artifact, version_id),
                )
                cur.execute(
                    "DELETE FROM tentative_handles WHERE handle = ?",
                    (handle,),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
