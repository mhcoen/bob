"""Check and auto-update CLAUDE.md alongside source file changes."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

_SOURCE_EXTENSIONS = frozenset(
    (
        ".py",
        ".swift",
        ".rs",
        ".go",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".rb",
        ".sh",
    )
)

_SOURCE_DIRS = ("src/", "lib/", "package/")

_UPDATE_SYSTEM_PROMPT = """\
You maintain a project manifest file called CLAUDE.md. You will receive
the current CLAUDE.md and a git diff showing what changed. Update ONLY
the entries affected by the diff:

- New source file created: add a descriptive entry in the appropriate section
- Source file deleted: remove its entry
- File renamed or moved: update the path in its entry
- Functions moved between files: update both source and destination entries
- File purpose changed significantly: update the description
- New test file: add a brief entry listing what it tests

Do NOT rewrite entries that are unrelated to the diff.
Do NOT change formatting, ordering, or wording of unaffected entries.
Do NOT add commentary or explanation outside the file content.

Respond with the complete updated CLAUDE.md file content and nothing else.
No markdown fences, no preamble, no explanation."""


def _is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file."""
    name = Path(path).name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.go"):
        return True
    return False


def _is_source_file(path: str) -> bool:
    """Return True if *path* is a non-test source file."""
    if _is_test_file(path):
        return False
    suffix = Path(path).suffix
    if suffix in _SOURCE_EXTENSIONS:
        return True
    for prefix in _SOURCE_DIRS:
        if path.startswith(prefix):
            return True
    return False


def check_claude_md_freshness(
    changed_files: list[str],
    project_dir: Path,  # noqa: ARG001
) -> bool:
    """Return False if source files changed but CLAUDE.md did not.

    *changed_files* should be a list of repo-relative paths (e.g. from
    ``git diff --name-only``).  *project_dir* is accepted for future use
    but currently unused.

    Returns True when no source files were touched **or** when CLAUDE.md
    is among the changed files.
    """
    has_source = False
    has_claude_md = False

    for path in changed_files:
        if Path(path).name == "CLAUDE.md":
            has_claude_md = True
        if _is_source_file(path):
            has_source = True

    if not has_source:
        return True
    return has_claude_md


def _get_diff_text(project_dir: Path) -> str:
    """Return the combined diff of staged and unstaged changes."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    result = subprocess.run(
        ["git", "diff"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_update_config() -> dict | None:
    """Load config for CLAUDE.md auto-update from ~/.mcloop/config.json.

    Uses the reviewer config (model, base_url, api_key) since the same
    OpenRouter setup works for both. Returns None if not configured.
    """
    config_path = Path.home() / ".mcloop" / "config.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    reviewer = data.get("reviewer")
    if not isinstance(reviewer, dict):
        return None
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    return {
        "model": reviewer.get("model", ""),
        "base_url": reviewer.get("base_url", "").rstrip("/"),
        "api_key": api_key,
    }


def auto_update_claude_md(project_dir: Path) -> bool:
    """Auto-update CLAUDE.md using a cheap LLM call.

    Reads the current CLAUDE.md and git diff, sends them to the
    configured model, and writes the updated content back.

    Returns True if CLAUDE.md was successfully updated, False otherwise.
    """
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        return False

    config = _load_update_config()
    if not config or not config["base_url"] or not config["model"]:
        return False

    diff_text = _get_diff_text(project_dir)
    if not diff_text:
        return False

    current_content = claude_md.read_text()

    user_msg = (
        f"## Current CLAUDE.md\n\n{current_content}\n\n## Git diff\n\n```diff\n{diff_text}\n```"
    )

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": _UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.0,
        "max_tokens": 16384,
    }

    url = f"{config['base_url']}/chat/completions"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"  CLAUDE.md auto-update failed: {exc}", flush=True)
        return False

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print("  CLAUDE.md auto-update: no content in response", flush=True)
        return False

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    if not content or len(content) < 100:
        print("  CLAUDE.md auto-update: response too short, skipping", flush=True)
        return False

    claude_md.write_text(content + "\n")
    print("  CLAUDE.md auto-updated by LLM", flush=True)
    return True
