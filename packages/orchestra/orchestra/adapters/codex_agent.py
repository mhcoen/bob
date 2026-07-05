"""Edit-agent adapter backed by the Codex CLI in workspace-mutating mode.

Used for the workspace-mutating invocation in code-edit workflows when
the user has bound the agent role to Codex. Mirrors
``ClaudeCodeAgentAdapter`` in shape but invokes Codex with
``--ask-for-approval never --sandbox workspace-write`` so the
subprocess can edit files inside the project directory without
prompting. The default sandbox value is ``workspace-write``; the
analogue of Claude Code's ``--allowedTools`` override flows through the
``default_sandbox`` constructor argument and ``backing_options.sandbox``
per call.

Subprocess invocation patterns (env passthrough including the
``OPENAI_API_KEY`` mapping for ``cli="codex"`` billing, stream capture,
log file format, watchdog, timeout) are lifted from
``mcloop/runner.py`` via ``orchestra.adapters._subprocess``. The
``changed_files`` detection helper is shared with
``ClaudeCodeAgentAdapter``.

The backing name registered on the registry is ``codex_agent``. The
api layer aliases the workflow's logical actor kind (``agent``) to this
backing per the project config.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from orchestra.adapters._subprocess import (
    DEFAULT_TIMEOUT_S,
    build_session_env,
    run_session,
    timeout_s_from_ms,
    verdict_for_exit_code,
    write_log,
)
from orchestra.adapters.claude_code_agent import _detect_changed_files
from orchestra.spine import InvocationRequest, PreparedInvocation

DEFAULT_SANDBOX: str = "workspace-write"
"""Codex sandbox mode that permits in-tree edits without prompting.

Maps to the ``--sandbox`` flag and is the closest analogue of
``ClaudeCodeAgentAdapter``'s ``DEFAULT_ALLOWED_TOOLS``. Other valid
values include ``read-only`` and ``danger-full-access``."""


class CodexAgentAdapter:
    """Adapter for the ``codex_agent`` backing.

    Per-call overrides flow through ``InvocationRequest.actor_binding``
    (model id), ``backing_options`` (sandbox override, task_label,
    project_dir, log_dir), and ``external_inputs`` (project_dir,
    log_dir, task_label).
    """

    backing: str = "codex_agent"
    WORKSPACE_MUTATION: str = "mutating"
    """The agent variant runs codex with workspace-write sandbox, so
    an invocation may edit files and run shell commands. PRJI binds
    only the implementer role to a mutating adapter."""

    manages_own_timeout: bool = True
    """``run_session`` enforces a wall-clock timeout and returns
    exit_code -2 on expiry. The executor honors this flag so it does
    not impose a second timer on top, which would race the adapter
    and discard the structured -2 payload."""

    def __init__(
        self,
        *,
        cli: str = "codex",
        default_model: str | None = None,
        default_sandbox: str = DEFAULT_SANDBOX,
        default_timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._cli = cli
        self._default_model = default_model
        self._default_sandbox = default_sandbox
        self._default_timeout_s = default_timeout_s

    # ----- Adapter contract -------------------------------------------

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        ext = request.external_inputs or {}
        backing = request.backing_options or {}

        model = backing.get("model_override") or self._default_model or binding.get("model")
        sandbox = str(backing.get("sandbox") or self._default_sandbox)
        project_dir = Path(backing.get("project_dir") or ext.get("project_dir") or os.getcwd())
        log_dir = Path(
            backing.get("log_dir") or ext.get("log_dir") or project_dir / ".mcloop" / "logs"
        )
        task_label = str(backing.get("task_label") or ext.get("task_label") or "")
        timeout_s = timeout_s_from_ms(request.timeout_ms, self._default_timeout_s)

        cmd = self._build_command(model, sandbox)
        env = build_session_env(task_label=task_label, cli=self._cli, model=model)

        prompt_bytes = prompt.encode("utf-8") if prompt else b""
        prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest() if prompt_bytes else ""
        return PreparedInvocation(
            request=request,
            summary={
                "kind": "agent",
                "adapter": self.backing,
                "cli": self._cli,
                "model": model,
                "sandbox": sandbox,
                "command": cmd,
                "cwd": str(project_dir),
                "log_dir": str(log_dir),
                "timeout_s": timeout_s,
                "prompt_chars": len(prompt),
                "prompt_sha256": prompt_sha256,
            },
            inner={
                "cmd": cmd,
                "env": env,
                "cwd": project_dir,
                "log_dir": log_dir,
                "timeout_s": timeout_s,
                "task_label": task_label or self.backing,
                "project_dir": project_dir,
                "prompt_bytes": prompt_bytes,
            },
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        inner = prepared.inner
        prompt_bytes_raw = inner.get("prompt_bytes")
        stdin_arg: bytes | None
        if isinstance(prompt_bytes_raw, bytes) and prompt_bytes_raw:
            stdin_arg = prompt_bytes_raw
        else:
            stdin_arg = None
        output, exit_code = run_session(
            inner["cmd"],
            inner["cwd"],
            env=inner["env"],
            timeout=int(inner["timeout_s"]),
            silent=True,
            stdin_bytes=stdin_arg,
        )
        log_path = write_log(
            inner["log_dir"],
            inner["task_label"],
            inner["cmd"],
            output,
            exit_code,
            state_id=prepared.request.state_id,
            attempt=prepared.request.attempt,
        )
        changed = _detect_changed_files(inner["project_dir"])
        # Codex emits the final assistant text on stdout (not
        # stream-json), so the captured output is the answer. Pass it
        # through unchanged.
        verdict = verdict_for_exit_code(exit_code)
        return {
            "output": output,
            "verdict": verdict,
            "fields": {
                "exit_code": exit_code,
                "log_path": str(log_path),
                "changed_files": changed,
            },
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
            "transcript_ref": str(log_path),
        }

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": self.backing,
            "kind": "subprocess",
            "cli": self._cli,
            "sandbox": self._default_sandbox,
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": "mutating",
        }

    # ----- internals --------------------------------------------------

    def _build_command(self, model: str | None, sandbox: str) -> list[str]:
        # The Codex CLI accepts ``--ask-for-approval`` and ``--sandbox``
        # at the top level only. Both flags must appear before the
        # ``exec`` subcommand or argument parsing rejects them.
        # ``--skip-git-repo-check`` is an ``exec`` subcommand flag in
        # codex 0.128 (verified via ``codex exec --help``); it must
        # follow ``exec``. Without it, codex refuses to run in any
        # directory it does not consider a trusted git repository
        # and exits with status 1 before contacting the model.
        cmd: list[str] = [
            self._cli,
            "--ask-for-approval",
            "never",
            "--sandbox",
            sandbox,
            "exec",
            "--skip-git-repo-check",
        ]
        if model:
            cmd.extend(["--model", model])
        # Pass-7 fix: prompt no longer in argv; the inner CLI reads
        # it from stdin so it cannot leak through ps output, the
        # .mcloop/active-pid file, transcript logs, or the prepare()
        # summary.
        return cmd


def register(registry: Any, *, default_model: str | None = None) -> None:
    """Register ``CodexAgentAdapter`` under ``codex_agent``.

    Idempotent: if the backing is already registered, this is a no-op.
    """
    if "codex_agent" in registry.actor_backings:
        return
    registry.register_actor_backing(
        "codex_agent",
        lambda: CodexAgentAdapter(default_model=default_model),
    )
