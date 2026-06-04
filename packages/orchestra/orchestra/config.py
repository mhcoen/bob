"""Orchestra configuration loader.

Reads two-tier configs documented in
``design/orchestra-shared-role-bindings-proposal.md``. A config has
top-level ``roles`` (canonical role-to-binding identities), top-level
``workflows`` (patterns plus optional ``role_overrides``), and
optional top-level ``verbs`` (verb-name to workflow-name mappings for
the CLI).

The loader assembles a single merged ``OrchestraConfig`` from up to
two on-disk files:

1. ``~/.orchestra/config.json`` (global). Defines roles, verbs, and
   workflows shared across projects. Always loaded first.
2. ``<project>/.orchestra/config.json`` (project-local, optional).
   Overrides specific entries on a per-key basis.

Merge rule (replace, not nest): for each top-level section, entries
in the project config replace entries of the same key in the global
config in full. Entries the project does not redefine are inherited.
The replace semantics match the workflow-level ``role_overrides``
behavior, applied one level up.

When neither file exists, ``load_config`` returns
``default_config()`` so consumers (mcloop) keep zero-regression
behavior. The loader rejects the legacy per-workflow ``roles`` shape
with a clear migration hint pointing at the proposal doc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from orchestra.errors import OrchestraError

CONFIG_RELATIVE_PATH: str = ".orchestra/config.json"

_PROPOSAL_HINT: str = (
    "see design/orchestra-shared-role-bindings-proposal.md for the "
    "current schema. Top-level 'roles' table plus 'workflows.<name>.pattern' "
    "with optional 'role_overrides'."
)


class ConfigError(OrchestraError):
    """Raised when the config file is malformed or missing required keys."""


@dataclass(frozen=True)
class CriterionDecl:
    """One acceptance criterion declared in ``.orchestra/config.json``.

    Promoted by F2.5a as the source of truth for criterion enumeration:
    the judge's verdict must include exactly one ``criteria_compliance``
    entry per declared criterion, observed against the current artifact.
    The pre-F2.5a model relied on the judge enumerating criteria from
    free-form ``task.md`` prose, which the iter-anchor calibration
    falsified as insufficient (see REPORT.md Addendum 6).

    ``id`` is referenced from the verdict's ``criterion_id`` field and
    must appear as a whole word in ``task.md`` (enforced by
    ``orchestra.calibration.lint_scenario``). ``required=True``
    criteria gate the accept-consistency invariant; ``required=False``
    criteria are reported in ``criteria_compliance`` for trajectory
    analysis but do not block accept.
    """

    id: str
    description: str
    required: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CriterionDecl:
        if not isinstance(raw, dict):
            raise ConfigError(f"criterion: expected an object, got {type(raw).__name__}")
        cid = raw.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise ConfigError(f"criterion: 'id' must be a non-empty string, got {cid!r}")
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ConfigError(
                f"criterion {cid!r}: 'description' must be a non-empty string, got {description!r}"
            )
        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise ConfigError(
                f"criterion {cid!r}: 'required' must be a bool, got {type(required).__name__}"
            )
        return cls(id=cid, description=description, required=required)


def _parse_criteria(context: str, raw_value: Any) -> tuple[CriterionDecl, ...]:
    """Parse a ``criteria`` array into a tuple of ``CriterionDecl``.

    Shared by ``OrchestraConfig.from_dict`` (top-level criteria) and
    ``CompoundRoleBinding.from_dict`` (role-scoped criteria) so both
    accept the same shape and enforce the same unique-id rule.
    ``context`` names the owning scope for error messages.
    """
    criteria_raw = raw_value or []
    if not isinstance(criteria_raw, list):
        raise ConfigError(f"{context}: 'criteria' must be an array")
    criteria_list = [CriterionDecl.from_dict(item) for item in criteria_raw]
    seen_ids: set[str] = set()
    for crit in criteria_list:
        if crit.id in seen_ids:
            raise ConfigError(f"{context}: duplicate criterion id {crit.id!r}; ids must be unique")
        seen_ids.add(crit.id)
    return tuple(criteria_list)


@dataclass(frozen=True)
class RoleBinding:
    """One role's binding to an adapter, model, template, and parameters.

    ``adapter`` is required for top-level ``roles`` entries, but a leaf
    binding under a compound ``role_bindings`` entry may omit it when
    ``model`` names a registered model identifier (``opus``, ``codex``,
    ``kimi``, ...). ``orchestra.run_role`` resolves the identifier
    through the ``ProfileRegistry`` at workflow start and synthesizes
    the missing adapter+model pair from the registered tuple. See
    ``orchestra.registry.BUILTIN_MODEL_IDENTIFIERS`` for the shipped
    set and ``orchestra/README.md`` for the resolution path.
    """

    adapter: str | None = None
    model: str | None = None
    instruction_template: str | None = None
    tools: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        role_name: str,
        raw: dict[str, Any],
        *,
        require_adapter: bool = True,
    ) -> RoleBinding:
        if not isinstance(raw, dict):
            raise ConfigError(f"role {role_name!r}: expected an object, got {type(raw).__name__}")
        adapter = raw.get("adapter")
        if adapter is None and not require_adapter:
            # Compound role binding leaf: adapter may be omitted when
            # ``model`` names a registered identifier. run_role
            # resolves the identifier to an (adapter, model) tuple at
            # workflow start; the model field is required so the
            # resolution has something to look up.
            optional = _role_optional_fields(role_name, raw)
            if optional.get("model") is None:
                raise ConfigError(
                    f"role {role_name!r}: either 'adapter' or 'model' "
                    "must be set. A bare-model binding looks up the "
                    "model identifier (e.g. 'opus', 'codex') through "
                    "the ProfileRegistry to derive both adapter and "
                    "model at workflow start."
                )
            return cls(adapter=None, **optional)
        if not isinstance(adapter, str) or not adapter:
            raise ConfigError(f"role {role_name!r}: missing or empty 'adapter' key")
        return cls(
            adapter=adapter,
            **_role_optional_fields(role_name, raw),
        )

    def with_overrides(self, role_name: str, overrides: dict[str, Any]) -> RoleBinding:
        """Return a new binding with ``overrides`` applied on top.

        Override values replace top-level values entirely. Validates
        each override key against the role-binding shape so a typo or
        wrong-typed override fails fast.
        """
        if not isinstance(overrides, dict):
            raise ConfigError(
                f"role {role_name!r}: 'role_overrides' entry must be an object, "
                f"got {type(overrides).__name__}"
            )
        replacements: dict[str, Any] = {}
        for key, value in overrides.items():
            if key == "adapter":
                if not isinstance(value, str) or not value:
                    raise ConfigError(
                        f"role {role_name!r}: override 'adapter' must be a non-empty string"
                    )
                replacements["adapter"] = value
            elif key == "model":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(f"role {role_name!r}: override 'model' must be a string")
                replacements["model"] = value
            elif key == "instruction_template":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(
                        f"role {role_name!r}: override 'instruction_template' must be a string"
                    )
                replacements["instruction_template"] = value
            elif key == "tools":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(f"role {role_name!r}: override 'tools' must be a string")
                replacements["tools"] = value
            elif key == "parameters":
                if not isinstance(value, dict):
                    raise ConfigError(
                        f"role {role_name!r}: override 'parameters' must be an object"
                    )
                replacements["parameters"] = dict(value)
            else:
                raise ConfigError(
                    f"role {role_name!r}: unknown override key {key!r}. "
                    "Valid keys: adapter, model, instruction_template, "
                    "tools, parameters"
                )
        return replace(self, **replacements)


def _role_optional_fields(role_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and extract the optional keys shared by RoleBinding init."""
    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"role {role_name!r}: 'model' must be a string")
    instruction_template = raw.get("instruction_template")
    if instruction_template is not None and not isinstance(instruction_template, str):
        raise ConfigError(f"role {role_name!r}: 'instruction_template' must be a string")
    tools = raw.get("tools")
    if tools is not None and not isinstance(tools, str):
        raise ConfigError(f"role {role_name!r}: 'tools' must be a string")
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ConfigError(f"role {role_name!r}: 'parameters' must be an object")
    return {
        "model": model,
        "instruction_template": instruction_template,
        "tools": tools,
        "parameters": dict(parameters),
    }


