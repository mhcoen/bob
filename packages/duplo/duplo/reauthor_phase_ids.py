"""PLAN.md phase identifier and lineage metadata parser.

Slice C of the Plan Ledger requires each phase header in PLAN.md to
carry a stable phase identifier so the ledger can track lineage
across re-authorings. The format is documented in the
``council_synthesizer`` template (orchestra) and in
``bob-tools/design/plan-ledger-slice-c.md``:

    ## Phase phase_001: Bring up scaffold

    ## Phase phase_002a: Refactor auth (split)
    <!-- split_from: phase_002 -->

    ## Phase phase_merged_x: Combined feature flag system
    <!-- merge_from: phase_003, phase_004 -->

This module parses headers and lineage metadata, validates that
every new phase id either preserves a prior id or carries explicit
``supersedes:`` / ``split_from:`` / ``merge_from:`` metadata, and
raises ``LineageValidationError`` (fail-closed, no silent repair)
on violation.

The header regex deliberately requires the ``Phase <id>:`` shape:

    ^##\\s+Phase\\s+(?P<id>[A-Za-z0-9_]+):\\s+(?P<title>.+)$

Pre-Slice C plans whose headers do not follow this format can still
be parsed (no phase ids extracted, treated as fresh-author input).
The first re-author run labels output with phase_NNN: prefixes;
subsequent runs validate lineage against those.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)
_LINEAGE_COMMENT_RE = re.compile(
    r"^<!--\s*"
    r"(?P<keyword>supersedes|split_from|merge_from)\s*:\s*"
    r"(?P<ids>[A-Za-z0-9_,\s]+)"
    r"\s*-->\s*$"
)


class LineageValidationError(ValueError):
    """Raised when a re-authored plan violates lineage rules.

    Slice C fails closed: silent repair invents lineage and is
    exactly the failure mode the explicit metadata is meant to
    prevent. The error message names the offending phase ids.
    """


@dataclass
class ParsedPhase:
    """One phase header parsed out of PLAN.md.

    ``supersedes`` / ``split_from`` / ``merge_from`` are the prior
    phase ids referenced by HTML-comment metadata immediately
    following the header. They are mutually exclusive; the validator
    rejects a phase that declares more than one kind of lineage.
    """

    id: str
    title: str
    header_line_index: int
    supersedes: list[str] = field(default_factory=list)
    split_from: list[str] = field(default_factory=list)
    merge_from: list[str] = field(default_factory=list)

    @property
    def has_lineage_claim(self) -> bool:
        return bool(self.supersedes or self.split_from or self.merge_from)

    def predecessor_ids(self) -> list[str]:
        """All prior ids this phase claims to derive from."""
        return list(self.supersedes) + list(self.split_from) + list(self.merge_from)


def parse_plan_phases(plan_text: str) -> list[ParsedPhase]:
    """Parse phase headers and immediately-following lineage comments.

    Lines that do not match the phase header regex are ignored.
    Lineage metadata is recognized only on the line directly after
    the header; intervening blank lines or other content end the
    metadata block. Multiple metadata comments under one header are
    accumulated (e.g., a phase could carry both ``supersedes`` and
    ``split_from`` claims, though the validator rejects mixed
    claims).
    """
    lines = plan_text.splitlines()
    phases: list[ParsedPhase] = []
    i = 0
    while i < len(lines):
        match = _HEADER_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        phase = ParsedPhase(
            id=match.group("id"),
            title=match.group("title"),
            header_line_index=i,
        )
        # Scan immediately-following lines for lineage HTML comments.
        # Any non-comment, non-blank line ends the metadata block.
        j = i + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if not stripped:
                j += 1
                continue
            comment_match = _LINEAGE_COMMENT_RE.match(stripped)
            if comment_match is None:
                break
            keyword = comment_match.group("keyword")
            ids = [
                token.strip()
                for token in comment_match.group("ids").split(",")
                if token.strip()
            ]
            if keyword == "supersedes":
                phase.supersedes.extend(ids)
            elif keyword == "split_from":
                phase.split_from.extend(ids)
            elif keyword == "merge_from":
                phase.merge_from.extend(ids)
            j += 1
        phases.append(phase)
        i = j
    return phases


def validate_lineage(
    old_phases: Iterable[ParsedPhase],
    new_phases: Iterable[ParsedPhase],
) -> None:
    """Fail closed if new phases violate lineage rules.

    Rules:

    1. Every new phase_id that is NOT present in old_phases must
       carry exactly one kind of lineage metadata (supersedes,
       split_from, or merge_from), and every prior id it references
       must exist in old_phases.

    2. A phase that declares more than one kind of lineage metadata
       (e.g., supersedes AND split_from) is invalid. Pick one.

    Elision is NOT a validation error: a prior phase id with no
    successor claim is recorded as ``phase_abandoned`` by the
    Slice C emitter, not rejected.
    """
    old_ids = {p.id for p in old_phases}
    new_ids = {p.id for p in new_phases}

    errors: list[str] = []

    for new_phase in new_phases:
        # Mixed-claim check.
        kinds = sum(
            bool(getattr(new_phase, attr))
            for attr in ("supersedes", "split_from", "merge_from")
        )
        if kinds > 1:
            errors.append(
                f"phase {new_phase.id!r} declares more than one kind of "
                "lineage metadata; pick exactly one of supersedes / "
                "split_from / merge_from"
            )
            continue

        # Preserved id: nothing more to check.
        if new_phase.id in old_ids:
            if new_phase.has_lineage_claim:
                errors.append(
                    f"phase {new_phase.id!r} preserves an existing id but "
                    "also declares lineage metadata; preserved phases must "
                    "have no supersedes / split_from / merge_from claim"
                )
            continue

        # Brand-new id: must carry lineage metadata pointing at prior
        # ids that exist in old_phases.
        if not new_phase.has_lineage_claim:
            errors.append(
                f"phase {new_phase.id!r} is a new id with no lineage "
                "metadata; declare supersedes / split_from / merge_from "
                "or reuse a preserved phase id from the prior plan"
            )
            continue

        for prior_id in new_phase.predecessor_ids():
            if prior_id not in old_ids:
                errors.append(
                    f"phase {new_phase.id!r} references prior id "
                    f"{prior_id!r} via lineage metadata but no such phase "
                    "exists in the prior plan"
                )

    # Duplicate phase id detection in the new plan.
    seen: set[str] = set()
    for new_phase in new_phases:
        if new_phase.id in seen:
            errors.append(
                f"phase id {new_phase.id!r} appears more than once in the "
                "new plan"
            )
        seen.add(new_phase.id)

    # Suppress unused locals lint when no validation paths fire.
    _ = new_ids

    if errors:
        joined = "; ".join(errors)
        raise LineageValidationError(joined)


@dataclass
class LineageDiff:
    """Structured lineage change between an old and new phase set.

    The Slice C emitter consumes this to produce the lifecycle
    events (``phase_superseded`` / ``phase_split`` / ``phase_merged``
    / ``phase_abandoned``) that are appended to the ledger before
    ``plan_reauthored``.

    ``elided`` lists prior ids that the new plan dropped without an
    explicit supersession claim. They are recorded as
    ``phase_abandoned`` by the emitter (not rejected by the
    validator).
    """

    superseded: list[tuple[str, str]] = field(default_factory=list)
    """List of (old_id, new_id) for explicit supersessions."""

    split: list[tuple[str, list[str]]] = field(default_factory=list)
    """List of (old_id, [new_id, ...]) for splits."""

    merged: list[tuple[list[str], str]] = field(default_factory=list)
    """List of ([old_id, ...], new_id) for merges."""

    elided: list[str] = field(default_factory=list)
    """Prior phase ids dropped by elision."""


def compute_lineage_diff(
    old_phases: Iterable[ParsedPhase],
    new_phases: Iterable[ParsedPhase],
) -> LineageDiff:
    """Return the structured lineage difference, sorted deterministically.

    Assumes ``validate_lineage`` has already passed. Emits one
    ``superseded`` entry per ``supersedes:`` claim, one ``split``
    entry per unique parent in any ``split_from:`` claim (with all
    new branches grouped), one ``merged`` entry per ``merge_from:``
    claim, and one ``elided`` entry per prior id with no successor
    in any of the above.
    """
    old_phases_list = list(old_phases)
    new_phases_list = list(new_phases)
    old_ids = {p.id for p in old_phases_list}
    new_ids = {p.id for p in new_phases_list}

    superseded: list[tuple[str, str]] = []
    merged: list[tuple[list[str], str]] = []
    split_buckets: dict[str, list[str]] = {}
    successors_claimed: set[str] = set()

    for new_phase in new_phases_list:
        for prior_id in new_phase.supersedes:
            superseded.append((prior_id, new_phase.id))
            successors_claimed.add(prior_id)
        for prior_id in new_phase.split_from:
            split_buckets.setdefault(prior_id, []).append(new_phase.id)
            successors_claimed.add(prior_id)
        if new_phase.merge_from:
            merged.append((list(new_phase.merge_from), new_phase.id))
            successors_claimed.update(new_phase.merge_from)

    split_list = [
        (old_id, sorted(new_ids_for_old))
        for old_id, new_ids_for_old in sorted(split_buckets.items())
    ]
    superseded.sort()
    merged.sort(key=lambda entry: (sorted(entry[0]), entry[1]))

    elided = sorted(
        old_id
        for old_id in old_ids
        if old_id not in new_ids and old_id not in successors_claimed
    )

    return LineageDiff(
        superseded=superseded,
        split=split_list,
        merged=merged,
        elided=elided,
    )


__all__ = [
    "LineageDiff",
    "LineageValidationError",
    "ParsedPhase",
    "compute_lineage_diff",
    "parse_plan_phases",
    "validate_lineage",
]
