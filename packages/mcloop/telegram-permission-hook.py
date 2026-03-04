#!/usr/bin/env python3
"""PreToolUse hook: Telegram notification + Remote Control approval.

Whitelisted commands (from settings.json permissions.allow) pass through.
Everything else sends a Telegram notification and returns "ask" so Claude Code
shows a permission prompt that the user can approve via Remote Control.
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

def telegram_api(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def send_message(text):
    return telegram_api("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="Markdown")


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


def _dbg(msg):
    with open("/tmp/telegram-hook-debug.log", "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def main():
    _dbg(f"invoked, BOT={bool(BOT_TOKEN)}, CHAT={bool(CHAT_ID)}")

    if not BOT_TOKEN or not CHAT_ID:
        _dbg("EXIT: no credentials, no opinion")
        json.dump({}, sys.stdout)
        return

    hook_input = json.load(sys.stdin)
    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})
    cwd = hook_input.get("cwd", "")
    # Derive "user/project" identifier matching Remote Control labels
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

    # Not whitelisted. In interactive sessions, return no opinion so
    # autoAllowBashIfSandboxed handles it. In unattended sessions (LOOP_ASK=1),
    # notify via Telegram and return "ask" to gate via Remote Control.
    unattended = os.environ.get("LOOP_ASK") or _env.get("LOOP_ASK")

    if not unattended:
        _dbg("EXIT: returning no opinion (interactive)")
        json.dump({}, sys.stdout)
        return

    desc = format_tool_description(tool_name, tool_input)
    msg = (
        f"*Permission needed* [{session_label}]\n\n"
        f"Tool: *{tool_name}*\n{desc}\n\n"
        f"Approve via Remote Control"
    )

    try:
        send_message(msg)
        _dbg("EXIT: notified")
    except Exception as e:
        _dbg(f"EXIT: telegram send failed ({e})")

    _dbg("EXIT: returning ask (unattended)")
    _respond("ask", "Awaiting approval via Remote Control")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _dbg(f"EXIT: exception {e}, no opinion")
        with open("/tmp/telegram-hook-error.log", "a") as f:
            import traceback
            f.write(f"--- {time.strftime('%H:%M:%S')} ---\n")
            traceback.print_exc(file=f)
        json.dump({}, sys.stdout)
