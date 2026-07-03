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
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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

# rtk is optional. Detect it once at startup so the hook never spawns a
# failing `rtk` subprocess per command when rtk is not installed. When rtk is
# absent, commands pass through unrewritten and only the Telegram gate applies.
RTK_AVAILABLE = shutil.which("rtk") is not None


# --- Session memory ---


_SHELL_SUFFIX = ";&|"


def _bash_prefix(cmd):
    """Extract the command prefix (executable + subcommand) from a Bash command.

    Returns the first two tokens unless the second token starts with '-',
    in which case only the executable is returned. Trailing shell
    metacharacters (;, &, |) glued to a token are stripped so a compound
    command like 'pytest; echo done' yields 'pytest echo', not 'pytest;
    echo'. If stripping leaves a token empty (e.g. standalone '&&'), the
    prefix stops at the previous token.
    Examples: 'git add a.py' -> 'git add', 'ls -la' -> 'ls',
              'ruff check .' -> 'ruff check', 'pytest;' -> 'pytest'.
    """
    parts = [p.rstrip(_SHELL_SUFFIX) for p in cmd.split(None, 2)]
    if not parts or not parts[0]:
        return ""
    if len(parts) >= 2 and parts[1] and not parts[1].startswith("-"):
        return f"{parts[0]} {parts[1]}"
    return parts[0]


# --- McLoop test-routing policy ---
#
# In a McLoop task session (MCLOOP_TASK_LABEL set) the inner agent must not
# run tests freely. A raw `pytest` run lets the agent self-interpret a
# vacuous green (nothing collected, all skipped, an unparseable summary) as
# success. All in-session verification must route through the sanctioned
# `mcloop verify` adapter, which applies the loop's scoped signal predicate
# and exits non-zero on no-signal. This policy denies every *recognized*
# free-form test shape and directs the agent to that entry point. It does
# not claim to catch every opaque alias or helper script from a raw shell
# string -- only that no recognized alternate test shape is waved through.

_TEST_DENY_REASON = (
    "Direct test execution is blocked in McLoop task sessions. Do not run "
    "pytest/tox/nox/make test or any other free-form test command and do "
    "not self-interpret raw pytest output. Run the sanctioned scoped "
    "verdict instead: `mcloop verify` (or `python -m mcloop verify`), which "
    "routes through the loop's signal predicate and exits non-zero on "
    "no-signal."
)

_PYTEST_NAMES = {"pytest", "py.test"}
_TEST_RUNNERS = {"tox", "nox"}
_PYTHON_NAMES = {"python", "python2", "python3"}
_RUN_WRAPPERS = {"uv", "poetry", "pdm", "pipenv", "rye"}
_PASSTHROUGH_WRAPPERS = {"xargs", "time", "nice", "nohup", "stdbuf", "command"}
_SHELL_NAMES = {"bash", "sh", "zsh", "dash", "ksh"}
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _basename(token):
    """Last path component of a token, e.g. '/usr/bin/python3' -> 'python3'."""
    return token.rsplit("/", 1)[-1]


