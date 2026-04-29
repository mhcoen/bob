"""Validation phases 3-7 from the runner spec.

Phase 1 (parse) is the parser itself. Phase 2 (profile load) is empty
in slice 1 because the slice has no profiles. Phases 3-7 run here.
"""

from __future__ import annotations

from pathlib import Path

from orchestra.errors import ValidationError
from orchestra.registry import ProfileRegistry
from orchestra.spine import (
    NO_INITIAL,
    Comparison,
    GuardExpr,
    Reference,
    StateDecl,
    TruthyTest,
    Workflow,
)

# Backing-scoped keywords admitted by the parser. Each maps to the set
# of actor backings on which the keyword is legal. v0 has no profile
# loader, so this table is the source of truth; slice 2 will replace
# this with profile-driven registration.
_BACKING_SCOPED_KEYWORDS: dict[str, frozenset[str]] = {
    "command": frozenset({"shell"}),
    "runs": frozenset({"shell"}),
    "continue_on_fail": frozenset({"shell"}),
    "require_diff": frozenset({"shell"}),
    "mode": frozenset({"shell"}),
}


def validate(workflow: Workflow, registry: ProfileRegistry) -> None:
    """Run all validation phases. Raises ValidationError on failure."""

    _phase3_declaration_resolution(workflow, registry)
    _phase4_name_uniqueness(workflow)
    _phase5_state_validation(workflow, registry)
    _phase6_dataflow(workflow)
    _phase7_cycle_bounds(workflow)


# --------------------------------------------------------------------
# Phase 3: declaration resolution
# --------------------------------------------------------------------


def _phase3_declaration_resolution(
    workflow: Workflow, registry: ProfileRegistry
) -> None:
    if not workflow.states:
        raise ValidationError("workflow has no states")
    if workflow.max_total_steps <= 0:
        raise ValidationError(
            "workflow must declare max_total_steps (or max_state_visits)"
        )
    for art in workflow.artifacts:
        if art.type not in registry.artifact_types:
            raise ValidationError(
                f"artifact {art.name!r}: unknown type {art.type!r}"
            )
    source_dir = Path(workflow.source_dir)
    for state in workflow.states:
        if state.prompt is None:
            continue
        if state.prompt.kind in ("file", "template"):
            assert state.prompt.path is not None
            full = source_dir / state.prompt.path
            if not full.exists():
                raise ValidationError(
                    f"state {state.name!r}: prompt file not found: {full}"
                )
    for role in workflow.roles:
        if role.default_prompt.kind in ("file", "template"):
            assert role.default_prompt.path is not None
            full = source_dir / role.default_prompt.path
            if not full.exists():
                raise ValidationError(
                    f"role {role.name!r}: prompt file not found: {full}"
                )
    # Agents reference declared models.
    model_names = {m.name for m in workflow.models}
    for agent in workflow.agents:
        if agent.model not in model_names:
            raise ValidationError(
                f"agent {agent.name!r}: undeclared model {agent.model!r}"
            )
    # Group members resolve to declared roles or agents per group kind.
    role_names = {r.name for r in workflow.roles}
    agent_names = {a.name for a in workflow.agents}
    for group in workflow.groups:
        for member in group.members:
            if group.kind == "roles" and member not in role_names:
                raise ValidationError(
                    f"group {group.name!r}: undeclared role member {member!r}"
                )
            if group.kind == "agents" and member not in agent_names:
                raise ValidationError(
                    f"group {group.name!r}: undeclared agent member {member!r}"
                )


# --------------------------------------------------------------------
# Phase 4: name uniqueness
# --------------------------------------------------------------------


def _phase4_name_uniqueness(workflow: Workflow) -> None:
    seen: dict[str, str] = {}
    categories = [
        ("state", [s.name for s in workflow.states]),
        ("artifact", [a.name for a in workflow.artifacts]),
        ("external_input", [e.name for e in workflow.external_inputs]),
        ("model", [m.name for m in workflow.models]),
        ("role", [r.name for r in workflow.roles]),
        ("agent", [a.name for a in workflow.agents]),
        ("group", [g.name for g in workflow.groups]),
    ]
    for category, names in categories:
        for n in names:
            if n in seen:
                raise ValidationError(
                    f"name {n!r} is declared as both {seen[n]} and {category}"
                )
            seen[n] = category
    for reserved in ("done", "stop", "attempts", "retries"):
        if reserved in seen:
            raise ValidationError(
                f"name {reserved!r} is reserved and cannot be used as a {seen[reserved]}"
            )


