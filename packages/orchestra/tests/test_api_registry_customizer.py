"""Tests for ``run_workflow``'s ``registry_customizer`` hook (T-000004).

A consumer (e.g. Duplo's plan-authoring loop) supplies a callback that
registers a caller-owned ``actor transform``. The callback must run on
BOTH the pre-load registry (so the loader's transform-record validator
sees the registration) and the runtime registry (so the executor can
resolve the transform at run time). Orchestra exposes the callback and
imports nothing from the consumer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestra.api import run_workflow
from orchestra.config import OrchestraConfig, WorkflowConfig
from orchestra.errors import ValidationError
from orchestra.registry.registry import ProfileRegistry
from orchestra.transforms import TransformContext

# A workflow whose only state is a caller-owned transform. It reads a
# seeded text artifact and writes a marker, so the run needs no model
# or agent adapter (and therefore never reaches an LLM).
_WORKFLOW_SRC = """spec 0.1

workflow custom_transform_demo

  max_total_steps 5

  artifact seed text
    initial "seed-value"
  artifact marker text

  state apply
    actor transform plan_body_check
    reads seed
    writes marker text
    on complete => done
    on error => stop
"""


def _plan_body_check(inputs: dict[str, Any], ctx: TransformContext) -> dict[str, Any]:
    """Caller-owned transform: trivially derive a marker from the seed."""
    return {"marker": f"checked:{inputs['seed']}"}


def _write_workflow(project_dir: Path) -> None:
    wf_dir = project_dir / ".orchestra" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "custom_transform_demo.orc").write_text(_WORKFLOW_SRC, encoding="utf-8")


def _config() -> OrchestraConfig:
    return OrchestraConfig(
        roles={},
        workflows={"custom_transform_demo": WorkflowConfig(pattern="custom_transform_demo")},
        verbs={},
        role_bindings={},
    )


def test_customizer_lets_workflow_load_and_run(tmp_path: Path) -> None:
    """With the customizer, the caller's transform is registered on
    both registries: the workflow loads and the transform runs."""
    _write_workflow(tmp_path)
    seen: list[int] = []

    def customizer(reg: ProfileRegistry) -> None:
        seen.append(id(reg))
        if "plan_body_check" not in reg.transforms:
            reg.register_transform(
                "plan_body_check",
                _plan_body_check,
                input_schema={"seed": str},
                output_schema={"marker": str},
            )

    result = run_workflow(
        "custom_transform_demo",
        {},
        _config(),
        project_dir=tmp_path,
        data_root=tmp_path / "runs",
        quiet=True,
        registry_customizer=customizer,
    )

    assert result.terminal == "done"
    # Invoked once per registry (pre-load and runtime), on two distinct
    # ProfileRegistry instances.
    assert len(seen) == 2
    assert len(set(seen)) == 2
    # The runtime registry saw the transform: it ran and wrote marker.
    marker = result.artifacts.get("marker")
    assert marker is not None
    assert marker.value == "checked:seed-value"


def test_without_customizer_workflow_fails_to_load(tmp_path: Path) -> None:
    """Without the customizer the transform is unregistered, so the
    same workflow fails to load exactly as it does today."""
    _write_workflow(tmp_path)

    with pytest.raises(ValidationError) as excinfo:
        run_workflow(
            "custom_transform_demo",
            {},
            _config(),
            project_dir=tmp_path,
            data_root=tmp_path / "runs",
            quiet=True,
        )
    assert "plan_body_check" in str(excinfo.value)
