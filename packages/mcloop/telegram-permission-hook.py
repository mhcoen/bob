#!/usr/bin/env python3
"""PreToolUse hook: Telegram approval gate with session memory.

Whitelisted commands (from settings.json permissions.allow) pass through.
Everything else sends a Telegram message with Approve/Deny/Allow All buttons
and blocks until the user responds. "Allow All" remembers the tool pattern
for the rest of the session.
"""

import fnmatch
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

ENV_FILE = Path.home() / ".claude" / "telegram-hook.env"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SESSION_FILE = Path.home() / ".claude" / "telegram-hook-session.json"

POLL_INTERVAL = 2  # seconds between Telegram polling
POLL_TIMEOUT = 600  # max seconds to wait for a response


def _load_env_file():
    """Load key=value pairs from ~/.claude/telegram-hook.env as fallback."""
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


_env = _load_env_file()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or _env.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or _env.get("TELEGRAM_CHAT_ID", "")

RULE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\((.+)\))?$")


# --- Session memory ---

def _tool_pattern(tool_name, tool_input):
    """Create a pattern key for session memory. Uses the full command."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        return f"Bash:{cmd}"
    if tool_name in ("Edit", "Read", "Write"):
        path = tool_input.get("file_path", "")
        return f"{tool_name}:{path}"
    return tool_name


def _load_session():
    """Load session-approved patterns from temp file."""
    try:
        data = json.loads(SESSION_FILE.read_text())
        # Expire after 24 hours
        if data.get("created", 0) < time.time() - 86400:
            return set()
        return set(data.get("patterns", []))
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def _save_session(patterns):
    """Save session-approved patterns to temp file."""
    try:
        # Preserve creation time if file exists
        try:
            existing = json.loads(SESSION_FILE.read_text())
            created = existing.get("created", time.time())
        except (OSError, json.JSONDecodeError):
            created = time.time()
        SESSION_FILE.write_text(json.dumps({
            "created": created,
            "patterns": sorted(patterns),
        }))
        _dbg(f"saved session: {sorted(patterns)}")
    except Exception as e:
        _dbg(f"session save FAILED: {e}")


def is_session_allowed(tool_name, tool_input):
    """Check if this tool pattern was approved for the session."""
    pattern = _tool_pattern(tool_name, tool_input)
    return pattern in _load_session()


def remember_session(tool_name, tool_input):
    """Add this tool pattern to the session allow list."""
    patterns = _load_session()
    patterns.add(_tool_pattern(tool_name, tool_input))
    _save_session(patterns)


# --- Permission rules ---

def _respond(decision, reason=""):
    """Write a properly formatted PreToolUse hook response to stdout."""
    resp = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        resp["hookSpecificOutput"]["permissionDecisionReason"] = reason
    json.dump(resp, sys.stdout)


def load_allow_rules():
    """Read permissions.allow from ~/.claude/settings.json."""
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        return settings.get("permissions", {}).get("allow", [])
    except (OSError, json.JSONDecodeError):
        return []


def match_rule(rule, tool_name, tool_input):
    """Check if a permission rule matches this tool call."""
    m = RULE_RE.match(rule)
    if not m:
        return False

    rule_tool, rule_arg = m.group(1), m.group(2)

    if rule_tool != tool_name:
        return False

    if rule_arg is None:
        return True

    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        if rule_arg.endswith(":*"):
            prefix = rule_arg[:-2]
            return cmd == prefix or cmd.startswith(prefix + " ")
        else:
            return cmd == rule_arg

    if tool_name == "WebFetch":
        if rule_arg.startswith("domain:"):
            domain = rule_arg[7:]
            url = tool_input.get("url", "")
            try:
                from urllib.parse import urlparse
                url_domain = urlparse(url).hostname or ""
                return url_domain == domain or url_domain.endswith("." + domain)
            except Exception:
                return False

    if tool_name in ("Edit", "Read", "Write", "Glob"):
        path_key = "file_path" if tool_name in ("Edit", "Read", "Write") else "path"
        target = tool_input.get(path_key, "")
        if not target:
            return False
        pattern = os.path.expanduser(rule_arg)
        return fnmatch.fnmatch(target, pattern)

    if tool_name == "Skill":
        skill = tool_input.get("skill", "")
        return skill == rule_arg

    return rule_arg in json.dumps(tool_input)


def is_allowed(tool_name, tool_input):
    """Check if this tool call is covered by any allow rule."""
    rules = load_allow_rules()
    return any(match_rule(rule, tool_name, tool_input) for rule in rules)


# --- Telegram ---

def telegram_api(method, data=None):
    """Call a Telegram Bot API method. Use data dict for POST body (supports nested JSON)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if data is not None:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def send_approval_request(text):
    """Send a message with inline Approve/Deny/Allow All buttons. Returns message_id."""
    data = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": "approve"},
                    {"text": "Deny", "callback_data": "deny"},
                ],
                [
                    {"text": "Allow All Session", "callback_data": "allow_session"},
                ],
            ]
        },
    }
    result = telegram_api("sendMessage", data=data)
    return result["result"]["message_id"]