@dataclass(frozen=True)
class CompoundRoleBinding:
    """A user-facing role-to-workflow binding consumed by
    ``orchestra.run_role(role_name, ...)``.

    Names the workflow pattern to run and, nested under it, the
    per-workflow-role bindings (e.g. ``judge``, ``reviewer``) the
    workflow's states will resolve at runtime. Distinct from the
    leaf ``RoleBinding`` form in ``OrchestraConfig.roles``: a leaf
    binding maps one workflow role to (adapter, model, ...) for
    ``run_workflow``, whereas a CompoundRoleBinding wraps a workflow
    plus a table of leaf bindings under one logical role name.

    ``max_rounds`` is a Phase-2 design-loop knob; it is parsed here
    so the schema accepts it without forcing every consumer to
    re-validate. ``criteria`` carries role-scoped acceptance criteria
    in the same shape ``OrchestraConfig.criteria`` accepts, letting a
    compound role run a workflow with its own criteria; it defaults to
    an empty tuple so existing bindings are unchanged. ``extra``
    captures any future top-level keys so forward-compatible config
    additions do not require schema surgery (the consumer reads them
    by name).
    """

    pattern: str
    bindings: dict[str, RoleBinding] = field(default_factory=dict)
    max_rounds: int | None = None
    criteria: tuple[CriterionDecl, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, role_name: str, raw: dict[str, Any]) -> CompoundRoleBinding:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"role_binding {role_name!r}: expected an object, got {type(raw).__name__}"
            )
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"role_binding {role_name!r}: missing or empty 'pattern' key")
        reserved = {"pattern", "max_rounds", "criteria"}
        max_rounds_raw = raw.get("max_rounds")
        if max_rounds_raw is not None and not isinstance(max_rounds_raw, int):
            raise ConfigError(
                f"role_binding {role_name!r}: 'max_rounds' must be an integer or omitted"
            )
        if isinstance(max_rounds_raw, bool):
            # bool is a subclass of int; reject explicitly to avoid
            # silently accepting True/False as a round count.
            raise ConfigError(
                f"role_binding {role_name!r}: 'max_rounds' must be an integer, got bool"
            )
        bindings: dict[str, RoleBinding] = {}
        extra: dict[str, Any] = {}
        for key, value in raw.items():
            if key in reserved:
                continue
            if isinstance(value, dict) and ("adapter" in value or "model" in value):
                # Leaf role binding. Adapter may be omitted when the
                # ``model`` field names a registered model identifier;
                # the actual lookup happens in run_role so an
                # unregistered identifier produces the documented
                # startup error at the right point in the lifecycle.
                bindings[key] = RoleBinding.from_dict(
                    f"{role_name}.{key}",
                    value,
                    require_adapter=False,
                )
            else:
                extra[key] = value
        criteria = _parse_criteria(f"role_binding {role_name!r}", raw.get("criteria"))
        return cls(
            pattern=pattern,
            bindings=bindings,
            max_rounds=max_rounds_raw,
            criteria=criteria,
            extra=extra,
        )


