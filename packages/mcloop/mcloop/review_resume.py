"""Preserve successful editor work while evidence assembly or review is blocked."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from bob_tools.json_state import atomic_write_json

from mcloop.runner import RunResult
from mcloop.task_review import ReviewPolicy, _git, _safe_path
from mcloop.timing import snapshot


def _path(root: Path, task: str) -> Path:
    identity = hashlib.sha256(task.encode()).hexdigest()
    return _safe_path(root, f".mcloop/review-resume/{identity}.json")


def _policy_digest(policy: ReviewPolicy) -> str:
    data = [
        policy.enabled,
        policy.model,
        policy.base_url,
        policy.api_key_env,
        policy.documents,
        [(str(p), digest) for p, digest in policy.configuration],
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _tree_digest(root: Path) -> str:
    names = set(
        _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(names - {""}):
        if name.startswith((".mcloop/", "logs/")) or name in {"PLAN.md", "BUGS.md", "NOTES.md"}:
            continue
        path = _safe_path(root, name)
        digest.update(name.encode() + b"\0")
        if path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as stream:
                while block := stream.read(65536):
                    digest.update(block)
        elif not path.exists():
            digest.update(b"deleted\0")
        else:
            raise ValueError(f"Cannot fingerprint review input: {name}")
        digest.update(b"\0")
    return digest.hexdigest()


def save(
    root: Path,
    policy: ReviewPolicy,
    task: str,
    baseline: str,
    model: str | None,
    result: RunResult,
) -> None:
    if not policy.enabled:
        return
    if not result.success or result.exit_code != 0:
        raise ValueError("Only successful editing can resume at review")
    atomic_write_json(
        _path(root, task),
        {
            "schema_version": 1,
            "timings": snapshot(),
            "task": task,
            "baseline": baseline,
            "policy": _policy_digest(policy),
            "tree": _tree_digest(root),
            "model": model,
            "log_path": str(result.log_path),
        },
    )


def load(root: Path, policy: ReviewPolicy, task: str) -> tuple[str, str | None, RunResult] | None:
    if not policy.enabled:
        return None
    path = _path(root, task)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Invalid review-resume checkpoint; preserve it for inspection")
    baseline = data.get("baseline", "")
    if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", baseline):
        raise ValueError("Invalid review-resume baseline")
    if (
        data.get("task") != task
        or data.get("policy") != _policy_digest(policy)
        or data.get("tree") != _tree_digest(root)
    ):
        print("\n>>> Saved review inputs changed; a new editor attempt is required.", flush=True)
        return None
    # The original baseline must still exist even if startup committed the work.
    _git(root, "cat-file", "-e", baseline + "^{commit}")
    model = data.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError("Invalid review-resume editor model")
    log = Path(data.get("log_path", ""))
    return (
        baseline,
        model,
        RunResult(True, "Resuming completed editing at requirement review.", 0, log),
    )


def clear(root: Path, task: str) -> None:
    _path(root, task).unlink(missing_ok=True)
