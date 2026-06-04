"""Tests for prompt-source snapshotting.

Pass-5 redesign replaces the prompt_manifest digest-and-refuse gate
with a snapshot-and-rewrite mechanism. The earlier manifest gate had
two distinct bypasses (symlink retargeting and source_dir ambiguity)
that came from "the path the manifest recorded" not matching "the
path the executor opens." Snapshotting eliminates the path-identity
question entirely: the executor opens snapshot files in the run
directory, not the live declared paths.

These tests cover both halves: the run-start snapshot (bytes copied,
workflow rewritten, manifest produced) and the resume-time restore
(integrity verification, workflow rewritten to point at snapshots,
hard refusal on snapshot mutation).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from orchestra.prompt_snapshot import (
    SnapshotIntegrityError,
    restore_prompt_snapshots,
    snapshot_prompt_sources,
)
from orchestra.spine import (
    ActorBinding,
    ArtifactDecl,
    PromptSource,
    RoleDecl,
    StateDecl,
    Transition,
    Workflow,
    WriteDecl,
)


def _build_workflow(
    tmp_path: Path,
    prompt_path: str = "templates/dummy.md",
) -> Workflow:
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "dummy.md").write_text("hi {{ topic }}\n")
    return Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(
                    kind="template",
                    path=prompt_path,
                    template_vars=("topic",),
                ),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )


def test_snapshot_copies_role_prompt_into_run_dir(tmp_path: Path) -> None:
    workflow = _build_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["kind"] == "role"
    assert entry["name"] == "r"
    snapshot_path = Path(entry["snapshot_path"])
    assert snapshot_path.is_file()
    assert snapshot_path.parent == run_dir / "prompt_sources"
    assert snapshot_path.read_text() == "hi {{ topic }}\n"
    # Manifest digest matches the snapshot bytes.
    expected_digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert entry["sha256"] == expected_digest


def test_snapshot_rewrites_workflow_to_point_at_snapshot(
    tmp_path: Path,
) -> None:
    """The rewrite is the load-bearing step. Without it the executor
    would still open the original declared path and the snapshot
    would be unused metadata."""
    workflow = _build_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    assert rewritten.roles[0].default_prompt.path == manifest[0]["snapshot_path"]
    # Other prompt fields are preserved.
    assert rewritten.roles[0].default_prompt.kind == "template"
    assert rewritten.roles[0].default_prompt.template_vars == ("topic",)


def test_snapshot_handles_state_prompt(tmp_path: Path) -> None:
    """state.prompt entries are snapshotted under state_<name> keys
    just like role.default_prompt, and the state's prompt is
    rewritten to point at the snapshot file."""
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "state.md").write_text("state-specific\n")
    workflow = Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(kind="file", path="templates/state.md"),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                prompt=PromptSource(kind="file", path="templates/state.md"),
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    kinds = sorted(e["kind"] for e in manifest)
    assert kinds == ["role", "state"]
    state_entry = next(e for e in manifest if e["kind"] == "state")
    role_entry = next(e for e in manifest if e["kind"] == "role")
    # Two snapshot files, distinct paths even though they share
    # source bytes; the rewrite uses each snapshot independently.
    assert state_entry["snapshot_path"] != role_entry["snapshot_path"]
    assert (
        rewritten.states[0].prompt is not None
        and rewritten.states[0].prompt.path == state_entry["snapshot_path"]
    )
    assert rewritten.roles[0].default_prompt.path == role_entry["snapshot_path"]


def test_snapshot_skips_non_file_kinds(tmp_path: Path) -> None:
    """A ``from``-kind prompt or a missing path is left alone; only
    file/template kinds with existing files are snapshotted."""
    workflow = Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(kind="from", from_ref="some.field"),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    assert manifest == []
    assert rewritten.roles[0].default_prompt.kind == "from"


def test_snapshot_skips_missing_files(tmp_path: Path) -> None:
    """A declared prompt path that does not exist on disk is left
    alone (the validator's existence check is the right place to
    catch this; snapshot is a runtime-input pinning, not a path
    existence check)."""
    workflow = Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(kind="file", path="not-here.md"),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    assert manifest == []
    # The original path is preserved (no snapshot to point at).
    assert rewritten.roles[0].default_prompt.path == "not-here.md"


def test_restore_rewrites_to_snapshot_paths(tmp_path: Path) -> None:
    workflow = _build_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snap_workflow, manifest = snapshot_prompt_sources(workflow, run_dir)
    snapshot_path = manifest[0]["snapshot_path"]

    # Edit the LIVE template after run_start. Resume should not see
    # this because restore_prompt_snapshots rewrites paths to the
    # snapshot file in run_dir.
    (tmp_path / "templates" / "dummy.md").write_text("LIVE EDITED\n")

    fresh_workflow = _build_workflow(tmp_path)
    restored = restore_prompt_snapshots(fresh_workflow, manifest)
    assert restored.roles[0].default_prompt.path == snapshot_path
    # Reading from that path returns the original bytes, not the
    # post-edit bytes.
    assert Path(snapshot_path).read_text() == "hi {{ topic }}\n"


def test_restore_refuses_when_snapshot_missing(tmp_path: Path) -> None:
    """A snapshot file that has been deleted between run_start and
    resume is a hard refusal: the original bytes are gone."""
    workflow = _build_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, manifest = snapshot_prompt_sources(workflow, run_dir)

    Path(manifest[0]["snapshot_path"]).unlink()

    fresh_workflow = _build_workflow(tmp_path)
    with pytest.raises(SnapshotIntegrityError) as excinfo:
        restore_prompt_snapshots(fresh_workflow, manifest)
    assert "missing" in str(excinfo.value)


def test_restore_refuses_when_snapshot_mutated(tmp_path: Path) -> None:
    """A snapshot file that has been edited between run_start and
    resume is a hard refusal even though the file still exists.
    Snapshots are read-only inputs once the run begins."""
    workflow = _build_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, manifest = snapshot_prompt_sources(workflow, run_dir)

    Path(manifest[0]["snapshot_path"]).write_text("MUTATED\n")

    fresh_workflow = _build_workflow(tmp_path)
    with pytest.raises(SnapshotIntegrityError) as excinfo:
        restore_prompt_snapshots(fresh_workflow, manifest)
    assert "mutated" in str(excinfo.value).lower()


def test_restore_with_symlink_retargeting_does_not_bypass(
    tmp_path: Path,
) -> None:
    """Pass-5 #1 regression test: a symlink retargeting between
    run_start and resume cannot reach the actor because resume reads
    the snapshot file in the run directory, not the live symlink
    target."""
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "a.md").write_text("ORIGINAL A\n")
    (tmpl / "b.md").write_text("OTHER B\n")
    link = tmpl / "active.md"
    link.symlink_to(tmpl / "a.md")

    workflow = Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(kind="file", path="templates/active.md"),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, manifest = snapshot_prompt_sources(workflow, run_dir)

    # Retarget the symlink to the OTHER file. Pre-fix, the legacy
    # manifest gate hashed the resolved target (a.md), so a retarget
    # bypassed the gate. Post-redesign, resume reads the snapshot
    # file in run_dir which contains the original bytes.
    link.unlink()
    link.symlink_to(tmpl / "b.md")

    fresh_workflow = Workflow(
        spec_version="0.1",
        name="t",
        roles=(
            RoleDecl(
                name="r",
                default_prompt=PromptSource(kind="file", path="templates/active.md"),
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                role="r",
                writes=(WriteDecl(name="response", type="text"),),
                transitions=(
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )
    restored = restore_prompt_snapshots(fresh_workflow, manifest)
    prompt = restored.roles[0].default_prompt
    assert prompt.path is not None
    snapshot_path = Path(prompt.path)
    assert snapshot_path.read_text() == "ORIGINAL A\n", (
        "snapshot must hold the bytes captured at run_start, not the "
        "retargeted symlink's new target"
    )


# --------------------------------------------------------------------
# Schema snapshotting (commit 1.5)
# --------------------------------------------------------------------


_SCHEMA_TEXT = (
    '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
    '"type":"object","required":["decision"],'
    '"properties":{"decision":{"type":"string",'
    '"enum":["accept","stop"]}}}\n'
)


def _build_schema_workflow(tmp_path: Path) -> Workflow:
    schemas = tmp_path / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "v.json").write_text(_SCHEMA_TEXT)
    return Workflow(
        spec_version="0.1",
        name="t",
        artifacts=(
            ArtifactDecl(
                name="verdict",
                type="json",
                schema_path="schemas/v.json",
            ),
        ),
        states=(
            StateDecl(
                name="s1",
                actor=ActorBinding(kind="model", ref="m"),
                writes=(WriteDecl(name="verdict", type="json"),),
                transitions=(
                    Transition(outcome="accept", target="done"),
                    Transition(outcome="stop", target="stop"),
                    Transition(outcome="error", target="stop"),
                    Transition(outcome="timeout", target="stop"),
                ),
            ),
        ),
        source_dir=str(tmp_path),
    )


def test_snapshot_copies_schema_into_run_dir(tmp_path: Path) -> None:
    workflow = _build_schema_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rewritten, manifest = snapshot_prompt_sources(workflow, run_dir)
    assert any(e["kind"] == "schema" for e in manifest)
    schema_entry = next(e for e in manifest if e["kind"] == "schema")
    assert schema_entry["name"] == "verdict"
    snapshot_path = Path(schema_entry["snapshot_path"])
    assert snapshot_path.is_file()
    assert snapshot_path.parent == run_dir / "prompt_sources"
    assert snapshot_path.read_text() == _SCHEMA_TEXT
    rewritten_art = next(a for a in rewritten.artifacts if a.name == "verdict")
    assert rewritten_art.schema_path == str(snapshot_path)
    digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert schema_entry["sha256"] == digest


def test_snapshot_schema_file_mode_is_private(tmp_path: Path) -> None:
    import os

    workflow = _build_schema_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    saved_umask = os.umask(0o022)
    try:
        _, manifest = snapshot_prompt_sources(workflow, run_dir)
    finally:
        os.umask(saved_umask)
    schema_entry = next(e for e in manifest if e["kind"] == "schema")
    snapshot_path = Path(schema_entry["snapshot_path"])
    mode = snapshot_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_restore_rewrites_schema_paths(tmp_path: Path) -> None:
    workflow = _build_schema_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, manifest = snapshot_prompt_sources(workflow, run_dir)
    fresh = _build_schema_workflow(tmp_path)
    restored = restore_prompt_snapshots(fresh, manifest)
    restored_art = next(a for a in restored.artifacts if a.name == "verdict")
    schema_entry = next(e for e in manifest if e["kind"] == "schema")
    assert restored_art.schema_path == schema_entry["snapshot_path"]


def test_restore_rejects_mutated_schema_snapshot(tmp_path: Path) -> None:
    workflow = _build_schema_workflow(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, manifest = snapshot_prompt_sources(workflow, run_dir)
    schema_entry = next(e for e in manifest if e["kind"] == "schema")
    snapshot_path = Path(schema_entry["snapshot_path"])
    snapshot_path.chmod(0o600)
    snapshot_path.write_text(_SCHEMA_TEXT.replace("accept", "approve"))
    fresh = _build_schema_workflow(tmp_path)
    with pytest.raises(SnapshotIntegrityError):
        restore_prompt_snapshots(fresh, manifest)
