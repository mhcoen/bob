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
_TRAILING_ANNO_RE = re.compile(r"(\s*\[(?:feat|fix):\s*[^\]]+\])+\s*$")
_ANNO_RE = re.compile(r"\[(feat|fix):\s*([^\]]+)\]")
_QUOTED_RE = re.compile(r"\"([^\"]+)\"")
# A task whose description is phrased like a behavior/verification check.
_VERIFY_PREFIX_RE = re.compile(r"^(?:verify|verification|test that)\b", re.IGNORECASE)
# The strict machine-rendered verification form ("Verify: type ..."), the
# only phrasing duplo's own renderers emit (spec_reader's behavior
# contracts, verification_extractor's video cases).
_VERIFY_STRICT_RE = re.compile(r"^(?:verify|verification)\s*:", re.IGNORECASE)


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


# Glue words that carry no feature identity; dropped before token-overlap
# matching so a paraphrase ("offline sync" vs "sync data offline") is not
# penalised for differing connective words.
_SCOPE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "to",
        "with",
        "in",
        "on",
        "via",
        "using",
        "from",
        "into",
        "by",
        "as",
        "able",
        "ability",
        "support",
        "supports",
        "feature",
        "features",
        "basic",
        "simple",
    }
)

# Separators that decompose an umbrella scope item into its constituents
# ("init, run, and next" -> init / run / next).
_CONSTITUENT_SPLIT_RE = re.compile(r",|\band\b|&|/|;")
# Minimum fraction of a scope item's significant tokens that a single build
# task must cover for the item to count as built. Set below a strict majority
# so paraphrases survive, in line with the bias-toward-not-flagging contract.
_TOKEN_OVERLAP_THRESHOLD = 0.6


def _leading_label(text: str) -> str:
    """Return the label before the first colon (or the whole text)."""
    return text.split(":", 1)[0].strip()


def _sig_tokens(text: str) -> frozenset[str]:
    """Significant word tokens of ``text`` (alphanumeric, non-stopword)."""
    return frozenset(
        t for t in re.findall(r"[a-z0-9]+", text) if len(t) >= 2 and t not in _SCOPE_STOPWORDS
    )


def _tokens_related(a: str, b: str) -> bool:
    """True when two tokens are equal or one is a stem-prefix of the other.

    Prefix matching (with a 4-char floor on the shorter token) lets
    morphological variants count as the same key noun: ``sync`` matches
    ``synchronization``, ``convert`` matches ``conversion``.
    """
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long.startswith(short)


def _token_overlap_covered(item_tokens: frozenset[str], task_tokens: frozenset[str]) -> bool:
    """True when a build task covers enough of an item's key tokens."""
    if not item_tokens:
        return False
    matched = sum(1 for it in item_tokens if any(_tokens_related(it, tt) for tt in task_tokens))
    return matched / len(item_tokens) >= _TOKEN_OVERLAP_THRESHOLD


@dataclass(frozen=True)
class _BuilderView:
    """A build task reduced to the forms the scope matcher compares against."""

    text_norm: str
    feat_norms: tuple[str, ...]
    token_bag: frozenset[str]


def _builder_views(build_tasks: list[_ParsedTask]) -> list[_BuilderView]:
    views: list[_BuilderView] = []
    for t in build_tasks:
        text_norm = _normalize(t.text)
        feat_norms = tuple(_normalize(f) for f in t.feats)
        token_bag = _sig_tokens(text_norm)
        for f in feat_norms:
            token_bag |= _sig_tokens(f)
        views.append(_BuilderView(text_norm=text_norm, feat_norms=feat_norms, token_bag=token_bag))
    return views


def _scope_text_matches(item_norm: str, builders: list[_BuilderView]) -> bool:
    """Match a single (already-normalized) scope phrase against builders.

    Tries, in order of confidence: verbatim substring against a task's
    description, two-way substring against a task's ``feat`` name, and
    finally token-overlap against the task's combined token bag. The same
    rules are retried against the item's leading label (the part before the
    first colon), so ``"Search: full-text over notes"`` is covered by a task
    that merely builds search.
    """
    if not item_norm:
        return False
    label = _leading_label(item_norm)
    candidates = [item_norm] if label == item_norm else [item_norm, label]
    for candidate in candidates:
        cand_tokens = _sig_tokens(candidate)
        for b in builders:
            if candidate and candidate in b.text_norm:
                return True
            if any(f and (f in candidate or candidate in f) for f in b.feat_norms):
                return True
            if _token_overlap_covered(cand_tokens, b.token_bag):
                return True
    return False


def _constituents(item_norm: str) -> list[str]:
    """Split an umbrella scope item into its listed constituents.

    A parenthetical list ("Subcommands (init, run, next)") is decomposed on
    its contents; otherwise the whole phrase is split on commas/``and``/
    slashes. Only fragments that carry a significant token are returned, so
    connective noise does not manufacture phantom constituents.
    """
    paren = re.search(r"\(([^)]*)\)", item_norm)
    source = paren.group(1) if paren else item_norm
    out: list[str] = []
    for part in _CONSTITUENT_SPLIT_RE.split(source):
        part = part.strip()
        if part and _sig_tokens(part):
            out.append(part)
    return out