def update_message(message_id, text):
    """Edit the approval message to show the decision (removes buttons)."""
    data = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        telegram_api("editMessageText", data=data)
    except Exception:
        pass


def poll_for_response(message_id):
    """Poll getUpdates for a callback_query on our message."""
    # Get the latest update_id to only look at new updates
    initial = telegram_api("getUpdates", data={"limit": 1, "offset": -1})
    offset = 0
    if initial.get("result"):
        offset = initial["result"][-1]["update_id"] + 1

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            updates = telegram_api("getUpdates", data={
                "offset": offset,
                "timeout": POLL_INTERVAL,
                "allowed_updates": ["callback_query"],
            })
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue

        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if not cb:
                continue
            if cb.get("message", {}).get("message_id") != message_id:
                continue

            # Answer the callback to dismiss the spinner
            try:
                telegram_api("answerCallbackQuery", data={
                    "callback_query_id": cb["id"],
                })
            except Exception:
                pass

            return cb["data"]  # "approve", "deny", or "allow_session"

    return None  # timed out


def format_tool_description(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        lines = [f"`{cmd}`"]
        if desc:
            lines.append(f"({desc})")
        return "\n".join(lines)
    elif tool_name in ("Write", "Read"):
        path = tool_input.get("file_path", "?")
        return f"{tool_name}: `{path}`"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "?")
        old = tool_input.get("old_string", "")[:80]
        return f"Edit: `{path}`\n`{old}` → ..."
    else:
        return f"`{json.dumps(tool_input)[:200]}`"


_DBG_PATH = Path.home() / ".claude" / "telegram-hook-debug.log"


def _dbg(msg):
    with open(_DBG_PATH, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def main():
    _dbg(f"invoked, BOT={bool(BOT_TOKEN)}, CHAT={bool(CHAT_ID)}, TMPDIR={os.environ.get('TMPDIR')}, SESSION={SESSION_FILE}")

    if not BOT_TOKEN or not CHAT_ID:
        _dbg("EXIT: no credentials, no opinion")
        json.dump({}, sys.stdout)
        return

    hook_input = json.load(sys.stdin)
    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})
    cwd = hook_input.get("cwd", "")
    home = str(Path.home())
    user = Path(home).name
    project = Path(cwd).name if cwd else "?"
    session_label = f"{user}/{project}"
    _dbg(f"tool={tool_name} input={json.dumps(tool_input)[:100]}")

    # Whitelisted commands pass through instantly
    if is_allowed(tool_name, tool_input):
        _dbg("EXIT: allowed by rules")
        _respond("allow")
        return

    # Session-approved patterns pass through
    if is_session_allowed(tool_name, tool_input):
        pattern = _tool_pattern(tool_name, tool_input)
        _dbg(f"EXIT: allowed by session memory ({pattern})")
        _respond("allow", f"Session-approved: {pattern}")
        return

    # Not whitelisted. Send Telegram with buttons and wait.
    desc = format_tool_description(tool_name, tool_input)
    pattern = _tool_pattern(tool_name, tool_input)
    task_label = os.environ.get("MCLOOP_TASK_LABEL", "")
    label_prefix = f"[{task_label}] " if task_label else ""
    msg = (
        f"*{label_prefix}Permission needed* [{session_label}]\n\n"
        f"Tool: *{tool_name}*\n{desc}\n\n"
        f"Pattern: `{pattern}`"
    )

    try:
        message_id = send_approval_request(msg)
        _dbg(f"sent approval request, message_id={message_id}")
    except Exception as e:
        _dbg(f"EXIT: telegram send failed ({e}), no opinion")
        json.dump({}, sys.stdout)
        return

    # Block and poll for the button press
    _dbg("polling for response...")
    decision = poll_for_response(message_id)

    if decision == "approve":
        update_message(message_id, f"{label_prefix}Approved: *{tool_name}*\n{desc}")
        _dbg("EXIT: approved via Telegram")
        _respond("allow", "Approved via Telegram")
    elif decision == "allow_session":
        remember_session(tool_name, tool_input)
        update_message(message_id, f"{label_prefix}Approved (session): *{tool_name}*\n{desc}\nPattern `{pattern}` remembered")
        _dbg(f"EXIT: session-approved via Telegram ({pattern})")
        _respond("allow", f"Session-approved via Telegram: {pattern}")
    elif decision == "deny":
        update_message(message_id, f"{label_prefix}Denied: *{tool_name}*\n{desc}")
        _dbg("EXIT: denied via Telegram")
        _respond("deny", "Denied via Telegram")
    else:
        update_message(message_id, f"{label_prefix}Timed out: *{tool_name}*\n{desc}")
        _dbg("EXIT: timed out, denying")
        _respond("deny", "Timed out waiting for Telegram approval")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _dbg(f"EXIT: exception {e}, no opinion")
        err_path = str(Path.home() / ".claude" / "telegram-hook-error.log")
        with open(err_path, "a") as f:
            import traceback
            f.write(f"--- {time.strftime('%H:%M:%S')} ---\n")
            traceback.print_exc(file=f)
        json.dump({}, sys.stdout)