# --------------------------------------------------------------------
# Phase 5: state validation
# --------------------------------------------------------------------


def _phase5_state_validation(
    workflow: Workflow, registry: ProfileRegistry
) -> None:
    state_names = {s.name for s in workflow.states}
    artifact_names = {a.name for a in workflow.artifacts}
    artifact_types = {a.name: a.type for a in workflow.artifacts}
    external_names = {e.name for e in workflow.external_inputs}
    model_names = {m.name for m in workflow.models}
    role_names = {r.name for r in workflow.roles}
    agent_names = {a.name for a in workflow.agents}

    for state in workflow.states:
        if state.actor.kind not in registry.actor_backings:
            raise ValidationError(
                f"state {state.name!r}: unknown actor backing {state.actor.kind!r}"
            )
        if state.actor.kind == "model":
            if state.actor.ref not in model_names:
                raise ValidationError(
                    f"state {state.name!r}: undeclared model {state.actor.ref!r}"
                )
        if state.actor.kind == "agent":
            if state.actor.ref not in agent_names:
                raise ValidationError(
                    f"state {state.name!r}: undeclared agent {state.actor.ref!r}"
                )
        if state.role is not None and state.role not in role_names:
            raise ValidationError(
                f"state {state.name!r}: undeclared role {state.role!r}"
            )
        for r in state.reads:
            if r not in artifact_names and r not in external_names:
                raise ValidationError(
                    f"state {state.name!r}: read {r!r} is not a declared artifact or external input"
                )
        for w in state.writes:
            if w.name not in artifact_names:
                raise ValidationError(
                    f"state {state.name!r}: writes undeclared artifact {w.name!r}"
                )
            declared_type = artifact_types[w.name]
            if w.type != declared_type:
                raise ValidationError(
                    f"state {state.name!r}: writes {w.name!r} as {w.type!r} but artifact is {declared_type!r}"
                )

        # Backing-scoped keywords: each backing-scoped clause present in
        # the state body must be legal for this state's actor backing.
        for clause_name in state.backing_options.keys():
            if clause_name == "options":
                # 'options' is the human-gate clause; carried through
                # backing_options for adapter convenience but it's a
                # core clause, not backing-scoped. Skip it.
                continue
            allowed = _BACKING_SCOPED_KEYWORDS.get(clause_name)
            if allowed is None:
                # Not a known backing-scoped clause; the parser would
                # have rejected an unknown name, so this is fine.
                continue
            if state.actor.kind not in allowed:
                raise ValidationError(
                    f"state {state.name!r}: clause {clause_name!r} is not legal "
                    f"on actor backing {state.actor.kind!r} (legal on: {sorted(allowed)})"
                )

        seen_outcomes: set[str] = set()
        for t in state.transitions:
            if t.target not in state_names and t.target not in {"done", "stop"}:
                raise ValidationError(
                    f"state {state.name!r}: transition target {t.target!r} is not a declared state"
                )
            seen_outcomes.add(t.outcome)

        # Per design rule 9: model and shell backings need both
        # 'on error' and 'on timeout'; human (choice gate) backings
        # only need 'on timeout'. The executor's error path is not
        # invoked for human gates in the same way it is for actor
        # invocations that can fail mid-run.
        required_outcomes: tuple[str, ...]
        if state.actor.kind == "human":
            required_outcomes = ("timeout",)
        else:
            required_outcomes = ("error", "timeout")
        for required in required_outcomes:
            if required not in seen_outcomes:
                raise ValidationError(
                    f"state {state.name!r}: missing 'on {required}' transition"
                )

        if state.actor.kind == "human":
            if not state.options:
                raise ValidationError(
                    f"state {state.name!r}: human state must declare options"
                )
            for opt in state.options:
                if opt not in seen_outcomes:
                    raise ValidationError(
                        f"state {state.name!r}: missing 'on {opt}' transition for option"
                    )
            if "cancelled" not in seen_outcomes:
                raise ValidationError(
                    f"state {state.name!r}: missing 'on cancelled' transition"
                )

        # Parser coverage: every declared write's type must be served
        # by at least one applicable parser. The "any matches any"
        # rule is wrong; we check each write individually.
        if state.writes:
            for w in state.writes:
                applicable = registry.parsers_for(
                    backing=state.actor.kind,
                    artifact_types=(w.type,),
                )
                if not applicable:
                    raise ValidationError(
                        f"state {state.name!r}: write {w.name!r} of type {w.type!r} "
                        f"has no registered result parser for backing {state.actor.kind!r}"
                    )

        for t in state.transitions:
            if t.guard is not None:
                _validate_guard_refs(
                    state, t.guard, state_names, artifact_names, external_names
                )


