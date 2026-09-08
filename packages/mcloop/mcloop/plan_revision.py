"""Prepare and explicitly apply plan revisions at a stopped project checkpoint."""

from __future__ import annotations

import difflib
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from bob_tools.json_state import StateError, atomic_write_json
from bob_tools.planfile import load, parse_plan, render_plan, save, update, validate_plan
from bob_tools.planfile.milestones import phase_tasks, validate_milestones
from bob_tools.planfile.model import PlanValidationError, TaskStatus

from mcloop.completion import pending_receipts, project_owner
from mcloop.review_resume import _tree_digest
from mcloop.task_review import _git, _safe_path


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def enforce_milestones(path: Path) -> None:
    if not path.exists():
        return
    try:
        validate_milestones(load(path))
    except PlanValidationError as exc:
        raise StateError(f"Milestone contract: {exc}") from exc
    _require_reconciled_revision(path.parent)


def _require_reconciled_revision(root: Path) -> None:
    for receipt in (root / ".mcloop/plan-revisions").glob("*/receipt.json"):
        try:
            status = json.loads(receipt.read_text())["status"]
        except (ValueError, KeyError, TypeError) as exc:
            raise StateError(f"Invalid plan revision receipt: {receipt}") from exc
        if status == "applying":
            raise StateError(
                f"Interrupted plan revision. Run mcloop revise-plan --apply {receipt}"
            )


def _snapshot(root: Path) -> dict:
    state = {}
    for name in (
        "BUGS.md",
        "NOTES.md",
        ".mcloop/config.json",
        ".orchestra/config.json",
        ".mcloop/interrupted.json",
        ".mcloop/task-baseline",
        ".mcloop/task-evidence.json",
    ):
        path = _safe_path(root, name)
        state[name] = _hash(path.read_bytes()) if path.exists() else None
    for directory in (".duplo/ledger", ".mcloop/completions", ".mcloop/review-resume"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                state[name] = _hash(_safe_path(root, name).read_bytes())
    return {
        "head": _git(root, "rev-parse", "HEAD").strip(),
        "tree": _tree_digest(root),
        "state": state,
    }


def _tasks(plan):
    result = {t.task_id: (p.phase_id, t) for p in plan.phases for t in phase_tasks(p)}
    if plan.bugs:
        if plan.bugs.tasks:
            raise StateError("Keep bug queues separate before revising a phased plan")
    return result


def _semantic(value):
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items() if k != "line_number"}
    if isinstance(value, (tuple, list)):
        return [_semantic(v) for v in value]
    return value


def validate_revision(before, after) -> None:
    validate_plan(before, constructed=True, require_acceptance=False)
    validate_plan(after, constructed=True, require_acceptance=False)
    validate_milestones(after, required=True)
    old_phases = [p.phase_id for p in before.phases]
    if [p.phase_id for p in after.phases if p.phase_id in old_phases] != old_phases:
        raise StateError("Revision must preserve existing phase identities and order")
    old, new = _tasks(before), _tasks(after)
    if None in old or None in new or not old.keys() <= new.keys():
        raise StateError("Revision must preserve every existing task ID")
    for identity, (phase, task) in old.items():
        new_phase, proposed = new[identity]
        if phase != new_phase:
            raise StateError(
                f"{identity}: cross-phase moves require ledger-aware Duplo reauthoring"
            )
        if task.status != proposed.status:
            raise StateError(f"{identity}: revision cannot change task status")
        if task.status is not TaskStatus.TODO and _semantic(asdict(task)) != _semantic(
            asdict(proposed)
        ):
            raise StateError(f"{identity}: revision must preserve completed or failed work")
    if any(t.status is not TaskStatus.TODO for key, (_, t) in new.items() if key not in old):
        raise StateError("New tasks must be pending")


def prepare_revision(plan_path: Path, candidate: Path) -> Path:
    root = plan_path.parent
    with project_owner(root):
        if pending_receipts(root):
            raise StateError("Resolve outstanding completions with mcloop recover before revising")
        return prepare_revision_owned(plan_path, candidate)


