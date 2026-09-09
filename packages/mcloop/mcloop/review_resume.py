"""Preserve successful editor work while evidence assembly or review is blocked."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from bob_tools.json_state import atomic_write_json

from mcloop.checks import CheckResult
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


def _policy_without_budget(policy: ReviewPolicy) -> str:
    configuration = []
    for path, _ in policy.configuration:
        value = json.loads(path.read_text()) if path.exists() else None
        if isinstance(value, dict) and isinstance(value.get("task_review"), dict):
            value["task_review"].pop("max_input_bytes", None)
        configuration.append((str(path), value))
    data = [
        policy.enabled,
        policy.model,
        policy.base_url,
        policy.api_key_env,
        policy.documents,
        configuration,
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
            "policy_without_budget": _policy_without_budget(policy),
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
        or (
            data.get("policy") != _policy_digest(policy)
            and data.get("policy_without_budget") != _policy_without_budget(policy)
        )
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


def checked_stage(
    root: Path,
    policy: ReviewPolicy,
    task: str,
    stage: str,
    operation: Callable[[], CheckResult],
) -> CheckResult:
    """Reuse completed checks only while their project and execution inputs match."""
    if not policy.enabled:
        return operation()
    identity = hashlib.sha256((task + "\0" + stage).encode()).hexdigest()
    path = _safe_path(root, f".mcloop/checkpoints/checks/{identity}.json")

    def fingerprint(command: str) -> str | None:
        # Hash the environment without recording credentials in the checkpoint.
        executables = {sys.executable}
        for token in shlex.split(command):
            resolved = shutil.which(str(root / token) if "/" in token else token)
            if resolved:
                executables.add(resolved)
        tools = []
        for executable in sorted(executables):
            binary = Path(executable)
            info = binary.stat()
            with binary.open("rb") as stream:
                content_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            tools.append(
                (str(binary.resolve()), info.st_mode, info.st_size, info.st_mtime_ns, content_hash)
            )
        names = set(
            _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
        )
        names.update(
            _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").split("\0")
        )
        tree = hashlib.sha256()
        for name in sorted(names - {""}):
            if name.startswith((".mcloop/", "logs/")):
                continue
            candidate = root / name
            tree.update(name.encode() + b"\0")
            if candidate.exists() or candidate.is_symlink():
                tree.update(str(candidate.lstat().st_mode).encode() + b"\0")
                if candidate.is_symlink():
                    tree.update(os.readlink(candidate).encode() + b"\0")
                if candidate.is_file():
                    with candidate.open("rb") as stream:
                        tree.update(hashlib.file_digest(stream, "sha256").digest())
                elif not (
                    candidate.is_dir() and candidate.resolve().is_relative_to(root.resolve())
                ):
                    # An external directory dependency has no bounded file manifest.
                    return None
            tree.update(b"\0")
        value = [
            tree.hexdigest(),
            _policy_without_budget(policy),
            tools,
            sorted(os.environ.items()),
        ]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def inputs(command: str) -> str | None:
        try:
            return fingerprint(command)
        except (OSError, ValueError):
            # A cache miss must still allow the actual check to run.
            return None

    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            data = None
        if (
            isinstance(data, dict)
            and data.get("schema_version") == 1
            and data.get("passed") is True
            and isinstance(data.get("command"), str)
            and isinstance(data.get("output"), str)
            and isinstance(data.get("inputs"), str)
            and data.get("inputs") == inputs(data["command"])
        ):
            print("\n>>> Reusing completed checks for unchanged task inputs.", flush=True)
            return CheckResult(True, data["output"], data["command"])
    result = operation()
    if isinstance(result, CheckResult) and result.passed:
        input_digest = inputs(result.command)
        if input_digest is None:
            return result
        atomic_write_json(
            path,
            {
                "schema_version": 1,
                "task": task,
                "stage": stage,
                "inputs": input_digest,
                "passed": True,
                "output": result.output,
                "command": result.command,
            },
        )
    return result
