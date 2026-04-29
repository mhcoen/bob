"""Project-local Orchestra configuration loader.

Reads ``.orchestra/config.json`` from a consumer project (mcloop,
Duplo, or anything else that imports orchestra). The schema is the
nested form documented in
``design/orchestra-mcloop-integration-plan.md``:

    {
      "workflows": {
        "<workflow_name>": {
          "pattern": "<workflow_file_name>",
          "roles": {
            "<role_name>": {
              "adapter": "<registered_backing_name>",
              "model": "<model_id>",
              "instruction_template": "<path_or_inline_string>",
              "tools": "default" | "<comma_separated_override>",
              "parameters": { ... }
            }
          }
        }
      }
    }

The loader is permissive at the top level and strict per role: missing
required keys (``adapter``) raise ``ConfigError`` so the integration
fails loudly rather than running with the wrong adapter. Other fields
have sensible defaults so a minimal config still loads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.errors import OrchestraError

CONFIG_RELATIVE_PATH: str = ".orchestra/config.json"


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
        return cls(
            adapter=adapter,
            model=model,
            instruction_template=instruction_template,
            tools=tools,
            parameters=dict(parameters),
        )


@dataclass(frozen=True)
class WorkflowConfig:
    """Per-workflow config: which pattern to run, with what role bindings."""

    pattern: str
    roles: dict[str, RoleBinding] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, workflow_name: str, raw: dict[str, Any]) -> WorkflowConfig:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"workflow {workflow_name!r}: expected an object, got "
                f"{type(raw).__name__}"
            )
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(
                f"workflow {workflow_name!r}: missing or empty 'pattern' key"
            )
        roles_raw = raw.get("roles") or {}
        if not isinstance(roles_raw, dict):
            raise ConfigError(
                f"workflow {workflow_name!r}: 'roles' must be an object"
            )
        roles = {
            name: RoleBinding.from_dict(name, body)
            for name, body in roles_raw.items()
        }
        return cls(pattern=pattern, roles=roles)


@dataclass(frozen=True)
class OrchestraConfig:
    """Whole-project Orchestra config."""

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
        workflows_raw = raw.get("workflows") or {}
        if not isinstance(workflows_raw, dict):
            raise ConfigError("'workflows' must be an object")
        workflows = {
            name: WorkflowConfig.from_dict(name, body)
            for name, body in workflows_raw.items()
        }
        return cls(workflows=workflows)


def default_config() -> OrchestraConfig:
    """Return the default OrchestraConfig.

    The default maps ``code_edit`` to the ``single`` pattern with the
    edit-agent adapter, no model override, default tools, and no
    parameters. This preserves zero-regression fallback when a project
    has not yet written a ``.orchestra/config.json``: the consumer
    (mcloop) can call ``run_workflow("code_edit", ...)`` without first
    creating a config file and still get the current behavior.
    """
    return OrchestraConfig(
        workflows={
            "code_edit": WorkflowConfig(
                pattern="single",
                roles={
                    "editor": RoleBinding(
                        adapter="claude_code_agent",
                        model=None,
                        instruction_template=None,
                        tools=None,
                        parameters={},
                    )
                },
            )
        }
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
