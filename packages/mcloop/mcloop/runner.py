"""Run AI CLI subprocesses and capture output."""

from __future__ import annotations

import collections
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mcloop.prompts import (
    build_audit_prompt,
    build_bug_fix_prompt,
    build_diagnostic_prompt,
    build_post_fix_review_prompt,
    build_sync_prompt,
)

DEFAULT_TASK_TIMEOUT = 1800  # 30 minutes; override with --timeout


@dataclass
class RunResult:
    success: bool
    output: str
    exit_code: int
    log_path: Path


INVESTIGATION_TOOLS = "Edit,Write,Bash,Read,Glob,Grep,WebFetch,WebSearch"

# Minimal set of environment variables passed to CLI subprocesses.
# Everything else (API keys, cloud credentials, tokens) is excluded.
_PASSTHROUGH_VARS = frozenset(
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


# Map from CLI name to the environment variable that controls
# whether the CLI bills via API key or subscription.
_BILLING_KEY = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
}


_KNOWN_MODELS = {
    "claude": frozenset(
        {
            "opus",
            "sonnet",
            "haiku",
            "opusplan",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-opus-4-5-20251101",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5-20251001",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "kimi-k2.6",
        }
    ),
    "codex": frozenset(
        {
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
            "gpt-5.2-codex",
            "gpt-5.2",
            "gpt-5.1-codex-max",
            "gpt-5.1-codex",
            "gpt-5-codex",
            "gpt-5-codex-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
        }
    ),
}

# Map short third-party model aliases to their OpenRouter provider
# prefix.  Used by _build_command so a user can write
# ``--model deepseek-v4-pro`` and still get the right provider env
# vars without typing the full ``deepseek/deepseek-v4-pro`` slug.
_MODEL_PROVIDERS = {
    "deepseek-v4-pro": "deepseek",
    "deepseek-v4-flash": "deepseek",
    "kimi-k2.6": "moonshotai",
}

_THIRD_PARTY_PREFIXES = ("deepseek/", "moonshotai/", "openai/")


def warn_unknown_model(cli: str, model: str) -> None:
    """Print a warning if model is not in the known-good list for cli."""
    known = _KNOWN_MODELS.get(cli, frozenset())
    if known and model not in known:
        print(
            f'Warning: model "{model}" not recognized for {cli} (may still work)',
            flush=True,
        )


_DEFAULT_PROVIDER_BASE_URL = "https://openrouter.ai/api"


def _provider_for_model(model: str) -> str | None:
    """Return third-party provider name for *model*, or None.

    Recognizes both fully-qualified slugs (``deepseek/deepseek-v4-pro``)
    and the short aliases listed in :data:`_MODEL_PROVIDERS`.
    """
    if not model:
        return None
    if model in _MODEL_PROVIDERS:
        return _MODEL_PROVIDERS[model]
    for prefix in _THIRD_PARTY_PREFIXES:
        if model.startswith(prefix):
            return prefix.rstrip("/")
    return None


def _provider_model_slug(model: str) -> str:
    """Expand a short alias (``kimi-k2.6``) to its provider slug."""
    provider = _MODEL_PROVIDERS.get(model)
    if provider and not model.startswith(provider + "/"):
        return f"{provider}/{model}"
    return model


def _apply_provider_env(
    env: dict[str, str],
    model: str,
    executor: dict | None,
) -> None:
    """Mutate *env* with third-party provider variables for *model*.

    No-op when *model* is empty or refers to a native Anthropic/Codex
    model.  When triggered, sets ``ANTHROPIC_BASE_URL``, the model
    routing variables (``ANTHROPIC_MODEL``, the
    ``ANTHROPIC_DEFAULT_*_MODEL`` family, and
    ``CLAUDE_CODE_SUBAGENT_MODEL``), the no-noise traffic toggle, and
    ``ENABLE_TOOL_SEARCH``.  Reads the auth token from the env var
    named in ``executor.auth_token_env`` (default
    ``OPENROUTER_API_KEY``) and sets ``ANTHROPIC_API_KEY`` to empty
    so the underlying CLI does not bill against the wrong account.
    """
    if _provider_for_model(model) is None:
        return
    config = executor or {}
    base_url = config.get("base_url") or _DEFAULT_PROVIDER_BASE_URL
    auth_token_env = config.get("auth_token_env", "OPENROUTER_API_KEY")
    auth_token = os.environ.get(auth_token_env, "")
    slug = _provider_model_slug(model)
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


