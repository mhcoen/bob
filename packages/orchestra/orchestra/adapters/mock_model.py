"""Mock model adapter.

Returns a deterministic response based on the prompt. Default behavior
is to echo the prompt with a fixed prefix; tests can override the
response by setting ``ORCHESTRA_MOCK_MODEL_RESPONSE`` in the
environment or by passing a response into the constructor.

This adapter does not call any real model. The runner spec's
``model_payload`` shape is produced exactly as specified, with cost
and transcript fields set to ``None`` (subscription-billing semantics).
"""

from __future__ import annotations

import os
from typing import Any

from orchestra.spine import InvocationRequest, PreparedInvocation


class MockModelAdapter:
    """Deterministic mock for the ``model`` backing."""

    def __init__(self, response: str | None = None) -> None:
        self._response_override = response

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        prepared = PreparedInvocation(
            request=request,
            summary={
                "kind": "model",
                "model": (request.actor_binding or {}).get("model"),
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:160],
            },
            inner={"prompt": prompt},
        )
        return prepared

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        prompt: str = prepared.inner["prompt"]
        response = self._resolve_response(prompt)
        payload: dict[str, Any] = {
            "output": response,
            "verdict": None,
            "fields": {},
            "tokens_in": len(prompt),
            "tokens_out": len(response),
            "cost_usd": None,
            "transcript_ref": None,
        }
        return payload

    def cancel(self, prepared: PreparedInvocation) -> None:
        # The mock has no side effects to abort.
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "model",
            "kind": "mock",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
        }

    # ----- internals ----------------------------------------------

    def _resolve_response(self, prompt: str) -> str:
        if self._response_override is not None:
            return self._response_override
        env = os.environ.get("ORCHESTRA_MOCK_MODEL_RESPONSE")
        if env is not None:
            return env
        # Default deterministic echo. Truncate to avoid blowing up logs
        # when prompts are long.
        return f"[mock-llm response to: {prompt[:80]}]"
