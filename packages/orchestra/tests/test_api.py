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

import json
from pathlib import Path

import pytest

from orchestra.api import (
    ErrorRecord,
    WorkflowApiError,
    _derive_termination,
    _pre_load_registry,
    _resolve_role_binding,
    _validate_role_bindings,
    run_role,
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


# --------------------------------------------------------------------
# _derive_termination: CONVERGED / CAPPED / ERROR classification from
# the run log (see T-000007 termination-resolution spec).
# --------------------------------------------------------------------


def _write_synthetic_log(
    path: Path,
    records: list[dict[str, object]],
) -> None:
    """Write JSONL records in LogReader-compatible format with
    contiguous sequence numbers starting at 0."""
    with open(path, "w", encoding="utf-8") as fh:
        for seq, body in enumerate(records):
            row = {
                "ts": "2026-05-26T00:00:00.000Z",
                "run_id": "test",
                "seq": seq,
                "state_id": None,
                "attempt": None,
            }
            row.update(body)
            fh.write(json.dumps(row, sort_keys=True))
            fh.write("\n")


def test_derive_termination_converged(tmp_path: Path) -> None:
    log_path = tmp_path / "log.jsonl"
    _write_synthetic_log(
        log_path,
        [
            {"event": "run_start"},
            {
                "event": "transition",
                "state_id": "judge",
                "attempt": 1,
                "outcome": "done",
                "target": "done",
            },
            {"event": "run_end", "terminal": "done"},
        ],
    )
    termination, error = _derive_termination(log_path)
    assert termination == "CONVERGED"
    assert error is None


def test_derive_termination_capped(tmp_path: Path) -> None:
    """A cap-hit transition routes the judge's ``iterate`` outcome to
    ``done`` without it being the judge's own done action; that is
    CAPPED rather than CONVERGED."""
    log_path = tmp_path / "log.jsonl"
    _write_synthetic_log(
        log_path,
        [
            {"event": "run_start"},
            {
                "event": "transition",
                "state_id": "judge",
                "attempt": 4,
                "outcome": "iterate",
                "target": "done",
            },
            {"event": "run_end", "terminal": "done"},
        ],
    )
    termination, error = _derive_termination(log_path)
    assert termination == "CAPPED"
    assert error is None


def test_derive_termination_error_on_stop(tmp_path: Path) -> None:
    log_path = tmp_path / "log.jsonl"
    _write_synthetic_log(
        log_path,
        [
            {"event": "run_start"},
            {
                "event": "state_exit",
                "state_id": "review",
                "attempt": 1,
                "status": "error",
                "outcome": "error",
                "error": {
                    "kind": "actor_failure",
                    "message": "boom",
                    "detail": {"phase": "invoke"},
                },
            },
            {
                "event": "transition",
                "state_id": "review",
                "attempt": 1,
                "outcome": "error",
                "target": "stop",
            },
            {"event": "run_end", "terminal": "stop"},
        ],
    )
    termination, error = _derive_termination(log_path)
    assert termination == "ERROR"
    assert error is not None
    assert error.kind == "actor_failure"
    assert error.message == "boom"
    assert error.state == "review"
    assert error.detail == {"phase": "invoke"}


def test_derive_termination_error_when_no_transition(tmp_path: Path) -> None:
    """A log with no transition records (e.g. crash during setup)
    classifies as ERROR with a runner_failure marker so callers see
    why they got no progress."""
    log_path = tmp_path / "log.jsonl"
    _write_synthetic_log(
        log_path,
        [
            {"event": "run_start"},
            {"event": "run_end", "terminal": "stop"},
        ],
    )
    termination, error = _derive_termination(log_path)
    assert termination == "ERROR"
    assert error is not None
    assert error.kind == "runner_failure"


def test_derive_termination_error_on_timeout_transition(tmp_path: Path) -> None:
    """``timeout`` is one of the failure outcomes that the workflow
    routes to stop, producing an ERROR result."""
    log_path = tmp_path / "log.jsonl"
    _write_synthetic_log(
        log_path,
        [
            {"event": "run_start"},
            {
                "event": "state_exit",
                "state_id": "judge",
                "attempt": 1,
                "status": "timeout",
                "outcome": "timeout",
                "error": {"kind": "timeout", "message": "ran too long"},
            },
            {
                "event": "transition",
                "state_id": "judge",
                "attempt": 1,
                "outcome": "timeout",
                "target": "stop",
            },
            {"event": "run_end", "terminal": "stop"},
        ],
    )
    termination, error = _derive_termination(log_path)
    assert termination == "ERROR"
    assert error is not None
    assert error.kind == "timeout"
    assert error.message == "ran too long"


# --------------------------------------------------------------------
# run_role pre-flight: unknown role raises WorkflowApiError with a
# diagnostic that lists what *was* configured.
# --------------------------------------------------------------------


@pytest.fixture
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


def test_run_role_unknown_role_raises(
    _isolated_home: Path,
    tmp_path: Path,
) -> None:
    """``run_role`` resolves the role against the merged config; an
    unknown role surfaces as ``WorkflowApiError`` so the duplo wrapper
    in T-000017 can wrap it into a typed exception, instead of being
    forwarded to ``run_workflow`` where the error would mislead."""
    cfg_dir = tmp_path / ".orchestra"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "role_bindings": {
                    "design": {
                        "pattern": "design_loop",
                        "judge": {"adapter": "claude_code_text"},
                        "reviewer": {"adapter": "codex_text"},
                    },
                },
            }
        )
    )
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("not_a_role", project_dir=tmp_path)
    msg = str(excinfo.value)
    assert "not_a_role" in msg
    assert "design" in msg  # lists what was configured