def _strip_env_prefix(tokens):
    """Drop a leading `env` and any VAR=value assignments."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "env" or _ENV_ASSIGN_RE.match(t):
            i += 1
            continue
        break
    return tokens[i:]


def _segment_is_test(tokens):
    """Is one shell segment (already tokenized) a recognized test invocation?"""
    tokens = _strip_env_prefix(tokens)
    if not tokens:
        return False
    head = _basename(tokens[0])
    rest = tokens[1:]

    if head in _PYTEST_NAMES or head in _TEST_RUNNERS:
        return True

    if head in _PYTHON_NAMES or head.startswith("python3."):
        # python -m pytest (or -m coverage ... but coverage handled below)
        if "-m" in rest:
            mi = rest.index("-m")
            if mi + 1 < len(rest) and _basename(rest[mi + 1]) in _PYTEST_NAMES:
                return True
        # python -c "import pytest; pytest.main(...)"
        if "-c" in rest:
            ci = rest.index("-c")
            if ci + 1 < len(rest) and "pytest" in rest[ci + 1]:
                return True
        return False

    if head == "coverage":
        # coverage run -m pytest
        if "-m" in rest:
            mi = rest.index("-m")
            if mi + 1 < len(rest) and _basename(rest[mi + 1]) in _PYTEST_NAMES:
                return True
        return False

    if head in _RUN_WRAPPERS:
        # uv run pytest, poetry run pytest, uv run python -m pytest, ...
        if rest[:1] == ["run"]:
            return _segment_is_test(rest[1:])
        return False

    if head == "hatch":
        if rest[:1] == ["test"]:
            return True
        if rest[:1] == ["run"]:
            return any("pytest" in _basename(t) for t in rest[1:])
        return False

    if head == "make":
        # make test, make test-fast, make check-tests, ...
        return any("test" in t for t in rest if not t.startswith("-"))

    if head in _SHELL_NAMES:
        # bash -c "pytest ...": recurse into the -c payload
        if "-c" in rest:
            ci = rest.index("-c")
            if ci + 1 < len(rest):
                return _looks_like_test_command(rest[ci + 1])
        return False

    if head == "eval":
        return _looks_like_test_command(" ".join(rest))

    if head in _PASSTHROUGH_WRAPPERS:
        return _segment_is_test(rest)

    return False


_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "\n"}


def _looks_like_test_command(cmd):
    """True if any sub-command of a raw shell string is a recognized test run.

    Tokenizes with shlex (``punctuation_chars`` so shell operators become
    their own tokens while quoting is respected -- a ``;`` inside a quoted
    ``python -c`` payload stays put), then splits the flat token list into
    segments on top-level separators and inspects each. Biases toward
    detection: an unbalanced-quote string falls back to a permissive
    whitespace split so shell-indirection forms still trip the deny.
    """
    if not cmd or not cmd.strip():
        return False
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        for seg in re.split(r"&&|\|\||;|\||\n", cmd):
            seg = seg.strip()
            if seg and _segment_is_test(seg.split()):
                return True
        return False

    segment = []
    for tok in tokens:
        if tok in _SEPARATORS:
            if segment and _segment_is_test(segment):
                return True
            segment = []
        else:
            segment.append(tok)
    return bool(segment) and _segment_is_test(segment)


def _tool_pattern(tool_name, tool_input):
    """Create a pattern key for session memory.

    For Bash tools, stores the command prefix (executable + subcommand)
    so that a single approval covers all invocations with different arguments.
    For other tools, uses exact-string matching on the relevant identifier.
    """
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        prefix = _bash_prefix(cmd)
        return f"Bash:{prefix}"
    if tool_name in ("Edit", "Read", "Write"):
        path = tool_input.get("file_path", "")
        return f"{tool_name}:{path}"
    return tool_name


def _load_session():
    """Load session-approved patterns from temp file."""
    try:
        data = json.loads(SESSION_FILE.read_text())
        if data.get("created", 0) < time.time() - 86400:
            SESSION_FILE.unlink(missing_ok=True)
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
        SESSION_FILE.write_text(
            json.dumps(
                {
                    "created": created,
                    "patterns": sorted(patterns),
                }
            )
        )
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


def _rtk_rewrite(cmd):
    """Call rtk rewrite and return the rewritten command, or None."""
    if not RTK_AVAILABLE:
        return None
    try:
        result = subprocess.run(
            ["rtk", "rewrite", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # exit 0 = allow, 3 = ask — both print a valid rewrite to stdout.
        # exit 1 = no rtk equivalent, 2 = deny — no rewrite. mcloop makes its
        # own permission decision, so rtk's verdict (0 vs 3) is irrelevant here.
        if result.returncode in (0, 3) and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _respond(decision, reason="", tool_name="", tool_input=None):
    """Write a properly formatted PreToolUse hook response to stdout."""
    resp = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        resp["hookSpecificOutput"]["permissionDecisionReason"] = reason
    # RTK re-enabled 2026-05-29 (rtk pytest -q bare-summary parse fixed in
    # ecc34d7; path-prefix #1053 fixed in mhcoen's patch). This single hook
    # does both rewrite and permission, so there is no multi-hook updatedInput
    # race: on an allowed Bash command, emit rtk's rewrite as updatedInput.
    if decision == "allow" and tool_name == "Bash" and tool_input is not None:
        cmd = tool_input.get("command", "").strip()
        if cmd:
            resp["hookSpecificOutput"]["updatedInput"] = {
                **tool_input,
                "command": _rtk_rewrite(cmd) or cmd,
            }
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

    return False


def _unwrap_rtk(tool_input):
    """If command starts with 'rtk proxy', return input with the unwrapped command."""
    if "command" not in tool_input:
        return tool_input
    cmd = tool_input["command"].strip()
    if cmd.startswith("rtk proxy "):
        unwrapped = cmd[len("rtk proxy ") :]
        return {**tool_input, "command": unwrapped}
    return tool_input


def is_allowed(tool_name, tool_input):
    """Check if this tool call is covered by any allow rule."""
    rules = load_allow_rules()
    if any(match_rule(rule, tool_name, tool_input) for rule in rules):
        return True
    # Check if the unwrapped command (without rtk proxy) is allowed
    unwrapped = _unwrap_rtk(tool_input)
    if unwrapped is not tool_input:
        return any(match_rule(rule, tool_name, unwrapped) for rule in rules)
    return False


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
    try:
        result = telegram_api("sendMessage", data=data)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        # Interpolated command text with unbalanced Markdown tokens
        # (_ * `) makes Telegram reject the message with 400, which
        # previously turned the ask into a silent "no opinion" and
        # bypassed the permission gate. Retry as plain text.
        data = dict(data)
        data.pop("parse_mode", None)
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
            updates = telegram_api(
                "getUpdates",
                data={
                    "offset": offset,
                    "timeout": POLL_INTERVAL,
                    "allowed_updates": ["callback_query"],
                },
            )
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
                telegram_api(
                    "answerCallbackQuery",
                    data={
                        "callback_query_id": cb["id"],
                    },
                )
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
    # Interactive sessions (no MCLOOP_TASK_LABEL): no Telegram permission flow,
    # but rtk stays active everywhere in Claude Code. Delegate to `rtk hook
    # claude`, which rewrites the command AND sets the permission decision per
    # the user's allow/ask/deny rules (so allow-listed commands don't start
    # prompting). Done inside this single registered hook so there is no second
    # PreToolUse hook racing on updatedInput. Any failure -> "{}" (no opinion).
    if not os.environ.get("MCLOOP_TASK_LABEL"):
        if not RTK_AVAILABLE:
            json.dump({}, sys.stdout)
            return
        raw = sys.stdin.read()
        out = "{}"
        try:
            r = subprocess.run(
                ["rtk", "hook", "claude"],
                input=raw,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.stdout.strip():
                out = r.stdout
        except Exception:
            pass
        sys.stdout.write(out)
        return

    _dbg(
        f"invoked, BOT={bool(BOT_TOKEN)}, CHAT={bool(CHAT_ID)},"
        f" TMPDIR={os.environ.get('TMPDIR')}, SESSION={SESSION_FILE}"
    )

    hook_input = json.load(sys.stdin)
    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})
    cwd = hook_input.get("cwd", "")
    home = str(Path.home())
    user = Path(home).name
    project = Path(cwd).name if cwd else "?"
    session_label = f"{user}/{project}"
    task_label = os.environ.get("MCLOOP_TASK_LABEL", "")
    _dbg(f"tool={tool_name} input={json.dumps(tool_input)[:100]}")

    # Test-routing policy (mcloop task sessions only). Deny recognized
    # free-form test invocations and direct the agent to the sanctioned
    # `mcloop verify` entry point. This runs BEFORE the no-credentials
    # fallback and BEFORE the allowlist / session-approval path so that
    # missing Telegram credentials or a prior approval cannot bypass test
    # routing. Interactive sessions (no MCLOOP_TASK_LABEL) never reach here.
    if task_label and tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if _looks_like_test_command(cmd):
            _dbg(f"EXIT: denied free-form test invocation: {cmd[:80]}")
            _respond("deny", _TEST_DENY_REASON)
            return

    if not BOT_TOKEN or not CHAT_ID:
        _dbg("EXIT: no credentials, no opinion")
        json.dump({}, sys.stdout)
        return

    # Block MCP tools in McLoop sessions
    if task_label and tool_name.startswith("mcp__"):
        _dbg(f"EXIT: blocked MCP tool {tool_name} in mcloop session")
        _respond("deny", "MCP tools blocked in mcloop")
        return

    # Whitelisted commands pass through instantly
    if is_allowed(tool_name, tool_input):
        _dbg("EXIT: allowed by rules")
        _respond("allow", tool_name=tool_name, tool_input=tool_input)
        return

    # Session-approved patterns pass through
    if is_session_allowed(tool_name, tool_input):
        pattern = _tool_pattern(tool_name, tool_input)
        _dbg(f"EXIT: allowed by session memory ({pattern})")
        _respond(
            "allow",
            f"Session-approved: {pattern}",
            tool_name=tool_name,
            tool_input=tool_input,
        )
        return

    # Not whitelisted. Send Telegram with buttons and wait.
    desc = format_tool_description(tool_name, tool_input)
    pattern = _tool_pattern(tool_name, tool_input)
    label_prefix = f"[{task_label}] " if task_label else ""
    msg = (
        f"*{label_prefix}Permission needed* [{session_label}]\n\n"
        f"Tool: *{tool_name}*\n{desc}\n\n"
        f"Pattern: `{pattern}`"
    )

    # Create pending file so McLoop can show waiting status
    pending_dir = Path(cwd) / ".mcloop" / "pending"
    pending_file = None
    try:
        pending_dir.mkdir(parents=True, exist_ok=True)
        pending_file = pending_dir / f"{os.getpid()}"
        pending_file.write_text(f"{tool_name}: {desc[:200]}")
    except OSError:
        pass

    try:
        message_id = send_approval_request(msg)
        _dbg(f"sent approval request, message_id={message_id}")
    except Exception as e:
        if pending_file:
            pending_file.unlink(missing_ok=True)
        _dbg(f"EXIT: telegram send failed ({e}), no opinion")
        json.dump({}, sys.stdout)
        return

    # Block and poll for the button press
    _dbg("polling for response...")
    decision = poll_for_response(message_id)

    # Remove pending file
    if pending_file:
        pending_file.unlink(missing_ok=True)

    if decision == "approve":
        update_message(message_id, f"{label_prefix}Approved: *{tool_name}*\n{desc}")
        _dbg("EXIT: approved via Telegram")
        _respond("allow", "Approved via Telegram", tool_name=tool_name, tool_input=tool_input)
    elif decision == "allow_session":
        remember_session(tool_name, tool_input)
        msg = (
            f"{label_prefix}Approved (session): *{tool_name}*\n"
            f"{desc}\nPattern `{pattern}` remembered"
        )
        update_message(message_id, msg)
        _dbg(f"EXIT: session-approved via Telegram ({pattern})")
        _respond(
            "allow",
            f"Session-approved via Telegram: {pattern}",
            tool_name=tool_name,
            tool_input=tool_input,
        )
    elif decision == "deny":
        update_message(message_id, f"{label_prefix}Denied: *{tool_name}*\n{desc}")
        _dbg("EXIT: denied via Telegram")
        # Write denial marker so mcloop can kill the session
        try:
            denied_file = pending_dir / "denied"
            denied_file.write_text(f"Denied: {tool_name} {desc[:200]}")
        except OSError:
            pass
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
