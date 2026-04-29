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

DEFAULT_TIMEOUT_S: int = 1800
"""Wall-clock cap matching mcloop's ``DEFAULT_TASK_TIMEOUT``."""

PROGRESS_QUEUE_INTERVAL: float = 3.0
"""How long the reader thread blocks before checking the timeout."""

PROGRESS_DOT_INTERVAL: float = 3.0
"""Seconds between progress dots when the inner process is silent."""

_MAX_HEAD_LINES = 5_000
_MAX_TAIL_LINES = 45_000
_SENTINEL = object()

_MCLOOP_CONFIG_PATH: Path = Path.home() / ".mcloop" / "config.json"


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


def load_role_config(
    role: str, source: dict[str, Any] | None = None
) -> dict[str, Any] | None:
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
    """
    if provider_for_model(model) is None:
        return
    config = executor or {}
    base_url = config.get("base_url") or DEFAULT_PROVIDER_BASE_URL
    auth_token_env = config.get("auth_token_env", "OPENROUTER_API_KEY")
    auth_token = os.environ.get(auth_token_env, "")
    slug = provider_model_slug(model)
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
            executor_config
            if executor_config is not None
            else load_role_config("executor")
        )
        apply_provider_env(env, model, provider_cfg)
    return env


# --------------------------------------------------------------------
# Process lifecycle helpers (PID file + watchdog) lifted from
# mcloop's ``_run_session``
# --------------------------------------------------------------------


def _publish_active_pid(
    cwd: Path, pid: int, pgid: int, cmd: list[str]
) -> Path:
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


def write_log(
    log_dir: Path,
    task_text: str,
    cmd: list[str],
    output: str,
    exit_code: int,
) -> Path:
    """Persist a captured session to ``log_dir`` and return the path.

    Format mirrors mcloop's ``_write_log`` so existing tooling reads
    both. The slug is bounded to 50 characters to keep filenames sane.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(task_text)
    log_path = log_dir / f"{timestamp}_{slug}.log"
    log_path.write_text(
        f"Task: {task_text}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Exit code: {exit_code}\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
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


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


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


def clear_active_process(
    *, session: SessionState | None = None
) -> None:
    """Drop the active-process reference. Called by ``run_session``
    when the subprocess exits, including on timeout and exception
    paths."""
    target = session if session is not None else _current_session()
    if target is not None:
        target.process = None


def get_active_process(
    *, session: SessionState | None = None
) -> subprocess.Popen[str] | None:
    """Return the running inner CLI process for ``session``, or
    ``None``."""
    target = session if session is not None else _current_session()
    return target.process if target is not None else None


def is_interrupted(*, session: SessionState | None = None) -> bool:
    """Return whether an external interrupt (typically a SIGINT
    handler) has asked the wait loop to bail out."""
    target = session if session is not None else _current_session()
    return target.interrupted if target is not None else False


def set_interrupted(
    value: bool = True, *, session: SessionState | None = None
) -> None:
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
    """
    session = SessionState()
    previous = _current_session()
    _set_current_session(session)
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    register_active_process(process, session=session)
    _last_output_lines.clear()
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = process.pid
    pid_file: Path = _publish_active_pid(cwd, process.pid, pgid, cmd)
    session.pid_file = pid_file
    watchdog: subprocess.Popen[bytes] | None = _start_watchdog(
        os.getpid(), pgid, pid_file
    )
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
        for line in process.stdout:
            line_q.put(line)
        line_q.put(_SENTINEL)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    head_lines: list[str] = []
    tail_lines: deque[str] = deque(maxlen=_MAX_TAIL_LINES)
    dropped = 0

    pending_dir = cwd / ".mcloop" / "pending"
    shown_waiting = False
    last_dot = time.monotonic()
    started = time.monotonic()

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
                            print(
                                f"\n>>> Waiting for Telegram approval{extra}\n    {desc}",
                                flush=True,
                            )
                            shown_waiting = True
                            continue
                now = time.monotonic()
                if now - last_dot >= PROGRESS_DOT_INTERVAL:
                    print(".", end="", flush=True)
                    last_dot = now
                continue
            if line is _SENTINEL:
                break
            if len(head_lines) < _MAX_HEAD_LINES:
                head_lines.append(line)
            else:
                if len(tail_lines) == _MAX_TAIL_LINES:
                    dropped += 1
                tail_lines.append(line)
            _last_output_lines.append(line.rstrip("\n"))
            shown_waiting = False
            now = time.monotonic()
            if now - last_dot >= PROGRESS_DOT_INTERVAL:
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
