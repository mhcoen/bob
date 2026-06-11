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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from orchestra.errors import RegistryConflict
from orchestra.spine import Envelope, StateDecl
from orchestra.transforms import (
    Transform,
    TransformCallable,
    validate_schema,
)

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


@dataclass(frozen=True)
class ModelIdentifier:
    """A short model name registered to an (adapter, model) tuple.

    Compound role bindings in ``~/.orchestra/config.json`` may name a
    model identifier in lieu of spelling out both ``adapter`` and
    ``model``. At workflow start ``orchestra.run_role`` resolves the
    identifier through the ``ProfileRegistry`` so the workflow's
    actor binding lands on a concrete adapter class plus the
    model-string the adapter forwards to the underlying CLI.
    """

    name: str
    adapter: str
    model: str


BUILTIN_MODEL_IDENTIFIERS: dict[str, ModelIdentifier] = {
    # Anthropic Claude family via Claude Code CLI (read-only, text role).
    "opus": ModelIdentifier(name="opus", adapter="claude_code_text", model="opus"),
    "sonnet": ModelIdentifier(name="sonnet", adapter="claude_code_text", model="sonnet"),
    "haiku": ModelIdentifier(name="haiku", adapter="claude_code_text", model="haiku"),
    # Direct-provider bindings (read-only, text role).
    "kimi": ModelIdentifier(
        name="kimi",
        adapter="claude_code_text_kimi",
        model="kimi-k2.6",
    ),
    "deepseek": ModelIdentifier(
        name="deepseek",
        adapter="claude_code_text_deepseek",
        model="deepseek-v4-pro",
    ),
    # Codex via the OpenAI CLI (read-only, text role). The bare
    # identifier must resolve to a model every Codex account serves.
    # gpt-5-codex is rejected with a 400 by ChatGPT-account Codex, so
    # it is opt-in via its own identifier below and must never be the
    # value behind the bare "codex" identifier.
    "codex": ModelIdentifier(name="codex", adapter="codex_text", model="gpt-5.5"),
    "gpt-5-codex": ModelIdentifier(
        name="gpt-5-codex",
        adapter="codex_text",
        model="gpt-5-codex",
    ),
}
"""Built-in model identifiers, registered onto every ``ProfileRegistry``
``with_core()`` produces. Acts as the default lookup table for the
compound-role-binding short form (``{"model": "opus"}``) in
``~/.orchestra/config.json``."""


# --------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------


@dataclass
class ProfileRegistry:
    """The runner's registry of registered capabilities.

    The registry is created with the core registrations baked in by
    ``with_core()``; profiles add to it via the ``register_*`` methods.

    Adapter instances are constructed lazily on first ``adapter_for``
    call and cached for the lifetime of the registry. The runner spec
    requires one instance per backing per process so adapters with
    process-local state (cached connections, scripted invocation
    state, persistent sessions) work correctly.
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
    transforms: dict[str, Transform] = field(default_factory=dict)
    model_identifiers: dict[str, ModelIdentifier] = field(default_factory=dict)
    _adapter_cache: dict[str, Any] = field(default_factory=dict, repr=False)

    # ----- registration -------------------------------------------

    def register_artifact_type(self, name: str, **info: Any) -> None:
        if name in self.artifact_types:
            raise RegistryConflict(f"artifact type already registered: {name!r}")
        self.artifact_types[name] = info

    def register_actor_backing(self, name: str, factory: AdapterFactory) -> None:
        if name in self.actor_backings:
            raise RegistryConflict(f"actor backing already registered: {name!r}")
        self.actor_backings[name] = factory
        # Invalidate any cached instance for this backing.
        self._adapter_cache.pop(name, None)

    def register_result_parser(self, parser: ResultParser) -> None:
        if parser.name in self.result_parsers:
            raise RegistryConflict(f"result parser already registered: {parser.name!r}")
        self.result_parsers[parser.name] = parser

    def register_validation_rule(self, name: str, fn: Callable[..., None]) -> None:
        if name in self.validation_rules:
            raise RegistryConflict(f"validation rule already registered: {name!r}")
        self.validation_rules[name] = fn

    def register_model_identifier(self, identifier: ModelIdentifier) -> None:
        """Register a model identifier short name.

        The registered name is what compound role bindings name in their
        ``model`` field when the binding omits ``adapter``; resolution
        replaces the leaf binding's adapter+model with the registered
        values. Re-registering an existing name is a conflict.
        """
        if identifier.name in self.model_identifiers:
            raise RegistryConflict(f"model identifier already registered: {identifier.name!r}")
        self.model_identifiers[identifier.name] = identifier

    def register_transform(
        self,
        name: str,
        callable: TransformCallable,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> None:
        """Register a transform for use in ``actor transform <name>``.

        The schemas restrict types to the Slice B vocabulary. The
        validator and the executor read from this record so static
        and runtime checks see the same shape.
        """
        if name in self.transforms:
            raise RegistryConflict(f"transform already registered: {name!r}")
        validate_schema(input_schema, where=f"{name!r} input_schema")
        validate_schema(output_schema, where=f"{name!r} output_schema")
        self.transforms[name] = Transform(
            name=name,
            callable=callable,
            input_schema=dict(input_schema),
            output_schema=dict(output_schema),
        )

    # ----- lookup -------------------------------------------------

    def adapter_for(self, backing: str) -> Any:
        """Return the cached adapter instance for ``backing``,
        constructing it on first call.
        """
        if backing not in self.actor_backings:
            raise KeyError(f"no adapter registered for backing {backing!r}")
        cached = self._adapter_cache.get(backing)
        if cached is not None:
            return cached
        instance = self.actor_backings[backing]()
        self._adapter_cache[backing] = instance
        return instance

    def parsers_for(self, *, backing: str, artifact_types: tuple[str, ...]) -> list[ResultParser]:
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

    # Built-in model identifiers. Compound role bindings in
    # ``~/.orchestra/config.json`` reference these by their short
    # name (``opus``, ``codex``, ``kimi``, ...); ``orchestra.run_role``
    # resolves the short name through this registry at workflow
    # start. See ``BUILTIN_MODEL_IDENTIFIERS`` for the shipped set.
    for identifier in BUILTIN_MODEL_IDENTIFIERS.values():
        reg.register_model_identifier(identifier)

    return reg
