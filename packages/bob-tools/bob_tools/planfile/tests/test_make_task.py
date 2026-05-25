"""Tests for make_task semantic field-stability construction."""

from __future__ import annotations

import pytest

from bob_tools.planfile import make_task as exported_make_task
from bob_tools.planfile.model import PlanValidationError, RuledOut, Task, TaskStatus
from bob_tools.planfile.operations import make_task


def _raw_task(
    text: str,
    *,
    task_id: str | None = None,
    children: tuple[Task, ...] = (),
    annotations: tuple[tuple[str, str], ...] = (),
    deps: tuple[str, ...] = (),
    ruled_out: tuple[RuledOut, ...] = (),
    action_tag: tuple[str, str] | None = None,
    flag_tags: tuple[str, ...] = (),
) -> Task:
    return Task(
        task_id=task_id,
        text=text,
        status=TaskStatus.TODO,
        flag_tags=flag_tags,
        action_tag=action_tag,
        annotations=annotations,
        deps=deps,
        children=children,
        ruled_out=ruled_out,
        indent_level=0,
        line_number=0,
        trailing_lines=(),
    )


def _error_text(exc_info: pytest.ExceptionInfo[PlanValidationError]) -> str:
    return "; ".join(exc_info.value.messages)


def test_package_root_exports_make_task() -> None:
    assert exported_make_task is make_task


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"text": "Document literal [fix: injected]"}, "text"),
        (
            {"text": "x", "annotations": (("feat", "real] [fix: injected"),)},
            "annotations",
        ),
        (
            {"text": "x", "action_tag": ("run_cli", "echo ok [fix: injected]")},
            "action_tag",
        ),
    ],
)
def test_make_task_rejects_d1_scalar_leaks(
    kwargs: dict[str, object], field: str
) -> None:
    with pytest.raises(PlanValidationError) as exc_info:
        make_task(**kwargs)  # type: ignore[arg-type]

    assert field in _error_text(exc_info)
    assert "failed to round-trip" in _error_text(exc_info)


def test_make_task_rejects_child_text_that_parses_as_annotation() -> None:
    child = _raw_task("Child [fix: injected]")

    with pytest.raises(PlanValidationError) as exc_info:
        make_task("Parent", children=(child,))

    message = _error_text(exc_info)
    assert "children[0].text" in message
    assert "failed to round-trip" in message


@pytest.mark.parametrize(
    "text",
    [
        "Document [fix: not] literal",
        "Document literal[fix: not]",
        "Handle [USER] literal in docs",
        "Discuss @deps T-000002 in docs",
    ],
)
def test_make_task_accepts_non_colliding_prose(text: str) -> None:
    task = make_task(text)

    assert task.text == text
    assert task.task_id is None
    assert task.trailing_lines == ()


def test_make_task_accepts_annotation_values_with_nested_brackets() -> None:
    task = make_task("x", annotations=(("feat", "use [x:y] value"),))

    assert task.annotations == (("feat", "use [x:y] value"),)


def test_make_task_preserves_multi_annotation_order() -> None:
    task = make_task("x", annotations=(("feat", "A"), ("fix", "B")))

    assert task.annotations == (("feat", "A"), ("fix", "B"))


def test_make_task_accepts_nested_children_ruled_out_action_and_deps() -> None:
    grandchild = make_task("Grandchild [literal] ok")
    child = make_task("Child task", children=(grandchild,))
    task = make_task(
        "",
        flag_tags=("USER",),
        action_tag=("run_cli", "echo ok"),
        deps=("T-000002",),
        children=(child,),
        ruled_out=(RuledOut(text="- [ ] not a task", line_number=25),),
    )

    assert task.flag_tags == ("USER",)
    assert task.action_tag == ("run_cli", "echo ok")
    assert task.deps == ("T-000002",)
    assert task.children == (child,)
    assert task.ruled_out == (RuledOut(text="- [ ] not a task", line_number=25),)
    assert task.task_id is None


def test_make_task_preserves_none_ids_for_all_none_nested_children() -> None:
    grandchild = _raw_task("Grandchild")
    child = _raw_task("Child", children=(grandchild,))

    task = make_task("Parent", children=(child,))

    assert task.task_id is None
    assert task.children[0].task_id is None
    assert task.children[0].children[0].task_id is None


def test_make_task_sentinel_allocator_skips_explicit_ids() -> None:
    explicit_child = _raw_task("Explicit child", task_id="T-000002")
    none_child = _raw_task("None child")

    task = make_task("Parent", children=(explicit_child, none_child))

    assert task.task_id is None
    assert task.children[0].task_id == "T-000002"
    assert task.children[1].task_id is None


def test_make_task_accepts_explicit_root_id() -> None:
    task = make_task("Root with id", task_id="T-000042")

    assert task.task_id == "T-000042"


def test_make_task_rejects_newlines_before_rendering() -> None:
    with pytest.raises(PlanValidationError) as exc_info:
        make_task("first\nsecond")

    assert "text" in _error_text(exc_info)
    assert "newline" in _error_text(exc_info)


def test_make_task_rejects_invalid_tags_and_ids() -> None:
    with pytest.raises(PlanValidationError) as exc_info:
        make_task(
            "x",
            flag_tags=("ADMIN",),
            action_tag=("run-cli", "echo ok"),
            deps=("bad",),
            task_id="bad",
        )

    message = _error_text(exc_info)
    assert "flag_tags[0]" in message
    assert "action_tag.action" in message
    assert "deps[0]" in message
    assert "task_id" in message
