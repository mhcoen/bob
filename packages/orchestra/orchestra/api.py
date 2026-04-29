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
distinguishes text-role adapters from edit-agent adapters by
backing name (``claude_code_text`` vs ``claude_code_agent``). The api
bridges the two: it reads the role-binding's ``adapter`` key from
config, instantiates the corresponding class with the role's model and
parameters, and registers it under both its canonical backing name
(for introspection and direct lookup) and the conventional kind name
the workflow uses for that role (``model`` for text, ``agent`` for
edit-agent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.config import OrchestraConfig, RoleBinding
from orchestra.errors import OrchestraError
from orchestra.executor.executor import Executor, new_run_id
from orchestra.executor.parsers import _identity_text_parse_fn
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogWriter
from orchestra.registry.registry import (
    ProfileRegistry,
    ResultParser,
    with_core,
)
from orchestra.spine import NO_INITIAL, Envelope, Workflow
from orchestra.store import ArtifactStore

# Maps a role's adapter name (from config) to the workflow actor kind
# the api will register that adapter under. Slice-1 grammar limits the
# kind vocabulary to {model, agent, shell, human}; the integration plan
# pairs text adapters with the ``model`` kind and edit-agent adapters
# with the ``agent`` kind.
_ADAPTER_TO_KIND: dict[str, str] = {
    "claude_code_text": "model",
    "claude_code_agent": "agent",
}

_ADAPTER_CLASSES: dict[str, type] = {
    "claude_code_text": ClaudeCodeTextAdapter,
    "claude_code_agent": ClaudeCodeAgentAdapter,
}


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
    """Outcome of a ``run_workflow`` invocation.

    Carries the fields the call site needs without forcing it to dig
    through the artifact store. ``terminal`` is ``"done"`` or ``"stop"``
    matching the executor's terminal target. ``envelope`` is the final
    state's envelope.
    """

    run_id: str
    terminal: str
    envelope: Envelope
    artifacts: dict[str, ArtifactView]
    log_path: Path
    summary: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------
# Registry construction
# --------------------------------------------------------------------


def _adapter_factory(
    binding: RoleBinding,
) -> tuple[Any, str]:
    """Return ``(factory, kind)`` for a configured role.

    The factory closes over the role's model and parameters so that
    ``adapter_for(kind)`` returns an instance with the right defaults
    without needing per-call wiring.
    """
    adapter_name = binding.adapter
    if adapter_name not in _ADAPTER_CLASSES:
        raise WorkflowApiError(
            f"unknown adapter {adapter_name!r}. Known: "
            f"{sorted(_ADAPTER_CLASSES)}"
        )
    cls = _ADAPTER_CLASSES[adapter_name]
    kind = _ADAPTER_TO_KIND[adapter_name]
    params = dict(binding.parameters)
    if binding.model is not None:
        params.setdefault("default_model", binding.model)
    if binding.tools and binding.tools != "default":
        if adapter_name == "claude_code_agent":
            params.setdefault("default_allowed_tools", binding.tools)
        elif adapter_name == "claude_code_text":
            params.setdefault("allowed_tools", binding.tools)

    def factory() -> Any:
        return cls(**params)

    return factory, kind


def _build_registry(
    role_bindings: dict[str, RoleBinding],
) -> ProfileRegistry:
    """Build a registry whose backings are wired to the configured
    adapters.

    Backings used by the configured roles ("model" and/or "agent") get
    the configured adapter. Mocks are kept for the kinds the workflow
    might still touch (``human``, ``shell``) so a multi-step workflow
    that includes a human gate or a shell step does not crash on
    lookup.
    """
    reg = with_core()

    needed_kinds: set[str] = set()
    for binding in role_bindings.values():
        factory, kind = _adapter_factory(binding)
        # Replace the mock under this kind with the configured adapter.
        # Direct dict assignment is intentional: ``register_actor_backing``
        # rejects duplicate names, but the api needs to override the
        # mock from with_core() for the kinds the configured roles use.
        reg.actor_backings[kind] = factory
        # Invalidate any cached instance so the new factory is honored
        # on the next lookup.
        reg._adapter_cache.pop(kind, None)
        # Also register under the canonical adapter name when free, so
        # callers can introspect or look up by adapter name directly.
        canonical = binding.adapter
        if canonical not in reg.actor_backings:
            reg.actor_backings[canonical] = factory
        needed_kinds.add(kind)

    # The slice-1 identity_text parser is registered only for the
    # ``model`` backing. Add an equivalent registration for ``agent`` so
    # workflows whose final state is ``actor agent`` can write a text
    # artifact without needing a profile registration.
    if "agent" in needed_kinds:
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
    """Compose the adapter-facing summary dict.

    Includes the terminal outcome, the final state's exit code if the
    payload carries one, the final state's output text, and whether
    any files changed (when the payload reports ``changed_files``).
    """
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


# --------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------


def run_workflow(
    name: str,
    inputs: dict[str, Any],
    config: OrchestraConfig | dict[str, Any],
    *,
    project_dir: Path | str | None = None,
    data_root: Path | str | None = None,
) -> WorkflowRunResult:
    """Execute a configured workflow by name.

    ``name`` is the config key under ``workflows`` (e.g. ``code_edit``).
    The config picks the pattern (the ``.orc`` file in the package or
    the project override), and the per-role adapter, model, template,
    and tool overrides.

    ``inputs`` are passed through to the workflow's ``external_input``
    declarations. The api validates that every declared input is
    supplied and no extra inputs are passed.

    ``project_dir`` controls workflow override lookup and the default
    working directory passed to subprocess adapters via
    ``external_inputs``. ``data_root`` controls where run state is
    persisted; defaults to ``~/.orchestra/runs``.
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
    _validate_inputs(workflow, inputs)

    run_id = new_run_id()
    if data_root is None:
        run_root = Path.home() / ".orchestra" / "runs"
    else:
        run_root = Path(data_root)
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

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
            "external_inputs": inputs,
            "max_total_steps": workflow.max_total_steps,
        },
    )

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs=dict(inputs),
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

    final_state_name = (
        workflow.states[-1].name if workflow.states else workflow.start_state_name()
    )
    envelopes = executor._envelopes
    last_state = (
        executor._last_state
        if executor._last_state is not None
        else final_state_name
    )
    if last_state in envelopes:
        envelope = envelopes[last_state]
    elif envelopes:
        # Fallback: the most recently executed state.
        envelope = next(iter(reversed(list(envelopes.values()))))
    else:
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
