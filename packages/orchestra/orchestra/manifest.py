"""Run-time integrity manifest for resumable workflows.

Pass-3 fix #3 hashed the .orc file at run_start so cmd_resume could
refuse to replay against a workflow whose semantics drifted between
the original run and the resume invocation. That signal misses
file-backed prompt sources (templates/*.md and config-supplied
instruction templates), which are read from disk at invocation time;
editing one between crash and resume changes the actor input while
the workflow digest still matches.

This module computes a manifest of every file-backed prompt source
the executor would read at run time, after instruction-template
overrides resolve. Both cmd_run and api.run_workflow stamp the
manifest into the run_start record; cmd_resume recomputes the
manifest against the current files and refuses on any mismatch.

Manifest shape: ``{"<absolute path>": "<sha256 hex>"}``. The path is
normalized via ``Path.resolve()`` so symlinks and relative-from
differences do not produce spurious mismatches across runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from orchestra.spine import PromptSource, Workflow


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_prompt_files(workflow: Workflow) -> list[Path]:
    """Walk every state and role, collecting file paths for prompt
    sources that read from disk at invocation time.

    The api layer's ``_apply_instruction_templates`` rewrites each
    role's default_prompt to point at an absolute resolved path;
    state.prompt may carry a path relative to ``workflow.source_dir``
    (the .orc file's directory). Both shapes are normalized into
    absolute paths so the manifest digest is independent of how the
    path was spelled.
    """
    sources: list[PromptSource] = []
    for role in workflow.roles:
        sources.append(role.default_prompt)
    for state in workflow.states:
        if state.prompt is not None:
            sources.append(state.prompt)

    base = (
        Path(workflow.source_dir)
        if workflow.source_dir
        else Path.cwd()
    )
    out: list[Path] = []
    for src in sources:
        if src.kind not in ("file", "template"):
            continue
        if src.path is None:
            continue
        candidate = Path(src.path)
        if not candidate.is_absolute():
            candidate = base / candidate
        out.append(candidate)
    return out


def compute_prompt_manifest(workflow: Workflow) -> dict[str, str]:
    """Return ``{absolute_path: sha256_hex}`` for every file-backed
    prompt source the workflow names.

    Missing files are recorded with the digest ``"<missing>"`` so
    resume catches both content drift and file removal. Duplicate
    paths (the same file reached via two roles or via a role and a
    state) collapse to one entry.
    """
    out: dict[str, str] = {}
    for path in _collect_prompt_files(workflow):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved)
        if key in out:
            continue
        if not path.is_file():
            out[key] = "<missing>"
            continue
        out[key] = _digest_file(path)
    return out


def diff_prompt_manifests(
    recorded: dict[str, str],
    current: dict[str, str],
) -> list[str]:
    """Return a human-readable list of differences between two
    manifests. Empty list means the manifests match.

    The list contains one entry per drift, naming the path and the
    direction of the change (added, removed, content changed). The
    caller passes this to the user so they can find the file that
    drifted.
    """
    lines: list[str] = []
    for path, recorded_digest in sorted(recorded.items()):
        current_digest = current.get(path)
        if current_digest is None:
            lines.append(f"  removed: {path}")
            continue
        if current_digest != recorded_digest:
            lines.append(
                f"  changed: {path} "
                f"(was {recorded_digest[:12]}..., "
                f"now {current_digest[:12]}...)"
            )
    for path in sorted(current.keys() - recorded.keys()):
        lines.append(f"  added:   {path}")
    return lines
