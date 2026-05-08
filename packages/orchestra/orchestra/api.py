"""Library API for invoking Orchestra workflows from Python.

The single entry point ``run_workflow`` resolves a workflow name to a
``.orc`` file via ``orchestra.loader.lookup``, builds a registry whose
actor backings are wired to the per-role adapters declared in
``.orchestra/config.json`` (see ``orchestra.config``), runs the
workflow with the given external inputs, and returns a
``WorkflowRunResult`` carrying the final envelope, the committed
artifacts, the run id, the log path, and a summary the consumer can
report on.

Mcloop, Duplo, or any other consumer imports this module. The CLI in
``orchestra.cli`` is independent and remains the canonical command-line
surface.

Adapter registration model
--------------------------

The slice-1 executor dispatches adapters by ``state.actor.kind`` (one
of ``model``, ``agent``, ``shell``, ``human``). The integration plan
needs per-role binding so a workflow with two text-role states can use
two different models, two different adapter parameter sets, or even
two different adapter classes. The api solves this with a
``_PerRoleDispatcher`` registered under each kind that the workflow
uses. The dispatcher fans out to a per-role adapter instance based on
``request.actor_binding["role"]`` (which the executor already
populates from the state's ``role`` clause). State declarations that
omit ``role`` fall back to the only adapter registered under that
kind, or raise.

Invocation options
------------------

Mcloop's wrapper interface takes ``model``, ``timeout``, and
``log_dir`` at the call site. These are not workflow inputs; they are
per-invocation knobs the consumer overrides without editing the
workflow file or the project config. ``run_workflow`` accepts them as
an ``invocation_options`` dict and threads them to the executor, which
merges them into every state's ``backing_options`` and into the actor
binding's ``model`` field.

Instruction template override
-----------------------------

When a role binding in the project config sets
``instruction_template``, the api rebuilds the workflow's role table
so that role's ``default_prompt`` points at the configured template
path. Path-shaped values resolve relative to the project directory or
the workflow's source directory; inline strings are written to a
side-car file in the run directory and referenced from there. The
state's own ``prompt`` clause still wins over the role default per
slice 1 grammar semantics; the override only takes effect for states
that rely on the role's default prompt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.adapters.base import WORKSPACE_MUTATION_VALUES
from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.adapters.codex_agent import CodexAgentAdapter
from orchestra.adapters.codex_text import CodexTextAdapter
from orchestra.config import ConfigError, OrchestraConfig, RoleBinding
from orchestra.errors import OrchestraError
from orchestra.executor.criteria import mode_for_workflow
from orchestra.executor.executor import Executor, new_run_id
from orchestra.executor.parsers import _identity_text_parse_fn
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogWriter
from orchestra.progress import (
    ChildBinding,
    ProgressCallback,
    ProgressEvent,
    silent_reporter,
    stderr_reporter,
)
from orchestra.prompts import build_code_edit_prompt
from orchestra.registry.registry import (
    ProfileRegistry,
    ResultParser,
    with_core,
)
from orchestra.spine import (
    NO_INITIAL,
    Envelope,
    InvocationRequest,
    PreparedInvocation,
    PromptSource,
    RoleDecl,
    Workflow,
)
from orchestra.store import ArtifactStore
from orchestra.transforms import anonymize_outputs, finish_panel

# Maps a configured adapter name to the workflow actor kind it serves.
# Slice 1 grammar limits the kind vocabulary to {model, agent, shell,
# human}; the integration plan pairs text adapters with the ``model``
# kind and edit-agent adapters with the ``agent`` kind.
_ADAPTER_TO_KIND: dict[str, str] = {
    "claude_code_text": "model",
    "claude_code_text_kimi": "model",
    "claude_code_text_deepseek": "model",
    "claude_code_agent": "agent",
    "codex_text": "model",
    "codex_agent": "agent",
}

_ADAPTER_CLASSES: dict[str, type] = {
    "claude_code_text": ClaudeCodeTextAdapter,
    # The kimi/deepseek bindings reuse ClaudeCodeTextAdapter with
    # provider_config injected by _build_role_adapter; the class is
    # the same, the adapter NAME is what selects the routing config.
    "claude_code_text_kimi": ClaudeCodeTextAdapter,
    "claude_code_text_deepseek": ClaudeCodeTextAdapter,
    "claude_code_agent": ClaudeCodeAgentAdapter,
    "codex_text": CodexTextAdapter,
    "codex_agent": CodexAgentAdapter,
}

# Provider-routing configs for the direct-provider bindings. Each entry
# specifies the anthropic-compatible base URL, the env var to read the
# bearer token from, an isolated CLAUDE_CONFIG_DIR per provider (so
# conversation history, MCP configs, and permissions state stay
# separated across providers), and use_slug_model=False because the
# direct endpoints expect the bare model name (not the OpenRouter-
# style provider/slug prefix). Matches Michael's shell wrappers
# verbatim. See design/criteria-compliance.md and the F2.5 actor-
# registry workstream notes for context.
_PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {
    "claude_code_text_kimi": {
        "base_url": "https://api.moonshot.ai/anthropic/",
        "auth_token_env": "MOONSHOT_API_KEY",
        "claude_config_dir": "~/.claude-kimi",
        "use_slug_model": False,
    },
    "claude_code_text_deepseek": {
        "base_url": "https://api.deepseek.com/anthropic",
        "auth_token_env": "DEEPSEEK_API_KEY",
        "claude_config_dir": "~/.claude-deepseek",
        "use_slug_model": False,
    },
}

FINAL_PROMPT_INPUT: str = "final_prompt"
"""Synthetic external input the api injects for code-edit workflows.

