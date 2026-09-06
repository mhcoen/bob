"""Durable receipts for verified commit transitions; never replay side effects.

The bare loop and recovery acknowledgement share a nonblocking ownership lock.
Receipts are evidence of returned operations, not transactions or attestations
that today's worktree still matches the verified candidate.
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from bob_tools.json_state import StateError, atomic_write_json, edit_json_object, read_json_object

from mcloop.git_ops import run_git_bounded

_STAGES = ("verified", "commit_returned", "plan_updated", "settled", "acknowledged")
_EXECUTION: ContextVar[tuple[Path, Path] | None] = ContextVar("mcloop_execution", default=None)


class RecoveryRequired(StateError):
    """Completion evidence requires operator reconciliation before more work."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory(project_dir: Path) -> Path:
    path = project_dir / ".mcloop" / "completions"
    if path.is_symlink() and not path.exists():
        raise StateError(f"Dangling completion directory: {path}; restore its target.")
    if path.exists() and not path.is_dir():
        raise StateError(f"Completion directory is not a directory: {path}; preserve it.")
    return path


def _prepare_directory(project_dir: Path) -> Path:
    path = _directory(project_dir)
    path.mkdir(parents=True, exist_ok=True)
    # Existing projects need not ignore .mcloop yet. Receipts must not get
    # swept into the commit whose outcome they are recording.
    ignore = path / ".gitignore"
    if not ignore.exists():
        try:
            with ignore.open("x") as stream:
                stream.write("*\n")
        except FileExistsError:
            pass
    if ignore.read_text() != "*\n":
        raise StateError(
            f"Unexpected completion ignore rules at {ignore}; preserve and inspect them."
        )
    return path


@contextmanager
def project_owner(project_dir: Path) -> Iterator[None]:
    """Serialize cooperating bare loops and acknowledgement; never wait on one."""
    path = _prepare_directory(project_dir) / "owner.lock"
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryRequired("Another McLoop run or recovery owns this project.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def guarded_loop(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(checklist_path: Path, *args: Any, **kwargs: Any) -> Any:
        with project_owner(checklist_path.parent):
            require_reconciled(checklist_path.parent)
            token = _EXECUTION.set(None)
            try:
                result = function(checklist_path, *args, **kwargs)
                execution = _EXECUTION.get()
                if execution is not None:
                    with edit_json_object(execution[1], validate=_validate) as data:
                        data["stage"] = "returned"
                        data["observations"].append(
                            {
                                "stage": "returned",
                                "timestamp": _now(),
                                "outcome": str(getattr(result, "status", "returned")),
                                **_snapshot(execution[0]),
                            }
                        )
                return result
            finally:
                _EXECUTION.reset(token)

    return wrapped


def _validate(data: dict[str, Any]) -> None:
    if type(data.get("schema_version")) is int and data["schema_version"] == 2:
        _validate_execution(data)
        return
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise StateError("Unsupported completion receipt version; preserve the receipt.")
    for key in (
        "id",
        "stage",
        "created_at",
        "plan_path",
        "plan_before",
        "check_command",
        "check_output",
    ):
        if not isinstance(data.get(key), str):
            raise StateError(f"Invalid completion receipt {key}; preserve the receipt.")
    if (
        data["stage"] not in _STAGES
        or not isinstance(data.get("tasks"), list)
        or not data["tasks"]
    ):
        raise StateError("Invalid completion receipt stage/tasks; preserve the receipt.")
    for task in data["tasks"]:
        if not isinstance(task, dict) or any(
            not isinstance(task.get(k), str) for k in ("id", "text")
        ):
            raise StateError("Invalid completion task identity; preserve the receipt.")
    if not isinstance(data.get("observations"), list):
        raise StateError("Invalid completion observations; preserve the receipt.")
    observations = data["observations"]
    if (
        not observations
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("timestamp"), str)
            or item.get("stage") != _STAGES[i]
            for i, item in enumerate(observations)
            if i < len(_STAGES)
        )
        or len(observations) > 4
    ):
        raise StateError("Invalid completion observation sequence; preserve the receipt.")
    if data["stage"] == "acknowledged":
        resolution = data.get("resolution")
        if (
            len(observations) > 3
            or not isinstance(resolution, dict)
            or not isinstance(resolution.get("reason"), str)
            or not resolution["reason"].strip()
        ):
            raise StateError("Missing completion resolution; preserve the receipt.")
    elif observations[-1]["stage"] != data["stage"]:
        raise StateError("Completion stage lacks matching observation; preserve the receipt.")


