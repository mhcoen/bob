"""Mock shell adapter.

The echo workflow does not use shell, but the adapter is implemented
to prove the shell payload shape and the multi-command ``runs`` block
parsing. The mock executes commands by string-matching against a
configured response table.

Configuration via the ``response_table`` constructor argument. Real
subprocess execution is slice 3.
"""

from __future__ import annotations

from typing import Any

from orchestra.errors import AdapterError
from orchestra.spine import InvocationRequest, PreparedInvocation


class MockShellAdapter:
    """Deterministic mock for the ``shell`` backing."""

    WORKSPACE_MUTATION: str = "text_only"
    """The mock returns canned (exit_code, stdout, stderr) tuples
    from a response table without executing commands, so it cannot
    mutate the workspace. The real shell adapter (slice 2) will
    declare ``"mutating"`` because real shell commands can modify
    the workspace."""

    def __init__(
        self,
        response_table: dict[str, tuple[int, str, str]] | None = None,
    ) -> None:
        self._table: dict[str, tuple[int, str, str]] = response_table or {}

    def set_responses(self, table: dict[str, tuple[int, str, str]]) -> None:
        self._table = dict(table)

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        commands = self._extract_commands(request)
        prepared = PreparedInvocation(
            request=request,
            summary={
                "kind": "shell",
                "commands": list(commands),
            },
            inner={
                "commands": commands,
                "continue_on_fail": bool(request.backing_options.get("continue_on_fail", False)),
            },
        )
        return prepared

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        commands: list[str] = prepared.inner["commands"]
        continue_on_fail: bool = prepared.inner["continue_on_fail"]
        per_command: list[dict[str, Any]] = []
        pass_count = 0
        fail_count = 0
        skipped_count = 0
        total_ms = 0
        short_circuited = False
        for cmd in commands:
            if short_circuited:
                per_command.append(
                    {
                        "command": cmd,
                        "exit_code": None,
                        "stdout_path": "",
                        "stderr_path": "",
                        "duration_ms": None,
                        "skipped": True,
                    }
                )
                skipped_count += 1
                continue
            exit_code, stdout, stderr = self._lookup(cmd)
            per_command.append(
                {
                    "command": cmd,
                    "exit_code": exit_code,
                    "stdout_path": "",  # mock does not write files
                    "stderr_path": "",
                    "duration_ms": 0,  # deterministic for byte-identical logs
                    "skipped": False,
                    "_stdout_inline": stdout,  # for tests; real adapter uses paths
                    "_stderr_inline": stderr,
                }
            )
            if exit_code == 0:
                pass_count += 1
            else:
                fail_count += 1
                if not continue_on_fail:
                    short_circuited = True
        payload: dict[str, Any] = {
            "commands": per_command,
            "aggregate": {
                "pass_count": pass_count,
                "fail_count": fail_count,
                "skipped_count": skipped_count,
                "total_ms": total_ms,
            },
        }
        return payload

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        # Workspace mutation is a self-classification of the adapter's
        # actual runtime behavior. MockShellAdapter does not execute
        # the configured commands; it returns canned (exit_code,
        # stdout, stderr) tuples from a response table and leaves the
        # filesystem untouched. So this mock is "text_only". The real
        # shell adapter (slice 2) will classify itself as "mutating"
        # since real shell commands can modify the workspace.
        return {
            "backing": "shell",
            "kind": "mock",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": "text_only",
        }

    # ----- internals ----------------------------------------------

    @staticmethod
    def _extract_commands(request: InvocationRequest) -> list[str]:
        opts = request.backing_options or {}
        if "command" in opts:
            cmd = opts["command"]
            if not isinstance(cmd, str):
                raise AdapterError("'command' must be a string")
            return [cmd]
        if "runs" in opts:
            runs = opts["runs"]
            if not isinstance(runs, list) or not all(isinstance(c, str) for c in runs):
                raise AdapterError("'runs' must be a list of strings")
            return list(runs)
        raise AdapterError(f"shell state {request.state_id!r} has neither 'command' nor 'runs'")

    def _lookup(self, command: str) -> tuple[int, str, str]:
        if command in self._table:
            return self._table[command]
        # Default: exit 0, no output. Matches a no-op shell for echo-only tests.
        return (0, "", "")
