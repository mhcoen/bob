#!/usr/bin/env python3
"""Live test of the reviewer against a real commit.

Usage:
    python tests/test_reviewer_live.py [commit_hash]

If no commit hash is given, uses HEAD. Requires OPENROUTER_API_KEY
in the environment and .mcloop/config.json in the project root.

This is NOT a unit test. It makes a real API call to OpenRouter
and prints the results. Run it manually to verify the reviewer
integration works end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcloop.config import load_reviewer_config
from mcloop.reviewer import ReviewRequest


def main() -> None:
    # Resolve commit
    commit = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    result = subprocess.run(
        ["git", "rev-parse", commit],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"Bad commit: {commit}")
        sys.exit(1)
    commit_hash = result.stdout.strip()
    print(f"Commit: {commit_hash[:8]}")

    # Load config
    config = load_reviewer_config(str(project_root))
    if config is None:
        print("Reviewer not configured.")
        print("Need .mcloop/config.json with reviewer section")
        print("and OPENROUTER_API_KEY in environment.")
        sys.exit(1)
    print(f"Model:  {config.get('model', '?')}")
    print(f"URL:    {config.get('base_url', '?')}")
    print(f"Key:    {config.get('api_key', '')[:8]}...")

    # Get diff
    result = subprocess.run(
        ["git", "diff", f"{commit_hash}^..{commit_hash}"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    if result.returncode != 0:
        print(f"git diff failed: {result.stderr}")
        sys.exit(1)
    diff = result.stdout
    if not diff.strip():
        print("Empty diff, nothing to review.")
        sys.exit(0)

    lines = diff.count("\n")
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    print(f"Diff:   {lines} lines ({added} added, {removed} removed)")
    print()

    # Get commit message for context
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", commit_hash],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    commit_msg = result.stdout.strip()
    print(f"Task:   {commit_msg}")
    print()

    # Load project description
    plan_path = project_root / "PLAN.md"
    description = ""
    if plan_path.exists():
        content = plan_path.read_text()
        # Just the description (before first checkbox)
        desc_lines = []
        for line in content.splitlines():
            if line.strip().startswith("- ["):
                break
            desc_lines.append(line)
        description = "\n".join(desc_lines).strip()

    # Collect changed functions for context
    from mcloop.reviewer import _collect_changed_functions

    file_contents = _collect_changed_functions(project_root, diff)
    if file_contents:
        total_chars = sum(len(c) for c in file_contents.values())
        print(f"Context: {len(file_contents)} file(s), ~{total_chars // 1000}K chars")
    print()

    # Run review
    request = ReviewRequest(
        commit_hash=commit_hash,
        diff_text=diff,
        project_description=description,
        task_label="",
        task_text=commit_msg,
        file_contents=file_contents or None,
    )

    # Verbose mode: call the API directly so we can see raw response
    print("Sending to reviewer...")
    import urllib.error
    import urllib.request

    user_msg = f"## Task\n: {commit_msg}\n\n"
    user_msg += f"## Project\n{description}\n\n"
    user_msg += f"## Diff (commit {commit_hash})\n"
    user_msg += f"```diff\n{diff}\n```"
    if file_contents:
        user_msg += "\n\n## Changed file contents\n"
        for path, content in file_contents.items():
            user_msg += f"\n### {path}\n```\n{content}\n```\n"

    from mcloop.reviewer import _SYSTEM_PROMPT

    payload = json.dumps({
        "model": config["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
    }).encode()

    req = urllib.request.Request(
        f"{config['base_url'].rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - start
        print(f"Response: {elapsed:.1f}s")
        print(f"HTTP {e.code}: {e.read().decode()[:500]}")
        return
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"Response: {elapsed:.1f}s")
        print(f"Error: {e}")
        return

    elapsed = time.monotonic() - start
    print(f"Response: {elapsed:.1f}s")
    print()

    # Show raw response content
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(f"Unexpected response structure: {json.dumps(body)[:500]}")
        return

    print(f"Raw response ({len(content)} chars):")
    print(content[:2000])
    if len(content) > 2000:
        print(f"... ({len(content) - 2000} more chars)")
    print()

    # Also check for reasoning_details
    try:
        reasoning = body["choices"][0]["message"].get("reasoning_details")
        if reasoning:
            print(f"Reasoning details present ({len(str(reasoning))} chars)")
    except (KeyError, IndexError):
        pass

    # Parse the response we already have (no second API call)
    from mcloop.reviewer import _parse_findings

    content_to_parse = content.strip()
    if content_to_parse.startswith("```"):
        lines_p = content_to_parse.split("\n")
        lines_p = lines_p[1:]
        if lines_p and lines_p[-1].strip() == "```":
            lines_p = lines_p[:-1]
        content_to_parse = "\n".join(lines_p)

    try:
        raw = json.loads(content_to_parse)
    except json.JSONDecodeError:
        print("Could not parse JSON from response.")
        return

    if not isinstance(raw, list):
        print(f"Expected list, got {type(raw).__name__}")
        return

    findings = _parse_findings(raw)

    if not findings:
        print("No findings after parsing.")
        return

    print(f"{len(findings)} finding(s):")
    print()
    for i, f in enumerate(findings, 1):
        print(f"  {i}. [{f.severity}/{f.confidence}] {f.file}")
        print(f"     Lines {f.line_range[0]}-{f.line_range[1]}")
        print(f"     {f.description}")
        print()


if __name__ == "__main__":
    main()
