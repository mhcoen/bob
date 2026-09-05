"""Recovery, migration, and concurrent edits through public state consumers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bob_tools.json_state import StateConflictError, StateError
from duplo import pipeline, saver
from duplo.extractor import Feature
from duplo.hasher import load_hashes, save_hashes
from duplo.questioner import BuildPreferences
from duplo.state import edit_manifest, edit_state, read_manifest, read_state
from duplo.verification_extractor import load_frame_descriptions


def _path(tmp_path, raw=b"{}"):
    path = tmp_path / saver.DUPLO_JSON
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    return path


_WRITERS = (
    lambda root: saver.save_selections(
        "url", [], BuildPreferences("", "", [], []), target_dir=root
    ),
    lambda root: saver.save_build_preferences([], "hash", target_dir=root),
    lambda root: saver.append_phase_to_history("# Phase 1", target_dir=root),
    lambda root: saver.save_feedback("feedback", target_dir=root),
    lambda root: saver.save_roadmap([], target_dir=root),
    lambda root: saver.advance_phase(target_dir=root),
    lambda root: saver.save_features([], target_dir=root),
    lambda root: saver.save_feature_status("name", "implemented", "1", target_dir=root),
    lambda root: saver.save_issue("bug", "test", "1", target_dir=root),
    lambda root: saver.resolve_issue("bug", target_dir=root),
    lambda root: saver.save_sources([], target_dir=root),
    lambda root: saver.save_design_requirements({}, target_dir=root),
    lambda root: saver.save_frame_descriptions([], target_dir=root),
)


@pytest.mark.parametrize("writer", _WRITERS)
@pytest.mark.parametrize("raw", [b"{", b'{"schema_version":2}', b'{"features":null}'])
def test_writers_refuse_invalid_existing_state(tmp_path, writer, raw):
    path = _path(tmp_path, raw)
    with pytest.raises(StateError, match="preserved"):
        writer(tmp_path)
    assert path.read_bytes() == raw


def test_legacy_migration_preserves_history_and_custom_fields(tmp_path):
    raw = b'{"features":[], "phases":[{"phase":"old"}], "custom":{"x":1}}'
    path = _path(tmp_path, raw)
    assert read_state(path)["custom"] == {"x": 1}
    assert path.read_bytes() == raw
    saver.save_feedback("new", target_dir=tmp_path)
    data = read_state(path)
    assert data["schema_version"] == 1
    assert data["phases"] == [{"phase": "old"}]
    assert data["custom"] == {"x": 1}


def test_feature_merge_rejects_intervening_update_without_locking_provider(tmp_path, monkeypatch):
    path = _path(tmp_path, b'{"features":[]}')
    calls = []

    def model(*args):
        calls.append(args)
        # A short update can finish while the model-backed merge is in flight.
        saver.save_feedback("concurrent feedback", target_dir=tmp_path)
        return {}

    monkeypatch.setattr(saver, "_deduplicate_features_llm", model)
    monkeypatch.setattr(saver, "_find_duplicate_groups", lambda names: [])
    monkeypatch.setattr(saver, "_propagate_implemented_status", lambda features: [])
    with pytest.raises(StateConflictError, match="changed"):
        saver.save_features([Feature("New", "Description", "core")], target_dir=tmp_path)
    data = read_state(path)
    assert data["features"] == []
    assert data["feedback"][0]["text"] == "concurrent feedback"
    assert len(calls) == 1


def test_preferences_reject_changes_during_model_call(tmp_path, monkeypatch):
    path = _path(tmp_path)
    monkeypatch.chdir(tmp_path)

    def model(*args, **kwargs):
        saver.save_feedback("arrived during parsing")
        return []

    monkeypatch.setattr(pipeline, "parse_build_preferences", model)
    with pytest.raises(StateConflictError):
        pipeline._load_preferences({}, SimpleNamespace(architecture="Python", platform_entries=[]))
    data = read_state(path)
    assert "preferences" not in data
    assert data["feedback"][0]["text"] == "arrived during parsing"


def test_unchanged_scrape_does_not_overwrite_feedback_received_during_fetch(tmp_path, monkeypatch):
    path = _path(
        tmp_path,
        b'{"source_url":"https://example.com", "reference_urls":[{"content_hash":"same"}]}',
    )
    monkeypatch.chdir(tmp_path)

    def fetch(url):
        saver.save_feedback("arrived during fetch")
        return "text", [], None, [SimpleNamespace(content_hash="same")], {}

    monkeypatch.setattr(pipeline, "fetch_site", fetch)
    with pytest.raises(StateConflictError):
        pipeline._rescrape_product_url()
    data = read_state(path)
    assert data["feedback"][0]["text"] == "arrived during fetch"
    assert "last_scrape_timestamp" not in data


def test_state_update_failure_and_invalid_output_preserve_old_bytes(tmp_path, monkeypatch):
    path = _path(tmp_path, b'{"feedback": [], "custom":true}')
    raw = path.read_bytes()
    with pytest.raises(RuntimeError, match="aborted"):
        with edit_state(path) as data:
            data["custom"] = False
            raise RuntimeError("aborted")
    assert path.read_bytes() == raw
    with pytest.raises(StateError):
        with edit_state(path) as data:
            data["features"] = ["not an object"]
    assert path.read_bytes() == raw

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        saver.save_feedback("new", target_dir=tmp_path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("name", ["file_hashes.json", "processed_videos.json"])
def test_manifest_migration_and_future_version_refusal(tmp_path, name):
    path = tmp_path / name
    raw = b'{"schema_version":"file-hash", "entries":"other-hash", "a":"h"}'
    path.write_bytes(raw)
    assert read_manifest(path) == json.loads(raw)
    assert path.read_bytes() == raw
    with edit_manifest(path) as entries:
        entries["b"] = "new"
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert data["entries"]["schema_version"] == "file-hash"
    data["schema_version"] = 99
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(StateError, match="unsupported"):
        with edit_manifest(path):
            pytest.fail("unsupported input reached mutation")
    assert path.read_bytes() == before


def test_hash_snapshot_cannot_replace_a_newer_checkpoint(tmp_path):
    path = save_hashes({"a": "old"}, directory=tmp_path)
    baseline = load_hashes(tmp_path)
    save_hashes({"a": "newer"}, directory=tmp_path)
    before = path.read_bytes()
    with pytest.raises(StateConflictError, match="checkpoint changed"):
        save_hashes({"b": "stale"}, expected=baseline, directory=tmp_path)
    assert path.read_bytes() == before


def test_incomplete_manifest_envelope_is_not_empty_state(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"entries": {}}')
    with pytest.raises(StateError, match="preserved"):
        read_manifest(path)


def test_parallel_feedback_and_video_merges_are_retained(tmp_path):
    script = """
import sys
from duplo.saver import save_feedback, record_processed_videos
for i in range(10):
    save_feedback(f'{sys.argv[2]}-{i}', target_dir=sys.argv[1])
    record_processed_videos({f'{sys.argv[2]}-{i}.mp4':'hash'}, target_dir=sys.argv[1])
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(tmp_path), str(i)]) for i in range(4)
    ]
    try:
        for process in processes:
            assert process.wait(timeout=30) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
    feedback = read_state(tmp_path / saver.DUPLO_JSON)["feedback"]
    assert len(feedback) == 40
    assert len({entry["text"] for entry in feedback}) == 40
    assert len(saver.load_processed_videos(target_dir=tmp_path)) == 40


def test_pipeline_refuses_corruption_before_processing(tmp_path, monkeypatch):
    path = _path(tmp_path, b'{"schema_version":123}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "read_spec", lambda: None)
    monkeypatch.setattr(pipeline, "compute_hashes", lambda *a: pytest.fail("processing started"))
    with pytest.raises(StateError, match="unsupported"):
        pipeline._subsequent_run()
    assert path.read_bytes() == b'{"schema_version":123}'
    with pytest.raises(StateError):
        load_frame_descriptions(target_dir=str(tmp_path))
