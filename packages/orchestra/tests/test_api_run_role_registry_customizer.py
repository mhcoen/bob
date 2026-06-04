"""Tests for threading ``registry_customizer`` through ``run_role`` (T-000005).

``run_role`` forwards a caller-supplied ``registry_customizer`` to
``run_workflow`` so a role-dispatched workflow can register a
caller-owned ``actor transform`` on both the pre-load and runtime
registries, exactly as the direct ``run_workflow`` path does. Without
the customizer the transform is unregistered and the workflow fails to
load, unchanged from today.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import run_role
from orchestra.errors import ValidationError
from orchestra.registry.registry import ProfileRegistry
from orchestra.transforms import TransformContext

# A workflow whose only state is a caller-owned transform. It reads a
# seeded text artifact and writes a marker, so the run needs no model
# or agent adapter (and therefore never reaches an LLM).
_WORKFLOW_SRC = """spec 0.1

workflow custom_transform_role_demo

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
    (wf_dir / "custom_transform_role_demo.orc").write_text(_WORKFLOW_SRC, encoding="utf-8")


def _write_config(project_dir: Path) -> None:
    """Bind a role to the transform-only workflow. The workflow has no
    actor model/agent states, so the compound binding needs only a
    pattern and no leaf bindings."""
    cfg_dir = project_dir / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"role_bindings": {"checker": {"pattern": "custom_transform_role_demo"}}}),
        encoding="utf-8",
    )


@pytest.fixture
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


def test_run_role_customizer_registers_transform(
    _isolated_home: Path,
    tmp_path: Path,
) -> None:
    """With the customizer, ``run_role`` registers the caller's
    transform on both registries: the role-bound workflow loads, the
    transform runs, and the run terminates in ``done``."""
    _write_workflow(tmp_path)
    _write_config(tmp_path)
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

    result = run_role(
        "checker",
        project_dir=tmp_path,
        quiet=True,
        registry_customizer=customizer,
    )

    # The transform was registered on the runtime registry: the
    # workflow loaded and ran to a ``done`` terminal rather than
    # failing to load or erroring out. (The transform state's
    # ``on complete => done`` transition classifies as CAPPED, not
    # CONVERGED; what matters here is that it is not ERROR.)
    assert result.termination != "ERROR"
    # Invoked on three distinct ProfileRegistry instances: run_role's
    # own up-front introspection load (to decide max_rounds injection)
    # plus run_workflow's pre-load and runtime registries. Distinct ids
    # confirm the customizer reached every registry on the role path,
    # including the runtime registry the executor uses.
    assert len(seen) == 3
    assert len(set(seen)) == 3


def test_run_role_without_customizer_fails_to_load(
    _isolated_home: Path,
    tmp_path: Path,
) -> None:
    """Without the customizer the transform is unregistered, so the
    same role-bound workflow fails to load exactly as it does today."""
    _write_workflow(tmp_path)
    _write_config(tmp_path)

    with pytest.raises(ValidationError) as excinfo:
        run_role(
            "checker",
            project_dir=tmp_path,
            quiet=True,
        )
    assert "plan_body_check" in str(excinfo.value)
