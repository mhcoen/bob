"""Read-only summary of a duplo run's LLM-call log.

A duplo process records one JSON-lines record per LLM call under
``.duplo/logs/<run_id>/calls.jsonl`` (see :mod:`duplo.call_log`). This
module reads such a run directory and prints a per-run report: each
``call_site`` in order with its model, path (``legacy``/``council``),
duration, and token counts (input / cache / output), followed by a run
total. It mirrors the aggregation done by hand on mcloop logs for quota
analysis.

Everything here is read-only over the JSONL; it never writes to the run
directory and never makes an LLM call.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from duplo.call_log import CALLS_FILENAME, LOGS_ROOT


class LogsError(Exception):
    """Raised when a requested run directory or log file cannot be read."""


@dataclasses.dataclass
class CallRow:
    """One call's worth of report fields, derived from a JSONL record."""

    call_site: str
    model: str
    path: str
    duration_seconds: float | None
    input_tokens: int
    cache_tokens: int
    output_tokens: int
    orchestra_run_id: str | None = None


@dataclasses.dataclass
class RunSummary:
    """A run's per-call rows plus aggregate totals."""

    run_id: str
    rows: list[CallRow]
    total_duration_seconds: float
    total_input_tokens: int
    total_cache_tokens: int
    total_output_tokens: int


def find_latest_run_id(target_dir: Path | str = ".") -> str | None:
    """Return the most recent run id under ``<target_dir>/.duplo/logs/``.

    Run ids are timestamp-prefixed and therefore lexically sortable by
    start time, so the lexical maximum is the latest run. Returns ``None``
    when the logs root does not exist or holds no run directories.
    """
    logs_root = Path(target_dir) / LOGS_ROOT
    if not logs_root.is_dir():
        return None
    run_ids = [p.name for p in logs_root.iterdir() if p.is_dir()]
    return max(run_ids) if run_ids else None


def resolve_calls_path(target_dir: Path | str = ".", run_id: str | None = None) -> Path:
    """Return the ``calls.jsonl`` path for a run, defaulting to the latest.

    When *run_id* is omitted the most recent run under the logs root is
    used. Raises :class:`LogsError` if no run can be found or the resolved
    ``calls.jsonl`` does not exist.
    """
    base = Path(target_dir)
    if run_id is None:
        run_id = find_latest_run_id(base)
        if run_id is None:
            raise LogsError(f"No runs found under {base / LOGS_ROOT}")
    calls_path = base / LOGS_ROOT / run_id / CALLS_FILENAME
    if not calls_path.exists():
        raise LogsError(f"No log file at {calls_path}")
    return calls_path


def load_records(calls_path: Path | str) -> list[dict]:
    """Read and parse every JSON-lines record from *calls_path*.

    Blank lines are skipped; malformed lines are skipped rather than
    aborting the whole report so a partially written log still summarizes.
    """
    records: list[dict] = []
    with open(calls_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def _row_from_record(record: dict) -> CallRow:
    """Project one JSONL record onto the report's columns.

    Legacy records carry per-call token usage; council pointer records do
    not (the underlying calls live in the referenced orchestra run), so
    their token and duration columns are zero/``None``.
    """
    usage = record.get("usage") or {}
    cache = int(usage.get("cache_creation_input_tokens", 0)) + int(
        usage.get("cache_read_input_tokens", 0)
    )
    return CallRow(
        call_site=record.get("call_site", ""),
        model=record.get("model", ""),
        path=record.get("path", "legacy"),
        duration_seconds=record.get("duration_seconds"),
        input_tokens=int(usage.get("input_tokens", 0)),
        cache_tokens=cache,
        output_tokens=int(usage.get("output_tokens", 0)),
        orchestra_run_id=record.get("orchestra_run_id"),
    )


def summarize_run(run_id: str, records: list[dict]) -> RunSummary:
    """Aggregate records into a :class:`RunSummary` preserving call order."""
    rows = [_row_from_record(r) for r in records]
    return RunSummary(
        run_id=run_id,
        rows=rows,
        total_duration_seconds=sum(r.duration_seconds or 0.0 for r in rows),
        total_input_tokens=sum(r.input_tokens for r in rows),
        total_cache_tokens=sum(r.cache_tokens for r in rows),
        total_output_tokens=sum(r.output_tokens for r in rows),
    )


def _fmt_duration(seconds: float | None) -> str:
    """Format a wall-clock duration, or ``-`` when not recorded."""
    if seconds is None:
        return "-"
    return f"{seconds:.2f}s"


def _fmt_int(value: int) -> str:
    """Format a token count with thousands separators, or ``-`` for zero."""
    return f"{value:,}" if value else "-"


def format_report(summary: RunSummary) -> str:
    """Render a :class:`RunSummary` as a fixed-column text table.

    Columns: ``call_site``, ``model``, ``path``, ``duration``, ``input``,
    ``cache``, ``output``. Council rows append the referenced orchestra
    run id. A ``TOTAL`` line closes the table.
    """
    headers = ("call_site", "model", "path", "duration", "input", "cache", "output")

    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for r in summary.rows:
        site = r.call_site or "-"
        if r.path == "council" and r.orchestra_run_id:
            site = f"{site} -> {r.orchestra_run_id}"
        rows.append(
            (
                site,
                r.model or "-",
                r.path,
                _fmt_duration(r.duration_seconds),
                _fmt_int(r.input_tokens),
                _fmt_int(r.cache_tokens),
                _fmt_int(r.output_tokens),
            )
        )

    total_row = (
        "TOTAL",
        "",
        "",
        _fmt_duration(summary.total_duration_seconds),
        _fmt_int(summary.total_input_tokens),
        _fmt_int(summary.total_cache_tokens),
        _fmt_int(summary.total_output_tokens),
    )

    widths = [len(h) for h in headers]
    for row in [*rows, total_row]:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt_line(cells: tuple[str, ...]) -> str:
        # Left-align the text columns; right-align the numeric ones.
        parts = []
        for i, cell in enumerate(cells):
            if i < 3:
                parts.append(cell.ljust(widths[i]))
            else:
                parts.append(cell.rjust(widths[i]))
        return "  ".join(parts).rstrip()

    rule = "-" * (sum(widths) + 2 * (len(widths) - 1))
    lines = [
        f"Run {summary.run_id}  ({len(summary.rows)} calls)",
        "",
        _fmt_line(headers),
        _fmt_line(tuple("-" * w for w in widths)),
    ]
    lines.extend(_fmt_line(row) for row in rows)
    lines.append(rule)
    lines.append(_fmt_line(total_row))
    return "\n".join(lines)


def print_run_report(target_dir: Path | str = ".", run_id: str | None = None) -> None:
    """Resolve a run, summarize its log, and print the report to stdout.

    Raises :class:`LogsError` (with a user-facing message) when the run or
    its log file cannot be found.
    """
    calls_path = resolve_calls_path(target_dir, run_id)
    resolved_run_id = calls_path.parent.name
    records = load_records(calls_path)
    summary = summarize_run(resolved_run_id, records)
    print(format_report(summary))
