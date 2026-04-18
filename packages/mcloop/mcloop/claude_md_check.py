"""Check CLAUDE.md freshness and append LLM diff summaries to NOTES.md.

CLAUDE.md (the project manifest) is treated as read-only by this
module.  ``check_claude_md_freshness`` only inspects it to decide
whether source changes went in without a matching manifest update.

The auto-update sends ONLY the git diff to a cheap LLM and asks
for a brief summary.  That summary is appended to NOTES.md, which
is a human-readable changelog.  CLAUDE.md is never written to.
"""

from __future__ import annotations

import enum
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcloop.formatting import strip_code_fences


class SyncResult(enum.Enum):
    """Outcome of an auto-update attempt."""

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

_SUMMARY_SYSTEM_PROMPT = """\
You summarize git diffs for a project changelog.  You will receive
a git diff.  Return a brief plain-text summary (2-5 lines) of what
changed and why.  Focus on the functional intent, not line counts.
Do not use markdown formatting, bullet points, or headers.
Do not include file paths unless a file was added or deleted.
Do not wrap your response in code fences.
Just return the summary text and nothing else."""


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


def _get_diff_text(project_dir: Path, commit_sha: str = "") -> str:
    """Return the diff to feed to the summary LLM.

    When *commit_sha* is provided, returns the diff of that commit
    (for post-commit sync).  Otherwise falls back to uncommitted changes.
    """
    if commit_sha:
        from mcloop.git_ops import _get_committed_diff

        return _get_committed_diff(project_dir, commit_sha)

    from mcloop.git_ops import _get_diff

    return _get_diff(project_dir)


def _load_update_config() -> dict | None:
    """Load config for diff-summary auto-update from ~/.mcloop/config.json.

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


def _parse_llm_response(body: object) -> str | None:
    """Extract summary text from an OpenRouter-format LLM response.

    Returns the cleaned content string, or None if the response is
    malformed or empty.
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

    content = strip_code_fences(content).strip()

    if not content:
        return None
    return content


def _call_deepseek(config: dict, diff_text: str) -> str | None:
    """Call DeepSeek via OpenRouter with just the diff.

    Returns the summary string or None on failure.
    """
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": diff_text},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"  NOTES.md summary call failed: {exc}", flush=True)
        return None

    content = _parse_llm_response(body)
    if content is None:
        print("  NOTES.md summary: empty response", flush=True)
    return content


def _call_sonnet_fallback(diff_text: str) -> str | None:
    """Call Claude Sonnet via ``claude -p`` subprocess as fallback.

    Strips ANTHROPIC_API_KEY from the environment so the subprocess
    bills against the Max subscription, not API credits.
    """
    prompt = f"{_SUMMARY_SYSTEM_PROMPT}\n\n{diff_text}"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "sonnet", prompt],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"  NOTES.md Sonnet fallback failed: {exc}", flush=True)
        return None

    if result.returncode != 0:
        print(f"  NOTES.md Sonnet fallback exited {result.returncode}", flush=True)
        return None

    content = strip_code_fences(result.stdout).strip()

    if not content:
        print("  NOTES.md Sonnet fallback: empty response", flush=True)
        return None
    return content


def auto_update_claude_md(project_dir: Path, commit_sha: str = "") -> SyncResult:
    """Append an LLM-generated diff summary to NOTES.md.

    Sends ONLY the git diff to a cheap LLM (DeepSeek via OpenRouter,
    with Sonnet fallback) and asks for a brief summary.  The summary
    is appended to NOTES.md.  CLAUDE.md is never read or written.

    The function name is retained for callsite compatibility; the
    behavior now targets NOTES.md exclusively.

    Returns a :class:`SyncResult` indicating the outcome.
    """
    notes_md = project_dir / "NOTES.md"
    if not notes_md.exists():
        return SyncResult.NO_WORK

    config = _load_update_config()
    if not config:
        return SyncResult.NO_WORK
    if not config["base_url"] or not config["model"]:
        return SyncResult.PERMANENT_FAILED

    diff_text = _get_diff_text(project_dir, commit_sha)
    if not diff_text:
        return SyncResult.NO_WORK

    summary = _call_deepseek(config, diff_text)
    if summary is None:
        time.sleep(_DEEPSEEK_RETRY_SLEEP)
        summary = _call_deepseek(config, diff_text)

    if summary is None:
        print("  NOTES.md: DeepSeek failed twice, trying Sonnet fallback...", flush=True)
        summary = _call_sonnet_fallback(diff_text)

    if summary is None:
        print("  NOTES.md auto-update: all providers failed", flush=True)
        return SyncResult.TRANSIENT_FAILED

    short_sha = commit_sha[:7] if commit_sha else "unknown"
    existing = notes_md.read_text()
    if not existing.endswith("\n"):
        existing += "\n"
    existing += f"\n{short_sha}: {summary}\n"
    notes_md.write_text(existing)
    print("  NOTES.md auto-updated by LLM", flush=True)
    return SyncResult.OK
