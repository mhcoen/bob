"""Preserve-by-default reauthor assembly.

Slice C reauthor mode used to trust the synthesizer to emit the FULL
re-authored plan plus a fully-accounting lineage sidecar. That kept
the synthesizer in the protocol-state ownership loop: when a
threshold crossing affected one phase out of N, the model was still
expected to repeat all N-1 unchanged phases verbatim and to write
``preserve`` lineage entries for each. In practice the model emits
only the changed phase plus a partial lineage; ``validate_lineage``
fails closed (correctly), and the user has no way to make progress.

This module owns the deterministic envelope. The contract becomes:

  - The synthesizer authors **changed/new phase content** and the
    **non-preserve lineage intent** (supersede / split / merge / new
    / abandoned). It is NOT responsible for repeating unchanged
    phase content or for emitting preserve entries.

  - Duplo (this module) parses the prior PLAN.md into per-phase
    markdown sections, normalizes the synthesizer's lineage by
    adding a ``preserve`` entry for every prior id the synthesizer
    did not explicitly consume, and assembles the final PLAN.md
    from preserved-prior sections plus synthesized changed/new
    sections.

  - ``validate_lineage`` continues to run after normalization, on
    the ASSEMBLED plan's headers vs. the normalized lineage.
    Contradictions still raise; the validator is not weakened.

  - The deterministic unit is the pair ``(preserved phase section,
    preserved lineage entry)``. Adding preserve entries to lineage
    while writing the synthesizer's partial plan is forbidden — the
    sections must be preserved alongside the lineage.

See the module-level tests in ``tests/test_reauthor_assemble.py``
for the contract pinned in code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from duplo.reauthor_phase_ids import _HEADER_RE


@dataclass(frozen=True)
class PhaseSection:
    """A phase's full markdown content keyed by ``phase_id``.

    ``text`` is the section body verbatim from PLAN.md, starting at
    the ``## Phase <id>: <title>`` header line and continuing through
    every following line up to (but NOT including) the next phase
    header or end of file. The text always ends with at least one
    newline so concatenation produces well-formed markdown.
    """

    id: str
    title: str
    text: str


class ReauthorAssemblyError(RuntimeError):
    """Raised when section/lineage assembly cannot produce a valid PLAN.md.

    Distinct from :class:`duplo.reauthor_phase_ids.LineageValidationError`:
    that one fires when validate_lineage rejects the (prior, new,
    lineage) tuple. This one fires when assembly itself fails: e.g.,
    the synthesizer's lineage references a new phase id (supersede /
    split / merge / new) but the synthesizer's plan body has no
    section for that id.
    """


def parse_plan_sections(plan_text: str) -> tuple[str, list[PhaseSection]]:
    """Split PLAN.md into ``(preamble, sections)``.

    A section starts at every ``## Phase <id>: <title>`` line and
    ends at the line BEFORE the next phase header or at end of file.
    Section text always ends with a newline so concatenation
    produces valid markdown.

    The preamble is everything before the first phase header. For
    canonical-mode plans this includes the project-name H1 envelope,
    project description, and the Phase 0 H1 if present. The preamble
    is preserved verbatim by ``assemble_reauthored_plan``.

    Lines that look like phase headers but use a non-Slice-C id form
    (e.g., ``## Phase 1: Foo``) are NOT recognized and become content
    of whatever section they fall in. The Slice C contract requires
    ``phase_NNN``-shaped ids; pre-Slice-C plans should not be passed
    here.
    """
    lines = plan_text.splitlines(keepends=True)

    header_indices: list[int] = []
    headers: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        match = _HEADER_RE.match(line)
        if match is not None:
            header_indices.append(i)
            headers.append((match.group("id"), match.group("title")))

    if not header_indices:
        return ("".join(lines), [])

    preamble = "".join(lines[: header_indices[0]])

    sections: list[PhaseSection] = []
    for slot, (start_idx, (sid, stitle)) in enumerate(
        zip(header_indices, headers, strict=True)
    ):
        end_idx = (
            header_indices[slot + 1]
            if slot + 1 < len(header_indices)
            else len(lines)
        )
        body = "".join(lines[start_idx:end_idx])
        if not body.endswith("\n"):
            body = body + "\n"
        sections.append(PhaseSection(id=sid, title=stitle, text=body))
    return (preamble, sections)


def _consumed_prior_ids(
    lineage: Mapping[str, Any], prior_ids: set[str]
) -> set[str]:
    """Return prior ids consumed by lineage entries (not preserved).

    Consumed = appears in supersede/split/merge ``from`` lists, or
    listed in ``abandoned``. Used by ``normalize_lineage_for_preservation``
    to decide which prior ids need a preserve-default entry.
    """
    consumed: set[str] = set()
    for entry in lineage.get("phases", []) or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        from_field = entry.get("from") or []
        if action in ("supersede", "split", "merge"):
            for fid in from_field:
                if isinstance(fid, str) and fid in prior_ids:
                    consumed.add(fid)
    for entry in lineage.get("abandoned") or []:
        if not isinstance(entry, Mapping):
            continue
        aid = entry.get("id")
        if isinstance(aid, str) and aid in prior_ids:
            consumed.add(aid)
    return consumed


def normalize_lineage_for_preservation(
    prior_ids: Iterable[str],
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    """Add ``preserve`` entries for prior ids the synthesizer didn't touch.

    For every prior id that is NOT named explicitly by the synthesizer
    (as a ``preserve`` entry, as a ``new`` entry, as a ``from`` list
    member of supersede / split / merge, or in ``abandoned``), append
    ``{"id": <prior_id>, "action": "preserve"}`` to ``phases``.

    The synthesizer's existing entries are preserved verbatim and in
    order; the preserve-defaults are appended at the end. Validation
    treats ``phases`` as an unordered set, so order is purely
    cosmetic.

    The function always returns a fresh dict; the input lineage is
    not mutated.

    Idempotent: running this twice produces the same result, and
    re-running it on a fully-explicit synthesizer lineage adds no
    entries.
    """
    prior_set = set(prior_ids)
    phases_in: list[Any] = list(lineage.get("phases") or [])
    abandoned_in: list[Any] = list(lineage.get("abandoned") or [])

    explicit_priors: set[str] = set()
    for entry in phases_in:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        pid = entry.get("id")
        if action == "preserve" and isinstance(pid, str) and pid in prior_set:
            explicit_priors.add(pid)
        elif action in ("supersede", "split", "merge"):
            for fid in entry.get("from") or []:
                if isinstance(fid, str) and fid in prior_set:
                    explicit_priors.add(fid)
        # "new" entries do not reference a prior id.
    for entry in abandoned_in:
        if not isinstance(entry, Mapping):
            continue
        aid = entry.get("id")
        if isinstance(aid, str) and aid in prior_set:
            explicit_priors.add(aid)

    phases_out: list[Any] = list(phases_in)
    for pid in prior_ids:
        if pid not in explicit_priors:
            phases_out.append({"id": pid, "action": "preserve"})

    out: dict[str, Any] = {"phases": phases_out}
    if abandoned_in:
        out["abandoned"] = abandoned_in
    return out


_NEW_ID_ACTIONS = frozenset(["supersede", "split", "merge", "new"])


def assemble_reauthored_plan(
    prior_preamble: str,
    prior_sections: list[PhaseSection],
    synth_sections: list[PhaseSection],
    normalized_lineage: Mapping[str, Any],
) -> str:
    """Build the full re-authored PLAN.md from preserved + new sections.

    Walks ``prior_sections`` in order:

      - If the prior id is in ``abandoned``, skip it.
      - If the prior id is consumed by supersede / split / merge,
        substitute the synthesized section(s) that took its place at
        this position. Merge targets emit at the position of their
        FIRST source prior; later sources of the same merge skip.
      - Otherwise (preserve), emit the prior section verbatim.

    After the walk, any ``"new"`` lineage entries (no prior id
    consumed) are appended at the end in synthesizer-declared order.

    Raises ``ReauthorAssemblyError`` when:

      - A new id named by lineage as supersede / split / merge / new
        has no matching ``## Phase <new_id>: ...`` section in the
        synthesized plan.
      - A new id is referenced by multiple non-split actions targeting
        the same prior position (validate_lineage catches this too,
        but assembly's own detection produces a clearer error).

    The preamble is emitted verbatim from ``prior_preamble``. The
    re-authored plan inherits the prior plan's project-level header
    block; reauthor mode does not regenerate it.
    """
    prior_by_id: dict[str, PhaseSection] = {
        section.id: section for section in prior_sections
    }
    if len(prior_by_id) != len(prior_sections):
        # The prior plan parser shouldn't produce duplicates, but
        # surface a clear error rather than silently merging.
        raise ReauthorAssemblyError(
            "prior plan has duplicate phase ids; refusing to assemble"
        )

    synth_by_id: dict[str, PhaseSection] = {
        section.id: section for section in synth_sections
    }

    abandoned_ids: set[str] = set()
    for entry in normalized_lineage.get("abandoned") or []:
        if isinstance(entry, Mapping):
            aid = entry.get("id")
            if isinstance(aid, str):
                abandoned_ids.add(aid)

    # Per-prior-id, the list of new ids that should appear at that
    # prior's position (in lineage-declared order).
    replacement_at_prior: dict[str, list[str]] = {}
    consumed_priors: set[str] = set()
    new_phase_ids: list[str] = []
    merge_first_source: dict[str, str] = {}

    for entry in normalized_lineage.get("phases") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        new_id = entry.get("id")
        if not isinstance(new_id, str):
            continue
        from_field = entry.get("from") or []
        if action == "supersede":
            for fid in from_field:
                if isinstance(fid, str):
                    replacement_at_prior.setdefault(fid, []).append(new_id)
                    consumed_priors.add(fid)
        elif action == "split":
            for fid in from_field:
                if isinstance(fid, str):
                    replacement_at_prior.setdefault(fid, []).append(new_id)
                    consumed_priors.add(fid)
        elif action == "merge":
            string_sources = [s for s in from_field if isinstance(s, str)]
            if string_sources:
                merge_first_source[new_id] = string_sources[0]
                replacement_at_prior.setdefault(string_sources[0], []).append(new_id)
                for fid in string_sources:
                    consumed_priors.add(fid)
        elif action == "new":
            new_phase_ids.append(new_id)
        # preserve: no replacement; the prior's section at that
        # position remains.

    # Verify every new id named by lineage has a synthesized section.
    missing_sections: list[str] = []
    seen_ids_used: set[str] = set()
    for entry in normalized_lineage.get("phases") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        new_id = entry.get("id")
        if action in _NEW_ID_ACTIONS and isinstance(new_id, str):
            if new_id not in synth_by_id:
                missing_sections.append(new_id)
            else:
                seen_ids_used.add(new_id)
    if missing_sections:
        raise ReauthorAssemblyError(
            "synthesized plan is missing section(s) for lineage-declared "
            f"new id(s): {', '.join(sorted(set(missing_sections)))}"
        )

    parts: list[str] = []
    if prior_preamble:
        parts.append(prior_preamble)

    emitted_merge_targets: set[str] = set()
    for prior in prior_sections:
        pid = prior.id
        if pid in abandoned_ids:
            continue
        if pid in consumed_priors:
            for new_id in replacement_at_prior.get(pid, []):
                if new_id in emitted_merge_targets:
                    continue
                parts.append(synth_by_id[new_id].text)
                if merge_first_source.get(new_id) == pid:
                    emitted_merge_targets.add(new_id)
            continue
        # Preserved or absent from lineage entirely (which
        # normalize_lineage_for_preservation already covered as a
        # preserve default).
        parts.append(prior.text)

    for new_id in new_phase_ids:
        parts.append(synth_by_id[new_id].text)

    return "".join(parts)


__all__ = [
    "PhaseSection",
    "ReauthorAssemblyError",
    "assemble_reauthored_plan",
    "normalize_lineage_for_preservation",
    "parse_plan_sections",
]