@dataclass(frozen=True)
class VerbBinding:
    """Maps a verb name to a workflow name.

    The CLI's verb dispatcher looks up the user-typed verb in the
    global config and runs the named workflow with a single ``query``
    input. Verb names are arbitrary; the user picks them. The
    referenced workflow must exist in the same config's ``workflows``
    table, but the loader does not enforce that to keep the config
    forgiving (a verb that points at a nonexistent workflow surfaces
    when the user invokes it, not at config load time).
    """

    workflow: str

    @classmethod
    def from_dict(cls, verb_name: str, raw: dict[str, Any]) -> VerbBinding:
        if not isinstance(raw, dict):
            raise ConfigError(f"verb {verb_name!r}: expected an object, got {type(raw).__name__}")
        workflow = raw.get("workflow")
        if not isinstance(workflow, str) or not workflow:
            raise ConfigError(f"verb {verb_name!r}: missing or empty 'workflow' key")
        return cls(workflow=workflow)


@dataclass(frozen=True)
class WorkflowConfig:
    """Per-workflow config: which pattern to run plus optional role overrides.

    The top-level ``roles`` table supplies the canonical bindings; this
    block names which pattern to instantiate and (if needed) which
    role-binding keys to replace for this workflow. The override is a
    partial RoleBinding shape, validated when applied.
    """

    pattern: str
    role_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, workflow_name: str, raw: dict[str, Any]) -> WorkflowConfig:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"workflow {workflow_name!r}: expected an object, got {type(raw).__name__}"
            )
        if "roles" in raw:
            raise ConfigError(
                f"workflow {workflow_name!r}: per-workflow 'roles' block "
                "is no longer supported. Move role bindings to the "
                f"top-level 'roles' table. {_PROPOSAL_HINT}"
            )
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"workflow {workflow_name!r}: missing or empty 'pattern' key")
        overrides_raw = raw.get("role_overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise ConfigError(f"workflow {workflow_name!r}: 'role_overrides' must be an object")
        role_overrides: dict[str, dict[str, Any]] = {}
        for role_name, override in overrides_raw.items():
            if not isinstance(override, dict):
                raise ConfigError(
                    f"workflow {workflow_name!r}: role_overrides entry "
                    f"{role_name!r} must be an object, got "
                    f"{type(override).__name__}"
                )
            role_overrides[role_name] = dict(override)
        return cls(pattern=pattern, role_overrides=role_overrides)


@dataclass(frozen=True)
class OrchestraConfig:
    """Whole-project Orchestra config.

    ``roles`` holds the canonical role-to-binding table. ``workflows``
    maps workflow names to a pattern plus optional per-workflow
    overrides. Resolution lives in ``orchestra.api._resolve_role_binding``.

    ``verbs`` maps verb names to workflow names for the verb-style CLI
    surface. The verb section is optional: a project-local config that
    only powers the McLoop integration omits it. The global config at
    ``~/.orchestra/config.json`` typically populates it.
    """

    roles: dict[str, RoleBinding] = field(default_factory=dict)
    workflows: dict[str, WorkflowConfig] = field(default_factory=dict)
    verbs: dict[str, VerbBinding] = field(default_factory=dict)
    role_bindings: dict[str, CompoundRoleBinding] = field(default_factory=dict)
    """Compound role bindings consumed by ``orchestra.run_role(name)``.
    Distinct from ``roles``: each entry wraps a workflow pattern plus a
    nested table of leaf RoleBindings the workflow's states resolve at
    runtime. See ``CompoundRoleBinding`` for the schema."""
    criteria: tuple[CriterionDecl, ...] = ()

    def workflow(self, name: str) -> WorkflowConfig:
        try:
            return self.workflows[name]
        except KeyError as exc:
            raise ConfigError(
                f"workflow {name!r} not configured in .orchestra/config.json"
            ) from exc

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OrchestraConfig:
        if not isinstance(raw, dict):
            raise ConfigError(f"config root must be an object, got {type(raw).__name__}")
        # Each top-level section is optional in a single config file.
        # The merge step combines a global file plus an optional
        # project-local file, and either may carry only a subset of
        # the sections. Validation that a workflow's required roles
        # have bindings runs against the merged config in the api,
        # not here, so a partial file does not fail to load.
        roles_raw = raw.get("roles") or {}
        if not isinstance(roles_raw, dict):
            raise ConfigError("'roles' must be an object")
        roles = {name: RoleBinding.from_dict(name, body) for name, body in roles_raw.items()}
        workflows_raw = raw.get("workflows") or {}
        if not isinstance(workflows_raw, dict):
            raise ConfigError("'workflows' must be an object")
        workflows = {
            name: WorkflowConfig.from_dict(name, body) for name, body in workflows_raw.items()
        }
        verbs_raw = raw.get("verbs") or {}
        if not isinstance(verbs_raw, dict):
            raise ConfigError("'verbs' must be an object")
        verbs = {name: VerbBinding.from_dict(name, body) for name, body in verbs_raw.items()}
        role_bindings_raw = raw.get("role_bindings") or {}
        if not isinstance(role_bindings_raw, dict):
            raise ConfigError("'role_bindings' must be an object")
        role_bindings = {
            name: CompoundRoleBinding.from_dict(name, body)
            for name, body in role_bindings_raw.items()
        }
        criteria = _parse_criteria("criteria", raw.get("criteria"))
        return cls(
            roles=roles,
            workflows=workflows,
            verbs=verbs,
            role_bindings=role_bindings,
            criteria=criteria,
        )


def default_config() -> OrchestraConfig:
    """Return the default OrchestraConfig.

    The default puts an ``editor`` binding in the top-level ``roles``
    table (claude_code_agent + default tools, no model pin) and maps
    ``code_edit`` to the ``single`` pattern. This preserves
    zero-regression fallback when a project has not yet written a
    ``.orchestra/config.json``: the consumer (mcloop) can call
    ``run_workflow("code_edit", ...)`` without first creating a config
    file and still get the current behavior.

    A default ``design`` compound role binding is also included so
    ``orchestra.run_role("design", ...)`` works without explicit
    configuration. Judge defaults to ``opus`` (a strong model for
    judgement-shaped roles); reviewer defaults to ``codex`` so the
    review is independent of the judge's training data. Both model
    fields are short identifiers resolved through the
    ``ProfileRegistry`` at workflow start.
    """
    return OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_agent",
                model=None,
                instruction_template=None,
                tools=None,
                parameters={},
            )
        },
        workflows={
            "code_edit": WorkflowConfig(pattern="single"),
        },
        role_bindings={
            "design": CompoundRoleBinding(
                pattern="design_loop",
                bindings={
                    "judge_role": RoleBinding(adapter=None, model="opus"),
                    "reviewer": RoleBinding(adapter=None, model="codex"),
                },
            ),
        },
    )


