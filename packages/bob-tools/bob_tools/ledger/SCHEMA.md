# Plan Ledger event schema

This is the human-readable reference for `bob_tools/ledger/events.py`
and `bob_tools/ledger/schema.py`. The authoritative shape is the JSON
Schema in `schema.py`; this file should match it.

## Envelope

Every event in `PLAN.events.jsonl` is one JSON object on one line:

```
{
  "event_id":       <UUIDv7 string>,
  "seq":            <non-negative int>,
  "ts":             <ISO 8601 UTC microsecond, e.g. "2026-05-08T06:00:00.000000Z">,
  "writer_id":      <stable string>,
  "run_id":         <string>,
  "schema_version": "1.0",
  "type":           <event-type enum>,
  "git": {
    "commit":   <sha or null>,
    "branch":   <string or null>,
    "dirty":    <bool or null>,
    "worktree": <path or null>
  },
  "payload": <type-specific object>
}
```

- `event_id` is a UUIDv7. The 48-bit time prefix gives roughly
  global ordering across writers; the trailing 74 random bits make
  collisions vanishingly unlikely. Schema validation enforces the
  version and variant nibbles via the `ledger-event-id` format
  checker.
- `seq` is monotonic per `writer_id`. The replay tiebreaker is
  `(writer_id, seq)`. A writer that crashes and resumes must
  persist its seq counter so resumed events do not collide with
  earlier ones.
- `ts` is for human audit; do not use it for replay ordering.
- `writer_id` identifies which McLoop instance (or other writer)
  emitted the event. Stable across restarts of the same process
  tree; document allocation in `storage.py` (Slice A part 2).
- `run_id` scopes events to a single McLoop run; one writer may host
  multiple runs sequentially.
- `schema_version` is `"1.0"` for Slice A. Bump on breaking changes.
- `git` is always present; fields are nullable for processes that
  emit events outside a checkout.
- `payload` shape is governed by `type`.

## Replay ordering

