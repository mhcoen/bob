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
