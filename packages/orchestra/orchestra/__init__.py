"""Orchestra runner.

This package implements the runtime described in
``design/orchestra-runner.md``. See ``orchestra.spine`` for the IR types
that flow between components, and ``orchestra.cli`` for the
command-line entry point.

Library consumers (mcloop, Duplo, others) import ``run_workflow``
from this module to invoke a configured workflow by name. See
``orchestra.api`` for the contract and ``design/orchestra-mcloop-
integration-plan.md`` for the integration design.
"""

from orchestra.api import (
    ArtifactView,
    WorkflowApiError,
    WorkflowRunResult,
    run_workflow,
)
from orchestra.config import (
    ConfigError,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
    default_config,
    load_config,
)

# Eager submodule imports so attribute-style access (``orchestra.api.X``,
# ``orchestra.cli.Y``, etc.) resolves deterministically under
# ``--import-mode=importlib`` with pytest-xdist. Without these, patch
# sites racing across xdist workers can hit a partially-loaded
# package and raise AttributeError on otherwise-valid submodule access.
from . import (  # noqa: E402, F401
    adapters,
    calibration,
    cli,
    errors,
    executor,
    loader,
    log,
    payloads,
    progress,
    prompt_snapshot,
    prompts,
    registry,
    repl,
    resume,
    schema,
    spine,
    store,
    transforms,
    visibility,
)

__version__ = "0.0.1"

__all__ = [
    "ArtifactView",
    "ConfigError",
    "OrchestraConfig",
    "RoleBinding",
    "WorkflowApiError",
    "WorkflowConfig",
    "WorkflowRunResult",
    "default_config",
    "load_config",
    "run_workflow",
]
