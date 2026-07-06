"""Text-role adapter backed by the Codex CLI.

Used for proposer, critic, adjudicator, and synthesizer roles when the
user has bound a role to Codex. Mirrors ``ClaudeCodeTextAdapter`` in
shape (constructor, prepare/invoke/cancel/describe contract,
manages_own_timeout flag) so the api layer's per-role dispatcher and
the executor's outcome-derivation treat it identically. The differences
are the command line and the output handling: Codex emits final
assistant text on stdout rather than the stream-json transcript Claude
Code emits, so the captured output is returned as the model payload's
``output`` field unchanged.

Read-only enforcement: ``*_text`` adapters are documented as read-only
in the project README. Claude Code text enforces that with an explicit
``--allowedTools Read,Glob,Grep`` allowlist. Codex text enforces it via
the top-level ``--sandbox read-only`` flag plus ``--ask-for-approval
never`` so a non-interactive run never escalates a denied write to a
prompt the user is not there to answer. Codex 0.128 deprecated the
``--full-auto`` shortcut in favor of explicit sandbox/approval flags,
so the adapter no longer uses it.

Subprocess invocation patterns (env passthrough including the
``OPENAI_API_KEY`` mapping for ``cli="codex"`` billing, stream capture,
log file format, watchdog, timeout) are lifted from
``mcloop/runner.py`` via ``orchestra.adapters._subprocess``.

The backing name registered on the registry is ``codex_text``. The api
layer aliases the workflow's logical actor kind (``model``) to this
backing per the project config.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
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
from orchestra.spine import InvocationRequest, PreparedInvocation


class CodexTextAdapter:
    """Adapter for the ``codex_text`` backing.

    Constructed with optional defaults that act as fallbacks when the
    invocation request does not supply a value. Per-call overrides flow
    through ``InvocationRequest.actor_binding`` (model id) and
    ``backing_options`` / ``external_inputs`` (project_dir, log_dir,
    task_label).
    """

    backing: str = "codex_text"
    WORKSPACE_MUTATION: str = "text_only"
    """The text variant runs codex exec in read-only sandbox and emits
    the model's output. The runtime captures the output but the
    adapter performs no workspace edits, so PRJI binds proposer,
    reviewer, and judge to text-only adapters of this kind."""

    manages_own_timeout: bool = True
    """``run_session`` enforces a wall-clock timeout and returns
    exit_code TIMEOUT_KILL_EXIT (-102) on expiry (IDLE_KILL_EXIT,
    -103, on stream-idle kills). The executor honors this flag so it does
    not impose a second timer on top, which would race the adapter
    and discard the structured kill-sentinel payload."""

    def __init__(
        self,
        *,
        cli: str = "codex",
        default_model: str | None = None,
        default_timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._cli = cli
        self._default_model = default_model
        self._default_timeout_s = default_timeout_s

    # ----- Adapter contract -------------------------------------------

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        binding = request.actor_binding or {}
        ext = request.external_inputs or {}
        backing = request.backing_options or {}

        model = backing.get("model_override") or self._default_model or binding.get("model")
        project_dir = Path(backing.get("project_dir") or ext.get("project_dir") or os.getcwd())
        log_dir = Path(
            backing.get("log_dir") or ext.get("log_dir") or project_dir / ".mcloop" / "logs"
        )
        task_label = str(backing.get("task_label") or ext.get("task_label") or "")
        timeout_s = timeout_s_from_ms(request.timeout_ms, self._default_timeout_s)

        cmd = self._build_command(model)
        env = build_session_env(task_label=task_label, cli=self._cli, model=model)

        prompt_bytes = prompt.encode("utf-8") if prompt else b""
        prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest() if prompt_bytes else ""
        return PreparedInvocation(
            request=request,
            summary={
                "kind": "model",
                "adapter": self.backing,
                "cli": self._cli,
                "model": model,
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
        # Capture ONLY the model's final message via ``--output-last-message``.
        # codex 0.138 changed ``exec`` stdout to prepend a startup banner and
        # echo the stdin prompt back ("Reading prompt from stdin...\nOpenAI Codex
        # v...\n...\nuser\n<prompt>") before the completion, so the raw stdout is
        # no longer "just the answer" the way it was on 0.128. Routing the final
        # message to a file and reading that gives the clean completion
        # regardless of banner/echo changes. stdout is kept only as a fallback.
        fd, last_message_path = tempfile.mkstemp(prefix="codex_last_", suffix=".txt")
        os.close(fd)
        run_cmd = list(inner["cmd"]) + ["--output-last-message", last_message_path]
        output, exit_code = run_session(
            run_cmd,
            inner["cwd"],
            env=inner["env"],
            timeout=int(inner["timeout_s"]),
            silent=True,
            stdin_bytes=stdin_arg,
        )
        last_message = ""
        try:
            last_message = Path(last_message_path).read_text(encoding="utf-8").strip()
        except OSError:
            last_message = ""
        finally:
            try:
                os.unlink(last_message_path)
            except OSError:
                pass
        # Prefer the clean final message. Fall back to raw stdout only if codex
        # wrote nothing to the file (e.g. an early error before any completion).
        answer = last_message if last_message else output
        log_path = write_log(
            inner["log_dir"],
            inner["task_label"],
            run_cmd,
            output,
            exit_code,
            state_id=prepared.request.state_id,
            attempt=prepared.request.attempt,
        )
        verdict = verdict_for_exit_code(exit_code)
        return {
            "output": answer,
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
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": "text_only",
        }

    # ----- internals --------------------------------------------------

    def _build_command(self, model: str | None) -> list[str]:
        # Pass-7 fix: prompt no longer in argv; the inner CLI reads
        # it from stdin (``codex exec`` prints "Reading prompt from
        # stdin..." when the prompt argument is omitted). Removes
        # leakage through ps output, the .mcloop/active-pid file,
        # transcript logs, and the prepare() summary.
        #
        # Sandbox and approval flags must precede the ``exec`` subcommand
        # (verified empirically against codex 0.128: ``--ask-for-approval``
        # is a top-level option and is rejected when placed after
        # ``exec``). ``read-only`` denies disk writes; ``never`` ensures
        # a denied write is returned to the model rather than escalated
        # to an interactive approval prompt that a non-interactive run
        # cannot answer. ``--skip-git-repo-check`` is an ``exec``
        # subcommand flag (codex refuses to run in untrusted directories
        # without it).
        cmd: list[str] = [
            self._cli,
            "--ask-for-approval",
            "never",
            "--sandbox",
            "read-only",
            "exec",
            "--skip-git-repo-check",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd


def register(registry: Any, *, default_model: str | None = None) -> None:
    """Register ``CodexTextAdapter`` under ``codex_text``.

    Idempotent: if the backing is already registered, this is a no-op.
    The api layer is responsible for aliasing the logical actor kind
    (``model``) to this backing.
    """
    if "codex_text" in registry.actor_backings:
        return
    registry.register_actor_backing(
        "codex_text",
        lambda: CodexTextAdapter(default_model=default_model),
    )
