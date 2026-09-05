"""Schema and persistence boundaries for Duplo project state and checkpoints."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from bob_tools.json_state import (
    StateConflictError,
    StateError,
    edit_json_object,
    read_json_object,
)


def _invalid(path: Path, detail: str) -> StateError:
    return StateError(
        f"{path}: {detail}; file preserved. Stop writers, keep a copy, "
        "then repair or restore a known-good backup."
    )


def _version(data: dict, path: Path) -> None:
    version = data.get("schema_version", 0)
    if type(version) is not int or version not in (0, 1):
        raise _invalid(path, f"unsupported schema_version {version!r}")


def validate_state(data: dict, path: Path) -> None:
    """Accept legacy partial objects, checking the shapes consumers depend on.

    Unknown fields are retained. This validates persisted structure, not the
    truth of feature completion or the quality of generated requirements.
    """
    _version(data, path)
    for key in ("source_url", "app_name", "architecture_hash"):
        if key in data and not isinstance(data[key], str):
            raise _invalid(path, f"{key} must be a string")
    for key in ("current_phase", "last_scrape_timestamp"):
        if key in data and (type(data[key]) not in (int, float) or data[key] < 0):
            raise _invalid(path, f"{key} must be a non-negative number")
    for key in ("design_requirements", "doc_structures"):
        if key in data and not isinstance(data[key], dict):
            raise _invalid(path, f"{key} must be an object")
    arrays = (
        "features",
        "roadmap",
        "phases",
        "issues",
        "feedback",
        "code_examples",
        "sources",
        "reference_urls",
        "frame_descriptions",
    )
    for key in arrays:
        if key not in data:
            continue
        if not isinstance(data[key], list) or any(not isinstance(x, dict) for x in data[key]):
            raise _invalid(path, f"{key} must be a list of objects")
    for key, field in (("features", "name"), ("sources", "url")):
        for entry in data.get(key, []):
            if not isinstance(entry.get(field), str):
                raise _invalid(path, f"{key} entries require a string {field}")
    if "preferences" in data:
        prefs = data["preferences"]
        if not isinstance(prefs, dict) and (
            not isinstance(prefs, list) or any(not isinstance(p, dict) for p in prefs)
        ):
            raise _invalid(path, "preferences must be an object or list of objects")


def read_state(path: Path) -> dict:
    """Read without migration; only missing state becomes an empty object."""
    data = read_json_object(path) or {}
    validate_state(data, path)
    return data


@contextmanager
def edit_state(path: Path, *, expected: dict | None = None) -> Iterator[dict]:
    """Serialize short updates, or reject an outdated expected snapshot."""
    with edit_json_object(
        path, validate=lambda d: validate_state(d, path), expected=expected
    ) as data:
        yield data
        data["schema_version"] = 1


def _manifest_entries(data: dict, path: Path) -> dict[str, str]:
    # Old manifests map arbitrary filenames to strings, even filenames such as
    # 'schema_version' or 'entries'. Do not mistake these for metadata.
    if all(isinstance(value, str) for value in data.values()):
        return dict(data)
    if "schema_version" not in data:
        raise _invalid(path, "manifest envelope requires schema_version")
    _version(data, path)
    entries = data.get("entries")
    if not isinstance(entries, dict) or any(not isinstance(v, str) for v in entries.values()):
        raise _invalid(path, "manifest entries must map filenames to hash strings")
    return dict(entries)


def read_manifest(path: Path) -> dict[str, str]:
    return _manifest_entries(read_json_object(path) or {}, path)


def _validate_manifest(data: dict, path: Path) -> None:
    _manifest_entries(data, path)


@contextmanager
def edit_manifest(
    path: Path,
    *,
    expected: dict[str, str] | None = None,
) -> Iterator[dict[str, str]]:
    """Version legacy manifests and serialize merges/replacements.

    Expected compares logical entries, so migration by another writer does not
    invalidate an otherwise unchanged checkpoint. None opts into last-write
    replacement semantics; pass the observed entries for work computed earlier.
    """
    with edit_json_object(path, validate=lambda d: _validate_manifest(d, path)) as data:
        entries = _manifest_entries(data, path)
        legacy = all(isinstance(value, str) for value in data.values())
        if expected is not None and entries != expected:
            raise StateConflictError(
                f"{path}: checkpoint changed; newer state preserved. Reload before retrying."
            )
        yield entries
        if legacy:
            data.clear()
        data.update(schema_version=1, entries=entries)