def _validate_guard_refs(
    state: StateDecl,
    expr: GuardExpr,
    state_names: set[str],
    artifact_names: set[str],
    external_names: set[str],
) -> None:
    refs = list(_collect_refs(expr))
    for ref in refs:
        head = ref.head()
        if head in ("attempts", "retries"):
            if len(ref.parts) != 2:
                raise ValidationError(
                    f"state {state.name!r}: counter reference {ref!s} must be of form attempts.<state>"
                )
            if ref.parts[1] not in state_names:
                raise ValidationError(
                    f"state {state.name!r}: counter reference {ref!s} names unknown state"
                )
            continue
        if head in state_names:
            continue
        if head in artifact_names:
            continue
        if head in external_names:
            continue
        raise ValidationError(
            f"state {state.name!r}: guard reference {ref!s} does not resolve"
        )


def _collect_refs(expr: GuardExpr) -> list[Reference]:
    out: list[Reference] = []
    if isinstance(expr, Reference):
        out.append(expr)
    elif isinstance(expr, TruthyTest):
        out.append(expr.ref)
    elif isinstance(expr, Comparison):
        out.append(expr.left)
        if isinstance(expr.right, Reference):
            out.append(expr.right)
    else:
        for child in getattr(expr, "parts", ()):
            out.extend(_collect_refs(child))
        if hasattr(expr, "inner"):
            out.extend(_collect_refs(expr.inner))
    return out


# --------------------------------------------------------------------
# Phase 6: dataflow
# --------------------------------------------------------------------


def _phase6_dataflow(workflow: Workflow) -> None:
    """Every artifact a state reads must be writable by some path:
    declared with `initial` (any value, including null) or `source`,
    or written by some state.
    """
    artifact_decls = {a.name: a for a in workflow.artifacts}
    written_by_some_state: set[str] = set()
    for s in workflow.states:
        for w in s.writes:
            written_by_some_state.add(w.name)
    for s in workflow.states:
        for r in s.reads:
            if r not in artifact_decls:
                continue
            decl = artifact_decls[r]
            has_initial = decl.initial is not NO_INITIAL
            has_source = decl.source_kind is not None
            if (
                not has_initial
                and not has_source
                and r not in written_by_some_state
            ):
                raise ValidationError(
                    f"state {s.name!r}: reads artifact {r!r} that is never initialized or written"
                )


# --------------------------------------------------------------------
# Phase 7: cycle bounds (lint only)
# --------------------------------------------------------------------


def _phase7_cycle_bounds(workflow: Workflow) -> None:
    graph: dict[str, set[str]] = {s.name: set() for s in workflow.states}
    for s in workflow.states:
        for t in s.transitions:
            if t.target in graph:
                graph[s.name].add(t.target)
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        stack.add(node)
        for nxt in graph[node]:
            if nxt in stack:
                continue
            if nxt not in visited:
                dfs(nxt)
        stack.discard(node)

    for s in workflow.states:
        if s.name not in visited:
            dfs(s.name)
