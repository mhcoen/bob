"""Check and auto-update CLAUDE.md alongside source file changes."""

from __future__ import annotations

import enum
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


class SyncResult(enum.Enum):
    """Outcome of a CLAUDE.md auto-update attempt."""

    OK = "ok"
    NO_WORK = "no_work"
    TRANSIENT_FAILED = "transient_failed"
    PERMANENT_FAILED = "permanent_failed"


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

_DEEPSEEK_RETRY_SLEEP = 5  # seconds between DeepSeek retry attempts

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
        if path == "CLAUDE.md":
            has_claude_md = True
        if _is_source_file(path):
            has_source = True

    if not has_source:
        return True
    return has_claude_md


def _get_diff_text(project_dir: Path) -> str:
    """Return the combined diff of staged and unstaged changes."""
    from mcloop.git_ops import _get_diff

    return _get_diff(project_dir)


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


def _build_user_message(current_content: str, diff_text: str) -> str:
    """Build the user message for CLAUDE.md auto-update."""
    return f"## Current CLAUDE.md\n\n{current_content}\n\n## Git diff\n\n```diff\n{diff_text}\n```"


def _parse_llm_response(body: object) -> str | None:
    """Extract and clean CLAUDE.md content from an LLM response body.

    Returns the cleaned content string, or None if the response is
    malformed, missing content, or too short (<100 chars).
    """
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or len(choices) == 0:
        return None
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None

    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    if not content or len(content) < 100:
        return None
    return content


def _call_deepseek(config: dict, user_msg: str) -> str | None:
    """Call DeepSeek via OpenRouter. Returns content or None on transient failure."""
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
        print(f"  CLAUDE.md DeepSeek call failed: {exc}", flush=True)
        return None

    content = _parse_llm_response(body)
    if content is None:
        print("  CLAUDE.md DeepSeek: malformed or too-short response", flush=True)
    return content


def _call_sonnet_fallback(user_msg: str) -> str | None:
    """Call Claude Sonnet via ``claude -p`` subprocess as fallback.

    Strips ANTHROPIC_API_KEY from the environment so the subprocess
    bills against the Max subscription, not API credits.
    """
    prompt = f"{_UPDATE_SYSTEM_PROMPT}\n\n{user_msg}"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "sonnet", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"  CLAUDE.md Sonnet fallback failed: {exc}", flush=True)
        return None

    if result.returncode != 0:
        print(f"  CLAUDE.md Sonnet fallback exited {result.returncode}", flush=True)
        return None

    content = result.stdout.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    if not content or len(content) < 100:
        print("  CLAUDE.md Sonnet fallback: response too short", flush=True)
        return None
    return content


def auto_update_claude_md(project_dir: Path) -> SyncResult:
    """Auto-update CLAUDE.md using a cheap LLM call with fallback.

    Tries DeepSeek via OpenRouter twice (with 5s sleep between attempts),
    then falls back to Claude Sonnet via ``claude -p`` subprocess.

    Returns a :class:`SyncResult` indicating the outcome.
    """
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        return SyncResult.NO_WORK

    config = _load_update_config()
    if not config:
        return SyncResult.NO_WORK
    if not config["base_url"] or not config["model"]:
        return SyncResult.PERMANENT_FAILED

    diff_text = _get_diff_text(project_dir)
    if not diff_text:
        return SyncResult.NO_WORK

    current_content = claude_md.read_text()
    user_msg = _build_user_message(current_content, diff_text)

    # DeepSeek attempt 1
    content = _call_deepseek(config, user_msg)
    if content is None:
        # DeepSeek attempt 2 after brief pause
        time.sleep(_DEEPSEEK_RETRY_SLEEP)
        content = _call_deepseek(config, user_msg)

    if content is None:
        # Sonnet fallback
        print("  CLAUDE.md: DeepSeek failed twice, trying Sonnet fallback...", flush=True)
        content = _call_sonnet_fallback(user_msg)

    if content is None:
        print("  CLAUDE.md auto-update: all providers failed", flush=True)
        return SyncResult.TRANSIENT_FAILED

    claude_md.write_text(content + "\n")
    print("  CLAUDE.md auto-updated by LLM", flush=True)
    return SyncResult.OK
