"""Tests for the two-tier OrchestraConfig schema.

Covers the shape documented in
``design/orchestra-shared-role-bindings-proposal.md``: a top-level
``roles`` table whose entries are full role bindings, plus a
``workflows`` table whose entries name a pattern and (optionally)
``role_overrides`` that replace individual binding keys.

Resolution itself lives in ``orchestra.api._resolve_role_binding`` and
is exercised in ``tests/test_api.py``. These tests cover the loader
and validator: shape errors, default-config shape, override shape
checks, and rejection of the legacy per-workflow ``roles`` form.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.config import (
    ConfigError,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
    default_config,
    load_config,
)


def _write_config(project_dir: Path, body: dict) -> Path:
    cfg_dir = project_dir / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    path.write_text(json.dumps(body))
    return path


def test_default_config_shape() -> None:
    cfg = default_config()
    assert "editor" in cfg.roles
    editor = cfg.roles["editor"]
    assert editor.adapter == "claude_code_agent"
    assert editor.model is None
    assert editor.tools is None
    assert editor.parameters == {}
    assert "code_edit" in cfg.workflows
    wf = cfg.workflows["code_edit"]
    assert wf.pattern == "single"
    assert wf.role_overrides == {}


def test_load_config_returns_default_when_file_absent(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.roles == default_config().roles
    assert cfg.workflows == default_config().workflows


def test_load_config_parses_minimal_new_shape(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_agent",
                    "model": "opus",
                    "tools": "default",
                    "parameters": {},
                },
            },
            "workflows": {
                "code_edit": {"pattern": "single"},
            },
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.roles["editor"].adapter == "claude_code_agent"
    assert cfg.roles["editor"].model == "opus"
    assert cfg.workflows["code_edit"].pattern == "single"
    assert cfg.workflows["code_edit"].role_overrides == {}


def test_load_config_parses_role_overrides(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "roles": {
                "drafter": {
                    "adapter": "claude_code_text",
                    "model": "kimi-k2.6",
                    "parameters": {},
                },
            },
            "workflows": {
                "code_edit_aggressive": {
                    "pattern": "draft_then_adjudicate",
                    "role_overrides": {
                        "drafter": {"model": "deepseek-v4-pro"},
                    },
                },
            },
        },
    )
    cfg = load_config(tmp_path)
    wf = cfg.workflows["code_edit_aggressive"]
    assert wf.pattern == "draft_then_adjudicate"
    assert wf.role_overrides == {"drafter": {"model": "deepseek-v4-pro"}}


def test_old_shape_rejected_with_migration_hint() -> None:
    raw = {
        "workflows": {
            "code_edit": {
                "pattern": "single",
                "roles": {
                    "editor": {"adapter": "claude_code_agent"}
                },
            }
        }
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    msg = str(excinfo.value)
    assert "top-level 'roles'" in msg
    assert "orchestra-shared-role-bindings-proposal.md" in msg


def test_per_workflow_roles_block_rejected() -> None:
    raw = {
        "roles": {
            "editor": {"adapter": "claude_code_agent"},
        },
        "workflows": {
            "code_edit": {
                "pattern": "single",
                "roles": {
                    "editor": {"adapter": "claude_code_agent"},
                },
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    msg = str(excinfo.value)
    assert "code_edit" in msg
    assert "no longer supported" in msg
    assert "orchestra-shared-role-bindings-proposal.md" in msg


def test_missing_top_level_roles_key_errors() -> None:
    raw = {"workflows": {"code_edit": {"pattern": "single"}}}
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "top-level 'roles'" in str(excinfo.value)


def test_role_missing_adapter_errors() -> None:
    raw = {
        "roles": {
            "editor": {"model": "opus"},
        },
        "workflows": {},
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "editor" in str(excinfo.value)
    assert "adapter" in str(excinfo.value)


def test_workflow_missing_pattern_errors() -> None:
    raw = {
        "roles": {
            "editor": {"adapter": "claude_code_agent"},
        },
        "workflows": {
            "code_edit": {},
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    msg = str(excinfo.value)
    assert "code_edit" in msg
    assert "pattern" in msg


def test_role_overrides_must_be_object() -> None:
    raw = {
        "roles": {
            "drafter": {"adapter": "claude_code_text"},
        },
        "workflows": {
            "code_edit": {
                "pattern": "single",
                "role_overrides": ["not", "an", "object"],
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "role_overrides" in str(excinfo.value)


def test_role_overrides_entry_must_be_object() -> None:
    raw = {
        "roles": {
            "drafter": {"adapter": "claude_code_text"},
        },
        "workflows": {
            "code_edit": {
                "pattern": "single",
                "role_overrides": {"drafter": "kimi"},
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "drafter" in str(excinfo.value)


def test_with_overrides_replaces_keys() -> None:
    base = RoleBinding(
        adapter="claude_code_text",
        model="kimi-k2.6",
        tools=None,
        parameters={"temperature": 0.0},
    )
    out = base.with_overrides("drafter", {"model": "deepseek-v4-pro"})
    assert out.adapter == "claude_code_text"
    assert out.model == "deepseek-v4-pro"
    assert out.parameters == {"temperature": 0.0}


def test_with_overrides_replaces_parameters_dict() -> None:
    base = RoleBinding(
        adapter="claude_code_text",
        parameters={"temperature": 0.0, "top_p": 1.0},
    )
    out = base.with_overrides("drafter", {"parameters": {"top_p": 0.5}})
    assert out.parameters == {"top_p": 0.5}


def test_with_overrides_rejects_unknown_key() -> None:
    base = RoleBinding(adapter="claude_code_text")
    with pytest.raises(ConfigError) as excinfo:
        base.with_overrides("drafter", {"made_up_field": "x"})
    assert "made_up_field" in str(excinfo.value)


def test_with_overrides_rejects_wrong_type() -> None:
    base = RoleBinding(adapter="claude_code_text")
    with pytest.raises(ConfigError) as excinfo:
        base.with_overrides("drafter", {"model": 123})
    assert "model" in str(excinfo.value)


def test_load_config_invalid_json_errors(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".orchestra"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("not valid json {{")
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)
    assert "invalid JSON" in str(excinfo.value)


def test_workflow_lookup_missing_errors() -> None:
    cfg = default_config()
    with pytest.raises(ConfigError) as excinfo:
        cfg.workflow("nope")
    assert "nope" in str(excinfo.value)


def test_workflow_config_default_role_overrides() -> None:
    wf = WorkflowConfig(pattern="single")
    assert wf.role_overrides == {}
