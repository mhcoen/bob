# Plan Ledger

## Purpose

Maintain causal synchronization between PLAN.md and implementation
evidence. PLAN.md alone is a static specification; it cannot record
why phases were abandoned, split, superseded, or added in response
to findings. The Plan Ledger is the structured record from which
PLAN.md is re-authored.

## Position in Bob

Bob is a design-control system for AI-assisted software engineering.
Code generation is one actuator. The Plan Ledger is the state-
tracking layer between Duplo (authors initial plan), McLoop
(executes), Orchestra (adjudicates decisions inside execution), and
Vroom (retrospective audit).

The ledger does not own design. It owns the *record* of what
happened, against which PLAN.md is the human-readable surface.

## Three modes of operation

1. **Canonical.** Duplo-authored PLAN.md exists before McLoop
   starts. Ledger records execution against the plan.

2. **Exploratory.** No upstream plan. Findings accumulate in the
   ledger. When enough structure emerges, Duplo synthesizes a plan
   or mini-plan from the ledger.

3. **Repair.** Canonical plan exists, but execution evidence
   invalidates part of it. Threshold-crossing triggers Duplo
   re-authoring with the ledger as input.

## Bounded integration

Planning and coding remain distinct as responsibilities. Re-
authoring goes through Duplo, not through McLoop directly. McLoop
never edits PLAN.md; it emits events to the ledger and pauses on
threshold-crossing for Duplo invocation.

## Three artifacts

- `PLAN.md` — human-readable current plan of record. Re-authored at
  thresholds. Not the source of truth.
- `PLAN.state.json` — machine-readable plan state: phase IDs,
  statuses, lineage, modification history, commit links, evidence
  links, supersession relations, abandonment reasons.
- `PLAN.events.jsonl` — append-only event ledger. Every nontrivial
  McLoop action emits one event.

The ledger (events.jsonl + state.json) is the source of truth.
PLAN.md is derived from it.

## Plan Steward responsibility

The Plan Steward owns the invariant: every nontrivial unit of work
performed by McLoop must be either covered by the current plan,
recorded as exploratory/provisional, or explicitly marked as
divergence.

This is a responsibility, not a tool. Initial implementation home:
McLoop, since it is the only component watching execution. Promote
to standalone component if scope grows.

## Implementation order

Four shippable slices, each independently useful.

### Slice A. Event schema + ledger files (observability only)

McLoop emits typed events to `PLAN.events.jsonl`. No behavior
change. Pure logging.

Event types (initial set):
- `phase_started` — phase ID, timestamp
- `phase_completed` — phase ID, commits, artifacts
- `phase_abandoned` — phase ID, reason
- `commit_landed` — commit hash, attributed phase ID or null
- `test_failed` — test ID, phase ID, failure summary
- `finding_observed` — free-form finding, evidence pointer
- `invariant_declared` — new correctness invariant, source

PLAN.state.json initial schema:
- `phases[]` — { id, status, lineage, evidence_refs, modification_history }
- `status` enum: pending, active, completed, abandoned, superseded, split, merged, blocked, provisional
- `lineage` — predecessor phase IDs, supersession relations

### Slice B. Threshold rules

A ruleset that classifies events as annotate-only vs trigger-re-
author. Initial rules conservative — only fire on explicit triggers:

- Commit not attributable to any phase.
- Phase abandoned by McLoop or human.
- New correctness invariant declared.
- Prior fix exposes new failure class.
- Next planned phase depends on assumption falsified by execution.
- More than N exploratory commits accumulate without promotion.

When a threshold fires, McLoop emits a `threshold_crossed` event
and notifies the Plan Steward. Threshold firing does not yet
trigger re-authoring — Slice B only alerts.

### Slice C. Duplo re-author mode

Duplo gains a re-author mode that takes (current PLAN.md, ledger
slice since last re-author, triggering event) and emits an updated
PLAN.md plus updated PLAN.state.json. Re-authoring is itself
Orchestra-mediated (council or propose-critique-synthesize) for
design rigor.

Output preserves lineage: every new phase ID either matches an old
one or carries explicit supersession metadata pointing at the old
phase IDs it replaces.

### Slice D. McLoop pause-on-threshold

McLoop pauses execution on threshold-crossing, invokes Duplo re-
author, accepts new plan, resumes. The loop is closed.

## What this enables

- Audit trail of how a plan evolved across implementation, not just
  what was originally planned.
- Vroom consumes the ledger for richer retrospective: it sees both
  shipped code and plan evolution, not just code.
- Exploratory work has a structured home that does not require
  forced fake rigor (a Duplo plan up front when none is justified).
- Threshold-crossing re-authoring replaces ad-hoc human-mediated
  consultation as the mechanism for plan revision.

## Open questions

1. Where does Plan Steward live in the codebase? McLoop subsystem
   for Slice A; reassess after C.
2. PLAN.state.json schema — JSON Schema definition needed before
   Slice A ships.
3. Threshold rule expression: code, configuration, or both?
4. Re-author invocation: synchronous (McLoop blocks) or
   asynchronous (McLoop continues with provisional state)?
5. How does the ledger interact with Git history? PLAN.md commits
   are part of Git; PLAN.state.json and PLAN.events.jsonl probably
   should be too. Verify before Slice A.
6. Concurrency: multiple McLoop instances writing to the same
   PLAN.events.jsonl. Append-only with line-buffered writes is the
   simplest discipline; verify it survives reasonable contention.
