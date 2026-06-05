"""Tests for ``completed_at`` git backfill (best-effort, ID-targeted)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bob_tools.planfile.backfill import (
    _epoch_to_iso_utc,
    backfill_file,
    backfill_plan,
    resolve_completed_at,
)
from bob_tools.planfile.parser import parse_plan


def _fake_run(
    epoch_by_marker: dict[str, int],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a fake ``subprocess.run`` keyed on the pickaxe ``-S`` marker.

    Returns ``%at`` epoch stdout for a known marker, empty otherwise so the
    resolver treats it as unresolved.
    """

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        marker = cmd[cmd.index("-S") + 1]
        epoch = epoch_by_marker.get(marker)
        stdout = f"{epoch}\n" if epoch is not None else ""
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    return run


_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "# Project\n\n"
    "## Stage 1: Core\n"
    "<!-- phase_id: phase_001 -->\n\n"
    "- [x] T-000001: done already <!-- created_at: 2026-05-01T00:00:00Z -->\n"
    "- [x] T-000002: also done\n"
    "- [ ] T-000003: still open\n"
)


def test_epoch_to_iso_utc_matches_canonical_form() -> None:
    # 2026-05-24T11:15:20Z == 1779621320 (UTC).
    assert _epoch_to_iso_utc(1779621320) == "2026-05-24T11:15:20Z"


def test_resolve_uses_flip_commit_author_timestamp() -> None:
    run = _fake_run({"[x] T-000001:": 1779621320})
    ts = resolve_completed_at("T-000001", "PLAN.md", repo_root=Path("/repo"), run=run)
    assert ts == "2026-05-24T11:15:20Z"


def test_resolve_returns_none_when_git_finds_nothing() -> None:
    run = _fake_run({})  # no marker resolves
    ts = resolve_completed_at("T-000999", "PLAN.md", repo_root=Path("/repo"), run=run)
    assert ts is None


def test_backfill_plan_stamps_resolved_and_leaves_unresolved_null() -> None:
    plan = parse_plan(_PLAN)
    run = _fake_run({"[x] T-000002:": 1779621320})  # only T-000002 resolves
    new_plan, backfilled, left_null = backfill_plan(
        plan, "PLAN.md", repo_root=Path("/repo"), run=run
    )
    # T-000002 resolves (backfilled); T-000001 is DONE-without-completed_at
    # but git resolves nothing for it (left null); T-000003 is TODO (ignored).
    assert (backfilled, left_null) == (1, 1)
    tasks = {t.task_id: t for t in new_plan.phases[0].tasks}
    # Resolved DONE task gets the flip timestamp.
    assert tasks["T-000002"].completed_at == "2026-05-24T11:15:20Z"
    # Unresolved DONE task is left null rather than guessed.
    assert tasks["T-000001"].completed_at is None
    # The TODO task is never stamped.
    assert tasks["T-000003"].completed_at is None


def test_backfill_plan_counts_unresolvable_done_tasks() -> None:
    plan = parse_plan(_PLAN)
    run = _fake_run({})  # nothing resolves
    _new_plan, backfilled, left_null = backfill_plan(
        plan, "PLAN.md", repo_root=Path("/repo"), run=run
    )
    # T-000001 and T-000002 are DONE-without-completed_at and unresolved.
    assert backfilled == 0
    assert left_null == 2


def test_backfill_file_rewrites_canonically(tmp_path: Path) -> None:
    repo_root = tmp_path
    path = repo_root / "PLAN.md"
    path.write_text(_PLAN, encoding="utf-8")
    run = _fake_run({"[x] T-000001:": 1779621320, "[x] T-000002:": 1779621320})
    backfilled, left_null = backfill_file(path, repo_root=repo_root, run=run)
    assert (backfilled, left_null) == (2, 0)
    reparsed = parse_plan(path.read_text(encoding="utf-8"))
    tasks = {t.task_id: t for t in reparsed.phases[0].tasks}
    assert tasks["T-000001"].completed_at == "2026-05-24T11:15:20Z"
    assert tasks["T-000002"].completed_at == "2026-05-24T11:15:20Z"
    # created_at survives the rewrite alongside the new completed_at.
    assert tasks["T-000001"].created_at == "2026-05-01T00:00:00Z"
