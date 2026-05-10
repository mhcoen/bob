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
    units, normalizes the synthesizer's lineage by adding a
    ``preserve`` entry for every prior id the synthesizer did not
    explicitly consume, and assembles the final plan from preserved
    prior units plus synthesized changed/new units.

  - ``validate_lineage`` continues to run after normalization, on
    the ASSEMBLED plan's headers vs. the normalized lineage.
    Contradictions still raise; the validator is not weakened.

  - The deterministic unit is the pair ``(preserved phase unit,
    preserved lineage entry)``. Adding preserve entries to lineage
    while writing the synthesizer's partial plan is forbidden — the
    units must be preserved alongside the lineage.

Structural ownership note. The H1 envelope + H2 phase header pair
that defines a phase unit is owned by :mod:`duplo.plan_document`.
This module never concatenates H1 lines by hand; it produces a
:class:`~duplo.plan_document.Plan` via :func:`assemble_reauthored_plan`
and lets the renderer write the structural metadata. That is the
only path that emits H1 envelopes: assembly cannot leave a stale
"Phase 2" H1 above a substituted ``phase_010`` H2 because render
derives every H1 ordinal from the final unit position.

See the module-level tests in ``tests/test_reauthor_assemble.py``
for the contract pinned in code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from duplo.plan_document import (
    PhaseUnit,
    Plan,
    parse_plan,
)


# Backwards-compatible alias. ``PhaseSection`` was the per-phase shape
# used before plan_document took structural ownership; the new shape is
# :class:`PhaseUnit`, which adds the H1 envelope. External callers that
# imported ``PhaseSection`` from this module continue to work, but the
# ``text`` field they may have read is no longer present — body and
# headers are split into separate fields.
PhaseSection = PhaseUnit


class ReauthorAssemblyError(RuntimeError):
    """Raised when section/lineage assembly cannot produce a valid PLAN.md.

    Distinct from :class:`duplo.reauthor_phase_ids.LineageValidationError`
    (synthesizer lineage contract) and from
    :class:`duplo.plan_document.StructuralValidationError` (assembled
    plan structural invariants). This one fires when assembly's own
    rules cannot be satisfied: e.g., the lineage names a new phase id
    (supersede / split / merge / new) but the synthesizer's plan body
    has no unit for that id.
    """


def parse_plan_sections(
    plan_text: str,
) -> tuple[str, list[PhaseUnit]]:
    """Thin wrapper around :func:`duplo.plan_document.parse_plan`.

    Returns ``(preamble, units)`` so existing callers that expected
    a ``(preamble, sections)`` tuple keep working. The list element
    type is now :class:`PhaseUnit`; the ``text`` attribute the old
    PhaseSection carried (full ``## Phase ...`` line + body) is split
    into ``phase_id``, ``h2_title``, and ``body``. The plan-document
    parser is strict: missing H2 under an H1, multiple H2s under one
    H1, or non-whitespace text between H1 and H2 raises
    :class:`~duplo.plan_document.ParseError`. The fswatch-run-smoke
    corruption shape (one H1 with several H2s + embedded verdict
    JSON) fails fast at this boundary rather than silently
    accumulating across reauthor passes.
    """
    plan = parse_plan(plan_text)
    return (plan.preamble, list(plan.units))


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
    prior_plan: Plan,
    synth_units: Iterable[PhaseUnit],
    normalized_lineage: Mapping[str, Any],
) -> Plan:
    """Build the re-authored :class:`Plan` from preserved + new units.

    Walks ``prior_plan.units`` in order and, for each prior unit:

      - If its phase_id is in ``abandoned``, the unit is dropped.
      - If its phase_id is consumed by a supersede / split / merge,
        the synthesized unit(s) that took its place are emitted at
        this position. Merge targets emit at the position of their
        FIRST source prior; later sources of the same merge are
        skipped. Splits emit every branch at the prior position.
      - Otherwise (preserve, explicit or default), the prior unit
        is emitted verbatim.

    After the walk, every ``"new"`` lineage entry (no prior id
    consumed) is appended at the end in synthesizer-declared order.

    The returned Plan inherits ``project_name`` and ``preamble`` from
    ``prior_plan``. H1 ordinals are NOT stored on the Plan; they are
    derived by :func:`~duplo.plan_document.render` from each unit's
    final position. Substituting a unit therefore renumbers every
    downstream H1 deterministically.

    Raises
    ------
    ReauthorAssemblyError
        When a new id named by lineage as supersede / split / merge /
        new has no matching unit in ``synth_units``. The synthesizer
        declared the lineage intent without authoring the unit body.
    """
    if len({u.phase_id for u in prior_plan.units}) != len(prior_plan.units):
        raise ReauthorAssemblyError(
            "prior plan has duplicate phase ids; refusing to assemble"
        )

    synth_by_id: dict[str, PhaseUnit] = {}
    for unit in synth_units:
        synth_by_id[unit.phase_id] = unit

    abandoned_ids: set[str] = set()
    for entry in normalized_lineage.get("abandoned") or []:
        if isinstance(entry, Mapping):
            aid = entry.get("id")
            if isinstance(aid, str):
                abandoned_ids.add(aid)

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
        # preserve: no replacement; the prior's unit at that
        # position remains.

    missing_units: list[str] = []
    for entry in normalized_lineage.get("phases") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        new_id = entry.get("id")
        if action in _NEW_ID_ACTIONS and isinstance(new_id, str):
            if new_id not in synth_by_id:
                missing_units.append(new_id)
    if missing_units:
        raise ReauthorAssemblyError(
            "synthesized plan is missing unit(s) for lineage-declared "
            f"new id(s): {', '.join(sorted(set(missing_units)))}"
        )

    out_units: list[PhaseUnit] = []
    emitted_merge_targets: set[str] = set()
    for prior in prior_plan.units:
        pid = prior.phase_id
        if pid in abandoned_ids:
            continue
        if pid in consumed_priors:
            for new_id in replacement_at_prior.get(pid, []):
                if new_id in emitted_merge_targets:
                    continue
                out_units.append(synth_by_id[new_id])
                if merge_first_source.get(new_id) == pid:
                    emitted_merge_targets.add(new_id)
            continue
        out_units.append(prior)

    for new_id in new_phase_ids:
        out_units.append(synth_by_id[new_id])

    return Plan(
        project_name=prior_plan.project_name,
        preamble=prior_plan.preamble,
        units=tuple(out_units),
    )


__all__ = [
    "PhaseSection",
    "PhaseUnit",
    "ReauthorAssemblyError",
    "assemble_reauthored_plan",
    "normalize_lineage_for_preservation",
    "parse_plan_sections",
]
