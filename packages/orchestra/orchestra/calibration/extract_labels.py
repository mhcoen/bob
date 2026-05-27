"""Per-judge-call label extractor for calibration runs.

Walks one or more calibration scenario directories' ``logs/`` and
emits a JSONL of per-judge-call labels plus a Markdown matrix
summarizing per-scenario classifications. Promoted from the Phase 2
sandbox (REPORT.md Addendum 6).

Usage::

    python -m orchestra.calibration.extract_labels \\
        --scenario prji-stuck-pos /path/to/prji-stuck-pos:propose_review_judge_implement \\
        --scenario prji-ambig     /path/to/prji-ambig:propose_review_judge_implement \\
        --output-dir /path/to/analysis_output

Each ``--scenario`` argument is two arguments: scenario_id, then a
``<dir>:<workflow_name>`` colon-separated string identifying the
scenario directory and the workflow it ran. Output goes to
``<output_dir>/calibration_labels.jsonl`` and
``<output_dir>/calibration_matrix.md``.

Per-judge-call labels (one row per judge invocation across all
scenarios):

    scenario_id            string
    workflow               propose_review_judge_implement (or other configured workflow name)
    expected_stuck         positive | negative | ambiguous
    judge_call_index       1-based ordinal of the judge invocation
    prior_decision         decision string from verdict_<n-1>.json; empty on cycle 1
    current_decision       decision string from verdict_<n>.json
    material_issue_cited   first ~140 chars of feedback
    concrete_next_action_exists  bool (heuristic: feedback contains imperative
                                       phrasing or fix_instructions non-empty)
    outcome_changed_after_next_cycle  bool (cycle n+1's decision differs from
                                            cycle n's; null if no cycle n+1)
    feedback_full          full feedback string
    fix_instructions       full fix_instructions string (PRJI only)

Per-scenario summary in calibration_matrix.md:

    scenario_id  expected_stuck  judge_calls  decision_trajectory  observed_stuck  classification

Where classification is:
    match          observed behavior aligns with expected
    over-trigger   expected negative, but stuck fired
    under-trigger  expected positive, but stuck did not fire
    ambiguous-stuck / ambiguous-non-stuck  expected ambiguous; both ends are valid signal

The expected_stuck field is read from each scenario's expected.txt
via ``read_expected_classifier``, which enforces the strict
first-nonempty-line contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestra.calibration.helpers import (
    ExpectedClassifierError,
    read_expected_classifier,
)

_IMPERATIVE_HINTS: tuple[str, ...] = (
    "fix",
    "change",
    "add",
    "remove",
    "rewrite",
    "shorten",
    "lengthen",
    "replace",
    "update",
    "rerun",
    "re-run",
    "include",
    "drop",
    "delete",
    "use",
    "must",
)


@dataclass(frozen=True)
class ScenarioSpec:
    """Identifies one scenario by its id, directory, and workflow name."""

    scenario_id: str
    scenario_dir: Path
    workflow: str


def _has_imperative(text: str) -> bool:
    """Return True if ``text`` contains an imperative-flavored verb.

    Coarse heuristic for ``concrete_next_action_exists``. Inspectable
    by listing the hints; not a strict NLP judgment.
    """
    if not text:
        return False
    lo = text.lower()
    for hint in _IMPERATIVE_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", lo):
            return True
    return False


def _balanced_object_spans(raw: str) -> list[tuple[int, int]]:
    """Return all top-level ``{...}`` spans in ``raw`` as (start, end)."""
    depth = 0
    in_string = False
    i = 0
    n = len(raw)
    spans: list[tuple[int, int]] = []
    start = 0
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
        i += 1
    return spans


def parse_verdict(verdict_path: Path) -> dict[str, Any] | None:
    """Best-effort parse of a verdict_<n>.json file.

    Tolerant of wrapped-output shapes (e.g., the F1 unwrap target).
    Returns the parsed object on success, or None on failure.
    """
    if not verdict_path.is_file():
        return None
    raw = verdict_path.read_text()
    try:
        loaded: Any = json.loads(raw)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        for s, e in reversed(_balanced_object_spans(raw)):
            try:
                loaded = json.loads(raw[s:e])
                if isinstance(loaded, dict):
                    return loaded
            except json.JSONDecodeError:
                continue
        return None


def _read_expected_or_unknown(scenario_dir: Path) -> str:
    """Return the classifier or 'unknown' if expected.txt is missing/malformed."""
    p = scenario_dir / "expected.txt"
    try:
        return read_expected_classifier(p)
    except ExpectedClassifierError:
        return "unknown"


def _verdicts(logs_dir: Path) -> list[dict[str, Any]]:
    """Return parsed verdicts from ``logs_dir/verdict_<n>.json`` in order."""
    if not logs_dir.is_dir():
        return []
    pairs: list[tuple[int, dict[str, Any] | None]] = []
    for child in logs_dir.iterdir():
        m = re.fullmatch(r"verdict_(\d+)\.json", child.name)
        if not m:
            continue
        n = int(m.group(1))
        pairs.append((n, parse_verdict(child)))
    pairs.sort(key=lambda x: x[0])
    return [v for _, v in pairs if v is not None]


def classify(expected: str, decisions: list[str]) -> str:
    """Map (expected, decisions) to a classification label."""
    observed_stuck = "stuck" in decisions
    if expected == "positive":
        return "match" if observed_stuck else "under-trigger"
    if expected == "negative":
        return "over-trigger" if observed_stuck else "match"
    if expected == "ambiguous":
        return "ambiguous-stuck" if observed_stuck else "ambiguous-non-stuck"
    return "unknown"


def scenario_rows(
    spec: ScenarioSpec,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract per-judge-call rows + a summary dict for a single scenario."""
    logs = spec.scenario_dir / "logs"
    expected = _read_expected_or_unknown(spec.scenario_dir)
    verdicts = _verdicts(logs)
    decisions = [str(v.get("decision", "?")) for v in verdicts]
    rows: list[dict[str, Any]] = []
    meta_path = logs / "run_meta.json"
    terminal = "?"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
            terminal = str(meta.get("terminal", "?"))
        except (json.JSONDecodeError, OSError):
            pass
    for i, v in enumerate(verdicts):
        prior = decisions[i - 1] if i > 0 else ""
        current = str(v.get("decision", "?"))
        feedback = str(v.get("feedback") or "")
        fix_instructions = str(v.get("fix_instructions") or "")
        next_changed: bool | None
        if i + 1 < len(verdicts):
            next_changed = decisions[i + 1] != current
        else:
            next_changed = None
        rows.append(
            {
                "scenario_id": spec.scenario_id,
                "workflow": spec.workflow,
                "expected_stuck": expected,
                "judge_call_index": i + 1,
                "prior_decision": prior,
                "current_decision": current,
                "material_issue_cited": feedback[:140],
                "concrete_next_action_exists": (
                    _has_imperative(feedback) or bool(fix_instructions.strip())
                ),
                "outcome_changed_after_next_cycle": next_changed,
                "feedback_full": feedback,
                "fix_instructions": fix_instructions,
            }
        )
    summary = {
        "scenario_id": spec.scenario_id,
        "workflow": spec.workflow,
        "expected_stuck": expected,
        "terminal": terminal,
        "judge_calls": len(verdicts),
        "decision_trajectory": decisions,
        "observed_stuck": "stuck" in decisions,
        "classification": classify(expected, decisions),
    }
    return rows, summary


