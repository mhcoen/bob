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


_env = _load_env()
_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or _env.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or _env.get("TELEGRAM_CHAT_ID", "")
_IMESSAGE_ID = os.environ.get("IMESSAGE_ID") or _env.get("IMESSAGE_ID", "")


def _send_telegram(text: str) -> None:
    if not _BOT_TOKEN or not _CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"}
    ).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def _send_imessage(text: str) -> None:
    if not _IMESSAGE_ID:
        return
    chat_id = f"any;-;{_IMESSAGE_ID}"
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
    return s.replace("\\", "\\\\").replace('"', '\\"')


_backend = "imessage" if os.environ.get("MCLOOP_IMESSAGE") else "telegram"


def notify(message: str, level: str = "info") -> None:
    """Send a notification. Default: Telegram. Set MCLOOP_IMESSAGE=1 for iMessage."""
    prefix = {"info": "", "warning": "Warning: ", "error": "ERROR: "}.get(level, "")
    if _backend == "imessage":
        _send_imessage(f"McLoop: {prefix}{message}")
    else:
        _send_telegram(f"*McLoop* {prefix}{message}")
