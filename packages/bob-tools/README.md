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

`bob_tools.ledger.thresholds` ships seven rules at
`severity=trigger_reauthor`:

| Rule | Trigger | Action |
|---|---|---|
| `unattributable_commit` | `commit_landed` with `attributed_phase_id == None` | `reauthor_plan` |
| `phase_abandoned` | a `phase_abandoned` event | `reauthor_phase` |
| `phase_superseded` | a `phase_superseded` event | `reauthor_phase` |
| `phase_topology_changed` | a `phase_split` or `phase_merged` event | `reauthor_phase` |
| `invariant_declared` | a new invariant on the projected `PlanState` | `reauthor_plan` |
| `assumption_falsified` | an `assumption_falsified` event | `reauthor_phase` |
| `exploratory_count_exceeded` | exploratory-commit count crosses `exploratory_commit_limit` (default 5) | `reauthor_plan` |

An exploratory commit is a `commit_landed` with no
`attributed_phase_id` whose `change_class` is not `plan_artifact`;
plan-refresh commits are out of scope by construction.

The first six rules fire once per evidence event. The seventh
fires once at the limit-crossing transition. The `since` cursor
(event_id of the most recent `plan_reauthored`) gates re-firing:
only crossings whose evidence has `event_id > since` are emitted,
and for the count rule the threshold must be crossed *after*
`since` (a log already over the limit at the cursor does not
re-fire).

Severity `annotate` and action `log_only` are reserved for future
rules; nothing currently emits them. Consumers can disable
individual rules per-environment via `ThresholdParams.enabled_rules`
without code changes.

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
