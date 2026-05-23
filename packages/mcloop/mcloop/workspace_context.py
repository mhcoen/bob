"""WorkspaceContext primitive for workspace/scope adaptation.

Introduced in Stage 12 of the workspace-context migration. Subsequent
stages thread this object through git operations, the run loop,
checks, builds, logging, and subcommand dispatch. See PLAN.md Stage 12
for the migration plan and the compatibility-mode invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WORKSPACE_TABLE_MARKER = "[tool.uv.workspace]"


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


class WorkspaceResolutionError(Exception):
    """Raised by :func:`resolve_workspace_context` when inputs are inconsistent.

    Carries a machine-readable ``kind`` discriminator plus a ``details``
    mapping so the CLI (Stage 13) can render structured diagnostics
    without re-parsing the message. The string form of the exception is
    a complete, ready-to-print error message.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.details: dict[str, Any] = dict(details or {})


def _is_workspace_root(directory: Path) -> bool:
    """Return True if *directory* is a uv-workspace root.

    A directory qualifies when it contains both a ``.git`` directory
    and a ``pyproject.toml`` that declares ``[tool.uv.workspace]``.
    The workspace table marker is the same authoritative signal used
    by ``_refuse_nested_init`` in ``mcloop/git_ops.py`` -- ancestor
    directory names (e.g. ``packages``) are not used.
    """
    if not (directory / ".git").is_dir():
        return False
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        content = pyproject.read_text()
    except OSError:
        return False
    return _WORKSPACE_TABLE_MARKER in content


def _find_workspace_root(anchor: Path) -> Path | None:
    """Walk upward (inclusive) from *anchor* looking for a workspace root.

    Returns the first directory (starting with *anchor* itself, then its
    parents) that satisfies :func:`_is_workspace_root`. Returns ``None``
    if no ancestor qualifies.
    """
    anchor = anchor.resolve()
    for candidate in (anchor, *anchor.parents):
        if _is_workspace_root(candidate):
            return candidate
    return None


