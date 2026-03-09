"""Notifications via Telegram and iMessage."""

from __future__ import annotations

import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".claude" / "telegram-hook.env"


def _load_env() -> dict[str, str]:
    vals = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    except OSError:
        pass
    return vals


def _get_config() -> tuple[str, str, str]:
    """Load notification config from env vars and env file at call time."""
    env = _load_env()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID", "")
    imessage_id = os.environ.get("IMESSAGE_ID") or env.get("IMESSAGE_ID", "")
    return bot_token, chat_id, imessage_id


def _send_telegram(text: str) -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    bot_token, chat_id, _ = _get_config()
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    ).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def _send_imessage(text: str) -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    _, _, imessage_id = _get_config()
    if not imessage_id:
        return
    chat_id = f"any;-;{imessage_id}"
    script = (
        'tell application "Messages"\n'
        f'  send "{_escape_applescript(text)}" to chat id "{_escape_applescript(chat_id)}"\n'
        "end tell"
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")


def notify(message: str, level: str = "info") -> None:
    """Send a notification. Default: Telegram. Set MCLOOP_IMESSAGE=1 for iMessage."""
    prefix = {"info": "", "warning": "Warning: ", "error": "ERROR: "}.get(level, "")
    if os.environ.get("MCLOOP_IMESSAGE"):
        _send_imessage(f"McLoop: {prefix}{message}")
    else:
        _send_telegram(f"*McLoop* {prefix}{message}")
