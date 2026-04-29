"""Edit-agent adapter backed by Claude Code in workspace-mutating mode.

Used for the single workspace-mutating invocation in each code-edit
workflow. The ``--allowedTools`` flag is set to the same list mcloop's
runner uses by default (``Edit,Write,Bash,Read,Glob,Grep``).

Subprocess invocation patterns are lifted from ``mcloop/runner.py`` via
``orchestra.adapters._subprocess``. Same shape as
``ClaudeCodeTextAdapter`` but with a different tool list and a result
that surfaces ``changed_files`` so the api layer can report back to
mcloop.

The backing name registered on the registry is ``claude_code_agent``.
The api layer aliases the workflow's logical actor kind (``agent``)
to this backing per the project config.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from orchestra.adapters._subprocess import (
    DEFAULT_TIMEOUT_S,
    build_session_env,
    run_session,
    write_log,
)
from orchestra.spine import InvocationRequest, PreparedInvocation

DEFAULT_ALLOWED_TOOLS: str = "Edit,Write,Bash,Read,Glob,Grep"
"""Matches mcloop's default tool list for ``run_task``."""


class ClaudeCodeAgentAdapter:
    """Adapter for the ``claude_code_agent`` backing.

    Per-call overrides flow through ``InvocationRequest.actor_binding``
    (model id), ``backing_options`` (allowed_tools override, task_label,
    project_dir, log_dir), and ``external_inputs`` (project_dir,
    log_dir, task_label).
    """

    backing: str = "claude_code_agent"
    manages_own_timeout: bool = True
    """``run_session`` enforces a wall-clock timeout and returns
    exit_code -2 on expiry. The executor honors this flag so it does
    not impose a second timer on top, which would race the adapter
    and discard the structured -2 payload."""

    def __init__(
        self,
        *,
        cli: str = "claude",
        default_model: str | None = None,
        default_allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
        default_timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._cli = cli
        self._default_model = default_model
        self._default_allowed_tools = default_allowed_tools
        self._default_timeout_s = default_timeout_s

    # ----- Adapter contract -------------------------------------------

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        ext = request.external_inputs or {}
        backing = request.backing_options or {}

        model = (
            backing.get("model_override")
            or self._default_model
            or binding.get("model")
        )
        allowed_tools = str(
            backing.get("allowed_tools") or self._default_allowed_tools
        )
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

        cmd = self._build_command(prompt, model, allowed_tools)
        env = build_session_env(
            task_label=task_label, cli=self._cli, model=model
        )

        return PreparedInvocation(
            request=request,
            summary={
                "kind": "agent",
                "adapter": self.backing,
                "cli": self._cli,
                "model": model,
                "allowed_tools": allowed_tools,
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
                "project_dir": project_dir,
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
        changed = _detect_changed_files(inner["project_dir"])
        verdict = _verdict_for_exit_code(exit_code)
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
            "allowed_tools": self._default_allowed_tools,
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": True,
        }

    # ----- internals --------------------------------------------------

    def _build_command(
        self, prompt: str, model: str | None, allowed_tools: str
    ) -> list[str]:
        cmd: list[str] = [self._cli, "-p"]
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


def _verdict_for_exit_code(exit_code: int) -> str:
    """Map a subprocess exit code to a runner verdict.

    Mirrors the convention the executor's ``_derive_outcome`` uses for
    the ``model`` and ``agent`` backings: ``complete`` on zero,
    ``timeout`` on the -2 timeout sentinel, ``error`` on any other
    nonzero. The full exit code is preserved in ``payload.fields`` so
    the adapter caller can read it back.
    """
    if exit_code == 0:
        return "complete"
    if exit_code == -2:
        return "timeout"
    return "error"


def _detect_changed_files(project_dir: Path) -> list[str]:
    """Return the list of files git reports as modified or new.

    Best-effort. If the project is not a git repo or git is missing,
    returns an empty list. Does not raise.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    files: list[str] = []
    for line in out.stdout.splitlines():
        path = line[3:].strip()
        if path:
            files.append(path)
    return files


def register(registry: Any, *, default_model: str | None = None) -> None:
    """Register ``ClaudeCodeAgentAdapter`` under ``claude_code_agent``.

    Idempotent: if the backing is already registered, this is a no-op.
    """
    if "claude_code_agent" in registry.actor_backings:
        return
    registry.register_actor_backing(
        "claude_code_agent",
        lambda: ClaudeCodeAgentAdapter(default_model=default_model),
    )
