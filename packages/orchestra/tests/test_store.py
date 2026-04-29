"""Unit tests for the artifact store."""

from __future__ import annotations

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


def test_discard_unknown_handle_is_idempotent(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    store.discard_tentative(["bogus"])  # no exception
    store.close()


def test_unknown_artifact_read_raises(tmp_path):
    store = ArtifactStore(tmp_path / "store.sqlite")
    with pytest.raises(StoreError):
        store.read_latest("never-declared")
    store.close()
