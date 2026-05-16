"""Stdlib-only Plan generator for property-based testing.

Implements ``generate_plan(rng)`` — a deterministic, seeded generator
that yields a structurally-valid :class:`bob_tools.planfile.model.Plan`
on every call. No third-party dependencies (Hypothesis specifically is
ruled out by the design): randomness is sourced from a caller-supplied
:class:`random.Random` so any failure can be reproduced from the seed.

Per Codex's pile-5 acceptance test gap, the generator drives property
tests that explore beyond the hand-crafted fixtures. This module
contains the generator, an invariant-check on the generator itself,
and the two round-trip properties from PLAN.md 4.3.2:

* ``parse(render(plan)) == plan`` modulo the fields normalized by
  :func:`bob_tools.planfile.renderer.normalize_positions` (line
  numbers, ``Task.indent_level``, and ``Phase.phase_id_source``).
* Task IDs in the rendered plan are unique.

The third 4.3.2 property — ``next_tasks`` returns tasks in canonical
order — is deferred to a follow-up because ``next_tasks`` lands in
Stage 5. PLAN.md 4.3.3 will bump the per-property iteration count
from the default (handful of seeds) to 100 default / 1000 slow-marker.

Generator design choices that keep round-trip well-behaved:

* Every task is assigned a unique ``T-NNNNNN`` id, so both compat-mode
  and magic-line strict-mode plans round-trip without the strict-mode
  parser rejecting a missing id.
* When a task carries an action tag, its free-form ``text`` is left
  empty. ``[AUTO:<action>] <args>`` consumes everything from the tag
  to end of line on re-parse, so emitting both a non-empty ``args``
  and non-empty ``text`` would collapse the two on round-trip.
* Phase ``phase_id_source`` is always ``"explicit_comment"`` when a
  ``phase_id`` is set, matching the canonical form the renderer emits;
  ``"none"`` otherwise.
* Deps reference only IDs declared earlier in document order, so the
  generated plan is acyclic and ``validate_plan`` accepts it.
* Prose, titles, and task text are drawn from a small alphabetic word
  pool. Nothing in the pool can be re-parsed as a heading, checkbox,
  tag, annotation, ``@deps`` line, or ``[RULEDOUT]`` sibling — keeping
  the text strictly inside its intended grammar production.
"""

from __future__ import annotations

import random

from bob_tools.planfile import parse_plan, render_plan
from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    RuledOut,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile.operations import validate_plan
from bob_tools.planfile.renderer import normalize_positions

_FLAG_TAG_CHOICES: tuple[str, ...] = ("USER", "BATCH")
_ANNOTATION_KEY_CHOICES: tuple[str, ...] = ("feat", "fix")
_ACTION_NAME_CHOICES: tuple[str, ...] = ("run", "run_cli", "verify_build", "deploy")
_STATUS_CHOICES: tuple[TaskStatus, ...] = (
    TaskStatus.TODO,
    TaskStatus.DONE,
    TaskStatus.FAILED,
)
_KEYWORD_CHOICES: tuple[str, ...] = ("Stage", "Phase")
_WORDS: tuple[str, ...] = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "wire",
    "verify",
    "check",
    "render",
    "parse",
    "ledger",
    "subtask",
    "review",
    "merge",
    "rollback",
)


def _words(rng: random.Random, n_min: int, n_max: int) -> str:
    """Return a space-separated string of ``n_min``..``n_max`` words.

    The word pool is purely alphabetic so the result cannot accidentally
    parse as a heading, checkbox, tag, annotation, ``@deps`` line, or
    ``[RULEDOUT]`` sibling when round-tripped through the parser.
    """
    n = rng.randint(n_min, n_max)
    return " ".join(rng.choice(_WORDS) for _ in range(n))


