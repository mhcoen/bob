"""Workflow-specific validators (role bindings + input shape checks)."""

from __future__ import annotations

from typing import Any

from orchestra.api._common import WorkflowApiError
from orchestra.api.bindings import (
    _actor_identity,
    _adapter_workspace_mutation,
    _resolve_workflow_role_bindings,
)
from orchestra.api.registry import _ADAPTER_TO_KIND
from orchestra.config import ConfigError, OrchestraConfig, RoleBinding
from orchestra.spine import Workflow


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
            f"workflow {workflow_name!r}: role-adapter kind mismatch:\n  " + "\n  ".join(mismatches)
        )
    _apply_workflow_specific_rules(workflow, resolved, workflow_name)
    return resolved


def _apply_workflow_specific_rules(
    workflow: Workflow,
    role_bindings: dict[str, RoleBinding],
    workflow_name: str,
) -> None:
    rule = _WORKFLOW_RULES.get(workflow.name)
    if rule is None:
        return
    rule(workflow, role_bindings, workflow_name)


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
            f"workflow {workflow_name!r}: missing required role bindings: {missing!r}"
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
    """Enforce the council_four binding constraints:

    - All six required roles present (framer + four proposers +
      synthesizer).
    - The four proposers must resolve to pairwise distinct
      (adapter, model) tuples. Otherwise the council is not actually
      drawing on N distinct model biases; the parallel fan-out is
      the value here.
    - Framer and synthesizer identities are unconstrained. The
      synthesizer in particular MAY share a model string with one of
      the proposers. See ``design/council-actor-bindings.md`` for the
      reasoning: the original same-model-judging concern was about
      single-prompt self-evaluation, which does not match the
      synthesis-across-four shape. Distinct ROLE BINDINGS remain
      structural (each role is its own dict key with its own
      template); distinct MODEL STRINGS are not required across
      roles, only across the four proposers.
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
            f"workflow {workflow_name!r}: missing required role bindings: {missing!r}"
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


_WORKFLOW_RULES: dict[
    str,
    Callable[[Workflow, dict[str, RoleBinding], str], None],
] = {
    "propose_review_judge_implement": _validate_prji,
    # The canonical / reauthor split landed alongside Slice D's
    # smoke. The same role-binding rule applies to all three names;
    # the old council_four entry is kept as the deprecated alias.
    # See orchestra/design/synthesizer-output-contract.md.
    "council_four": _validate_council_four,
    "council_four_canonical": _validate_council_four,
    "council_four_reauthor": _validate_council_four,
}


def _validate_inputs(workflow: Workflow, inputs: dict[str, Any]) -> None:
    declared = {ext.name for ext in workflow.external_inputs}
    extras = set(inputs) - declared
    if extras:
        raise WorkflowApiError(f"unknown inputs: {sorted(extras)}. Declared: {sorted(declared)}")
    missing = declared - set(inputs)
    if missing:
        raise WorkflowApiError(f"missing required inputs: {sorted(missing)}")