The api computes the prompt up front via ``orchestra.prompts.build_code_edit_prompt``
and supplies it under this name. The packaged templates substitute it
directly so prompt construction stays faithful to mcloop's branching
without needing format_map conditionals."""

_CODE_EDIT_WORKFLOW_NAMES: frozenset[str] = frozenset(
    {"single", "draft_then_adjudicate", "propose_critique_synthesize"}
)


class WorkflowApiError(OrchestraError):
    """Raised when ``run_workflow`` cannot wire up or run the workflow."""


@dataclass(frozen=True)
class ArtifactView:
    """A read-only view of one committed artifact."""

    name: str
    type: str
    version_id: str
    value: Any


@dataclass
class WorkflowRunResult:
    """Outcome of a ``run_workflow`` invocation."""

    run_id: str
    terminal: str
    envelope: Envelope
    artifacts: dict[str, ArtifactView]
    log_path: Path
    summary: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------
# Per-role adapter dispatcher
# --------------------------------------------------------------------


class _PerRoleDispatcher:
    """Adapter that fans out to a per-role adapter instance.

    Registered under a workflow actor kind ("model" or "agent"). On
    ``prepare`` it reads ``request.actor_binding["role"]`` and forwards
    the call to the matching role adapter. ``invoke`` and ``cancel``
    consult a per-prepare back-reference so the same adapter handles
    the same call across the prepare/invoke boundary.
    """

    def __init__(self, role_to_adapter: dict[str, Any]) -> None:
        if not role_to_adapter:
            raise WorkflowApiError(
                "_PerRoleDispatcher requires at least one role-adapter binding"
            )
        self._adapters: dict[str, Any] = dict(role_to_adapter)
        # Note: the dispatcher does not expose a static
        # ``manages_own_timeout`` attribute. The executor consults the
        # actually-selected role-adapter's flag via
        # ``prepared.inner["_role_adapter"]`` after ``prepare`` has
        # picked the adapter. A static aggregate (all-or-nothing) would
        # mask True-flagged adapters in a future mixed dispatcher and
        # reintroduce the timeout race the per-dispatch lookup avoids.

    def _pick(self, request: InvocationRequest) -> Any:
        binding = request.actor_binding or {}
        role = binding.get("role")
        if isinstance(role, str) and role in self._adapters:
            return self._adapters[role]
        if role is None and len(self._adapters) == 1:
            # Roleless states are valid in the slice 1 grammar; fall
            # back only when a single adapter is configured under this
            # kind so there is no ambiguity. Roles that are present
            # but unbound must raise rather than silently fall back to
            # a different role's adapter.
            return next(iter(self._adapters.values()))
        raise WorkflowApiError(
            f"no adapter configured for role {role!r}. "
            f"Configured roles: {sorted(self._adapters)}"
        )

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        adapter = self._pick(request)
        prepared = adapter.prepare(request)
        return PreparedInvocation(
            request=prepared.request,
            summary=prepared.summary,
            inner={
                "_role_adapter": adapter,
                "_role_prepared": prepared,
            },
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        adapter = prepared.inner["_role_adapter"]
        inner_prepared = prepared.inner["_role_prepared"]
        result: dict[str, Any] = adapter.invoke(inner_prepared)
        return result

    def cancel(self, prepared: PreparedInvocation) -> None:
        adapter = prepared.inner["_role_adapter"]
        inner_prepared = prepared.inner["_role_prepared"]
        adapter.cancel(inner_prepared)

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "per_role_dispatcher",
            "roles": sorted(self._adapters.keys()),
        }


# --------------------------------------------------------------------
# Adapter and registry construction
# --------------------------------------------------------------------


_ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA: dict[str, Any] = {
    "panelist_1_output": str,
    "panelist_2_output": str,
    "panelist_3_output": str,
    "panelist_4_output": str,
    "panelist_5_output": str,
}
_PARALLEL_THINKING_FINISH_PANEL_INPUT_SCHEMA: dict[str, Any] = {
    "panelist_1_output": str,
    "panelist_2_output": str,
    "panelist_3_output": str,
    "panelist_4_output": str,
    "panelist_5_output": str,
}
"""Input schema the Parallel Thinking workflow's ``finish_panel``
transform reads. Keys are the five panelist outputs that must reach
the join site for the workflow to terminate cleanly. The transform
produces only ``finish_marker``; the consumer of the workflow reads
the per-panelist outputs by name."""
"""Input schema the ``ask_anonymous_reviewers`` workflow's
``anonymize`` state expects. Keys are the five panelist output
artifacts; values are the panelist texts. ``anonymize_outputs``
shuffles the keys deterministically per
``(run_id, state_name, sorted_input_keys)`` and returns ``anon_map``
keyed A through E with the texts as values. The corrected
``ask_council`` workflow does NOT use this transform: its chairman
state reads the five named lens-advisor outputs directly with their
identities in clear."""


def _wrap_progress_callback(
    user_callback: ProgressCallback | None,
    role_bindings: dict[str, RoleBinding],
) -> Callable[
    [
        str,
        str,
        str | None,
        int,
        int,
        float | None,
        tuple[tuple[str, str | None], ...] | None,
    ],
    None,
] | None:
    """Adapt the user-facing ``ProgressCallback`` to the executor's
    callback signature, enriching each event with the resolved adapter
    and model from the role binding.

    For ``fan_out_start`` events the wrapper expands the executor's
    ``children`` tuple of ``(state_name, role)`` pairs into a tuple of
    fully populated ``ChildBinding`` records so the reporter does not
    have to look up bindings a second time.

    Returns ``None`` when ``user_callback`` is ``None`` so the
    executor stays in its no-op fast path.
    """
    if user_callback is None:
        return None

    def _resolve(role: str | None) -> tuple[str | None, str | None]:
        if role is None:
            return (None, None)
        binding = role_bindings.get(role)
        if binding is None:
            return (None, None)
        return (binding.adapter, binding.model)

    def _inner(
        kind: str,
        state_name: str,
        role: str | None,
        index: int,
        total: int,
        elapsed_seconds: float | None,
        children: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        adapter, model = _resolve(role)
        enriched_children: tuple[ChildBinding, ...] | None = None
        if children is not None:
            enriched_children = tuple(
                ChildBinding(
                    state_name=child_state,
                    role=child_role,
                    adapter=_resolve(child_role)[0],
                    model=_resolve(child_role)[1],
                )
                for child_state, child_role in children
            )
        event = ProgressEvent(
            kind=kind,
            state_name=state_name,
            role=role,
            adapter=adapter,
            model=model,
            index=index,
            total=total,
            elapsed_seconds=elapsed_seconds,
            children=enriched_children,
        )
        try:
            user_callback(event)
        except Exception:
            # The user-facing reporter is for UX only. A misbehaving
            # callback must never abort an in-flight run.
            pass

    return _inner


def _resolve_progress_callback(
    user_callback: ProgressCallback | None,
    quiet: bool,
) -> ProgressCallback | None:
    """Apply the library's default-on rule for progress reporting.

    Order of precedence:
    1. ``quiet=True``: always suppress, even if a user callback was
       passed. (Caller asked for silence; honor it.)
    2. ``user_callback`` is not ``None``: use the user's callback.
    3. Otherwise: install the default ``stderr_reporter()`` so library
       calls are visible during integration and active testing.

    The CLI installs its own callback up front (``stderr_reporter``
    by default, ``silent_reporter`` for ``--quiet``) so the
    ``user_callback is not None`` branch fires for every CLI dispatch.
    Library callers that want suppression should pass ``quiet=True``;
    the silent_reporter alternative still installs an event handler
    that does nothing, which is functionally identical from the
    reporter's perspective.
    """
    if quiet:
        return silent_reporter()
    if user_callback is not None:
        return user_callback
    return stderr_reporter()


def _register_builtin_transforms(reg: ProfileRegistry) -> None:
    """Register Slice B builtins with their canonical Slice C schemas.

    ``anonymize_outputs`` is registered with the five-panelist input
    schema the ``ask_anonymous_reviewers`` workflow declares. That
    workflow's synthesizer state reads the anonymized panel through
    ``anon_map`` plus the five anonymous reviews; no de-anonymization
    is needed and ``anon_map`` is the only output. Registering with
    the panelist shape unconditionally is harmless: workflows that do
    not reference ``anonymize_outputs`` never trigger the validator's
    transform-record check, and tests that need a different input
    shape build their own registry.
    """
    if "anonymize_outputs" not in reg.transforms:
        reg.register_transform(
            "anonymize_outputs",
            anonymize_outputs,
            input_schema=dict(
                _ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA
            ),
            output_schema={"anon_map": dict[str, str]},
        )
    if "finish_panel" not in reg.transforms:
        reg.register_transform(
            "finish_panel",
            finish_panel,
            input_schema=dict(_PARALLEL_THINKING_FINISH_PANEL_INPUT_SCHEMA),
            output_schema={"finish_marker": str},
        )


def _pre_load_registry() -> ProfileRegistry:
    """Return a registry the loader can validate any workflow against.

    ``with_core()`` registers ``model``, ``human``, and ``shell``
    backings; the ``agent`` backing is added by the runtime registry
    builder once role bindings are known. We need to load the
    workflow first to know which roles it requires, so this helper
    pre-registers a placeholder ``agent`` factory plus the
    identity-text result parser bound to ``agent``. The placeholder
    factory is replaced by the real per-role dispatcher in
    ``_build_registry`` before the executor runs.

    Slice B builtin transforms are registered here too so the loader's
    phase-5 validator finds the registered shape for every transform
    state in any packaged workflow.
    """
    reg = with_core()
    if "agent" not in reg.actor_backings:
        reg.actor_backings["agent"] = lambda: None
        reg.register_result_parser(
            ResultParser(
                name="identity_text_agent",
                backing_filter=("agent",),
                artifact_type_filter=("text",),
                fn=_identity_text_parse_fn,
            )
        )
    _register_builtin_transforms(reg)
    return reg


def _build_role_adapter(binding: RoleBinding) -> tuple[Any, str]:
    """Instantiate the adapter for ``binding`` and return ``(adapter, kind)``.

    The instance carries the role's configured model, tool override,
    and adapter parameters as defaults so per-call requests can omit
    them and still get the right behavior.
    """
    adapter_name = binding.adapter
    if adapter_name not in _ADAPTER_CLASSES:
        raise WorkflowApiError(
            f"unknown adapter {adapter_name!r}. Known: "
            f"{sorted(_ADAPTER_CLASSES)}"
        )
    cls = _ADAPTER_CLASSES[adapter_name]
    kind = _ADAPTER_TO_KIND[adapter_name]
    params: dict[str, Any] = dict(binding.parameters)
    if binding.model is not None:
        params.setdefault("default_model", binding.model)
    if binding.tools and binding.tools != "default":
        if adapter_name == "claude_code_agent":
            params.setdefault("default_allowed_tools", binding.tools)
        elif adapter_name in ("claude_code_text", "claude_code_text_kimi",
                              "claude_code_text_deepseek"):
            params.setdefault("allowed_tools", binding.tools)
        elif adapter_name == "codex_agent":
            params.setdefault("default_sandbox", binding.tools)
    if adapter_name in _PROVIDER_CONFIGS:
        # Direct-provider bindings: inject the routing config and turn
        # on retry-on-throttle. Cloudflare fronts the Moonshot edge and
        # tight bursts (council fan-out) can hit 403/429 transiently.
        params.setdefault("provider_config", _PROVIDER_CONFIGS[adapter_name])
        params.setdefault("retry_on_throttle", True)
    return cls(**params), kind


def _build_registry(
    role_bindings: dict[str, RoleBinding],
) -> ProfileRegistry:
    """Build a registry whose actor backings dispatch per role.

    For each adapter kind referenced by the configured roles, the api
    registers a single ``_PerRoleDispatcher`` instance whose role
    table maps role names to per-role adapter instances. This lets a
    multi-state workflow with two text-role states bind two different
    models and adapter parameter sets without collapsing them.
    """
    reg = with_core()
    _register_builtin_transforms(reg)

    by_kind: dict[str, dict[str, Any]] = {}
    for role_name, binding in role_bindings.items():
        adapter, kind = _build_role_adapter(binding)
        by_kind.setdefault(kind, {})[role_name] = adapter

    for kind, role_to_adapter in by_kind.items():
        dispatcher = _PerRoleDispatcher(role_to_adapter)

        def make_factory(d: _PerRoleDispatcher) -> Any:
            def factory() -> Any:
                return d

            return factory

        # Replace the slice-1 mock under this kind with the dispatcher.
        # Direct dict assignment is intentional: register_actor_backing
        # rejects duplicate names, but the api needs to override the
        # mock from with_core() for the kinds the configured roles use.
        reg.actor_backings[kind] = make_factory(dispatcher)
        reg._adapter_cache.pop(kind, None)

    if "agent" in by_kind:
        # The slice-1 identity_text parser is registered for the
        # ``model`` backing only. Mirror it for ``agent`` so a
        # workflow whose final state is ``actor agent`` can write a
        # text artifact without needing a profile.
        reg.register_result_parser(
            ResultParser(
                name="identity_text_agent",
                backing_filter=("agent",),
                artifact_type_filter=("text",),
                fn=_identity_text_parse_fn,
            )
        )

    return reg


# --------------------------------------------------------------------
# Instruction template override
# --------------------------------------------------------------------


def _apply_instruction_templates(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    *,
    project_dir: Path | None,
    run_dir: Path,
) -> Workflow:
    """Rebuild ``workflow.roles`` so each role's default prompt points
    at the config-supplied template path or inline text.

    Path-shaped values resolve in this order: absolute path as given,
    relative to ``project_dir``, relative to the workflow's
    ``source_dir``. Inline strings (anything that is not an existing
    file) are written to a side-car file in ``run_dir`` and referenced
    by absolute path.
    """
    if not workflow.roles:
        return workflow
    new_roles: list[RoleDecl] = []
    changed = False
    for role in workflow.roles:
        binding = role_bindings.get(role.name)
        override = binding.instruction_template if binding is not None else None
        if not override:
            new_roles.append(role)
            continue
        resolved = _resolve_template(
            override,
            project_dir=project_dir,
            workflow_source_dir=Path(workflow.source_dir)
            if workflow.source_dir
            else None,
            run_dir=run_dir,
            role_name=role.name,
        )
        old = role.default_prompt
        new_prompt = PromptSource(
            kind="template" if old.kind == "template" else "file",
            path=str(resolved),
            template_vars=old.template_vars,
            from_ref=old.from_ref,
        )
        new_roles.append(RoleDecl(name=role.name, default_prompt=new_prompt))
        changed = True
    if not changed:
        return workflow
    workflow.roles = tuple(new_roles)
    return workflow


def _resolve_template(
    value: str,
    *,
    project_dir: Path | None,
    workflow_source_dir: Path | None,
    run_dir: Path,
    role_name: str,
) -> Path:
    candidates: list[Path] = []
    raw = Path(value)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        if project_dir is not None:
            candidates.append(project_dir / raw)
        if workflow_source_dir is not None:
            candidates.append(workflow_source_dir / raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    # Inline string fallback. Hash for stable filename across reruns.
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    side_dir = run_dir / "instruction_templates"
    side_dir.mkdir(parents=True, exist_ok=True)
    side_path = side_dir / f"{role_name}_{digest}.md"
    if not side_path.exists():
        side_path.write_text(value, encoding="utf-8")
    return side_path


# --------------------------------------------------------------------
# Run setup
# --------------------------------------------------------------------


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        if art.source_kind is not None:
            qualifiers["source"] = {
                "kind": art.source_kind,
                "value": art.source_value,
            }
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _resolve_role_binding(
    workflow_name: str,
    role_name: str,
    config: OrchestraConfig,
) -> RoleBinding:
    """Resolve a role binding for a workflow per the two-tier rules.

    Resolution order:

    1. If the workflow has ``role_overrides.<role>``, the top-level
       binding for ``<role>`` must exist; the override keys replace
       (do not merge with) the corresponding top-level keys.
    2. Else if the top-level ``roles.<role>`` exists, return it as-is.
    3. Else raise ``ConfigError`` naming the workflow, the role, and
       what was searched.

    Override values replace top-level values entirely. ``parameters``
    overrides replace the entire dict, not individual keys.
    """
    workflow_cfg = config.workflow(workflow_name)
    override = workflow_cfg.role_overrides.get(role_name)
    if override is not None:
        if role_name not in config.roles:
            raise ConfigError(
                f"workflow {workflow_name!r}: role_overrides entry "
                f"{role_name!r} has no corresponding top-level binding "
                "in 'roles'. Overrides replace keys on top of an existing "
                "top-level binding."
            )
        return config.roles[role_name].with_overrides(role_name, override)
    if role_name in config.roles:
        return config.roles[role_name]
    raise ConfigError(
        f"workflow {workflow_name!r}: role {role_name!r} has no binding. "
        f"Configured top-level roles: {sorted(config.roles)}"
    )


def _resolve_workflow_role_bindings(
    workflow: Workflow,
    workflow_name: str,
    config: OrchestraConfig,
) -> dict[str, RoleBinding]:
    """Resolve every role the workflow's states reference.

    Walks the workflow states, collects each unique role name, and
    resolves it via ``_resolve_role_binding``. Missing top-level
    bindings, dangling overrides, and adapter-kind mismatches all
    accumulate into a single ``ConfigError``.
    """
    needed: dict[str, str] = {}
    first_state_for_role: dict[str, str] = {}
    for state in workflow.states:
        if state.role is None:
            continue
        if state.actor.kind not in ("model", "agent"):
            continue
        needed.setdefault(state.role, state.actor.kind)
        first_state_for_role.setdefault(state.role, state.name)

    resolution_errors: list[str] = []
    resolved: dict[str, RoleBinding] = {}
    for role_name in needed:
        try:
            resolved[role_name] = _resolve_role_binding(
                workflow_name, role_name, config
            )
        except ConfigError as exc:
            resolution_errors.append(str(exc))
    if resolution_errors:
        raise ConfigError(
            f"workflow {workflow_name!r}: role-binding resolution failed:\n  "
            + "\n  ".join(resolution_errors)
        )
    return resolved


def _validate_role_bindings(
    workflow: Workflow,
    workflow_name: str,
    config: OrchestraConfig,
) -> dict[str, RoleBinding]:
    """Resolve every workflow role and check adapter kinds match.

    Two failure modes are caught here:

    1. A state whose role has no top-level binding (and no override
       references one) would silently fall back to the slice-1 mock
       under the actor kind, or reuse a different role's adapter via
       the dispatcher's one-adapter shortcut.
    2. A state whose resolved adapter has the wrong kind (a text
       adapter on an ``actor agent`` state, or an edit-agent adapter
       on an ``actor model`` state) would route wrong at runtime. The
       mismatch only surfaces when the inner CLI sees the wrong tool
       list, which is too late.

    Both fail loudly with ``ConfigError`` naming the workflow, the
    role, the first state that needs it, the configured adapter, and
    the expected kind. Returns the resolved bindings keyed by role
    name so callers can pass them to the dispatcher without resolving
    a second time.
    """
    resolved = _resolve_workflow_role_bindings(workflow, workflow_name, config)

    first_state_for_role: dict[str, str] = {}
    needed: dict[str, str] = {}
    for state in workflow.states:
        if state.role is None:
            continue
        if state.actor.kind not in ("model", "agent"):
            continue
        first_state_for_role.setdefault(state.role, state.name)
        needed.setdefault(state.role, state.actor.kind)

    mismatches: list[str] = []
    for role_name, expected_kind in needed.items():
        binding = resolved[role_name]
        adapter_kind = _ADAPTER_TO_KIND.get(binding.adapter)
        if adapter_kind is None:
            mismatches.append(
                f"role {role_name!r} (state {first_state_for_role[role_name]!r}): "
                f"adapter {binding.adapter!r} is not a known orchestra adapter"
            )
            continue
        if adapter_kind != expected_kind:
            mismatches.append(
                f"role {role_name!r} (state {first_state_for_role[role_name]!r}): "
                f"adapter {binding.adapter!r} serves backing {adapter_kind!r} "
                f"but the state's actor kind is {expected_kind!r}"
            )
    if mismatches:
        raise ConfigError(
            f"workflow {workflow_name!r}: role-adapter kind mismatch:\n  "
            + "\n  ".join(mismatches)
        )
    _apply_workflow_specific_rules(workflow, resolved, workflow_name)
    return resolved


# --------------------------------------------------------------------
# Workflow-specific config validation rules
#
# Some shipped workflows enforce binding-level invariants the grammar
# cannot express. The Iterate-Until-Acceptable workflow requires the
# proposer and reviewer to resolve to distinct actors so the review
# is independent. The Propose-Review-Judge-Implement workflow has the
# same distinct-actor rule plus a workspace-mutation rule (only the
# implementer may be bound to a "mutating" adapter). Both checks run
# after role-binding resolution and adapter-kind matching but before
# the executor starts.
# --------------------------------------------------------------------


def _actor_identity(binding: RoleBinding) -> tuple[str, str | None]:
    """The (adapter, model) tuple used as actor identity.

    Per
    ``design/iteration-and-implementation-workflows.md`` the
    distinct-actor constraint is about training-data independence,
    not prompt independence; the role binding's ``parameters`` map
    is excluded so two roles bound to the same (adapter, model) with
    different system prompts or temperatures still count as the same
    actor.
    """
    return (binding.adapter, binding.model)


def _adapter_workspace_mutation(binding: RoleBinding) -> str:
    """Read the ``WORKSPACE_MUTATION`` class-level metadata off the
    adapter class the binding names. Fails closed: an unknown
    adapter, a missing attribute, or an out-of-vocabulary value
    raises ``ConfigError`` rather than defaulting to a permissive
    classification.

    The earlier defaulting-to-``"text_only"`` fallback could let a
    mutating adapter with broken metadata pass the PRJI proposer/
    reviewer/judge bindings (which forbid mutating adapters). The
    audit pass against the implementation flagged it as a SERIOUS
    finding; the contract is now read from the class without
    instantiation, and any contract violation aborts validation.
    """
    cls = _ADAPTER_CLASSES.get(binding.adapter)
    if cls is None:
        raise ConfigError(
            f"adapter {binding.adapter!r} is not registered in "
            "_ADAPTER_CLASSES; cannot determine workspace_mutation. "
            f"Known adapters: {sorted(_ADAPTER_CLASSES)}"
        )
    if not hasattr(cls, "WORKSPACE_MUTATION"):
        raise ConfigError(
            f"adapter {cls.__name__!r}: missing required class-level "
            "'WORKSPACE_MUTATION' attribute. The adapter contract "
            "requires every adapter class to declare "
            "'WORKSPACE_MUTATION = \"mutating\"' or "
            "'WORKSPACE_MUTATION = \"text_only\"'."
        )
    value = cls.WORKSPACE_MUTATION
    if value not in WORKSPACE_MUTATION_VALUES:
        raise ConfigError(
            f"adapter {cls.__name__!r}: WORKSPACE_MUTATION value "
            f"{value!r} is not valid. Must be one of "
            f"{sorted(WORKSPACE_MUTATION_VALUES)}."
        )
    return str(value)


def _apply_workflow_specific_rules(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    workflow_name: str,
) -> None:
    rule = _WORKFLOW_RULES.get(workflow.name)
    if rule is None:
        return
    rule(workflow, role_bindings, workflow_name)


def _validate_iterate_until_acceptable(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    workflow_name: str,
) -> None:
    """Enforce the iterate_until_acceptable distinct-actor rule:
    proposer and reviewer must resolve to distinct (adapter, model)
    tuples. Judge typically resolves to the same actor as proposer;
    that is permitted.
    """
    missing = [r for r in ("proposer", "reviewer") if r not in role_bindings]
    if missing:
        raise ConfigError(
            f"workflow {workflow_name!r}: missing required role "
            f"bindings: {missing!r}"
        )
    proposer_identity = _actor_identity(role_bindings["proposer"])
    reviewer_identity = _actor_identity(role_bindings["reviewer"])
    if proposer_identity == reviewer_identity:
        raise ConfigError(
            f"workflow {workflow_name!r}: 'proposer' and 'reviewer' "
            "resolve to the same actor "
            f"(adapter={proposer_identity[0]!r}, "
            f"model={proposer_identity[1]!r}). The iterate-until-"
            "acceptable pattern requires the reviewer to be a "
            "different actor so the critique is independent of the "
            "proposer's training data and blind spots."
        )


def _validate_prji(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    workflow_name: str,
) -> None:
    """Enforce the propose_review_judge_implement constraints:

    - Proposer, reviewer, and implementer must resolve to pairwise
      distinct (adapter, model) tuples.
    - Implementer must be bound to a "mutating" adapter.
    - Proposer, reviewer, and judge must each be bound to a
      "text_only" adapter.
    """
    required = ("proposer", "reviewer", "judge_role", "implementer")
    missing = [r for r in required if r not in role_bindings]
    if missing:
        raise ConfigError(
            f"workflow {workflow_name!r}: missing required role "
            f"bindings: {missing!r}"
        )
    distinct_roles = ("proposer", "reviewer", "implementer")
    seen: dict[tuple[str, str | None], str] = {}
    for role_name in distinct_roles:
        identity = _actor_identity(role_bindings[role_name])
        prior = seen.get(identity)
        if prior is not None:
            raise ConfigError(
                f"workflow {workflow_name!r}: roles {prior!r} and "
                f"{role_name!r} both resolve to actor "
                f"(adapter={identity[0]!r}, model={identity[1]!r}). "
                "PRJI requires proposer, reviewer, and implementer "
                "to be pairwise distinct so the review and the fix "
                "do not share blind spots with what is being judged."
            )
        seen[identity] = role_name
    implementer_mut = _adapter_workspace_mutation(role_bindings["implementer"])
    if implementer_mut != "mutating":
        raise ConfigError(
            f"workflow {workflow_name!r}: 'implementer' is bound to "
            f"adapter {role_bindings['implementer'].adapter!r}, which "
            "self-classifies as 'text_only'. The implementer is the "
            "only role permitted to mutate the workspace; bind it to "
            "a mutating adapter (the *_agent variants)."
        )
    for role_name in ("proposer", "reviewer", "judge_role"):
        mut = _adapter_workspace_mutation(role_bindings[role_name])
        if mut == "mutating":
            raise ConfigError(
                f"workflow {workflow_name!r}: role {role_name!r} is "
                f"bound to adapter "
                f"{role_bindings[role_name].adapter!r}, which "
                "self-classifies as 'mutating'. PRJI restricts "
                "workspace mutation to the implementer; bind this "
                "role to a text-only adapter (the *_text variants)."
            )


def _validate_council_four(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    workflow_name: str,
) -> None:
    """Enforce the council_four distinct-actor rule:

    - All five required roles present (framer + four proposers +
      synthesizer).
    - The four proposers must resolve to pairwise distinct
      (adapter, model) tuples. Otherwise the council is not actually
      drawing on N distinct model biases.
    - The synthesizer must differ from each of the four proposers.
      A model judging its own proposal against itself loses the
      multi-model-judging discipline; for the initial scaffold this
      is a hard refusal rather than auto-rotation with own-proposal
      exclusion. Auto-rotation is defensible later but adds
      transition semantics that need explicit test coverage.
    - Framer's identity is unconstrained; it can match any other role.
    """
    required = (
        "framer",
        "proposer_code",
        "proposer_codex",
        "proposer_kimi",
        "proposer_deepseek",
        "synthesizer",
    )
    missing = [r for r in required if r not in role_bindings]
    if missing:
        raise ConfigError(
            f"workflow {workflow_name!r}: missing required role "
            f"bindings: {missing!r}"
        )
    proposer_roles = (
        "proposer_code",
        "proposer_codex",
        "proposer_kimi",
        "proposer_deepseek",
    )
    seen: dict[tuple[str, str | None], str] = {}
    for role_name in proposer_roles:
        identity = _actor_identity(role_bindings[role_name])
        prior = seen.get(identity)
        if prior is not None:
            raise ConfigError(
                f"workflow {workflow_name!r}: proposer roles {prior!r} "
                f"and {role_name!r} both resolve to actor "
                f"(adapter={identity[0]!r}, model={identity[1]!r}). "
                "council_four expects four distinct model biases; "
                "bind each proposer to a different (adapter, model) "
                "tuple."
            )
        seen[identity] = role_name
    synth_identity = _actor_identity(role_bindings["synthesizer"])
    for role_name in proposer_roles:
        if synth_identity == _actor_identity(role_bindings[role_name]):
            raise ConfigError(
                f"workflow {workflow_name!r}: 'synthesizer' resolves "
                f"to the same actor as {role_name!r} "
                f"(adapter={synth_identity[0]!r}, "
                f"model={synth_identity[1]!r}). council_four requires "
                "the synthesizer to differ from every proposer so a "
                "model is not judging its own output. Configure a "
                "fifth distinct binding for 'synthesizer'."
            )


_WORKFLOW_RULES: dict[
    str,
    Callable[[Workflow, dict[str, RoleBinding], str], None],
] = {
    "iterate_until_acceptable": _validate_iterate_until_acceptable,
    "propose_review_judge_implement": _validate_prji,
    "council_four": _validate_council_four,
}


def _validate_inputs(workflow: Workflow, inputs: dict[str, Any]) -> None:
    declared = {ext.name for ext in workflow.external_inputs}
    extras = set(inputs) - declared
    if extras:
        raise WorkflowApiError(
            f"unknown inputs: {sorted(extras)}. "
            f"Declared: {sorted(declared)}"
        )
    missing = declared - set(inputs)
    if missing:
        raise WorkflowApiError(
            f"missing required inputs: {sorted(missing)}"
        )


def _gather_artifacts(
    workflow: Workflow, store: ArtifactStore
) -> dict[str, ArtifactView]:
    out: dict[str, ArtifactView] = {}
    for art in workflow.artifacts:
        latest = store.read_latest(art.name)
        if latest is None:
            continue
        out[art.name] = ArtifactView(
            name=art.name,
            type=latest.type,
            version_id=latest.version_id,
            value=latest.value,
        )
    return out


def _build_summary(
    *,
    terminal: str,
    envelope: Envelope,
    artifacts: dict[str, ArtifactView],
) -> dict[str, Any]:
    """Compose the adapter-facing summary dict."""
    payload = envelope.payload or {}
    fields = payload.get("fields") or {}
    output = payload.get("output")
    if not isinstance(output, str):
        output = ""
    exit_code = fields.get("exit_code")
    changed_files = fields.get("changed_files")
    log_path = fields.get("log_path")
    summary: dict[str, Any] = {
        "terminal": terminal,
        "outcome": envelope.outcome,
        "status": envelope.status,
        "final_state": envelope.state_id,
        "output": output,
        "artifacts": sorted(artifacts.keys()),
    }
    if exit_code is not None:
        summary["exit_code"] = int(exit_code)
    if changed_files is not None:
        summary["changed_files"] = list(changed_files)
        summary["files_changed"] = bool(changed_files)
    if log_path is not None:
        summary["adapter_log"] = str(log_path)
    if envelope.error is not None:
        summary["error"] = {
            "kind": envelope.error.kind,
            "message": envelope.error.message,
        }
    return summary


def _maybe_inject_final_prompt(
    workflow: Workflow,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Inject ``final_prompt`` for code-edit workflows.

    The packaged code-edit workflows declare ``final_prompt`` as an
    external input. The api computes its value by running the lifted
    mcloop prompt builders on the other inputs, mirroring the
    branching mcloop's run_task does at the call site.
    """
    declared = {ext.name for ext in workflow.external_inputs}
    if FINAL_PROMPT_INPUT not in declared:
        return inputs
    if FINAL_PROMPT_INPUT in inputs:
        return inputs
    final_prompt = build_code_edit_prompt(inputs)
    return {**inputs, FINAL_PROMPT_INPUT: final_prompt}