Sort by `event_id`. Break ties on equal `event_id` (which should not
happen in practice given UUIDv7's 74 random bits, but is well-defined)
by `(writer_id, seq)`. Do not sort by `ts`. The projector replays in
this order; a partial event log replays to the same partial state
regardless of which writer's events are present.

## Concurrency

Writers append to `PLAN.events.jsonl` via `O_APPEND`, one `write()`
call per event. POSIX serializes writes <= `PIPE_BUF` (~4KB on Linux,
larger on macOS). For events larger than `PIPE_BUF`, callers must use
the `storage.append()` entry point so the storage layer can serialize
appropriately. File locking is a Slice B follow-up if needed.

Two writers can interleave events in the file; the read-time sort by
`event_id` recovers a deterministic replay order. Per-writer seq is
persisted at `<ledger_dir>/.writers/<writer_id>.seq`.

## Active event types (Slice A: 16)

The projector applies all of these.

### `phase_started`

```
{
  "phase_id": "<stable string>",
  "title": "<string>",
  "goal": <string or null>,
  "predecessor_phase_ids": [<phase_id>, ...]
}
```

A new phase enters the plan. Status starts at `pending`; the
projector flips it to `active` lazily on the first attributed
`commit_landed` or `work_observed`.

### `phase_completed`

```
{
  "phase_id": "<string>",
  "commit_event_ids": [<event_id>, ...],
  "artifact_paths": [<string>, ...]
}
```

### `phase_abandoned`

```
{
  "phase_id": "<string>",
  "reason": "<string>"
}
```

### `phase_blocked`

```
{
  "phase_id": "<string>",
  "reason": "<string>",
  "blocker_event_id": <event_id or null>
}
```

### `phase_superseded`

```
{
  "phase_id": "<string>",
  "superseded_by_phase_id": "<string>",
  "reason": "<string>"
}
```

### `phase_split`

```
{
  "phase_id": "<string>",
  "into_phase_ids": [<string>, <string>, ...],   // at least 2
  "reason": "<string>"
}
```

### `phase_merged`

```
{
  "merged_phase_ids": [<string>, <string>, ...], // at least 2
  "into_phase_id": "<string>",
  "reason": "<string>"
}
```

### `commit_landed`

```
{
  "commit": "<sha>",
  "parent_commits": [<sha>, ...],
  "branch": <string or null>,
  "author": "<string>",
  "subject": "<string>",
  "attributed_phase_id": <phase_id or null>,
  "files_changed": <int>,
  "lines_added": <int>,
  "lines_removed": <int>,
  "change_class": <code|plan_artifact|test|docs|infra|mixed|unknown>,
  "touched_paths": [<path>, ...]   // optional
}
```

`change_class` distinguishes code commits from plan-artifact commits
without proliferating top-level event types. Slice B's threshold rules
will discriminate on this field.

### `test_failed`

```
{
  "test_id": "<string>",
  "phase_id": <phase_id or null>,
  "failure_kind": "<string>",
  "summary": "<string>",
  "transcript_ref": <string or null>
}
```

### `finding_observed`

```
{
  "summary": "<string>",
  "phase_id": <phase_id or null>,
  "evidence_ref": <string or null>,
  "tags": [<string>, ...]
}
```

### `invariant_declared`

```
{
  "invariant_id": "<string>",
  "statement": "<string>",
  "source": "<string>",
  "phase_id": <phase_id or null>
}
```

### `assumption_declared`

```
{
  "assumption_id": "<string>",
  "statement": "<string>",
  "phase_id": <phase_id or null>,
  "confidence": <high|medium|low>
}
```

### `assumption_falsified`

```
{
  "assumption_id": "<string>",      // links a prior assumption_declared
  "evidence_event_id": "<event_id>",
  "summary": "<string>"
}
```

### `work_observed`

```
{
  "summary": "<string>",
  "phase_id": <phase_id or null>,
  "evidence_ref": <string or null>
}
```

### `human_decision_recorded`

```
{
  "decision_id": "<string>",
  "summary": "<string>",
  "rationale": "<string>",
  "decided_by": "<string>",
  "applies_to_phase_ids": [<phase_id>, ...]
}
```

### `design_reasoning_recorded`

```
{
  "decision_id": "<string>",
  "linked_event_id": "<event_id>",     // REQUIRED, not nullable
  "rationale": "<string>",
  "constraints": [<string>, ...],
  "approaches_rejected": [
    {"approach": "<string>", "reason": "<string>"},
    ...
  ]
}
```

`linked_event_id` is required, not nullable: design reasoning must
attach to a concrete prior ledger event so the projector can resolve
it to a phase. Silent non-attribution is forbidden; the projector
fails or orphans (recommended: orphan into `PlanState.orphaned`) if
the link cannot be resolved at replay time.

## Reserved event types (Slice A: 2; Slice B applies them)

Schemas validate. Slice A's projector records that they occurred (via
`last_event_id`) but does not project state from them.

### `threshold_crossed`

```
{
  "rule_id": "<string>",
  "triggering_event_ids": [<event_id>, ...],
  "summary": "<string>"
}
```

### `plan_reauthored`

```
{
  "from_plan_commit": "<sha>",
  "to_plan_commit": "<sha>",
  "ledger_slice_event_ids": [<event_id>, ...],
  "trigger_event_id": "<event_id>",
  "council_run_id": <string or null>
}
```

## Phase status enum

The projector maintains one status per phase. The full enum:

- `pending` — `phase_started` emitted, no observed work yet.
- `active` — projector observed an attributed `commit_landed` or
  `work_observed`. Lazy: no `phase_activated` event.
- `completed` — `phase_completed` emitted.
- `abandoned` — `phase_abandoned` emitted.
- `superseded` — `phase_superseded` emitted; lineage carries a
  `supersession` block pointing at the replacement.
- `split` — `phase_split` emitted; lineage's `successors` lists the
  resulting phases.
- `merged` — input phases of `phase_merged` go to this status; the
  resulting `into_phase_id` phase is `active` once it sees its first
  observed work.
