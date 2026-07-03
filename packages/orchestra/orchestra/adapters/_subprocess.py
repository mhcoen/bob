"""Subprocess invocation, output capture, and logging helpers.

Lifted from ``mcloop/runner.py`` so the two real adapters in this
package preserve mcloop's runtime behavior bit-for-bit:

- minimal environment via ``PASSTHROUGH_VARS`` plus billing-mode
  injection (``api``, ``openrouter``) and third-party model provider
  routing (deepseek, kimi, openai, etc.) per ``_apply_provider_env``,
- stream-json line-by-line output capture with head-and-tail
  truncation,
- monotonic-clock timeout that exit-codes -2 and kills the process
  group,
- ``.mcloop/active-pid`` publishing plus a watchdog subprocess that
  kills the inner CLI if mcloop dies,
- ``.mcloop/pending`` Telegram-approval polling with a ``denied``
  short-circuit that returns exit code 1,
- a per-invocation log file with the same prologue mcloop writes.

The orchestra executor enforces its own per-state timeout above this
layer; the wall-clock cap inside ``run_session`` is a defence in depth
matching mcloop's invariants.
"""

from __future__ import annotations

import collections
import json as _json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Minimal environment passed to CLI subprocesses. Matches mcloop's
# _PASSTHROUGH_VARS exactly so behavior is identical at the parity
# layer. RTK_* are kept because mcloop's tee tooling looks for them.
PASSTHROUGH_VARS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "COLORTERM",
        "FORCE_COLOR",
        "NO_COLOR",
        "RTK_DB_PATH",
        "RTK_TEE",
        "RTK_TEE_DIR",
    }
)

# Map from CLI name to the env var that gates API vs subscription
# billing. Lifted from mcloop's ``_BILLING_KEY``.
BILLING_KEY: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
}

# Short third-party model aliases routed through the OpenRouter
# provider. Lifted from mcloop's ``_MODEL_PROVIDERS``.
MODEL_PROVIDERS: dict[str, str] = {
    "deepseek-v4-pro": "deepseek",
    "deepseek-v4-flash": "deepseek",
    "kimi-k2.6": "moonshotai",
}

# Provider prefixes recognized in fully-qualified model slugs. Lifted
# from mcloop's ``_THIRD_PARTY_PREFIXES``.
THIRD_PARTY_PREFIXES: tuple[str, ...] = ("deepseek/", "moonshotai/", "openai/")

DEFAULT_PROVIDER_BASE_URL: str = "https://openrouter.ai/api"

DEFAULT_TIMEOUT_S: int = 3600
"""Hard wall-clock cap. Defense against runaway sessions that produce
output but never finish. Substantive editor/verify tasks routinely
cross 30 minutes of real work; the idle timeout below is the actual
"stuck or alive" detector."""

IDLE_TIMEOUT_S: float = 600.0
"""Kill the session if no stream event has arrived in this many
seconds. The reader thread sees every line the inner CLI emits, so
no progress on that channel is a true "stuck" signal — distinct from
"working slowly". Prevents the wall-clock cap from punishing
legitimate long-but-progressing work."""


