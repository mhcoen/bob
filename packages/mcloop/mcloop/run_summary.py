"""Run summary: schema definition and file writing for .mcloop/runs/."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TaskEntry:
    """Per-task entry in the run summary.

    The four parity fields (``success``, ``exit_code``, ``log_path``,
    ``changed_files``) match the ``CodeEditResult`` shape produced by
    ``invoke_code_edit`` so the orchestra integration smoke test can
    compare backends by reading this entry directly.

    ``task_id`` is the canonical ``T-NNNNNN`` identifier (R4 = Option B).
    Stored as a separate field rather than fused into ``text`` so
    downstream readers can use the structured id directly. Empty
    string for legacy / id-less rows.
    """

    label: str
    text: str
    outcome: str  # "success", "failed", "skipped"
    elapsed: float  # seconds
    model: str = ""
    attempts: int = 1
    commit_hash: str = ""
    success: bool = False
    exit_code: int = 0
    log_path: str = ""
    changed_files: list[str] = field(default_factory=list)
    task_id: str = ""


@dataclass
class CheckEntry:
    """Per-check entry in the run summary."""

    command: str
    passed: bool
    elapsed: float  # seconds


@dataclass
class RunSummary:
    """Complete run summary schema."""

    run_start: str  # ISO 8601
    run_end: str  # ISO 8601
    elapsed_seconds: float
    mode: str  # "plan", "bug-only", "maintain"
    tasks: list[TaskEntry] = field(default_factory=list)
    checks: list[CheckEntry] = field(default_factory=list)
    full_suite_passed: bool | None = None
    build_passed: bool | None = None
    audit_result: str | None = None  # "no_bugs", "fixed", "failed", "skipped", or None
    terminal_status: str = ""  # "success", "failure", "interrupted", "stopped"
    failure_detail: str = ""
    stop_reason: str = ""  # set when terminal_status == "stopped"
    stuck: list[str] = field(default_factory=list)
    commit_hashes: list[str] = field(default_factory=list)


def _iso_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def write_run_summary(project_dir: Path, summary: RunSummary) -> Path:
    """Write the run summary to .mcloop/runs/ and update latest.json.

    Returns the path to the dated summary file.
    """
    runs_dir = project_dir / ".mcloop" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Build filename from run_start timestamp
    try:
        dt = datetime.fromisoformat(summary.run_start)
        stamp = dt.strftime("%Y%m%d_%H%M%S")
    except (ValueError, TypeError):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    dated_path = runs_dir / f"{stamp}_run-summary.json"
    latest_path = runs_dir / "latest.json"

    data = asdict(summary)
    content = json.dumps(data, indent=2) + "\n"

    dated_path.write_text(content)
    shutil.copy2(dated_path, latest_path)

    return dated_path
