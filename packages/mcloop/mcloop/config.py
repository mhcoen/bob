"""Reviewer configuration loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

# Roles supported by the role-based ~/.mcloop/config.json schema.
# The old flat "model" and project-level "reviewer" keys remain
# valid when the new role section is absent.
_ROLES = frozenset({"executor", "sync", "reviewer"})

# Reviewer dispatch backends. "rest" hits an OpenAI-compatible endpoint
# (OpenRouter, provider APIs, Ollama) and requires OPENROUTER_API_KEY
# plus base_url. "claude_code" and "codex" route through the matching
# orchestra adapter and authenticate via the user's existing CLI
# subscription, so they do not need an API key in the environment.
REVIEWER_BACKENDS = frozenset({"rest", "claude_code", "codex"})
_DEFAULT_REVIEWER_BACKEND = "rest"

_USER_CONFIG_PATH = Path.home() / ".mcloop" / "config.json"


def _read_user_config() -> dict:
    """Return the parsed contents of ~/.mcloop/config.json or {}."""
    if not _USER_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_USER_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_role_config(role: str, source: dict | None = None) -> dict | None:
    """Return the per-role config block from ~/.mcloop/config.json.

    *role* must be one of "executor", "sync", or "reviewer".  When the
    new role-based schema is absent, this returns None so callers can
    fall back to the legacy flat "model" / project-level "reviewer"
    keys.  Pass *source* to override the parsed config (test hook).
    """
    if role not in _ROLES:
        raise ValueError(f"unknown role: {role}")
    data = source if source is not None else _read_user_config()
    block = data.get(role)
    if not isinstance(block, dict):
        return None
    return dict(block)


def load_reviewer_config(
    project_dir: str,
    force: bool = False,
) -> dict | None:
    """Load reviewer config from .mcloop/config.json in the project directory.

    Returns the reviewer dict if the config file has a "reviewer" section
    AND the section's backend prerequisites are met AND either
    "enabled": true is in the config or force=True (from --reviewer flag).
    Returns None otherwise.

    The backend field defaults to "rest". For backend="rest" the function
    additionally requires OPENROUTER_API_KEY in the environment and adds
    it to the returned dict under "api_key". For backend="claude_code" or
    "codex" the env var is not required and "api_key" is omitted; the
    orchestra adapter authenticates through the user's CLI subscription.
    Unknown backend values cause this function to return None.
    """
    config_path = Path(project_dir) / ".mcloop" / "config.json"
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
    if not force and not reviewer.get("enabled", False):
        return None
    backend = reviewer.get("backend", _DEFAULT_REVIEWER_BACKEND)
    if backend not in REVIEWER_BACKENDS:
        return None
    result = dict(reviewer)
    result["backend"] = backend
    if backend == "rest":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return None
        result["api_key"] = api_key
    return result


def format_reviewer_status(config: dict | None) -> str:
    """Format a human-readable status string for the reviewer config.

    Returns:
        "{model} via {host} (API key set)" for the rest backend when
            an api_key is present,
        "{model} via Claude Code (subscription)" for claude_code,
        "{model} via Codex (subscription)" for codex,
        "configured but OPENROUTER_API_KEY not set (disabled)" if a
            rest-backend config exists but no API key,
        "" if no config.
    """
    if config is None:
        return ""
    model = config.get("model", "")
    backend = config.get("backend", _DEFAULT_REVIEWER_BACKEND)
    if backend == "claude_code":
        return f"{model} via Claude Code (subscription)"
    if backend == "codex":
        return f"{model} via Codex (subscription)"
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    if not api_key:
        return "configured but OPENROUTER_API_KEY not set (disabled)"
    host = urlparse(base_url).hostname or base_url
    return f"{model} via {host} (API key set)"