def test_run_role_error_record_dataclass_shape() -> None:
    """The api's ErrorRecord is the public surface; confirm the
    fields run_role consumers (and duplo's T-000017 wrapper) will
    pattern-match against stay stable."""
    err = ErrorRecord(
        kind="actor_failure",
        message="boom",
        state="judge",
        detail={"phase": "invoke"},
    )
    assert err.kind == "actor_failure"
    assert err.state == "judge"
    assert err.detail == {"phase": "invoke"}


# --------------------------------------------------------------------
# T-000012: max_rounds validation at workflow-start. The cap defaults
# to 4, comes from the compound binding when set, and can be overridden
# per call. run_role refuses to start when the resolved cap is not a
# positive int so the cap-hit transition can never fire before the
# first judge round completes.
# --------------------------------------------------------------------


def _write_design_config(tmp_path: Path, extra: dict[str, object] | None = None) -> None:
    cfg_dir = tmp_path / ".orchestra"
    cfg_dir.mkdir()
    body: dict[str, object] = {
        "pattern": "design_loop",
        "judge": {"adapter": "claude_code_text"},
        "reviewer": {"adapter": "codex_text"},
    }
    if extra:
        body.update(extra)
    (cfg_dir / "config.json").write_text(json.dumps({"role_bindings": {"design": body}}))


@pytest.mark.parametrize("bad_cap", [0, -1, -42])
def test_run_role_rejects_non_positive_max_rounds_override(
    _isolated_home: Path,
    tmp_path: Path,
    bad_cap: int,
) -> None:
    """A per-call ``max_rounds=N`` with N <= 0 fails before any
    workflow run begins."""
    _write_design_config(tmp_path)
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", max_rounds=bad_cap, project_dir=tmp_path)
    assert "max_rounds" in str(excinfo.value)


def test_run_role_rejects_non_positive_max_rounds_in_binding(
    _isolated_home: Path,
    tmp_path: Path,
) -> None:
    """A compound binding declaring ``max_rounds: 0`` is rejected at
    workflow start so a misconfigured project cannot silently produce
    a CAPPED termination before any round runs."""
    _write_design_config(tmp_path, extra={"max_rounds": 0})
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", project_dir=tmp_path)
    assert "max_rounds" in str(excinfo.value)


def test_run_role_rejects_non_int_max_rounds_override(
    _isolated_home: Path,
    tmp_path: Path,
) -> None:
    """A per-call ``max_rounds`` that is not an int (including bool,
    which is technically an int subclass) fails before workflow run."""
    _write_design_config(tmp_path)
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", max_rounds=True, project_dir=tmp_path)
    assert "max_rounds" in str(excinfo.value)
    with pytest.raises(WorkflowApiError) as excinfo:
        run_role("design", max_rounds="four", project_dir=tmp_path)  # type: ignore[arg-type]
    assert "max_rounds" in str(excinfo.value)
