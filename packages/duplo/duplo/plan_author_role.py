"""The duplo-owned ``plan_author`` compound role binding.

``plan_author`` is the Orchestra compound role consumed by
``orchestra.run_role("plan_author", ...)`` to author one phase's
PLAN.md body through the proposer -> reviewer -> judge -> validate
loop defined in ``duplo/workflows/plan_author.orc``.

Where this config lives
-----------------------
The binding is emitted into the project-local
``<project>/.orchestra/config.json`` ``role_bindings`` table by
``duplo.init`` (see ``_ORCHESTRA_COUNCIL_CONFIG``). It is owned by
duplo, not Orchestra.

Distinct from the shared ``design`` role
----------------------------------------
Orchestra ships a *shared* ``design`` compound role in
``orchestra.config.default_config`` (pattern ``design_loop``,
judge-first, judge=fable / reviewer=codex, no proposer, no criteria).
``plan_author`` is a DISTINCT role: it binds a different workflow
pattern (``plan_author``), declares its own proposer/reviewer/judge
leaf bindings, and -- unlike ``design`` -- carries a set of
role-scoped acceptance ``criteria``. The two must not be folded
together; they bind different workflows and gate on different rules.

Leaf bindings
-------------
The leaf-binding keys are the workflow's own role names
(``proposer``, ``reviewer``, ``judge_role`` in ``plan_author.orc``).
The reviewer is bound to a different actor (``codex``) than the
proposer and judge (``fable``) so the review is independent of the
authoring/judging model's training data -- the same independence
rationale the shared ``design`` role uses for its judge/reviewer
split.

Acceptance criteria
-------------------
The criteria reach the judge via Orchestra phase_001 extension point
A: ``CompoundRoleBinding.criteria`` -> ``run_role`` ->
``derived_criteria`` -> the Executor (see
``orchestra/api/dispatch.py``). They encode ONLY the judgment-level
PLAN.md quality rules from ``duplo.planner._PHASE_SYSTEM`` that the
structural validation transform (``validate_plan_body``, NOTES
[9.5]/[T-000787]) does not already enforce:

  - task granularity (5-15 top-level checklist items per phase),
  - [BATCH]/[USER]/[AUTO] discipline,
  - [feat:]/[fix:] annotation presence.

The hard structural rules -- the canonical ``## Phase phase_NNN:``
header, the required phase id, no ``## Bugs`` section, and no project
``# H1`` -- are enforced mechanically by ``validate_plan_body`` (the
post-accept validation gate) and are deliberately NOT duplicated as
prose criteria here.
"""

from __future__ import annotations

from typing import Any

ROLE_NAME = "plan_author"
WORKFLOW_PATTERN = "plan_author"

# Round cap for the proposer -> reviewer -> judge loop. Mirrors the
# original hardcoded ``attempts.judge < 6`` cap in
# ``iterate_until_acceptable`` that the fork replaced with the
# ``max_rounds`` external input (see plan_author.orc).
MAX_ROUNDS = 6

# Leaf-binding model identifiers, resolved through Orchestra's
# ProfileRegistry at workflow start. Reviewer is ``codex`` so its
# critique is independent of the ``fable`` proposer/judge.
PROPOSER_MODEL = "fable"
REVIEWER_MODEL = "codex"
JUDGE_MODEL = "fable"

PLAN_AUTHOR_CRITERIA: tuple[dict[str, Any], ...] = (
    {
        "id": "task_granularity_5_to_15",
        "description": (
            "The phase body has between 5 and 15 top-level checklist items, inclusive."
        ),
        "required": True,
    },
    {
        "id": "batch_user_auto_discipline",
        "description": (
            "[BATCH] marks only parents whose subtasks are all concrete "
            "(file paths, function names, explicit values) and need no "
            "design decisions, and never a parent that contains a [USER] "
            "or [AUTO] subtask. [USER] is reserved for genuinely "
            "human-only checks with no scriptable form; any scriptable "
            "verification uses a helper-script task plus an [AUTO:run_cli] "
            "task instead of a [USER] task."
        ),
        "required": True,
    },
    {
        "id": "feat_fix_annotations_present",
        "description": (
            "Every task implementing a feature from the input list ends "
            'with a [feat: "..."] annotation; tasks that fix a bug or '
            'issue end with a [fix: "..."] annotation; scaffolding or '
            "structural tasks that map to no feature carry no annotation."
        ),
        "required": True,
    },
)


def render_criteria_block() -> str:
    """Render :data:`PLAN_AUTHOR_CRITERIA` as the judge-prompt criteria block.

    The block is injected into ``templates/plan_author_judge.md`` through
    the workflow's ``criteria_block`` input (see ``plan_author.orc`` and
    :func:`duplo.plan_author_adapter.run_plan_author`). It enumerates the
    configured criterion ids and descriptions so the judge emits a
    ``criteria_compliance`` entry per criterion using these EXACT ids and
    no others -- otherwise
    ``orchestra.executor.criteria.check_decision_consistency`` fails with
    ``missing_ids`` / ``extra_ids``.

    Generating the block from the same :data:`PLAN_AUTHOR_CRITERIA` tuple
    that :func:`plan_author_role_binding` feeds into the executor as the
    configured criteria keeps the judge prompt and the binding from
    drifting: there is one source of truth for both the ids the prompt
    asks for and the ids the consistency check enforces.
    """
    lines: list[str] = []
    for index, criterion in enumerate(PLAN_AUTHOR_CRITERIA, start=1):
        lines.append(f"{index}. id: {criterion['id']}")
        lines.append(f"   {criterion['description']}")
    return "\n".join(lines)


def plan_author_role_binding() -> dict[str, Any]:
    """Return the ``plan_author`` compound role binding as a JSON-ready dict.

    Shaped for the ``role_bindings`` table in
    ``.orchestra/config.json`` (one entry, keyed by :data:`ROLE_NAME`).
    Parses into :class:`orchestra.config.CompoundRoleBinding` with a
    ``pattern``, the three leaf bindings (keyed by the workflow's role
    names ``proposer`` / ``reviewer`` / ``judge_role``), ``max_rounds``,
    and the role-scoped ``criteria``.
    """
    return {
        "pattern": WORKFLOW_PATTERN,
        "max_rounds": MAX_ROUNDS,
        "proposer": {"model": PROPOSER_MODEL},
        "reviewer": {"model": REVIEWER_MODEL},
        "judge_role": {"model": JUDGE_MODEL},
        "criteria": [dict(criterion) for criterion in PLAN_AUTHOR_CRITERIA],
    }
