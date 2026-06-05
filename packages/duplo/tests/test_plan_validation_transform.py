"""Unit tests for the duplo-owned ``validate_plan_body`` transform.

The transform (``duplo/plan_validation_transform.py``) is the validation
gate at the end of the ``plan_author`` workflow. It is owned by duplo and
supplied to Orchestra through the caller-supplied transform-registration
hook (``run_role``/``run_workflow``'s ``registry_customizer``); Orchestra
never imports duplo or ``bob_tools``.

These tests pin the gate contract: a canonical body validates ``ok``; a
body with a wrong phase id, a malformed checklist, or a ``## Bugs``
section each return ``validation_ok=false`` with feedback; and the
transform NEVER raises for a merely-invalid body -- the gate must always
produce a routable result. No LLM calls are involved: validation is
static parsing.
"""

from __future__ import annotations

from orchestra.registry.registry import with_core

from duplo.plan_validation_transform import (
    TRANSFORM_NAME,
    make_validate_plan_body,
    register_validate_plan_body,
)

CANONICAL_BODY = (
    "## Phase phase_001: Bring up scaffold\n\n- [ ] do the thing [accept: command-exit: true]\n"
)


def _run(body: str, required_phase_id: str = "phase_001") -> dict:
    transform = make_validate_plan_body(required_phase_id)
    # The second positional arg is Orchestra's TransformContext; the
    # transform does not consult it, so a sentinel is fine here.
    return transform({"proposal": body}, ctx=None)


def test_canonical_body_validates_ok() -> None:
    result = _run(CANONICAL_BODY)
    assert result == {"validation_ok": True, "validation_feedback": ""}


def test_wrong_phase_id_returns_not_ok_with_feedback() -> None:
    # Body declares phase_001 but the runtime required phase_002.
    result = _run(CANONICAL_BODY, required_phase_id="phase_002")
    assert result["validation_ok"] is False
    assert "phase_002" in result["validation_feedback"]
    assert "not present" in result["validation_feedback"]


def test_malformed_checklist_returns_not_ok_with_feedback() -> None:
    # An unknown leading bracket tag on a checklist item is a malformed
    # task body that validate_plan rejects.
    body = "## Phase phase_001: x\n\n- [ ] [NOPE] do something with a bad tag\n"
    result = _run(body)
    assert result["validation_ok"] is False
    assert result["validation_feedback"]
    assert "[NOPE]" in result["validation_feedback"]


def test_bugs_section_returns_not_ok_with_feedback() -> None:
    # A canonical phase body must contain phases only; a ``## Bugs``
    # section is an mcloop convention the synthesizer may not emit.
    body = (
        "## Phase phase_001: x\n\n"
        "- [ ] do the thing [accept: command-exit: true]\n\n"
        "## Bugs\n\n"
        "- [ ] fix it [accept: command-exit: true]\n"
    )
    result = _run(body)
    assert result["validation_ok"] is False
    assert "## Bugs" in result["validation_feedback"]


def test_transform_never_raises_for_invalid_body() -> None:
    # Several flavors of invalid body: pure prose (no phases), a
    # duplicate phase id, and an empty string. None may raise; each must
    # return a not-ok gate result.
    for body in (
        "just prose, not a plan at all",
        "## Phase phase_001: A\n\n"
        "- [ ] a [accept: command-exit: true]\n\n"
        "## Phase phase_001: B\n\n"
        "- [ ] b [accept: command-exit: true]\n",
        "",
    ):
        result = _run(body)
        assert result["validation_ok"] is False
        assert isinstance(result["validation_feedback"], str)


def test_register_validate_plan_body_registers_on_registry() -> None:
    registry = with_core()
    customizer = register_validate_plan_body("phase_001")
    customizer(registry)
    assert TRANSFORM_NAME in registry.transforms


def test_registration_callback_is_idempotent_per_registry() -> None:
    # Orchestra invokes the callback on both the pre-load and the runtime
    # registry, so the same callable must be safe to call more than once
    # without raising RegistryConflict.
    customizer = register_validate_plan_body("phase_001")
    registry = with_core()
    customizer(registry)
    customizer(registry)
    assert TRANSFORM_NAME in registry.transforms


def test_registered_transform_validates_through_registry() -> None:
    # End-to-end: the registered transform's schemas accept the body and
    # the stored callable produces the gate result.
    registry = with_core()
    register_validate_plan_body("phase_001")(registry)
    transform = registry.transforms[TRANSFORM_NAME]
    assert transform.input_schema == {"proposal": str}
    assert transform.output_schema == {"validation_ok": bool, "validation_feedback": str}
    result = transform.callable({"proposal": CANONICAL_BODY}, None)
    assert result == {"validation_ok": True, "validation_feedback": ""}