def timeout_s_from_ms(timeout_ms: int | None, default_s: int) -> int:
    """Convert a request's ``timeout_ms`` to whole seconds for ``run_session``.

    Ceil-divides so a sub-second cap does not truncate to 0, which the
    session loop treats as "no wall-clock timeout", and floors any
    explicit cap at 1s. Returns *default_s* when no cap was requested.
    Shared by every adapter's ``prepare`` so the conversion cannot
    drift per adapter.
    """
    if timeout_ms is None:
        return default_s
    return max(1, (timeout_ms + 999) // 1000)


PROGRESS_QUEUE_INTERVAL: float = 3.0
"""How long the reader thread blocks before checking the timeout."""

PROGRESS_DOT_INTERVAL: float = 3.0
"""Seconds between progress dots when the inner process is silent."""

_MAX_HEAD_LINES = 5_000
_MAX_TAIL_LINES = 45_000
_SENTINEL = object()

_MCLOOP_CONFIG_PATH: Path = Path.home() / ".mcloop" / "config.json"


# --------------------------------------------------------------------
# Live-activity surfacing
#
# The orchestra ``actor_progress`` ticker only knows elapsed time. For
# agent-routed sessions, the inner CLI emits ``tool_use`` blocks on the
# stream-json channel that describe what the agent is currently doing
# (Read /path/x, Edit /path/y, Bash some-cmd). The reader thread parses
# each line it consumes and updates the module-global activity string so
# the stateful progress reporter can surface it as a second line under
# the elapsed-time ticker without coupling the reporter to the
# subprocess module.
# --------------------------------------------------------------------


_activity_lock: threading.Lock = threading.Lock()
_current_activity: str = ""


def _clear_current_activity() -> None:
    global _current_activity
    with _activity_lock:
        _current_activity = ""


def get_current_activity() -> str:
    """Return the most recent ``tool_use`` summary from the active
    session, or ``""`` if no session is running or no tool_use has been
    observed yet.

    Safe to call from any thread. The stateful progress reporter calls
    this from its watchdog-driven ``actor_progress`` handler to surface
    "currently doing X" beneath the elapsed-time line.
    """
    with _activity_lock:
        return _current_activity


def _set_current_activity(summary: str) -> None:
    global _current_activity
    with _activity_lock:
        _current_activity = summary


def _format_tool_use_summary(block: dict[str, Any]) -> str:
    """Render a stream-json ``tool_use`` block as a short activity
    description.

    Picks the most useful single-line representation per known tool
    name (path for Read/Edit/Write/Glob, command for Bash, pattern for
    Grep). Unknown tools fall back to the bare tool name. Returns ``""``
    if the block has no usable name.
    """
    name = block.get("name")
    if not isinstance(name, str) or not name:
        return ""
    raw_input = block.get("input")
    inp: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}

    def _first(*keys: str) -> str:
        for key in keys:
            value = inp.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        detail = _first("file_path", "notebook_path", "path")
    elif name == "Bash":
        detail = _first("command")
    elif name in ("Grep", "Glob"):
        detail = _first("pattern", "path")
    elif name == "WebFetch":
        detail = _first("url")
    elif name == "WebSearch":
        detail = _first("query")
    elif name == "TodoWrite":
        detail = ""
    else:
        detail = _first("command", "file_path", "path", "query", "pattern", "url")
    return f"{name} {detail}".strip() if detail else name


