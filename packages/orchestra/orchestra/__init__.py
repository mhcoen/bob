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
    load_config,
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
    "load_config",
    "run_workflow",
]
