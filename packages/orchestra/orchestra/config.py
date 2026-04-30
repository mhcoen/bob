"""Project-local Orchestra configuration loader.

Reads ``.orchestra/config.json`` from a consumer project (mcloop,
Duplo, or anything else that imports orchestra). The schema is the
two-tier form documented in
``design/orchestra-shared-role-bindings-proposal.md``:

    {
      "roles": {
        "<role_name>": {
          "adapter": "<registered_backing_name>",
          "model": "<model_id>",
          "instruction_template": "<path_or_inline_string>",
          "tools": "default" | "<comma_separated_override>",
          "parameters": { ... }
        }
      },
      "workflows": {
        "<workflow_name>": {
          "pattern": "<workflow_file_name>",
          "role_overrides": {
            "<role_name>": {
              "<key>": "<value>",
              ...
            }
          }
        }
      }
    }

Top-level ``roles`` defines the actor identities once. Each workflow
references them by name through its ``pattern``. A workflow may
specify ``role_overrides.<role>`` to replace individual binding keys
for that workflow only. Override values replace the top-level value
entirely. There is no nested merging.

The loader is permissive at the top level and strict per role: missing
required keys (``adapter``) raise ``ConfigError`` so the integration
fails loudly rather than running with the wrong adapter. Other fields
have sensible defaults so a minimal config still loads. The loader
rejects the legacy per-workflow ``roles`` shape with a clear migration
hint pointing at the proposal doc.
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
class RoleBinding:
    """One role's binding to an adapter, model, template, and parameters."""

    adapter: str
    model: str | None = None
    instruction_template: str | None = None
    tools: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, role_name: str, raw: dict[str, Any]) -> RoleBinding:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"role {role_name!r}: expected an object, got {type(raw).__name__}"
            )
        adapter = raw.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise ConfigError(
                f"role {role_name!r}: missing or empty 'adapter' key"
            )
        return cls(
            adapter=adapter,
            **_role_optional_fields(role_name, raw),
        )

    def with_overrides(
        self, role_name: str, overrides: dict[str, Any]
    ) -> RoleBinding:
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
                        f"role {role_name!r}: override 'adapter' must be a "
                        "non-empty string"
                    )
                replacements["adapter"] = value
            elif key == "model":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(
                        f"role {role_name!r}: override 'model' must be a string"
                    )
                replacements["model"] = value
            elif key == "instruction_template":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(
                        f"role {role_name!r}: override 'instruction_template' "
                        "must be a string"
                    )
                replacements["instruction_template"] = value
            elif key == "tools":
                if value is not None and not isinstance(value, str):
                    raise ConfigError(
                        f"role {role_name!r}: override 'tools' must be a string"
                    )
                replacements["tools"] = value
            elif key == "parameters":
                if not isinstance(value, dict):
                    raise ConfigError(
                        f"role {role_name!r}: override 'parameters' must be "
                        "an object"
                    )
                replacements["parameters"] = dict(value)
            else:
                raise ConfigError(
                    f"role {role_name!r}: unknown override key {key!r}. "
                    "Valid keys: adapter, model, instruction_template, "
                    "tools, parameters"
                )
        return replace(self, **replacements)


def _role_optional_fields(
    role_name: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Validate and extract the optional keys shared by RoleBinding init."""
    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(
            f"role {role_name!r}: 'model' must be a string"
        )
    instruction_template = raw.get("instruction_template")
    if instruction_template is not None and not isinstance(
        instruction_template, str
    ):
        raise ConfigError(
            f"role {role_name!r}: 'instruction_template' must be a string"
        )
    tools = raw.get("tools")
    if tools is not None and not isinstance(tools, str):
        raise ConfigError(
            f"role {role_name!r}: 'tools' must be a string"
        )
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ConfigError(
            f"role {role_name!r}: 'parameters' must be an object"
        )
    return {
        "model": model,
        "instruction_template": instruction_template,
        "tools": tools,
        "parameters": dict(parameters),
    }


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
                f"workflow {workflow_name!r}: expected an object, got "
                f"{type(raw).__name__}"
            )
        if "roles" in raw:
            raise ConfigError(
                f"workflow {workflow_name!r}: per-workflow 'roles' block "
                "is no longer supported. Move role bindings to the "
                f"top-level 'roles' table. {_PROPOSAL_HINT}"
            )
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(
                f"workflow {workflow_name!r}: missing or empty 'pattern' key"
            )
        overrides_raw = raw.get("role_overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise ConfigError(
                f"workflow {workflow_name!r}: 'role_overrides' must be an object"
            )
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
    """

    roles: dict[str, RoleBinding] = field(default_factory=dict)
    workflows: dict[str, WorkflowConfig] = field(default_factory=dict)

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
            raise ConfigError(
                f"config root must be an object, got {type(raw).__name__}"
            )
        if "roles" not in raw:
            raise ConfigError(
                "config is missing the top-level 'roles' table. "
                f"{_PROPOSAL_HINT}"
            )
        roles_raw = raw.get("roles") or {}
        if not isinstance(roles_raw, dict):
            raise ConfigError("'roles' must be an object")
        roles = {
            name: RoleBinding.from_dict(name, body)
            for name, body in roles_raw.items()
        }
        workflows_raw = raw.get("workflows") or {}
        if not isinstance(workflows_raw, dict):
            raise ConfigError("'workflows' must be an object")
        workflows = {
            name: WorkflowConfig.from_dict(name, body)
            for name, body in workflows_raw.items()
        }
        return cls(roles=roles, workflows=workflows)


def default_config() -> OrchestraConfig:
    """Return the default OrchestraConfig.

    The default puts an ``editor`` binding in the top-level ``roles``
    table (claude_code_agent + default tools, no model pin) and maps
    ``code_edit`` to the ``single`` pattern. This preserves
    zero-regression fallback when a project has not yet written a
    ``.orchestra/config.json``: the consumer (mcloop) can call
    ``run_workflow("code_edit", ...)`` without first creating a config
    file and still get the current behavior.
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
    )


def load_config(project_dir: Path | str) -> OrchestraConfig:
    """Load ``.orchestra/config.json`` from ``project_dir``.

    Returns the default config (see ``default_config``) if the file
    does not exist. Raises ``ConfigError`` if the file exists but
    cannot be parsed or fails schema validation.
    """
    path = Path(project_dir) / CONFIG_RELATIVE_PATH
    if not path.is_file():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path}: invalid JSON ({exc})"
        ) from exc
    return OrchestraConfig.from_dict(raw)
