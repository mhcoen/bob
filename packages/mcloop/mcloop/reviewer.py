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
You are a code reviewer. You will receive a git diff along with the
enclosing functions from each changed file (imports, then only the
functions that contain changes, with line numbers). Use this context
to understand variable definitions, function signatures, and control
flow before evaluating the diff.

Review for:
- Bugs and logic errors
- Unhandled errors or exceptions
- Logic mismatches with the task specification
- Resource leaks (file handles, connections, memory)
- Missing edge cases

Do NOT flag issues that exist in the full file but are unrelated to
the diff. Only report problems introduced or exposed by the changes.

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
    file_contents: dict[str, str] | None = None  # path -> content


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
    if request.file_contents:
        user_msg += "\n\n## Changed file contents\n"
        for path, content in request.file_contents.items():
            user_msg += f"\n### {path}\n```\n{content}\n```\n"

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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def _parse_diff_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified diff to extract changed line ranges per file.

    Returns {filepath: [(start, end), ...]} where start/end are
    1-indexed line numbers in the post-change version of the file.
    """
    import re

    result: dict[str, list[tuple[int, int]]] = {}
    current_file = None
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in result:
                result[current_file] = []
        elif line.startswith("+++ /dev/null"):
            current_file = None  # deleted file
        elif current_file is not None:
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                end = start + max(count - 1, 0)
                result[current_file].append((start, end))

    return result


def _extract_enclosing_functions(
    file_path: Path,
    line_ranges: list[tuple[int, int]],
) -> str:
    """Extract functions/methods that contain the changed lines.

    Uses indentation-based heuristics that work for Python, Swift,
    and most C-like languages. Returns the concatenated function
    bodies with "..." separators, plus the file's import block.
    """
    try:
        lines = file_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return ""

    if not lines:
        return ""

    # Collect all changed line numbers (0-indexed)
    changed = set()
    for start, end in line_ranges:
        for ln in range(start - 1, end):  # convert to 0-indexed
            if 0 <= ln < len(lines):
                changed.add(ln)

    if not changed:
        return ""

    # Find function boundaries using def/func/fn/class patterns
    import re

    func_re = re.compile(
        r"^(\s*)"
        r"(def |async def |class "
        r"|func |private func |public func |internal func "
        r"|fn |pub fn "
        r"|function "
        r"|static "
        r")"
    )

    # Build list of (start_line, indent_level) for each function
    func_starts: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        m = func_re.match(line)
        if m:
            indent = len(m.group(1))
            func_starts.append((i, indent))

    # For each function start, find its end (next line at same or
    # less indentation that starts a new definition, or EOF)
    func_ranges: list[tuple[int, int]] = []
    for idx, (start, indent) in enumerate(func_starts):
        end = len(lines) - 1
        for next_start, next_indent in func_starts[idx + 1 :]:
            if next_indent <= indent:
                end = next_start - 1
                break
        # Trim trailing blank lines
        while end > start and not lines[end].strip():
            end -= 1
        func_ranges.append((start, end))

    # Find which functions contain changed lines
    selected: set[int] = set()
    for i, (fstart, fend) in enumerate(func_ranges):
        for ln in changed:
            if fstart <= ln <= fend:
                selected.add(i)
                break

    # Also collect the import/header block (lines before first function)
    header_end = func_starts[0][0] if func_starts else len(lines)
    header = lines[:header_end]
    # Trim to just imports and top-level assignments
    header_text = "\n".join(header).rstrip()

    # If no functions matched (changes in top-level code), return
    # the header plus surrounding context
    if not selected:
        context_lines: list[str] = []
        for ln in sorted(changed):
            start = max(0, ln - 5)
            end = min(len(lines), ln + 6)
            for j in range(start, end):
                context_lines.append(f"{j + 1:4d}  {lines[j]}")
        return (
            header_text
            + "\n\n# Changed lines:\n"
            + "\n".join(
                dict.fromkeys(context_lines)  # deduplicate, preserve order
            )
        )

    # Build output: header + selected functions with line numbers
    parts = [header_text] if header_text.strip() else []
    for i in sorted(selected):
        fstart, fend = func_ranges[i]
        func_lines = []
        for j in range(fstart, fend + 1):
            func_lines.append(f"{j + 1:4d}  {lines[j]}")
        parts.append("\n".join(func_lines))

    return "\n\n...\n\n".join(parts)


def _collect_changed_functions(
    project_dir: Path,
    diff_text: str,
) -> dict[str, str] | None:
    """Parse a diff and extract enclosing functions from each changed file.

    Returns {filepath: extracted_functions} or None if nothing found.
    """
    ranges = _parse_diff_line_ranges(diff_text)
    if not ranges:
        return None

    result: dict[str, str] = {}
    for filepath, line_ranges in ranges.items():
        fpath = project_dir / filepath
        if not fpath.exists():
            continue
        extracted = _extract_enclosing_functions(fpath, line_ranges)
        if extracted:
            result[filepath] = extracted

    return result or None


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

    # Collect changed functions from each modified file for context
    file_contents = _collect_changed_functions(proj, diff_text)

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
        file_contents=file_contents or None,
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