def _build_session_env(
    task_label: str = "",
    cli: str = "claude",
) -> dict[str, str]:
    """Build a minimal environment for CLI subprocesses.

    Includes only variables from _PASSTHROUGH_VARS. If the config
    has '"billing": "api"', the appropriate API key for the active
    CLI is also included so the CLI uses API credits instead of the
    subscription. Credentials are excluded by default.
    """
    from mcloop.install_cmd import _load_mcloop_config

    env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_VARS}
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    config = _load_mcloop_config()
    billing = config.get("billing")
    if billing == "api":
        key_name = _BILLING_KEY.get(cli, "")
        if key_name and key_name in os.environ:
            env[key_name] = os.environ[key_name]
    elif billing == "openrouter":
        env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if or_key:
            env["ANTHROPIC_AUTH_TOKEN"] = or_key
        env["ANTHROPIC_API_KEY"] = ""
    return env


def _build_shared_parts(
    task_text: str,
    task_label: str,
    check_commands: list[str] | None,
) -> list[str]:
    """Return prompt parts shared by both normal and bug-investigation variants."""
    parts = []
    parts.append("Do not chain shell commands with && or ;. Use separate Bash calls instead.")
    parts.append(
        "Never set, unset, or override environment variables"
        " in Bash commands. Do not use VAR=value command,"
        " env -u, unset, or export. The environment is"
        " controlled by the orchestrator."
    )
    parts.append(
        "Never run destructive commands like rm -rf,"
        " sudo rm, mkfs, or dd, even for testing."
        " Test dangerous behavior with mocks, not"
        " live commands. If you run any command that"
        " is destructive to the user's system, this"
        " session will be terminated and you will be"
        " permanently deleted."
    )
    parts.append(
        "Never delete any file. Do not use rm, git rm,"
        " os.remove, unlink, shutil.rmtree, or any"
        " other file deletion mechanism. Do not delete"
        " PLAN.md, CLAUDE.md, NOTES.md, or any other"
        " project file under any circumstances. If you"
        " believe a file should be removed, leave it"
        " and note it in NOTES.md for the user to"
        " decide."
    )
    if check_commands:
        cmds = ", ".join(check_commands)
        parts.append(
            "CHECK COMMANDS (mandatory, strict rules):\n"
            f"Commands: {cmds}\n"
            "1. Run each check command EXACTLY ONCE before finishing.\n"
            "2. Run the command exactly as listed. Do not append"
            " | tail, | head, or any pipe. Do not truncate output."
            " Do not modify the command in any way.\n"
            "3. If a check fails, fix the issue, then re-run that"
            " same exact check command ONCE.\n"
            "4. Maximum 3 total runs of any single check command."
            " If it still fails after 3 runs, stop and report"
            " what is failing.\n"
            "5. NEVER run the same check command twice in a row"
            " without making a code change between runs."
            " Re-running a passing test is forbidden.\n"
            "6. ONLY run the exact check commands listed above."
            " Do not run subsets, individual test files, or"
            " any variation. Do not run pytest on a single file."
            " Do not run any test command other than the ones"
            " listed here. The orchestrator runs its own"
            " verification after this session ends."
        )
    # rtk instruction disabled 2026-04-15: rtk is disabled in the
    # mcloop hook (telegram-permission-hook.py), so instructing the
    # inner Claude to use rtk would be misleading.
    # if shutil.which("rtk"):
    #     rtk_instruction = _build_rtk_instruction()
    #     if rtk_instruction:
    #         parts.append(rtk_instruction)
    parts.append(
        "Do not remove or modify code between"
        " mcloop:wrap markers (e.g. `// mcloop:wrap:begin`"
        " ... `// mcloop:wrap:end` or the Python `#`"
        " equivalents). These are auto-injected crash"
        " handlers managed by mcloop. If a task requires"
        " changes to the entry point file, work around"
        " the marked block."
    )
    parts.append(
        "If you notice edge cases, design decisions,"
        " assumptions, potential issues, or anything"
        " worth revisiting later, append a note to"
        " NOTES.md. Each entry should include the"
        " current date and reference the task:"
        f" [{task_label}] {task_text}."
        " Do not create NOTES.md if you have nothing"
        " to note."
        " NOTES.md must use three sections:"
        " ## Observations (confirmed facts from"
        " runtime, docs, logs, or experiments),"
        " ## Hypotheses (candidate explanations not"
        " yet confirmed), and ## Eliminated (things"
        " ruled out, with the experiment that ruled"
        " them out). Place each note under the"
        " appropriate section."
    )
    parts.append(
        "When building UI (SwiftUI, HTML, React, Qt,"
        " or any other UI framework), add accessibility"
        " identifiers to every interactive element"
        " (buttons, text fields, menu items, toggles,"
        " sliders, pickers, links, tabs). Use the"
        " platform-native API: .accessibilityIdentifier()"
        " in SwiftUI, data-testid in HTML/React,"
        " setAccessibleName() in Qt. This makes the"
        " app programmatically testable."
    )
    parts.append(
        "Never install tools or dependencies via brew,"
        " cargo, pip, npm, apt, or any other package"
        " manager. If a required tool is not found,"
        " report what is missing and stop. Do not"
        " search for alternative ways to obtain it."
        " The user will install it and re-run."
    )
    return parts