# --------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------


def run_workflow(
    name: str,
    inputs: dict[str, Any],
    config: OrchestraConfig | dict[str, Any],
    *,
    invocation_options: dict[str, Any] | None = None,
    project_dir: Path | str | None = None,
    data_root: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
    quiet: bool = False,
) -> WorkflowRunResult:
    """Execute a configured workflow by name.

    ``inputs`` must satisfy the workflow's declared ``external_input``s
    exactly. ``invocation_options`` carries per-call overrides
    (``model``, ``timeout`` in seconds, ``log_dir``, ``project_dir``,
    plus any adapter-specific keys) that flow through to the adapter
    via ``backing_options`` and override the role's configured model
    on the actor binding.

    Progress reporting is on by default. Library callers see one line
    per ``state_enter`` and ``state_exit`` on stderr, plus a
    parallel-block header and per-completion lines for fan-out groups.
    Pass ``quiet=True`` to suppress, or pass an explicit
    ``progress_callback`` to install a custom reporter. The CLI also
    installs its own callback up front (``stderr_reporter`` by
    default, ``silent_reporter`` for ``--quiet``).
    """
    if isinstance(config, dict):
        cfg = OrchestraConfig.from_dict(config)
    else:
        cfg = config
    workflow_cfg = cfg.workflow(name)

    workflow_path = resolve_workflow_path(
        workflow_cfg.pattern, project_dir=project_dir
    )

    # Two-pass load: a pre-registry with placeholder backings for any
    # kind the workflow might reference (the loader validates that
    # every ``actor`` clause names a registered backing), then resolve
    # the per-workflow role bindings against the project config and
    # build the runtime registry whose dispatchers fan out per role.
    pre_registry = _pre_load_registry()
    workflow = load_workflow(workflow_path, pre_registry)
    role_bindings = _validate_role_bindings(workflow, name, cfg)
    registry = _build_registry(role_bindings)

    run_id = new_run_id()
    if data_root is None:
        run_root = Path.home() / ".orchestra" / "runs"
    else:
        run_root = Path(data_root)
    run_dir = run_root / run_id
    # Pass-8 fix #2: run directories carry prompt snapshots, log
    # files, and SQLite stores that may contain credentials and
    # proprietary content. Default umask 022 leaves them
    # world-readable. Force 0700 on the run-root tree and the
    # per-run directory so only the owning user can enumerate the
    # contents.
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        run_root.chmod(0o700)
    except OSError:
        pass
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        run_dir.chmod(0o700)
    except OSError:
        pass

    workflow = _apply_instruction_templates(
        workflow,
        role_bindings,
        project_dir=Path(project_dir) if project_dir is not None else None,
        run_dir=run_dir,
    )

    enriched = _maybe_inject_final_prompt(workflow, inputs)
    _validate_inputs(workflow, enriched)

    inv_opts: dict[str, Any] = dict(invocation_options or {})
    if project_dir is not None and "project_dir" not in inv_opts:
        inv_opts["project_dir"] = str(project_dir)

    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log_path = run_dir / "log.jsonl"
    log = LogWriter(log_path, run_id)
    from orchestra.prompt_snapshot import snapshot_prompt_sources
    workflow, prompt_snapshot_manifest = snapshot_prompt_sources(
        workflow, run_dir
    )
    log.write(
        "run_start",
        fields={
            "workflow_path": str(Path(workflow_path).resolve()),
            "workflow_digest": hashlib.sha256(
                Path(workflow_path).read_bytes()
            ).hexdigest(),
            "prompt_snapshot_manifest": prompt_snapshot_manifest,
            "workflow_name": workflow.name,
            "config_name": name,
            "pattern": workflow_cfg.pattern,
            "spec_version": workflow.spec_version,
            "external_inputs": enriched,
            "max_total_steps": workflow.max_total_steps,
            "invocation_options": _safe_options(inv_opts),
        },
    )

    resolved_progress = _resolve_progress_callback(progress_callback, quiet)
    executor_progress = _wrap_progress_callback(
        resolved_progress, role_bindings
    )
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs=dict(enriched),
        invocation_options=inv_opts,
        progress_callback=executor_progress,
        criteria=cfg.criteria,
        decision_consistency_mode=mode_for_workflow(name),
    )

    terminal: str = "stop"
    try:
        terminal = executor.run_to_completion()
    finally:
        log.write(
            "run_end",
            fields={"terminal": terminal},
        )
        log.close()

    envelopes = executor._envelopes
    last_state = (
        executor._last_state
        if executor._last_state is not None
        else (workflow.states[-1].name if workflow.states else workflow.start_state_name())
    )
    if last_state in envelopes:
        envelope = envelopes[last_state]
    elif envelopes:
        envelope = next(iter(reversed(list(envelopes.values()))))
    else:
        store.close()
        raise WorkflowApiError(
            f"workflow {name!r} produced no envelopes"
        )

    artifacts = _gather_artifacts(workflow, store)
    summary = _build_summary(
        terminal=terminal, envelope=envelope, artifacts=artifacts
    )
    store.close()

    return WorkflowRunResult(
        run_id=run_id,
        terminal=terminal,
        envelope=envelope,
        artifacts=artifacts,
        log_path=log_path,
        summary=summary,
    )


