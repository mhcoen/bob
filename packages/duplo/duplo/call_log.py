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
        call_site: str = "",
        path: str = "legacy",
        response: str | None = None,
        error: str | None = None,
        outcome: str | None = None,
        attempt: int | None = None,
        duration_seconds: float | None = None,
        usage: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a single LLM-call record to ``calls.jsonl``.

        Creates the run directory on first use. ``response`` is set on
        success and ``error`` on failure; exactly one is normally present.
        ``call_site`` is a caller-supplied label identifying which
        phase/feature/step invoked the call; ``path`` is the generation
        path the call belongs to (``"legacy"`` for the single-actor
        ``query()`` route, ``"council"`` for the orchestra council route);
        ``outcome`` is one of ``"ok"``/``"timeout"``/``"error"``;
        ``attempt`` is the attempt number the record describes. ``usage``
        carries per-call token counts (``input_tokens``,
        ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
        ``output_tokens``) when they could be extracted, and is omitted
        otherwise. Prompts and responses are stored at full fidelity
        (never truncated).
        """
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "call_site": call_site,
            "path": path,
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "system": system,
        }
        if outcome is not None:
            record["outcome"] = outcome
        if attempt is not None:
            record["attempt"] = attempt
        if response is not None:
            record["response"] = response
        if error is not None:
            record["error"] = error
        if duration_seconds is not None:
            record["duration_seconds"] = round(duration_seconds, 3)
        if usage:
            record["usage"] = usage
        if extra:
            record["extra"] = extra

        self._append(record)

    def log_council_phase(
        self,
        *,
        call_site: str,
        orchestra_run_id: str,
        transcript_path: Path | str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a council-path reference record to ``calls.jsonl``.

        The council route fans a phase out to several actors through
        Orchestra, which already captures each per-actor LLM call at full
        fidelity inside its own run directory. Rather than duplicate that
        transcript, this records a *pointer*: one record per
        council-authored phase carrying the ``call_site`` (the same label
        the legacy ``query()`` route would use for the phase),
        ``path="council"``, the orchestra ``run_id``, and the
        ``transcript_path`` of the orchestra run log. A reader walking the
        duplo run directory thus has a complete index of every LLM call
        regardless of path: legacy calls inline, council phases by
        reference.

        Creates the run directory on first use, like :meth:`log_call`.
        """
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "call_site": call_site,
            "path": "council",
            "orchestra_run_id": orchestra_run_id,
        }
        if transcript_path is not None:
            record["transcript_path"] = str(transcript_path)
        if extra:
            record["extra"] = extra

        self._append(record)

    def _append(self, record: dict[str, Any]) -> None:
        """Create the run directory if needed and append one JSON line."""
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


def log_council_phase(**kwargs: Any) -> None:
    """Append one council-path reference record to the active run, if any.

    No-op when no run is active so library and test usage of the council
    path never writes to disk.
    """
    if _active is not None:
        _active.log_council_phase(**kwargs)