def render_matrix(rows: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> str:
    """Render the calibration matrix as Markdown."""
    lines = [
        "# Calibration matrix",
        "",
        "Generated by orchestra.calibration.extract_labels.",
        "",
        "## Per-scenario summary",
        "",
        "| scenario | workflow | expected | terminal | judge_calls | "
        "trajectory | observed_stuck | classification |",
        "|----------|----------|----------|----------|------------:|"
        "------------|---------------:|---------------|",
    ]
    for s in summaries:
        traj_list = s["decision_trajectory"]
        traj = ",".join(traj_list) if traj_list else "(none)"
        wf_short = "prji" if str(s["workflow"]).startswith("propose_review_judge") else "wf"
        lines.append(
            f"| {s['scenario_id']} "
            f"| {wf_short} "
            f"| {s['expected_stuck']} "
            f"| {s['terminal']} "
            f"| {s['judge_calls']} "
            f"| {traj} "
            f"| {s['observed_stuck']} "
            f"| {s['classification']} |"
        )
    lines.append("")
    lines.append("## Per-judge-call labels")
    lines.append("")
    lines.append(
        "| scenario | call# | prior | current | concrete_next_action | "
        "next_changed | issue_cited (first 80) |"
    )
    lines.append(
        "|----------|------:|-------|---------|----------------------|"
        "--------------|------------------------|"
    )
    for r in rows:
        issue = (r["material_issue_cited"] or "").replace("|", "\\|")
        if len(issue) > 80:
            issue = issue[:77] + "..."
        next_changed = (
            ""
            if r["outcome_changed_after_next_cycle"] is None
            else str(r["outcome_changed_after_next_cycle"])
        )
        lines.append(
            f"| {r['scenario_id']} "
            f"| {r['judge_call_index']} "
            f"| {r['prior_decision'] or '-'} "
            f"| {r['current_decision']} "
            f"| {r['concrete_next_action_exists']} "
            f"| {next_changed} "
            f"| {issue} |"
        )
    lines.append("")
    lines.append("## Classification legend")
    lines.append("")
    lines.append("- match: observed behavior aligns with expected.")
    lines.append("- under-trigger: expected positive, but stuck did not fire.")
    lines.append("- over-trigger: expected negative, but stuck fired.")
    lines.append(
        "- ambiguous-stuck / ambiguous-non-stuck: expected ambiguous; both ends are valid signal."
    )
    return "\n".join(lines) + "\n"


def _parse_scenario_arg(s: str) -> tuple[Path, str]:
    """Parse a ``<dir>:<workflow_name>`` argument. Returns (dir, workflow)."""
    if ":" not in s:
        raise argparse.ArgumentTypeError(f"expected <dir>:<workflow_name>, got {s!r}")
    dir_str, workflow = s.rsplit(":", 1)
    return (Path(dir_str), workflow)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scenario",
        action="append",
        nargs=2,
        metavar=("ID", "DIR_AND_WORKFLOW"),
        required=True,
        help=(
            "Repeat per scenario. ID is a short label; "
            "DIR_AND_WORKFLOW is <scenario_dir>:<workflow_name>. "
            "Example: --scenario prji-stuck-pos /tmp/.../prji-stuck-pos:"
            "propose_review_judge_implement"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write calibration_labels.jsonl and calibration_matrix.md.",
    )
    args = parser.parse_args(argv)

    specs: list[ScenarioSpec] = []
    for scenario_id, dir_and_workflow in args.scenario:
        scenario_dir, workflow = _parse_scenario_arg(dir_and_workflow)
        specs.append(ScenarioSpec(scenario_id, scenario_dir, workflow))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.output_dir / "calibration_labels.jsonl"
    matrix_path = args.output_dir / "calibration_matrix.md"

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for spec in specs:
        if not spec.scenario_dir.is_dir():
            sys.stderr.write(f"  skipped {spec.scenario_id}: dir absent ({spec.scenario_dir})\n")
            continue
        rows, summary = scenario_rows(spec)
        all_rows.extend(rows)
        summaries.append(summary)

    with rows_path.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    sys.stderr.write(f"wrote {len(all_rows)} rows to {rows_path}\n")

    matrix_path.write_text(render_matrix(all_rows, summaries))
    sys.stderr.write(f"wrote matrix to {matrix_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
