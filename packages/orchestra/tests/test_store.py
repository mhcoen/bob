"""Unit tests for the artifact store."""

from __future__ import annotations

from typing import Literal

import pytest

from orchestra.errors import StoreError
from orchestra.store import ArtifactStore


def test_declare_and_read_initial(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("greeting", "text", qualifiers={"initial": "hello"})
    v = store.read_latest("greeting")
    assert v is not None
    assert v.name == "greeting"
    assert v.type == "text"
    assert v.value == "hello"
    store.close()


def test_initial_null_is_a_real_initial_version(tmp_path):
    """initial: null distinguishable from 'no initial qualifier'."""
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "json", qualifiers={"initial": None})
    v = store.read_latest("a")
    assert v is not None
    assert v.value is None
    store.close()


def test_no_initial_qualifier_means_no_versions(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    assert store.read_latest("a") is None
    store.close()


def test_redeclare_same_type_is_noop(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("g", "text")
    store.declare("g", "text")  # idempotent
    store.close()


def test_redeclare_with_different_type_fails(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("g", "text")
    with pytest.raises(StoreError):
        store.declare("g", "json")
    store.close()


def test_tentative_then_commit(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    handle = store.tentative_write("a", "v1", written_by="t")
    # Tentative is not visible to read_latest.
    assert store.read_latest("a") is None
    committed = store.commit_tentative([handle])
    assert len(committed) == 1
    v = store.read_latest("a")
    assert v is not None and v.value == "v1"
    store.close()


def test_tentative_then_discard(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    handle = store.tentative_write("a", "v1", written_by="t")
    store.discard_tentative([handle])
    assert store.read_latest("a") is None
    store.close()


def test_commit_is_atomic(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    store.declare("b", "text")
    h1 = store.tentative_write("a", "x", written_by="t")
    h2 = store.tentative_write("b", "y", written_by="t")
    store.commit_tentative([h1, h2])
    assert store.read_latest("a").value == "x"
    assert store.read_latest("b").value == "y"
    store.close()


def test_commit_unknown_handle_raises(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    with pytest.raises(StoreError):
        store.commit_tentative(["bogus"])
    store.close()


def test_versions_are_content_addressed(tmp_path):
    """Two writes of identical content share the same version_id."""
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    h1 = store.tentative_write("a", "same content", written_by="t1")
    store.commit_tentative([h1])
    v1 = store.read_latest("a")
    h2 = store.tentative_write("a", "same content", written_by="t2")
    store.commit_tentative([h2])
    v2 = store.read_latest("a")
    assert v1.version_id == v2.version_id
    store.close()


def test_a_b_a_rewrite_returns_a_as_latest(tmp_path):
    """Rewriting an artifact A -> B -> A: read_latest sees the
    most recent A commit, not the older A row with matching content."""
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    h1 = store.tentative_write("a", "A", written_by="s1")
    store.commit_tentative([h1])
    h2 = store.tentative_write("a", "B", written_by="s2")
    store.commit_tentative([h2])
    assert store.read_latest("a").value == "B"
    h3 = store.tentative_write("a", "A", written_by="s3")
    store.commit_tentative([h3])
    v = store.read_latest("a")
    assert v.value == "A"
    # Three commits in history.
    assert len(store.list_versions("a")) == 3
    # The latest A row is a fresh commit by s3, not the s1 row.
    assert store.list_versions("a")[-1].written_by == "s3"
    store.close()


def test_discard_unknown_handle_is_idempotent(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.discard_tentative(["bogus"])  # no exception
    store.close()


def test_unknown_artifact_read_raises(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    with pytest.raises(StoreError):
        store.read_latest("never-declared")
    store.close()


# --------------------------------------------------------------------
# Slice A: producer_kind, invocation_id, visibility rule, migration
# --------------------------------------------------------------------


class _StubVisibilityIndex:
    """Test double for the VisibilityIndex protocol."""

    def __init__(self) -> None:
        self._statuses: dict[str, Literal["pending", "success", "error"]] = {}

    def set(self, invocation_id, status):
        self._statuses[invocation_id] = status

    def status(self, invocation_id):
        return self._statuses.get(invocation_id)


def test_external_artifact_always_visible(tmp_path):
    """External artifact versions (write_external) are visible
    regardless of visibility-index state."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("topic", "text")
    store.write_external("topic", "hello world")
    v = store.read_latest("topic")
    assert v is not None
    assert v.value == "hello world"
    versions = store.list_versions("topic")
    assert len(versions) == 1
    assert versions[0].producer_kind == "external"
    assert versions[0].invocation_id is None
    store.close()


def test_initial_artifact_always_visible(tmp_path):
    """Initial-qualifier rows are tagged producer_kind=initial and
    visible regardless of index state."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("seed", "text", qualifiers={"initial": "default"})
    v = store.read_latest("seed")
    assert v.value == "default"
    versions = store.list_versions("seed")
    assert versions[0].producer_kind == "initial"
    store.close()


def test_state_invocation_visible_only_when_success(tmp_path):
    """A version tagged producer_kind=state_invocation is visible only
    when the producing invocation_id has status=success in the index.
    Pending and error invocations have their versions hidden."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("a", "text")
    inv = "run-1::state-a::1"
    h = store.tentative_write(
        "a", "answer", written_by="state-a", invocation_id=inv
    )
    store.commit_tentative([h])
    # Pending invocation: hidden.
    idx.set(inv, "pending")
    assert store.read_latest("a") is None
    # Errored invocation: hidden.
    idx.set(inv, "error")
    assert store.read_latest("a") is None
    # Success: visible.
    idx.set(inv, "success")
    v = store.read_latest("a")
    assert v is not None
    assert v.value == "answer"
    store.close()


def test_visibility_key_by_invocation_not_state(tmp_path):
    """Two invocations of the same state name produce independently
    keyed versions whose visibility is independent. V1 from
    invocation_1 stays visible even when V2 from invocation_2 errors.
    """
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("a", "text")
    inv1 = "run::a::1"
    inv2 = "run::a::2"
    h1 = store.tentative_write("a", "V1", written_by="a", invocation_id=inv1)
    store.commit_tentative([h1])
    idx.set(inv1, "success")
    h2 = store.tentative_write("a", "V2", written_by="a", invocation_id=inv2)
    store.commit_tentative([h2])
    # invocation_2 is pending: hidden. The store should fall back
    # to the latest visible version, which is V1.
    idx.set(inv2, "pending")
    v = store.read_latest("a")
    assert v is not None
    assert v.value == "V1"
    # invocation_2 errors. V1 remains visible.
    idx.set(inv2, "error")
    v = store.read_latest("a")
    assert v is not None
    assert v.value == "V1"
    # invocation_2 succeeds. V2 wins as the most recent visible
    # version.
    idx.set(inv2, "success")
    v = store.read_latest("a")
    assert v.value == "V2"
    store.close()


def test_legacy_rows_always_visible(tmp_path):
    """A version written without an invocation_id (the legacy slice-1
    path) is tagged producer_kind=legacy and always visible. This is
    the migration safety guarantee for runs that pre-date Slice A."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("a", "text")
    h = store.tentative_write("a", "old", written_by="legacy")
    store.commit_tentative([h])
    versions = store.list_versions("a")
    assert versions[0].producer_kind == "legacy"
    v = store.read_latest("a")
    assert v.value == "old"
    store.close()


def test_schema_migration_tags_pre_slice_a_rows_as_legacy(tmp_path):
    """Open a store created by the slice-1 schema (no producer_kind /
    invocation_id columns), reopen it under the Slice A schema, and
    confirm the migration adds the columns and existing rows behave
    as legacy (always visible)."""
    import sqlite3

    db = tmp_path / "store.sqlite"
    # Hand-build the slice-1 schema and write one row.
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE artifacts (name TEXT PRIMARY KEY, type TEXT NOT NULL, "
        "qualifiers TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE versions ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "artifact TEXT NOT NULL REFERENCES artifacts(name), "
        "version_id TEXT NOT NULL, "
        "value BLOB NOT NULL, "
        "written_at TEXT NOT NULL, "
        "written_by TEXT NOT NULL, "
        "is_tentative INTEGER NOT NULL, "
        "tentative_handle TEXT)"
    )
    conn.execute(
        "CREATE TABLE tentative_handles (handle TEXT PRIMARY KEY, "
        "seq INTEGER NOT NULL REFERENCES versions(seq))"
    )
    conn.execute(
        "INSERT INTO artifacts (name, type, qualifiers) VALUES (?, ?, ?)",
        ("a", "text", "{}"),
    )
    conn.execute(
        "INSERT INTO versions (artifact, version_id, value, written_at, "
        "written_by, is_tentative, tentative_handle) "
        "VALUES (?, ?, ?, ?, ?, 0, NULL)",
        ("a", "abc123", b'"old-row"', "2026-04-30T00:00:00.000Z", "legacy"),
    )
    conn.commit()
    conn.close()
    # Reopen under Slice A.
    idx = _StubVisibilityIndex()
    store = ArtifactStore(db, visibility_index=idx)
    versions = store.list_versions("a")
    assert len(versions) == 1
    assert versions[0].producer_kind == "legacy"
    assert versions[0].invocation_id is None
    # Legacy rows visible without index entry.
    v = store.read_latest("a")
    assert v.value == "old-row"
    # New writes use the strict invocation keying.
    inv = "run::a::1"
    h = store.tentative_write("a", "new-row", written_by="a", invocation_id=inv)
    store.commit_tentative([h])
    idx.set(inv, "success")
    v = store.read_latest("a")
    assert v.value == "new-row"
    store.close()


def test_purge_invisible_state_invocation_versions(tmp_path):
    """Cleanup pass deletes committed state_invocation rows whose
    producing invocation is not success. External, initial, and
    legacy rows are not touched. Idempotent."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("a", "text", qualifiers={"initial": "init-value"})
    store.declare("b", "text")
    # External version on b.
    store.write_external("b", "external-value")
    # state_invocation versions: one success, one error.
    h_ok = store.tentative_write("b", "good", written_by="s", invocation_id="i1")
    store.commit_tentative([h_ok])
    idx.set("i1", "success")
    h_err = store.tentative_write("b", "bad", written_by="s", invocation_id="i2")
    store.commit_tentative([h_err])
    idx.set("i2", "error")
    # Confirm pre-purge visible value is the success row.
    assert store.read_latest("b").value == "good"
    # Purge.
    deleted = store.purge_invisible_state_invocation_versions()
    assert deleted == 1
    # Initial (a) still visible. External (b external) and success
    # (b good) still in history.
    assert store.read_latest("a").value == "init-value"
    versions_b = store.list_versions("b")
    kinds = sorted(v.producer_kind for v in versions_b)
    assert kinds == ["external", "state_invocation"]
    # Idempotent: a second purge deletes zero rows.
    assert store.purge_invisible_state_invocation_versions() == 0
    store.close()


def test_cleanup_mid_purge_preserves_hidden_invariant(tmp_path):
    """If a cleanup pass is interrupted mid-purge, the visibility rule
    continues to hide the orphans; the next cleanup completes the
    purge. Modeled here by not running the purge at all (interrupted
    immediately) and asserting the rows remain hidden, then running
    the purge to completion."""
    idx = _StubVisibilityIndex()
    store = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx)
    store.declare("b", "text")
    h = store.tentative_write("b", "orphan", written_by="s", invocation_id="i1")
    store.commit_tentative([h])
    idx.set("i1", "error")
    # Crash before cleanup. Visibility rule still hides the row.
    assert store.read_latest("b") is None
    # Reopen the store (simulating a fresh process). Index is rebuilt
    # to the same status; the row remains hidden.
    store.close()
    idx2 = _StubVisibilityIndex()
    idx2.set("i1", "error")
    store2 = ArtifactStore(tmp_path / "store.sqlite", visibility_index=idx2)
    assert store2.read_latest("b") is None
    # Cleanup completes.
    assert store2.purge_invisible_state_invocation_versions() == 1
    assert store2.read_latest("b") is None
    store2.close()


def test_tentative_write_atomic_under_concurrent_pressure(tmp_path):
    """``tentative_write`` performs two inserts (one into ``versions``,
    one into ``tentative_handles``). The store runs with
    ``isolation_level=None``, so without an explicit
    ``BEGIN IMMEDIATE`` / commit / rollback wrap, a failure between
    the two inserts would leak a half-written row: a ``versions`` row
    with ``is_tentative=1`` and no matching handle, with no way to
    commit or discard it.

    Tests Follow-up 2.
    """
    import threading
    from typing import Any

    # Sanity: under normal conditions, two threads each calling
    # ``tentative_write`` on different artifacts both commit; the
    # store-level RLock plus BEGIN IMMEDIATE serialise them cleanly.
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    store.declare("b", "text")

    barrier = threading.Barrier(2)
    handles: list[str] = []
    handles_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(name: str, value: str) -> None:
        try:
            barrier.wait()
            h = store.tentative_write(name, value, written_by=f"t-{name}")
            with handles_lock:
                handles.append(h)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("a", "v1"))
    t2 = threading.Thread(target=worker, args=("b", "v2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert errors == []
    assert len(handles) == 2

    cur = store._conn.cursor()
    versions = cur.execute(
        "SELECT seq FROM versions WHERE is_tentative = 1"
    ).fetchall()
    assert len(versions) == 2
    handle_rows = cur.execute("SELECT handle FROM tentative_handles").fetchall()
    assert len(handle_rows) == 2
    store.close()

    # Now corrupt the second insert. A separate store instance gets
    # a wrapped ``cursor()`` factory whose returned cursor raises on
    # the second ``execute`` call (the INSERT INTO tentative_handles
    # statement). Without the BEGIN IMMEDIATE wrap, the first insert
    # would already be committed; with it, rollback undoes the
    # versions insert too.
    fresh = ArtifactStore(tmp_path / "store2.sqlite")
    fresh.declare("c", "text")

    class _FailingCursor:
        def __init__(self, real: Any) -> None:
            self._real = real
            self._n = 0

        def execute(self, *a: Any, **k: Any) -> Any:
            self._n += 1
            if self._n == 2:
                raise RuntimeError("synthetic failure on second insert")
            return self._real.execute(*a, **k)

        def fetchone(self) -> Any:
            return self._real.fetchone()

        def fetchall(self) -> Any:
            return self._real.fetchall()

        @property
        def lastrowid(self) -> int:
            return int(self._real.lastrowid)

    real_conn = fresh._conn

    class _WrappedConn:
        """Delegates everything except cursor() to the real
        connection. ``cursor()`` returns a failing cursor."""

        def __getattr__(self, name: str) -> Any:
            return getattr(real_conn, name)

        def cursor(self) -> Any:
            return _FailingCursor(real_conn.cursor())

    fresh._conn = _WrappedConn()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="synthetic failure"):
        fresh.tentative_write("c", "x", written_by="t")

    # Restore the real connection for queries.
    fresh._conn = real_conn

    # The rollback undid the versions insert: no half-written row.
    cur = fresh._conn.cursor()
    versions = cur.execute(
        "SELECT seq FROM versions WHERE is_tentative = 1"
    ).fetchall()
    assert versions == []
    handle_rows = cur.execute("SELECT handle FROM tentative_handles").fetchall()
    assert handle_rows == []
    fresh.close()


def test_list_committed_by_invocation_returns_only_matching_versions(
    tmp_path,
):
    """The pass-3 stranded-commit refusal queries the store keyed by
    invocation_id. The method must return committed (non-tentative)
    versions tagged with the given invocation_id and nothing else."""
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    store.declare("b", "text")

    h1 = store.tentative_write(
        "a", "v-from-i1", written_by="edit#1",
        invocation_id="run::edit::1",
    )
    h2 = store.tentative_write(
        "b", "v-from-i1", written_by="edit#1",
        invocation_id="run::edit::1",
    )
    h3 = store.tentative_write(
        "a", "v-from-i2", written_by="other#1",
        invocation_id="run::other::1",
    )
    store.commit_tentative([h1, h2, h3])

    inv1 = store.list_committed_by_invocation("run::edit::1")
    assert sorted(v.name for v in inv1) == ["a", "b"]
    assert all(v.invocation_id == "run::edit::1" for v in inv1)

    inv2 = store.list_committed_by_invocation("run::other::1")
    assert [v.name for v in inv2] == ["a"]

    none = store.list_committed_by_invocation("run::missing::1")
    assert none == []

    store.close()


def test_list_committed_by_invocation_excludes_tentative_versions(tmp_path):
    """Only committed rows count for the refusal logic. A tentative
    write that was never committed is rolled back on resume; it
    must not trip the stranded-commit refusal."""
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.declare("a", "text")
    handle = store.tentative_write(
        "a", "tentative-only", written_by="edit#1",
        invocation_id="run::edit::1",
    )
    # Do NOT commit. Tentative row exists; should not show up.
    assert store.list_committed_by_invocation("run::edit::1") == []
    store.discard_tentative([handle])
    store.close()
