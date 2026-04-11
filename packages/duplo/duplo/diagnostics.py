"""Append-only diagnostics channel for non-fatal failures.

Every non-fatal failure site writes a JSON-lines record to
``.duplo/errors.jsonl`` so that silent data loss becomes observable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ERRORS_JSONL = ".duplo/errors.jsonl"

# Valid categories for diagnostics records.
CATEGORIES = frozenset({"fetch", "screenshot", "llm", "hash", "io"})


def record_failure(
    site: str,
    category: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
    errors_path: Path | str = ERRORS_JSONL,
) -> None:
    """Append a single non-fatal failure record to the JSONL log.

    Args:
        site: ``module:function`` string identifying the failure site.
        category: One of ``fetch``, ``screenshot``, ``llm``, ``hash``,
            or ``io``.
        message: Human-readable description of what went wrong.
        context: Optional dict with extra structured data.
        errors_path: Override for testing; defaults to
            ``.duplo/errors.jsonl``.
    """
    if category not in CATEGORIES:
        raise ValueError(f"Invalid category {category!r}; must be one of {sorted(CATEGORIES)}")

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site": site,
        "category": category,
        "message": message,
    }
    if context:
        entry["context"] = context

    path = Path(errors_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def count_failures(errors_path: Path | str = ERRORS_JSONL) -> int:
    """Return the number of records in the JSONL log."""
    path = Path(errors_path)
    if not path.exists():
        return 0
    count = 0
    with open(path) as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def print_summary(errors_path: Path | str = ERRORS_JSONL) -> None:
    """Print a one-line summary if any non-fatal failures were logged."""
    n = count_failures(errors_path)
    if n > 0:
        print(f"{n} non-fatal failure{'s' if n != 1 else ''} logged to {ERRORS_JSONL}")
