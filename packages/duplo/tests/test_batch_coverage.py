"""Tests for :mod:`duplo.batch_coverage`.

The transform co-locates a covering test task inside any ``[BATCH]`` task
whose subtasks create new executable ``.py`` modules without a sibling
test that already exercises them, so the batch is self-contained for
mcloop's coverage gate. These tests pin both the unit transform and its
integration through :func:`duplo.council.typed_plan_from_synthesizer_text`.
"""

from __future__ import annotations

from bob_tools.planfile import parse_plan, render_plan

from duplo.batch_coverage import ensure_batch_test_coverage
from duplo.council import typed_plan_from_synthesizer_text


def _phase_body(tasks_md: str) -> str:
    return f"# Proj\n\n## Phase phase_001: Core\n\n{tasks_md}\n"


def _batch_children_texts(plan, phase_index=0, task_index=0):
    task = plan.phases[phase_index].tasks[task_index]
    return [child.text for child in task.children]


def test_module_batch_without_test_gets_covering_sibling():
    """A batch creating modules but lacking a test gains a covering test child."""
    body = _phase_body(
        '- [ ] [BATCH] Build scanner [feat: "Scanner"]\n'
        "  - [ ] Create scanner.py with scan()\n"
        "  - [ ] Create orchestrator.py with run()\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    children = _batch_children_texts(result)
    assert len(children) == 3
    # The two creation subtasks are preserved unchanged.
    assert children[0] == "Create scanner.py with scan()"
    assert children[1] == "Create orchestrator.py with run()"
    # The appended sibling is a test task targeting both created modules.
    added = children[2]
    assert "tests/" in added
    assert "scanner.py" in added
    assert "orchestrator.py" in added


def test_batch_with_existing_covering_test_is_untouched():
    """A batch whose test already exercises its modules is not modified."""
    body = _phase_body(
        '- [ ] [BATCH] Build widget [feat: "Widget"]\n'
        "  - [ ] Create widget.py with render()\n"
        "  - [ ] Add tests/test_widget.py exercising widget.py\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    assert result is plan
    assert len(_batch_children_texts(result)) == 2


def test_non_batch_module_creation_is_not_touched():
    """Module-creation outside a [BATCH] parent is out of scope."""
    body = _phase_body(
        "- [ ] Create scanner.py with scan()\n- [ ] Create orchestrator.py with run()\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    assert result is plan


def test_batch_without_module_creation_is_not_touched():
    """A batch whose subtasks create no .py modules needs no covering test."""
    body = _phase_body(
        '- [ ] [BATCH] Configure project [feat: "Config"]\n'
        "  - [ ] Add an entry to pyproject.toml\n"
        "  - [ ] Document the build in README.md\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    assert result is plan


def test_partial_coverage_adds_test_for_uncovered_module():
    """When only some created modules are tested, the rest are covered."""
    body = _phase_body(
        '- [ ] [BATCH] Build feature [feat: "Feature"]\n'
        "  - [ ] Create alpha.py with a()\n"
        "  - [ ] Create beta.py with b()\n"
        "  - [ ] Add tests/test_alpha.py exercising alpha.py\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    children = _batch_children_texts(result)
    assert len(children) == 4
    added = children[3]
    assert "beta.py" in added
    # alpha is already covered, so it is not re-listed in the new test task.
    assert "alpha.py" not in added


def test_transform_is_idempotent():
    """Running the transform on its own output adds nothing further."""
    body = _phase_body(
        '- [ ] [BATCH] Build scanner [feat: "Scanner"]\n  - [ ] Create scanner.py with scan()\n'
    )
    plan = parse_plan(body)
    once = ensure_batch_test_coverage(plan)
    twice = ensure_batch_test_coverage(once)

    assert len(_batch_children_texts(once)) == 2
    assert twice is once


def test_subsection_batches_are_covered():
    """A [BATCH] inside a ### subsection is also repaired."""
    body = (
        "# Proj\n\n## Phase phase_001: Core\n\n"
        "### Group\n\n"
        '- [ ] [BATCH] Build parser [feat: "Parser"]\n'
        "  - [ ] Create parser.py with parse()\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    sub_task = result.phases[0].subsections[0].tasks[0]
    assert len(sub_task.children) == 2
    assert "parser.py" in sub_task.children[1].text


def test_test_files_are_not_counted_as_created_modules():
    """A batch that only adds test files needs no extra covering test."""
    body = _phase_body(
        '- [ ] [BATCH] Add regression tests [feat: "Tests"]\n'
        "  - [ ] Add tests/test_alpha.py covering alpha.py\n"
        "  - [ ] Add tests/test_beta.py covering beta.py\n"
    )
    plan = parse_plan(body)
    result = ensure_batch_test_coverage(plan)

    # alpha.py / beta.py are referenced as test targets and already
    # exercised by their named test files, so nothing is appended.
    assert result is plan


def test_integration_through_typed_plan_from_synthesizer_text():
    """The covering test lands in the validated, id-stamped typed Plan.

    This is the regression contract for T-000002: every batch that
    creates new .py modules contains a sibling test task targeting them.
    """
    body = _phase_body(
        '- [ ] [BATCH] Build scanner [feat: "Scanner"]\n'
        "  - [ ] Create scanner.py with scan()\n"
        "  - [ ] Create orchestrator.py with run()\n"
        "- [ ] Wire entry point\n"
    )
    plan = typed_plan_from_synthesizer_text(body, required_phase_id="phase_001")

    batch = plan.phases[0].tasks[0]
    assert "BATCH" in batch.flag_tags
    assert len(batch.children) == 3
    # Every created module is exercised by a sibling test task in the
    # same batch.
    rendered = render_plan(plan)
    assert "test_" in rendered
    test_child = batch.children[2]
    assert "scanner.py" in test_child.text
    assert "orchestrator.py" in test_child.text
    # The appended task received a stable id alongside the rest.
    assert test_child.task_id is not None
