"""Reference calibration runner for the iterate_until_acceptable workflow.

Promoted from /tmp/orchestra-phase2/run_calibration_iterate.py during
Phase 2 closure (REPORT.md Addendum 6).

Usage::

    python -m orchestra.calibration.iterate_runner <scenario_dir>

Where ``scenario_dir`` is a directory containing:

  - ``task.md``: the user query (read into the workflow's ``query`` input).
  - ``history.md`` (optional): conversation history seed.
  - ``expected.txt``: classifier token (positive | negative | ambiguous)
    on the first nonempty line. Trailing prose comments allowed.
  - ``.orchestra/config.json``: role bindings.

Writes:

  - ``<scenario_dir>/logs/``: per-cycle artifact dumps, the
    workflow's log.jsonl, run_meta.json with provenance tags, and a
    progress.jsonl streaming the per-state-event timing.
  - ``<scenario_dir>/runs/<run_id>/``: orchestra's per-run dir
    (store.sqlite, log.jsonl, run_meta.json, prompt_sources/).
  - ``<scenario_dir>/summary.md``: a short human-readable trajectory
    summary.

Stale per-cycle artifacts in ``logs/`` from prior runs are cleaned
before populating new outputs (see ``clean_stale_versioned_artifacts``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

from orchestra.api import run_workflow
from orchestra.calibration._runner_common import (
    dump_versions,
    parse_verdict_decisions,
)
from orchestra.calibration.helpers import (
    clean_stale_versioned_artifacts,
    read_expected_classifier,
)
from orchestra.config import OrchestraConfig
from orchestra.progress import ProgressEvent

WORKFLOW = "iterate_until_acceptable"


def _make_callback(fh: IO[str]) -> Any:
    """Build a progress callback that writes to ``fh`` and stderr."""

    def callback(event: ProgressEvent) -> None:
        record: dict[str, Any] = {
            "kind": event.kind,
            "state_name": event.state_name,
            "role": event.role,
            "adapter": event.adapter,
            "model": event.model,
            "index": event.index,
            "total": event.total,
            "elapsed_seconds": event.elapsed_seconds,
        }
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        line = (
            f"[{event.kind}] {event.state_name} role={event.role} index={event.index}/{event.total}"
        )
        if event.elapsed_seconds is not None:
            line += f" elapsed={event.elapsed_seconds:.2f}s"
        sys.stderr.write(line + "\n")
        sys.stderr.flush()

    return callback


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns shell exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "scenario_dir",
        type=Path,
        help="Directory containing task.md, expected.txt, .orchestra/config.json",
    )
    parser.add_argument(
        "--scenario-id",
        default=None,
        help="Override the scenario id recorded in run_meta.json. "
        "Defaults to the scenario directory name.",
    )
    args = parser.parse_args(argv)

    sandbox: Path = args.scenario_dir
    scenario_id: str = args.scenario_id or sandbox.name
    if not sandbox.is_dir():
        sys.stderr.write(f"scenario directory not found: {sandbox}\n")
        return 2

    task_path = sandbox / "task.md"
    history_path = sandbox / "history.md"
    expected_path = sandbox / "expected.txt"
    cfg_path = sandbox / ".orchestra" / "config.json"

    query = task_path.read_text() if task_path.is_file() else ""
    history = history_path.read_text() if history_path.is_file() else ""
    expected = read_expected_classifier(expected_path)

    logs = sandbox / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    removed = clean_stale_versioned_artifacts(logs)
    if removed:
        sys.stderr.write(f"cleaned {removed} stale versioned artifact(s) from {logs}\n")

    progress_path = logs / "progress.jsonl"
    fh = progress_path.open("w", encoding="utf-8")
    callback = _make_callback(fh)

    cfg_raw = cast(Mapping[str, Any], json.loads(cfg_path.read_text()))
    cfg = OrchestraConfig.from_dict(dict(cfg_raw))

    try:
        result = run_workflow(
            WORKFLOW,
            inputs={"query": query, "history": history},
            config=cfg,
            project_dir=str(sandbox),
            data_root=str(sandbox / "runs"),
            progress_callback=callback,
            quiet=False,
        )
    finally:
        fh.close()

    sys.stderr.write(f"\nrun_id={result.run_id}\nterminal={result.terminal}\n")

    run_dir = result.log_path.parent
    shutil.copy(result.log_path, logs / "log.jsonl")
    shutil.copy(cfg_path, logs / "config.json")
    shutil.copy(task_path, logs / "task.md")
    if history_path.is_file():
        shutil.copy(history_path, logs / "history.md")

    store_path = run_dir / "store.sqlite"
    conn = sqlite3.connect(store_path)
    cur = conn.cursor()

    verdicts = dump_versions(cur, "judge_verdict", ".json", logs)
    for i, (_, value, _) in enumerate(verdicts, start=1):
        from orchestra.calibration._runner_common import to_text

        (logs / f"verdict_{i}.json").write_text(to_text(value))
    dump_versions(cur, "proposal", ".txt", logs)
    dump_versions(cur, "review_output", ".txt", logs)
    dump_versions(cur, "judge_decision", ".txt", logs)
    dump_versions(cur, "judge_feedback", ".txt", logs)

    conn.close()

    reviewer_actor = (
        cfg.roles["reviewer"].adapter,
        cfg.roles["reviewer"].model,
    )
    judge_actor = (
        cfg.roles["judge_role"].adapter,
        cfg.roles["judge_role"].model,
    )
    same_model_review_judge = reviewer_actor == judge_actor

    decisions, feedbacks, _ = parse_verdict_decisions(verdicts)

    meta: dict[str, Any] = {
        "run_id": result.run_id,
        "terminal": result.terminal,
        "workflow": WORKFLOW,
        "scenario_id": scenario_id,
        "config_path": str(cfg_path),
        "role_bindings": {
            role: {
                "adapter": cfg.roles[role].adapter,
                "model": cfg.roles[role].model,
            }
            for role in ("proposer", "reviewer", "judge_role")
        },
        "tags": {
            "f2_stress_test": True,
            "expected_stuck": expected,
            "same_model_review_judge": same_model_review_judge,
            "phase2_exploratory": True,
        },
        "decision_trajectory": decisions,
        "judge_feedback_strings": feedbacks,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.copy(run_dir / "run_meta.json", logs / "run_meta.json")

    summary_lines = [
        f"# Calibration scenario: {scenario_id}",
        "",
        f"Workflow:       {WORKFLOW}",
        f"Run id:         {result.run_id}",
        f"Terminal:       {result.terminal}",
        f"Expected stuck: {expected}",
        f"Iterations:     {len(verdicts)}",
        "",
        "## Decision trajectory",
        "```",
        json.dumps(decisions, indent=2),
        "```",
    ]
    (sandbox / "summary.md").write_text("\n".join(summary_lines) + "\n")

    sys.stderr.write(f"summary written to {sandbox / 'summary.md'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