- `blocked` — `phase_blocked` emitted.
- `provisional` — reserved for Slice C/D async re-author. No Slice A
  event transitions to this.

## PLAN.state.json

The projected state. Built by `bob_tools.ledger.projector.project`
from a sequence of events. One JSON object:

```
{
  "schema_version":              "1.0",
  "last_event_id":               <event_id of most recent applied event or null>,
  "last_event_seq_per_writer":   {"<writer_id>": <highest applied seq>, ...},
  "writer_ids_seen":             [<writer_id>, ...],
  "phases":                      [<PhaseRecord>, ...],
  "invariants":                  [<InvariantRecord>, ...],
  "assumptions":                 [<AssumptionRecord>, ...],
  "human_decisions":             [<HumanDecisionRecord>, ...],
  "findings_unattributed":       [<event_id>, ...],
  "orphaned_design_reasoning":   [<event_id>, ...],
  "orphaned_design_reasoning_count": <int, mirrors list length>
}
```

`last_event_seq_per_writer` records, per writer, the highest seq seen
on a successfully-applied event. Slice A treats every event reaching
`project()` as successfully applied — including the two reserved
types whose semantics are deferred. Validation-rejected events never
reach `project()` and so do not appear here. Per Codex's Slice A
tightening (T1).

`orphaned_design_reasoning_count` mirrors the list's length and is
written explicitly so any CLI / diagnostic surface can report orphan
volume without scanning the array. Per Codex's Slice A tightening
(T2).

### PhaseRecord

```
{
  "id":                    "<stable phase id>",
  "title":                 "<string>",
  "goal":                  <string or null>,
  "status":                <pending|active|completed|abandoned|superseded
                           |split|merged|blocked|provisional>,
  "created_event_id":      "<event_id of the phase_started>",
  "lineage": {
    "predecessors": [<phase_id>, ...],
    "successors":   [<phase_id>, ...],
    "supersession": null | {"superseded_by_id": <phase_id>, "reason": <string>}
  },
  "evidence_refs":          [<event_id>, ...],
  "modification_history":   [<event_id>, ...],
  "design_reasoning_refs":  [<event_id>, ...]
}
```

`evidence_refs` collects `commit_landed`, `test_failed`,
`finding_observed`, and `work_observed` events that name this phase.
`modification_history` collects the lifecycle events that changed the
phase's status: `phase_started`, `phase_completed`, `phase_abandoned`,
`phase_blocked`, `phase_superseded`, `phase_split`, `phase_merged`.
`design_reasoning_refs` collects `design_reasoning_recorded` events
whose `linked_event_id` resolved to this phase at projection time.

`status` becomes `active` lazily on the first attributed
`commit_landed` or `work_observed`. `provisional` is reserved for
Slice C/D async re-author and is never reached by Slice A events.

### InvariantRecord, AssumptionRecord, HumanDecisionRecord

Top-level records. Each carries a `<thing>_id`, the originating event
id (`declared_event_id` / `decided_event_id`), and the payload-level
fields. `AssumptionRecord` additionally carries `falsified`,
`falsified_event_id`, and `falsified_summary` which an
`assumption_falsified` event mutates in place.

### Replay determinism

`project(events)` is a pure function. The output is invariant under:

- **Input order**: `project(shuffle(events)) == project(events)` for
  any permutation. The projector sorts by `(event_id, writer_id,
  seq)` internally.
- **Timestamp shifts**: changing `ts` on every event does not affect
  state. `ts` is for human audit only; the projector never reads it.
- **Concurrent writers**: two writers with overlapping or skewed
  timestamps project to the same state regardless of clock drift.
- **Reserved events**: adding `threshold_crossed` or
  `plan_reauthored` to a log changes only `last_event_id`,
  `last_event_seq_per_writer`, and `writer_ids_seen`. All other
  collections are unaffected at Slice A.

These four invariants are encoded in `tests/test_projector.py`
(`TestDeterminism`).

## Threshold rules (Slice B)

