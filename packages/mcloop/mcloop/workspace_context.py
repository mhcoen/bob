"""WorkspaceContext primitive for workspace/scope adaptation.

Introduced in Stage 12 of the workspace-context migration. Subsequent
stages thread this object through git operations, the run loop,
checks, builds, logging, and subcommand dispatch. See PLAN.md Stage 12
for the migration plan and the compatibility-mode invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved workspace and scope context for a single mcloop run.

    Fields:
        workspace_root: The git root and home of the everything log.
        scope: Either ``"root"`` for standalone / workspace-root runs,
            or a package name (e.g. ``"orchestra"``) for package-scoped
            runs inside a uv workspace.
        scope_root: The directory holding the scope's state files
            (PLAN.md, BUGS.md, .mcloop/, NOTES.md, CLAUDE.md).
        execution_cwd: The directory in which tests, builds, and check
            commands run.
        plan_path: The specific PLAN.md being advanced this run.

    Compatibility-mode invariant: in a standalone repo
    (``scope == "root"``), ``workspace_root``, ``scope_root``, and
    ``execution_cwd`` are all the same directory.
    """

    workspace_root: Path
    scope: str
    scope_root: Path
    execution_cwd: Path
    plan_path: Path

    def __post_init__(self) -> None:
        if self.scope == "root":
            assert self.workspace_root == self.scope_root == self.execution_cwd, (
                "WorkspaceContext compatibility-mode invariant violated: "
                "when scope == 'root', workspace_root, scope_root, and "
                "execution_cwd must be the same directory "
                f"(workspace_root={self.workspace_root!s}, "
                f"scope_root={self.scope_root!s}, "
                f"execution_cwd={self.execution_cwd!s})"
            )
