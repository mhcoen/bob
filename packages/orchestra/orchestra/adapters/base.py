"""Adapter base contract.

Every adapter implements four operations: ``prepare``, ``invoke``,
``cancel``, ``describe``. See ``design/orchestra-runner.md``, section
"Actor adapter interface".

Adapters do not mutate the artifact store. They return payloads. The
executor commits any artifact writes proposed by the payload through
the result-parser dispatch path.

``WORKSPACE_MUTATION`` class-level metadata
-------------------------------------------

Every adapter class is required to declare a class-level
``WORKSPACE_MUTATION`` attribute with one of two literal values:

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

The classification is a static property of the adapter class, not a
runtime computation. The validator reads it from the class without
instantiation: a missing attribute or an out-of-vocabulary value is
a hard ``ConfigError`` because the previous fall-back-to-text_only
defaulting could let a mutating adapter with broken metadata pass as
proposer/reviewer/judge.

Adapters should also include the same value in their ``describe()``
return for parity with the older debug-introspection path; the
canonical source of truth is the class attribute.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, Protocol

from orchestra.spine import InvocationRequest, PreparedInvocation

WorkspaceMutation = Literal["mutating", "text_only"]
"""The two valid values for an adapter's ``WORKSPACE_MUTATION``
class-level metadata attribute."""

WORKSPACE_MUTATION_VALUES: frozenset[str] = frozenset({"mutating", "text_only"})
"""Set form of ``WorkspaceMutation`` for runtime membership checks
the api validator runs against arbitrary class attributes."""


class Adapter(Protocol):
    """Adapter contract."""

    WORKSPACE_MUTATION: ClassVar[WorkspaceMutation]
    """Static classification of the adapter's workspace-mutation
    behavior. See module docstring."""

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        """Build the prepared invocation. No side effects."""

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        """Perform the call; return the payload."""

    def cancel(self, prepared: PreparedInvocation) -> None:
        """Abort an in-progress invocation. No-op if nothing is in flight."""

    def describe(self) -> dict[str, Any]:
        """Return adapter metadata.

        For parity with the older debug-introspection path, the
        return should include a ``workspace_mutation`` key with the
        same value as the class-level ``WORKSPACE_MUTATION``
        attribute. The class attribute is the canonical source of
        truth.
        """