def _safe_options(opts: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-friendly view of ``opts`` for the run_start log."""
    out: dict[str, Any] = {}
    for k, v in opts.items():
        if isinstance(v, Path):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def run_verb(
    verb_name: str,
    query: str,
    config: OrchestraConfig,
    *,
    history: str = "",
    progress_callback: ProgressCallback | None = None,
    quiet: bool = False,
    project_dir: Path | str | None = None,
) -> str:
    """Run the workflow named by ``verb_name`` and return the answer text.

    The verb resolves to a workflow name through ``config.verbs``. The
    workflow runs with ``inputs={"query": query}`` and, when
    ``history`` is non-empty AND the workflow declares a ``history``
    external_input, ``inputs["history"] = history`` too. Workflows
    that do not declare ``history`` ignore it silently so a custom
    verb pointing at a non-ask workflow keeps working.

    ``progress_callback`` (optional) is forwarded to ``run_workflow``
    so the CLI and REPL can stream per-state progress to stderr.

    ``project_dir`` is threaded into both the introspection load and
    the run so a project-local override at
    ``<project_dir>/.orchestra/workflows/<name>.orc`` is honoured by
    both phases. ``resolve_workflow_path`` documents that precedence;
    pre-fix the verb path resolved with ``project_dir=None`` and ran
    the packaged workflow even when an override existed.

    Returns the final state's text payload, which the CLI prints to
    stdout. Raises ``WorkflowApiError`` if the verb is unknown, the
    workflow does not terminate in ``done``, or the final envelope
    carries no text response.
    """
    if verb_name not in config.verbs:
        raise WorkflowApiError(
            f"unknown verb {verb_name!r}. Configured: "
            f"{sorted(config.verbs)}"
        )
    workflow_name = config.verbs[verb_name].workflow
    inputs: dict[str, Any] = {"query": query}
    # history threads through only when the workflow asks for it.
    # The pre-load registry is enough to introspect declared inputs;
    # run_workflow does the real load again with the runtime registry.
    workflow_path = resolve_workflow_path(
        workflow_name, project_dir=project_dir
    )
    workflow = load_workflow(workflow_path, _pre_load_registry())
    declared = {ext.name for ext in workflow.external_inputs}
    if "history" in declared:
        inputs["history"] = history
    result = run_workflow(
        workflow_name,
        inputs,
        config,
        progress_callback=progress_callback,
        quiet=quiet,
        project_dir=project_dir,
    )
    if result.terminal != "done":
        raise WorkflowApiError(
            f"verb {verb_name!r} (workflow {workflow_name!r}) did not "
            f"complete: terminal={result.terminal!r}. Run dir: "
            f"{result.log_path.parent}"
        )
    output = result.summary.get("output", "")
    if not isinstance(output, str) or not output:
        raise WorkflowApiError(
            f"verb {verb_name!r} (workflow {workflow_name!r}) produced "
            "no text response. Check the run log for details: "
            f"{result.log_path}"
        )
    return output
