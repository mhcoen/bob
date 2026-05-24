"""Legacy prompt-manifest helper for resumable workflows.

Pass-4 runs recorded a sha256 digest for each file-backed prompt
source in ``prompt_manifest``. Current runs use prompt snapshots
instead; ``cli.cmd_resume`` retains an inline compatibility gate for
old logs that have ``prompt_manifest`` but no snapshot manifest.

This module preserves the old manifest computation for regression
tests that construct legacy run_start records. Manifest shape is
``{"<absolute path>": "<sha256 hex>"}``; paths are normalized via
``Path.resolve()`` to match the historical format.
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