def pending_receipts(project_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    pending: list[tuple[Path, dict[str, Any]]] = []
    try:
        paths = list(_directory(project_dir).iterdir())
    except FileNotFoundError:
        return pending
    for path in sorted(p for p in paths if p.name.endswith(".json")):
        data = read_json_object(path)
        if data is None:
            raise StateError(f"Completion receipt disappeared: {path}")
        _validate(data)
        if data["id"] != path.stem:
            raise StateError(f"Completion identity does not match {path}; preserve the receipt.")
        if data["stage"] not in ("settled", "acknowledged", "returned"):
            pending.append((path, data))
    return pending


def require_reconciled(project_dir: Path, *, allow_active_run: bool = False) -> None:
    pending = pending_receipts(project_dir)
    execution = _EXECUTION.get()
    if allow_active_run and execution is not None and execution[0] == project_dir.resolve():
        pending = [(p, d) for p, d in pending if p.resolve() != execution[1]]
    if pending:
        raise RecoveryRequired(
            f"Unresolved completion {pending[0][1]['id']} at {pending[0][0]}. "
            "Run `mcloop recover` to inspect Git, plan, and ledger evidence before retrying."
        )


def _git(project_dir: Path, *args: str) -> dict[str, Any]:
    try:
        proc = run_git_bounded(["git", "--no-optional-locks", *args], project_dir)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


def _snapshot(project_dir: Path) -> dict[str, Any]:
    return {
        "head": _git(project_dir, "rev-parse", "HEAD"),
        "status": _git(project_dir, "status", "--porcelain=v1"),
    }


def _validate_execution(data: dict[str, Any]) -> None:
    for key in ("id", "stage", "created_at", "plan_path", "plan_before"):
        if not isinstance(data.get(key), str):
            raise StateError(f"Invalid execution receipt {key}; preserve the receipt.")
    if data["stage"] not in ("active", "returned", "acknowledged"):
        raise StateError("Invalid execution receipt stage; preserve the receipt.")
    observations = data.get("observations")
    if (
        not isinstance(observations, list)
        or not observations
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("stage"), str)
            or not isinstance(item.get("timestamp"), str)
            for item in observations
        )
    ):
        raise StateError("Invalid execution observations; preserve the receipt.")
    if observations[0]["stage"] != "started":
        raise StateError("Execution receipt lacks its starting observation.")
    if data["stage"] == "returned" and observations[-1]["stage"] != "returned":
        raise StateError("Execution receipt lacks its return observation.")
    if data["stage"] == "acknowledged":
        resolution = data.get("resolution")
        if (
            not isinstance(resolution, dict)
            or not isinstance(resolution.get("reason"), str)
            or not resolution["reason"].strip()
        ):
            raise StateError("Execution receipt lacks explicit reconciliation.")


def start_execution(plan_path: Path, *, allow_missing_plan: bool = False) -> None:
    """Publish before the bare loop's first mutation, after read-only preflight."""
    project_dir = plan_path.parent.resolve()
    if _EXECUTION.get() is not None:
        raise RecoveryRequired("An execution receipt is already active.")
    require_reconciled(project_dir)
    try:
        plan_before = plan_path.read_text()
    except FileNotFoundError:
        if not allow_missing_plan:
            raise
        plan_before = ""
    identity = uuid4().hex
    path = _prepare_directory(project_dir) / f"{identity}.json"
    data = {
        "schema_version": 2,
        "id": identity,
        "stage": "active",
        "created_at": _now(),
        "plan_path": str(plan_path.resolve()),
        "plan_before": plan_before,
        "observations": [{"stage": "started", "timestamp": _now(), **_snapshot(project_dir)}],
    }
    _validate(data)
    atomic_write_json(path, data)
    _EXECUTION.set((project_dir, path.resolve()))


