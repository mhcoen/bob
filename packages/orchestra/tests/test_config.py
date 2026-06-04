"""Tests for the two-tier OrchestraConfig schema.

Covers the shape documented in
``design/orchestra-shared-role-bindings-proposal.md``: a top-level
``roles`` table whose entries are full role bindings, plus a
``workflows`` table whose entries name a pattern and (optionally)
``role_overrides`` that replace individual binding keys, plus an
optional ``verbs`` table.

Resolution itself lives in ``orchestra.api._resolve_role_binding`` and
is exercised in ``tests/test_api.py``. These tests cover the loader
and validator: shape errors, default-config shape, override shape
checks, rejection of the legacy per-workflow ``roles`` form, and the
global-plus-project merge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orchestra.config import (
    CompoundRoleBinding,
    ConfigError,
    CriterionDecl,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
    _merge_configs,
    default_config,
    load_config,
    load_global_config,
)


def _write_config(project_dir: Path, body: dict) -> Path:
    cfg_dir = project_dir / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    path.write_text(json.dumps(body))
    return path


def _write_global_config(home: Path, body: dict) -> Path:
    cfg_dir = home / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    path.write_text(json.dumps(body))
    return path


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect ``~`` to a fresh tmp directory for every test in this
    module so the developer's real ``~/.orchestra/config.json`` is
    never consulted. Tests that want a global config write it
    explicitly via ``_write_global_config(isolated_home, body)``."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


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


def test_old_nested_roles_shape_rejected_with_migration_hint() -> None:
    """A legacy file that nests ``roles`` inside a workflow must fail
    to load with a clear hint pointing at the proposal. The
    workflow-level check still fires regardless of where the file
    lives (global, project, or merged)."""
    raw = {
        "workflows": {
            "code_edit": {
                "pattern": "single",
                "roles": {"editor": {"adapter": "claude_code_agent"}},
            }
        }
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    msg = str(excinfo.value)
    assert "code_edit" in msg
    assert "no longer supported" in msg
    assert "orchestra-shared-role-bindings-proposal.md" in msg


def test_per_workflow_roles_block_rejected() -> None:
    """The same nested-roles rejection fires even when a top-level
    ``roles`` table is also present, so partial migrations cannot
    leak through."""
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


def test_partial_config_with_only_workflows_loads() -> None:
    """A single config file may omit the top-level ``roles`` table.
    The merge step combines it with the other layer (or
    ``default_config``) at load time. Validation that a workflow's
    required role has a binding runs in the api against the merged
    config, not at parse time."""
    raw = {"workflows": {"code_edit": {"pattern": "single"}}}
    cfg = OrchestraConfig.from_dict(raw)
    assert cfg.roles == {}
    assert cfg.workflows["code_edit"].pattern == "single"


def test_partial_config_with_only_verbs_loads() -> None:
    """A global config may carry only verbs and rely on a project
    config for roles and workflows."""
    raw = {"verbs": {"ask": {"workflow": "ask_single"}}}
    cfg = OrchestraConfig.from_dict(raw)
    assert cfg.verbs["ask"].workflow == "ask_single"
    assert cfg.roles == {}
    assert cfg.workflows == {}


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


# --------------------------------------------------------------------
# Global + project merge
# --------------------------------------------------------------------


def test_load_config_returns_default_when_neither_file_present(
    tmp_path: Path,
) -> None:
    """No global, no project: fall back to ``default_config``."""
    cfg = load_config(tmp_path)
    assert cfg.roles == default_config().roles
    assert cfg.workflows == default_config().workflows
    assert cfg.verbs == {}


def test_load_config_returns_default_with_no_project_dir() -> None:
    """``load_config()`` with no project_dir and no global file falls
    back to default_config too. Mirrors mcloop's behavior when run in
    an unconfigured directory."""
    cfg = load_config()
    assert cfg.roles == default_config().roles


def test_load_config_global_only(isolated_home: Path) -> None:
    """A global config in ``~/.orchestra/config.json`` is returned
    as-is when no project_dir is given."""
    _write_global_config(
        isolated_home,
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_text",
                    "model": "opus",
                    "parameters": {},
                },
            },
            "verbs": {"ask": {"workflow": "ask_single"}},
        },
    )
    cfg = load_config()
    assert cfg.roles["editor"].model == "opus"
    assert cfg.verbs["ask"].workflow == "ask_single"


def test_load_config_project_only(tmp_path: Path) -> None:
    """A project config with no global config returns the project
    file as-is. The smoke-test repo's standalone usage pattern."""
    _write_config(
        tmp_path,
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_agent",
                    "model": "sonnet",
                    "tools": "default",
                    "parameters": {},
                },
            },
            "workflows": {"code_edit": {"pattern": "single"}},
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.roles["editor"].model == "sonnet"
    assert cfg.workflows["code_edit"].pattern == "single"


