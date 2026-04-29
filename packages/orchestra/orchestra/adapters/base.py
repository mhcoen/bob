"""Adapter base contract.

Every adapter implements four operations: ``prepare``, ``invoke``,
``cancel``, ``describe``. See ``design/orchestra-runner.md``, section
"Actor adapter interface".

Adapters do not mutate the artifact store. They return payloads. The
executor commits any artifact writes proposed by the payload through
the result-parser dispatch path.
"""

from __future__ import annotations

from typing import Any, Protocol

from orchestra.spine import InvocationRequest, PreparedInvocation


class Adapter(Protocol):
    """Adapter contract."""

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        """Build the prepared invocation. No side effects."""

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        """Perform the call; return the payload."""

    def cancel(self, prepared: PreparedInvocation) -> None:
        """Abort an in-progress invocation. No-op if nothing is in flight."""

    def describe(self) -> dict[str, Any]:
        """Return adapter metadata."""
