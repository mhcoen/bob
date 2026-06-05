"""Append-only test-verification waiver records.

When the coverage-proven verification fallback cannot prove an unmapped
behavioral change is exercised -- a non-Python behavior input (which has
no executable coverage lines), a Python change with no scoped candidate
test, or a change no scoped test executes -- the gate fails closed unless
an *explicit* waiver exists for that changed input under the current
task. A waiver is keyed on the changed input plus the task identity so a
commit or checkpoint that advances the pre-edit baseline SHA mid-task
does not silently nullify a waiver the user already recorded; the exact
baseline SHA is still matched as a fallback for environments that run
without a task label.

Waivers are never written silently by the gate. They are recorded only
through a deliberate action (the ``mcloop waive`` subcommand or a direct
``record_waiver`` call) so that every bypass of the verification gate
leaves a durable, auditable trail. Each record carries the task label,
the changed input, the task's pre-edit baseline SHA, a human reason, and
a UTC timestamp, and is appended as one JSON object per line to
``.mcloop/test-verification-waivers.jsonl``.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

WAIVERS_REL = ".mcloop/test-verification-waivers.jsonl"

# The fields every waiver record must carry. Exposed so callers/tests can
# assert completeness without hard-coding the list.
REQUIRED_FIELDS: tuple[str, ...] = (
    "task_label",
    "changed_input",
    "baseline_sha",
    "reason",
    "timestamp",
)


def _waivers_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / WAIVERS_REL


def record_waiver(
    project_dir: str | Path,
    *,
    task_label: str,
    changed_input: str,
    baseline_sha: str,
    reason: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Append a waiver record and return it.

    *timestamp* defaults to the current UTC time in ISO-8601 form. The
    record is appended atomically as a single JSON line so concurrent
    appends never interleave a partial record. Returns the record dict
    so callers can log or surface exactly what was written.
    """
    ts = timestamp or datetime.datetime.now(datetime.UTC).isoformat()
    record = {
        "task_label": str(task_label),
        "changed_input": str(changed_input),
        "baseline_sha": str(baseline_sha),
        "reason": str(reason),
        "timestamp": ts,
    }
    path = _waivers_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load_waivers(project_dir: str | Path) -> list[dict]:
    """Return all recorded waivers, skipping unparseable lines.

    A missing file yields an empty list. Malformed lines (a truncated or
    corrupt append) are skipped rather than raising, so a single bad line
    cannot wedge the gate.
    """
    path = _waivers_path(project_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def has_waiver(
    project_dir: str | Path,
    changed_input: str,
    baseline_sha: str = "",
    *,
    task_label: str = "",
) -> bool:
    """Return True if an explicit waiver covers this input for this task.

    Task identity is the durable key: a waiver recorded for a task's work
    on *changed_input* is matched whenever the current *task_label*
    matches, so a commit or checkpoint that advances the pre-edit baseline
    SHA mid-task does not silently nullify a waiver the user already
    recorded. The exact pre-edit *baseline_sha* is matched as a fallback,
    which keeps waivers working in environments that leave the task label
    unset (e.g. native models with no ``MCLOOP_TASK_LABEL``).

    Matching is scoped: an empty *task_label* and an empty *baseline_sha*
    together never match, so the gate cannot treat a missing baseline and
    a missing task identity as a blanket waivable state.
    """
    for rec in load_waivers(project_dir):
        if rec.get("changed_input") != changed_input:
            continue
        if task_label and rec.get("task_label") == task_label:
            return True
        if baseline_sha and rec.get("baseline_sha") == baseline_sha:
            return True
    return False


__all__ = [
    "REQUIRED_FIELDS",
    "WAIVERS_REL",
    "has_waiver",
    "load_waivers",
    "record_waiver",
]