def test_load_config_merges_global_and_project_no_overlap(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Global defines roles and verbs; project defines workflows. The
    merge produces the union with nothing replaced."""
    _write_global_config(
        isolated_home,
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_text",
                    "model": "opus",
                    "parameters": {},
                },
            },
            "verbs": {"ask": {"workflow": "ask_single"}},
        },
    )
    _write_config(
        tmp_path,
        {
            "workflows": {"code_edit": {"pattern": "single"}},
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.roles["editor"].model == "opus"
    assert cfg.verbs["ask"].workflow == "ask_single"
    assert cfg.workflows["code_edit"].pattern == "single"


def test_load_config_project_overrides_overlapping_role(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Where global and project both define a role, the project wins
    in full. Other global roles are inherited."""
    _write_global_config(
        isolated_home,
        {
            "roles": {
                "editor": {"adapter": "claude_code_text", "model": "opus"},
                "drafter": {"adapter": "claude_code_text", "model": "kimi-k2.6"},
            },
        },
    )
    _write_config(
        tmp_path,
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_agent",
                    "model": "deepseek-v4-pro",
                    "tools": "default",
                },
            },
        },
    )
    cfg = load_config(tmp_path)
    # The project's editor wins in full: adapter and model both reflect
    # the project's values, not the global's.
    assert cfg.roles["editor"].adapter == "claude_code_agent"
    assert cfg.roles["editor"].model == "deepseek-v4-pro"
    assert cfg.roles["editor"].tools == "default"
    # Other global roles are inherited unchanged.
    assert cfg.roles["drafter"].model == "kimi-k2.6"


def test_load_config_project_overrides_overlapping_verb(
    isolated_home: Path, tmp_path: Path
) -> None:
    _write_global_config(
        isolated_home,
        {
            "verbs": {
                "ask": {"workflow": "ask_single"},
                "council": {"workflow": "ask_propose_critique_synthesize"},
            },
        },
    )
    _write_config(
        tmp_path,
        {"verbs": {"ask": {"workflow": "ask_draft_then_adjudicate"}}},
    )
    cfg = load_config(tmp_path)
    assert cfg.verbs["ask"].workflow == "ask_draft_then_adjudicate"
    assert cfg.verbs["council"].workflow == "ask_propose_critique_synthesize"


def test_load_config_project_overrides_overlapping_workflow(
    isolated_home: Path, tmp_path: Path
) -> None:
    _write_global_config(
        isolated_home,
        {
            "workflows": {
                "code_edit": {"pattern": "single"},
                "ask_single": {"pattern": "ask_single"},
            },
        },
    )
    _write_config(
        tmp_path,
        {"workflows": {"code_edit": {"pattern": "draft_then_adjudicate"}}},
    )
    cfg = load_config(tmp_path)
    assert cfg.workflows["code_edit"].pattern == "draft_then_adjudicate"
    assert cfg.workflows["ask_single"].pattern == "ask_single"


def test_merge_does_not_nest_into_role_parameters() -> None:
    """An overlay role replaces the entire RoleBinding, including
    parameters and tools. No nested-key merging."""
    base = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_text",
                model="opus",
                parameters={"temperature": 0.0, "top_p": 1.0},
            ),
        },
    )
    overlay = OrchestraConfig(
        roles={
            "editor": RoleBinding(
                adapter="claude_code_text",
                model="opus",
                parameters={"top_p": 0.5},
            ),
        },
    )
    merged = _merge_configs(base, overlay)
    # The base's "temperature" key is gone; the overlay parameters
    # replaced the dict in full.
    assert merged.roles["editor"].parameters == {"top_p": 0.5}


def test_merge_inherits_when_overlay_section_absent() -> None:
    """An overlay with no roles inherits all base roles unchanged."""
    from orchestra.config import VerbBinding

    base = OrchestraConfig(
        roles={"editor": RoleBinding(adapter="claude_code_text")},
        workflows={"code_edit": WorkflowConfig(pattern="single")},
    )
    overlay = OrchestraConfig(verbs={"ask": VerbBinding(workflow="ask_single")})
    merged = _merge_configs(base, overlay)
    assert "editor" in merged.roles
    assert merged.workflows["code_edit"].pattern == "single"
    assert merged.verbs["ask"].workflow == "ask_single"


def test_load_global_config_raises_when_missing(
    isolated_home: Path,
) -> None:
    """``load_global_config`` keeps its loud-failure behavior so the
    verb CLI can emit a setup hint instead of silently falling back
    to default_config."""
    with pytest.raises(ConfigError) as excinfo:
        load_global_config()
    msg = str(excinfo.value)
    assert "no config" in msg
    assert ".orchestra" in msg


def test_load_global_config_returns_global_when_present(
    isolated_home: Path,
) -> None:
    _write_global_config(
        isolated_home,
        {
            "verbs": {"ask": {"workflow": "ask_single"}},
            "roles": {
                "editor": {
                    "adapter": "claude_code_text",
                    "model": "opus",
                    "parameters": {},
                },
            },
        },
    )
    cfg = load_global_config()
    assert cfg.verbs["ask"].workflow == "ask_single"


# --------------------------------------------------------------------
# CompoundRoleBinding parsing (consumed by orchestra.run_role)
# --------------------------------------------------------------------


