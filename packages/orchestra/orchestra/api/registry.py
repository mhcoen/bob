"""Adapter and registry construction for orchestra.run_workflow."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.adapters.codex_agent import CodexAgentAdapter
from orchestra.adapters.codex_text import CodexTextAdapter
from orchestra.api._common import WorkflowApiError
from orchestra.config import RoleBinding
from orchestra.executor.parsers import _identity_text_parse_fn
from orchestra.registry.registry import (
    ProfileRegistry,
    ResultParser,
    with_core,
)
from orchestra.spine import (
    NO_INITIAL,
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
            raise WorkflowApiError("_PerRoleDispatcher requires at least one role-adapter binding")
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
            f"no adapter configured for role {role!r}. Configured roles: {sorted(self._adapters)}"
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
            input_schema=dict(_ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA),
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
            f"unknown adapter {adapter_name!r}. Known: {sorted(_ADAPTER_CLASSES)}"
        )
    cls = _ADAPTER_CLASSES[adapter_name]
    kind = _ADAPTER_TO_KIND[adapter_name]
    params: dict[str, Any] = dict(binding.parameters)
    if binding.model is not None:
        params.setdefault("default_model", binding.model)
    if binding.tools and binding.tools != "default":
        if adapter_name == "claude_code_agent":
            params.setdefault("default_allowed_tools", binding.tools)
        elif adapter_name in (
            "claude_code_text",
            "claude_code_text_kimi",
            "claude_code_text_deepseek",
        ):
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
            workflow_source_dir=Path(workflow.source_dir) if workflow.source_dir else None,
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
