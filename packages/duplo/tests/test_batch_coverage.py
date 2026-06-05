"""Tests for :mod:`duplo.batch_coverage`.

The transform co-locates a covering test task inside any ``[BATCH]`` task
whose subtasks create new executable ``.py`` modules without a sibling
test that already exercises them, so the batch is self-contained for
mcloop's coverage gate. These tests pin both the unit transform and its
integration through :func:`duplo.council.typed_plan_from_synthesizer_text`.
"""

from __future__ import annotations

import re

from bob_tools.planfile import Plan, Task, parse_plan, render_plan

from duplo.batch_coverage import ensure_batch_test_coverage
from duplo.council import typed_plan_from_synthesizer_text


def _phase_body(tasks_md: str) -> str:
    return f"# Proj\n\n## Phase phase_001: Core\n\n{tasks_md}\n"


# --- Independent invariant checker (do NOT import batch_coverage internals) ---
#
# The functions below re-derive "which modules a batch creates" and "which of
# those a sibling test exercises" from scratch, so the regression assertion is
# a genuine independent verification of the generated plan rather than a tautology
# that re-runs the production detection logic against itself.

_PY_PATH = re.compile(r"[\w./-]+\.py\b")


def _stem(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if base.endswith(".py"):
        base = base[: -len(".py")]
    return base.lower()


def _is_test_file(path: str) -> bool:
    if "tests" in path.split("/")[:-1]:
        return True
    stem = _stem(path)
    return stem.startswith("test_") or stem.endswith("_test")


def _walk(task: Task):
    yield task
    for child in task.children:
        yield from _walk(child)


def _all_batches(plan: Plan):
    for phase in plan.phases:
        roots = list(phase.tasks)
        for sub in phase.subsections:
            roots.extend(sub.tasks)
        for root in roots:
            for task in _walk(root):
                if "BATCH" in task.flag_tags:
                    yield task


def _created_module_stems(batch: Task) -> set[str]:
    stems: set[str] = set()
    for node in _walk(batch):
        for path in _PY_PATH.findall(node.text):
            if not _is_test_file(path):
                stems.add(_stem(path))
    return stems


def _covered_module_stems(batch: Task, created: set[str]) -> set[str]:
    covered: set[str] = set()
    for node in _walk(batch):
        paths = _PY_PATH.findall(node.text)
        if not any(_is_test_file(p) for p in paths):
            continue
        lowered = node.text.lower()
        for path in paths:
            stem = _stem(path)
            if stem.startswith("test_"):
                covered.add(stem[len("test_") :])
            elif stem.endswith("_test"):
                covered.add(stem[: -len("_test")])
            else:
                covered.add(stem)
        for stem in created:
            if re.search(rf"\b{re.escape(stem)}\b", lowered):
                covered.add(stem)
    return covered & created


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
        "- [ ] Wire entry point [accept: command-exit: true]\n"
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


def test_every_module_batch_in_generated_plan_has_a_sibling_covering_test():
    """Regression contract for T-000002 (whole-plan invariant).

    For a plan produced by the generation path, EVERY [BATCH] task that
    creates new non-test .py modules must contain -- as a sibling task in
    the same batch -- a test task that targets those modules. No
    module-creation batch may have its covering test deferred to a later
    phase. This walks every batch (phase-level, nested, and inside ###
    subsections) and asserts the invariant for all of them, using an
    independent checker rather than the production detection logic.

    The body intentionally mixes batch states: an uncovered batch, a
    fully self-covered batch, a partially covered batch, a batch that
    creates no modules, a plain (non-batch) task, and a batch inside a
    subsection -- so the invariant is exercised across the whole shape a
    real synthesizer plan can take.
    """
    body = (
        "# Proj\n\n## Phase phase_001: Core\n\n"
        '- [ ] [BATCH] Build scanner [feat: "Scanner"]\n'
        "  - [ ] Create scanner.py with scan()\n"
        "  - [ ] Create orchestrator.py with run()\n"
        '- [ ] [BATCH] Build parser [feat: "Parser"]\n'
        "  - [ ] Create parser.py with parse()\n"
        "  - [ ] Add tests/test_parser.py exercising parser.py\n"
        '- [ ] [BATCH] Build widget [feat: "Widget"]\n'
        "  - [ ] Create widget.py with render()\n"
        "  - [ ] Create panel.py with draw()\n"
        "  - [ ] Add tests/test_widget.py exercising widget.py\n"
        '- [ ] [BATCH] Configure project [feat: "Config"]\n'
        "  - [ ] Add an entry to pyproject.toml\n"
        "- [ ] Wire entry point [accept: command-exit: true]\n\n"
        "### Extras\n\n"
        '- [ ] [BATCH] Build loader [feat: "Loader"]\n'
        "  - [ ] Create loader.py with load()\n"
    )
    plan = typed_plan_from_synthesizer_text(body, required_phase_id="phase_001")

    batches = list(_all_batches(plan))
    # Sanity: the fixture really produced multiple batches and at least
    # some of them create modules, so the assertion below is not vacuous.
    assert len(batches) >= 5
    module_batches = [b for b in batches if _created_module_stems(b)]
    assert len(module_batches) >= 4

    for batch in module_batches:
        created = _created_module_stems(batch)
        covered = _covered_module_stems(batch, created)
        uncovered = created - covered
        assert not uncovered, (
            f"batch {batch.text!r} creates {sorted(created)} but no sibling "
            f"test in the same batch exercises {sorted(uncovered)}"
        )
