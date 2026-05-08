# Plan Ledger event schema

This is the human-readable reference for `bob/ledger/events.py` and
`bob/ledger/schema.py`. The authoritative shape is the JSON Schema in
`schema.py`; this file should match it.

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

## Slice A boundary

Slice A ships:

- `events.py` — Event/EventType dataclasses, payload builders.
- `schema.py` — JSON Schema, validator.
- `_uuid7.py` — UUIDv7 generator.
- This file.

Slice A part 2 (next surface) ships:

- `storage.py` — append-only writer, writer_id allocation, seq
  persistence, atomic-append discipline.
- `projector.py` — pure events -> PlanState replay.
- `PlanState` schema (separate review surface before code lands).
