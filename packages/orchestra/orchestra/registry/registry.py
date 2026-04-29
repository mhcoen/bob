"""Profile registry.

Holds the runner's catalog of registered capabilities: artifact types,
actor backings, backing-scoped keywords, postconditions, guard
predicates, result parsers, validation rules, default policies, and
resume hooks.

In slice 1 the only registrations are core: the inline artifact types,
the model/human/shell actor backings (served by mock adapters), and
the identity model-output parser.

The conflict-detection logic is implemented but exercised only by
unit tests in slice 1, since the slice has no profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from orchestra.errors import RegistryConflict
from orchestra.spine import Envelope, StateDecl


# --------------------------------------------------------------------
# Protocols and small types
# --------------------------------------------------------------------


class AdapterFactory(Protocol):
    """A callable that constructs an adapter instance."""

    def __call__(self) -> Any: ...


ScopePredicate = Callable[[StateDecl], bool]
"""A function that decides whether a registration applies to a state."""

ParserFn = Callable[[Envelope, Any], list[tuple[str, Any]]]
"""A result parser callback.

Receives the envelope (with payload) and an artifact-store handle (the
caller's choice, opaque to the type). Returns a list of (artifact_name,
value) pairs that should be tentatively written.

The actual ``tentative_write`` call is performed by the executor, not
the parser. Parsers are pure functions of the envelope plus the
declared writes; making them return values rather than perform writes
keeps the executor as the only thing that touches the store's mutation
path.
"""


@dataclass(frozen=True)
class ResultParser:
    name: str
    backing_filter: tuple[str, ...]
    """Actor backings this parser applies to (e.g. ('model',))."""
    artifact_type_filter: tuple[str, ...]
    """Artifact types this parser produces (e.g. ('text',))."""
    fn: ParserFn


# --------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------


@dataclass
class ProfileRegistry:
    """The runner's registry of registered capabilities.

    The registry is created with the core registrations baked in by
    ``with_core()``; profiles add to it via the ``register_*`` methods.
    """

    artifact_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    actor_backings: dict[str, AdapterFactory] = field(default_factory=dict)
    backing_scoped_keywords: dict[str, ScopePredicate] = field(default_factory=dict)
    postconditions: dict[str, tuple[ScopePredicate, Callable[..., None]]] = field(
        default_factory=dict
    )
    guard_predicates: dict[str, Callable[..., bool]] = field(default_factory=dict)
    result_parsers: dict[str, ResultParser] = field(default_factory=dict)
    validation_rules: dict[str, Callable[..., None]] = field(default_factory=dict)
    default_policies: dict[str, Any] = field(default_factory=dict)
    resume_hooks: dict[str, Any] = field(default_factory=dict)

    # ----- registration -------------------------------------------

    def register_artifact_type(self, name: str, **info: Any) -> None:
        if name in self.artifact_types:
            raise RegistryConflict(f"artifact type already registered: {name!r}")
        self.artifact_types[name] = info

    def register_actor_backing(self, name: str, factory: AdapterFactory) -> None:
        if name in self.actor_backings:
            raise RegistryConflict(f"actor backing already registered: {name!r}")
        self.actor_backings[name] = factory

    def register_result_parser(self, parser: ResultParser) -> None:
        if parser.name in self.result_parsers:
            raise RegistryConflict(f"result parser already registered: {parser.name!r}")
        self.result_parsers[parser.name] = parser

    def register_validation_rule(
        self, name: str, fn: Callable[..., None]
    ) -> None:
        if name in self.validation_rules:
            raise RegistryConflict(f"validation rule already registered: {name!r}")
        self.validation_rules[name] = fn

    # ----- lookup -------------------------------------------------

    def adapter_for(self, backing: str) -> Any:
        if backing not in self.actor_backings:
            raise KeyError(f"no adapter registered for backing {backing!r}")
        return self.actor_backings[backing]()

    def parsers_for(
        self, *, backing: str, artifact_types: tuple[str, ...]
    ) -> list[ResultParser]:
        applicable: list[ResultParser] = []
        for parser in self.result_parsers.values():
            if backing not in parser.backing_filter:
                continue
            if not any(t in parser.artifact_type_filter for t in artifact_types):
                continue
            applicable.append(parser)
        return applicable


# --------------------------------------------------------------------
# Core registrations (called once when the registry is created)
# --------------------------------------------------------------------


def with_core() -> ProfileRegistry:
    """Build a registry pre-populated with the slice-1 core."""
    reg = ProfileRegistry()

    # Inline artifact types. ``git-workspace`` is intentionally absent;
    # it is profile-registered (versioned-workspace) and slice 2.
    for type_name in ("text", "json", "messages", "prompt", "schema", "document"):
        reg.register_artifact_type(type_name)

    # Actor backings. Adapter classes are imported lazily to avoid a
    # circular dependency at module load time (adapters depend on
    # spine; spine does not depend on adapters; registry depends on
    # both at runtime).
    from orchestra.adapters.mock_human import MockHumanAdapter
    from orchestra.adapters.mock_model import MockModelAdapter
    from orchestra.adapters.mock_shell import MockShellAdapter

    reg.register_actor_backing("model", MockModelAdapter)
    reg.register_actor_backing("human", MockHumanAdapter)
    reg.register_actor_backing("shell", MockShellAdapter)

    # Identity model-output parser.
    from orchestra.executor.parsers import identity_text_parser

    reg.register_result_parser(identity_text_parser)

    return reg
