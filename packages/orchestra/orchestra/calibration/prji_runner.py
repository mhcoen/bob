"""Reference calibration runner for the propose_review_judge_implement workflow.

Promoted from /tmp/orchestra-phase2/run_calibration_prji.py during
Phase 2 closure (REPORT.md Addendum 6).

Usage::

    python -m orchestra.calibration.prji_runner <scenario_dir>

Where ``scenario_dir`` is a directory containing:

  - ``task.md``: the task description (read into the workflow's
    ``task`` input).
  - ``expected.txt``: classifier token (positive | negative |
    ambiguous) on the first nonempty line. Trailing prose comments
    allowed.
  - ``.orchestra/config.json``: role bindings (proposer, reviewer,
    judge_role, implementer).
  - The PRJI workflow expects ``scenario_dir`` to also be a
    project_dir, typically a git repo with code the implementer
    will modify.

In addition to the iterate-runner outputs, this runner registers a
``state_exit`` callback on the implementer state that captures the
git diff for that step into ``logs/implement_<n>.diff`` and
``logs/implement_<n>_stat.txt``, then commits the implementer's
changes (with --allow-empty) so the next state_exit captures only
the next implementer's delta. The scenario directory must be a git
repo for this capture to succeed; if it is not, the diffs will be
empty.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

from orchestra.api import run_workflow
from orchestra.calibration._runner_common import (
    dump_versions,
    parse_verdict_decisions,
    to_text,
)
from orchestra.calibration.helpers import (
    clean_stale_versioned_artifacts,
    read_expected_classifier,
)
from orchestra.config import OrchestraConfig
from orchestra.progress import ProgressEvent

WORKFLOW = "propose_review_judge_implement"


def _make_callback(
    fh: IO[str],
    sandbox: Path,
    logs: Path,
    scenario_id: str,
    implement_attempt: dict[str, int],
) -> Any:
    """Build the progress callback. Captures implementer diffs."""

    def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(sandbox), *args],
            capture_output=True,
            text=True,
            check=False,
        )

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

        if event.kind == "state_exit" and event.state_name == "implement":
            implement_attempt["counter"] += 1
            n = implement_attempt["counter"]
            _git(["add", "-A"])
            (logs / f"implement_{n}.diff").write_text(
                _git(["diff", "--cached", "--no-color"]).stdout
            )
            (logs / f"implement_{n}_stat.txt").write_text(
                _git(["diff", "--cached", "--stat", "--no-color"]).stdout
            )
            _git(
                [
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    f"calibration {scenario_id} implement step {n}",
                ]
            )
            sys.stderr.write(f"  captured implement_{n}.diff\n")
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
    expected_path = sandbox / "expected.txt"
    cfg_path = sandbox / ".orchestra" / "config.json"

    task = task_path.read_text()
    expected = read_expected_classifier(expected_path)

    logs = sandbox / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    removed = clean_stale_versioned_artifacts(logs)
    if removed:
        sys.stderr.write(f"cleaned {removed} stale versioned artifact(s) from {logs}\n")

    progress_path = logs / "progress.jsonl"
    fh = progress_path.open("w", encoding="utf-8")

    implement_attempt: dict[str, int] = {"counter": 0}
    callback = _make_callback(fh, sandbox, logs, scenario_id, implement_attempt)

    cfg_raw = cast(Mapping[str, Any], json.loads(cfg_path.read_text()))
    cfg = OrchestraConfig.from_dict(dict(cfg_raw))

    try:
        result = run_workflow(
            WORKFLOW,
            inputs={
                "task": task,
                "project_dir": str(sandbox),
                "history": "",
            },
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

    store_path = run_dir / "store.sqlite"
    conn = sqlite3.connect(store_path)
    cur = conn.cursor()

    verdicts = dump_versions(cur, "judge_verdict", ".json", logs)
    for i, (_, value, _) in enumerate(verdicts, start=1):
        (logs / f"verdict_{i}.json").write_text(to_text(value))
    dump_versions(cur, "framing", ".txt", logs)
    dump_versions(cur, "review_output", ".txt", logs)
    dump_versions(cur, "judge_decision", ".txt", logs)
    dump_versions(cur, "judge_feedback", ".txt", logs)
    dump_versions(cur, "fix_instructions", ".txt", logs)
    dump_versions(cur, "implementer_output", ".txt", logs)

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

    decisions, feedbacks, _fixes = parse_verdict_decisions(verdicts)

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
            for role in ("proposer", "reviewer", "judge_role", "implementer")
        },
        "tags": {
            "f2_stress_test": True,
            "expected_stuck": expected,
            "same_model_review_judge": same_model_review_judge,
            "phase2_exploratory": True,
        },
        "decision_trajectory": decisions,
        "judge_feedback_strings": feedbacks,
        "implement_step_count": implement_attempt["counter"],
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.copy(run_dir / "run_meta.json", logs / "run_meta.json")

    summary_lines = [
        f"# Calibration scenario: {scenario_id}",
        "",
        f"Workflow:        {WORKFLOW}",
        f"Run id:          {result.run_id}",
        f"Terminal:        {result.terminal}",
        f"Expected stuck:  {expected}",
        f"Judge calls:     {len(verdicts)}",
        f"Implement steps: {implement_attempt['counter']}",
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
