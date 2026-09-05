"""Failure boundaries and cooperating writers for JSON persistence."""

import errno
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from bob_tools.json_state import (
    StateError,
    atomic_write_json,
    read_json_object,
    update_json_object,
)


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"[]", b"null", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}'],
)
def test_corrupt_state_never_reaches_update(tmp_path, raw):
    path = tmp_path / "state.json"
    path.write_bytes(raw)

    def unexpected(data):
        pytest.fail("operation ran on corrupt state")

    with pytest.raises(StateError, match="preserved"):
        update_json_object(path, unexpected)
    assert path.read_bytes() == raw


def test_missing_read_is_read_only_and_updates_preserve_fields(tmp_path):
    path = tmp_path / "new" / "state.json"
    assert read_json_object(path) is None
    assert not path.parent.exists()
    update_json_object(path, lambda data: {**data, "first": 1})
    update_json_object(path, lambda data: {**data, "second": 2})
    assert read_json_object(path) == {"first": 1, "second": 2}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("boundary", ["serialize", "validate", "file_sync", "replace"])
def test_failure_before_replace_preserves_bytes_and_mode(
    tmp_path, monkeypatch, boundary
):
    path = tmp_path / "state.json"
    old = b'{"count": 1}\n'
    path.write_bytes(old)
    path.chmod(0o640)

    def fail(*args):
        raise OSError("injected failure")

    def validate(data):
        if boundary == "validate" and data["count"] == 2:
            raise ValueError("invalid update")

    if boundary == "file_sync":
        monkeypatch.setattr(os, "fsync", fail)
    elif boundary == "replace":
        monkeypatch.setattr(os, "replace", fail)
    value = float("nan") if boundary == "serialize" else 2
    with pytest.raises((ValueError, OSError)):
        update_json_object(path, lambda data: {"count": value}, validate=validate)
    assert path.read_bytes() == old
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(tmp_path.glob("*.tmp"))


def test_success_preserves_permissions(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    path.chmod(0o640)
    atomic_write_json(path, {"complete": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_failure_after_replace_leaves_complete_new_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"count": 1})
    real_fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync failed"):
        update_json_object(path, lambda data: {"count": 2})
    # An exception here does not establish that the update didn't happen.
    assert read_json_object(path) == {"count": 2}


def test_interrupt_releases_lock_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"count": 1})
    with monkeypatch.context() as patch:

        def interrupt(*args):
            raise KeyboardInterrupt

        patch.setattr(os, "replace", interrupt)
        with pytest.raises(KeyboardInterrupt):
            update_json_object(path, lambda data: {"count": 2})
    assert not list(tmp_path.glob("*.tmp"))
    update_json_object(path, lambda data: {"count": data["count"] + 1})
    assert read_json_object(path) == {"count": 2}


def test_process_writers_do_not_lose_updates(tmp_path):
    path = tmp_path / "state.json"
    script = """
import sys
from pathlib import Path
from bob_tools.json_state import update_json_object
for index in range(30):
    update_json_object(Path(sys.argv[1]), lambda data: {
        **data, 'entries': [*data.get('entries', []), [sys.argv[2], index]]
    })
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(path), str(worker)])
        for worker in range(4)
    ]
    try:
        for process in processes:
            assert process.wait(timeout=30) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
    entries = json.loads(path.read_text())["entries"]
    assert len(entries) == 120
    assert {tuple(entry) for entry in entries} == {
        (str(worker), index) for worker in range(4) for index in range(30)
    }


def test_dangling_symlink_is_not_initialized(tmp_path):
    path = tmp_path / "state.json"
    target = tmp_path / "missing.json"
    path.symlink_to(target)
    with pytest.raises(StateError, match="dangling"):
        update_json_object(path, lambda data: {"new": True})
    assert not target.exists()


def test_process_death_before_replace_preserves_state_and_releases_lock(tmp_path):
    path = tmp_path / "state.json"
    ready = tmp_path / "ready"
    atomic_write_json(path, {"count": 1})
    script = """
import os
import sys
import time
from pathlib import Path
from bob_tools.json_state import update_json_object
def stop_before_replace(src, dst):
    Path(sys.argv[2]).write_text(str(src))
    time.sleep(60)
os.replace = stop_before_replace
update_json_object(Path(sys.argv[1]), lambda data: {'count': 2})
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(path), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.01)
        assert ready.exists(), "writer never reached replacement boundary"
    finally:
        process.kill()
        process.wait(timeout=10)
    assert read_json_object(path) == {"count": 1}
    # SIGKILL cannot clean up its tempfile, but the next writer ignores it.
    assert list(tmp_path.glob("*.tmp"))
    update_json_object(path, lambda data: {"count": data["count"] + 1})
    assert read_json_object(path) == {"count": 2}
