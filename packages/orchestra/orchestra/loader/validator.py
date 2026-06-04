"""Validation phases 3-7 from the runner spec.

Phase 1 (parse) is the parser itself. Phase 2 (profile load) is empty
in slice 1 because the slice has no profiles. Phases 3-7 run here.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from orchestra.errors import ValidationError
from orchestra.registry import ProfileRegistry
from orchestra.schema import (
    EXTRACTABLE_FIELD_TYPES,
    SchemaError,
    SchemaSpec,
    load_schema,
)
from orchestra.spine import (
    NO_INITIAL,
    ArtifactDecl,
    Comparison,
    GuardExpr,
    Reference,
    StateDecl,
    Transition,
    TruthyTest,
    Workflow,
)
from orchestra.transforms import schema_artifact_type, type_label

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

    schema_specs = _load_schemas(workflow)
    _phase3_declaration_resolution(workflow, registry)
    _phase4_name_uniqueness(workflow)
    _phase5_state_validation(workflow, registry, schema_specs)
    _phase6_dataflow(workflow)
    _phase7_cycle_bounds(workflow)


def _load_schemas(workflow: Workflow) -> dict[str, SchemaSpec]:
    """Load every schema-backed json artifact's schema file and validate
    its v0 shape. Returns a mapping from artifact name to SchemaSpec.

    Schema-backed artifacts whose ``type`` is not ``json`` fail here
    rather than later: the runtime cannot meaningfully populate a
    text artifact from a parsed JSON object, and the schema-verdict
    contract is specifically about json-shaped output.
    """
    out: dict[str, SchemaSpec] = {}
    source_dir = Path(workflow.source_dir) if workflow.source_dir else Path.cwd()
    for art in workflow.artifacts:
        if art.schema_path is None:
            continue
        if art.type != "json":
            raise ValidationError(
                f"artifact {art.name!r}: 'schema' qualifier requires "
                f"artifact type 'json', got {art.type!r}"
            )
        full = Path(art.schema_path)
        if not full.is_absolute():
            full = source_dir / art.schema_path
        try:
            spec = load_schema(full)
        except SchemaError as exc:
            raise ValidationError(f"artifact {art.name!r}: {exc}") from exc
        out[art.name] = spec
    return out


# --------------------------------------------------------------------
# Phase 3: declaration resolution
# --------------------------------------------------------------------


def _phase3_declaration_resolution(workflow: Workflow, registry: ProfileRegistry) -> None:
    if not workflow.states:
        raise ValidationError("workflow has no states")
    if workflow.max_total_steps <= 0:
        raise ValidationError("workflow must declare max_total_steps (or max_state_visits)")
    for art in workflow.artifacts:
        if art.type not in registry.artifact_types:
            raise ValidationError(f"artifact {art.name!r}: unknown type {art.type!r}")
        if art.source_kind is not None:
            # ``source file`` and ``source path`` qualifiers parse and
            # are exposed on ArtifactDecl, but the store's declare()
            # only materializes ``initial``; nothing reads the source
            # at run start, so a state reading a source-backed
            # artifact gets None silently. Reject the qualifier here
            # until the store-side initialization lands so workflows
            # cannot be loaded under the impression that the source
            # data will be present.
            raise ValidationError(
                f"artifact {art.name!r}: 'source {art.source_kind}' "
                "qualifier is not implemented; the store materializes "
                "only 'initial' values today, so any state reading "
                "this artifact would receive None at run time. Use "
                "'initial' or remove the source clause until source "
                "loading lands."
            )
    source_dir = Path(workflow.source_dir)
    for state in workflow.states:
        if state.prompt is None:
            continue
        if state.prompt.kind in ("file", "template"):
            assert state.prompt.path is not None
            full = source_dir / state.prompt.path
            if not full.exists():
                raise ValidationError(f"state {state.name!r}: prompt file not found: {full}")
    for role in workflow.roles:
        if role.default_prompt.kind in ("file", "template"):
            assert role.default_prompt.path is not None
            full = source_dir / role.default_prompt.path
            if not full.exists():
                raise ValidationError(f"role {role.name!r}: prompt file not found: {full}")
    # Agents reference declared models.
    model_names = {m.name for m in workflow.models}
    for agent in workflow.agents:
        if agent.model not in model_names:
            raise ValidationError(f"agent {agent.name!r}: undeclared model {agent.model!r}")
    # Group members resolve to declared roles or agents per group kind.
    role_names = {r.name for r in workflow.roles}
    agent_names = {a.name for a in workflow.agents}
    for group in workflow.groups:
        for member in group.members:
            if group.kind == "roles" and member not in role_names:
                raise ValidationError(f"group {group.name!r}: undeclared role member {member!r}")
            if group.kind == "agents" and member not in agent_names:
                raise ValidationError(f"group {group.name!r}: undeclared agent member {member!r}")


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
                raise ValidationError(f"name {n!r} is declared as both {seen[n]} and {category}")
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
    workflow: Workflow,
    registry: ProfileRegistry,
    schema_specs: dict[str, SchemaSpec],
) -> None:
    state_names = {s.name for s in workflow.states}
    artifact_names = {a.name for a in workflow.artifacts}
    artifact_types = {a.name: a.type for a in workflow.artifacts}
    artifact_decls = {a.name: a for a in workflow.artifacts}
    external_names = {e.name for e in workflow.external_inputs}
    model_names = {m.name for m in workflow.models}
    role_names = {r.name for r in workflow.roles}
    agent_names = {a.name for a in workflow.agents}

    _validate_extractions(workflow, schema_specs, artifact_decls)

    # Names of artifacts whose writes are populated by the schema layer
    # (the schema-backed json artifact itself plus every declared
    # extraction target). The parser-coverage rule below skips these
    # because the executor's schema layer commits them directly.
    schema_handled_artifacts: set[str] = set()
    for art in workflow.artifacts:
        if art.schema_path is None:
            continue
        schema_handled_artifacts.add(art.name)
        for ext in art.extractions:
            schema_handled_artifacts.add(ext.target)

    for state in workflow.states:
        if state.actor.kind != "transform" and state.actor.kind not in registry.actor_backings:
            raise ValidationError(
                f"state {state.name!r}: unknown actor backing {state.actor.kind!r}"
            )
        if state.actor.kind == "model":
            if state.actor.ref not in model_names:
                raise ValidationError(f"state {state.name!r}: undeclared model {state.actor.ref!r}")
        if state.actor.kind == "agent":
            if state.actor.ref not in agent_names:
                raise ValidationError(f"state {state.name!r}: undeclared agent {state.actor.ref!r}")
        if state.actor.kind == "transform":
            _validate_transform_state(state, workflow, registry, artifact_types)
        if state.role is not None and state.role not in role_names:
            raise ValidationError(f"state {state.name!r}: undeclared role {state.role!r}")
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
            if t.is_fan_out():
                # Validate fan-out children are declared states.
                for child in t.fan_out:
                    if child not in state_names:
                        raise ValidationError(
                            f"state {state.name!r}: fan_out child {child!r} is not a declared state"
                        )
                if t.error_target is None:
                    raise ValidationError(
                        f"state {state.name!r}: fan_out transition is missing "
                        "the 'on error <target>' clause"
                    )
                if t.error_target not in state_names and t.error_target not in {"done", "stop"}:
                    raise ValidationError(
                        f"state {state.name!r}: fan_out error target "
                        f"{t.error_target!r} is not a declared state"
                    )
                # Sibling write collision: per the real-council plan,
                # two children of the same fan-out group writing the
                # same artifact name is a load error (caught here so
                # the runtime never has to mediate conflicting
                # writes).
                child_decls = [s for s in workflow.states if s.name in t.fan_out]
                seen_writes: dict[str, str] = {}
                for child_decl in child_decls:
                    for w in child_decl.writes:
                        prior = seen_writes.get(w.name)
                        if prior is not None:
                            raise ValidationError(
                                f"state {state.name!r}: fan_out children "
                                f"{prior!r} and {child_decl.name!r} both write "
                                f"artifact {w.name!r}; sibling writes to "
                                "the same artifact are not permitted"
                            )
                        seen_writes[w.name] = child_decl.name
                # Pass-3 fix #1 + pass-4 fix #1: a fan-out child's
                # transition graph is not dispatched by the executor.
                # The child worker runs the child's body plus local
                # retry handling and returns; the controller then
                # routes the PARENT to the join target on success or
                # to the error_target on failure.
                #
                # Allowed child shapes:
                #   - plain transition to ``done`` or ``stop`` (the
                #     terminal targets the controller treats as
                #     "child is done"),
                #   - bounded retry (``retry_max`` set) whose
                #     post-exhaustion target is also terminal: the
                #     executor handles the retry loop, then routes to
                #     the post-exhaustion target.
                #
                # Rejected child shapes:
                #   - nested ``fan_out`` (the inner group is silently
                #     skipped at run time),
                #   - any non-retry transition whose target is not
                #     terminal, INCLUDING a plain self-target (the
                #     pass-3 attempt admitted this on the assumption
                #     that ``target == name`` meant local retry; it
                #     does not — local retry is encoded by retry_max,
                #     not by the target name; a plain self-target is
                #     entered exactly once and then the parent
                #     fan-out joins, so the loop is silently lost),
                #   - retry transitions whose post-exhaustion target
                #     is not terminal (the exhaustion path re-enters
                #     the graph and is silently skipped the same way
                #     a plain non-terminal target would be).
                _TERMINAL_CHILD_TARGETS = {"done", "stop"}
                for child_decl in child_decls:
                    for child_t in child_decl.transitions:
                        if child_t.is_fan_out():
                            raise ValidationError(
                                f"state {child_decl.name!r}: fan_out "
                                "child cannot itself declare a "
                                "'fan_out' transition. Nested fan-out "
                                "violates the rule that a state cannot "
                                "appear inside its own execution "
                                "scope. The executor would silently "
                                "skip the inner group; restructure the "
                                "workflow so each fan-out runs at the "
                                "top level."
                            )
                        if child_t.target in _TERMINAL_CHILD_TARGETS:
                            # Both plain ``=> done|stop`` and
                            # ``retry max N then done|stop`` are fine
                            # because the post-retry target is
                            # terminal and the retry loop is the
                            # executor's responsibility.
                            continue
                        if child_t.retry_max is not None:
                            raise ValidationError(
                                f"state {child_decl.name!r}: fan_out "
                                f"child retry transition 'on "
                                f"{child_t.outcome} retry max "
                                f"{child_t.retry_max} then "
                                f"{child_t.target}' lands on a "
                                "non-terminal post-retry target. "
                                "Once the retry budget is exhausted "
                                "the child must route to 'done' or "
                                "'stop' so the parent fan-out can "
                                "close the group; routing back into "
                                "the workflow graph is silently "
                                "skipped at run time."
                            )
                        raise ValidationError(
                            f"state {child_decl.name!r}: fan_out "
                            f"child transition 'on {child_t.outcome} "
                            f"=> {child_t.target}' targets a "
                            "non-terminal state. The executor only "
                            "dispatches the child's body and local "
                            "retry (retry_max); routing through the "
                            "child's transition graph is silently "
                            "skipped. Use 'done' or 'stop' as the "
                            "target, or fold the downstream state "
                            "into the fan-out parent's join graph. "
                            "Note: a plain self-target like "
                            f"'=> {child_decl.name}' is NOT local "
                            "retry; local retry is expressed via "
                            "'retry max N then <terminal>'."
                        )

        # Schema-backed state checks: a state writing a schema-backed
        # json artifact has its transition outcomes derived from the
        # schema's ``decision`` enum. ``on complete`` is meaningless
        # because the runtime never emits ``complete`` for such
        # states; every enum value must have a transition; every
        # non-failure transition outcome must be in the enum.
        schema_backed_writes = [w for w in state.writes if w.name in schema_specs]
        if schema_backed_writes:
            if len(schema_backed_writes) > 1:
                raise ValidationError(
                    f"state {state.name!r}: writes more than one "
                    "schema-backed json artifact "
                    f"({sorted(w.name for w in schema_backed_writes)}); "
                    "v0 admits at most one per state"
                )
            spec = schema_specs[schema_backed_writes[0].name]
            if "complete" in seen_outcomes:
                raise ValidationError(
                    f"state {state.name!r}: 'on complete' is not "
                    "permitted on a schema-backed state; declare a "
                    "transition for each value of the schema's "
                    f"decision enum {list(spec.decision_enum)!r} instead"
                )
            failure_outcomes = {"error", "timeout", "cancelled"}
            non_failure = seen_outcomes - failure_outcomes
            for required_enum in spec.decision_enum:
                if required_enum not in seen_outcomes:
                    raise ValidationError(
                        f"state {state.name!r}: missing 'on "
                        f"{required_enum}' transition (required by "
                        "the schema's decision enum)"
                    )
            extra = non_failure - set(spec.decision_enum)
            if extra:
                raise ValidationError(
                    f"state {state.name!r}: transition outcomes "
                    f"{sorted(extra)!r} are not in the schema's "
                    f"decision enum {list(spec.decision_enum)!r}"
                )
            # The state must also list each extraction target in writes
            # so the data flow is visible at the state site.
            schema_artifact_name = schema_backed_writes[0].name
            schema_artifact = artifact_decls[schema_artifact_name]
            declared_writes = {w.name for w in state.writes}
            for ext in schema_artifact.extractions:
                if ext.target not in declared_writes:
                    raise ValidationError(
                        f"state {state.name!r}: writes schema-backed "
                        f"artifact {schema_artifact_name!r} but does "
                        "not list extraction target "
                        f"{ext.target!r} in 'writes'. Schema-backed "
                        "states must declare each extraction target "
                        "in 'writes' so the data flow is visible at "
                        "the state site."
                    )

        # Per design rule 9 plus the success-outcome rule: every
        # outcome the executor could derive must have a matching
        # transition, otherwise the state would invoke its actor,
        # write payloads and artifacts, emit state_exit, and only then
        # crash with "no transition matched outcome" with side effects
        # already on disk.
        #
        # Failure outcomes (executor's _derive_outcome can return
        # these for any actor backing other than the special-case
        # branches): error and timeout. Human gates cannot fail with
        # 'error' because the choice gate can only return a chosen
        # option, 'cancelled', or 'timeout'; transforms cannot 'time
        # out' because they are synchronous pure functions.
        required_outcomes: tuple[str, ...]
        if state.actor.kind == "human":
            required_outcomes = ("timeout",)
        elif state.actor.kind == "transform":
            required_outcomes = ("complete", "error")
        elif state.actor.kind == "shell":
            required_outcomes = ("error", "timeout", "pass", "fail")
        else:
            required_outcomes = ("error", "timeout")
        for required in required_outcomes:
            if required not in seen_outcomes:
                raise ValidationError(f"state {state.name!r}: missing 'on {required}' transition")

        # Model and agent states must also handle the success path.
        # The executor's _derive_outcome returns the payload's verdict
        # when present, otherwise 'complete'. A state that declares no
        # outcome other than error/timeout/cancelled has no branch for
        # any success path the executor can derive, so a successful
        # invocation would crash on "no transition matched outcome"
        # after the actor had already run. Require at least one
        # transition whose outcome is not a failure outcome; that
        # branch covers either 'complete' (default) or a verdict-based
        # outcome (schema-backed states).
        if state.actor.kind in ("model", "agent"):
            failure_outcomes = {"error", "timeout", "cancelled"}
            non_failure = seen_outcomes - failure_outcomes
            if not non_failure:
                raise ValidationError(
                    f"state {state.name!r}: missing a success transition; "
                    f"{state.actor.kind} states must declare 'on complete' "
                    "or a verdict-based outcome so the executor can route "
                    "after a successful invocation"
                )

        if state.actor.kind == "human":
            if not state.options:
                raise ValidationError(f"state {state.name!r}: human state must declare options")
            for opt in state.options:
                if opt not in seen_outcomes:
                    raise ValidationError(
                        f"state {state.name!r}: missing 'on {opt}' transition for option"
                    )
            if "cancelled" not in seen_outcomes:
                raise ValidationError(f"state {state.name!r}: missing 'on cancelled' transition")

        # Parser coverage: every declared write's type must be served
        # by at least one applicable parser. The "any matches any"
        # rule is wrong; we check each write individually. Transform
        # states bypass the parser dispatch, so the parser-coverage
        # rule does not apply to them. Writes whose target is the
        # schema-backed json artifact itself or one of its declared
        # extraction targets are populated by the executor's schema
        # layer in the same transaction as the json write, so they
        # also bypass the parser-coverage rule.
        if state.writes and state.actor.kind != "transform":
            for w in state.writes:
                if w.name in schema_handled_artifacts:
                    continue
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
                _validate_guard_refs(state, t.guard, state_names, artifact_names, external_names)


def _validate_extractions(
    workflow: Workflow,
    schema_specs: dict[str, SchemaSpec],
    artifact_decls: dict[str, ArtifactDecl],
) -> None:
    """Validate every ``extract`` clause attached to a schema-backed
    json artifact.

    Per the design's "Surface: field extraction" section:
      - Source field must exist in the schema's properties.
      - Source field's type must be in EXTRACTABLE_FIELD_TYPES (string,
        integer, number, boolean). Object/array source fields are a
        load error in v0.
      - Target artifact must be separately declared in the workflow as
        type ``text``.
      - For any extract whose target is read by a downstream state,
        the source field must be in the schema's ``required`` list so
        a successful but field-omitting model output cannot leave a
        stale extracted value visible to that read.
    """
    # Set of artifact names read by any state (for the required-field
    # check on extractions whose targets are downstream reads).
    read_by_some_state: set[str] = set()
    for s in workflow.states:
        for r in s.reads:
            read_by_some_state.add(r)

    for art in workflow.artifacts:
        if art.schema_path is None and art.extractions:
            raise ValidationError(
                f"artifact {art.name!r}: 'extract' qualifier requires "
                "a 'schema' qualifier on the same artifact"
            )
        if art.schema_path is None:
            continue
        spec = schema_specs[art.name]
        for ext in art.extractions:
            if ext.source_field not in spec.field_types:
                raise ValidationError(
                    f"artifact {art.name!r}: extract source field "
                    f"{ext.source_field!r} is not declared in the "
                    f"schema's properties"
                )
            src_type = spec.field_types[ext.source_field]
            if src_type not in EXTRACTABLE_FIELD_TYPES:
                raise ValidationError(
                    f"artifact {art.name!r}: extract source field "
                    f"{ext.source_field!r} has schema type "
                    f"{src_type!r}; v0 admits only "
                    f"{sorted(EXTRACTABLE_FIELD_TYPES)}"
                )
            if ext.target not in artifact_decls:
                raise ValidationError(
                    f"artifact {art.name!r}: extract target "
                    f"{ext.target!r} is not a declared artifact"
                )
            target_decl = artifact_decls[ext.target]
            if ext.type != "text":
                raise ValidationError(
                    f"artifact {art.name!r}: extract target type "
                    f"{ext.type!r} is unsupported in v0; use 'text'"
                )
            if target_decl.type != "text":
                raise ValidationError(
                    f"artifact {art.name!r}: extract target "
                    f"{ext.target!r} is declared as type "
                    f"{target_decl.type!r}, but v0 requires 'text'"
                )
            if ext.target in read_by_some_state and ext.source_field not in spec.required_fields:
                raise ValidationError(
                    f"artifact {art.name!r}: extract source field "
                    f"{ext.source_field!r} is not in the schema's "
                    "'required' list, but the extraction target "
                    f"{ext.target!r} is read by at least one state. "
                    "An optional source field would let the runtime "
                    "leave stale data visible to downstream reads. "
                    "Add the field to 'required' in the schema or "
                    "remove the extract clause."
                )


def _validate_transform_state(
    state: StateDecl,
    workflow: Workflow,
    registry: ProfileRegistry,
    artifact_types: dict[str, str],
) -> None:
    """Slice B: enforce the transform registry contract for a state
    whose actor backing is ``transform``.

    Checks the registered transform exists, the workflow's ``reads``
    keys exactly match ``input_schema`` keys, the workflow's ``writes``
    keys exactly match ``output_schema`` keys, every read's artifact
    type matches the schema's expected artifact type, and every
    write's artifact type matches the schema's expected artifact type.
    Type checking on actual values is deferred to the executor.
    """
    if state.actor.ref is None:
        raise ValidationError(
            f"state {state.name!r}: transform actor must name a registered transform"
        )
    transform = registry.transforms.get(state.actor.ref)
    if transform is None:
        raise ValidationError(
            f"state {state.name!r}: transform {state.actor.ref!r} is not registered"
        )
    if state.role is not None:
        raise ValidationError(f"state {state.name!r}: transform states do not take a role binding")
    if state.prompt is not None:
        raise ValidationError(f"state {state.name!r}: transform states do not take a prompt clause")
    for t in state.transitions:
        if t.retry_max is not None:
            raise ValidationError(
                f"state {state.name!r}: transform states do not support "
                "retry; transforms are pure functions"
            )
    expected_inputs = set(transform.input_schema.keys())
    declared_reads = set(state.reads)
    if declared_reads != expected_inputs:
        missing = expected_inputs - declared_reads
        extra = declared_reads - expected_inputs
        parts: list[str] = []
        if missing:
            parts.append(f"missing reads: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected reads: {sorted(extra)}")
        raise ValidationError(
            f"state {state.name!r}: transform {transform.name!r} reads "
            f"do not match input_schema; {'; '.join(parts)}"
        )
    expected_outputs = set(transform.output_schema.keys())
    declared_writes = {w.name for w in state.writes}
    if declared_writes != expected_outputs:
        missing = expected_outputs - declared_writes
        extra = declared_writes - expected_outputs
        parts = []
        if missing:
            parts.append(f"missing writes: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected writes: {sorted(extra)}")
        raise ValidationError(
            f"state {state.name!r}: transform {transform.name!r} writes "
            f"do not match output_schema; {'; '.join(parts)}"
        )
    # Each read's artifact must have an artifact-type that matches the
    # schema's expected artifact-type. External inputs are not
    # admissible reads for transforms in Slice B because they lack a
    # declared artifact type the schema can be checked against.
    artifact_names = {a.name for a in workflow.artifacts}
    for read_name, read_type in transform.input_schema.items():
        if read_name not in artifact_names:
            raise ValidationError(
                f"state {state.name!r}: transform {transform.name!r} input "
                f"{read_name!r} is not a declared artifact"
            )
        expected_artifact_type = schema_artifact_type(read_type)
        actual = artifact_types[read_name]
        if actual != expected_artifact_type:
            raise ValidationError(
                f"state {state.name!r}: transform {transform.name!r} input "
                f"{read_name!r} has schema type {type_label(read_type)} "
                f"(expects artifact type {expected_artifact_type!r}) but "
                f"the workflow declares artifact {read_name!r} as "
                f"{actual!r}"
            )
    for write_name, write_type in transform.output_schema.items():
        expected_artifact_type = schema_artifact_type(write_type)
        actual = artifact_types[write_name]
        if actual != expected_artifact_type:
            raise ValidationError(
                f"state {state.name!r}: transform {transform.name!r} output "
                f"{write_name!r} has schema type {type_label(write_type)} "
                f"(expects artifact type {expected_artifact_type!r}) but "
                f"the workflow declares artifact {write_name!r} as "
                f"{actual!r}"
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
        raise ValidationError(f"state {state.name!r}: guard reference {ref!s} does not resolve")


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
    """Every artifact a state reads must have an initialized
    declaration or a writer that dominates the read site.

    Two checks:

    1. Existence: an artifact a state reads must either be declared
       with ``initial`` (any value, including null) or written
       somewhere in the workflow. External inputs are admissible
       reads independently of this check.

    2. Reachability/dominance: an artifact a state reads must be
       guaranteed-written on every entry-path from the start state to
       the reading state, otherwise the executor substitutes None at
       run time and silently feeds wrong data into prompts or
       transforms. Implemented as a forward must-reach analysis with
       intersection over entry-paths and union over fan-out children
       at the join site (the executor only enters the join when every
       child has completed, so all children's writes are guaranteed
       there; sibling-write collisions are already rejected, so each
       artifact a child writes has a unique writer in the group).
       External inputs and ``initial`` artifacts trivially satisfy
       the rule.
    """
    artifact_decls = {a.name: a for a in workflow.artifacts}
    external_names = {e.name for e in workflow.external_inputs}
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
            if not has_initial and r not in written_by_some_state:
                raise ValidationError(
                    f"state {s.name!r}: reads artifact {r!r} that is never initialized or written"
                )

    if not workflow.states:
        return
    state_decls = {s.name: s for s in workflow.states}
    state_names = list(state_decls.keys())
    start = state_names[0]

    # An entry-path describes how control reaches the state. A normal
    # entry-path names a single parent state (its writes contribute).
    # A fan-out join entry-path names the fan-out parent plus all
    # children; the executor only enters the join target when every
    # child has completed, so children's writes are guaranteed at the
    # join site (sibling collisions are rejected upstream, so each
    # written artifact has exactly one producer in the group).
    entry_paths: dict[str, list[tuple[str, tuple[str, ...]]]] = {n: [] for n in state_names}
    for s in workflow.states:
        for t in s.transitions:
            if t.is_fan_out():
                for child in t.fan_out:
                    if child in entry_paths:
                        entry_paths[child].append((s.name, ()))
                if t.target in entry_paths:
                    entry_paths[t.target].append((s.name, t.fan_out))
                if t.error_target is not None and t.error_target in entry_paths:
                    entry_paths[t.error_target].append((s.name, ()))
            else:
                if t.target in entry_paths:
                    entry_paths[t.target].append((s.name, ()))

    initial_artifacts = {a.name for a in workflow.artifacts if a.initial is not NO_INITIAL}
    universe = set(written_by_some_state) | initial_artifacts | external_names
    base_set = set(initial_artifacts) | set(external_names)

    reaching: dict[str, set[str]] = {start: set(base_set)}
    for n in state_names:
        if n != start:
            reaching[n] = set(universe)

    def _contribution(parent: str, children: tuple[str, ...]) -> set[str]:
        out = reaching[parent] | {w.name for w in state_decls[parent].writes}
        for c in children:
            out = out | {w.name for w in state_decls[c].writes}
        return out

    changed = True
    iterations = 0
    max_iterations = len(state_names) * (len(universe) + 1) + 1
    while changed:
        changed = False
        iterations += 1
        if iterations > max_iterations:
            raise ValidationError(
                "dataflow analysis did not converge; workflow graph may have an unexpected shape"
            )
        for n in state_names:
            if n == start:
                continue
            paths = entry_paths[n]
            new_set: set[str] | None
            if not paths:
                # Unreachable from start. Reads must rely on
                # initial/external; pin reaching to that base.
                new_set = set(base_set)
            else:
                new_set = None
                for parent, children in paths:
                    contrib = _contribution(parent, children)
                    if new_set is None:
                        new_set = set(contrib)
                    else:
                        new_set &= contrib
                assert new_set is not None
            if new_set != reaching[n]:
                reaching[n] = new_set
                changed = True

    role_decls = {r.name: r for r in workflow.roles}

    def _check_artifact_dominated(
        site_state: str,
        artifact: str,
        kind: str,
        available: set[str],
    ) -> None:
        if artifact in external_names:
            return
        if artifact not in artifact_decls:
            return
        decl = artifact_decls[artifact]
        if decl.initial is not NO_INITIAL:
            return
        if artifact in available:
            return
        raise ValidationError(
            f"state {site_state!r}: {kind} reference {artifact!r} is "
            "not guaranteed to have been written on every path from "
            "the start state. Mark the artifact with 'initial' "
            "(any value, including null), or restructure the workflow "
            "so a writer of this artifact dominates the site."
        )

    for s in workflow.states:
        # Pre-invocation reads (state.reads + prompt template vars)
        # see reaching[s] only; the state's own writes have not
        # happened yet.
        pre_reads = reaching[s.name]
        for r in s.reads:
            _check_artifact_dominated(s.name, r, "read", pre_reads)

        # Prompt-template variable references are real data
        # dependencies: the executor renders a template by reading
        # each variable from external inputs or read_latest, so an
        # unwritten artifact silently substitutes None. The prompt
        # source is either declared on the state or inherited from the
        # state's role default.
        prompt = s.prompt
        if prompt is None and s.role is not None:
            role = role_decls.get(s.role)
            if role is not None:
                prompt = role.default_prompt
        if prompt is not None and prompt.kind == "template":
            for var in prompt.template_vars:
                _check_artifact_dominated(s.name, var, "prompt template variable", pre_reads)

        # Guards run AFTER the state's actor body and AFTER its writes
        # commit, so guard references see reaching[s] plus the state's
        # own writes. State-envelope references must be either the
        # current state itself (its just-completed envelope is
        # guaranteed) or a state that dominates the current one
        # (so its envelope is already in the executor's table). Counter
        # references (attempts.<state>, retries.<state>) are always
        # available because the executor's counter dicts cover every
        # declared state.
        guard_available = reaching[s.name] | {w.name for w in s.writes}
        for t in s.transitions:
            if t.guard is None:
                continue
            for ref in _collect_refs(t.guard):
                head = ref.head()
                if head in ("attempts", "retries"):
                    continue
                if head in artifact_decls or head in external_names:
                    _check_artifact_dominated(s.name, head, "guard data", guard_available)
                    continue
                if head in state_decls:
                    if head == s.name:
                        # Self-envelope: the just-completed invocation
                        # is always available to the state's own
                        # guards.
                        continue
                    if not _state_dominates(head, s.name, entry_paths, state_decls):
                        raise ValidationError(
                            f"state {s.name!r}: guard references "
                            f"envelope {ref!s} but state {head!r} is "
                            "not guaranteed to have completed before "
                            f"the guard on {s.name!r} runs. Restructure "
                            "the workflow so the referenced state "
                            "dominates the guard site."
                        )


def _state_dominates(
    candidate: str,
    target: str,
    entry_paths: dict[str, list[tuple[str, tuple[str, ...]]]],
    state_decls: dict[str, StateDecl],
) -> bool:
    """Return True when ``candidate`` lies on every path from the
    workflow's start state to ``target``.

    Uses the same entry-path encoding as ``_phase6_dataflow``: a
    fan-out join target's predecessors are the children for join
    purposes, but for dominator analysis the relevant predecessor is
    the fan-out parent (the children themselves do not dominate the
    join because each child is one of several parallel paths). We
    treat fan-out children as predecessors of the join when computing
    "must lie on every path" because the executor only enters the
    join when ALL children completed; a child therefore dominates the
    join iff the child is the only entry-path or every fan-out join
    entry-path includes that child as a sibling.

    The simpler invariant we actually need: a state X dominates Y iff
    every entry-path of Y has X as the parent or X dominates the
    parent (and, for fan-out-join entry-paths, X dominates the
    parent OR X is one of the children).
    """
    if candidate == target:
        return True
    state_names = list(state_decls.keys())
    if not state_names:
        return False
    start = state_names[0]
    # Compute dominators iteratively. dom[s] = set of states that
    # dominate s, initialized to all states for non-start, {start}
    # for start.
    universe = set(state_names)
    dom: dict[str, set[str]] = {start: {start}}
    for n in state_names:
        if n != start:
            dom[n] = set(universe)
    changed = True
    while changed:
        changed = False
        for n in state_names:
            if n == start:
                continue
            paths = entry_paths.get(n, [])
            new_set: set[str] | None
            if not paths:
                new_set = {n}
            else:
                new_set = None
                for parent, children in paths:
                    contrib = dom[parent] | {parent}
                    if children:
                        for c in children:
                            contrib = contrib | {c}
                    if new_set is None:
                        new_set = set(contrib)
                    else:
                        new_set &= contrib
                assert new_set is not None
                new_set = new_set | {n}
            if new_set != dom[n]:
                dom[n] = new_set
                changed = True
    return candidate in dom.get(target, set())


# --------------------------------------------------------------------
# Phase 7: cycle bounds (lint only)
# --------------------------------------------------------------------


def _transition_targets(t: Transition) -> tuple[str, ...]:
    """Every state name a transition can route to: its linear target,
    any fan-out children, and the on-error join target."""
    targets = [t.target, *t.fan_out]
    if t.error_target is not None:
        targets.append(t.error_target)
    return tuple(targets)


def _cycle_is_bounded(cycle: list[str], states_by_name: dict[str, StateDecl]) -> bool:
    """A cycle is bounded (rule 11) when at least one of its states
    offers a termination mechanism: a guarded transition (a counter
    guard, a guard on workflow state the cycle can change, or a verdict
    that routes out), a retry counter, or a human gate that can exit.
    A cycle whose every transition is an unconditional route is the
    unbounded case this lint warns about."""
    for name in cycle:
        state = states_by_name.get(name)
        if state is None:
            continue
        if state.actor.kind == "human":
            return True
        for t in state.transitions:
            if t.guard is not None or t.retry_max is not None:
                return True
    return False


def _phase7_cycle_bounds(workflow: Workflow) -> None:
    states_by_name = {s.name: s for s in workflow.states}
    graph: dict[str, set[str]] = {s.name: set() for s in workflow.states}
    for s in workflow.states:
        for t in s.transitions:
            for target in _transition_targets(t):
                if target in graph:
                    graph[s.name].add(target)
    visited: set[str] = set()
    path: list[str] = []
    on_path: set[str] = set()
    reported: set[frozenset[str]] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        path.append(node)
        on_path.add(node)
        for nxt in graph[node]:
            if nxt in on_path:
                cycle = path[path.index(nxt) :]
                key = frozenset(cycle)
                if key not in reported and not _cycle_is_bounded(cycle, states_by_name):
                    reported.add(key)
                    warnings.warn(
                        f"workflow {workflow.name!r}: cycle "
                        f"{' -> '.join([*cycle, nxt])} has no termination "
                        "mechanism on any transition (no counter guard, "
                        "verdict, retry, or human gate); max_total_steps "
                        "remains the only ceiling.",
                        UserWarning,
                        stacklevel=2,
                    )
                continue
            if nxt not in visited:
                dfs(nxt)
        path.pop()
        on_path.discard(node)

    for s in workflow.states:
        if s.name not in visited:
            dfs(s.name)