def resolve_workspace_context(
    cwd: Path,
    plan_path: Path | None,
    *,
    workspace_override: Path | None = None,
    scope_override: str | None = None,
) -> WorkspaceContext:
    """Resolve a :class:`WorkspaceContext` from CLI inputs and the filesystem.

    Resolution rules, applied in order:

    1. If *workspace_override* is given, use it directly as
       ``workspace_root``. Otherwise walk upward from the *anchor*
       looking for a workspace root (see :func:`_find_workspace_root`).
       If the walk finds nothing and no override is given, the
       standalone case applies.
    2. The *anchor* is ``plan_path.parent`` when *plan_path* is given
       explicitly, otherwise *cwd*. This preserves the historic
       behavior where ``mcloop --file /other/repo/PLAN.md`` operates
       against ``/other/repo/`` regardless of cwd.
    3. Ambiguity checks: if *plan_path* is explicit and *cwd* is inside
       a workspace different from the one containing ``plan_path.parent``,
       raise :class:`WorkspaceResolutionError`. Likewise if
       *workspace_override* disagrees with the workspace found by
       walking upward from the anchor.
    4. Standalone case (no workspace ancestor found): every path field
       collapses to ``plan_path.parent`` (or *cwd* when *plan_path* is
       absent); ``scope == "root"``; ``plan_path`` defaults to
       ``scope_root / "PLAN.md"``. A *scope_override* other than
       ``"root"`` is rejected.
    5. Consolidated case (workspace ancestor found): if the anchor is
       ``workspace_root`` itself then ``scope == "root"`` and
       ``scope_root == workspace_root``; if the anchor lives under
       ``workspace_root/packages/<name>/`` then ``scope == "<name>"``
       and ``scope_root == workspace_root/packages/<name>``; any other
       layout is rejected. ``execution_cwd == cwd``; ``plan_path``
       defaults to ``scope_root / "PLAN.md"``.
    6. *scope_override*, if given, must match the resolved scope.
    """
    cwd_resolved = Path(cwd).resolve()

    plan_path_explicit = plan_path is not None
    if plan_path_explicit:
        plan_obj = Path(plan_path)  # type: ignore[arg-type]
        if not plan_obj.is_absolute():
            plan_obj = cwd_resolved / plan_obj
        resolved_plan = plan_obj.resolve()
        plan_parent: Path | None = resolved_plan.parent
        anchor = resolved_plan.parent
    else:
        resolved_plan = None
        plan_parent = None
        anchor = cwd_resolved

    anchor_workspace = _find_workspace_root(anchor)

    if plan_path_explicit:
        cwd_workspace = _find_workspace_root(cwd_resolved)
        plan_workspace = anchor_workspace
        if (
            cwd_workspace is not None
            and plan_workspace is not None
            and cwd_workspace != plan_workspace
        ):
            raise WorkspaceResolutionError(
                f"--plan-path points into workspace {plan_workspace}, "
                f"but cwd {cwd_resolved} is inside a different workspace "
                f"{cwd_workspace}. Re-run mcloop from inside the workspace "
                f"that contains the plan, or pass --workspace explicitly.",
                kind="plan_workspace_mismatch",
                details={
                    "cwd": str(cwd_resolved),
                    "cwd_workspace": str(cwd_workspace),
                    "plan_path": str(resolved_plan),
                    "plan_workspace": str(plan_workspace),
                },
            )

    if workspace_override is not None:
        workspace_override_obj = Path(workspace_override)
        if not workspace_override_obj.is_absolute():
            workspace_override_obj = cwd_resolved / workspace_override_obj
        workspace_root_override = workspace_override_obj.resolve()
        if anchor_workspace is not None and anchor_workspace != workspace_root_override:
            raise WorkspaceResolutionError(
                f"--workspace override {workspace_root_override} disagrees "
                f"with the workspace {anchor_workspace} discovered by "
                f"walking up from the anchor {anchor}.",
                kind="workspace_override_disagreement",
                details={
                    "workspace_override": str(workspace_root_override),
                    "anchor": str(anchor),
                    "anchor_workspace": str(anchor_workspace),
                },
            )
        workspace_root: Path | None = workspace_root_override
        consolidated = True
    elif anchor_workspace is not None:
        workspace_root = anchor_workspace
        consolidated = True
    else:
        workspace_root = None
        consolidated = False

    if not consolidated:
        if plan_path_explicit:
            assert resolved_plan is not None and plan_parent is not None
            base = plan_parent
            standalone_plan_path = resolved_plan
        else:
            base = cwd_resolved
            standalone_plan_path = base / "PLAN.md"

        if scope_override is not None and scope_override != "root":
            raise WorkspaceResolutionError(
                f"--scope override {scope_override!r} disagrees with the "
                f"resolved scope 'root' (standalone repository at {base}).",
                kind="scope_override_disagreement",
                details={
                    "scope_override": scope_override,
                    "resolved_scope": "root",
                    "scope_root": str(base),
                },
            )

        return WorkspaceContext(
            workspace_root=base,
            scope="root",
            scope_root=base,
            execution_cwd=base,
            plan_path=standalone_plan_path,
        )

    assert workspace_root is not None
    try:
        rel = anchor.relative_to(workspace_root)
    except ValueError:
        raise WorkspaceResolutionError(
            f"Anchor {anchor} is not inside workspace root "
            f"{workspace_root}. Re-run mcloop from inside the workspace, "
            f"or pass --workspace with a matching path.",
            kind="anchor_outside_workspace",
            details={
                "anchor": str(anchor),
                "workspace_root": str(workspace_root),
            },
        ) from None

    parts = rel.parts
    if len(parts) == 0:
        scope = "root"
        scope_root = workspace_root
    elif len(parts) >= 2 and parts[0] == "packages":
        scope = parts[1]
        scope_root = workspace_root / "packages" / scope
    else:
        raise WorkspaceResolutionError(
            f"Anchor {anchor} sits in an unsupported workspace layout "
            f"(expected the workspace root itself or "
            f"{workspace_root}/packages/<name>/...).",
            kind="unsupported_layout",
            details={
                "anchor": str(anchor),
                "workspace_root": str(workspace_root),
                "relative_parts": list(parts),
            },
        )

    if scope_override is not None and scope_override != scope:
        raise WorkspaceResolutionError(
            f"--scope override {scope_override!r} disagrees with the "
            f"resolved scope {scope!r} (scope_root={scope_root}).",
            kind="scope_override_disagreement",
            details={
                "scope_override": scope_override,
                "resolved_scope": scope,
                "scope_root": str(scope_root),
            },
        )

    if plan_path_explicit:
        assert resolved_plan is not None
        consolidated_plan_path = resolved_plan
    else:
        consolidated_plan_path = scope_root / "PLAN.md"

    return WorkspaceContext(
        workspace_root=workspace_root,
        scope=scope,
        scope_root=scope_root,
        execution_cwd=cwd_resolved,
        plan_path=consolidated_plan_path,
    )
