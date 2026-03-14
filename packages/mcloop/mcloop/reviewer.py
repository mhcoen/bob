"""AI-powered diff reviewer using OpenAI-compatible API."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

_MCLOOP_CONFIG = Path.home() / ".mcloop" / "config.json"

_SEVERITIES = frozenset({"error", "warning", "info"})
_CONFIDENCES = frozenset({"high", "medium", "low"})

_SYSTEM_PROMPT = """\
You are a code reviewer. Review the following git diff for:
- Bugs and logic errors
- Unhandled errors or exceptions
- Logic mismatches with the task specification
- Resource leaks (file handles, connections, memory)
- Missing edge cases

Respond with a JSON array of findings. Each finding is an object with:
- "file": string (file path)
- "line_range": [start, end] (line numbers in the diff)
- "severity": "error" | "warning" | "info"
- "description": string (what the issue is and how to fix it)
- "confidence": "high" | "medium" | "low"

If there are no issues, respond with an empty array: []
Respond ONLY with the JSON array, no other text."""


@dataclass
class ReviewFinding:
    """A single finding from a code review."""

    file: str
    line_range: list[int]
    severity: str  # error, warning, info
    description: str
    confidence: str  # high, medium, low


@dataclass
class ReviewRequest:
    """Input for a code review."""

    commit_hash: str
    diff_text: str
    project_description: str
    task_label: str
    task_text: str


def _load_config() -> dict:
    """Load ~/.mcloop/config.json, returning {} if missing or invalid."""
    if not _MCLOOP_CONFIG.exists():
        return {}
    try:
        return json.loads(_MCLOOP_CONFIG.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_findings(raw: list) -> list[ReviewFinding]:
    """Parse raw JSON list into ReviewFinding objects, skipping invalid."""
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            severity = str(item.get("severity", "")).lower()
            confidence = str(item.get("confidence", "")).lower()
            if severity not in _SEVERITIES:
                severity = "info"
            if confidence not in _CONFIDENCES:
                confidence = "medium"
            findings.append(
                ReviewFinding(
                    file=str(item.get("file", "")),
                    line_range=list(item.get("line_range", [0, 0])),
                    severity=severity,
                    description=str(item.get("description", "")),
                    confidence=confidence,
                )
            )
        except (TypeError, ValueError):
            continue
    return findings


def run_review(request: ReviewRequest, config: dict) -> list[ReviewFinding]:
    """Send diff to an OpenAI-compatible endpoint for review.

    Config keys (from load_reviewer_config):
        model: model name (required)
        base_url: API base URL (required)
        api_key: API key (required, from OPENROUTER_API_KEY env var)
    """
    api_key = config.get("api_key", "")
    if not api_key:
        return []

    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return []
    model = config.get("model", "")

    user_msg = f"## Task\n{request.task_label}: {request.task_text}\n\n"
    user_msg += f"## Project\n{request.project_description}\n\n"
    user_msg += f"## Diff (commit {request.commit_hash})\n"
    user_msg += f"```diff\n{request.diff_text}\n```"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
    }

    url = f"{base_url}/chat/completions"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []

    try:
        content = body["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (fences)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        raw = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    return _parse_findings(raw)


def run_review_cli(commit_hash: str, project_dir: str) -> None:
    """CLI entry point: review a commit and write results to disk."""
    from mcloop.config import load_reviewer_config

    proj = Path(project_dir)
    config = load_reviewer_config(project_dir)
    if config is None:
        return

    # Get diff
    result = subprocess.run(
        ["git", "diff", f"{commit_hash}^..{commit_hash}"],
        capture_output=True,
        text=True,
        cwd=proj,
    )
    if result.returncode != 0:
        print(f"git diff failed: {result.stderr.strip()}", file=sys.stderr)
        return

    diff_text = result.stdout
    if not diff_text.strip():
        print("Empty diff, nothing to review.", file=sys.stderr)
        return

    # Load project description from PLAN.md
    plan_path = proj / "PLAN.md"
    project_description = ""
    if plan_path.exists():
        try:
            project_description = plan_path.read_text()
        except OSError:
            pass

    request = ReviewRequest(
        commit_hash=commit_hash,
        diff_text=diff_text,
        project_description=project_description,
        task_label="",
        task_text="",
    )

    import time as _time

    _start = _time.monotonic()
    findings = run_review(request, config)
    _elapsed = _time.monotonic() - _start

    # Write results with elapsed time
    reviews_dir = proj / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    out_path = reviews_dir / f"{commit_hash}.json"
    result_data = {
        "findings": [asdict(f) for f in findings],
        "elapsed_seconds": round(_elapsed, 1),
        "commit": commit_hash,
    }
    out_path.write_text(json.dumps(result_data, indent=2) + "\n")

    print(f"Review complete: {len(findings)} finding(s) [{_elapsed:.0f}s] -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python -m mcloop.reviewer <commit_hash> <project_dir>",
            file=sys.stderr,
        )
        sys.exit(1)
    run_review_cli(sys.argv[1], sys.argv[2])
