"""Failure boundaries preserve reference originals and example generations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bob_tools.json_state import StateError
from duplo.doc_examples import CodeExample
from duplo.file_ops import pending, project_owner, recover, require_reconciled
from duplo.saver import load_examples, move_references, save_examples, store_accepted_frames


def example(text):
    return CodeExample(input=text, expected_output="result", source_url="", language="python")


@pytest.mark.parametrize("boundary", ["before_backup", "after_backup", "after_publish", "finish"])
def test_example_replace_interruption_keeps_old_generation(tmp_path, boundary):
    root = save_examples([example("original")], target_dir=tmp_path)
    (root / "notes.txt").write_text("operator notes")
    original = (root / "000_original.json").read_bytes()
    real_rename = os.rename

    def rename(src, dest):
        if Path(src) == root and boundary == "before_backup":
            raise KeyboardInterrupt()
        result = real_rename(src, dest)
        if Path(src) == root and boundary == "after_backup":
            raise KeyboardInterrupt()
        if Path(dest) == root and boundary == "after_publish":
            raise KeyboardInterrupt()
        return result

    with patch("duplo.file_ops.os.rename", side_effect=rename):
        if boundary == "finish":
            with patch("duplo.file_ops._finish", side_effect=OSError("finish failed")):
                with pytest.raises(OSError):
                    save_examples([example("new")], target_dir=tmp_path)
        else:
            with pytest.raises(KeyboardInterrupt):
                save_examples([example("new")], target_dir=tmp_path)
    path, receipt = pending(tmp_path)[0]
    backup = Path(receipt["entries"][0]["backup"])
    before_dir = backup if backup.exists() else root
    assert (before_dir / "000_original.json").read_bytes() == original
    assert (before_dir / "notes.txt").read_text() == "operator notes"
    old_receipt = path.read_bytes()
    with pytest.raises(StateError, match="duplo recover"):
        load_examples(target_dir=tmp_path)
    with pytest.raises(StateError):
        save_examples([example("retry")], target_dir=tmp_path)
    assert path.read_bytes() == old_receipt


def test_staging_failure_never_removes_current_examples(tmp_path, monkeypatch):
    root = save_examples([example("original")], target_dir=tmp_path)
    before = (root / "000_original.json").read_bytes()

    def fail(*args):
        raise OSError("no space")

    monkeypatch.setattr("duplo.file_ops.atomic_write_json", fail)
    with pytest.raises(OSError):
        save_examples([example("replacement")], target_dir=tmp_path)
    assert (root / "000_original.json").read_bytes() == before
    assert pending(tmp_path) == []


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("boundary", ["before_publish", "after_publish", "finish"])
def test_reference_operation_preserves_both_originals(tmp_path, move, boundary):
    source = tmp_path / "image.png"
    source.write_bytes(b"new reference")
    target = tmp_path / ".duplo" / "references"
    target.mkdir(parents=True)
    (target / source.name).write_bytes(b"old reference")
    state = tmp_path / ".duplo" / "duplo.json"
    state.write_text('{"features": []}')
    real_replace = os.replace

    def replace(src, dst):
        if Path(dst) == target / source.name:
            if boundary == "before_publish":
                raise KeyboardInterrupt()
            result = real_replace(src, dst)
            if boundary == "after_publish":
                raise KeyboardInterrupt()
            return result
        return real_replace(src, dst)

    def invoke():
        if move:
            return move_references([source], target_dir=tmp_path)
        return store_accepted_frames(
            [{"path": source, "filename": source.name, "state": "UI", "detail": "description"}],
            target_dir=tmp_path,
        )

    with patch("duplo.file_ops.os.replace", side_effect=replace):
        if boundary == "finish":
            with patch("duplo.file_ops._finish", side_effect=OSError("finish failed")):
                with pytest.raises(OSError):
                    invoke()
        else:
            with pytest.raises(KeyboardInterrupt):
                invoke()
    path, receipt = pending(tmp_path)[0]
    entry = receipt["entries"][0]
    assert Path(entry["source_backup"]).read_bytes() == b"new reference"
    assert Path(entry["backup"]).read_bytes() == b"old reference"
    if not move:
        assert source.read_bytes() == b"new reference"
        archive = path.parent / path.stem
        assert (archive / "duplo.before.json").read_text() == '{"features": []}'
    with pytest.raises(StateError):
        invoke()


def test_move_preserves_newer_source_when_external_editor_changes_it(tmp_path):
    source = tmp_path / "ref.txt"
    source.write_text("first")
    destination = tmp_path / ".duplo" / "references" / source.name
    real_replace = os.replace

    def replace(src, dst):
        result = real_replace(src, dst)
        if Path(dst) == destination:
            source.write_text("newer")
        return result

    with patch("duplo.file_ops.os.replace", side_effect=replace), pytest.raises(StateError):
        move_references([source], target_dir=tmp_path)
    assert source.read_text() == "newer"
    assert destination.read_text() == "first"
    assert pending(tmp_path)


def test_reconciliation_keeps_receipt_and_backups_without_replay(tmp_path, capsys):
    save_examples([example("original")], target_dir=tmp_path)
    with patch("duplo.file_ops._finish", side_effect=OSError()), pytest.raises(OSError):
        save_examples([example("new")], target_dir=tmp_path)
    path, data = pending(tmp_path)[0]
    backup = Path(data["entries"][0]["backup"])
    recover(tmp_path, None, None)
    assert json.loads(capsys.readouterr().out)["pending"][0]["receipt"]["id"] == data["id"]
    with project_owner(tmp_path, recovery=True):
        # An independent process must not acknowledge while a pipeline owns it.
        script = "\n".join(
            [
                "from duplo.file_ops import recover",
                "import sys",
                'recover(sys.argv[1],sys.argv[2],"reviewed")',
            ]
        )
        proc = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), data["id"]],
            capture_output=True,
            timeout=15,
        )
        assert proc.returncode != 0
    with pytest.raises(StateError):
        recover(tmp_path, data["id"], "")
    recover(tmp_path, data["id"], "Reviewed published new examples and retained old backup.")
    require_reconciled(tmp_path)
    assert backup.is_dir()
    assert path.exists()
    assert load_examples(target_dir=tmp_path)[0].input == "new"


@pytest.mark.parametrize("content", ["{", '{"schema_version": 2}', "[]"])
def test_bad_operation_record_never_gets_overwritten(tmp_path, content):
    root = tmp_path / ".duplo" / "operations"
    root.mkdir(parents=True)
    record = root / "bad.json"
    record.write_text(content)
    with pytest.raises(StateError):
        move_references([], target_dir=tmp_path)
    assert record.read_text() == content


def test_unclean_exit_leaves_recoverable_example_backup(tmp_path):
    save_examples([example("original")], target_dir=tmp_path)
    script = """
