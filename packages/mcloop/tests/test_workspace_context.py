"""Tests for mcloop.workspace_context."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mcloop.workspace_context import WorkspaceContext


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


def test_root_scope_invariant_violation_execution_cwd() -> None:
    with pytest.raises(AssertionError):
        WorkspaceContext(
            workspace_root=Path("/repo"),
            scope="root",
            scope_root=Path("/repo"),
            execution_cwd=Path("/repo/packages/orchestra"),
            plan_path=Path("/repo/PLAN.md"),
        )


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