def test_compound_role_binding_parses_nested_form() -> None:
    cfg = OrchestraConfig.from_dict(
        {
            "role_bindings": {
                "design": {
                    "pattern": "design_loop",
                    "judge": {"adapter": "claude_code_text", "model": "opus"},
                    "reviewer": {"adapter": "codex_text", "model": "gpt-5"},
                    "max_rounds": 4,
                },
            },
        }
    )
    assert "design" in cfg.role_bindings
    rb = cfg.role_bindings["design"]
    assert rb.pattern == "design_loop"
    assert rb.max_rounds == 4
    assert set(rb.bindings) == {"judge", "reviewer"}
    assert rb.bindings["judge"].adapter == "claude_code_text"
    assert rb.bindings["judge"].model == "opus"
    assert rb.bindings["reviewer"].adapter == "codex_text"
    assert rb.bindings["reviewer"].model == "gpt-5"


def test_compound_role_binding_missing_pattern_errors() -> None:
    raw = {
        "role_bindings": {
            "design": {
                "judge": {"adapter": "claude_code_text"},
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    msg = str(excinfo.value)
    assert "design" in msg
    assert "pattern" in msg


def test_compound_role_binding_rejects_bool_max_rounds() -> None:
    raw = {
        "role_bindings": {
            "design": {
                "pattern": "design_loop",
                "max_rounds": True,
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "max_rounds" in str(excinfo.value)


def test_compound_role_binding_accepts_no_sub_bindings() -> None:
    """A compound binding with only a pattern (no sub-role bindings)
    is legal: the workflow may declare no roles, or callers may
    intentionally let workflow defaults supply the bindings."""
    cfg = OrchestraConfig.from_dict(
        {
            "role_bindings": {
                "design": {"pattern": "design_loop"},
            },
        }
    )
    rb = cfg.role_bindings["design"]
    assert rb.pattern == "design_loop"
    assert rb.bindings == {}
    assert rb.max_rounds is None


def test_compound_role_binding_merges_across_layers(tmp_path: Path) -> None:
    """Project-local role_bindings replace entries of the same key in
    the global config; entries the project does not redefine are
    inherited."""
    home_dir = Path(os.environ["HOME"])
    _write_global_config(
        home_dir,
        {
            "role_bindings": {
                "design": {
                    "pattern": "design_loop",
                    "judge": {"adapter": "claude_code_text", "model": "opus"},
                },
                "other": {"pattern": "single"},
            },
        },
    )
    _write_config(
        tmp_path,
        {
            "role_bindings": {
                "design": {
                    "pattern": "design_loop",
                    "judge": {"adapter": "codex_text", "model": "gpt-5"},
                    "max_rounds": 2,
                },
            },
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.role_bindings["design"].bindings["judge"].adapter == "codex_text"
    assert cfg.role_bindings["design"].max_rounds == 2
    assert cfg.role_bindings["other"].pattern == "single"


def test_compound_role_binding_from_dict_used_directly() -> None:
    rb = CompoundRoleBinding.from_dict(
        "design",
        {
            "pattern": "design_loop",
            "judge": {"adapter": "claude_code_text"},
            "reviewer": {"adapter": "codex_text"},
            "max_rounds": 5,
            "future_knob": "ignored-but-preserved",
        },
    )
    assert rb.pattern == "design_loop"
    assert rb.max_rounds == 5
    assert rb.extra == {"future_knob": "ignored-but-preserved"}


def test_compound_role_binding_with_criteria_round_trips() -> None:
    """A compound binding may carry its own acceptance criteria, in the
    same shape the top-level ``criteria`` parser accepts. The parsed
    criteria are not swept into ``extra``."""
    rb = CompoundRoleBinding.from_dict(
        "design",
        {
            "pattern": "design_loop",
            "judge": {"adapter": "claude_code_text"},
            "criteria": [
                {"id": "exact_12_words", "description": "d", "required": True},
                {"id": "ends_question", "description": "d", "required": False},
            ],
        },
    )
    assert rb.criteria == (
        CriterionDecl(id="exact_12_words", description="d", required=True),
        CriterionDecl(id="ends_question", description="d", required=False),
    )
    assert "criteria" not in rb.extra


def test_compound_role_binding_without_criteria_is_empty() -> None:
    """A compound binding that omits ``criteria`` parses and carries an
    empty criteria tuple, leaving existing bindings unchanged."""
    rb = CompoundRoleBinding.from_dict(
        "design",
        {"pattern": "design_loop"},
    )
    assert rb.criteria == ()
    assert "criteria" not in rb.extra


def test_compound_role_binding_rejects_duplicate_criterion_ids() -> None:
    raw = {
        "role_bindings": {
            "design": {
                "pattern": "design_loop",
                "criteria": [
                    {"id": "dup", "description": "d"},
                    {"id": "dup", "description": "d"},
                ],
            },
        },
    }
    with pytest.raises(ConfigError) as excinfo:
        OrchestraConfig.from_dict(raw)
    assert "dup" in str(excinfo.value)
