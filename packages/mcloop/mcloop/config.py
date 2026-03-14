"""Reviewer configuration loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse


def load_reviewer_config(
    project_dir: str,
    force: bool = False,
) -> dict | None:
    """Load reviewer config from .mcloop/config.json in the project directory.

    Returns the reviewer dict (with api_key added) if the config file has
    a "reviewer" section AND OPENROUTER_API_KEY env var is set AND either
    "enabled": true is in the config or force=True (from --reviewer flag).
    Returns None otherwise.
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
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    result = dict(reviewer)
    result["api_key"] = api_key
    return result


def format_reviewer_status(config: dict | None) -> str:
    """Format a human-readable status string for the reviewer config.

    Returns:
        "{model} via {host} (API key set)" if fully configured,
        "configured but OPENROUTER_API_KEY not set (disabled)" if config
            exists but no API key,
        "" if no config.
    """
    if config is None:
        return ""
    model = config.get("model", "")
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    if not api_key:
        return "configured but OPENROUTER_API_KEY not set (disabled)"
    host = urlparse(base_url).hostname or base_url
    return f"{model} via {host} (API key set)"
