"""Whole-plan post-generation sanity verifier for an assembled PLAN.md.

The per-phase planner and the various task injectors (scope-derived
features, behavior contracts, video verification cases) each produce
locally-valid fragments, but nothing checks the *assembled* plan as a
whole. Three failure classes have escaped that gap:

  - a ``## Scope`` ``include:`` item that no phase actually builds (the
    user asked for a feature and it silently fell off the roadmap);
  - a behavior/verification task that tests a feature no phase builds
    (a "verify-without-build" task that can never pass);
  - duplicate or non-sequential ``<!-- phase_id: ... -->`` comments,
    which break downstream lineage tracking.

:func:`check_plan_sanity` runs all three checks against the assembled
PLAN.md text and returns a :class:`PlanSanityReport`. It is pure: it
reads the plan (and the spec's scope list) and reports, it never
rewrites anything. Wiring the report into a corrective gate is a
separate concern (see the pipeline integration task), so the kinds are
exposed as stable constants for a downstream repairer to dispatch on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from duplo.reauthor_phase_ids import parse_plan_phases

# Stable violation-kind tags. A downstream repair gate dispatches on
# these, so they are part of the module's public contract.
KIND_SCOPE_UNCOVERED = "scope_uncovered"
KIND_VERIFY_WITHOUT_BUILD = "verify_without_build"
KIND_PHASE_IDS = "phase_ids"

# A checkbox task line, checked or unchecked.
_TASK_LINE_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\]\s*(?P<body>.*\S)?\s*$")
# A leading task id token (``T-000002:``) prefixing the description.
_TASK_ID_RE = re.compile(r"^[A-Za-z]+-\d+:\s*")
# Trailing run of [feat: "..."] / [fix: "..."] annotations on a task.
_TRAILING_ANNO_RE = re.compile(r"(\s*\[(?:feat|fix):\s*\"[^\"]+\"(?:,\s*\"[^\"]+\")*\])+\s*$")
_ANNO_RE = re.compile(r"\[(feat|fix):\s*((?:\"[^\"]+\")(?:,\s*\"[^\"]+\")*)\]")
_QUOTED_RE = re.compile(r"\"([^\"]+)\"")
# A task whose description is a behavior/verification check.
_VERIFY_PREFIX_RE = re.compile(r"^(?:verify|verification|test that)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SanityViolation:
    """One whole-plan invariant that the assembled PLAN.md breaks."""

    kind: str
    message: str


@dataclass
class PlanSanityReport:
    """Result of :func:`check_plan_sanity`.

    ``ok`` is true exactly when no invariant was broken. ``violations``
    preserves discovery order: scope coverage, then verification
    mapping, then phase-id structure.
    """

    violations: list[SanityViolation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def kinds(self) -> set[str]:
        """Return the distinct violation kinds present."""
        return {v.kind for v in self.violations}


@dataclass(frozen=True)
class _ParsedTask:
    """A task line reduced to the fields the checks care about.

    ``line_index`` is the task line's 0-based position in
    ``plan_text.splitlines()``. The pure checks ignore it; a downstream
    repairer (see :mod:`duplo.plan_gate`) uses it to target the exact
    source line for a deterministic edit without re-parsing.
    """

    text: str
    feats: tuple[str, ...]
    is_verification: bool
    line_index: int = -1


def _normalize(text: str) -> str:
    """Lowercase, strip, and collapse internal whitespace for matching."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_tasks(plan_text: str) -> list[_ParsedTask]:
    """Extract every checkbox task (checked or unchecked) from the plan.

    Trailing ``[feat: "..."]`` / ``[fix: "..."]`` annotations are split
    off the description; only ``feat`` names are retained (fixes do not
    name a built feature). A task is flagged as a verification task when
    its remaining description begins with a verify/test phrasing, which
    is exactly how behavior contracts and video cases are rendered
    (``Verify: type ...``).
    """
    tasks: list[_ParsedTask] = []
    for index, line in enumerate(plan_text.splitlines()):
        match = _TASK_LINE_RE.match(line)
        if match is None:
            continue
        body = (match.group("body") or "").strip()
        feats: list[str] = []
        trailing = _TRAILING_ANNO_RE.search(body)
        if trailing:
            tail = body[trailing.start() :]
            for anno in _ANNO_RE.finditer(tail):
                if anno.group(1) == "feat":
                    feats.extend(_QUOTED_RE.findall(anno.group(2)))
            body = body[: trailing.start()].rstrip()
        body = _TASK_ID_RE.sub("", body)
        tasks.append(
            _ParsedTask(
                text=body,
                feats=tuple(feats),
                is_verification=bool(_VERIFY_PREFIX_RE.match(body)),
                line_index=index,
            )
        )
    return tasks


def _resolve_scope_include(scope_include: Any | None, spec: Any | None) -> list[str]:
    """Pick the scope include list from an explicit arg or a spec object."""
    if scope_include is not None:
        return [str(item) for item in scope_include]
    if spec is not None:
        items = getattr(spec, "scope_include", None)
        if items:
            return [str(item) for item in items]
    return []


def _check_scope_coverage(
    scope_include: list[str], build_tasks: list[_ParsedTask]
) -> list[SanityViolation]:
    """Every scope ``include:`` item must be built by some build task.

    A scope item is covered when its normalized text appears in a build
    task's description, or it shares a (substring-either-direction) name
    with one of that task's ``feat`` annotations.
    """
    haystacks: list[tuple[str, list[str]]] = [
        (_normalize(t.text), [_normalize(f) for f in t.feats]) for t in build_tasks
    ]
    violations: list[SanityViolation] = []
    for item in scope_include:
        item_norm = _normalize(item)
        if not item_norm:
            continue
        covered = False
        for text_norm, feat_norms in haystacks:
            if item_norm in text_norm:
                covered = True
                break
            if any(f and (f in item_norm or item_norm in f) for f in feat_norms):
                covered = True
                break
        if not covered:
            violations.append(
                SanityViolation(
                    kind=KIND_SCOPE_UNCOVERED,
                    message=(
                        f"Scope include item {item!r} is built by no phase "
                        "(no task implements it)."
                    ),
                )
            )
    return violations


