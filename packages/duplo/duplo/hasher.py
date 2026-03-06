"""Compute and persist a SHA-256 hash manifest of project files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from duplo.saver import DUPLO_DIR

_SKIP_DIRS = {".duplo", ".git", "__pycache__", "node_modules", ".venv", "venv"}
_HASH_FILE = "file_hashes.json"
_BUF_SIZE = 65536


@dataclass
class HashDiff:
    """Changes detected between two hash manifests."""

    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_hashes(directory: Path | str = ".") -> dict[str, str]:
    """Walk *directory* and return ``{relative_path: sha256}`` for every file.

    Skips ``.duplo/``, ``.git/``, and other non-project directories
    (same set as ``scanner.py``).
    """
    root = Path(directory).resolve()
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # Skip files inside excluded directories.
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            hashes[str(rel)] = _hash_file(path)
        except OSError:
            continue
    return hashes


def load_hashes(directory: Path | str = ".") -> dict[str, str]:
    """Load the previously saved hash manifest from ``.duplo/file_hashes.json``.

    Returns an empty dict if the file does not exist.
    """
    path = Path(directory).resolve() / DUPLO_DIR / _HASH_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_hashes(
    hashes: dict[str, str],
    *,
    directory: Path | str = ".",
) -> Path:
    """Write *hashes* to ``.duplo/file_hashes.json``.

    Creates ``.duplo/`` if it does not exist.  Returns the path to the
    written file.
    """
    duplo_dir = Path(directory).resolve() / DUPLO_DIR
    duplo_dir.mkdir(parents=True, exist_ok=True)
    path = duplo_dir / _HASH_FILE
    path.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def diff_hashes(
    old: dict[str, str],
    new: dict[str, str],
) -> HashDiff:
    """Compare two hash manifests and return the differences."""
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and new[k] != old[k])
    return HashDiff(added=added, changed=changed, removed=removed)
