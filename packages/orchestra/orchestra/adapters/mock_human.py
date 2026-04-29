"""Mock human adapter.

Returns a pre-scripted choice. Slice 1's tests run the human adapter
in scripted mode: a list of choices is configured, and each invocation
consumes one choice in order.

Configuration is via:

  - ``ORCHESTRA_MOCK_HUMAN_SCRIPT``: a comma-separated list of choices,
    consumed left-to-right. Used by integration tests.
  - The constructor's ``script`` argument: takes precedence over the
    environment variable. Used by unit tests.

If the script is exhausted, ``invoke`` raises an AdapterError. There
is no real notification backend in slice 1.
"""

from __future__ import annotations

import os
from typing import Any

from orchestra.errors import AdapterError
from orchestra.spine import InvocationRequest, PreparedInvocation


class MockHumanAdapter:
    """Deterministic mock for the ``human`` backing."""

    # Class-level shared script is convenient for the CLI/integration
    # path: tests can set a script before launching the runner without
    # needing to thread an instance through.
    _shared_script: list[str] | None = None

    def __init__(self, script: list[str] | None = None) -> None:
        self._instance_script = list(script) if script is not None else None

    @classmethod
    def set_shared_script(cls, script: list[str]) -> None:
        cls._shared_script = list(script)

    @classmethod
    def clear_shared_script(cls) -> None:
        cls._shared_script = None

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        options = tuple(request.backing_options.get("options") or ())
        if not options:
            raise AdapterError(
                f"human state {request.state_id!r} declared no options"
            )
        prompt = request.prompt_artifact or ""
        prepared = PreparedInvocation(
            request=request,
            summary={
                "kind": "human",
                "options": list(options),
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:160],
            },
            inner={"options": options},
        )
        return prepared

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        options: tuple[str, ...] = prepared.inner["options"]
        choice = self._next_choice()
        if choice not in options:
            raise AdapterError(
                f"scripted choice {choice!r} not in options {list(options)!r}"
            )
        payload: dict[str, Any] = {
            "chosen": choice,
            "notification_id": "mock-notification",
            "prompt_artifact_id": None,
            "responded_at": None,
        }
        return payload

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "human",
            "kind": "mock",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
        }

    # ----- internals ----------------------------------------------

    def _next_choice(self) -> str:
        if self._instance_script is not None and self._instance_script:
            return self._instance_script.pop(0)
        if MockHumanAdapter._shared_script:
            return MockHumanAdapter._shared_script.pop(0)
        env = os.environ.get("ORCHESTRA_MOCK_HUMAN_SCRIPT")
        if env:
            parts = [p.strip() for p in env.split(",") if p.strip()]
            if parts:
                # Mutate the env var so subsequent invocations consume
                # the next entry. This keeps state outside the adapter
                # instance, which matters because the registry creates
                # one adapter per backing per process.
                os.environ["ORCHESTRA_MOCK_HUMAN_SCRIPT"] = ",".join(parts[1:])
                return parts[0]
        raise AdapterError("mock human adapter: scripted choices exhausted")
