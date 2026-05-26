"""Prompt-source snapshotting for resume integrity.

The pass-4 ``prompt_manifest`` recorded sha256 digests of file-backed
prompt sources at run_start and refused resume on any drift. Two
audit passes (pass-5 #1 symlink retargeting, pass-5 #2 relative
workflow paths) found that "the path the manifest recorded" and "the
path the executor actually opens" can drift through symlink targets,
working-directory changes, and source_dir ambiguity. The right
abstraction is to copy the bytes the run will use into the run
directory at run_start and resolve resume against that snapshot
rather than against the live filesystem.

This module owns that snapshot. ``snapshot_prompt_sources`` is called
once at new-run creation, walks every file/template prompt on the
workflow, copies the resolved bytes into ``<run_dir>/prompt_sources/``,
rewrites the in-memory workflow so prompt paths point at the snapshot
files, and returns a manifest list that gets stamped into the
``run_start`` log record.

``restore_prompt_snapshots`` is called at resume time. It takes the
freshly-loaded workflow, walks the recorded manifest, verifies each
snapshot file exists and matches its recorded sha256 (mid-run
mutation is a hard refusal: snapshots are read-only inputs once the
run begins), and rewrites the workflow so prompt sources point at the
snapshot files instead of the live declared paths.

Resume becomes deterministic with respect to prompt inputs: symlink
retargeting, relative-cwd resume, moved files, deleted files, and
source-dir ambiguity all stop mattering because the resumed actor
reads the same bytes the original run pinned.

Backward compatibility: old runs that were created before this module
shipped have no ``prompt_snapshot_manifest`` field in run_start. The
caller falls back to the legacy ``prompt_manifest`` gate (or, for
even older runs, the ``workflow_digest``-only gate). A new run gets
the snapshot path; the manifest digest is metadata for snapshot
integrity, not the primary drift detector.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from orchestra.spine import (
    ArtifactDecl,
    PromptSource,
    RoleDecl,
    StateDecl,
    Workflow,
)

SNAPSHOT_DIR_NAME = "prompt_sources"


def _resolved_existing(source: PromptSource, base: Path) -> Path | None:
    """Return the absolute path to the file the executor would open
    for ``source``, or ``None`` if the source is not file-backed or
    the file does not exist.

    Mirrors ``Executor._render_prompt``'s resolution: an absolute
    ``source.path`` is used as-is; a relative path is resolved
    against ``base`` (the workflow's ``source_dir``).
    """
    if source.kind not in ("file", "template"):
        return None
    if source.path is None:
        return None
    candidate = Path(source.path)
    if not candidate.is_absolute():
        candidate = base / candidate
    if not candidate.is_file():
        return None
    return candidate


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_private(src: Path, dst: Path) -> None:
    """Copy bytes from ``src`` to ``dst`` and force 0600 on the
    destination.

    ``shutil.copyfile`` honours the calling process's umask, which
    on most multi-user POSIX systems leaves files at 0644 and
    therefore world-readable. Snapshot files are sensitive (the
    audit verified they may contain credentials and proprietary
    context), so an explicit chmod follows every copy.
    """
    shutil.copyfile(src, dst)
    try:
        dst.chmod(0o600)
    except OSError:
        # Filesystems without POSIX permission semantics fall through;
        # the audit threat model is multi-user POSIX hosts.
        pass


def _snapshot_filename(kind: str, name: str, source_path: str) -> str:
    """Build a stable filename for a snapshot under prompt_sources/.

    Two roles that share the same template file get distinct
    snapshots so the rewritten workflow can point each role at its
    own snapshot path; the ``role_<name>`` / ``state_<name>`` prefix
    guarantees uniqueness. The original extension is preserved so
    debug tooling and human readers can recognize the file type.
    """
    suffix = Path(source_path).suffix or ".prompt"
    return f"{kind}_{name}{suffix}"


def snapshot_prompt_sources(
    workflow: Workflow, run_dir: Path
) -> tuple[Workflow, list[dict[str, Any]]]:
    """Snapshot every file-backed prompt source the workflow names.

    Returns the rewritten workflow (prompt paths point at snapshot
    files) and the manifest list that should be stamped into the
    run_start log record.

    The rewrite is the load-bearing step. Without it, the executor
    would still open the original declared paths and the snapshot
    would be unused metadata.
    """
    snapshot_dir = run_dir / SNAPSHOT_DIR_NAME
    # Pass-8 fix #2: snapshot files may contain credentials,
    # proprietary context, or customer data. Default umask 022
    # produces 0755 directories and 0644 files, leaving the
    # snapshots world-readable. Force a private mode 0700 on the
    # directory so other local users cannot enumerate it. Files
    # written below also get chmod 0600 to close the same hole.
    snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        snapshot_dir.chmod(0o700)
    except OSError:
        # On platforms or filesystems that do not support chmod
        # (e.g. some Windows configurations), proceed; the audit's
        # threat model is multi-user POSIX systems.
        pass

    base = Path(workflow.source_dir) if workflow.source_dir else Path.cwd()

    manifest: list[dict[str, Any]] = []

    new_roles: list[RoleDecl] = []
    for role in workflow.roles:
        resolved = _resolved_existing(role.default_prompt, base)
        if resolved is None:
            new_roles.append(role)
            continue
        snapshot_name = _snapshot_filename("role", role.name, str(resolved))
        snapshot_path = snapshot_dir / snapshot_name
        _copy_private(resolved, snapshot_path)
        digest = _digest_file(snapshot_path)
        manifest.append(
            {
                "kind": "role",
                "name": role.name,
                "original_path": role.default_prompt.path or "",
                "resolved_path": str(resolved),
                "snapshot_path": str(snapshot_path),
                "sha256": digest,
            }
        )
        new_default = replace(role.default_prompt, path=str(snapshot_path))
        new_roles.append(replace(role, default_prompt=new_default))

    new_states: list[StateDecl] = []
    for state in workflow.states:
        if state.prompt is None:
            new_states.append(state)
            continue
        resolved = _resolved_existing(state.prompt, base)
        if resolved is None:
            new_states.append(state)
            continue
        snapshot_name = _snapshot_filename("state", state.name, str(resolved))
        snapshot_path = snapshot_dir / snapshot_name
        _copy_private(resolved, snapshot_path)
        digest = _digest_file(snapshot_path)
        manifest.append(
            {
                "kind": "state",
                "name": state.name,
                "original_path": state.prompt.path or "",
                "resolved_path": str(resolved),
                "snapshot_path": str(snapshot_path),
                "sha256": digest,
            }
        )
        new_prompt = replace(state.prompt, path=str(snapshot_path))
        new_states.append(replace(state, prompt=new_prompt))

    # Schema-verdict 1.5: schema files are static inputs identical in
    # spirit to prompt templates. The walk includes every artifact's
    # schema "<path>" qualifier; the workflow's artifact decl is
    # rewritten so the executor's schema-spec cache loads the
    # snapshot copy. Resume verifies the snapshot's bytes against the
    # recorded sha256 so a schema file edited between crash and
    # resume is a hard refusal.
    new_artifacts: list[ArtifactDecl] = []
    for art in workflow.artifacts:
        if art.schema_path is None:
            new_artifacts.append(art)
            continue
        candidate = Path(art.schema_path)
        if not candidate.is_absolute():
            candidate = base / art.schema_path
        if not candidate.is_file():
            new_artifacts.append(art)
            continue
        snapshot_name = _schema_snapshot_filename(art.name, str(candidate))
        snapshot_path = snapshot_dir / snapshot_name
        _copy_private(candidate, snapshot_path)
        digest = _digest_file(snapshot_path)
        manifest.append(
            {
                "kind": "schema",
                "name": art.name,
                "original_path": art.schema_path,
                "resolved_path": str(candidate),
                "snapshot_path": str(snapshot_path),
                "sha256": digest,
            }
        )
        new_artifacts.append(replace(art, schema_path=str(snapshot_path)))

    workflow.roles = tuple(new_roles)
    workflow.states = tuple(new_states)
    workflow.artifacts = tuple(new_artifacts)
    return workflow, manifest


def _schema_snapshot_filename(artifact_name: str, source_path: str) -> str:
    """Stable filename for a snapshotted schema, parallel to
    ``_snapshot_filename`` for prompt sources.
    """
    suffix = Path(source_path).suffix or ".json"
    return f"schema_{artifact_name}{suffix}"


class SnapshotIntegrityError(Exception):
    """Raised when a recorded snapshot file has been mutated, deleted,
    or its bytes no longer match the recorded digest.

    Snapshots are read-only inputs once the run begins. Anything that
    changes a snapshot file mid-run is a hard refusal because the
    original bytes the run was pinning are gone and the resumed
    invocation would read input the original run never saw.
    """


def restore_prompt_snapshots(workflow: Workflow, manifest: list[dict[str, Any]]) -> Workflow:
    """Rewrite ``workflow`` so file-backed prompts point at the
    recorded snapshot files. Verify each snapshot's current digest
    matches the recorded one; raise ``SnapshotIntegrityError`` on
    any drift.

    The verification step covers two cases:
      - the snapshot file was deleted between run_start and resume,
      - the snapshot file was edited between run_start and resume
        (e.g., by a buggy adapter that wrote outside its sandbox, or
        by the user manually).

    Both are conditions where the resumed actor would not see the
    original bytes; resume must refuse rather than proceed.
    """
    by_role: dict[str, str] = {}
    by_state: dict[str, str] = {}
    by_schema: dict[str, str] = {}
    for entry in manifest:
        kind = entry.get("kind")
        name = entry.get("name")
        snapshot_path_str = entry.get("snapshot_path")
        recorded_digest = entry.get("sha256")
        if (
            not isinstance(kind, str)
            or not isinstance(name, str)
            or not isinstance(snapshot_path_str, str)
            or not isinstance(recorded_digest, str)
        ):
            raise SnapshotIntegrityError(f"malformed snapshot manifest entry: {entry!r}")
        snapshot_path = Path(snapshot_path_str)
        if not snapshot_path.is_file():
            raise SnapshotIntegrityError(
                f"snapshot file missing for {kind} {name!r}: {snapshot_path}"
            )
        current_digest = _digest_file(snapshot_path)
        if current_digest != recorded_digest:
            raise SnapshotIntegrityError(
                f"snapshot file for {kind} {name!r} has been mutated "
                f"since run_start; recorded digest "
                f"{recorded_digest[:12]}..., current "
                f"{current_digest[:12]}.... Resume cannot proceed: "
                "the original prompt bytes are no longer available."
            )
        if kind == "role":
            by_role[name] = snapshot_path_str
        elif kind == "state":
            by_state[name] = snapshot_path_str
        elif kind == "schema":
            by_schema[name] = snapshot_path_str
        else:
            raise SnapshotIntegrityError(f"unknown snapshot kind {kind!r} for {name!r}")

    new_roles: list[RoleDecl] = []
    for role in workflow.roles:
        snap = by_role.get(role.name)
        if snap is None or role.default_prompt.kind not in ("file", "template"):
            new_roles.append(role)
            continue
        new_default = replace(role.default_prompt, path=snap)
        new_roles.append(replace(role, default_prompt=new_default))

    new_states: list[StateDecl] = []
    for state in workflow.states:
        snap = by_state.get(state.name)
        if snap is None or state.prompt is None or state.prompt.kind not in ("file", "template"):
            new_states.append(state)
            continue
        new_prompt = replace(state.prompt, path=snap)
        new_states.append(replace(state, prompt=new_prompt))

    new_artifacts: list[ArtifactDecl] = []
    for art in workflow.artifacts:
        snap = by_schema.get(art.name)
        if snap is None or art.schema_path is None:
            new_artifacts.append(art)
            continue
        new_artifacts.append(replace(art, schema_path=snap))

    workflow.roles = tuple(new_roles)
    workflow.states = tuple(new_states)
    workflow.artifacts = tuple(new_artifacts)
    return workflow
