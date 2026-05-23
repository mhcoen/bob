"""Tests for mcloop.workspace_context."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mcloop.workspace_context import (
    WorkspaceContext,
    WorkspaceResolutionError,
    resolve_workspace_context,
)


def _make_workspace_root(root: Path) -> Path:
    """Create a directory that satisfies ``_is_workspace_root``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "ws"\n\n[tool.uv.workspace]\nmembers = ["packages/*"]\n'
    )
    return root.resolve()


def _make_package(workspace: Path, name: str) -> Path:
    """Create ``workspace/packages/<name>/`` and return the resolved path."""
    pkg = workspace / "packages" / name
    pkg.mkdir(parents=True, exist_ok=True)
    return pkg.resolve()


def _make_plain_dir(path: Path) -> Path:
    """Create a non-workspace directory (no .git, no pyproject)."""
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def test_frozen_dataclass() -> None:
    p = Path("/repo")
    ctx = WorkspaceContext(
        workspace_root=p,
        scope="root",
        scope_root=p,
        execution_cwd=p,
        plan_path=p / "PLAN.md",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.scope = "other"  # type: ignore[misc]


def test_root_scope_compat_invariant_holds() -> None:
    p = Path("/repo")
    ctx = WorkspaceContext(
        workspace_root=p,
        scope="root",
        scope_root=p,
        execution_cwd=p,
        plan_path=p / "PLAN.md",
    )
    assert ctx.workspace_root == ctx.scope_root == ctx.execution_cwd


def test_root_scope_invariant_violation_workspace_vs_scope_root() -> None:
    with pytest.raises(AssertionError):
        WorkspaceContext(
            workspace_root=Path("/repo"),
            scope="root",
            scope_root=Path("/repo/packages/orchestra"),
            execution_cwd=Path("/repo"),
            plan_path=Path("/repo/PLAN.md"),
        )


def test_root_scope_allows_execution_cwd_in_subdir() -> None:
    ctx = WorkspaceContext(
        workspace_root=Path("/repo"),
        scope="root",
        scope_root=Path("/repo"),
        execution_cwd=Path("/repo/packages/orchestra"),
        plan_path=Path("/repo/PLAN.md"),
    )
    assert ctx.workspace_root == ctx.scope_root == Path("/repo")
    assert ctx.execution_cwd == Path("/repo/packages/orchestra")


def test_non_root_scope_skips_invariant() -> None:
    workspace = Path("/repo")
    pkg = workspace / "packages" / "orchestra"
    ctx = WorkspaceContext(
        workspace_root=workspace,
        scope="orchestra",
        scope_root=pkg,
        execution_cwd=pkg,
        plan_path=pkg / "PLAN.md",
    )
    assert ctx.scope == "orchestra"
    assert ctx.workspace_root != ctx.scope_root


def _assert_compat_invariant(ctx: WorkspaceContext) -> None:
    """Compatibility-mode invariant for every standalone case."""
    assert ctx.scope == "root"
    assert ctx.workspace_root == ctx.scope_root == ctx.execution_cwd


def test_standalone_cwd_anchored(tmp_path: Path) -> None:
    repo = _make_plain_dir(tmp_path / "repo")

    ctx = resolve_workspace_context(repo, None)

    assert ctx.workspace_root == repo
    assert ctx.plan_path == repo / "PLAN.md"
    _assert_compat_invariant(ctx)


def test_standalone_plan_path_anchored(tmp_path: Path) -> None:
    repo = _make_plain_dir(tmp_path / "repo")
    plan_path = repo / "PLAN.md"

    ctx = resolve_workspace_context(repo, plan_path)

    assert ctx.workspace_root == repo
    assert ctx.plan_path == plan_path.resolve()
    _assert_compat_invariant(ctx)


def test_standalone_plan_path_wins_over_cwd(tmp_path: Path) -> None:
    """plan_path.parent anchors the resolution; cwd is ignored for scope_root."""
    cwd = _make_plain_dir(tmp_path / "cwd")
    other_repo = _make_plain_dir(tmp_path / "other")
    plan_path = other_repo / "PLAN.md"

    ctx = resolve_workspace_context(cwd, plan_path)

    assert ctx.workspace_root == other_repo
    assert ctx.scope_root == other_repo
    assert ctx.execution_cwd == other_repo
    assert ctx.plan_path == plan_path.resolve()
    _assert_compat_invariant(ctx)


def test_workspace_ancestor_cwd_at_root(tmp_path: Path) -> None:
    workspace = _make_workspace_root(tmp_path / "ws")

    ctx = resolve_workspace_context(workspace, None)

    assert ctx.workspace_root == workspace
    assert ctx.scope == "root"
    assert ctx.scope_root == workspace
    assert ctx.execution_cwd == workspace
    assert ctx.plan_path == workspace / "PLAN.md"


def test_workspace_ancestor_cwd_in_package(tmp_path: Path) -> None:
    workspace = _make_workspace_root(tmp_path / "ws")
    pkg = _make_package(workspace, "orchestra")

    ctx = resolve_workspace_context(pkg, None)

    assert ctx.workspace_root == workspace
    assert ctx.scope == "orchestra"
    assert ctx.scope_root == pkg
    assert ctx.execution_cwd == pkg
    assert ctx.plan_path == pkg / "PLAN.md"


def test_plan_path_in_different_workspace_raises(tmp_path: Path) -> None:
    workspace_a = _make_workspace_root(tmp_path / "wsA")
    workspace_b = _make_workspace_root(tmp_path / "wsB")
    plan_path = workspace_b / "PLAN.md"

    with pytest.raises(WorkspaceResolutionError) as exc_info:
        resolve_workspace_context(workspace_a, plan_path)

    err = exc_info.value
    assert err.kind == "plan_workspace_mismatch"
    assert err.details["cwd_workspace"] == str(workspace_a)
    assert err.details["plan_workspace"] == str(workspace_b)


def test_scope_override_matches_resolved_scope(tmp_path: Path) -> None:
    workspace = _make_workspace_root(tmp_path / "ws")
    pkg = _make_package(workspace, "orchestra")

    ctx = resolve_workspace_context(pkg, None, scope_override="orchestra")

    assert ctx.scope == "orchestra"
    assert ctx.scope_root == pkg


def test_scope_override_disagrees_raises(tmp_path: Path) -> None:
    workspace = _make_workspace_root(tmp_path / "ws")
    pkg = _make_package(workspace, "orchestra")

    with pytest.raises(WorkspaceResolutionError) as exc_info:
        resolve_workspace_context(pkg, None, scope_override="duplo")

    err = exc_info.value
    assert err.kind == "scope_override_disagreement"
    assert err.details["scope_override"] == "duplo"
    assert err.details["resolved_scope"] == "orchestra"


def test_workspace_override_matches(tmp_path: Path) -> None:
    workspace = _make_workspace_root(tmp_path / "ws")
    pkg = _make_package(workspace, "orchestra")

    ctx = resolve_workspace_context(pkg, None, workspace_override=workspace)

    assert ctx.workspace_root == workspace
    assert ctx.scope == "orchestra"
    assert ctx.scope_root == pkg


def test_workspace_override_disagrees_raises(tmp_path: Path) -> None:
    workspace_a = _make_workspace_root(tmp_path / "wsA")
    workspace_b = _make_workspace_root(tmp_path / "wsB")

    with pytest.raises(WorkspaceResolutionError) as exc_info:
        resolve_workspace_context(workspace_a, None, workspace_override=workspace_b)

    err = exc_info.value
    assert err.kind == "workspace_override_disagreement"
    assert err.details["workspace_override"] == str(workspace_b)
    assert err.details["anchor_workspace"] == str(workspace_a)