class _IdAllocator:
    """Issues ``T-NNNNNN`` ids in sequence so generated tasks are unique."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def allocate(self) -> str:
        value = self._next
        self._next += 1
        return f"T-{value:06d}"


def generate_plan(
    rng: random.Random,
    *,
    max_phases: int = 3,
    max_tasks_per_phase: int = 4,
    max_subsections_per_phase: int = 2,
    max_tasks_per_subsection: int = 3,
    max_child_depth: int = 2,
    max_children_per_task: int = 2,
    max_ruled_out_per_task: int = 2,
    max_bugs: int = 3,
) -> Plan:
    """Generate a random, structurally-valid :class:`Plan` using ``rng``.

    The plan satisfies the parser's structural-sanity check by
    construction: phase ordinals are sequential, only one project title
    is emitted, and at most one Bugs section is present. Task ids are
    unique across the plan, and any ``@deps`` reference points to an id
    declared earlier in document order so the result also passes
    :func:`bob_tools.planfile.operations.validate_plan`.

    Caller controls reproducibility entirely via ``rng`` — pass a
    seeded :class:`random.Random` to replay a previously-failing case.
    """
    ids = _IdAllocator()
    declared_ids: list[str] = []

    phase_count = rng.randint(1, max_phases)
    phases: list[Phase] = []
    for ordinal in range(1, phase_count + 1):
        phases.append(
            _generate_phase(
                rng=rng,
                ordinal=ordinal,
                ids=ids,
                declared_ids=declared_ids,
                max_tasks=max_tasks_per_phase,
                max_subsections=max_subsections_per_phase,
                max_tasks_per_subsection=max_tasks_per_subsection,
                max_child_depth=max_child_depth,
                max_children_per_task=max_children_per_task,
                max_ruled_out_per_task=max_ruled_out_per_task,
            )
        )

    bugs: BugsSection | None = None
    if rng.random() < 0.5:
        bug_task_count = rng.randint(0, max_bugs)
        bugs = BugsSection(
            tasks=tuple(
                _generate_task(
                    rng=rng,
                    ids=ids,
                    declared_ids=declared_ids,
                    depth=0,
                    max_child_depth=0,
                    max_children_per_task=0,
                    max_ruled_out_per_task=max_ruled_out_per_task,
                )
                for _ in range(bug_task_count)
            ),
            line_number=0,
        )

    include_magic = rng.random() < 0.5
    include_preamble = rng.random() < 0.5

    return Plan(
        magic_version=1 if include_magic else None,
        project_title=f"Generated Plan {rng.randint(1, 9999)}",
        preamble=_words(rng, 3, 8) if include_preamble else "",
        phases=tuple(phases),
        bugs=bugs,
        source_path=None,
    )


def _generate_phase(
    *,
    rng: random.Random,
    ordinal: int,
    ids: _IdAllocator,
    declared_ids: list[str],
    max_tasks: int,
    max_subsections: int,
    max_tasks_per_subsection: int,
    max_child_depth: int,
    max_children_per_task: int,
    max_ruled_out_per_task: int,
) -> Phase:
    """Generate one phase with random tasks and optional subsections."""
    has_phase_id = rng.random() < 0.5
    phase_id = f"phase_{ordinal:03d}" if has_phase_id else None
    phase_id_source = "explicit_comment" if has_phase_id else "none"

    task_count = rng.randint(0, max_tasks)
    tasks = tuple(
        _generate_task(
            rng=rng,
            ids=ids,
            declared_ids=declared_ids,
            depth=0,
            max_child_depth=max_child_depth,
            max_children_per_task=max_children_per_task,
            max_ruled_out_per_task=max_ruled_out_per_task,
        )
        for _ in range(task_count)
    )

    subsection_count = rng.randint(0, max_subsections)
    subsections = tuple(
        _generate_subsection(
            rng=rng,
            ids=ids,
            declared_ids=declared_ids,
            max_tasks=max_tasks_per_subsection,
            max_child_depth=max_child_depth,
            max_children_per_task=max_children_per_task,
            max_ruled_out_per_task=max_ruled_out_per_task,
        )
        for _ in range(subsection_count)
    )

    return Phase(
        phase_id=phase_id,
        phase_id_source=phase_id_source,
        ordinal=ordinal,
        keyword=rng.choice(_KEYWORD_CHOICES),
        title=_words(rng, 1, 3).title(),
        prose=_words(rng, 3, 8) if rng.random() < 0.5 else "",
        subsections=subsections,
        tasks=tasks,
        line_number=0,
    )


def _generate_subsection(
    *,
    rng: random.Random,
    ids: _IdAllocator,
    declared_ids: list[str],
    max_tasks: int,
    max_child_depth: int,
    max_children_per_task: int,
    max_ruled_out_per_task: int,
) -> Subsection:
    """Generate a subsection with random tasks at indent zero."""
    task_count = rng.randint(0, max_tasks)
    tasks = tuple(
        _generate_task(
            rng=rng,
            ids=ids,
            declared_ids=declared_ids,
            depth=0,
            max_child_depth=max_child_depth,
            max_children_per_task=max_children_per_task,
            max_ruled_out_per_task=max_ruled_out_per_task,
        )
        for _ in range(task_count)
    )
    return Subsection(
        title=_words(rng, 1, 3).title(),
        prose=_words(rng, 3, 8) if rng.random() < 0.5 else "",
        tasks=tasks,
        line_number=0,
    )


def _generate_task(
    *,
    rng: random.Random,
    ids: _IdAllocator,
    declared_ids: list[str],
    depth: int,
    max_child_depth: int,
    max_children_per_task: int,
    max_ruled_out_per_task: int,
) -> Task:
    """Generate one task plus any recursive child tasks below it.

    ``declared_ids`` is appended-to as ids are allocated, and the new
    task's ``deps`` are drawn only from ids appended *before* this
    task's own id. That ordering guarantees deps reference tasks that
    exist earlier in document order and the plan stays acyclic.
    """
    task_id = ids.allocate()

    deps_candidates = list(declared_ids)
    deps: tuple[str, ...] = ()
    if deps_candidates and rng.random() < 0.4:
        dep_count = rng.randint(1, min(2, len(deps_candidates)))
        deps = tuple(rng.sample(deps_candidates, dep_count))

    declared_ids.append(task_id)

    flag_tags: tuple[str, ...] = ()
    if rng.random() < 0.4:
        chosen = sorted(
            set(rng.choices(_FLAG_TAG_CHOICES, k=rng.randint(1, 2))),
            key=_FLAG_TAG_CHOICES.index,
        )
        flag_tags = tuple(chosen)

    action_tag: tuple[str, str] | None = None
    text = ""
    if rng.random() < 0.3:
        action = rng.choice(_ACTION_NAME_CHOICES)
        args = _words(rng, 0, 3)
        action_tag = (action, args)
    else:
        text = _words(rng, 1, 4)

    annotations: tuple[tuple[str, str], ...] = ()
    if rng.random() < 0.3:
        key = rng.choice(_ANNOTATION_KEY_CHOICES)
        value = _words(rng, 1, 3)
        annotations = ((key, value),)

    ruled_out_count = rng.randint(0, max_ruled_out_per_task)
    ruled_out = tuple(
        RuledOut(text=_words(rng, 1, 4), line_number=0) for _ in range(ruled_out_count)
    )

    children: tuple[Task, ...] = ()
    if depth < max_child_depth and max_children_per_task > 0:
        child_count = rng.randint(0, max_children_per_task)
        children = tuple(
            _generate_task(
                rng=rng,
                ids=ids,
                declared_ids=declared_ids,
                depth=depth + 1,
                max_child_depth=max_child_depth,
                max_children_per_task=max_children_per_task,
                max_ruled_out_per_task=max_ruled_out_per_task,
            )
            for _ in range(child_count)
        )

    return Task(
        task_id=task_id,
        text=text,
        status=rng.choice(_STATUS_CHOICES),
        flag_tags=flag_tags,
        action_tag=action_tag,
        annotations=annotations,
        deps=deps,
        children=children,
        ruled_out=ruled_out,
        indent_level=depth * 2,
        line_number=0,
    )


def _assert_deps_well_ordered(
    tasks: tuple[Task, ...],
    id_position: dict[str, int],
    seed: int,
) -> None:
    """Assert every ``deps`` reference is to an id declared earlier.

    Walks ``tasks`` and their recursive ``children``; each ``dep`` must
    appear in ``id_position`` (declared somewhere in the plan) and must
    appear strictly before the dependent task (no forward references,
    no cycles). Lifted out of the test body so the inner closure does
    not capture loop-bound variables — ruff B023.
    """
    for task in tasks:
        assert task.task_id is not None
        self_pos = id_position[task.task_id]
        for dep in task.deps:
            assert dep in id_position, (
                f"seed={seed}: task {task.task_id} has dep {dep} "
                "that is not declared in the plan"
            )
            assert id_position[dep] < self_pos, (
                f"seed={seed}: task {task.task_id} depends on {dep} "
                "which is declared later in document order"
            )
        _assert_deps_well_ordered(task.children, id_position, seed)


def _collect_task_ids(plan: Plan) -> list[str]:
    """Return every ``task_id`` in ``plan`` in document order."""
    ids: list[str] = []

    def _walk(tasks: tuple[Task, ...]) -> None:
        for task in tasks:
            if task.task_id is not None:
                ids.append(task.task_id)
            _walk(task.children)

    for phase in plan.phases:
        _walk(phase.tasks)
        for subsection in phase.subsections:
            _walk(subsection.tasks)
    if plan.bugs is not None:
        _walk(plan.bugs.tasks)
    return ids


def test_generator_produces_structurally_sound_plan() -> None:
    """Sanity test: the generator output is valid by every cheap check.

    Runs a handful of seeds and asserts the invariants the generator
    promises by construction — unique ids, sequential phase ordinals,
    deps reference earlier ids, the plan renders and re-parses without
    a :class:`PlanSyntaxError`, and :func:`validate_plan` accepts it.
    Round-trip equality (``parse(render(plan)) == plan``) is the next
    task's job; this test only proves the generator is well-formed
    enough for that task to use.
    """
    for seed in range(8):
        rng = random.Random(seed)
        plan = generate_plan(rng)

        ids = _collect_task_ids(plan)
        assert len(ids) == len(set(ids)), (
            f"seed={seed}: duplicate task ids in generated plan: {ids}"
        )

        ordinals = [phase.ordinal for phase in plan.phases]
        assert ordinals == list(range(1, len(ordinals) + 1)), (
            f"seed={seed}: phase ordinals are not sequential: {ordinals}"
        )

        id_position = {tid: idx for idx, tid in enumerate(ids)}

        for phase in plan.phases:
            _assert_deps_well_ordered(phase.tasks, id_position, seed)
            for subsection in phase.subsections:
                _assert_deps_well_ordered(subsection.tasks, id_position, seed)
        if plan.bugs is not None:
            _assert_deps_well_ordered(plan.bugs.tasks, id_position, seed)

        rendered = render_plan(plan)
        reparsed = parse_plan(rendered)
        assert _collect_task_ids(reparsed) == ids, (
            f"seed={seed}: rendered plan lost ids on re-parse"
        )
        validate_plan(reparsed)


def test_parse_render_plan_equals_plan_modulo_line_numbers() -> None:
    """Property: ``parse(render(plan))`` equals ``plan`` modulo line numbers.

    The "modulo" set is what :func:`normalize_positions` collapses —
    ``line_number`` (the rendered text has its own layout),
    ``Task.indent_level`` (renderer canonicalizes to two-space-per-level),
    and ``Phase.phase_id_source`` (renderer migrates legacy
    ``explicit_header`` to ``explicit_comment``). The generator already
    emits plans in canonical form for those fields, so applying
    ``normalize_positions`` to the original is effectively a no-op and
    only the re-parsed side gains anything; running it on both sides is
    cheaper than asserting that and keeps the oracle symmetric with the
    fixture-based ``test_parse_render_parse_idempotent``.
    """
    for seed in range(8):
        rng = random.Random(seed)
        plan = generate_plan(rng)
        reparsed = parse_plan(render_plan(plan))
        assert normalize_positions(reparsed) == normalize_positions(plan), (
            f"seed={seed}: parse(render(plan)) != plan modulo line numbers"
        )


def test_rendered_plan_has_unique_task_ids() -> None:
    """Property: every task in the rendered plan has a distinct ``task_id``.

    Re-parses the rendered text and walks the resulting Plan, asserting
    that no two tasks (across phases, subsections, the Bugs section, and
    nested children) share an id. Catches a renderer bug that drops or
    duplicates an id, and a parser bug that conflates two T-NNNNNN
    strings — both of which the round-trip equality property would also
    catch but only at the cost of a less-targeted failure message.
    """
    for seed in range(8):
        rng = random.Random(seed)
        plan = generate_plan(rng)
        reparsed = parse_plan(render_plan(plan))
        rendered_ids = _collect_task_ids(reparsed)
        assert len(rendered_ids) == len(set(rendered_ids)), (
            f"seed={seed}: duplicate task ids in rendered plan: {rendered_ids}"
        )
