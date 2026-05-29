"""Per-run structured logging of duplo's LLM calls.

A duplo process activates a run via :func:`start_run`, which fixes a
``run_id`` (UTC timestamp + short random suffix) and a durable, repo-internal
per-run log directory at ``.duplo/logs/<run_id>/`` in the target project.
Each LLM call appends one JSON-lines record to ``calls.jsonl`` in that
directory.

Until a run is started the module-level logger is inactive and
:func:`log_call` is a no-op, so importing duplo or exercising the CLI
wrappers in isolation (e.g. unit tests) never touches the filesystem.
The log directory itself is created lazily on the first appended record,
so an activated run that makes no LLM calls leaves no empty directory.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGS_ROOT = ".duplo/logs"
CALLS_FILENAME = "calls.jsonl"


def generate_run_id() -> str:
    """Return a sortable run id: UTC timestamp + short random suffix.

    The timestamp prefix makes ids lexically sortable by start time; the
    random suffix keeps two runs started in the same second distinct.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


class CallLog:
    """Owns one run's log directory and appends one record per LLM call."""

    def __init__(self, run_id: str, target_dir: Path | str = ".") -> None:
        self.run_id = run_id
        self.run_dir = Path(target_dir) / LOGS_ROOT / run_id
        self.calls_path = self.run_dir / CALLS_FILENAME

    def log_call(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        system: str = "",
        response: str | None = None,
        error: str | None = None,
        duration_s: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a single LLM-call record to ``calls.jsonl``.

        Creates the run directory on first use. ``response`` is set on
        success and ``error`` on failure; exactly one is normally present.
        """
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "provider": provider,
            "model": model,
            "ok": error is None,
            "prompt": prompt,
            "system": system,
        }
        if response is not None:
            record["response"] = response
        if error is not None:
            record["error"] = error
        if duration_s is not None:
            record["duration_s"] = round(duration_s, 3)
        if extra:
            record["extra"] = extra

        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.calls_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")


_active: CallLog | None = None


def start_run(target_dir: Path | str = ".", run_id: str | None = None) -> CallLog:
    """Activate the module-level logger for this process and return it.

    Called once at duplo process start. A fresh ``run_id`` is generated
    unless one is supplied (the override exists for tests).
    """
    global _active
    _active = CallLog(run_id or generate_run_id(), target_dir=target_dir)
    return _active


def current_run() -> CallLog | None:
    """Return the active logger, or ``None`` if no run has been started."""
    return _active


def log_call(**kwargs: Any) -> None:
    """Append one LLM-call record to the active run, if any.

    No-op when no run is active so library and test usage of the CLI
    wrappers never writes to disk.
    """
    if _active is not None:
        _active.log_call(**kwargs)
