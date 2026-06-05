"""The duplo-owned ``validate_plan_body`` Orchestra transform.

The ``plan_author`` workflow (``duplo/workflows/plan_author.orc``) ends
in a ``validate`` state whose actor is ``actor transform
validate_plan_body``. That transform is owned by duplo, not Orchestra:
Orchestra exposes a caller-supplied transform-registration hook
(``run_role``/``run_workflow``'s ``registry_customizer`` callback, the
phase_001 extension point B) and duplo supplies a function that registers
this transform. Orchestra never imports duplo or ``bob_tools``.

The transform runs the candidate plan body through
:func:`duplo.council.typed_plan_from_synthesizer_text`, which parses,
rebuilds the plan in construction mode, migrates ids, runs
``validate_plan(constructed=True)`` and ``assert_mcloop_canonical``, and
checks the required phase id. On success the transform returns
``validation_ok=true``; on any ``PlanSyntaxError``/``PlanValidationError``
it returns ``validation_ok=false`` with the joined error text as
``validation_feedback``. The transform never raises for a merely-invalid
body -- the validation gate must always produce a gate result so the
workflow can route on it.

``required_phase_id`` is owned by the runtime, not the model, and is not
a workflow artifact (see NOTES.md [9.1] [T-000786]). The validate state
reads only the ``proposal`` artifact; ``required_phase_id`` reaches the
transform by being bound into the closure when duplo registers the
transform via the registration callback.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bob_tools.planfile import PlanSyntaxError, PlanValidationError

from duplo.acceptance import AcceptanceAuthoringError
from duplo.council import typed_plan_from_synthesizer_text

TRANSFORM_NAME = "validate_plan_body"

# The transform's workflow-level input/output shape. ``validation_ok`` is
# declared ``bool`` here (Orchestra maps it to the ``json`` artifact type;
# there is no ``bool`` artifact type -- see NOTES.md [9.1] [T-000786]).
_INPUT_SCHEMA: dict[str, Any] = {"proposal": str}
_OUTPUT_SCHEMA: dict[str, Any] = {"validation_ok": bool, "validation_feedback": str}


def make_validate_plan_body(required_phase_id: str) -> Callable[..., dict[str, Any]]:
    """Build the ``validate_plan_body`` transform bound to a phase id.

    The returned callable matches Orchestra's transform contract
    ``(inputs, ctx) -> dict``. It reads ``inputs["proposal"]`` (the
    candidate plan body) and validates it against ``required_phase_id``
    via :func:`typed_plan_from_synthesizer_text`. It returns a gate
    result and never raises for an invalid body.
    """

    def validate_plan_body(inputs: dict[str, Any], ctx: Any) -> dict[str, Any]:
        body = inputs["proposal"]
        try:
            typed_plan_from_synthesizer_text(body, required_phase_id=required_phase_id)
        except (AcceptanceAuthoringError, PlanSyntaxError, PlanValidationError) as exc:
            return {"validation_ok": False, "validation_feedback": str(exc)}
        return {"validation_ok": True, "validation_feedback": ""}

    return validate_plan_body


def register_validate_plan_body(required_phase_id: str) -> Callable[[Any], None]:
    """Build the ``registry_customizer`` callback duplo hands to Orchestra.

    Orchestra invokes the returned callback on both the pre-load and the
    runtime registry, so it must be safe to call more than once. Each
    invocation registers the ``validate_plan_body`` transform (bound to
    ``required_phase_id``) if it is not already present on that registry;
    ``register_transform`` raises on a duplicate name, so the guard keeps
    the callback idempotent per registry.
    """
    transform = make_validate_plan_body(required_phase_id)

    def customizer(registry: Any) -> None:
        if TRANSFORM_NAME in registry.transforms:
            return
        registry.register_transform(
            TRANSFORM_NAME,
            transform,
            input_schema=dict(_INPUT_SCHEMA),
            output_schema=dict(_OUTPUT_SCHEMA),
        )

    return customizer
