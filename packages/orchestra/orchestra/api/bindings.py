"""Role binding resolution and progress callback wiring."""

from __future__ import annotations

from collections.abc import Callable

from orchestra.adapters._subprocess import get_current_activity
from orchestra.adapters.base import WORKSPACE_MUTATION_VALUES
from orchestra.api.registry import _ADAPTER_CLASSES
from orchestra.config import ConfigError, OrchestraConfig, RoleBinding
from orchestra.progress import (
    ChildBinding,
    ProgressCallback,
    ProgressEvent,
    silent_reporter,
    stderr_reporter,
)
from orchestra.spine import Workflow


def _wrap_progress_callback(
    user_callback: ProgressCallback | None,
    role_bindings: dict[str, RoleBinding],
) -> (
    Callable[
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
    ]
    | None
):
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

    The CLI installs its own callback up front (``stderr_reporter``)
    so the ``user_callback is not None`` branch fires for every CLI
    dispatch. Library callers that want suppression should pass
    ``quiet=True``; the silent_reporter alternative still installs
    an event handler that does nothing, which is functionally
    identical from the reporter's perspective.
    """
    if quiet:
        return silent_reporter()
    if user_callback is not None:
        return user_callback
    return stderr_reporter(activity_getter=get_current_activity)


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
    for state in workflow.states:
        if state.role is None:
            continue
        if state.actor.kind not in ("model", "agent"):
            continue
        needed.setdefault(state.role, state.actor.kind)

    resolution_errors: list[str] = []
    resolved: dict[str, RoleBinding] = {}
    for role_name in needed:
        try:
            resolved[role_name] = _resolve_role_binding(workflow_name, role_name, config)
        except ConfigError as exc:
            resolution_errors.append(str(exc))
    if resolution_errors:
        raise ConfigError(
            f"workflow {workflow_name!r}: role-binding resolution failed:\n  "
            + "\n  ".join(resolution_errors)
        )
    return resolved


# --------------------------------------------------------------------
# Workflow-specific config validation rules
#
# Some shipped workflows enforce binding-level invariants the grammar
# cannot express. The Propose-Review-Judge-Implement workflow requires
# proposer, reviewer, and implementer to resolve to pairwise distinct
# actors plus a workspace-mutation rule (only the implementer may be
# bound to a "mutating" adapter). The check runs after role-binding
# resolution and adapter-kind matching but before the executor starts.
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
    if binding.adapter is None:
        raise ConfigError(
            "actor identity requires a resolved adapter, but the role "
            "binding has none. A compound-role leaf must have its "
            "adapter resolved from the registry before identity is taken."
        )
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
    cls = _ADAPTER_CLASSES.get(binding.adapter) if binding.adapter is not None else None
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
