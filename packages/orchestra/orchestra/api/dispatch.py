"""Workflow dispatch: run_workflow, run_verb, run_role."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestra.api._common import (
    FINAL_PROMPT_INPUT,
    ArtifactView,
    IterativeDesignResult,
    WorkflowApiError,
    WorkflowRunResult,
)
from orchestra.api.bindings import (
    _resolve_progress_callback,
    _wrap_progress_callback,
)
from orchestra.api.registry import (
    _apply_instruction_templates,
    _build_registry,
    _initialize_store,
    _pre_load_registry,
)
from orchestra.api.transcript import (
    _build_transcript,
    _count_judge_rounds,
    _derive_termination,
    _IncrementalTranscriptWriter,
    _select_final_artifact,
)
from orchestra.api.validators import (
    _validate_inputs,
    _validate_role_bindings,
)
from orchestra.config import (
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
    load_config,
)
from orchestra.executor.criteria import mode_for_workflow
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogWriter
from orchestra.progress import (
    ProgressCallback,
)
from orchestra.prompts import build_code_edit_prompt
from orchestra.registry.registry import BUILTIN_MODEL_IDENTIFIERS, ProfileRegistry
from orchestra.spine import (
    Envelope,
    Workflow,
)
from orchestra.store import ArtifactStore

_CODE_EDIT_WORKFLOW_NAMES: frozenset[str] = frozenset(
    {"single", "draft_then_adjudicate", "propose_critique_synthesize"}
)


def _gather_artifacts(workflow: Workflow, store: ArtifactStore) -> dict[str, ArtifactView]:
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
    registry_customizer: Callable[[ProfileRegistry], None] | None = None,
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
    ``progress_callback`` to install a custom reporter. The CLI
    installs ``stderr_reporter`` up front for every dispatch.

    ``registry_customizer`` (optional) is a caller-supplied callback
    invoked on BOTH the pre-load registry (before the workflow is
    loaded, so the loader's transform-record validator sees the
    registration) and the runtime registry (before the executor runs,
    so the executor's transform states can resolve the registration).
    It is invoked after core registration on each registry. A consumer
    uses it to register a caller-owned ``actor transform`` without
    Orchestra importing the consumer package: Orchestra exposes the
    callback, the caller supplies a function that registers its own
    transform. The same callable must be safe to call twice (once per
    registry); the built-in registration helpers are idempotent and
    callers should match that.
    """
    if name == "council_four":
        import warnings

        warnings.warn(
            "workflow name 'council_four' is deprecated; the workflow "
            "split into 'council_four_canonical' (McLoop-executable plan "
            "authoring) and 'council_four_reauthor' (Slice C lineage-"
            "preserving re-author). The old name continues to work for "
            "this release but will be removed in a follow-up. See "
            "orchestra/design/synthesizer-output-contract.md.",
            DeprecationWarning,
            stacklevel=2,
        )
    if isinstance(config, dict):
        cfg = OrchestraConfig.from_dict(config)
    else:
        cfg = config
    workflow_cfg = cfg.workflow(name)

    workflow_path = resolve_workflow_path(workflow_cfg.pattern, project_dir=project_dir)

    # Two-pass load: a pre-registry with placeholder backings for any
    # kind the workflow might reference (the loader validates that
    # every ``actor`` clause names a registered backing), then resolve
    # the per-workflow role bindings against the project config and
    # build the runtime registry whose dispatchers fan out per role.
    pre_registry = _pre_load_registry()
    if registry_customizer is not None:
        registry_customizer(pre_registry)
    workflow = load_workflow(workflow_path, pre_registry)
    role_bindings = _validate_role_bindings(workflow, name, cfg)
    registry = _build_registry(role_bindings)
    if registry_customizer is not None:
        registry_customizer(registry)

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

    workflow, prompt_snapshot_manifest = snapshot_prompt_sources(workflow, run_dir)
    log.write(
        "run_start",
        fields={
            "workflow_path": str(Path(workflow_path).resolve()),
            "workflow_digest": hashlib.sha256(Path(workflow_path).read_bytes()).hexdigest(),
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
    executor_progress = _wrap_progress_callback(resolved_progress, role_bindings)
    transcript_path = run_dir / "transcript.jsonl"
    transcript_writer = _IncrementalTranscriptWriter(transcript_path, run_dir, workflow)
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
        on_state_exit=transcript_writer,
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
        raise WorkflowApiError(f"workflow {name!r} produced no envelopes")

    artifacts = _gather_artifacts(workflow, store)
    summary = _build_summary(terminal=terminal, envelope=envelope, artifacts=artifacts)
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
        raise WorkflowApiError(f"unknown verb {verb_name!r}. Configured: {sorted(config.verbs)}")
    workflow_name = config.verbs[verb_name].workflow
    inputs: dict[str, Any] = {"query": query}
    # history threads through only when the workflow asks for it.
    # The pre-load registry is enough to introspect declared inputs;
    # run_workflow does the real load again with the runtime registry.
    workflow_path = resolve_workflow_path(workflow_name, project_dir=project_dir)
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


# --------------------------------------------------------------------
# run_role: user-facing role-to-workflow entry point
# --------------------------------------------------------------------


# Outcomes that indicate the workflow ended in a failure transition
# (as opposed to a judge-driven convergence or a cap-driven CAPPED
# termination). Per T-000007: stuck/error/timeout transitions map to
# ERROR; the executor uses the same names for adapter failures and
# the loader-side stuck condition.
_ERROR_OUTCOMES: frozenset[str] = frozenset({"stuck", "error", "timeout", "cancelled"})


def _resolve_compound_model_identifiers(
    role_name: str,
    bindings: dict[str, RoleBinding],
) -> dict[str, RoleBinding]:
    """Replace short-form leaf bindings with their resolved (adapter,
    model) tuples.

    A leaf binding with ``adapter is None`` is the short form: its
    ``model`` field names a registered model identifier (``opus``,
    ``codex``, ``kimi``, ...). Look the identifier up in the built-in
    model identifier table (the same table ``ProfileRegistry`` is
    populated from in ``with_core()``) and synthesize a new
    ``RoleBinding`` with both adapter and model filled in. Other
    binding fields (``instruction_template``, ``tools``,
    ``parameters``) are carried through unchanged.

    Raises ``WorkflowApiError`` when a short-form binding names an
    identifier that is not registered. The error names both the
    missing identifier and the available identifiers so the user can
    correct the config without guessing.
    """
    resolved: dict[str, RoleBinding] = {}
    for leaf_name, binding in bindings.items():
        if binding.adapter is not None:
            resolved[leaf_name] = binding
            continue
        model_id = binding.model
        if not isinstance(model_id, str) or not model_id:
            raise WorkflowApiError(
                f"role {role_name!r}: leaf binding {leaf_name!r} omits "
                "'adapter' and 'model'; one of them is required"
            )
        identifier = BUILTIN_MODEL_IDENTIFIERS.get(model_id)
        if identifier is None:
            available = sorted(BUILTIN_MODEL_IDENTIFIERS)
            raise WorkflowApiError(
                f"role {role_name!r}: leaf binding {leaf_name!r} names "
                f"unregistered model identifier {model_id!r}. "
                f"Available identifiers: {available}"
            )
        resolved[leaf_name] = RoleBinding(
            adapter=identifier.adapter,
            model=identifier.model,
            instruction_template=binding.instruction_template,
            tools=binding.tools,
            parameters=dict(binding.parameters),
        )
    return resolved


def _validate_design_distinct_actors(
    role_name: str,
    bindings: dict[str, RoleBinding],
) -> None:
    """Reject a ``design`` role binding whose judge and reviewer
    resolve to the same actor.

    The design loop's reviewer is supposed to be independent of the
    judge so the critique catches failure modes the judge would not
    catch on its own. Same-actor bindings collapse that independence
    silently; the workflow refuses to start so the misconfiguration
    is visible up front.

    The pair is identified by name (``judge_role`` or ``judge`` as
    the judge slot, ``reviewer`` as the reviewer slot); when the
    expected names are not present, this validator is a no-op so a
    differently-named pair pattern can reuse the ``design`` role
    binding shape without forcing this specific naming.
    """
    judge: RoleBinding | None = None
    for candidate in ("judge_role", "judge"):
        if candidate in bindings:
            judge = bindings[candidate]
            break
    reviewer = bindings.get("reviewer")
    if judge is None or reviewer is None:
        return
    if (judge.adapter, judge.model) == (reviewer.adapter, reviewer.model):
        raise WorkflowApiError(
            f"role {role_name!r}: judge and reviewer resolve to the "
            f"same actor (adapter={judge.adapter!r}, model={judge.model!r}). "
            "The design loop requires the reviewer to be a different "
            "actor so the critique is independent of the judge's "
            "training data and blind spots."
        )


def run_role(
    role_name: str,
    **kwargs: Any,
) -> IterativeDesignResult:
    """Run the workflow bound to ``role_name`` and return an
    ``IterativeDesignResult``.

    Reads the merged config (``~/.orchestra/config.json`` plus, when
    ``project_dir`` is in ``kwargs``, the project-local override) and
    resolves ``role_name`` against ``OrchestraConfig.role_bindings``.
    The matched ``CompoundRoleBinding`` names the workflow pattern to
    run plus the per-workflow-role leaf bindings the workflow's states
    will resolve at runtime; the api builds a synthetic
    ``OrchestraConfig`` carrying just those entries and dispatches to
    ``run_workflow``.

    ``**kwargs`` carries:

    - Reserved keys consumed by run_role itself: ``project_dir``,
      ``quiet``, ``progress_callback``, ``max_rounds``,
      ``invocation_options``, ``registry_customizer``.
    - Everything else is forwarded to ``run_workflow`` as workflow
      inputs (must match the workflow's declared ``external_input``s).

    ``registry_customizer`` (optional) is forwarded verbatim to
    ``run_workflow`` so a role-dispatched workflow can also register a
    caller-owned ``actor transform`` on both the pre-load and runtime
    registries. See ``run_workflow`` for the contract. Orchestra does
    not import any consumer package to support this: the caller owns
    the transform and supplies the callback.

    Termination is derived from the final ``transition`` log record's
    outcome and target — see ``_derive_termination`` and T-000007.

    Existing ``run_workflow`` callers are unaffected: this function
    constructs its own derived OrchestraConfig and never mutates the
    caller's config.
    """
    project_dir = kwargs.pop("project_dir", None)
    quiet = kwargs.pop("quiet", False)
    progress_callback = kwargs.pop("progress_callback", None)
    max_rounds_override = kwargs.pop("max_rounds", None)
    invocation_options = dict(kwargs.pop("invocation_options", {}) or {})
    registry_customizer = kwargs.pop("registry_customizer", None)
    inputs: dict[str, Any] = dict(kwargs)

    config = load_config(project_dir)
    compound = config.role_bindings.get(role_name)
    if compound is None:
        raise WorkflowApiError(
            f"unknown role {role_name!r}. Configured role_bindings: {sorted(config.role_bindings)}"
        )

    # T-000012: resolve the effective round cap. Per-call override
    # wins, otherwise fall back to the compound binding's
    # ``max_rounds``, otherwise default to 4. The workflow refuses to
    # start when the resolved value is not a positive int so a
    # zero/negative cap can never produce a cap-hit transition that
    # would terminate the workflow before any judge round completes.
    if max_rounds_override is not None:
        effective_max_rounds = max_rounds_override
    elif compound.max_rounds is not None:
        effective_max_rounds = compound.max_rounds
    else:
        effective_max_rounds = 4
    if isinstance(effective_max_rounds, bool) or not isinstance(effective_max_rounds, int):
        raise WorkflowApiError(
            f"role {role_name!r}: max_rounds must be an int, got "
            f"{type(effective_max_rounds).__name__}"
        )
    if effective_max_rounds <= 0:
        raise WorkflowApiError(
            f"role {role_name!r}: max_rounds must be > 0, got {effective_max_rounds}"
        )

    # Load the workflow up front so we can decide whether to inject
    # ``max_rounds`` as an external_input. Workflows that declare it
    # (design_loop) read the resolved cap through the GuardContext's
    # external_inputs lookup; other workflows would reject an
    # undeclared input and are left alone.
    workflow_path = resolve_workflow_path(compound.pattern, project_dir=project_dir)
    # Apply the caller's registry_customizer to this introspection load
    # too: run_workflow runs it again on its own registries, but this
    # up-front load (to decide max_rounds injection) uses a separate
    # pre-load registry and would otherwise reject a caller-owned
    # transform as unregistered before run_workflow ever sees it.
    pre_registry = _pre_load_registry()
    if registry_customizer is not None:
        registry_customizer(pre_registry)
    workflow = load_workflow(workflow_path, pre_registry)
    if any(ext.name == "max_rounds" for ext in workflow.external_inputs):
        inputs["max_rounds"] = effective_max_rounds

    # T-000014: resolve model identifiers on the compound binding's
    # leaf bindings. A leaf binding may name only ``model`` (e.g.
    # ``{"model": "opus"}``); look the identifier up in the
    # ProfileRegistry and fill in both adapter and model. An
    # unregistered identifier fails startup here, naming the missing
    # identifier and the available ones, so the downstream registry
    # builder never sees an unresolved binding.
    resolved_bindings = _resolve_compound_model_identifiers(role_name, compound.bindings)

    # T-000014: enforce that the design role binding's leaf bindings
    # resolve to distinct actors so the reviewer's critique is
    # independent of the judge's training data. Other compound
    # bindings are unconstrained.
    if role_name == "design":
        _validate_design_distinct_actors(role_name, resolved_bindings)

    # T-000003: a compound role may carry its own acceptance criteria
    # (CompoundRoleBinding.criteria). Forward those to the derived
    # config so they reach the executor; run_workflow forwards
    # ``cfg.criteria`` to the Executor unchanged. When the binding
    # declares no criteria, fall back to the merged top-level criteria
    # so a project-wide criteria set still applies.
    derived_criteria = compound.criteria if compound.criteria else config.criteria

    derived_cfg = OrchestraConfig(
        roles=resolved_bindings,
        workflows={compound.pattern: WorkflowConfig(pattern=compound.pattern)},
        verbs={},
        role_bindings={},
        criteria=derived_criteria,
    )

    result = run_workflow(
        compound.pattern,
        inputs,
        derived_cfg,
        invocation_options=invocation_options,
        project_dir=project_dir,
        progress_callback=progress_callback,
        quiet=quiet,
        registry_customizer=registry_customizer,
    )

    run_dir = result.log_path.parent

    transcript = _build_transcript(result.log_path, run_dir, workflow)
    # transcript.jsonl is written incrementally by the executor's
    # on_state_exit hook (see _IncrementalTranscriptWriter in
    # run_workflow). The file is already on disk at this point;
    # ``run_role`` just publishes its path.
    transcript_path = run_dir / "transcript.jsonl"

    termination, error = _derive_termination(result.log_path)
    rounds_completed = _count_judge_rounds(transcript, workflow)
    final_artifact = _select_final_artifact(workflow, result.artifacts, transcript)
    if termination == "ERROR" and not final_artifact:
        final_artifact = ""

    return IterativeDesignResult(
        termination=termination,
        rounds_completed=rounds_completed,
        final_artifact=final_artifact,
        transcript=transcript,
        transcript_path=transcript_path,
        run_id=result.run_id,
        error=error,
    )
