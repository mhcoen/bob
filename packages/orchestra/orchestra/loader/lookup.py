"""Workflow name resolution.

Resolves a workflow name (e.g. ``single``) to a ``.orc`` file path.
Search order:

1. The project-local override directory at
   ``<project_dir>/.orchestra/workflows/<name>.orc``. The consumer
   project (mcloop, Duplo, or any other) keeps overrides here so a
   project can ship a customized version of a packaged workflow without
   forking the orchestra package.
2. The packaged directory at ``orchestra/workflows/<name>.orc`` shipped
   with this package.

Raises ``WorkflowNotFound`` if neither location contains the file.
"""

from __future__ import annotations

from pathlib import Path

from orchestra.errors import OrchestraError

PROJECT_OVERRIDE_DIR: str = ".orchestra/workflows"
"""Project-local override directory, relative to the project root."""

PACKAGED_DIR_NAME: str = "workflows"
"""Directory under the orchestra package that ships built-in workflows."""


class WorkflowNotFound(OrchestraError):
    """Raised when a workflow name cannot be resolved to a file."""

    def __init__(self, name: str, searched: list[Path]) -> None:
        searched_str = ", ".join(str(p) for p in searched)
        super().__init__(f"workflow {name!r} not found. searched: {searched_str}")
        self.name = name
        self.searched = searched


def packaged_workflows_dir() -> Path:
    """Return the directory holding built-in workflow files."""
    return Path(__file__).resolve().parent.parent / PACKAGED_DIR_NAME


def project_workflows_dir(project_dir: Path) -> Path:
    """Return the project-local override directory under ``project_dir``."""
    return Path(project_dir) / PROJECT_OVERRIDE_DIR


def resolve_workflow_path(
    name: str,
    *,
    project_dir: Path | str | None = None,
) -> Path:
    """Resolve ``name`` to an existing ``.orc`` file path.

    The project-local override takes precedence over the packaged copy.
    ``name`` must be a bare workflow name (no extension, no directory
    parts). Path-like names raise ``ValueError`` to keep the lookup
    surface narrow.
    """
    if "/" in name or "\\" in name or name.endswith(".orc") or ".." in name:
        raise ValueError(f"workflow name must be a bare identifier, got {name!r}")

    searched: list[Path] = []
    if project_dir is not None:
        local = project_workflows_dir(Path(project_dir)) / f"{name}.orc"
        searched.append(local)
        if local.is_file():
            return local

    packaged = packaged_workflows_dir() / f"{name}.orc"
    searched.append(packaged)
    if packaged.is_file():
        return packaged

    raise WorkflowNotFound(name, searched)
