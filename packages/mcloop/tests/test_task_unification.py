"""Regression coverage for the unified checklist/shim Task dataclass."""

from __future__ import annotations

from mcloop import _planfile_compat as shim
from mcloop.checklist import Task


def test_legacy_shape_task_gets_structured_fields_with_safe_defaults() -> None:
    task = Task(
        "Do work",
        False,
        False,
        0,
        0,
        "Stage 1: Test",
        [],
        [],
        "",
    )

    assert task.task_id is None
    assert task.flag_tags == ()
    assert task.action_tag is None


def test_shim_classifiers_accept_legacy_shape_task_without_attribute_error() -> None:
    task = Task(
        "[USER] legacy text is unstructured here",
        False,
        False,
        0,
        0,
        "Stage 1: Test",
        [],
        [],
        "",
    )

    for classifier in (shim.is_user_task, shim.is_auto_task, shim.is_batch_task):
        try:
            result = classifier(task)
        except AttributeError as exc:  # pragma: no cover - assertion path
            raise AssertionError(f"{classifier.__name__} raised AttributeError") from exc
        assert isinstance(result, bool)


def test_shim_classifiers_still_use_structured_fields() -> None:
    task = Task(
        "verify manually",
        False,
        False,
        0,
        0,
        "Stage 1: Test",
        [],
        [],
        "",
        flag_tags=("USER",),
    )

    assert shim.is_user_task(task)
