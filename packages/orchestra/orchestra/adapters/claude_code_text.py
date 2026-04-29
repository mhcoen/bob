"""Text-role adapter backed by Claude Code with read-only tools.

Used for proposer, critic, adjudicator, and synthesizer roles. Does
not mutate the workspace. The ``--allowedTools`` flag is set to
``Read,Glob,Grep`` so the subprocess cannot edit, write, or run shell
commands. Output is captured verbatim and returned as the model
payload's ``output`` field.

Subprocess invocation patterns (command shape, env passthrough, stream
capture, log file format) are lifted from ``mcloop/runner.py`` via
``orchestra.adapters._subprocess``.

The backing name registered on the registry is ``claude_code_text``.
The api layer aliases the workflow's logical actor kind (``model``)
to this backing per the project config.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from orchestra.adapters._subprocess import (
    DEFAULT_TIMEOUT_S,
    build_session_env,
    run_session,
    write_log,
)
from orchestra.errors import AdapterError
from orchestra.spine import InvocationRequest, PreparedInvocation

ALLOWED_TOOLS: str = "Read,Glob,Grep"
"""Read-only tool list. Enforced via the CLI's ``--allowedTools`` flag."""


class ClaudeCodeTextAdapter:
    """Adapter for the ``claude_code_text`` backing.

    Constructed with optional defaults that act as fallbacks when the
    invocation request does not supply a value. Per-call overrides flow
    through ``InvocationRequest.actor_binding`` (model id) and
    ``backing_options`` / ``external_inputs`` (project_dir, log_dir,
    task_label).
    """

    backing: str = "claude_code_text"

    def __init__(
        self,
        *,
        cli: str = "claude",
        default_model: str | None = None,
        allowed_tools: str = ALLOWED_TOOLS,
        default_timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._cli = cli
        self._default_model = default_model
        self._allowed_tools = allowed_tools
        self._default_timeout_s = default_timeout_s

    # ----- Adapter contract -------------------------------------------

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        ext = request.external_inputs or {}
        backing = request.backing_options or {}

        model = binding.get("model") or self._default_model
        project_dir = Path(
            backing.get("project_dir")
            or ext.get("project_dir")
            or os.getcwd()
        )
        log_dir = Path(
            backing.get("log_dir")
            or ext.get("log_dir")
            or project_dir / ".mcloop" / "logs"
        )
        task_label = str(
            backing.get("task_label") or ext.get("task_label") or ""
        )
        timeout_s = (
            int(request.timeout_ms / 1000)
            if request.timeout_ms is not None
            else self._default_timeout_s
        )

        cmd = self._build_command(prompt, model)
        env = build_session_env(task_label=task_label)

        return PreparedInvocation(
            request=request,
            summary={
                "kind": "model",
                "adapter": self.backing,
                "cli": self._cli,
                "model": model,
                "allowed_tools": self._allowed_tools,
                "command": cmd,
                "cwd": str(project_dir),
                "log_dir": str(log_dir),
                "timeout_s": timeout_s,
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:160],
            },
            inner={
                "cmd": cmd,
                "env": env,
                "cwd": project_dir,
                "log_dir": log_dir,
                "timeout_s": timeout_s,
                "task_label": task_label or self.backing,
            },
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        inner = prepared.inner
        output, exit_code = run_session(
            inner["cmd"],
            inner["cwd"],
            env=inner["env"],
            timeout=int(inner["timeout_s"]),
        )
        log_path = write_log(
            inner["log_dir"],
            inner["task_label"],
            inner["cmd"],
            output,
            exit_code,
        )
        if exit_code != 0:
            raise AdapterError(
                f"{self.backing} returned exit code {exit_code} "
                f"(log {log_path})",
            )
        return {
            "output": output,
            "verdict": None,
            "fields": {
                "exit_code": exit_code,
                "log_path": str(log_path),
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
            "allowed_tools": self._allowed_tools,
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": True,
        }

    # ----- internals --------------------------------------------------

    def _build_command(self, prompt: str, model: str | None) -> list[str]:
        cmd: list[str] = [self._cli, "-p"]
        if prompt:
            cmd.append(prompt)
        cmd.extend(
            [
                "--allowedTools",
                self._allowed_tools,
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


def register(registry: Any, *, default_model: str | None = None) -> None:
    """Register ``ClaudeCodeTextAdapter`` under ``claude_code_text``.

    Idempotent: if the backing is already registered, this is a no-op.
    The api layer is responsible for aliasing the logical actor kind
    (``model``) to this backing.
    """
    if "claude_code_text" in registry.actor_backings:
        return
    registry.register_actor_backing(
        "claude_code_text",
        lambda: ClaudeCodeTextAdapter(default_model=default_model),
    )
