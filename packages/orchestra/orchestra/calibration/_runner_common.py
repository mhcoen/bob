"""Internal shared logic for the iterate and prji calibration runners.

Both runners follow the same shape: read scenario inputs, prepare
logs/, register a progress callback that forwards to a JSONL file,
load OrchestraConfig, invoke run_workflow, then dump per-version
artifacts from the SQLite store and write run_meta.json.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def to_text(v: object) -> str:
    """Decode a SQLite versions.value blob/string to text."""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("utf-8", errors="replace")
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def dump_versions(
    cur: sqlite3.Cursor,
    artifact: str,
    suffix: str,
    logs: Path,
) -> list[tuple[str, Any, str]]:
    """Dump committed versions of ``artifact`` into ``logs/<artifact>_<n><suffix>``.

    Returns the rows pulled from the store, each as
    ``(version_id, value, written_by)``. Only non-tentative versions
    are dumped, in seq order.
    """
    cur.execute(
        """
        SELECT version_id, value, written_by FROM versions
        WHERE artifact = ? AND is_tentative = 0
        ORDER BY seq
        """,
        (artifact,),
    )
    rows: list[tuple[str, Any, str]] = list(cur.fetchall())
    for i, (_, value, _) in enumerate(rows, start=1):
        (logs / f"{artifact}_{i}{suffix}").write_text(to_text(value))
    return rows


def parse_verdict_decisions(
    verdicts: list[tuple[str, Any, str]],
) -> tuple[list[str], list[str], list[str]]:
    """Extract decision, feedback, fix_instructions per verdict row.

    Returns three parallel lists (decisions, feedbacks, fix_instructions).
    Best-effort parse: malformed verdicts contribute "?" / "" entries
    rather than failing the run.
    """
    decisions: list[str] = []
    feedbacks: list[str] = []
    fixes: list[str] = []
    for _, value, _ in verdicts:
        try:
            obj_raw: Any
            if isinstance(value, (str | bytes)):
                obj_raw = json.loads(value)
            else:
                obj_raw = value
            if isinstance(obj_raw, dict):
                decisions.append(str(obj_raw.get("decision", "?")))
                feedbacks.append(str(obj_raw.get("feedback") or ""))
                fixes.append(str(obj_raw.get("fix_instructions") or ""))
            else:
                decisions.append("?")
                feedbacks.append("")
                fixes.append("")
        except (json.JSONDecodeError, ValueError):
            decisions.append("?")
            feedbacks.append("")
            fixes.append("")
    return decisions, feedbacks, fixes
