# Plan Ledger Slice B: Threshold Rules

## Purpose

Slice A produced the event ledger and the projected PlanState.
Slice B classifies events as annotate-only or trigger-re-author.
The re-authoring itself is Slice C; Slice B's job ends at deciding
whether re-authoring should happen and surfacing the decision.

## Architectural shape

Two layers, per Codex's earlier recommendation on the parallel
threshold question:

1. Threshold policy lives in code. What a threshold means, how it
   composes with PlanState, what it considers a meaningful
   crossing.
2. Threshold parameters live in configuration. Numeric values,
   feature flags, mode-specific multipliers.

This split mirrors Plan Ledger's overall design: the policy of
"no route should accept with failed required criteria" lives in
runtime; the criterion list lives in config.

## Initial conservative rule set

Per Codex (earlier review) and ChatGPT, fire only on explicit
triggers in Slice B. False positives desensitize; false negatives
miss the window. Conservative wins.

Seven rules to ship in Slice B:

1. Unattributable commit. A `commit_landed` event arrives with no
   attributable phase. Slice A already routes this to
   `findings_unattributed`. Slice B reads from PlanState, sees the
   list grew, and emits a `threshold_crossed` event.

2. Phase abandoned. Any `phase_abandoned` event triggers. Trivial
   classifier: the event itself is the trigger.

3. Phase superseded. Any `phase_superseded` event triggers. Same
   shape as #2.

4. Phase topology changed. Any `phase_split` or `phase_merged`
   event triggers. Codex's review flagged the prior spec's
   handwave that split/merged "fold into invariant/assumption" —
   a topology change is its own class of plan-invalidating event
   and must be explicit, not implicit.

5. New invariant declared. Any `invariant_declared` event
   triggers. New correctness invariants typically reframe what
   "done" means for adjacent phases.

6. Assumption falsified. Any `assumption_falsified` event
   triggers. Falsified assumptions are exactly the case "next
   planned phase depends on assumption falsified by execution"
   from Codex's threshold list.

7. Exploratory commit count exceeded. When the count of
   `findings_unattributed` (or `commit_landed` with `change_class
   != plan_artifact` and no phase attribution) exceeds N, trigger.
   N is configurable, default 5.

These seven map onto Codex's earlier list. Codex's "prior fix
exposes a new failure class" folds into rule 5 (the new failure
class is declared as a new invariant) or rule 6 (the prior
assumption is falsified).

## Watcher placement

Three options:

(i) Inline in Storage.append. Every successful append runs the
    threshold check synchronously. Pro: lowest-latency detection,
    no separate process. Con: couples threshold semantics to write
    path; threshold evaluation cost is paid by every writer;
    awkward failure semantics (if threshold evaluation fails, did
    append fail?).

(ii) Separate watcher process that polls the ledger. Pro: write
     path stays minimal. Con: latency between event and threshold
     detection; another process to deploy.

(iii) On-demand via CLI / API call. A consumer (McLoop, human,
      future Plan Steward) explicitly asks "any thresholds crossed
      since last_event_id X?" Pro: no implicit work; full control
      over when checks run. Con: thresholds only fire when someone
      asks; no automatic re-author triggering.

For Slice B, **(iii) on-demand** per Codex review.

Codex's framing: "evaluate_thresholds(state, params, since=None)
is the right unit. McLoop/Plan Steward can later call it inline,
periodically, or before handoff without changing threshold
semantics."

The on-demand evaluator can be wired into any of (i), (ii), or
McLoop's loop later without changing its semantics.

## API shape

    from bob_tools.ledger import Storage, project, PlanState
    from bob_tools.ledger.thresholds import (
        evaluate_thresholds,
        ThresholdCrossing,
        ThresholdParams,
    )

    storage = Storage(ledger_dir)
    state = project(storage.read_all())
    crossings = evaluate_thresholds(
        state,
        params=ThresholdParams(exploratory_commit_limit=5),
        since=None,
    )
    for crossing in crossings:
        # ThresholdCrossing has: rule_id, severity, evidence_event_ids,
        # human_readable_summary, recommended_action
        ...

A separate function `record_crossings(storage, crossings)` writes
`threshold_crossed` events back to the ledger so the audit trail
captures both the crossing and (eventually, in Slice C) the
response.