def _scope_item_covered(item: str, builders: list[_BuilderView]) -> bool:
    """True when some build task plausibly builds this scope item.

    First the whole item is matched directly; failing that, an umbrella item
    is treated as covered when it decomposes into two or more constituents
    that are each built by some task (subcommand-style scope lines whose
    parts land in separate finer features).
    """
    item_norm = _normalize(item)
    if not item_norm:
        return True
    if _scope_text_matches(item_norm, builders):
        return True
    constituents = _constituents(item_norm)
    if len(constituents) >= 2 and all(_scope_text_matches(c, builders) for c in constituents):
        return True
    return False


def _is_verification(body: str) -> bool:
    """Classify a task description as verification or build work.

    Phrase-only: the strict machine-rendered form (``Verify: type ...``)
    and any prose verify/test phrasing both classify as verification,
    REGARDLESS of a ``[feat: ...]`` annotation. An earlier fix
    (T-000003) exempted feat-annotated verify phrasings so the repair
    gate would not delete them -- but that let their feats enter
    ``built_features``, so a plan containing ONLY verification text
    passed the gate clean with full scope coverage: verification
    satisfied its own build requirement. The deletion protection now
    lives in :func:`orphan_verification_lines` instead (feat-carrying
    verify tasks are never mechanically dropped; they hard-stop the
    gate loudly for a human decision), so classification can stay
    honest: verification text never counts as building anything.
    """
    return bool(_VERIFY_STRICT_RE.match(body)) or bool(_VERIFY_PREFIX_RE.match(body))


def _annotation_values(raw: str) -> list[str]:
    """Return feature/fix names from a raw annotation value list."""
    quoted = _QUOTED_RE.findall(raw)
    if quoted:
        return quoted
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_tasks(plan_text: str) -> list[_ParsedTask]:
    """Extract every checkbox task (checked or unchecked) from the plan.

    Trailing ``[feat: "..."]`` / ``[fix: "..."]`` annotations are split
    off the description; only ``feat`` names are retained (fixes do not
    name a built feature). Verification classification is delegated to
    :func:`_is_verification`.
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
                    feats.extend(_annotation_values(anno.group(2)))
            body = body[: trailing.start()].rstrip()
        body = _TASK_ID_RE.sub("", body)
        feat_names = tuple(feats)
        tasks.append(
            _ParsedTask(
                text=body,
                feats=feat_names,
                is_verification=_is_verification(body),
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

    Coverage is matched robustly rather than by verbatim identity, because
    a feature is routinely built under a paraphrased name or decomposed
    across several finer tasks. An item counts as covered when a single
    build task matches it by substring, by ``feat`` name, or by sharing
    enough key tokens (paraphrase), or when an umbrella item's listed
    constituents are each built. The check is biased toward *not* flagging:
    :data:`KIND_SCOPE_UNCOVERED` is reported only when no plausible builder
    exists for the item under any of these strategies.
    """
    builders = _builder_views(build_tasks)
    violations: list[SanityViolation] = []
    for item in scope_include:
        item_norm = _normalize(item)
        if not item_norm:
            continue
        if not _scope_item_covered(item, builders):
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
    """Return the 0-based source line indices of DROPPABLE orphan
    verification tasks.

    An orphan verification task is one that :func:`check_plan_sanity`
    flags as :data:`KIND_VERIFY_WITHOUT_BUILD`. This read-only accessor
    lets a deterministic repairer drop exactly those lines using the
    same parse and the same orphan predicate the checker uses.

    A feat-annotated PROSE-form orphan ("Verify the exporter handles
    empty input [feat: ...]") is deliberately EXCLUDED from the
    droppable set: the annotation is a signal the task may be genuine
    build work merely phrased as "Verify ...", and deleting it
    mechanically would destroy feature work (the original T-000003
    bug). Such a task stays flagged by the checker, so the gate's
    single repair pass cannot clear it and the hard-stop report
    surfaces it for a human decision: add a real builder, rephrase the
    task, or drop the annotation. Its feats never count toward
    ``built_features`` either way -- a plan of pure verification text
    cannot pass the gate. The strict machine-rendered form
    (``Verify: ...``) remains droppable even with a feat annotation: it
    is exactly what duplo's own renderers emit for verification cases,
    so it is never ambiguous build work. Indices are returned in source
    order.
    """
    tasks = _parse_tasks(plan_text)
    build_tasks = [t for t in tasks if not t.is_verification]
    built_features = {_normalize(f) for t in build_tasks for f in t.feats}
    return [
        t.line_index
        for t in tasks
        if t.is_verification
        and (bool(_VERIFY_STRICT_RE.match(t.text)) or not t.feats)
        and _is_orphan_verification(t, built_features)
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
