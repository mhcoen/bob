"""Tests for duplo.hasher."""

from __future__ import annotations

from pathlib import Path

from duplo.hasher import HashDiff, compute_hashes, diff_hashes, load_hashes, save_hashes


def test_compute_hashes_finds_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    hashes = compute_hashes(tmp_path)
    assert "a.txt" in hashes
    assert "b.txt" in hashes
    assert len(hashes) == 2


def test_compute_hashes_skips_duplo_dir(tmp_path: Path) -> None:
    (tmp_path / ".duplo").mkdir()
    (tmp_path / ".duplo" / "state.json").write_text("{}")
    (tmp_path / "real.txt").write_text("data")
    hashes = compute_hashes(tmp_path)
    assert "real.txt" in hashes
    assert ".duplo/state.json" not in hashes


def test_compute_hashes_skips_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "src.py").write_text("pass")
    hashes = compute_hashes(tmp_path)
    assert "src.py" in hashes
    assert ".git/HEAD" not in hashes


def test_compute_hashes_includes_subdirs(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("nested")
    hashes = compute_hashes(tmp_path)
    assert "sub/file.txt" in hashes


def test_compute_hashes_deterministic(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("content")
    h1 = compute_hashes(tmp_path)
    h2 = compute_hashes(tmp_path)
    assert h1 == h2


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    (tmp_path / ".duplo").mkdir()
    hashes = {"a.txt": "abc123", "b.txt": "def456"}
    path = save_hashes(hashes, directory=tmp_path)
    assert path.exists()
    loaded = load_hashes(tmp_path)
    assert loaded == hashes


def test_load_hashes_empty_when_missing(tmp_path: Path) -> None:
    assert load_hashes(tmp_path) == {}


def test_diff_hashes_added() -> None:
    old: dict[str, str] = {}
    new = {"a.txt": "abc"}
    diff = diff_hashes(old, new)
    assert diff.added == ["a.txt"]
    assert diff.changed == []
    assert diff.removed == []


def test_diff_hashes_removed() -> None:
    old = {"a.txt": "abc"}
    new: dict[str, str] = {}
    diff = diff_hashes(old, new)
    assert diff.added == []
    assert diff.removed == ["a.txt"]


def test_diff_hashes_changed() -> None:
    old = {"a.txt": "abc"}
    new = {"a.txt": "def"}
    diff = diff_hashes(old, new)
    assert diff.changed == ["a.txt"]
    assert diff.added == []
    assert diff.removed == []


def test_diff_hashes_no_changes() -> None:
    hashes = {"a.txt": "abc", "b.txt": "def"}
    diff = diff_hashes(hashes, hashes)
    assert diff == HashDiff()


def test_diff_hashes_mixed() -> None:
    old = {"keep.txt": "aaa", "change.txt": "bbb", "remove.txt": "ccc"}
    new = {"keep.txt": "aaa", "change.txt": "xxx", "add.txt": "ddd"}
    diff = diff_hashes(old, new)
    assert diff.added == ["add.txt"]
    assert diff.changed == ["change.txt"]
    assert diff.removed == ["remove.txt"]


def test_compute_and_detect_change(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("original")
    h1 = compute_hashes(tmp_path)
    (tmp_path / "f.txt").write_text("modified")
    h2 = compute_hashes(tmp_path)
    diff = diff_hashes(h1, h2)
    assert diff.changed == ["f.txt"]
