"""Round-trip property tests for parse_plan and render_plan.

Two properties are asserted across every fixture in
``bob_tools/planfile/tests/fixtures/``:

* ``test_parse_render_parse_idempotent``: ``parse(render(parse(text)))``
  equals ``parse(text)`` modulo the fields that legitimately differ
  between iterations — line numbers (because the rendered text has a
  different line layout), task indent levels (because the renderer
  always emits canonical two-space indentation), and the
  ``Phase.phase_id_source`` tag (because the renderer migrates the
  legacy ``## Phase phase_NNN: ...`` header form to the canonical
  ``<!-- phase_id: ... -->`` comment form per design doc section 7.1).
  :func:`bob_tools.planfile.renderer.normalize_positions` collapses
  those three fields so the equality check is a faithful oracle for
  the underlying semantic round-trip.

* ``test_render_parse_render_stable``: ``render(parse(render(plan)))``
  equals ``render(plan)`` byte-for-byte. This is the canonical-form
  fixed-point property: once a plan is rendered, subsequent
  parse-then-render cycles produce the same text. No normalization is
  needed because both sides are already canonical text.

Fixtures are markdown files committed under ``fixtures/`` so a human
can read what each one exercises. The fixture loader collects every
``*.md`` file in that directory and parametrizes both tests, so adding
a new fixture automatically extends the coverage of both properties
without test-code changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bob_tools.planfile import Plan, Task, canonicalize, parse_plan, render_plan
from bob_tools.planfile.renderer import normalize_positions

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_paths() -> list[Path]:
    paths = sorted(FIXTURES_DIR.glob("*.md"))
    if not paths:
        pytest.fail(f"no fixtures found in {FIXTURES_DIR}")
    return paths


def _collect_task_ids_including_none(plan: Plan) -> list[str | None]:
    """Return every ``task_id`` in document order, preserving ``None`` entries.

    Unlike the non-None-filtering helper in ``test_generative.py``, this
    keeps the ID-less slots in the sequence so list equality is a faithful
    "no ID was assigned, no ID was removed" oracle.
    """
    ids: list[str | None] = []

    def _walk(tasks: tuple[Task, ...]) -> None:
        for task in tasks:
            ids.append(task.task_id)
            _walk(task.children)

    for phase in plan.phases:
        _walk(phase.tasks)
        for subsection in phase.subsections:
            _walk(subsection.tasks)
    if plan.bugs is not None:
        _walk(plan.bugs.tasks)
    return ids


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_parse_render_parse_idempotent(fixture_path: Path) -> None:
    text = fixture_path.read_text()
    first = parse_plan(text)
    rendered = render_plan(first)
    second = parse_plan(rendered)
    assert normalize_positions(first) == normalize_positions(second), (
        f"parse(render(parse({fixture_path.name}))) "
        f"did not equal parse({fixture_path.name})"
    )


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_render_parse_render_stable(fixture_path: Path) -> None:
    text = fixture_path.read_text()
    plan = parse_plan(text)
    first_render = render_plan(plan)
    second_render = render_plan(parse_plan(first_render))
    assert first_render == second_render, (
        f"render(parse(render(parse({fixture_path.name})))) "
        f"diverged from render(parse({fixture_path.name})) "
        f"— canonical form is not a fixed point"
    )


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_canonicalize_idempotent(fixture_path: Path) -> None:
    """``canonicalize(canonicalize(text)) == canonicalize(text)`` for every fixture.

    Canonical text is by definition the fixed point of ``parse∘render``,
    and ``canonicalize`` is just that composition, so applying it twice
    must equal applying it once. This is the user-facing restatement of
    ``test_render_parse_render_stable`` — same property, expressed
    against the public ``canonicalize`` surface.
    """
    text = fixture_path.read_text()
    once = canonicalize(text)
    twice = canonicalize(once)
    assert once == twice, (
        f"canonicalize(canonicalize({fixture_path.name})) "
        f"diverged from canonicalize({fixture_path.name})"
    )


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.name,
)
def test_canonicalize_does_not_assign_task_ids(fixture_path: Path) -> None:
    """``canonicalize`` does not migrate identities: tasks without IDs in
    the input have no IDs in the output, and tasks with IDs keep theirs.

    Identity assignment (giving an ID-less task a fresh ``T-NNNNNN``) is
    the responsibility of ``migrate`` per design doc section 3.2, not of
    ``canonicalize``. Comparing the full ``task_id`` sequence with
    ``None`` entries preserved catches both directions of drift: an ID
    appearing where there was none, or an existing ID being dropped.
    """
    text = fixture_path.read_text()
    before = _collect_task_ids_including_none(parse_plan(text))
    after = _collect_task_ids_including_none(parse_plan(canonicalize(text)))
    assert before == after, (
        f"canonicalize({fixture_path.name}) altered task identities: "
        f"before={before} after={after}"
    )


def test_trailing_prose_continuation_preserved() -> None:
    """Non-checkbox prose lines under a task survive parse -> render.

    The lost-content defect at mcloop/PLAN.EXAMPLE.md:111-116: a task
    body ends with "Example flow:" and is followed by markdown sub-
    bullets (``  - ">>> ..."``) at child indent that have no
    checkbox. The parser does not model markdown list items as
    first-class nodes; pre-fix it dropped them and the renderer
    therefore could not emit them, breaking the lossless-
    canonicalize invariant of design doc section 3.2
    (planfile.md:268). Opaque retention on ``Task.trailing_lines``
    preserves them.
    """
    source = (
        "# Project\n"
        "\n"
        "## Stage 1: Outputs\n"
        "\n"
        "- [x] Clearer terminal output. Example flow:\n"
        '  - ">>> [TASK 13.2] Extracting frames..."\n'
        '  - ">>> [CHECKS] Running ruff check, pytest..."\n'
        "  - Keep Bash commands visible since they show meaningful actions\n"
        "\n"
        "- [x] Reduce notification frequency\n"
    )
    plan = parse_plan(source)
    task = plan.phases[0].tasks[0]
    assert task.trailing_lines == (
        '  - ">>> [TASK 13.2] Extracting frames..."',
        '  - ">>> [CHECKS] Running ruff check, pytest..."',
        "  - Keep Bash commands visible since they show meaningful actions",
        "",
    )
    rendered = render_plan(plan)
    for retained in task.trailing_lines:
        if retained:
            assert retained in rendered, f"trailing line {retained!r} dropped on render"
    # Fixed-point: re-parse and re-render must agree byte-for-byte.
    second = render_plan(parse_plan(rendered))
    assert second == rendered, (
        "render(parse(render(plan))) drifted; "
        "trailing_lines are not stable under round-trip"
    )


def test_intra_section_blank_lines_preserved() -> None:
    """Blank lines between sibling tasks survive parse -> render.

    Pre-fix the renderer emitted exactly one canonical blank after
    ``phase.tasks`` regardless of source spacing, collapsing the
    visual grouping authors use (e.g. mcloop/PLAN.EXAMPLE.md groups
    sibling task blocks with blank-line separators). With opaque
    retention the blank line falls into the prior task's
    ``trailing_lines`` and survives.
    """
    source = (
        "# Project\n"
        "\n"
        "## Stage 1: Spacing\n"
        "\n"
        "- [x] task A\n"
        "\n"
        "- [x] task B\n"
        "\n"
        "- [x] task C\n"
    )
    plan = parse_plan(source)
    tasks = plan.phases[0].tasks
    assert tasks[0].trailing_lines == ("",), tasks[0].trailing_lines
    assert tasks[1].trailing_lines == ("",), tasks[1].trailing_lines
    # Last task: trailing blank (line before EOF) is stripped by
    # render_plan's trailing-blank truncation, so retention here is
    # incidental — the property being asserted is intra-section
    # spacing between sibling tasks.
    rendered = render_plan(plan)
    expected_prefix = (
        "# Project\n\n## Stage 1: Spacing\n\n"
        "- [x] task A\n\n"
        "- [x] task B\n\n"
        "- [x] task C\n"
    )
    assert rendered.startswith(expected_prefix), rendered
