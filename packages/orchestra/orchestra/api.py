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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.config import ConfigError, OrchestraConfig, RoleBinding
from orchestra.errors import OrchestraError
from orchestra.executor.executor import Executor, new_run_id
from orchestra.executor.parsers import _identity_text_parse_fn
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogWriter
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

# Maps a configured adapter name to the workflow actor kind it serves.
# Slice 1 grammar limits the kind vocabulary to {model, agent, shell,
# human}; the integration plan pairs text adapters with the ``model``
# kind and edit-agent adapters with the ``agent`` kind.
_ADAPTER_TO_KIND: dict[str, str] = {
    "claude_code_text": "model",
    "claude_code_agent": "agent",
}

_ADAPTER_CLASSES: dict[str, type] = {
    "claude_code_text": ClaudeCodeTextAdapter,
    "claude_code_agent": ClaudeCodeAgentAdapter,
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
        # Aggregate the manages_own_timeout flag so the executor can
        # skip its thread-based timer when every underlying adapter
        # owns its timeout. Mixed adapter sets fall back to executor
        # enforcement (False) because the safe answer is the more
        # restrictive one.
        self.manages_own_timeout = all(
            getattr(a, "manages_own_timeout", False)
            for a in self._adapters.values()
        )

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
        elif adapter_name == "claude_code_text":
            params.setdefault("allowed_tools", binding.tools)
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


def _validate_role_bindings(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
) -> None:
    """Every actor role declared on a workflow state must be bound in
    the project config.

    Without this, a state whose role is missing from the config would
    silently fall back to the slice-1 mock under the actor kind, or
    (worse) reuse a different role's adapter via the dispatcher's
    one-adapter shortcut. Both produce wrong behavior at runtime
    (mock outputs or off-role models) without surfacing the gap. This
    check fires early so the consumer sees a clear ConfigError.
    """
    needed: dict[str, str] = {}
    for state in workflow.states:
        if state.role is None:
            continue
        if state.actor.kind not in ("model", "agent"):
            continue
        needed.setdefault(state.role, state.actor.kind)
    missing = sorted(name for name in needed if name not in role_bindings)
    if missing:
        raise ConfigError(
            f"workflow {workflow.name!r}: role bindings missing in config: "
            f"{missing}. Configured: {sorted(role_bindings)}"
        )


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
) -> WorkflowRunResult:
    """Execute a configured workflow by name.

    ``inputs`` must satisfy the workflow's declared ``external_input``s
    exactly. ``invocation_options`` carries per-call overrides
    (``model``, ``timeout`` in seconds, ``log_dir``, ``project_dir``,
    plus any adapter-specific keys) that flow through to the adapter
    via ``backing_options`` and override the role's configured model
    on the actor binding.
    """
    if isinstance(config, dict):
        cfg = OrchestraConfig.from_dict(config)
    else:
        cfg = config
    workflow_cfg = cfg.workflow(name)

    workflow_path = resolve_workflow_path(
        workflow_cfg.pattern, project_dir=project_dir
    )

    registry = _build_registry(workflow_cfg.roles)
    workflow = load_workflow(workflow_path, registry)
    _validate_role_bindings(workflow, workflow_cfg.roles)

    run_id = new_run_id()
    if data_root is None:
        run_root = Path.home() / ".orchestra" / "runs"
    else:
        run_root = Path(data_root)
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    workflow = _apply_instruction_templates(
        workflow,
        workflow_cfg.roles,
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
    log.write(
        "run_start",
        fields={
            "workflow_path": str(workflow_path),
            "workflow_name": workflow.name,
            "config_name": name,
            "pattern": workflow_cfg.pattern,
            "spec_version": workflow.spec_version,
            "external_inputs": enriched,
            "max_total_steps": workflow.max_total_steps,
            "invocation_options": _safe_options(inv_opts),
        },
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
