"""Maintain mode: check and enforce invariants from MAINTAIN.md."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcloop import formatting
from mcloop.checklist import CHECKBOX_RE
from mcloop.checks import get_check_commands
from mcloop.git_ops import (
    _checkpoint,
    _commit,
    _ensure_git,
    _has_meaningful_changes,
    _push_or_die,
    _sanitize_commit_msg,
)
from mcloop.lifecycle import _kill_orphan_sessions, register_signal_handlers
from mcloop.notify import notify
from mcloop.runner import run_task, warn_unknown_model


@dataclass
class InvariantResult:
    """Result of checking a single invariant."""

    text: str
    outcome: str  # "satisfied", "fixed", "failed"
    autonomous: bool = False
    autonomous_note: str = ""


@dataclass
class MaintainSummary:
    """Summary of a full maintain run."""

    results: list[InvariantResult] = field(default_factory=list)

    @property
    def satisfied(self) -> int:
        return sum(1 for r in self.results if r.outcome == "satisfied")

    @property
    def fixed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "fixed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "failed")

    @property
    def autonomous_decisions(self) -> list[InvariantResult]:
        return [r for r in self.results if r.autonomous]


def parse_invariants(path: str | Path) -> list[str]:
    """Parse MAINTAIN.md and return a list of invariant texts.

    Reuses the checklist parser's checkbox regex. Each unchecked
    item ``- [ ] ...`` is an invariant. Checked items are skipped
    (they indicate permanently satisfied invariants the user has
    retired).
    """
    p = Path(path)
    if not p.exists():
        return []
    invariants: list[str] = []
    for line in p.read_text().splitlines():
        m = CHECKBOX_RE.match(line)
        if m and m.group(2) == " ":
            invariants.append(m.group(3).strip())
    return invariants


def _build_maintain_prompt(
    invariant_text: str,
    check_commands: list[str] | None,
) -> str:
    """Build the prompt for a maintain session checking one invariant."""
    parts = []
    parts.append(
        "You are checking whether an invariant holds in this codebase."
        " An invariant is a statement of desired state, not a task."
        " Your job is to verify it, fix it if broken, and report"
        " the outcome."
    )
    parts.append(f"INVARIANT: {invariant_text}")
    parts.append(
        "Follow these steps:\n"
        "1. Check whether the invariant currently holds in the codebase.\n"
        "2. If it holds, report SATISFIED.\n"
        "3. If it does not hold, fix it with minimal changes.\n"
        "4. Run the project's check commands to verify your fix.\n"
        "5. If the fix passes all checks, report FIXED.\n"
        "6. If you cannot fix it or checks fail, report FAILED."
    )
    if check_commands:
        cmds = ", ".join(check_commands)
        parts.append(
            "CHECK COMMANDS (mandatory):\n"
            f"Commands: {cmds}\n"
            "Run each check command EXACTLY ONCE before reporting"
            " FIXED. If a check fails, attempt to fix and re-run"
            " (maximum 3 total runs per command)."
        )
    parts.append(
        "Report your outcome in this exact format at the end"
        " of your response:\n"
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: SATISFIED|FIXED|FAILED\n"
        "DETAIL: <brief explanation>\n"
        "--- END MAINTAIN ---"
    )
    parts.append("Do not chain shell commands with && or ;. Use separate Bash calls instead.")
    parts.append(
        "Never delete any file. Do not use rm, git rm,"
        " os.remove, unlink, shutil.rmtree, or any other"
        " file deletion mechanism."
    )
    return "\n\n".join(parts)


def parse_maintain_output(output: str) -> tuple[str, str]:
    """Parse maintain session output for outcome.

    Returns (outcome, detail) where outcome is one of
    'satisfied', 'fixed', 'failed', or 'unknown'.
    """
    marker = "--- MAINTAIN RESULT ---"
    end_marker = "--- END MAINTAIN ---"
    idx = output.find(marker)
    if idx == -1:
        return "failed", "No result marker found in session output"
    after = output[idx + len(marker) :]
    end_idx = after.find(end_marker)
    if end_idx != -1:
        after = after[:end_idx]

    outcome = "unknown"
    detail = ""
    for line in after.strip().splitlines():
        line = line.strip()
        if line.startswith("OUTCOME:"):
            raw = line[len("OUTCOME:") :].strip().lower()
            if raw in ("satisfied", "fixed", "failed"):
                outcome = raw
        elif line.startswith("DETAIL:"):
            detail = line[len("DETAIL:") :].strip()

    if outcome == "unknown":
        return "failed", detail or "Could not parse outcome from session"
    return outcome, detail


def _write_maintain_log(
    project_dir: Path,
    results: list[InvariantResult],
) -> None:
    """Append maintain results to .mcloop/maintain-log.json."""
    log_dir = project_dir / ".mcloop"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "maintain-log.json"

    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []

    timestamp = datetime.now(timezone.utc).isoformat()
    run_entry = {
        "timestamp": timestamp,
        "results": [],
    }
    for r in results:
        entry: dict = {
            "invariant": r.text,
            "outcome": r.outcome,
        }
        if r.autonomous:
            entry["autonomous"] = True
            if r.autonomous_note:
                entry["autonomous_note"] = r.autonomous_note
        run_entry["results"].append(entry)

    existing.append(run_entry)
    log_path.write_text(json.dumps(existing, indent=2) + "\n")


def _print_maintain_summary(summary: MaintainSummary) -> None:
    """Print the maintain run summary."""
    print(
        formatting.system_msg(
            f"Maintain summary: {summary.satisfied} satisfied,"
            f" {summary.fixed} fixed, {summary.failed} failed"
        ),
        flush=True,
    )
    if summary.autonomous_decisions:
        print(
            formatting.system_msg("Autonomous decisions (no user confirmation):"),
            flush=True,
        )
        for r in summary.autonomous_decisions:
            note = f": {r.autonomous_note}" if r.autonomous_note else ""
            print(f"  - [{r.outcome}] {r.text}{note}", flush=True)


def run_maintain(
    maintain_path: Path,
    cli: str = "claude",
    model: str | None = None,
) -> MaintainSummary:
    """Run the maintain loop over all invariants in MAINTAIN.md.

    Each invariant gets its own CLI session. Failure of one does
    not stop the run. Returns a MaintainSummary with all results.
    """
    import mcloop.runner as _runner

    register_signal_handlers(_runner)

    project_dir = maintain_path.parent
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    _kill_orphan_sessions(project_dir)
    _ensure_git(project_dir)
    _checkpoint(project_dir, verbose=True)
    _push_or_die(project_dir)

    if model:
        warn_unknown_model(cli, model)

    invariants = parse_invariants(maintain_path)
    if not invariants:
        print(
            formatting.system_msg("No invariants found in MAINTAIN.md"),
            flush=True,
        )
        return MaintainSummary()

    project_checks = get_check_commands(project_dir)
    summary = MaintainSummary()

    notify(f"Maintain: checking {len(invariants)} invariant(s)")
    print(
        formatting.system_msg(f"Maintain: {len(invariants)} invariant(s) to check"),
        flush=True,
    )

    for i, invariant_text in enumerate(invariants, 1):
        print(
            formatting.task_header(str(i), invariant_text, cli),
            flush=True,
        )

        task_text = _build_maintain_prompt(invariant_text, project_checks)
        task_start = time.monotonic()

        result = run_task(
            task_text,
            cli,
            project_dir,
            log_dir,
            description="",
            task_label=f"maintain-{i}",
            model=model,
            session_context="",
            check_commands=None,  # Already included in the maintain prompt
        )

        elapsed = formatting.format_elapsed(time.monotonic() - task_start)

        if not result.success:
            inv_result = InvariantResult(
                text=invariant_text,
                outcome="failed",
            )
            summary.results.append(inv_result)
            print(
                formatting.error_msg(
                    f"Invariant {i} session failed (exit {result.exit_code}) [{elapsed}]"
                ),
                flush=True,
            )
            continue

        outcome, detail = parse_maintain_output(result.output)

        if outcome == "fixed":
            if _has_meaningful_changes(project_dir):
                commit_msg = f"maintain: {_sanitize_commit_msg(invariant_text)}"
                try:
                    _commit(project_dir, commit_msg)
                except RuntimeError as exc:
                    print(
                        formatting.error_msg(f"Invariant {i} commit failed: {exc}"),
                        flush=True,
                    )
                    outcome = "failed"
                    detail = f"Commit failed: {exc}"
            else:
                # Session said fixed but no changes — treat as satisfied
                outcome = "satisfied"
                detail = detail or "No changes needed"

        inv_result = InvariantResult(
            text=invariant_text,
            outcome=outcome,
        )
        summary.results.append(inv_result)

        status_icon = {"satisfied": "OK", "fixed": "FIXED", "failed": "FAIL"}.get(outcome, "?")
        print(
            formatting.system_msg(f"Invariant {i}: {status_icon} [{elapsed}]"),
            flush=True,
        )
        if detail:
            print(f"    {detail}", flush=True)

    # Write log and print summary
    _write_maintain_log(project_dir, summary.results)
    _print_maintain_summary(summary)

    # Final notification
    notify(
        f"Maintain done: {summary.satisfied} satisfied,"
        f" {summary.fixed} fixed, {summary.failed} failed"
    )

    return summary