def run_owned_command(
    plan_path: Path, command: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    @guarded_loop
    def invoke(path: Path) -> Any:
        start_execution(path, allow_missing_plan=True)
        execution_event(
            path.parent,
            "command_started",
            command=getattr(command, "__name__", type(command).__name__),
        )
        return command(*args, **kwargs)

    return invoke(plan_path)


def execution_event(project_dir: Path, stage: str, **evidence: Any) -> None:
    execution = _EXECUTION.get()
    if execution is None or execution[0] != project_dir.resolve():
        return
    with edit_json_object(execution[1], validate=_validate) as data:
        if data["stage"] != "active":
            raise RecoveryRequired("Execution receipt is no longer active.")
        data["observations"].append({"stage": stage, "timestamp": _now(), **evidence})


@contextmanager
def execution_operation(project_dir: Path, operation: str) -> Iterator[None]:
    execution = _EXECUTION.get()
    if execution is None or execution[0] != project_dir.resolve():
        yield
        return
    execution_event(project_dir, f"{operation}_started", **_snapshot(project_dir))
    yield
    execution_event(project_dir, f"{operation}_returned", **_snapshot(project_dir))


class Completion:
    def __init__(self, project_dir: Path, path: Path):
        self.project_dir = project_dir
        self.path = path

    @classmethod
    def begin(
        cls,
        project_dir: Path,
        plan_path: Path,
        tasks: list[Any],
        *,
        command: str,
        output: str,
        baseline: str = "",
        ledger_dir: str = "",
    ) -> Completion:
        # Called while the bare loop owns the project, after verification and
        # before invoking Git. Direct callers must hold project_owner too.
        require_reconciled(project_dir, allow_active_run=True)
        identity = uuid4().hex
        path = _prepare_directory(project_dir) / f"{identity}.json"
        data = {
            "schema_version": 1,
            "id": identity,
            "stage": "verified",
            "created_at": _now(),
            "plan_path": str(plan_path.resolve()),
            "plan_before": plan_path.read_text(),
            "tasks": [{"id": task.task_id or "", "text": task.text} for task in tasks],
            "check_command": command,
            "check_output": output,
            "baseline": baseline,
            "ledger_dir": ledger_dir,
            "observations": [{"stage": "verified", "timestamp": _now(), **_snapshot(project_dir)}],
        }
        _validate(data)
        atomic_write_json(path, data)
        return cls(project_dir, path)

    def record_error(self, detail: str) -> None:
        with edit_json_object(self.path, validate=_validate) as data:
            data["error"] = detail

    def advance(self, stage: str, **evidence: Any) -> None:
        with edit_json_object(self.path, validate=_validate) as data:
            if stage not in _STAGES or _STAGES.index(stage) != _STAGES.index(data["stage"]) + 1:
                raise StateError(f"Invalid completion transition: {data['stage']} -> {stage}")
            data["stage"] = stage
            data["observations"].append({"stage": stage, "timestamp": _now(), **evidence})


def recovery_report(project_dir: Path) -> dict[str, Any]:
    """Read-only evidence; a changed HEAD alone cannot prove task completion."""
    records = []
    for path, data in pending_receipts(project_dir):
        try:
            current_plan: Any = Path(data["plan_path"]).read_text()
        except (OSError, UnicodeError) as exc:
            current_plan = {"error": str(exc)}
        records.append({"receipt_path": str(path), "receipt": data, "current_plan": current_plan})
    return {"pending": records, "current_git": _snapshot(project_dir)}


def acknowledge(project_dir: Path, identity: str, reason: str) -> None:
    """Record operator reconciliation, without editing Git/plans/ledger or replay."""
    if not reason.strip():
        raise RecoveryRequired(
            "Acknowledgement requires a nonempty --reason describing reconciliation."
        )
    with project_owner(project_dir):
        matches = [(p, d) for p, d in pending_receipts(project_dir) if d["id"] == identity]
        if len(matches) != 1:
            raise RecoveryRequired(f"No pending completion matches {identity!r}.")
        path, original = matches[0]
        evidence = recovery_report(project_dir)
        with edit_json_object(path, validate=_validate, expected=original) as data:
            data["resolution"] = {
                "reason": reason.strip(),
                "timestamp": _now(),
                "evidence": evidence,
            }
            data["stage"] = "acknowledged"


def recovery_command(project_dir: Path, *, identity: str | None, reason: str | None) -> None:
    if identity is not None:
        acknowledge(project_dir, identity, reason or "")
        print(f"Recorded reconciliation for {identity}. No task or Git actions were replayed.")
    elif reason is not None:
        raise RecoveryRequired("--reason requires --acknowledge ID.")
    else:
        print(json.dumps(recovery_report(project_dir), indent=2))
