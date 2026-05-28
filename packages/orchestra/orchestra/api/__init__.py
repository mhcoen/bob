"""Backward-compatible re-export shim for ``orchestra.api``.

Implementation moved to focused submodules: ``_common``, ``registry``,
``bindings``, ``validators``, ``transcript``, ``dispatch``. Every name
previously importable from ``orchestra.api`` continues to work via this
shim. New code should import from the focused submodules directly.
"""

from __future__ import annotations

from orchestra.api._common import (  # noqa: F401
    ArtifactView,
    ErrorRecord,
    FINAL_PROMPT_INPUT,
    IterativeDesignResult,
    Turn,
    WorkflowApiError,
    WorkflowRunResult,
)
from orchestra.api.registry import (  # noqa: F401
    _ADAPTER_CLASSES,
    _ADAPTER_TO_KIND,
    _ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA,
    _PARALLEL_THINKING_FINISH_PANEL_INPUT_SCHEMA,
    _PROVIDER_CONFIGS,
    _PerRoleDispatcher,
    _apply_instruction_templates,
    _build_registry,
    _build_role_adapter,
    _initialize_store,
    _pre_load_registry,
    _register_builtin_transforms,
    _resolve_template,
)
from orchestra.api.bindings import (  # noqa: F401
    _actor_identity,
    _adapter_workspace_mutation,
    _resolve_progress_callback,
    _resolve_role_binding,
    _resolve_workflow_role_bindings,
    _wrap_progress_callback,
)
from orchestra.api.validators import (  # noqa: F401
    _WORKFLOW_RULES,
    _apply_workflow_specific_rules,
    _validate_council_four,
    _validate_inputs,
    _validate_prji,
    _validate_role_bindings,
)
from orchestra.api.transcript import (  # noqa: F401
    _IncrementalTranscriptWriter,
    _build_transcript,
    _count_judge_rounds,
    _derive_termination,
    _select_final_artifact,
    _write_transcript_jsonl,
)
from orchestra.api.dispatch import (  # noqa: F401
    _CODE_EDIT_WORKFLOW_NAMES,
    _ERROR_OUTCOMES,
    _build_summary,
    _gather_artifacts,
    _maybe_inject_final_prompt,
    _resolve_compound_model_identifiers,
    _safe_options,
    _validate_design_distinct_actors,
    run_role,
    run_verb,
    run_workflow,
)

__all__ = [
    "ArtifactView",
    "ErrorRecord",
    "FINAL_PROMPT_INPUT",
    "IterativeDesignResult",
    "Turn",
    "WorkflowApiError",
    "WorkflowRunResult",
    "_ADAPTER_CLASSES",
    "_ADAPTER_TO_KIND",
    "_ASK_ANONYMOUS_REVIEWERS_ANONYMIZE_INPUT_SCHEMA",
    "_CODE_EDIT_WORKFLOW_NAMES",
    "_ERROR_OUTCOMES",
    "_IncrementalTranscriptWriter",
    "_PARALLEL_THINKING_FINISH_PANEL_INPUT_SCHEMA",
    "_PROVIDER_CONFIGS",
    "_PerRoleDispatcher",
    "_WORKFLOW_RULES",
    "_actor_identity",
    "_adapter_workspace_mutation",
    "_apply_instruction_templates",
    "_apply_workflow_specific_rules",
    "_build_registry",
    "_build_role_adapter",
    "_build_summary",
    "_build_transcript",
    "_count_judge_rounds",
    "_derive_termination",
    "_gather_artifacts",
    "_initialize_store",
    "_maybe_inject_final_prompt",
    "_pre_load_registry",
    "_register_builtin_transforms",
    "_resolve_compound_model_identifiers",
    "_resolve_progress_callback",
    "_resolve_role_binding",
    "_resolve_template",
    "_resolve_workflow_role_bindings",
    "_safe_options",
    "_select_final_artifact",
    "_validate_council_four",
    "_validate_design_distinct_actors",
    "_validate_inputs",
    "_validate_prji",
    "_validate_role_bindings",
    "_wrap_progress_callback",
    "_write_transcript_jsonl",
    "run_role",
    "run_verb",
    "run_workflow",
]
