"""JSONL log writer and reader.

The log is the source of truth for what happened during a run. The
writer appends one record per line and fsyncs after each write so a
crash leaves a complete, possibly-truncated log rather than a partial
last record. The reader handles a truncated last line by discarding it
on replay (per ``orchestra-runner.md`` open question 6).

Records are dictionaries with at minimum the common fields specified
in the runner spec's "Common record fields" section: ``ts``, ``run_id``,
``seq``, ``event``, ``state_id``, ``attempt``. Event-specific fields
sit alongside.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Record:
    """One log record. Common fields are first-class; everything else
    lives in ``fields``."""

    event: str
    run_id: str
    seq: int
    ts: str = field(default_factory=_now_iso)
    state_id: str | None = None
    attempt: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        body: dict[str, Any] = {
            "ts": self.ts,
            "run_id": self.run_id,
            "seq": self.seq,
            "event": self.event,
            "state_id": self.state_id,
            "attempt": self.attempt,
        }
        body.update(self.fields)
        return json.dumps(body, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        common = {"ts", "run_id", "seq", "event", "state_id", "attempt"}
        return cls(
            event=str(data["event"]),
            run_id=str(data["run_id"]),
            seq=int(data["seq"]),
            ts=str(data["ts"]),
            state_id=data.get("state_id"),
            attempt=data.get("attempt"),
            fields={k: v for k, v in data.items() if k not in common},
        )


class LogWriter:
    """Append-only JSONL writer with per-record fsync.

    The writer maintains a monotonic ``seq`` counter starting at 0 for
    the first record (which is conventionally ``run_start``).
    """

    def __init__(self, path: str | Path, run_id: str, *, start_seq: int = 0) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._seq = start_seq
        self._fh = open(self._path, "a", encoding="utf-8")

    def close(self) -> None:
        self._fh.close()

    @property
    def next_seq(self) -> int:
        return self._seq

    def write(
        self,
        event: str,
        *,
        state_id: str | None = None,
        attempt: int | None = None,
        fields: dict[str, Any] | None = None,
    ) -> Record:
        record = Record(
            event=event,
            run_id=self._run_id,
            seq=self._seq,
            state_id=state_id,
            attempt=attempt,
            fields=fields or {},
        )
        self._fh.write(record.to_json())
        self._fh.write("\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._seq += 1
        return record


class LogReader:
    """JSON Lines reader that tolerates a truncated last record.

    ``read_all`` returns every fully-formed record; a partial line at
    end-of-file is silently dropped. This matches the runner spec's
    crash-recovery contract: a crash mid-record leaves an incomplete
    last line, which resume must skip.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def read_all(self) -> list[Record]:
        records: list[Record] = []
        if not self._path.exists():
            return records
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # Truncated final line. Per the runner spec, drop it.
                    break
                records.append(Record.from_dict(data))
        return records

    def iter_records(self) -> Iterator[Record]:
        yield from self.read_all()
