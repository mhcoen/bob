"""Core IR and runtime types shared across runner components.

This module is the canonical home for the dataclasses that flow between
the loader, the registry, the executor, the adapters, the artifact
store, the log, and the resume machinery. Putting them in one module
prevents circular imports and gives reviewers one place to read the
runner's value-shape contract.

The shapes here are the slice-1 subset of what the design documents
specify. They are forward-compatible with the full spec but do not
implement features the slice does not exercise (groups, joins,
schemas, agents, retry policy, cycle bounds beyond ``max_total_steps``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# --------------------------------------------------------------------
# Workflow IR (produced by the loader, consumed by the executor)
# --------------------------------------------------------------------

ArtifactType = Literal[
    "text",
    "json",
    "messages",
    "prompt",
    "schema",
    "document",
    "file",
    "directory",
]
"""Core artifact types. Profiles register additional types (e.g.
``git-workspace``) but slice 1 has no profiles and so the type vocabulary
is closed."""


@dataclass(frozen=True)
class ExternalInputDecl:
    name: str
    type: str  # any declared type, validated at load time


@dataclass(frozen=True)
class ModelDecl:
    name: str


@dataclass(frozen=True)
class PromptSource:
    """A prompt source.

    Exactly one of ``file``, ``template``, or ``from_state`` is set.
    """

    kind: Literal["file", "template", "from"]
    path: str | None = None
    template_vars: tuple[str, ...] = ()
    from_ref: str | None = None  # for ``prompt from <state>.<field>``


@dataclass(frozen=True)
class RoleDecl:
    name: str
    default_prompt: PromptSource


@dataclass(frozen=True)
class ArtifactDecl:
    name: str
    type: str
    initial: Any | None = None  # ``initial`` literal, or None
    source_kind: Literal["file", "path", None] = None
    source_value: str | None = None  # path string or external_input ref


@dataclass(frozen=True)
class Transition:
    outcome: str
    target: str  # state name, "done", or "stop"
    guard: GuardExpr | None = None


@dataclass(frozen=True)
class WriteDecl:
    name: str
    type: str


@dataclass(frozen=True)
class ActorBinding:
    """What the state's ``actor`` clause names."""

    kind: Literal["model", "agent", "shell", "human"]
    ref: str | None = None  # model id, agent id; None for shell/human


@dataclass(frozen=True)
class StateDecl:
    name: str
    actor: ActorBinding
    role: str | None = None
    prompt: PromptSource | None = None
    reads: tuple[str, ...] = ()
    writes: tuple[WriteDecl, ...] = ()
    options: tuple[str, ...] = ()  # for human gates
    transitions: tuple[Transition, ...] = ()
    timeout_ms: int | None = None
    backing_options: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------
# Guard expressions (a small AST; slice 1 only needs comparisons and
# truthy references, but the shape is pinned for forward compatibility)
# --------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """A dotted identifier reference, e.g. ``attempts.confirm`` or
    ``task.needs_tests``."""

    parts: tuple[str, ...]

    def head(self) -> str:
        return self.parts[0]

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass(frozen=True)
class Literal_:
    value: Any


@dataclass(frozen=True)
class Comparison:
    op: str  # one of <, <=, >, >=, ==, !=
    left: Reference
    right: Reference | Literal_


@dataclass(frozen=True)
class TruthyTest:
    ref: Reference


@dataclass(frozen=True)
class NotExpr:
    inner: GuardExpr


@dataclass(frozen=True)
class AndExpr:
    parts: tuple[GuardExpr, ...]


@dataclass(frozen=True)
class OrExpr:
    parts: tuple[GuardExpr, ...]


GuardExpr = Comparison | TruthyTest | NotExpr | AndExpr | OrExpr


# --------------------------------------------------------------------
# The Workflow object: result of loading + validating a workflow file
# --------------------------------------------------------------------


@dataclass
class Workflow:
    spec_version: str
    name: str
    profiles: tuple[str, ...] = ()
    external_inputs: tuple[ExternalInputDecl, ...] = ()
    max_total_steps: int = 0
    compression_model: str | None = None
    models: tuple[ModelDecl, ...] = ()
    roles: tuple[RoleDecl, ...] = ()
    # agents and groups are forward-compat slots; slice 1 leaves them empty
    agents: tuple[Any, ...] = ()
    groups: tuple[Any, ...] = ()
    artifacts: tuple[ArtifactDecl, ...] = ()
    states: tuple[StateDecl, ...] = ()
    source_dir: str = ""
    """Directory containing the workflow file, used for resolving relative
    paths in prompt sources."""

    def state(self, name: str) -> StateDecl:
        for s in self.states:
            if s.name == name:
                return s
        raise KeyError(f"unknown state: {name}")

    def start_state_name(self) -> str:
        if not self.states:
            raise ValueError("workflow has no states")
        return self.states[0].name


# --------------------------------------------------------------------
# Result envelope and payload (per orchestra-result-schemas.md)
# --------------------------------------------------------------------


Status = Literal["ok", "error", "timeout", "cancelled"]


@dataclass
class ErrorRecord:
    kind: Literal[
        "actor_failure",
        "timeout",
        "postcondition_failure",
        "parser_failure",
        "runner_failure",
        "cancelled",
    ]
    message: str
    detail: dict[str, Any] | None = None


@dataclass
class Envelope:
    state_id: str
    attempt: int
    actor_binding: dict[str, Any]
    status: Status
    outcome: str
    started_at: str
    ended_at: str
    duration_ms: int
    inputs_read: list[dict[str, str]]  # [{artifact, version_id}]
    artifacts_written: list[dict[str, str]]  # [{artifact, version_id}]
    payload: dict[str, Any]
    error: ErrorRecord | None = None


# --------------------------------------------------------------------
# Invocation request shape passed to adapter.prepare()
# --------------------------------------------------------------------


@dataclass
class InvocationRequest:
    state_id: str
    attempt: int
    actor_binding: dict[str, Any]
    reads: dict[str, Any]  # {artifact_name: artifact_value}
    external_inputs: dict[str, Any]
    prompt_artifact: str | None
    schema: dict[str, Any] | None
    backing_options: dict[str, Any]
    timeout_ms: int | None


@dataclass
class PreparedInvocation:
    """Adapter-internal handle returned from prepare(), passed to
    invoke(). The runner does not inspect this; it stores it across
    the prepare/invoke boundary so the log can record what was prepared
    before any side effect happened.

    Adapters store whatever they need here. We keep a few fields the
    logger uses for the actor_prepare summary.
    """

    request: InvocationRequest
    summary: dict[str, Any] = field(default_factory=dict)
    """Adapter-supplied summary for the actor_prepare log record. For
    LLMs: resolved prompt artifact id. For shell: the command list. For
    human: the notification message."""
    inner: Any = None
    """Adapter-internal state for invoke()."""
