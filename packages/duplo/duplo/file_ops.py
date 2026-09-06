"""Preserve originals and journal multi-file publication; no automatic replay."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from bob_tools.json_state import StateError, atomic_write_json, edit_json_object, read_json_object

_OWNER: ContextVar[Path | None] = ContextVar("duplo_file_owner", default=None)


def _root(target_dir: Path | str) -> Path:
    return Path(target_dir).resolve() / ".duplo" / "operations"


def _validate(data: dict[str, Any]) -> None:
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise StateError("Unsupported Duplo file-operation version; preserve the receipt.")
    for key in ("id", "kind", "stage", "created_at"):
        if not isinstance(data.get(key), str):
            raise StateError(f"Invalid Duplo file-operation {key}; preserve the receipt.")
    if data["stage"] not in ("prepared", "done", "acknowledged") or not isinstance(
        data.get("entries"), list
    ):
        raise StateError("Invalid Duplo file-operation stage/entries; preserve the receipt.")
    if (
        data["kind"] not in ("replace_examples", "move_references", "copy_frames")
        or not data["entries"]
    ):
        raise StateError("Invalid Duplo operation kind or missing entries; preserve the receipt.")
    required: tuple[str, ...] = ("destination", "backup", "staged")
    if data["kind"] != "replace_examples":
        required += ("source", "source_backup", "source_hash")
    for entry in data["entries"]:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) for k in required):
            raise StateError("Invalid Duplo operation entry; preserve the receipt.")
        if data["kind"] == "replace_examples":
            if type(entry.get("previously_existed")) is not bool:
                raise StateError("Invalid example-directory history; preserve the receipt.")
        elif type(entry.get("move")) is not bool or not (
            entry.get("destination_hash") is None or isinstance(entry["destination_hash"], str)
        ):
            raise StateError("Invalid reference history; preserve the receipt.")
    if data["stage"] == "acknowledged":
        if not isinstance(data.get("reason"), str) or not data["reason"].strip():
            raise StateError("Missing Duplo file-operation reconciliation reason.")


def pending(target_dir: Path | str) -> list[tuple[Path, dict[str, Any]]]:
    root = _root(target_dir)
    if root.is_symlink() and not root.exists():
        raise StateError(f"Dangling operations directory: {root}")
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for path in sorted(p for p in entries if p.suffix == ".json"):
        data = read_json_object(path)
        if data is None:
            raise StateError(f"File-operation receipt disappeared: {path}")
        _validate(data)
        if data["id"] != path.stem:
            raise StateError(f"File-operation identity mismatch: {path}")
        if data["stage"] == "prepared":
            result.append((path, data))
    return result


def require_reconciled(target_dir: Path | str) -> None:
    records = pending(target_dir)
    if records:
        raise StateError(
            f"Unresolved Duplo file operation at {records[0][0]}. "
            "Stop writers and run `duplo recover` before retrying."
        )


@contextmanager
def project_owner(target_dir: Path | str, *, recovery: bool = False) -> Iterator[None]:
    project = Path(target_dir).resolve()
    if _OWNER.get() == project:
        if not recovery:
            require_reconciled(project)
        yield
        return
    root = _root(project)
    if root.is_symlink() and not root.exists():
        raise StateError(f"Dangling operations directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    if not ignore.exists():
        try:
            with ignore.open("x") as stream:
                stream.write("*\n")
        except FileExistsError:
            pass
    if ignore.read_text() != "*\n":
        raise StateError(f"Unexpected operation ignore rules: {ignore}; preserve them.")
    with (root / "owner.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError(
                "Another Duplo pipeline or file operation owns this project."
            ) from exc
        token = _OWNER.set(project)
        try:
            if not recovery:
                require_reconciled(project)
            yield
        finally:
            _OWNER.reset(token)
            fcntl.flock(stream, fcntl.LOCK_UN)


def owned_pipeline(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with project_owner(Path.cwd()):
            return function(*args, **kwargs)

    return wrapped


def _sync_directory(path: Path) -> None:
    import errno

    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise
    finally:
        os.close(fd)


def _copy(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise StateError(f"Recovery operations require regular files: {source}")
    shutil.copy2(source, destination)
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())


def _hash(path: Path) -> str | None:
    if path.is_symlink():
        raise StateError(f"Recovery operations refuse symlinks: {path}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _receipt(
    project: Path, identity: str, kind: str, entries: list[dict], **evidence: Any
) -> Path:
    path = _root(project) / f"{identity}.json"
    data = {
        "schema_version": 1,
        "id": identity,
        "kind": kind,
        "stage": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
        **evidence,
    }
    _validate(data)
    atomic_write_json(path, data)
    return path


def _finish(path: Path) -> None:
    with edit_json_object(path, validate=_validate) as data:
        if data["stage"] != "prepared":
            raise StateError("File operation no longer pending.")
        data["stage"] = "done"


def replace_examples(target_dir: Path | str, files: dict[str, dict]) -> Path:
    """Stage every new example before renaming the old directory into backup."""
    project = Path(target_dir).resolve()
    target = project / ".duplo" / "examples"
    with project_owner(project):
        identity = uuid4().hex
        archive = _root(project) / identity
        stage = archive / "new"
        backup = archive / "before"
        stage.mkdir(parents=True)
        if target.is_symlink():
            raise StateError(f"Example directory is a symlink: {target}")
        # Preserve operator-added non-JSON files. Directories/symlinks are
        # refused until their own recursive preservation contract is defined.
        if target.exists():
            for old in target.iterdir():
                if old.is_symlink() or not old.is_file():
                    raise StateError(f"Unsupported example entry: {old}; original retained.")
                if old.suffix != ".json":
                    _copy(old, stage / old.name)
        for name, data in files.items():
            if Path(name).name != name:
                raise StateError(f"Invalid example filename: {name}")
            atomic_write_json(stage / name, data)
        _sync_directory(stage)
        _sync_directory(archive)
        receipt = _receipt(
            project,
            identity,
            "replace_examples",
            [
                {
                    "destination": str(target),
                    "backup": str(backup),
                    "staged": str(stage),
                    "previously_existed": target.exists(),
                }
            ],
        )
        if target.exists():
            os.rename(target, backup)
            _sync_directory(archive)
            _sync_directory(target.parent)
        os.rename(stage, target)
        _sync_directory(target.parent)
        _sync_directory(archive)
        _finish(receipt)
    return target


def publish_references(
    target_dir: Path | str,
    sources: list[tuple[Path, str]],
    *,
    move: bool,
    after_publish: Callable[[], Any] | None = None,
) -> list[Path]:
    """Stage source copies and old destinations before publishing/deleting any."""
    project = Path(target_dir).resolve()
    target = project / ".duplo" / "references"
    with project_owner(project):
        if target.is_symlink():
            raise StateError(f"Reference directory is a symlink: {target}")
        target.mkdir(parents=True, exist_ok=True)
        identity = uuid4().hex
        archive = _root(project) / identity
        archive.mkdir()
        entries: list[dict[str, Any]] = []
        names: set[str] = set()
        for source, name in sources:
            source = source.absolute()
            if Path(name).name != name or name in ("", ".", "..") or name in names:
                raise StateError(f"Invalid or duplicate reference filename: {name}")
            names.add(name)
            if source.is_symlink():
                raise StateError(f"Reference source is a symlink: {source}")
            if not source.exists():
                continue
            destination = target / name
            if source == destination:
                continue
            index = len(entries)
            staged = archive / f"{index}.new"
            source_backup = archive / f"{index}.source"
            backup = archive / f"{index}.before"
            digest = _hash(source)
            _copy(source, source_backup)
            if _hash(source_backup) != digest:
                raise StateError(f"Source changed while staging: {source}; originals retained.")
            _copy(source_backup, staged)
            old_digest = _hash(destination)
            if old_digest is not None:
                _copy(destination, backup)
                if _hash(backup) != old_digest:
                    raise StateError(f"Destination changed while staging: {destination}")
            entries.append(
                {
                    "source": str(source),
                    "destination": str(destination),
                    "source_hash": digest,
                    "destination_hash": old_digest,
                    "source_backup": str(source_backup),
                    "backup": str(backup),
                    "staged": str(staged),
                    "move": move,
                }
            )
        if not entries:
            return []
        # Frame publication also updates duplo.json. Keep its exact prior bytes.
        state = project / ".duplo" / "duplo.json"
        state_before = None
        if after_publish is not None:
            state_before = {
                "path": str(state),
                "hash": _hash(state),
                "backup": str(archive / "duplo.before.json"),
            }
            if state.exists():
                _copy(state, archive / "duplo.before.json")
        _sync_directory(archive)
        receipt = _receipt(
            project,
            identity,
            "move_references" if move else "copy_frames",
            entries,
            state_before=state_before,
        )
        for entry in entries:
            source = Path(entry["source"])
            destination = Path(entry["destination"])
            if (
                _hash(destination) != entry["destination_hash"]
                or _hash(source) != entry["source_hash"]
            ):
                raise StateError("Reference changed before publication; inspect `duplo recover`.")
            os.replace(entry["staged"], destination)
            _sync_directory(target)
            _sync_directory(archive)
            if move:
                if _hash(source) != entry["source_hash"]:
                    raise StateError(f"Source changed before removal: {source}; preserved.")
                source.unlink()
                _sync_directory(source.parent)
        if after_publish is not None:
            after_publish()
        _finish(receipt)
        return [Path(entry["destination"]) for entry in entries]


def report(target_dir: Path | str) -> dict[str, Any]:
    records = []
    for path, data in pending(target_dir):
        paths = {
            entry[key]
            for entry in data["entries"]
            for key in ("destination", "backup", "staged", "source", "source_backup")
            if key in entry
        }
        current: dict[str, Any] = {}
        for name in sorted(paths):
            candidate = Path(name)
            try:
                if candidate.is_dir() and not candidate.is_symlink():
                    current[name] = {"files": sorted(p.name for p in candidate.iterdir())}
                else:
                    current[name] = {"sha256": _hash(candidate)}
            except (OSError, StateError) as exc:
                current[name] = {"error": str(exc)}
        records.append({"receipt_path": str(path), "receipt": data, "current_paths": current})
    return {"pending": records}


def recover(target_dir: Path | str, identity: str | None, reason: str | None) -> None:
    if identity is None:
        if reason is not None:
            raise StateError("--reason requires --acknowledge ID.")
        print(json.dumps(report(target_dir), indent=2))
        return
    if not reason or not reason.strip():
        raise StateError("Acknowledgement requires a nonempty reconciliation --reason.")
    with project_owner(target_dir, recovery=True):
        matches = [(p, d) for p, d in pending(target_dir) if d["id"] == identity]
        if len(matches) != 1:
            raise StateError(f"No pending Duplo file operation matches {identity!r}.")
        path, original = matches[0]
        evidence = report(target_dir)
        with edit_json_object(path, validate=_validate, expected=original) as data:
            data["stage"] = "acknowledged"
            data["reason"] = reason.strip()
            data["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            data["reconciliation_evidence"] = evidence
        print(
            f"Recorded reconciliation for {identity}; originals retained, no operations replayed."
        )