def _build_bug_task_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    eliminated: list[str] | None = None,
) -> str:
    """Build prompt for a first-attempt bug task from BUGS.md.

    Unlike ``_build_normal_prompt``, this tells the session that
    the described behavior is confirmed broken and code changes
    are mandatory.  A session that exits without modifying files
    will be treated as a failure by the orchestrator.
    """
    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    parts.append(
        "BUG FIX (MANDATORY CODE CHANGE): This task comes"
        " from BUGS.md. The behavior described below is"
        " confirmed broken. You MUST modify code to fix it."
        " Do not exit without making changes. If a function"
        " mentioned in the task already exists, that does not"
        " mean the bug is fixed -- read the task carefully,"
        " it describes what the function should do differently"
        " from what it does now. Exiting without file changes"
        " means you failed."
    )
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append(
        "Fix the bug with a targeted change. Write or update"
        " tests to cover the new behavior so it cannot regress."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    if eliminated:
        elim_text = "\n".join(eliminated)
        parts.append(
            "RULED OUT APPROACHES: The following approaches"
            " have already been tried for this task and"
            " failed. Do not repeat any of them. If you"
            " find yourself heading toward a ruled out"
            " approach, stop and try a fundamentally"
            " different strategy.\n" + elim_text
        )
    return "\n\n".join(parts)


def _build_normal_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    eliminated: list[str] | None = None,
) -> str:
    """Build prompt for a normal (first-attempt) task."""
    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append(
        "Write unit tests only when the change introduces"
        " non-obvious behavior or a regression risk. Trivial"
        " additions (constants, dataclass fields, simple"
        " delegations) do not need tests."
    )
    parts.append(
        "Tests must NEVER make real subprocess calls to"
        " claude, codex, or any LLM CLI. Any function"
        " that transitively invokes an LLM must be mocked."
        " Before writing a test that calls a function,"
        " check its source to see if it reaches an LLM"
        " call path. If it does, mock at the narrowest"
        " point that eliminates the real call. Real LLM"
        " round-trips cost 5-15 seconds each and will"
        " make the test suite unusably slow."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    if eliminated:
        elim_text = "\n".join(eliminated)
        parts.append(
            "RULED OUT APPROACHES: The following approaches"
            " have already been tried for this task and"
            " failed. Do not repeat any of them. If you"
            " find yourself heading toward a ruled out"
            " approach, stop and try a fundamentally"
            " different strategy.\n" + elim_text
        )
    return "\n\n".join(parts)


def _build_bug_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    prior_errors: str,
    eliminated: list[str] | None,
) -> str:
    """Build prompt for a bug-investigation task (prior_errors populated)."""
    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    # Lead with the failure context so the session focuses on it
    parts.append(
        "BUG INVESTIGATION: A previous attempt at this task"
        " failed. Your primary goal is to diagnose and fix"
        " the errors below. Read the error output carefully"
        " before reading source code. Understand the root"
        " cause before changing anything."
    )
    parts.append(f"ERRORS FROM PREVIOUS ATTEMPT:\n{prior_errors}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append(
        "When debugging crashes or unexpected"
        " behavior, always find and read the actual"
        " error output first. Check crash reports"
        " (~/Library/Logs/DiagnosticReports/ on"
        " macOS), stderr, log files, tracebacks, core"
        " dumps, or browser console errors. Read them"
        " before looking at source code. Do not guess"
        " at the cause from code inspection alone."
        " After applying a fix, find a way to"
        " reproduce the original failure and verify"
        " the fix actually works. Run the app, trigger"
        " the same condition, and confirm it no longer"
        " crashes. Compiling is not enough."
    )
    parts.append(
        "Fix the bug with a minimal, targeted change."
        " Do not refactor surrounding code. Write or"
        " update tests to cover the failure case so"
        " it cannot regress."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    if eliminated:
        elim_text = "\n".join(eliminated)
        parts.append(
            "RULED OUT APPROACHES: The following approaches"
            " have already been tried for this task and"
            " failed. Do not repeat any of them. If you"
            " find yourself heading toward a ruled out"
            " approach, stop and try a fundamentally"
            " different strategy.\n" + elim_text
        )
    return "\n\n".join(parts)


def _warn_if_orchestra_bypassed(
    project_dir: Path, *, cli: str, allowed_tools: str | None
) -> None:
    """Warn when a custom tool list or non-claude cli routes around
    orchestra even though the project configured a non-direct backend.

    Slice 1 orchestra adapters use a fixed tool set, so the legacy path
    is the right answer here. Surfacing the bypass tells the user that
    their orchestra config did not apply to this call and prevents a
    silent regression once orchestra adapters learn custom tool lists.
    """
    import sys as _sys

    try:
        from mcloop.code_edit import _select_backend
    except Exception:
        return
    try:
        backend = _select_backend(Path(project_dir))
    except Exception:
        return
    if backend != "orchestra":
        return
    reason = "custom allowed_tools" if allowed_tools else f"cli={cli!r}"
    print(
        f"[orchestra] bypassed for this call ({reason}); "
        "configured workflow did not run.",
        file=_sys.stderr,
    )


def run_task(
    task_text: str,
    cli: str,
    project_dir: str | Path,
    log_dir: str | Path,
    description: str = "",
    task_label: str = "",
    model: str | None = None,
    prior_errors: str = "",
    session_context: str = "",
    check_commands: list[str] | None = None,
    allowed_tools: str | None = None,
    eliminated: list[str] | None = None,
    timeout: int = DEFAULT_TASK_TIMEOUT,
    is_bug_task: bool = False,
) -> RunResult:
    """Launch a CLI session to perform a task. Returns RunResult."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # The single inner edit attempt now flows through the code_edit
    # wrapper, which dispatches to either the direct backend (the
    # original prompt-build + run-session + write-log sequence below
    # was lifted into mcloop.code_edit) or to orchestra when the
    # project's .orchestra/config.json selects it. The outer loop in
    # the caller (retry, rate-limit, success classification, Telegram
    # approval, watchdog) is unchanged.
    #
    # The wrapper is the dispatch path only when ``cli == "claude"``
    # and ``allowed_tools`` is left at the default. Codex sessions and
    # custom tool overrides keep the legacy inline path so callers
    # that pin those parameters get bit-for-bit prior behavior.
    if cli == "claude" and not allowed_tools:
        from mcloop.code_edit import invoke_code_edit

        ce = invoke_code_edit(
            instruction=task_text,
            context=session_context,
            prior_errors=prior_errors,
            eliminated=list(eliminated or []),
            project_dir=project_dir,
            log_dir=log_dir,
            description=description,
            task_label=task_label,
            check_commands=check_commands,
            is_bug_task=is_bug_task,
            model=model,
            timeout=timeout,
        )
        return RunResult(
            success=ce.success,
            output=ce.output,
            exit_code=ce.exit_code,
            log_path=ce.log_path,
        )

    # Legacy path. If the project has opted in to orchestra for
    # code_edit but a custom allowed_tools list (or codex cli) routed
    # us here, warn so the user does not silently lose orchestra
    # routing on a single call.
    _warn_if_orchestra_bypassed(project_dir, cli=cli, allowed_tools=allowed_tools)

    if prior_errors:
        prompt = _build_bug_prompt(
            task_text,
            description,
            task_label,
            session_context,
            check_commands,
            prior_errors,
            eliminated,
        )
    elif is_bug_task:
        prompt = _build_bug_task_prompt(
            task_text,
            description,
            task_label,
            session_context,
            check_commands,
            eliminated,
        )
    else:
        prompt = _build_normal_prompt(
            task_text,
            description,
            task_label,
            session_context,
            check_commands,
            eliminated,
        )
    build_kwargs: dict = {"model": model}
    if allowed_tools:
        build_kwargs["allowed_tools"] = allowed_tools
    session_env = _build_session_env(task_label=task_label, cli=cli)
    cmd = _build_command(cli, prompt, env=session_env, **build_kwargs)
    output, returncode = _run_session(
        cmd,
        project_dir,
        env=session_env,
        timeout=timeout,
    )
    log_path = _write_log(
        log_dir,
        task_text,
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def _build_command(
    cli: str,
    prompt: str | None = None,
    model: str | None = None,
    allowed_tools: str = "Edit,Write,Bash,Read,Glob,Grep",
    env: dict[str, str] | None = None,
) -> list[str]:
    if env is not None and cli == "claude" and model:
        from mcloop.config import load_role_config

        executor_cfg = load_role_config("executor")
        _apply_provider_env(env, model, executor_cfg)
    if cli == "claude":
        cmd = ["claude", "-p"]
        if prompt:
            cmd.append(prompt)
        cmd.extend(
            [
                "--allowedTools",
                allowed_tools,
                "--permission-mode",
                "default",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
            ]
        )
        if model:
            cmd.extend(["--model", model])
        return cmd
    elif cli == "codex":
        # Flag-ordering constraint: --ask-for-approval and --sandbox are
        # top-level codex flags. They must precede the exec subcommand.
        # The correct shape is
        #   codex --ask-for-approval <mode> --sandbox <mode> exec --model <m> <prompt>
        # NOT
        #   codex exec --ask-for-approval <mode> --sandbox <mode> ...
        # codex 0.111.0 rejects the latter with
        #   error: unexpected argument '--ask-for-approval' found
        # --full-auto is accepted on the exec subcommand and is the only
        # codex flag this branch currently emits, so this command shape
        # is fine. Pinned by orchestra's
        # tests/test_codex_agent_adapter.py::test_build_command_top_level_flags_precede_exec.
        cmd = [
            "codex",
            "exec",
            "--full-auto",
        ]
        if model:
            cmd.extend(["--model", model])
        if prompt:
            cmd.append(prompt)
        return cmd
    else:
        raise ValueError(f"Unknown CLI: {cli}")


PROGRESS_DOT_INTERVAL = 3  # seconds between progress dots
_SENTINEL = object()
_active_process = None  # type: subprocess.Popen | None
_interrupted = False
_last_output_lines: collections.deque[str] = collections.deque(maxlen=20)


def _run_session(
    cmd: list[str],
    cwd: Path,
    env: dict | None = None,
    timeout: int = DEFAULT_TASK_TIMEOUT,
) -> tuple[str, int]:
    """Run a CLI session, stream output, return (output, exit_code).

    If *timeout* seconds elapse, the process group is killed and
    exit code -2 is returned.
    """
    session_env = env if env is not None else _build_session_env()
    _last_output_lines.clear()
    global _active_process
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=session_env,
        start_new_session=True,
    )
    _active_process = process
    # Write PID file so orphans can be killed on next startup
    pid_dir = cwd / ".mcloop"
    pid_dir.mkdir(exist_ok=True)
    pid_file = pid_dir / "active-pid"
    import json as _pid_json

    start_iso = datetime.now().isoformat()
    cmd_line = shlex.join(cmd)
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = process.pid
    pid_file.write_text(
        _pid_json.dumps(
            {
                "pid": process.pid,
                "pgid": pgid,
                "cmd": cmd_line,
                "started": start_iso,
            }
        )
        + "\n"
    )
    # Watchdog: a tiny shell process that kills claude if mcloop dies.
    # Survives kill -9 on mcloop because it's in its own session.
    # Polls every 2 seconds. When mcloop's PID disappears, kills
    # claude's entire process group.
    _watchdog = subprocess.Popen(
        [
            "sh",
            "-c",
            f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 2; done; "
            f"kill -9 -{pgid} 2>/dev/null; "
            f"rm -f {shlex.quote(str(pid_file))}",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.stdout is None:
        try:
            _watchdog.kill()
            _watchdog.wait()
        except OSError:
            pass
        raise RuntimeError("stdout is None despite stdout=PIPE")

    # Read lines in a thread so the main thread
    # can check for pending approval files.
    line_q: queue.Queue = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_q.put(line)
        line_q.put(_SENTINEL)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    session_start = time.monotonic()

    # Cap output buffer to prevent unbounded memory growth.
    # A stuck claude session running checks in a loop can produce
    # millions of lines. Keep both the *head* (so the session prologue
    # — task prompt, early errors — survives) and the *tail* (so the
    # most recent output that usually reveals failure cause survives).
    # A marker line is inserted between them when truncation actually
    # happened.
    _MAX_HEAD_LINES = 5_000
    _MAX_TAIL_LINES = 45_000
    head_lines: list[str] = []
    tail_lines: collections.deque[str] = collections.deque(maxlen=_MAX_TAIL_LINES)
    dropped_count = 0

    def _assemble_output() -> str:
        if not tail_lines:
            return "".join(head_lines)
        if dropped_count == 0:
            return "".join(head_lines) + "".join(tail_lines)
        marker = (
            f"\n... [truncated {dropped_count} line(s) "
            f"between head ({_MAX_HEAD_LINES}) and tail "
            f"({_MAX_TAIL_LINES})] ...\n"
        )
        return "".join(head_lines) + marker + "".join(tail_lines)

    pending_dir = cwd / ".mcloop" / "pending"
    shown_waiting = False
    last_dot = time.monotonic()
    global _interrupted
    while True:
        if _interrupted:
            break
        # Check task timeout
        if timeout and (time.monotonic() - session_start) > timeout:
            elapsed_m = timeout / 60
            print(
                f"\n!!! Task timed out after {elapsed_m:.0f}m. Killing session.",
                flush=True,
            )
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except OSError:
                process.kill()
            process.wait()
            _active_process = None
            try:
                _watchdog.kill()
                _watchdog.wait()
            except OSError:
                pass
            try:
                (cwd / ".mcloop" / "active-pid").unlink(missing_ok=True)
            except OSError:
                pass
            return _assemble_output(), -2
        try:
            line = line_q.get(
                timeout=PROGRESS_DOT_INTERVAL,
            )
        except queue.Empty:
            if _interrupted:
                break
            # Silence. Check for pending approvals.
            if pending_dir.exists():
                # Check if a permission was denied
                denied_file = pending_dir / "denied"
                if denied_file.exists():
                    try:
                        reason = denied_file.read_text()[:200]
                    except OSError:
                        reason = "unknown"
                    denied_file.unlink(missing_ok=True)
                    print(
                        f"\n!!! Permission denied, killing session: {reason}",
                        flush=True,
                    )
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
                    _active_process = None
                    try:
                        _watchdog.kill()
                        _watchdog.wait()
                    except OSError:
                        pass
                    try:
                        (cwd / ".mcloop" / "active-pid").unlink(
                            missing_ok=True,
                        )
                    except OSError:
                        pass
                    return _assemble_output(), 1
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
            # Print a progress dot
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
                dropped_count += 1
            tail_lines.append(line)
        _last_output_lines.append(line.rstrip("\n"))
        printed_visible = _print_stream_event(line)
        shown_waiting = False
        now = time.monotonic()
        if printed_visible:
            last_dot = now
        elif now - last_dot >= PROGRESS_DOT_INTERVAL:
            print(".", end="", flush=True)
            last_dot = now

    t.join(timeout=5)
    process.wait()
    _active_process = None
    # Kill the watchdog and clean up PID file on normal exit
    try:
        _watchdog.kill()
        _watchdog.wait()
    except OSError:
        pass
    try:
        (cwd / ".mcloop" / "active-pid").unlink(missing_ok=True)
    except OSError:
        pass
    return _assemble_output(), process.returncode


# Suppress ALL tool names from stream output. Only the task
# label (">>> Task N)") and progress dots are shown.
_SUPPRESS_ALL_TOOLS = True


def _print_stream_event(line: str) -> bool:
    """Parse a stream-json line and print relevant activity.

    Returns True iff something user-visible was printed. Callers use
    this to decide whether to suppress the next progress dot.
    """
    import json as _json

    try:
        data = _json.loads(line)
    except (ValueError, TypeError):
        return False

    if data.get("type") == "assistant":
        for block in data.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                if not _SUPPRESS_ALL_TOOLS:
                    inp = block.get("input", {})
                    detail = inp.get("command", "") if name == "Bash" else ""
                    label = f"{name}: {detail}" if detail else name
                    print(f"  {label}", flush=True)
                    return True
    return False


def run_sync(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session with full project context for sync analysis."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_sync_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "sync",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_audit(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
    existing_bugs: str = "",
) -> RunResult:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_audit_prompt(existing_bugs=existing_bugs)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "audit",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_bug_verify(
    project_dir: str | Path,
    log_dir: str | Path,
    bugs_content: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only session to verify bug reports."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Same wrapper shape as run_task: dispatch to invoke_bug_verify
    # which returns a CodeEditResult, then narrow to RunResult.
    # Direct backend mirrors the legacy invocation; orchestra backend
    # is wired but raises until orchestra ships a bug_verify workflow.
    from mcloop.code_edit import invoke_bug_verify

    bv = invoke_bug_verify(
        bugs_content=bugs_content,
        project_dir=project_dir,
        log_dir=log_dir,
        model=model,
    )
    return RunResult(
        success=bv.success,
        output=bv.output,
        exit_code=bv.exit_code,
        log_path=bv.log_path,
    )


def run_post_fix_review(
    project_dir: str | Path,
    log_dir: str | Path,
    bug_descriptions: str,
    diff: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only review session on post-fix changes."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_post_fix_review_prompt(bug_descriptions, diff)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "post-fix-review",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_bug_fix(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session to fix bugs listed in BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_bug_fix_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "bug-fix",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_diagnostic(
    project_dir: str | Path,
    log_dir: str | Path,
    error_entry: dict,
    source_content: str = "",
    git_log: str = "",
    model: str | None = None,
) -> RunResult:
    """Run a read-only diagnostic session for a single error."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_diagnostic_prompt(error_entry, source_content, git_log)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
        allowed_tools="Read,Glob,Grep",
    )
    output, returncode = _run_session(cmd, project_dir)
    exc_type = error_entry.get("exception_type", "unknown")
    log_path = _write_log(
        log_dir,
        f"diagnostic-{exc_type}",
        cmd,
        output,
        returncode,
    )
    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


def _write_log(log_dir: Path, task_text: str, cmd: list[str], output: str, exit_code: int) -> Path:
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