def prepare_revision_owned(
    plan_path: Path, candidate: Path, *, revision_id: str | None = None
) -> Path:
    """Stage a candidate while the caller holds McLoop project ownership."""
    root = plan_path.parent
    _require_reconciled_revision(root)
    before, after = load(plan_path), load(candidate)
    validate_revision(before, after)
    snapshot = _snapshot(root)
    original = plan_path.read_text()
    revision_id = revision_id or uuid.uuid4().hex
    if uuid.UUID(revision_id).hex != revision_id:
        raise StateError("Invalid plan revision ID")
    directory = _safe_path(root, ".mcloop/plan-revisions/" + revision_id)
    receipt = directory / "receipt.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text())
        proposed = render_plan(after)
        if (
            existing.get("status") != "prepared"
            or existing.get("original") != original
            or existing.get("snapshot") != snapshot
            or existing.get("candidate_sha256") != _hash(proposed.encode())
            or (directory / "PLAN.md").read_text() != proposed
        ):
            raise StateError("Existing revision differs from the candidate or checkpoint")
        return receipt
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / "PLAN.md", after)
    proposed = (directory / "PLAN.md").read_text()
    report = "".join(
        difflib.unified_diff(
            original.splitlines(True),
            proposed.splitlines(True),
            fromfile="Current PLAN.md",
            tofile="Proposed PLAN.md",
        )
    )
    (directory / "changes.diff").write_text(report)
    old, new = _tasks(before), _tasks(after)
    added = [key for key in new if key not in old]
    (directory / "REVIEW.md").write_text(
        "# Proposed plan revision\n\n"
        "PLAN.md has not changed. Review PLAN.md and changes.diff in this directory.\n"
        "Check that each milestone exercises the assembled program "
        "and names its simulations.\n"
        "Existing task IDs, statuses and phase ownership are preserved.\n\n"
        f"Added tasks: {', '.join(added)}.\n\n"
        f"Apply only after review: `mcloop revise-plan --apply {directory / 'receipt.json'}`\n"
    )
    receipt = directory / "receipt.json"
    atomic_write_json(
        receipt,
        {
            "schema_version": 1,
            "status": "prepared",
            "snapshot": snapshot,
            "original": original,
            "candidate_sha256": _hash(proposed.encode()),
        },
    )
    return receipt


def apply_revision(plan_path: Path, receipt_path: Path) -> None:
    root = plan_path.parent
    receipt_path = receipt_path.resolve()
    directory = (root / ".mcloop/plan-revisions").resolve()
    if not receipt_path.is_relative_to(directory) or receipt_path.name != "receipt.json":
        raise StateError("Apply a receipt from this project's .mcloop/plan-revisions directory")
    with project_owner(root):
        if pending_receipts(root):
            raise StateError("Resolve outstanding completions with mcloop recover before revising")
        record = json.loads(receipt_path.read_text())
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("status")
            not in {
                "prepared",
                "applying",
                "applied",
            }
        ):
            raise StateError("Invalid plan revision receipt")
        if not all(
            isinstance(record.get(key), kind)
            for key, kind in (("candidate_sha256", str), ("snapshot", dict), ("original", str))
        ):
            raise StateError("Incomplete plan revision receipt")
        proposed = _safe_path(
            root, str((receipt_path.parent / "PLAN.md").relative_to(root))
        ).read_text()
        if _hash(proposed.encode()) != record["candidate_sha256"]:
            raise StateError("Candidate changed after preparation; prepare a new revision")
        if _snapshot(root) != record["snapshot"]:
            raise StateError("Project changed after preparation; prepare a new revision")
        original = record["original"]
        after = parse_plan(proposed)
        validate_revision(parse_plan(original), after)
        current = plan_path.read_text()
        if current == proposed and record["status"] in {"applying", "applied"}:
            record["status"] = "applied"
            atomic_write_json(receipt_path, record)
            return
        if current != original or record["status"] == "applied":
            raise StateError("PLAN.md changed after preparation; prepare a new revision")
        record["status"] = "applying"
        atomic_write_json(receipt_path, record)

        def replace(current_plan):
            if render_plan(current_plan) != render_plan(parse_plan(original)):
                raise StateError("PLAN.md changed during application")
            return after

        update(plan_path, replace)
        record["status"] = "applied"
        atomic_write_json(receipt_path, record)


def revision_command(plan_path: Path, candidate: Path | None, receipt: Path | None) -> None:
    try:
        if candidate is not None:
            result = prepare_revision(plan_path, candidate)
            print(f"Proposed revision: {result.parent / 'REVIEW.md'}\nPLAN.md is unchanged.")
        else:
            assert receipt is not None
            apply_revision(plan_path, receipt)
            print("Plan revision applied. Review records are retained; run mcloop to continue.")
    except (OSError, ValueError, PlanValidationError) as exc:
        raise StateError(str(exc)) from exc
