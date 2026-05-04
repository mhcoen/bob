"""Adapter base contract.

Every adapter implements four operations: ``prepare``, ``invoke``,
``cancel``, ``describe``. See ``design/orchestra-runner.md``, section
"Actor adapter interface".

Adapters do not mutate the artifact store. They return payloads. The
executor commits any artifact writes proposed by the payload through
the result-parser dispatch path.

``describe()`` return contract
------------------------------

In addition to the historical ``backing``, ``kind``, ``supports_cancel``,
``reports_cost``, and ``supports_streaming`` fields, every adapter's
``describe()`` is required to include a ``workspace_mutation`` key
with one of two values:

  - ``"mutating"``: the adapter, when invoked, may modify the
    project workspace (file create/delete/rename, command execution,
    git commits, etc.). The Propose-Review-Judge-Implement workflow's
    config validation rule binds the implementer role to a
    "mutating" adapter and refuses to bind any non-implementer role
    to one. See
    ``design/iteration-and-implementation-workflows.md`` section
    "Mutation contract and where it is checked".

  - ``"text_only"``: the adapter, when invoked, never modifies the
    workspace. PRJI binds the proposer, reviewer, and judge to
    text-only adapters and refuses to bind the implementer to one.

Adapters that do not classify themselves are treated as
``"text_only"`` by the validator: a missing key is interpreted
conservatively, but every shipped adapter declares the key
explicitly and a new adapter is expected to do the same.
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
        """Return adapter metadata.

        Must include the ``workspace_mutation`` key. See the
        module docstring for the contract.
        """