## ThresholdCrossing shape

    {
      rule_id: <enum: unattributable_commit | phase_abandoned |
                      phase_superseded | phase_topology_changed |
                      invariant_declared | assumption_falsified |
                      exploratory_count_exceeded>,
      severity: <enum: annotate | trigger_reauthor>,
      evidence_event_ids: [<event_id>],
      recommended_action: <enum: log_only | reauthor_phase |
                                 reauthor_plan>,
      summary: <human-readable string>,
      detected_at_event_id: <event_id of latest event in state
                             when the crossing was detected>,
    }

severity=annotate rules log a `threshold_crossed` event but do not
propose re-authoring. Slice B ships all seven rules at
severity=trigger_reauthor because the conservative set is already
tightly scoped; severity exists for future rule additions that may
legitimately be annotate-only.

## ThresholdParams

    {
      exploratory_commit_limit: int = 5,
      enabled_rules: set[str] = {all seven rule_ids},
      // Future: per-rule severity overrides, mode-specific
      // multipliers
    }

Loaded from .orchestra/config.json or equivalent at the consuming
project level. Slice B accepts the params as a dataclass; wiring
to a config file is consumer-side.

## `since` semantics

Per Codex: opaque event_id owned by caller. Slice B is stateless.
Different consumers may have different cursors and policies (CLI,
McLoop, human review, future steward). Putting per-consumer
cursors into projected state would make state consumer-dependent.

Codex's exact tightening: "since should mean 'only crossings whose
evidence includes events with event_id > since,' with
deterministic ordering by event id. For count-based rules like
exploratory commits, the crossing should fire when the threshold
is crossed after since, not merely because total count is above N."

Implication for the count-based rule (rule 7): evaluate_thresholds
must know the count as of `since` to decide whether the threshold
was crossed AFTER that point. Two implementation approaches:

(a) Re-project up to `since` to get historical state, compare
    against current state, fire if crossed in the interval.
(b) PlanState retains enough history (per-event count snapshots
    or a time-series of unattributed_count) for retroactive
    comparison without re-projection.

(a) is simpler and correct; (b) is faster but requires PlanState
schema changes. Recommend (a) for Slice B unless profiling shows
it dominates evaluation cost.

## Determinism contract

Codex confirmed: encode determinism tests for evaluate_thresholds
in the same shape as the projector's contract.

Required test invariants:

- Same crossings regardless of input event ordering. Sort key:
  event_id.
- Independent of ts. Two inputs with identical event sequences but
  different ts produce identical crossings.
- Shuffle-invariant: evaluate_thresholds(project(shuffle(events)))
  == evaluate_thresholds(project(sorted(events))).
- Deterministic crossing order in the returned list. Sort by
  detected_at_event_id then rule_id.

## Test surface (mandatory)

For each of the seven rules:

- Positive test: condition holds → crossing emitted with correct
  rule_id, evidence_event_ids, severity.
- Negative test: condition does not hold → no crossing.
- Idempotence test: calling evaluate_thresholds twice on the same
  state produces the same crossings (no spurious detection).

Plus:

- `since` parameter test: crossings before `since` are not
  re-emitted; crossings after `since` are emitted. Specifically
  test the count-based rule (rule 7): if exploratory count was 3
  at `since` and is 6 now (limit=5), crossing fires once. If count
  was 8 at `since` and is still 8 now, crossing does NOT fire.
- Multi-rule test: a single state with multiple triggering events
  returns all relevant crossings in deterministic order.
- Empty-state test: evaluate_thresholds on a fresh PlanState
  returns no crossings.
- Disabled-rule test: `enabled_rules` excludes a rule → that
  rule's crossings are not emitted even when the condition
  triggers.
- Determinism tests: four invariants from the section above,
  encoded as explicit tests.

Estimated test count: 30-35.

## Files to create

    bob_tools/ledger/thresholds.py
    bob_tools/ledger/tests/test_thresholds.py

Plus updates to:

    bob_tools/ledger/__init__.py     # re-export evaluate_thresholds,
                                     #   ThresholdCrossing, ThresholdParams
    bob_tools/ledger/SCHEMA.md       # threshold rule documentation

## Quality gates

ruff, mypy --strict, pytest. Same discipline as Slice A.

## What Slice B does NOT do

- Does NOT fire `plan_reauthored` events. That's Slice C
  (re-authoring via Duplo).
- Does NOT auto-pause McLoop. McLoop integration is its own
  workstream.
- Does NOT change PlanState shape beyond reading existing fields.
  Slice A's PlanState is sufficient for threshold evaluation,
  modulo any per-event-count history additions for the count-based
  rule (see `since` semantics above).
- Does NOT introduce a separate watcher process. Pure function
  `evaluate_thresholds` is the deliverable.
- Does NOT persist consumer cursors. `since` is caller-owned.