def global_config_path() -> Path:
    """Return the path the global verb-config lives at."""
    return Path.home() / ".orchestra" / "config.json"


def project_config_path(project_dir: Path | str) -> Path:
    """Return the path the project-local config lives at."""
    return Path(project_dir) / CONFIG_RELATIVE_PATH


def _read_config_file(path: Path) -> OrchestraConfig | None:
    """Parse one config file. Returns ``None`` if the file is absent.

    Raises ``ConfigError`` when the file exists but cannot be parsed
    or fails schema validation. Each top-level section is optional in
    isolation; merge-time logic upstream combines partial files into a
    complete config.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc
    return OrchestraConfig.from_dict(raw)


def _merge_configs(
    base: OrchestraConfig,
    overlay: OrchestraConfig,
) -> OrchestraConfig:
    """Return a merged config with ``overlay`` applied on top of ``base``.

    Per the proposal: for each top-level section (roles, workflows,
    verbs), entries in ``overlay`` replace entries of the same key in
    ``base`` in full. ``base`` entries that ``overlay`` does not
    redefine are inherited unchanged. There is no nested merging:
    overriding a single role replaces the entire RoleBinding for that
    role, including its parameters and tools.
    """
    return OrchestraConfig(
        roles={**base.roles, **overlay.roles},
        workflows={**base.workflows, **overlay.workflows},
        verbs={**base.verbs, **overlay.verbs},
        role_bindings={**base.role_bindings, **overlay.role_bindings},
    )


def load_config(
    project_dir: Path | str | None = None,
) -> OrchestraConfig:
    """Load and merge the global and (optional) project-local configs.

    Loads ``~/.orchestra/config.json`` first when present. If
    ``project_dir`` is given and contains ``.orchestra/config.json``,
    that file is merged on top via ``_merge_configs``. Returns
    ``default_config()`` when neither file exists. Either file may
    omit any top-level section; the merge fills gaps from the other
    side.
    """
    global_cfg = _read_config_file(global_config_path())
    project_cfg: OrchestraConfig | None = None
    if project_dir is not None:
        project_cfg = _read_config_file(project_config_path(project_dir))

    if global_cfg is None and project_cfg is None:
        return default_config()
    if project_cfg is None:
        assert global_cfg is not None
        return global_cfg
    if global_cfg is None:
        return project_cfg
    return _merge_configs(global_cfg, project_cfg)


def load_global_config() -> OrchestraConfig:
    """Load only the global config, raising if it is missing.

    Verb-style CLI uses this to detect the no-config case and emit a
    setup hint rather than silently falling back to ``default_config``.
    A loaded global config still supports being merged with a project
    config via ``load_config(project_dir=...)`` for callers that want
    both layers.
    """
    path = global_config_path()
    cfg = _read_config_file(path)
    if cfg is None:
        raise ConfigError(
            f"no config at {path}; create one with verb mappings to "
            "use this command. See `orchestra help` for the format."
        )
    return cfg
