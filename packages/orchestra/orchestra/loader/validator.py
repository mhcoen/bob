"""Validation phases 3-7 from the runner spec.

Phase 1 (parse) is the parser itself. Phase 2 (profile load) is empty
in slice 1 because the slice has no profiles. Phases 3-7 run here.
"""

from __future__ import annotations

from pathlib import Path

from orchestra.errors import ValidationError
from orchestra.registry import ProfileRegistry
from orchestra.spine import (
    Comparison,
    GuardExpr,
    Reference,
    StateDecl,
    TruthyTest,
    Workflow,
)


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
    # Artifact types must be known to the registry.
    for art in workflow.artifacts:
        if art.type not in registry.artifact_types:
            raise ValidationError(
                f"artifact {art.name!r}: unknown type {art.type!r}"
            )
    # Source files referenced by 'prompt file' / 'prompt template' must
    # exist on disk relative to the workflow's source dir.
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
    ]
    for category, names in categories:
        for n in names:
            if n in seen:
                raise ValidationError(
                    f"name {n!r} is declared as both {seen[n]} and {category}"
                )
            seen[n] = category
    # Reserved targets cannot collide.
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

    for state in workflow.states:
        # Actor backings must be registered.
        if state.actor.kind not in registry.actor_backings:
            raise ValidationError(
                f"state {state.name!r}: unknown actor backing {state.actor.kind!r}"
            )
        # Model and agent references resolve to declared models/agents.
        if state.actor.kind == "model":
            if state.actor.ref not in model_names:
                raise ValidationError(
                    f"state {state.name!r}: undeclared model {state.actor.ref!r}"
                )
        if state.actor.kind == "agent":
            # Slice 1 does not exercise agents but the check is here
            # for forward compatibility.
            agent_names = {getattr(a, "name", "") for a in workflow.agents}
            if state.actor.ref not in agent_names:
                raise ValidationError(
                    f"state {state.name!r}: undeclared agent {state.actor.ref!r}"
                )
        # Role references resolve.
        if state.role is not None and state.role not in role_names:
            raise ValidationError(
                f"state {state.name!r}: undeclared role {state.role!r}"
            )
        # Reads resolve to declared artifacts or external inputs.
        for r in state.reads:
            if r not in artifact_names and r not in external_names:
                raise ValidationError(
                    f"state {state.name!r}: read {r!r} is not a declared artifact or external input"
                )
        # Writes: artifact must exist and types must match.
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
        # Transitions: targets exist; outcomes appropriate to backing.
        seen_outcomes: set[str] = set()
        for t in state.transitions:
            if t.target not in state_names and t.target not in {"done", "stop"}:
                raise ValidationError(
                    f"state {state.name!r}: transition target {t.target!r} is not a declared state"
                )
            seen_outcomes.add(t.outcome)
        # For LLM states, error/timeout transitions must exist.
        if state.actor.kind in ("model", "agent"):
            for required in ("error", "timeout"):
                if required not in seen_outcomes:
                    raise ValidationError(
                        f"state {state.name!r}: missing 'on {required}' transition"
                    )
        # For human gates, options must be non-empty and every option
        # must have an 'on <option>' transition; timeout/cancelled also
        # need transitions.
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
            for required in ("timeout", "cancelled"):
                if required not in seen_outcomes:
                    raise ValidationError(
                        f"state {state.name!r}: missing 'on {required}' transition"
                    )
        # Writes must be coverable by a registered parser.
        if state.writes:
            applicable = registry.parsers_for(
                backing=state.actor.kind,
                artifact_types=tuple(w.type for w in state.writes),
            )
            if not applicable:
                raise ValidationError(
                    f"state {state.name!r}: declared writes have no registered result parser"
                )

        # Guards: every reference's head resolves to something known.
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
    # Walk the AST; check every Reference's head.
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
        # NotExpr, AndExpr, OrExpr have child expressions
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
    declared with `initial` or `source`, or written by some state.
    """
    artifact_decls = {a.name: a for a in workflow.artifacts}
    written_by_some_state = set()
    for s in workflow.states:
        for w in s.writes:
            written_by_some_state.add(w.name)
    for s in workflow.states:
        for r in s.reads:
            if r not in artifact_decls:
                # External input; phase 5 handled it.
                continue
            decl = artifact_decls[r]
            has_initial = decl.initial is not None
            has_source = decl.source_kind is not None
            if (
                not has_initial
                and not has_source
                and r not in written_by_some_state
            ):
                # Per the runner spec, this is a warning, not an error.
                # Slice 1 does not have a warning channel; the validator
                # raises on this so tests can assert on it. A future
                # diagnostic layer can downgrade to a warning.
                raise ValidationError(
                    f"state {s.name!r}: reads artifact {r!r} that is never initialized or written"
                )


# --------------------------------------------------------------------
# Phase 7: cycle bounds (lint only)
# --------------------------------------------------------------------


def _phase7_cycle_bounds(workflow: Workflow) -> None:
    # Slice 1 does not need a full cycle detector (the echo workflow
    # has no cycles). The mechanism is wired up: we walk the graph and
    # confirm the absence of cycles on the echo workflow. A future
    # slice will replace this with the real lint check.
    graph: dict[str, set[str]] = {s.name: set() for s in workflow.states}
    for s in workflow.states:
        for t in s.transitions:
            if t.target in graph:
                graph[s.name].add(t.target)
    # Tarjan-lite: detect any cycle.
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        stack.add(node)
        for nxt in graph[node]:
            if nxt in stack:
                # Cycle detected. Slice 1 issues no warnings (no
                # warning channel yet) and treats it as acceptable for
                # forward compatibility. A future slice will implement
                # the lint check from validation rule 11.
                continue
            if nxt not in visited:
                dfs(nxt)
        stack.discard(node)

    for s in workflow.states:
        if s.name not in visited:
            dfs(s.name)
