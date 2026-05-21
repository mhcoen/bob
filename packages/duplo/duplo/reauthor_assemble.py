"""Preserve-by-default reauthor assembly via bob_tools.planfile.

Slice C reauthor mode used to trust the synthesizer to emit the FULL
re-authored plan plus a fully-accounting lineage sidecar. That kept
the synthesizer in the protocol-state ownership loop: when a
threshold crossing affected one phase out of N, the model was still
expected to repeat all N-1 unchanged phases verbatim and to write
``preserve`` lineage entries for each. In practice the model emits
only the changed phase plus a partial lineage; ``validate_lineage``
fails closed (correctly), and the user has no way to make progress.

This module owns the deterministic envelope. The contract is:

  - The synthesizer authors **changed/new phase content** and the
    **non-preserve lineage intent** (supersede / split / merge / new
    / abandoned). It is NOT responsible for repeating unchanged
    phase content or for emitting preserve entries.

  - Duplo (this module) parses the prior PLAN.md into typed
    :class:`bob_tools.planfile.Phase` values, normalizes the
    synthesizer's lineage by adding a ``preserve`` entry for every
    prior id the synthesizer did not explicitly consume, and
    assembles the final plan from preserved prior phases plus
    synthesized changed/new phases.

  - ``validate_lineage`` continues to run after normalization, on
    the ASSEMBLED plan's phase ids vs. the normalized lineage.
    Contradictions still raise; the validator is not weakened.

Phase C Increment 12 (T-000192) migrated the assembly path off
``duplo.plan_document``: parsing, rendering, and substitution all go
through :mod:`bob_tools.planfile`. The 1:1 substitution primitive is
:func:`bob_tools.planfile.replace_phase_validated`; split/merge/new/
abandoned cases compose around that primitive by tuple manipulation,
with the canonical-mode validators (``validate_plan(constructed=True)``
and ``assert_mcloop_canonical``) enforcing the result.

See the module-level tests in ``tests/test_reauthor_assemble.py``
for the contract pinned in code.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

from bob_tools.planfile import (
    Phase,
    Plan,
    RuledOut,
    Subsection,
    Task,
    make_task,
    migrate,
    replace_phase_validated,
)


class ReauthorAssemblyError(RuntimeError):
    """Raised when section/lineage assembly cannot produce a valid PLAN.md.

    Distinct from :class:`duplo.reauthor_phase_ids.LineageValidationError`
    (synthesizer lineage contract). This one fires when assembly's own
    rules cannot be satisfied: e.g., the lineage names a new phase id
    (supersede / split / merge / new) but the synthesizer's plan body
    has no phase for that id.
    """


_NEW_ID_ACTIONS = frozenset(["supersede", "split", "merge", "new"])


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


def rebuild_task_constructed(task: Task) -> Task:
    """Rebuild ``task`` via :func:`make_task` so it is field-stable.

    The synthesizer's plan body comes through
    :func:`bob_tools.planfile.parse_plan`, which attaches source
    positions and may absorb stray prose into ``trailing_lines``. The
    constructed-mode validator in
    :func:`bob_tools.planfile.validate_plan` rejects nonempty
    ``trailing_lines`` and runs a per-task field-stability harness.
    Rebuilding each task with :func:`make_task` and the canonical
    structural fields (text, status, tags, annotations, deps,
    ruled_out, children) drops position metadata and forces
    ``trailing_lines=()`` so the result is a constructed plan, not a
    parsed one.

    Children are rebuilt recursively. Task ids on the parsed tasks are
    typically ``None`` because the synthesizer template does not author
    them; :func:`bob_tools.planfile.migrate` (called by the caller)
    assigns ids on the rebuilt plan later.
    """
    rebuilt_children = tuple(rebuild_task_constructed(child) for child in task.children)
    rebuilt_ruled_out = tuple(
        RuledOut(text=ruled.text, line_number=0) for ruled in task.ruled_out
    )
    return make_task(
        task.text,
        status=task.status,
        flag_tags=task.flag_tags,
        action_tag=task.action_tag,
        annotations=task.annotations,
        deps=task.deps,
        children=rebuilt_children,
        ruled_out=rebuilt_ruled_out,
        task_id=task.task_id,
    )


def rebuild_phase_constructed(phase: Phase, *, ordinal: int) -> Phase:
    """Rebuild ``phase`` with constructed-mode tasks and stable ordinal.

    Normalizes ``phase_id_source="explicit_header"`` to
    ``"explicit_comment"`` so the renderer emits a
    ``<!-- phase_id: ... -->`` line that survives round-trip under
    constructed-mode field-stability checks.
    """
    rebuilt_tasks = tuple(rebuild_task_constructed(t) for t in phase.tasks)
    rebuilt_subsections = tuple(
        Subsection(
            title=sub.title,
            prose=sub.prose,
            tasks=tuple(rebuild_task_constructed(t) for t in sub.tasks),
            line_number=0,
        )
        for sub in phase.subsections
    )
    phase_id_source = phase.phase_id_source
    if phase_id_source == "explicit_header":
        phase_id_source = "explicit_comment"
    return Phase(
        phase_id=phase.phase_id,
        phase_id_source=phase_id_source,
        ordinal=ordinal,
        keyword=phase.keyword,
        title=phase.title,
        prose=phase.prose,
        subsections=rebuilt_subsections,
        tasks=rebuilt_tasks,
        line_number=0,
    )


def assemble_reauthored_plan(
    prior_plan: Plan,
    synth_phases: Iterable[Phase],
    normalized_lineage: Mapping[str, Any],
) -> Plan:
    """Build the re-authored :class:`Plan` from preserved + new phases.

    Walks ``prior_plan.phases`` in order and, for each prior phase:

      - If its phase_id is in ``abandoned``, the phase is dropped.
      - If its phase_id is consumed by a supersede / split / merge,
        the synthesized phase(s) that took its place are emitted at
        this position. Merge targets emit at the position of their
        FIRST source prior; later sources of the same merge are
        skipped. Splits emit every branch at the prior position.
      - Otherwise (preserve, explicit or default), the prior phase
        is emitted verbatim.

    After the walk, every ``"new"`` lineage entry (no prior id
    consumed) is appended at the end in synthesizer-declared order.

    The 1:1 supersede substitution is materialized via
    :func:`bob_tools.planfile.replace_phase_validated`, which assigns
    missing task ids, normalizes the ordinal to the replaced slot, and
    validates the surrounding plan in constructed mode. Split, merge,
    new, and abandoned cases are not 1:1 and compose around the
    primitive by direct phases-tuple manipulation; the final plan is
    re-numbered and handed back for the caller to gate through
    :func:`bob_tools.planfile.assert_mcloop_canonical`.

    Returned plan carries ``magic_version=1`` so the canonical gate
    accepts it. Project title, preamble, and the bugs section are
    inherited verbatim from ``prior_plan``.

    Raises
    ------
    ReauthorAssemblyError
        When a new id named by lineage as supersede / split / merge /
        new has no matching phase in ``synth_phases``; when the
        synthesizer emitted two phases with the same id; or when the
        prior plan carries duplicate phase ids (which would make the
        lineage map ambiguous).
    """
    prior_phase_ids = [p.phase_id for p in prior_plan.phases if p.phase_id is not None]
    if len(set(prior_phase_ids)) != len(prior_phase_ids):
        raise ReauthorAssemblyError(
            "prior plan has duplicate phase ids; refusing to assemble"
        )

    synth_by_id: dict[str, Phase] = {}
    for phase in synth_phases:
        if phase.phase_id is None:
            raise ReauthorAssemblyError(
                "synthesized phase is missing phase_id; the council "
                "emitted a phase without a stable identifier, which "
                "the assembly path cannot key by lineage"
            )
        if phase.phase_id in synth_by_id:
            # Last-write-wins on the dict would silently drop the
            # earlier phase's body; the canonical validator on the
            # assembled plan can't see the duplicate because only one
            # survives. Surface the violation here, before any
            # downstream layer has a chance to mask it.
            raise ReauthorAssemblyError(
                "synthesized plan has duplicate phase id "
                f"{phase.phase_id!r}; the council emitted two phases "
                "for the same phase id, which is a model output "
                "error"
            )
        synth_by_id[phase.phase_id] = phase

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
                # Every merge source records the merge target as its
                # replacement; only the first-source slot actually
                # emits the target (tracked by ``merge_first_source``),
                # while later-source slots see the target in
                # ``emitted_merge_targets`` and drop themselves
                # without leaking the consumed prior into the output.
                for fid in string_sources:
                    replacement_at_prior.setdefault(fid, []).append(new_id)
                    consumed_priors.add(fid)
        elif action == "new":
            new_phase_ids.append(new_id)
        # preserve: no replacement; the prior's phase at that
        # position remains.

    missing_phases: list[str] = []
    for entry in normalized_lineage.get("phases") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        new_id = entry.get("id")
        if action in _NEW_ID_ACTIONS and isinstance(new_id, str):
            if new_id not in synth_by_id:
                missing_phases.append(new_id)
    if missing_phases:
        raise ReauthorAssemblyError(
            "synthesized plan is missing phase(s) for lineage-declared "
            f"new id(s): {', '.join(sorted(set(missing_phases)))}"
        )

    # Rebuild the prior plan's phases for constructed-mode field
    # stability. Phases that came through ``parse_plan`` carry
    # ``trailing_lines`` and parser-side ``line_number`` values; the
    # constructed-mode validator rejects both. Rebuilding clears
    # those positional artifacts while preserving task content
    # verbatim. Tasks with existing ids keep them; ``migrate`` below
    # assigns ids to any task that never had one so the substituted
    # plan satisfies ``validate_plan(constructed=True)`` at every
    # ``replace_phase_validated`` step.
    rebuilt_prior_phases = tuple(
        rebuild_phase_constructed(phase, ordinal=index + 1)
        for index, phase in enumerate(prior_plan.phases)
    )
    working_plan = dataclasses.replace(prior_plan, phases=rebuilt_prior_phases)
    if working_plan.magic_version is None:
        # The canonical reauthor output is a constructed plan; the
        # magic line is required for strict-mode parse on read-back.
        working_plan = dataclasses.replace(working_plan, magic_version=1)
    working_plan = migrate(working_plan)

    one_to_one_supersedes: dict[str, str] = {}
    for prior_id, new_ids in replacement_at_prior.items():
        if (
            len(new_ids) == 1
            and merge_first_source.get(new_ids[0]) is None
        ):
            # Genuine 1:1 supersede (or a single-source split or a
            # single-source 'merge' that the validator rejects later;
            # we treat all of these uniformly here since the lineage
            # validator catches the malformed cases elsewhere).
            one_to_one_supersedes[prior_id] = new_ids[0]

    for prior_id, new_id in one_to_one_supersedes.items():
        working_plan = replace_phase_validated(
            working_plan,
            prior_id,
            synth_by_id[new_id],
            assign_missing_ids=True,
            preserve_position=True,
        )

    # After 1:1 supersedes have run, walk the (partially substituted)
    # plan and apply the remaining transformations: drop abandoned
    # priors; expand multi-branch substitutions (split, merge); skip
    # already-substituted priors (handled by replace_phase_validated
    # above); preserve everything else verbatim.
    out_phases: list[Phase] = []
    emitted_merge_targets: set[str] = set()
    for phase in working_plan.phases:
        pid = phase.phase_id
        if pid is None:
            out_phases.append(phase)
            continue
        if pid in abandoned_ids:
            continue
        if pid in one_to_one_supersedes:
            # The replace_phase_validated call above already swapped
            # this slot; the phase we're iterating IS the new one.
            out_phases.append(phase)
            continue
        replacements = replacement_at_prior.get(pid, [])
        if replacements:
            for new_id in replacements:
                if new_id in emitted_merge_targets:
                    continue
                out_phases.append(synth_by_id[new_id])
                if merge_first_source.get(new_id) == pid:
                    emitted_merge_targets.add(new_id)
            continue
        out_phases.append(phase)

    # Append any genuinely new phases.
    for new_id in new_phase_ids:
        out_phases.append(synth_by_id[new_id])

    # Renumber ordinals 1..N (canonical for constructed plans).
    renumbered = tuple(
        dataclasses.replace(phase, ordinal=index + 1)
        for index, phase in enumerate(out_phases)
    )

    assembled = dataclasses.replace(working_plan, phases=renumbered)

    # Assign stable T-NNNNNN ids to any task that came through the
    # preserve path without one (legacy PLAN.md inputs do not carry
    # ids on every task). ``migrate`` preserves existing ids; tasks
    # already assigned by ``replace_phase_validated`` above are left
    # untouched. After migrate the plan satisfies the
    # constructed-mode requirement that every task has a stable id,
    # which the canonical gate (``assert_mcloop_canonical``) enforces
    # via its R2 equivalent.
    return migrate(assembled)


__all__ = [
    "ReauthorAssemblyError",
    "assemble_reauthored_plan",
    "normalize_lineage_for_preservation",
    "rebuild_phase_constructed",
    "rebuild_task_constructed",
]
