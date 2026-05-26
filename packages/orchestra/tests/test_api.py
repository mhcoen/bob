"""Tests for the api-side role-binding resolution and validation.

Covers ``_resolve_role_binding`` and ``_validate_role_bindings`` in
``orchestra/api.py``. The proposal in
``design/orchestra-shared-role-bindings-proposal.md`` defines the
resolution rules; these tests confirm the validator catches the same
misconfigurations the prior per-workflow validator caught, expressed
in the new schema.

The tests load a real workflow file (``single.orc``) via the loader's
public entry point so the dispatcher attaches against the same
workflow shape ``run_workflow`` uses at runtime.
"""

from __future__ import annotations

import pytest

from orchestra.api import (
    _pre_load_registry,
    _resolve_role_binding,
    _validate_role_bindings,
)
from orchestra.config import (
    ConfigError,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
)
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path


def _single_workflow():
    path = resolve_workflow_path("single", project_dir=None)
    return load_workflow(path, _pre_load_registry())


# --------------------------------------------------------------------
# _resolve_role_binding
# --------------------------------------------------------------------


def test_resolve_uses_top_level_when_no_override() -> None:
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_agent",
                model="opus",
                tools="default",
            ),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    binding = _resolve_role_binding("code_edit", "editor", cfg)
    assert binding.adapter == "claude_code_agent"
    assert binding.model == "opus"
    assert binding.tools == "default"


def test_resolve_applies_override_replacing_keys() -> None:
    cfg = OrchestraConfig(
        roles={
            "drafter": RoleBinding(
                adapter="claude_code_text",
                model="kimi-k2.6",
                parameters={"temperature": 0.0},
            ),
        },
        workflows={
            "code_edit_aggressive": WorkflowConfig(
                pattern="draft_then_adjudicate",
                role_overrides={
                    "drafter": {"model": "deepseek-v4-pro"},
                },
            ),
        },
    )
    binding = _resolve_role_binding("code_edit_aggressive", "drafter", cfg)
    assert binding.adapter == "claude_code_text"
    assert binding.model == "deepseek-v4-pro"
    assert binding.parameters == {"temperature": 0.0}


def test_resolve_override_replaces_parameters_entirely() -> None:
    cfg = OrchestraConfig(
        roles={
            "drafter": RoleBinding(
                adapter="claude_code_text",
                parameters={"temperature": 0.0, "top_p": 1.0},
            ),
        },
        workflows={
            "code_edit": WorkflowConfig(
                pattern="single",
                role_overrides={
                    "drafter": {"parameters": {"top_p": 0.5}},
                },
            ),
        },
    )
    binding = _resolve_role_binding("code_edit", "drafter", cfg)
    assert binding.parameters == {"top_p": 0.5}


def test_resolve_missing_top_level_errors() -> None:
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(adapter="claude_code_agent"),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    with pytest.raises(ConfigError) as excinfo:
        _resolve_role_binding("code_edit", "drafter", cfg)
    msg = str(excinfo.value)
    assert "drafter" in msg
    assert "code_edit" in msg


def test_resolve_override_references_missing_top_level_errors() -> None:
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(adapter="claude_code_agent"),
        },
        workflows={
            "code_edit": WorkflowConfig(
                pattern="single",
                role_overrides={
                    "drafter": {"model": "deepseek-v4-pro"},
                },
            ),
        },
    )
    with pytest.raises(ConfigError) as excinfo:
        _resolve_role_binding("code_edit", "drafter", cfg)
    msg = str(excinfo.value)
    assert "drafter" in msg
    assert "no corresponding top-level binding" in msg


def test_resolve_workflow_lookup_missing_errors() -> None:
    cfg = OrchestraConfig(
        roles={"editor": RoleBinding(adapter="claude_code_agent")},
        workflows={},
    )
    with pytest.raises(ConfigError) as excinfo:
        _resolve_role_binding("code_edit", "editor", cfg)
    assert "code_edit" in str(excinfo.value)


# --------------------------------------------------------------------
# _validate_role_bindings against single.orc
# --------------------------------------------------------------------


def test_validate_resolves_and_returns_bindings_for_single() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_agent",
                model="opus",
                tools="default",
            ),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    resolved = _validate_role_bindings(workflow, "code_edit", cfg)
    assert "editor" in resolved
    assert resolved["editor"].adapter == "claude_code_agent"
    assert resolved["editor"].model == "opus"


def test_validate_rejects_missing_role_binding() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "drafter": RoleBinding(adapter="claude_code_text"),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    with pytest.raises(ConfigError) as excinfo:
        _validate_role_bindings(workflow, "code_edit", cfg)
    msg = str(excinfo.value)
    assert "editor" in msg
    assert "code_edit" in msg


def test_validate_rejects_kind_mismatch_text_adapter_on_agent_state() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(adapter="claude_code_text"),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    with pytest.raises(ConfigError) as excinfo:
        _validate_role_bindings(workflow, "code_edit", cfg)
    msg = str(excinfo.value)
    assert "kind mismatch" in msg
    assert "editor" in msg
    assert "claude_code_text" in msg


def test_validate_rejects_unknown_adapter() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(adapter="totally_made_up_adapter"),
        },
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    with pytest.raises(ConfigError) as excinfo:
        _validate_role_bindings(workflow, "code_edit", cfg)
    msg = str(excinfo.value)
    assert "totally_made_up_adapter" in msg
    assert "not a known orchestra adapter" in msg


def test_validate_applies_override_at_resolution_time() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_agent",
                model="sonnet",
            ),
        },
        workflows={
            "code_edit": WorkflowConfig(
                pattern="single",
                role_overrides={
                    "editor": {"model": "opus"},
                },
            ),
        },
    )
    resolved = _validate_role_bindings(workflow, "code_edit", cfg)
    assert resolved["editor"].model == "opus"
    assert resolved["editor"].adapter == "claude_code_agent"


def test_validate_dangling_override_errors() -> None:
    workflow = _single_workflow()
    cfg = OrchestraConfig(
        roles={
            "drafter": RoleBinding(adapter="claude_code_text"),
        },
        workflows={
            "code_edit": WorkflowConfig(
                pattern="single",
                role_overrides={
                    "editor": {"model": "opus"},
                },
            ),
        },
    )
    with pytest.raises(ConfigError) as excinfo:
        _validate_role_bindings(workflow, "code_edit", cfg)
    msg = str(excinfo.value)
    assert "editor" in msg
    assert "no corresponding top-level binding" in msg