import os, sys
from pathlib import Path
from duplo.saver import save_examples
from duplo.doc_examples import CodeExample
import duplo.file_ops as ops
root=Path(sys.argv[1])
rename=ops.os.rename
def die(src, dst):
    rename(src,dst)
    if Path(src)==root/'.duplo'/'examples': os._exit(74)
ops.os.rename=die
new=CodeExample(input='new',expected_output='',source_url='',language='')
save_examples([new],target_dir=root)
"""
    proc = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=15)
    assert proc.returncode == 74
    with pytest.raises(StateError):
        load_examples(target_dir=tmp_path)
    _, data = pending(tmp_path)[0]
    assert (Path(data["entries"][0]["backup"]) / "000_original.json").is_file()


def test_pending_operation_blocks_pipeline_before_model_work(tmp_path, monkeypatch):
    from duplo.pipeline import _subsequent_run

    save_examples([example("old")], target_dir=tmp_path)
    with patch("duplo.file_ops._finish", side_effect=OSError()), pytest.raises(OSError):
        save_examples([example("new")], target_dir=tmp_path)
    monkeypatch.chdir(tmp_path)
    with patch("duplo.pipeline.read_state") as read_state, pytest.raises(StateError):
        _subsequent_run()
    read_state.assert_not_called()


def test_recover_cli_is_read_only(tmp_path, monkeypatch, capsys):
    from duplo.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["duplo", "recover"])
    with patch("duplo.main.call_log.start_run") as start_run:
        main()
    start_run.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {"pending": []}
    assert list(tmp_path.iterdir()) == []


def test_examples_publish_preserves_notes_and_keeps_previous_generation(tmp_path):
    root = save_examples([example("first")], target_dir=tmp_path)
    (root / "notes.txt").write_text("retain me")
    save_examples([example("second")], target_dir=tmp_path)
    assert load_examples(target_dir=tmp_path)[0].input == "second"
    assert (root / "notes.txt").read_text() == "retain me"
    assert list((tmp_path / ".duplo" / "operations").glob("*/before/000_first.json"))
    assert pending(tmp_path) == []