def _is_orphan_verification(task: _ParsedTask, built_features: set[str]) -> bool:
    """True when a verification task maps to no feature any phase builds.

    A verification task with ``[feat: ...]`` annotations is an orphan
    when none of those features is built by a non-verify task. An
    unannotated verification task is an orphan only when the plan builds
    nothing at all (otherwise it is assumed to exercise some built
    feature). This is the single predicate behind both the
    :data:`KIND_VERIFY_WITHOUT_BUILD` check and the repairer's
    line-targeting in :mod:`duplo.plan_gate`.
    """
    if task.feats:
        named = [_normalize(f) for f in task.feats]
        return not any(f in built_features for f in named)
    return not built_features


def _check_verification_mapping(
    verify_tasks: list[_ParsedTask], built_features: set[str]
) -> list[SanityViolation]:
    """Every verification task must map to a feature some phase builds.

    A verification task with ``[feat: ...]`` annotations is valid only
    when at least one of those features is actually built by a non-verify
    task. An unannotated verification task is accepted as long as the
    plan builds at least one feature (it is assumed to exercise one of
    them); a plan that has verification tasks but builds nothing fails.
    """
    violations: list[SanityViolation] = []
    for task in verify_tasks:
        if not _is_orphan_verification(task, built_features):
            continue
        if task.feats:
            missing = ", ".join(repr(f) for f in task.feats)
            violations.append(
                SanityViolation(
                    kind=KIND_VERIFY_WITHOUT_BUILD,
                    message=(
                        f"Verification task {task.text!r} references "
                        f"feature(s) {missing} that no phase builds."
                    ),
                )
            )
        else:
            violations.append(
                SanityViolation(
                    kind=KIND_VERIFY_WITHOUT_BUILD,
                    message=(
                        f"Verification task {task.text!r} maps to no built "
                        "feature; the plan builds nothing."
                    ),
                )
            )
    return violations


def orphan_verification_lines(plan_text: str) -> list[int]:
    """Return the 0-based source line indices of orphan verification tasks.

    An orphan verification task is one that :func:`check_plan_sanity`
    flags as :data:`KIND_VERIFY_WITHOUT_BUILD`. This read-only accessor
    lets a deterministic repairer drop exactly those lines using the same
    parse and the same orphan predicate the checker uses, so the two can
    never diverge. Indices are returned in source order.
    """
    tasks = _parse_tasks(plan_text)
    build_tasks = [t for t in tasks if not t.is_verification]
    built_features = {_normalize(f) for t in build_tasks for f in t.feats}
    return [
        t.line_index
        for t in tasks
        if t.is_verification and _is_orphan_verification(t, built_features)
    ]


def _check_phase_ids(plan_text: str) -> list[SanityViolation]:
    """phase_id comments must be unique and sequential phase_001..phase_NNN."""
    headers = parse_plan_phases(plan_text)
    ids = [h.id for h in headers]
    if not ids:
        return []
    expected = [f"phase_{n:03d}" for n in range(1, len(ids) + 1)]
    if ids == expected:
        return []
    seen: set[str] = set()
    dups: list[str] = []
    for pid in ids:
        if pid in seen and pid not in dups:
            dups.append(pid)
        seen.add(pid)
    if dups:
        return [
            SanityViolation(
                kind=KIND_PHASE_IDS,
                message="Duplicate phase_id(s): " + ", ".join(sorted(dups)),
            )
        ]
    return [
        SanityViolation(
            kind=KIND_PHASE_IDS,
            message=(
                "phase_ids are not sequential phase_001.."
                f"phase_{len(ids):03d}; got {ids} expected {expected}."
            ),
        )
    ]


def check_plan_sanity(
    plan_text: str,
    *,
    scope_include: Any | None = None,
    spec: Any | None = None,
) -> PlanSanityReport:
    """Run the whole-plan invariant checks on an assembled PLAN.md.

    Args:
        plan_text: The full, assembled PLAN.md markdown.
        scope_include: The ``## Scope`` include list. When omitted and
            ``spec`` is given, ``spec.scope_include`` is used.
        spec: A parsed ``ProductSpec`` (or any object exposing
            ``scope_include``). Only the scope list is read from it.

    Returns:
        A :class:`PlanSanityReport`; ``report.ok`` is true when the plan
        passes every check.
    """
    tasks = _parse_tasks(plan_text)
    build_tasks = [t for t in tasks if not t.is_verification]
    verify_tasks = [t for t in tasks if t.is_verification]
    built_features = {_normalize(f) for t in build_tasks for f in t.feats}

    resolved_scope = _resolve_scope_include(scope_include, spec)

    violations: list[SanityViolation] = []
    violations.extend(_check_scope_coverage(resolved_scope, build_tasks))
    violations.extend(_check_verification_mapping(verify_tasks, built_features))
    violations.extend(_check_phase_ids(plan_text))

    return PlanSanityReport(violations=violations)


__all__ = [
    "KIND_PHASE_IDS",
    "KIND_SCOPE_UNCOVERED",
    "KIND_VERIFY_WITHOUT_BUILD",
    "PlanSanityReport",
    "SanityViolation",
    "check_plan_sanity",
    "orphan_verification_lines",
]
