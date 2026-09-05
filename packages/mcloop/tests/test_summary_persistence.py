"""Run evidence survives collisions and partial publication failures."""

import json
import os

import pytest

from mcloop.run_summary import RunSummary, write_run_summary


def _summary():
    return RunSummary(
        run_start="2026-09-05T12:00:00+00:00",
        run_end="2026-09-05T12:01:00+00:00",
        elapsed_seconds=60,
        mode="plan",
    )


def test_same_second_runs_have_distinct_evidence(tmp_path):
    first, second = _summary(), _summary()
    path1 = write_run_summary(tmp_path, first)
    before = path1.read_bytes()
    path2 = write_run_summary(tmp_path, second)
    assert path1 != path2
    assert path1.read_bytes() == before
    assert json.loads(path2.read_text())["run_id"] == second.run_id
    assert (path2.parent / "latest.json").read_bytes() == path2.read_bytes()
    assert write_run_summary(tmp_path, first) == path1


@pytest.mark.parametrize("fail_latest", [False, True])
def test_failed_publication_keeps_previous_latest(tmp_path, monkeypatch, fail_latest):
    old_path = write_run_summary(tmp_path, _summary())
    latest = old_path.parent / "latest.json"
    before = latest.read_bytes()
    summary = _summary()
    real_replace = os.replace

    def fail(src, dst):
        if not fail_latest or str(dst).endswith("latest.json"):
            raise OSError("publication failed")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="publication failed"):
        write_run_summary(tmp_path, summary)
    assert latest.read_bytes() == before
    assert old_path.read_bytes() == before
    new_paths = list(old_path.parent.glob(f"*_{summary.run_id}_run-summary.json"))
    assert len(new_paths) == int(fail_latest)
    if new_paths:
        assert json.loads(new_paths[0].read_text())["run_id"] == summary.run_id
    assert not list(old_path.parent.glob("*.tmp"))