def _record_activity_from_line(line: str) -> None:
    """Parse one stream-json line and, if it announces a new tool_use,
    update the module-global current activity.

    Tolerant of non-JSON lines and unexpected shapes; never raises.
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return
    try:
        data = _json.loads(line)
    except (_json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    # Two shapes Claude Code emits for the same event:
    # - top-level ``{"type": "assistant", "message": {"content": [...]}}``
    # - ``{"type": "stream_event", "event": {"type": "content_block_start",
    #   "content_block": {"type": "tool_use", ...}}}``
    if data.get("type") == "assistant":
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        summary = _format_tool_use_summary(block)
                        if summary:
                            _set_current_activity(summary)
                            return
    if data.get("type") == "stream_event":
        event = data.get("event")
        if isinstance(event, dict) and event.get("type") == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                summary = _format_tool_use_summary(block)
                if summary:
                    _set_current_activity(summary)


# --------------------------------------------------------------------
# Environment construction (mirrors mcloop's _build_session_env and
# _apply_provider_env)
# --------------------------------------------------------------------


_MCLOOP_ROLES: frozenset[str] = frozenset({"executor", "sync", "reviewer"})


def _load_mcloop_config() -> dict[str, Any]:
    """Load ``~/.mcloop/config.json``. Returns empty dict on missing
    or malformed file. Mirrors mcloop's ``_load_mcloop_config``."""
    if not _MCLOOP_CONFIG_PATH.exists():
        return {}
    try:
        loaded = _json.loads(_MCLOOP_CONFIG_PATH.read_text())
    except (_json.JSONDecodeError, OSError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def load_role_config(role: str, source: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return the per-role config block from ``~/.mcloop/config.json``.

    Lifted from mcloop ``load_role_config``. ``role`` must be one of
    ``executor``, ``sync``, ``reviewer``. The block typically carries
    ``base_url`` and ``auth_token_env`` overrides that
    ``apply_provider_env`` honors when routing third-party model
    aliases. Returns ``None`` when the role section is absent so
    callers can fall back to defaults.
    """
    if role not in _MCLOOP_ROLES:
        raise ValueError(f"unknown role: {role}")
    data = source if source is not None else _load_mcloop_config()
    block = data.get(role)
    if not isinstance(block, dict):
        return None
    return dict(block)


def provider_for_model(model: str) -> str | None:
    """Return third-party provider name for ``model`` or ``None``.

    Recognizes both fully-qualified slugs (``deepseek/deepseek-v4-pro``)
    and the short aliases listed in ``MODEL_PROVIDERS``. Lifted from
    mcloop's ``_provider_for_model``.
    """
    if not model:
        return None
    if model in MODEL_PROVIDERS:
        return MODEL_PROVIDERS[model]
    for prefix in THIRD_PARTY_PREFIXES:
        if model.startswith(prefix):
            return prefix.rstrip("/")
    return None


def provider_model_slug(model: str) -> str:
    """Expand a short alias (``kimi-k2.6``) to its provider slug.

    Lifted from mcloop's ``_provider_model_slug``.
    """
    provider = MODEL_PROVIDERS.get(model)
    if provider and not model.startswith(provider + "/"):
        return f"{provider}/{model}"
    return model


def apply_provider_env(
    env: dict[str, str],
    model: str,
    executor: dict[str, Any] | None,
) -> None:
    """Mutate ``env`` with third-party provider variables for ``model``.

    No-op when ``model`` is empty or refers to a native Anthropic or
    Codex model. Lifted from mcloop's ``_apply_provider_env``.

    The ``executor`` config dict supports these keys:

    - ``base_url`` (str): provider base URL. Default OpenRouter.
    - ``auth_token_env`` (str): env var name to read the bearer token
      from. Default ``OPENROUTER_API_KEY``.
    - ``use_slug_model`` (bool): when True (default), the model name
      written into the subprocess env is prefixed with the provider
      slug (``moonshotai/kimi-k2.6``). OpenRouter requires the prefix.
      Direct provider routing (Moonshot's anthropic-compat endpoint,
      DeepSeek's anthropic-compat endpoint) requires the bare model
      name; set ``use_slug_model: False`` for those.
    - ``claude_config_dir`` (str): path written into ``CLAUDE_CONFIG_DIR``
      so the subprocess uses an isolated config dir per provider.
      ``~`` is expanded. Optional. Direct-routing bindings set this to
      ``~/.claude-kimi`` or ``~/.claude-deepseek`` to prevent
      cross-contamination of conversation history, MCP configs, and
      permissions state across providers.
    """
    if provider_for_model(model) is None:
        return
    config = executor or {}
    base_url = config.get("base_url") or DEFAULT_PROVIDER_BASE_URL
    auth_token_env = config.get("auth_token_env", "OPENROUTER_API_KEY")
    auth_token = os.environ.get(auth_token_env, "")
    use_slug_model = config.get("use_slug_model", True)
    slug = provider_model_slug(model) if use_slug_model else model
    env["ANTHROPIC_BASE_URL"] = base_url
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    env["ANTHROPIC_API_KEY"] = ""
    env["ANTHROPIC_MODEL"] = slug
    env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = slug
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = slug
    env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = slug
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = slug
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["ENABLE_TOOL_SEARCH"] = "1"
    claude_config_dir = config.get("claude_config_dir")
    if claude_config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(str(claude_config_dir))


def build_session_env(
    *,
    task_label: str = "",
    cli: str = "claude",
    model: str | None = None,
    executor_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build a minimal environment for a CLI subprocess.

    Mirrors mcloop's ``_build_session_env`` plus the per-model provider
    routing applied at command-build time in mcloop's ``_build_command``.
    Reads ``~/.mcloop/config.json`` for billing mode (``api`` or
    ``openrouter``). Applies third-party provider env when the model
    resolves to a non-native slug.
    """
    env = {k: v for k, v in os.environ.items() if k in PASSTHROUGH_VARS}
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    config = _load_mcloop_config()
    billing = config.get("billing")
    if billing == "api":
        key_name = BILLING_KEY.get(cli, "")
        if key_name and key_name in os.environ:
            env[key_name] = os.environ[key_name]
    elif billing == "openrouter":
        env["ANTHROPIC_BASE_URL"] = DEFAULT_PROVIDER_BASE_URL
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if or_key:
            env["ANTHROPIC_AUTH_TOKEN"] = or_key
        env["ANTHROPIC_API_KEY"] = ""
    if model:
        # Mirror mcloop's runner: when the caller has not supplied an
        # explicit executor_config, fall back to the executor section
        # of ~/.mcloop/config.json so DeepSeek, Kimi, OpenRouter, and
        # any other provider routing the user has configured actually
        # takes effect on the env passed to the subprocess.
        provider_cfg = (
            executor_config if executor_config is not None else load_role_config("executor")
        )
        apply_provider_env(env, model, provider_cfg)
    return env


# --------------------------------------------------------------------
# Process lifecycle helpers (PID file + watchdog) lifted from
# mcloop's ``_run_session``
# --------------------------------------------------------------------


def _publish_active_pid(cwd: Path, pid: int, pgid: int, cmd: list[str]) -> Path:
    pid_dir = cwd / ".mcloop"
    pid_dir.mkdir(exist_ok=True)
    pid_file = pid_dir / "active-pid"
    pid_file.write_text(
        _json.dumps(
            {
                "pid": pid,
                "pgid": pgid,
                "cmd": shlex.join(cmd),
                "started": datetime.now().isoformat(),
            }
        )
        + "\n"
    )
    return pid_file


def _start_watchdog(parent_pid: int, pgid: int, pid_file: Path) -> subprocess.Popen[bytes]:
    """Spawn a tiny shell watchdog that kills the inner CLI's process
    group when mcloop's PID disappears, then removes the PID file.

    Survives ``kill -9`` on the parent because it lives in its own
    session. Polls every two seconds. Lifted from mcloop's runner.
    """
    return subprocess.Popen(
        [
            "sh",
            "-c",
            f"while kill -0 {parent_pid} 2>/dev/null; do sleep 2; done; "
            f"kill -9 -{pgid} 2>/dev/null; "
            f"rm -f {shlex.quote(str(pid_file))}",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _kill_watchdog(watchdog: subprocess.Popen[bytes] | None) -> None:
    if watchdog is None:
        return
    try:
        watchdog.kill()
        watchdog.wait()
    except OSError:
        pass


def _remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------
# Output capture
# --------------------------------------------------------------------


def extract_final_text(stream_json_output: str) -> str:
    """Pull the assistant's final text out of a stream-json transcript.

    Claude Code is invoked with ``--output-format stream-json --verbose
    --include-partial-messages``, which emits one JSON record per
    line: hook events, init events, message_start, content_block_delta
    deltas, content_block_stop, message_stop, rate_limit events, and
    finally a ``{"type":"result","subtype":"success",...}`` record
    whose ``result`` field carries the final assistant text.

    Resolution order:

    1. The most recent record with ``type == "result"`` and a
       string-typed ``result`` field. Wins when the run completed
       cleanly and Claude Code emitted the canonical summary record.
    2. Concatenated ``text_delta`` text fields from every
       ``content_block_delta`` event in order. Used when the run
       crashed mid-stream and never produced a result record but did
       emit incremental text.
    3. The raw ``stream_json_output`` unchanged. Last resort when the
       output is not stream-json at all (e.g. an early subprocess
       failure that printed a traceback before any JSON).
    """
    if not stream_json_output:
        return ""
    last_result_text: str | None = None
    deltas: list[str] = []
    saw_stream_json_record = False
    for line in stream_json_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        saw_stream_json_record = True
        rtype = record.get("type")
        if rtype == "result":
            result = record.get("result")
            # Empty result.result is treated as "no result" and falls
            # through to the text_delta fallback. Some Claude Code
            # vendors (e.g. kimi via moonshot/Parasail) emit
            # result.result == "" when the response includes thinking
            # blocks; the actual answer text only reaches the consumer
            # via content_block_delta events.
            if isinstance(result, str) and result:
                last_result_text = result
        elif rtype == "stream_event":
            event = record.get("event")
            if isinstance(event, dict) and event.get("type") == "content_block_delta":
                delta = event.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str):
                        deltas.append(text)
        elif rtype == "content_block_delta":
            # Some Claude Code versions emit the delta record at the
            # top level instead of wrapping it in a stream_event. Treat
            # both shapes the same.
            delta = record.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    deltas.append(text)
    if last_result_text is not None:
        return last_result_text
    if deltas:
        return "".join(deltas)
    # Neither a result record nor text deltas appeared. If the output WAS
    # stream-json (we parsed at least one record, e.g. only a session-init or
    # other control record), the run produced no answer text -- return empty so
    # CLI control chrome (a {"type":"system","subtype":"init",...} handshake)
    # never leaks downstream as if it were the model's answer. Only when the
    # output was not stream-json at all (a traceback before any JSON) do we fall
    # back to the raw text, which is the genuine debugging last resort.
    if saw_stream_json_record:
        return ""
    return stream_json_output


def write_log(
    log_dir: Path,
    task_text: str,
    cmd: list[str],
    output: str,
    exit_code: int,
    *,
    state_id: str | None = None,
    attempt: int | None = None,
) -> Path:
    """Persist a captured session to ``log_dir`` and return the path.

    Format mirrors mcloop's ``_write_log`` so existing tooling reads
    both. The slug is bounded to 50 characters to keep filenames sane.

    Filenames include ``state_id`` and ``attempt`` when supplied so
    concurrent fan-out children sharing a task_label (and therefore a
    slug) and finishing in the same wall-clock second do not collide
    on the same path. The (state_id, attempt) pair is unique per
    invocation within a run, and the timestamp prefix discriminates
    across runs. A monotonic nanosecond suffix is appended as a final
    tiebreaker for the rare case where two distinct adapters land on
    the same (timestamp, state_id, attempt) tuple (e.g., manual
    write_log calls in tests, or two different adapters writing for
    the same state).
    """
    # Pass-9 fix: transcript files are debug convenience; they hold
    # raw model stdout/stderr including any secret, customer data,
    # internal doc excerpt, or tool output the model emitted. The
    # default umask 022 leaves them 0644 on a multi-user POSIX host,
    # readable by every other local user. Tighten the directory
    # tree to 0700 and the file to 0600. Mirrors the pass-8
    # discipline that locked down the run directory and prompt
    # snapshots.
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        log_dir.chmod(0o700)
    except OSError:
        pass
    # Tighten the .mcloop parent too when log_dir lives under it.
    # The convention is project_dir/.mcloop/logs; chmod that parent
    # so a stale 0755 left over from earlier mcloop runs is closed.
    parent = log_dir.parent
    if parent.name == ".mcloop":
        try:
            parent.chmod(0o700)
        except OSError:
            pass
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(task_text)
    parts: list[str] = [timestamp, slug]
    if state_id is not None:
        parts.append(_slugify(state_id, max_length=40))
    if attempt is not None:
        parts.append(f"a{int(attempt)}")
    parts.append(str(time.monotonic_ns()))
    log_path = log_dir / ("_".join(parts) + ".log")
    log_path.write_text(
        f"Task: {task_text}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Exit code: {exit_code}\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
    try:
        log_path.chmod(0o600)
    except OSError:
        pass
    return log_path


def _assemble(
    head_lines: list[str],
    tail_lines: deque[str],
    dropped: int,
) -> str:
    if not tail_lines:
        return "".join(head_lines)
    if dropped == 0:
        return "".join(head_lines) + "".join(tail_lines)
    marker = (
        f"\n... [truncated {dropped} line(s) "
        f"between head ({_MAX_HEAD_LINES}) and tail "
        f"({_MAX_TAIL_LINES})] ...\n"
    )
    return "".join(head_lines) + marker + "".join(tail_lines)


def _slugify(text: str, *, max_length: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:max_length]


# --------------------------------------------------------------------
# Session runner (mcloop's _run_session, faithful)
# --------------------------------------------------------------------


_last_output_lines: collections.deque[str] = collections.deque(maxlen=20)


@dataclass
class SessionState:
    """Per-call state for one ``run_session`` invocation.

    Each ``run_session`` call allocates one ``SessionState`` and
    registers it as the thread-local current session for the duration
    of the call. Mcloop signal handlers operate on the current session
    or on an explicitly passed handle.

    Carrying state per call avoids the module-global races where two
    in-flight sessions would overwrite each other's active process or
    where ``set_interrupted(False)`` at the top of one ``run_session``
    would clear an interrupt aimed at a different session.
    """

    process: subprocess.Popen[str] | None = None
    interrupted: bool = False
    pid_file: Path | None = None
    watchdog: subprocess.Popen[bytes] | None = None


# Thread-local "current session" pointer. Mcloop's signal handler runs
# on the same thread as run_session (signal delivery in CPython is
# main-thread only), so a thread-local fits the slice 1 serial model
# while still scoping state correctly when the same process happens to
# run multiple threads.
_current: threading.local = threading.local()


def _current_session() -> SessionState | None:
    return getattr(_current, "session", None)


def _set_current_session(s: SessionState | None) -> None:
    if s is None:
        if hasattr(_current, "session"):
            del _current.session
    else:
        _current.session = s


# --------------------------------------------------------------------
# Public lifecycle API
#
# Mcloop installs signal handlers (SIGINT and friends) that need to
# kill the inner CLI process and break out of run_session's wait loop.
# Mcloop's old runner kept ``_active_process`` and ``_interrupted`` as
# module globals on ``mcloop.runner``. With the integration the
# inner-process state lives on the orchestra side, so mcloop needs a
# stable handle into it. The five functions below are that handle.
# Orchestra is the single source of truth; mcloop's signal handlers
# call these without importing private names.
#
# Each function accepts an optional ``session`` argument. When
# omitted, it operates on the thread-local current session that
# ``run_session`` registers for the duration of its call.
# --------------------------------------------------------------------


def register_active_process(
    proc: subprocess.Popen[str], *, session: SessionState | None = None
) -> None:
    """Record ``proc`` as the running inner CLI process for ``session``.

    ``run_session`` calls this on the per-call ``SessionState`` it
    allocated. External callers may pass an explicit ``session`` to
    address a specific run, or omit it to address the thread-local
    current session.
    """
    target = session if session is not None else _current_session()
    if target is None:
        target = SessionState()
        _set_current_session(target)
    target.process = proc


def clear_active_process(*, session: SessionState | None = None) -> None:
    """Drop the active-process reference. Called by ``run_session``
    when the subprocess exits, including on timeout and exception
    paths."""
    target = session if session is not None else _current_session()
    if target is not None:
        target.process = None


def get_active_process(*, session: SessionState | None = None) -> subprocess.Popen[str] | None:
    """Return the running inner CLI process for ``session``, or
    ``None``."""
    target = session if session is not None else _current_session()
    return target.process if target is not None else None


def is_interrupted(*, session: SessionState | None = None) -> bool:
    """Return whether an external interrupt (typically a SIGINT
    handler) has asked the wait loop to bail out."""
    target = session if session is not None else _current_session()
    return target.interrupted if target is not None else False


def set_interrupted(value: bool = True, *, session: SessionState | None = None) -> None:
    """Flag the wait loop for early exit on ``session``.

    Mcloop's signal handler sets this to ``True`` on whichever session
    is currently running. ``run_session`` allocates a fresh
    ``SessionState`` per call so a later run always starts with a
    clean interrupted flag without needing an explicit reset.
    """
    target = session if session is not None else _current_session()
    if target is None:
        target = SessionState()
        _set_current_session(target)
    target.interrupted = bool(value)


def run_session(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT_S,
    *,
    silent: bool = False,
    stdin_bytes: bytes | None = None,
) -> tuple[str, int]:
    """Run ``cmd`` in ``cwd``, stream output, return ``(output, exit_code)``.

    Mirrors mcloop's ``_run_session``: launches the inner CLI in its
    own session, publishes ``.mcloop/active-pid``, spawns a watchdog,
    streams stdout (with stderr merged), polls ``.mcloop/pending`` for
    Telegram approvals, returns exit 1 on a ``denied`` file, returns
    exit -2 and kills the process group on timeout, and bounds the
    captured output with a head-plus-tail buffer.

    Allocates a per-call ``SessionState`` and registers it as the
    thread-local current session for the duration of the run so the
    public signal API (``set_interrupted``, ``get_active_process``)
    operates on this session by default. The previously-current
    session is restored on exit.

    ``silent`` suppresses the four user-facing prints (progress dots,
    permission-denied banner, Telegram-waiting banner). Control flow
    is unchanged. Adapters consumed by structured callers (the
    orchestra REPL, McLoop's invoke_code_edit) pass ``silent=True``
    so progress noise does not bleed into captured output.

    ``stdin_bytes`` (when provided) is written to the subprocess's
    stdin and the pipe is closed. Adapters use this to feed the
    rendered prompt to the inner CLI without putting the prompt text
    in argv, where it would otherwise leak into ``ps`` output, the
    .mcloop/active-pid file, transcript logs, and the prepare()
    summary. Callers that have no prompt (mock adapters, codex
    smoke probes) can leave it ``None`` and the stdin pipe falls
    back to /dev/null.
    """
    session = SessionState()
    previous = _current_session()
    _set_current_session(session)
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=(subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    register_active_process(process, session=session)
    _last_output_lines.clear()
    _clear_current_activity()
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = process.pid
    pid_file: Path = _publish_active_pid(cwd, process.pid, pgid, cmd)
    session.pid_file = pid_file
    watchdog: subprocess.Popen[bytes] | None = _start_watchdog(os.getpid(), pgid, pid_file)
    session.watchdog = watchdog

    if process.stdout is None:
        _kill_watchdog(watchdog)
        _remove_pid_file(pid_file)
        clear_active_process(session=session)
        _set_current_session(previous)
        raise RuntimeError("stdout is None despite stdout=PIPE")

    line_q: queue.Queue[Any] = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line_q.put(line)
        except Exception:
            # Decoding errors, closed pipe, etc. should not strand the
            # main loop waiting for a SENTINEL that will never arrive.
            pass
        finally:
            line_q.put(_SENTINEL)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # Feed stdin from a dedicated thread that runs concurrently with the
    # reader above. Writing the whole prompt inline before the reader
    # started could deadlock: a prompt larger than the OS pipe buffer
    # blocks the parent on write() while the child blocks writing to its
    # own full stdout pipe, and no timeout in the loop below could break
    # it because the loop had not been reached yet.
    if stdin_bytes is not None and process.stdin is not None:
        stdin_stream = process.stdin
        stdin_text = stdin_bytes.decode("utf-8")

        def _writer() -> None:
            try:
                stdin_stream.write(stdin_text)
                stdin_stream.flush()
            except (BrokenPipeError, OSError):
                # The CLI may close stdin early after consuming the
                # prompt header. That is not an error condition; drop
                # the remainder and let the regular timeout/output path
                # finish.
                pass
            finally:
                try:
                    stdin_stream.close()
                except (BrokenPipeError, OSError):
                    pass

        threading.Thread(target=_writer, daemon=True).start()

    head_lines: list[str] = []
    tail_lines: deque[str] = deque(maxlen=_MAX_TAIL_LINES)
    dropped = 0

    pending_dir = cwd / ".mcloop" / "pending"
    shown_waiting = False
    last_dot = time.monotonic()
    started = time.monotonic()
    last_event_time = time.monotonic()

    try:
        while True:
            if is_interrupted(session=session):
                try:
                    os.killpg(os.getpgid(process.pid), 9)
                except OSError:
                    process.kill()
                process.wait()
                return _assemble(head_lines, tail_lines, dropped), 130
            if timeout and (time.monotonic() - started) > timeout:
                try:
                    os.killpg(os.getpgid(process.pid), 9)
                except OSError:
                    process.kill()
                process.wait()
                return _assemble(head_lines, tail_lines, dropped), -2
            if (time.monotonic() - last_event_time) > IDLE_TIMEOUT_S:
                if not silent:
                    elapsed_min = (time.monotonic() - last_event_time) / 60.0
                    print(
                        f"\n!!! No stream activity for {elapsed_min:.1f} min "
                        f"(IDLE_TIMEOUT_S={IDLE_TIMEOUT_S:.0f}s). Killing session.",
                        flush=True,
                    )
                try:
                    os.killpg(os.getpgid(process.pid), 9)
                except OSError:
                    process.kill()
                process.wait()
                return _assemble(head_lines, tail_lines, dropped), -3
            # Bailout: if the reader thread is gone AND no buffered
            # lines remain AND the subprocess has exited, return now.
            # Covers the case where the reader crashed in the for-line
            # loop and never queued a SENTINEL.
            if not reader_thread.is_alive() and line_q.empty() and process.poll() is not None:
                exit_code = process.returncode if process.returncode is not None else 1
                return _assemble(head_lines, tail_lines, dropped), exit_code
            try:
                line = line_q.get(timeout=PROGRESS_QUEUE_INTERVAL)
            except queue.Empty:
                # Silence. Check for pending approvals.
                if pending_dir.exists():
                    denied_file = pending_dir / "denied"
                    if denied_file.exists():
                        try:
                            reason = denied_file.read_text()[:200]
                        except OSError:
                            reason = "unknown"
                        try:
                            denied_file.unlink(missing_ok=True)
                        except OSError:
                            pass
                        if not silent:
                            print(
                                f"\n!!! Permission denied, killing session: {reason}",
                                flush=True,
                            )
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait()
                        return _assemble(head_lines, tail_lines, dropped), 1
                    if not shown_waiting:
                        try:
                            pending = list(pending_dir.iterdir())
                        except OSError:
                            pending = []
                        if pending:
                            count = len(pending)
                            try:
                                desc = pending[0].read_text()[:80]
                            except OSError:
                                desc = "unknown"
                            extra = f" ({count} pending)" if count > 1 else ""
                            if not silent:
                                print(
                                    f"\n>>> Waiting for Telegram approval{extra}\n    {desc}",
                                    flush=True,
                                )
                            shown_waiting = True
                            continue
                now = time.monotonic()
                if now - last_dot >= PROGRESS_DOT_INTERVAL:
                    if not silent:
                        print(".", end="", flush=True)
                    last_dot = now
                # Liveness check: if the subprocess has exited but the
                # reader thread is stuck (stdout buffer never returned
                # EOF — can happen when the CLI dies early), force-
                # close stdout to unblock the reader and bail out with
                # whatever we collected. Without this the main loop
                # waits forever for a SENTINEL that never arrives.
                if process.poll() is not None:
                    try:
                        if process.stdout is not None:
                            process.stdout.close()
                    except OSError:
                        pass
                    reader_thread.join(timeout=2.0)
                    exit_code = process.returncode if process.returncode is not None else 1
                    return _assemble(head_lines, tail_lines, dropped), exit_code
                continue
            last_event_time = time.monotonic()
            if line is _SENTINEL:
                break
            if len(head_lines) < _MAX_HEAD_LINES:
                head_lines.append(line)
            else:
                if len(tail_lines) == _MAX_TAIL_LINES:
                    dropped += 1
                tail_lines.append(line)
            _last_output_lines.append(line.rstrip("\n"))
            _record_activity_from_line(line)
            shown_waiting = False
            now = time.monotonic()
            if now - last_dot >= PROGRESS_DOT_INTERVAL:
                if not silent:
                    print(".", end="", flush=True)
                last_dot = now

        reader_thread.join(timeout=5)
        process.wait()
        return _assemble(head_lines, tail_lines, dropped), process.returncode
    finally:
        _kill_watchdog(watchdog)
        _remove_pid_file(pid_file)
        clear_active_process(session=session)
        _set_current_session(previous)
        _clear_current_activity()
