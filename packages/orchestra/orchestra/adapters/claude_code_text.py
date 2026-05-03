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

import hashlib
import os
from pathlib import Path
from typing import Any

from orchestra.adapters._subprocess import (
    DEFAULT_TIMEOUT_S,
    build_session_env,
    extract_final_text,
    run_session,
    write_log,
)
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

        model = (
            backing.get("model_override")
            or self._default_model
            or binding.get("model")
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

        cmd = self._build_command(model)
        env = build_session_env(
            task_label=task_label, cli=self._cli, model=model
        )

        prompt_bytes = prompt.encode("utf-8") if prompt else b""
        prompt_sha256 = (
            hashlib.sha256(prompt_bytes).hexdigest() if prompt_bytes else ""
        )
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
                "prompt_sha256": prompt_sha256,
            },
            inner={
                "cmd": cmd,
                "env": env,
                "cwd": project_dir,
                "log_dir": log_dir,
                "timeout_s": timeout_s,
                "task_label": task_label or self.backing,
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
        # The CLI emits stream-json. Extract the final assistant text
        # so callers see the answer, not the entire transcript. The
        # raw stream is preserved at log_path for debugging.
        final_text = extract_final_text(output)
        verdict = _verdict_for_exit_code(exit_code)
        return {
            "output": final_text,
            "verdict": verdict,
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

    def _build_command(self, model: str | None) -> list[str]:
        # Pass-7 fix: the prompt no longer appears in argv. The
        # adapter pipes the rendered prompt to the inner CLI via
        # stdin so it cannot leak through ps output, the
        # .mcloop/active-pid file, transcript logs, or the prepare()
        # summary. ``-p`` enters non-interactive (print) mode; the
        # CLI reads its prompt from stdin when no prompt argument
        # is supplied.
        cmd: list[str] = [
            self._cli,
            "-p",
            "--allowedTools",
            self._allowed_tools,
            "--permission-mode",
            "default",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
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
