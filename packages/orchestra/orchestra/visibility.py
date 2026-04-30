"""Visibility index: invocation_id -> outcome status.

Slice A of ``design/orchestra-real-council-plan.md``.

Per-state invocations are uniquely keyed by
``invocation_id = "{run_id}::{state_name}::{attempt_seq}"``. Every
artifact version row produced by a state invocation carries that
invocation_id (see ``orchestra.store``). Visibility of a committed
``state_invocation`` row is gated on the producing invocation's
durable ``state_exit`` outcome:

- ``pending``: state has entered, no durable ``state_exit`` yet.
  Hidden.
- ``success``: durable ``state_exit`` with a success outcome.
  Visible.
- ``error``: durable ``state_exit`` with an error outcome. Hidden,
  to be purged by the post-fan-out cleanup pass.

The index is the single source of truth for visibility status across
the executor and the artifact store. The store reads it through the
``VisibilityIndexProtocol`` interface (defined in
``orchestra.store``); the executor writes to it on ``state_enter``
and ``state_exit``. Replay reconstructs it from the log.

Persistence is a small JSON file in the run dir. Persistence is
informational and recovery-supporting: replay rebuilds the index
from the log (the log is the source of truth), and the persisted
file is a fast-path so a fresh process can start with the right
visibility state without having to re-parse the entire log.

Thread-safety: every public method is guarded by a single
``threading.Lock``. The lock is short-held for index lookups and
mutations only; it does not interact with the LogWriter or the
ArtifactStore lock.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

VisibilityStatus = Literal["pending", "success", "error"]


def make_invocation_id(run_id: str, state_name: str, attempt_seq: int) -> str:
    """Mint the canonical ``invocation_id`` string used everywhere a
    per-state invocation needs to be referenced.

    The composition (``run_id::state_name::attempt_seq``) makes the
    parts recoverable from the string alone for diagnostic use.
    Callers that need to parse an invocation_id back into its parts
    can use ``parse_invocation_id``.
    """
    if "::" in run_id:
        raise ValueError(f"run_id may not contain '::', got {run_id!r}")
    if "::" in state_name:
        raise ValueError(f"state_name may not contain '::', got {state_name!r}")
    if attempt_seq < 1:
        raise ValueError(f"attempt_seq must be >= 1, got {attempt_seq!r}")
    return f"{run_id}::{state_name}::{attempt_seq}"


@dataclass(frozen=True)
class InvocationParts:
    run_id: str
    state_name: str
    attempt_seq: int


def parse_invocation_id(invocation_id: str) -> InvocationParts:
    """Inverse of ``make_invocation_id``."""
    parts = invocation_id.split("::")
    if len(parts) != 3:
        raise ValueError(
            f"invocation_id {invocation_id!r} is not in run_id::state::seq form"
        )
    run_id, state_name, attempt_seq = parts
    return InvocationParts(
        run_id=run_id,
        state_name=state_name,
        attempt_seq=int(attempt_seq),
    )


class VisibilityIndex:
    """Thread-safe ``invocation_id -> VisibilityStatus`` map.

    Persisted as a JSON object so a fresh process can start with the
    last-known status without re-parsing the entire log. Replay still
    rebuilds the in-memory state from the log on startup and may
    overwrite the persisted snapshot.
    """

    def __init__(
        self,
        *,
        persist_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._statuses: dict[str, VisibilityStatus] = {}
        self._persist_path = persist_path
        self._load()

    # ----- protocol surface (consumed by the artifact store) -------

    def status(self, invocation_id: str) -> VisibilityStatus | None:
        """Return the recorded status, or None if unknown.

        ``None`` is treated by the store the same as ``pending``: not
        yet visible. The contract is that the executor inserts a
        ``pending`` entry on ``state_enter`` before any artifact
        commit by that invocation; if the store ever sees a row whose
        invocation is unknown, something is structurally wrong and
        hiding it is the safe default.
        """
        with self._lock:
            return self._statuses.get(invocation_id)

    # ----- mutation (executor + replay) ----------------------------

    def insert_pending(self, invocation_id: str) -> None:
        """Record a fresh state_enter. Subsequent state_exit upgrades
        the status to success or error."""
        with self._lock:
            self._statuses[invocation_id] = "pending"
            self._persist_unlocked()

    def mark_success(self, invocation_id: str) -> None:
        with self._lock:
            self._statuses[invocation_id] = "success"
            self._persist_unlocked()

    def mark_error(self, invocation_id: str) -> None:
        with self._lock:
            self._statuses[invocation_id] = "error"
            self._persist_unlocked()

    def replace_from(self, statuses: dict[str, VisibilityStatus]) -> None:
        """Replace the entire in-memory state. Used by replay after
        rebuilding from the log."""
        with self._lock:
            self._statuses = dict(statuses)
            self._persist_unlocked()

    def snapshot(self) -> dict[str, VisibilityStatus]:
        """Return a copy of the current state for diagnostics or
        replay-side rebuild."""
        with self._lock:
            return dict(self._statuses)

    # ----- persistence ---------------------------------------------

    def _persist_unlocked(self) -> None:
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: write to a sibling and rename. The index
        # is a small JSON object so this is cheap.
        tmp = self._persist_path.with_suffix(
            self._persist_path.suffix + ".tmp"
        )
        tmp.write_text(json.dumps(self._statuses, sort_keys=True), encoding="utf-8")
        tmp.replace(self._persist_path)

    def _load(self) -> None:
        if self._persist_path is None:
            return
        if not self._persist_path.is_file():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt persisted file is recoverable: replay rebuilds
            # the index from the log on startup.
            return
        if not isinstance(data, dict):
            return
        cleaned: dict[str, VisibilityStatus] = {}
        for k, v in data.items():
            if isinstance(k, str) and v in ("pending", "success", "error"):
                cleaned[k] = v
        self._statuses = cleaned


def rebuild_from_records(
    records: list[object],
) -> dict[str, VisibilityStatus]:
    """Rebuild a visibility-index snapshot from an iterable of log
    records (typically from ``LogReader.read_all()``).

    The pass walks the records in order and updates the inferred
    status for each invocation_id. ``state_enter`` inserts ``pending``;
    ``state_exit`` updates to ``success`` or ``error`` based on the
    record's ``status`` field (``"ok"`` is success, anything else is
    error). The executor writes both ``status`` and ``outcome`` into
    every ``state_exit`` record; the rebuild keys on ``status``
    because that field is the executor's own success classification
    and is invariant across actor backings (model/agent/shell/human).
    Records without an invocation_id field are ignored (legacy or
    non-state events).

    The result can be fed to ``VisibilityIndex.replace_from`` to
    initialise a fresh process's index from a log on disk.
    """
    statuses: dict[str, VisibilityStatus] = {}
    for record in records:
        event = getattr(record, "event", None)
        fields = getattr(record, "fields", {}) or {}
        invocation_id = fields.get("invocation_id")
        if not isinstance(invocation_id, str):
            continue
        if event == "state_enter":
            statuses[invocation_id] = "pending"
        elif event == "state_exit":
            status = fields.get("status")
            if status == "ok":
                statuses[invocation_id] = "success"
            else:
                # Some legacy or pre-Slice-A logs may carry only
                # ``outcome``. Treat ``complete``/``success`` as
                # success there for back-compat.
                outcome = fields.get("outcome")
                if status is None and outcome in ("complete", "success"):
                    statuses[invocation_id] = "success"
                else:
                    statuses[invocation_id] = "error"
    return statuses
