# bob-tools

Shared bob-level infrastructure for the Bob toolchain (Duplo, McLoop,
Orchestra, Vroom). Anything that does not belong to one specific tool
but is needed across two or more lives here.

## Current contents

- **`bob_tools.ledger`** — the Plan Ledger. Append-only typed event
  log plus a deterministic projector that turns
  `PLAN.events.jsonl` into `PLAN.state.json`. Captures execution
  evidence and design reasoning so plans can be re-authored from
  the ledger rather than re-derived.

  Design docs: `design/plan-ledger.md` plus per-slice notes in
  `design/plan-ledger-slice-{b,c,d}.md`.
  Schema reference: `bob_tools/ledger/SCHEMA.md`.

## Threshold rules

`bob_tools.ledger.thresholds` ships seven rules. The evaluator's
job is to classify ledger events as no-op or "the plan needs
re-authoring"; the actual re-authoring belongs to Slice C in
`duplo.reauthor`. All seven rules ship at
`severity=trigger_reauthor` (the lower `annotate` level is reserved
for future rules that may legitimately log without recommending a
re-author).

The set is intentionally conservative per Slice B's design: false
positives desensitize, false negatives miss the window. Each rule
fires on an explicit triggering event rather than a heuristic.

**`unattributable_commit`** — a `commit_landed` event arrives with
no `attributed_phase_id`. Slice A's projector routes such commits
into `findings_unattributed`; this rule fires when that list
grows. The commit is execution work the plan does not account
for. Recommended action: `reauthor_plan`.

**`phase_abandoned`** — fires on a `phase_abandoned` event. The
event itself is the trigger: a phase the project no longer pursues
invalidates whatever the plan said about reaching its outcome.
Recommended action: `reauthor_phase`.

**`phase_superseded`** — fires on a `phase_superseded` event. Same
shape as `phase_abandoned`; the supersession event records that an
earlier phase has been replaced by a successor, and the plan must
reflect that structurally. Recommended action: `reauthor_phase`.

**`phase_topology_changed`** — fires on `phase_split` or
`phase_merged`. A topology change is its own class of
plan-invalidating event: the original decomposition has been
judged wrong (too coarse if split, too fine if merged), and phase
boundaries need to be redrawn. Recommended action:
`reauthor_phase`.

**`invariant_declared`** — fires when the projector surfaces a new
invariant on `PlanState`. New correctness invariants typically
reframe what "done" means for adjacent phases, so prior phases
authored without them in view may need revision. Recommended
action: `reauthor_plan`.

**`assumption_falsified`** — fires on an `assumption_falsified`
event. The case it catches is "next planned phase depends on
assumption falsified by execution": the phase that relied on the
assumption has lost its foundation. Recommended action:
`reauthor_phase`.

**`exploratory_count_exceeded`** — fires when the running count of
*exploratory* commits crosses `exploratory_commit_limit` (default
5, configurable). An exploratory commit is a `commit_landed` with
no `attributed_phase_id` whose `change_class` is not
`plan_artifact`; plan-refresh commits are out of scope by
construction. Recommended action: `reauthor_plan`.

The split between `reauthor_phase` and `reauthor_plan` follows the
scope of the evidence. Phase-scoped events (abandoned, superseded,
topology, assumption falsified) recommend `reauthor_phase`;
cross-cutting signals (uncovered commits, new invariants)
recommend `reauthor_plan`. Both currently route through the same
`auto_reauthor` → `duplo.reauthor.reauthor_plan` call site, so the
choice is denormalized for future use rather than acted on today.

The `since` cursor (event_id of the most recent `plan_reauthored`)
gates re-firing: only crossings whose evidence has
`event_id > since` are emitted, and for the count rule the
threshold must be crossed *after* `since` (a log already over the
limit at the cursor does not re-fire). Each successful re-author
implicitly resets the slice for the next pass.

Consumers can disable individual rules per-environment via
`ThresholdParams.enabled_rules` without code changes.

## Layout

```
bob-tools/
  pyproject.toml         editable install for the bob-tools package
  README.md              this file
  design/                slice design docs (plan-ledger.md plus slice-{b,c,d})
  bob_tools/
    __init__.py
    ledger/
      __init__.py        public re-exports
      events.py          Event/EventType, payload builders
      projector.py       events → PlanState (deterministic projection)
      storage.py         append-only event log on disk + writer-id allocation
      schema.py          JSON Schema + validator
      thresholds.py      threshold-rule evaluator + record_crossings
      _uuid7.py          local UUIDv7 generator (no external dep)
      SCHEMA.md          human-readable schema reference
      tests/             unit tests
```

## Install

```
pip install -e .
```

Adds `bob_tools` to the active Python environment as an editable
package. Consumers (Duplo, McLoop, etc.) can then `import bob_tools`.

## Quality gates

```
pytest
ruff check bob_tools
mypy --strict bob_tools
```

## License

Copyright 2026 Michael Coen. All rights reserved.