`bob_tools.ledger.thresholds.evaluate_thresholds` is a pure
on-demand classifier. Given the projected `PlanState` plus the
events that produced it, it returns a list of `ThresholdCrossing`
records describing rules that have fired since an opaque caller-
owned cursor.

```
crossings = evaluate_thresholds(
    state, events, params, since=None
)
```

`since` is a UUIDv7 event_id; only crossings with evidence newer
than `since` are emitted. For count-based rules, the crossing
fires only when the threshold is crossed AFTER `since` (count was
below limit at `since` and is at or above limit now), not on a
state where the count was already above the limit before the
cursor.

The evaluator is stateless. Different consumers (CLI, McLoop,
human review, future Plan Steward) own their own cursors. No state
is added to PLAN.state.json for thresholds at Slice B; the
crossings list is computed each call.

### ThresholdCrossing

```
{
  "rule_id":              <enum: see below>,
  "severity":             <annotate | trigger_reauthor>,
  "evidence_event_ids":   [<event_id>, ...],
  "recommended_action":   <log_only | reauthor_phase | reauthor_plan>,
  "summary":              "<human-readable>",
  "detected_at_event_id": "<event_id of the triggering event>"
}
```

### Rules shipped in Slice B

All seven fire at `severity=trigger_reauthor`. `severity=annotate`
is reserved for future rules.

| rule_id                       | recommended_action | trigger                                                   |
| ----------------------------- | ------------------ | --------------------------------------------------------- |
| `unattributable_commit`       | `reauthor_plan`    | `commit_landed` with `attributed_phase_id == null`        |
| `phase_abandoned`             | `reauthor_phase`   | one `phase_abandoned` event                               |
| `phase_superseded`            | `reauthor_phase`   | one `phase_superseded` event                              |
| `phase_topology_changed`      | `reauthor_phase`   | one `phase_split` or `phase_merged` event                 |
| `invariant_declared`          | `reauthor_plan`    | one `invariant_declared` event                            |
| `assumption_falsified`        | `reauthor_phase`   | one `assumption_falsified` event                          |
| `exploratory_count_exceeded`  | `reauthor_plan`    | count of unattributed non-plan-artifact `commit_landed` reaches `exploratory_commit_limit` (default 5) |

Rule 7 ("exploratory commit") explicitly excludes
`commit_landed` events whose `change_class == plan_artifact` and
those with a non-null `attributed_phase_id`. Plan-artifact commits
are the plan being refreshed, not exploratory work that escaped it.

### ThresholdParams

```
{
  "exploratory_commit_limit": <int, default 5>,
  "enabled_rules":            <set[rule_id], default ALL_RULES>
}
```

A rule absent from `enabled_rules` is skipped entirely. Other
rules continue to evaluate normally.

### Determinism contract

The evaluator inherits the projector's contract:

- Same crossings regardless of input event order. Sort key:
  `event_id`.
- Independent of `ts`. The evaluator never reads it.
- Shuffle-invariant on the projector composition:
  `evaluate_thresholds(project(shuffle), shuffle)` ==
  `evaluate_thresholds(project(sorted), sorted)`.
- Returned list is sorted by `(detected_at_event_id, rule_id)`.

These four invariants are encoded in
`tests/test_thresholds.py::TestDeterminism`.

### What Slice B does NOT do

- Does NOT write back to the ledger. `record_crossings(storage,
  crossings)` lands later (Slice B part 2 or Slice C).
- Does NOT auto-pause McLoop or auto-trigger re-authoring.
- Does NOT mutate PlanState or add fields to PLAN.state.json.

## Slice A boundary

Slice A ships:

- `events.py` — Event/EventType dataclasses, payload builders.
- `schema.py` — JSON Schema, validator.
- `_uuid7.py` — UUIDv7 generator.
- `projector.py` — `PlanState` dataclasses + pure `project()` function.
- `storage.py` — append-only writer, writer_id allocation, per-writer
  seq persistence, atomic-append discipline.
- This file.
- `tests/test_events.py`, `tests/test_projector.py`, `tests/test_storage.py`.

Slice B (next workstream) lands threshold rules on top of the
existing schema. Reserved events become active there.
