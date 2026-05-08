"""PLAN.md phase header parser plus lineage validator/diff for the JSON sidecar.

Slice C of the Plan Ledger requires the synthesizer to declare phase
lineage explicitly so the projector does not have to infer
relationships between old and new phases. The shape of that
declaration changed from in-prose HTML comments to a JSON sidecar
attached to the verdict; the synthesizer now emits a
``lineage`` object alongside ``decision`` / ``feedback`` /
``agreements`` etc., and this module consumes it.

See ``orchestra/workflows/templates/council_synthesizer.md`` for the
synthesizer-side description of the contract and the per-action
constraints. The schema lives at
``orchestra/workflows/schemas/council_synthesis_verdict.json``.

Public surface:

  - :func:`parse_plan_phases` extracts header id + title pairs from
    PLAN.md in source order.
  - :func:`validate_lineage` enforces the Slice C invariants on the
    sidecar and raises :class:`LineageValidationError` fail-closed.
  - :func:`compute_lineage_diff` walks the sidecar and returns a
    structured :class:`LineageDiff` for the lifecycle-event emitter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)

_VALID_ACTIONS = frozenset(["preserve", "supersede", "split", "merge", "new"])
_NO_FROM_ACTIONS = frozenset(["preserve", "new"])


class LineageValidationError(ValueError):
    """Raised when a re-authored plan violates lineage rules.

    Slice C fails closed. Silent repair invents lineage and is
    exactly the failure mode the explicit declaration is meant to
    prevent. The error message names the offending phase ids and
    the rules they break.
    """


@dataclass(frozen=True)
class ParsedHeader:
    """One phase header parsed out of PLAN.md.

    Only ``id``, ``title``, and the source line index are recorded.
    Lineage is no longer carried in the markdown; see the lineage
    sidecar in the synthesizer's verdict JSON for that.
    """

    id: str
    title: str
    header_line_index: int


def parse_plan_phases(plan_text: str) -> list[ParsedHeader]:
    """Extract phase headers from PLAN.md in source order.

    Lines that do not match the strict ``## Phase <id>: <title>``
    format are ignored. Pre-Slice C plans whose headers do not
    follow this format produce an empty list, which the consumer
    treats as a fresh-id labeling pass on a pre-Slice C plan.
    """
    headers: list[ParsedHeader] = []
    for i, line in enumerate(plan_text.splitlines()):
        match = _HEADER_RE.match(line)
        if match is None:
            continue
        headers.append(
            ParsedHeader(
                id=match.group("id"),
                title=match.group("title"),
                header_line_index=i,
            )
        )
    return headers


@dataclass
class LineageDiff:
    """Structured lineage change derived directly from the sidecar.

    The Slice C emitter consumes this to produce the lifecycle
    events (``phase_superseded`` / ``phase_split`` / ``phase_merged``
    / ``phase_abandoned``) appended to the ledger before
    ``plan_reauthored``. Order is deterministic so a replay against
    the same sidecar produces the same event sequence.
    """

    superseded: list[tuple[str, str]] = field(default_factory=list)
    """One ``(old_id, new_id)`` per supersede ``from`` entry, sorted."""

    split: list[tuple[str, list[str]]] = field(default_factory=list)
    """One ``(old_id, [new_id, ...])`` per prior phase that any split
    entry's ``from`` referenced. Sorted by old_id; branches sorted
    within each entry."""

    merged: list[tuple[list[str], str]] = field(default_factory=list)
    """One ``([old_id, ...], new_id)`` per merge entry. The old_ids
    list preserves the synthesizer's declared order so the audit
    trail matches what the synthesizer wrote."""

    abandoned: list[tuple[str, str]] = field(default_factory=list)
    """One ``(id, reason)`` per ``lineage.abandoned`` entry, sorted
    by id."""


def validate_lineage(
    prior_plan_ids: Iterable[str],
    new_plan_ids: Iterable[str],
    lineage: Mapping[str, Any],
) -> None:
    """Fail closed if the lineage sidecar violates Slice C's invariants.

    Parameters
    ----------
    prior_plan_ids
        Phase ids the prior plan declared, in any order. The
        validator builds a set; duplicates here are a caller error
        (the parser cannot produce them, so this is a sanity check).
    new_plan_ids
        Phase ids the synthesizer wrote in the new plan body, in
        source order. Duplicates here are a synthesizer error and
        the validator reports them.
    lineage
        The ``lineage`` object from the synthesizer's verdict JSON.
        ``phases`` is required; ``abandoned`` is optional.

    Rules enforced (per
    ``design/council-actor-bindings.md``-adjacent contract,
    documented in the synthesizer template):

      - ``phases[].id`` is unique within ``phases[]``.
      - The set of plan-header ids equals the set of ``phases[].id``
        ids exactly. No unmentioned headers; no phantom entries.
      - ``preserve``: id MUST exist in prior plan; ``from`` MUST be
        absent.
      - ``new``: id MUST NOT exist in prior plan; ``from`` MUST be
        absent.
      - ``supersede`` / ``split``: id MUST NOT exist in prior plan;
        ``from`` MUST be a non-empty list of prior plan ids.
      - ``merge``: id MUST NOT exist in prior plan; ``from`` MUST
        contain at least two prior plan ids.
      - ``abandoned[].id`` MUST exist in prior plan AND must not
        appear elsewhere as a preserved id or a ``from`` entry.
      - Every prior plan id appears EXACTLY ONCE across the union
        of {preserved ids, ``from`` entries of supersede / split /
        merge entries, abandoned ids}.
      - No preserved id appears in any ``from`` list.

    All violations are accumulated; the error message lists every
    one. Fail-closed: any violation raises before the caller writes
    anything to the ledger.
    """
    prior = list(prior_plan_ids)
    prior_set = set(prior)
    if len(prior_set) != len(prior):
        raise LineageValidationError(
            "internal: prior_plan_ids contains duplicates: "
            + ", ".join(sorted(_dups(prior)))
        )

    new_list = list(new_plan_ids)
    new_set = set(new_list)
    if len(new_set) != len(new_list):
        raise LineageValidationError(
            "duplicate phase id in plan headers: "
            + ", ".join(sorted(_dups(new_list)))
        )

    if not isinstance(lineage, Mapping):
        raise LineageValidationError("lineage sidecar must be a JSON object")

    phases = lineage.get("phases")
    if not isinstance(phases, list):
        raise LineageValidationError("lineage.phases must be a list")

    abandoned_raw = lineage.get("abandoned")
    if abandoned_raw is None:
        abandoned: list[Any] = []
    elif isinstance(abandoned_raw, list):
        abandoned = abandoned_raw
    else:
        raise LineageValidationError(
            "lineage.abandoned must be a list when present"
        )

    errors: list[str] = []
    seen_phase_ids: set[str] = set()
    preserved_ids: set[str] = set()
    # Per prior id, the list of (action, new_id) entries that reference it.
    consumed_by: dict[str, list[tuple[str, str]]] = {}

    for index, entry in enumerate(phases):
        if not isinstance(entry, Mapping):
            errors.append(f"lineage.phases[{index}] is not an object")
            continue
        pid = entry.get("id")
        action = entry.get("action")
        from_field = entry.get("from")

        if not isinstance(pid, str) or not pid:
            errors.append(
                f"lineage.phases[{index}].id missing or not a non-empty string"
            )
            continue
        if pid in seen_phase_ids:
            errors.append(f"lineage.phases has duplicate id {pid!r}")
            continue
        seen_phase_ids.add(pid)

        if action not in _VALID_ACTIONS:
            errors.append(
                f"lineage.phases entry {pid!r}: action {action!r} is not "
                f"one of {sorted(_VALID_ACTIONS)}"
            )
            continue

        if action in _NO_FROM_ACTIONS:
            if from_field is not None:
                errors.append(
                    f"lineage.phases entry {pid!r} action={action!r} must "
                    "not include a 'from' field"
                )
            if action == "preserve":
                if pid not in prior_set:
                    errors.append(
                        f"lineage.phases entry {pid!r} action='preserve' "
                        "names an id not in the prior plan"
                    )
                else:
                    preserved_ids.add(pid)
            else:  # action == "new"
                if pid in prior_set:
                    errors.append(
                        f"lineage.phases entry {pid!r} action='new' uses "
                        "an id that already exists in the prior plan"
                    )
            continue

        # supersede / split / merge
        if pid in prior_set:
            errors.append(
                f"lineage.phases entry {pid!r} action={action!r} uses an "
                "id that already exists in the prior plan; introduce a "
                "new id for derived phases"
            )
        if not isinstance(from_field, list) or not from_field:
            errors.append(
                f"lineage.phases entry {pid!r} action={action!r} requires "
                "a non-empty 'from' list"
            )
            continue
        if action == "merge" and len(from_field) < 2:
            errors.append(
                f"lineage.phases entry {pid!r} action='merge' requires at "
                "least two prior plan ids in 'from'"
            )
        unknown = sorted({p for p in from_field if p not in prior_set})
        if unknown:
            errors.append(
                f"lineage.phases entry {pid!r} 'from' references unknown "
                f"prior plan id(s): {', '.join(unknown)}"
            )
        assert isinstance(action, str)
        for prior_id in from_field:
            if isinstance(prior_id, str) and prior_id in prior_set:
                consumed_by.setdefault(prior_id, []).append((action, pid))

    # phases[] vs plan-header ids.
    if seen_phase_ids != new_set:
        missing_in_phases = sorted(new_set - seen_phase_ids)
        extra_in_phases = sorted(seen_phase_ids - new_set)
        if missing_in_phases:
            errors.append(
                "lineage.phases is missing an entry for plan header(s): "
                + ", ".join(missing_in_phases)
            )
        if extra_in_phases:
            errors.append(
                "lineage.phases has entries with no plan header: "
                + ", ".join(extra_in_phases)
            )

    abandoned_ids: list[str] = []
    for index, entry in enumerate(abandoned):
        if not isinstance(entry, Mapping):
            errors.append(f"lineage.abandoned[{index}] is not an object")
            continue
        aid = entry.get("id")
        reason = entry.get("reason")
        if not isinstance(aid, str) or not aid:
            errors.append(
                f"lineage.abandoned[{index}].id missing or not a non-empty string"
            )
            continue
        if not isinstance(reason, str) or not reason:
            errors.append(
                f"lineage.abandoned entry {aid!r} missing 'reason' string"
            )
        if aid not in prior_set:
            errors.append(
                f"lineage.abandoned entry {aid!r} is not a prior plan id"
            )
            continue
        if aid in preserved_ids:
            errors.append(
                f"lineage.abandoned entry {aid!r} also appears as a "
                "preserved id; an abandoned phase cannot also be preserved"
            )
        if aid in consumed_by:
            errors.append(
                f"lineage.abandoned entry {aid!r} also appears in a 'from' "
                "list; an abandoned phase cannot also be consumed by "
                "supersede / split / merge"
            )
        abandoned_ids.append(aid)

    # Within-consumed contradictions:
    # Multiple split entries sharing a prior is the natural split case
    # and is allowed. Multiple supersede or merge claims on the same
    # prior, or a mix of action types claiming the same prior, are
    # contradictions.
    for prior_id, claims in consumed_by.items():
        actions_seen = {action for action, _ in claims}
        if len(claims) > 1 and actions_seen != {"split"}:
            new_ids = ", ".join(sorted({nid for _, nid in claims}))
            actions_str = "/".join(sorted(actions_seen))
            errors.append(
                f"prior id {prior_id!r} is consumed by multiple entries "
                f"({actions_str}: {new_ids}); only multiple 'split' "
                "branches may share a prior"
            )

    # Exactly-once across the three buckets:
    # preserved_set, consumed_set (any prior with >=1 consumer),
    # abandoned_set must be DISJOINT and cover prior_set.
    consumed_set = set(consumed_by)
    abandoned_set = set(abandoned_ids)

    bad_preserved = sorted(preserved_ids & consumed_set)
    if bad_preserved:
        errors.append(
            "preserved phase id(s) also appear in a 'from' list: "
            + ", ".join(bad_preserved)
        )
    # preserved/abandoned and consumed/abandoned overlaps were caught
    # above per-abandoned-entry; preserved/abandoned likewise.

    accounted = preserved_ids | consumed_set | abandoned_set
    missing = sorted(prior_set - accounted)
    if missing:
        errors.append(
            "prior plan id(s) not accounted for (must be preserved, "
            "consumed by supersede/split/merge, or abandoned): "
            + ", ".join(missing)
        )

    if errors:
        raise LineageValidationError("; ".join(errors))


def compute_lineage_diff(lineage: Mapping[str, Any]) -> LineageDiff:
    """Return a structured LineageDiff derived directly from the sidecar.

    Assumes :func:`validate_lineage` has already passed against the
    same sidecar. The diff is constructed, not inferred: every
    supersede / split / merge entry maps directly to a lifecycle
    record, and every abandoned[] entry maps to one phase_abandoned.
    Determinism is via sorted output; replay against the same
    sidecar yields the same emission order.
    """
    phases_obj = lineage.get("phases", [])
    phases: list[Any] = list(phases_obj) if isinstance(phases_obj, list) else []
    abandoned_obj = lineage.get("abandoned") or []
    abandoned: list[Any] = (
        list(abandoned_obj) if isinstance(abandoned_obj, list) else []
    )

    superseded: list[tuple[str, str]] = []
    split_buckets: dict[str, list[str]] = {}
    merged: list[tuple[list[str], str]] = []

    for entry in phases:
        action = entry.get("action")
        pid = entry["id"]
        from_field = entry.get("from") or []
        if action == "supersede":
            for prior in from_field:
                superseded.append((prior, pid))
        elif action == "split":
            for prior in from_field:
                split_buckets.setdefault(prior, []).append(pid)
        elif action == "merge":
            merged.append((list(from_field), pid))

    superseded.sort()
    split_list = [
        (old_id, sorted(new_ids))
        for old_id, new_ids in sorted(split_buckets.items())
    ]
    merged.sort(key=lambda e: (sorted(e[0]), e[1]))

    abandoned_pairs = sorted(
        (entry["id"], entry.get("reason", ""))
        for entry in abandoned
    )

    return LineageDiff(
        superseded=superseded,
        split=split_list,
        merged=merged,
        abandoned=abandoned_pairs,
    )


def _dups(items: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for x in items:
        if x in seen:
            dups.add(x)
        seen.add(x)
    return dups


__all__ = [
    "LineageDiff",
    "LineageValidationError",
    "ParsedHeader",
    "compute_lineage_diff",
    "parse_plan_phases",
    "validate_lineage",
]
