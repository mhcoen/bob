# bob-tools

bob-tools is the determinism layer of the [bob ecosystem](https://github.com/mhcoen/bob),
a deterministic control plane for stochastic agents. Everything in the
ecosystem — McLoop, Duplo, Orchestra, Vroom — is built on top of two
libraries that live here: `bob_tools.planfile`, which owns the formal
`PLAN.md` grammar and the deterministic mutation API; and
`bob_tools.ledger`, the append-only event log and threshold-rule
evaluator that captures execution evidence and signals when plans need
re-authoring.

## Why bob-tools exists

The bob ecosystem's central claim is that a deterministic framework
around stochastic actors is what makes autonomous coding work. A
language model produces fluent, confident output that is wrong often
enough to matter; the framework's job is to make sure nothing the
model produces becomes state until something that is not a model has
checked it. That promise depends on two artifacts being precise: the
plan the system is executing, and the log of what it actually did.

`PLAN.md` is the writable surface — what the system is supposed to
do. If `PLAN.md` is a casual markdown checklist, every consumer
(McLoop running tasks, Duplo authoring phases, Orchestra coordinating
multi-model edits) reimplements the same parsing slightly differently
and the boundary between "agent stochastic" and "control plane
deterministic" dissolves at every consumer. `bob_tools.planfile`
refuses that outcome: there is one parser, one validator, one
canonical-form renderer, and one set of sanctioned mutation
operations. A consumer that wants to mark a task complete cannot do
it by rewriting markdown — it calls `complete_task`, which refuses
invalid transitions and returns a `Settlement` record describing
exactly what changed. The agent never edits `PLAN.md` directly;
only the library's mutation path does, and that path enforces every
invariant the grammar requires.

The Plan Ledger is the append-only witness — what the system
actually did. Every meaningful action — a task completion, a commit,
a check outcome, a phase transition, a re-author event — is recorded
as a typed event with a stable id, tagged back to the `PLAN.md`
element that caused it. A deterministic projector turns the event
stream into a `PlanState` snapshot, so the projection is replayable
and auditable; the ledger is the substrate Vroom stands on. Threshold
rules evaluate the stream and emit `threshold_crossed` events when
the plan needs re-authoring, so the decision to regenerate a plan is
made from explicit signals rather than a heuristic.

Both libraries live here together because neither belongs to one
specific tool, and splitting them would force every consumer to pull
two packages that already share assumptions about identity (`T-NNNNNN`
task ids, `phase_NNN` phase ids), about canonicalization (the
rendered form a tool can re-parse and reason about), and about what
counts as evidence (the Settlement records the planfile API returns
are what the ledger consumes). Anything else that does not belong to
one specific tool but is needed across two or more — shared
schemas, cross-cutting utilities — lives here too.

## Contents

### `bob_tools.planfile` — the formal PLAN.md API

`PLAN.md` is a structured document with a defined grammar: stages with
stable phase identifiers, tasks with stable `T-NNNNNN` identifiers,
structured annotations (`[feat: ...]`, `[fix: ...]`, `[BATCH]`,
`[RULEDOUT]`), and a canonical form that the parser, validator, and
renderer agree on byte-for-byte. The library is the single owner of
that grammar.

A small canonical plan, after the library has assigned task IDs and
rendered it through `render_plan`:

```markdown
<!-- bob-plan-format: 1 -->

# Build a thing

A one-paragraph project description. Everything from the H1 down to
the first `## Stage` heading is prose and survives canonicalization.

## Stage 1: Scaffolding
<!-- phase_id: phase_001 -->

- [x] T-000001: create the package layout
- [ ] T-000002: [BATCH] wire the CLI entry point
  - [x] T-000003: add `__main__.py`
  - [ ] T-000004: register the console_script in pyproject.toml
    @deps T-000003
- [ ] T-000005: [USER] verify `pip install -e .` works on a clean venv

## Stage 2: First feature
<!-- phase_id: phase_002 -->

- [ ] T-000006: parse the input file [feat: "input parsing"]
  [RULEDOUT] regex-based parser — fails on nested quotes
- [!] T-000007: render the output [feat: "output rendering"]

## Bugs

- [ ] T-000099: crash on empty input file [fix: "empty-file guard"]
```

Every element above is part of the grammar, not convention: the
magic-line comment, the H1, the phase headings with their `phase_id`
comments, the `T-NNNNNN` identifiers, the checkbox markers
(`- [ ]` / `- [x]` / `- [!]`), the in-task tags (`[USER]`, `[BATCH]`,
`[AUTO:...]`), the `[feat: ...]` and `[fix: ...]` annotations, the
`[RULEDOUT]` sibling lines, the `@deps` sibling lines, the
`## Bugs` section. The parser recognises each by position and form
and rejects anything outside the grammar.

What the planfile API guarantees:

- **Parse-then-validate.** `parse_plan` reads markdown and produces a
  typed `Plan` object (with `Phase`, `Task`, `Outcome`, `BugsSection`,
  `RuledOut`, and friends). `validate_plan` checks the structural
  invariants the grammar requires: task IDs are unique, parents only
  check off after their children, bug-section structure is well-formed,
  no stage references a missing phase, no dangling annotations.
  Malformed plans are rejected at the API boundary; consumers never
  see an invalid `Plan`.

- **Canonical form.** `render_plan` and `canonicalize` produce a
  byte-stable output from any valid `Plan`. The file round-trips
  through canonical form on every save. Diffs between runs reflect
  semantic changes, not whitespace, ordering, or trailing-newline drift.

- **Deterministic mutations.** `complete_task`, `fail_task`, `add_task`,
  `add_phase_task`, `add_bug_task`, `reset_task`, `clear_failed`,
  `replace_phase`, `replace_phase_validated`, `purge_done_bug_tasks`,
  and `migrate` are the only sanctioned ways to change plan state.
  Each refuses invalid transitions: you cannot complete a task whose
  subtasks are incomplete, cannot duplicate an ID, cannot break a
  phase boundary. The operations return `Settlement` records that
  describe what changed, so callers can journal mutations to the
  ledger without re-deriving them.

- **Concurrent-update detection.** `load`, `save`, and `update`
  implement an mtime+content check so two writers racing on the same
  `PLAN.md` produce a `ConcurrentUpdateError` rather than a silent
  last-write-wins clobber.

- **mcloop canonicality assertion.** `assert_mcloop_canonical` is a
  precondition McLoop applies before any mutation, so the plan is
  guaranteed to be in canonical form when mcloop touches it. This is
  how McLoop's "agent stochastic, control plane deterministic"
  contract is enforced at the file boundary.

- **Artifact sanitisation.** `sanitize_plan_artifact` is the planfile's
  intake for LLM-produced or human-edited fragments — content that
  may have well-meaning but invalid structure. It either coerces the
  fragment into canonical form or rejects it with a structured error
  via `PlanArtifactRejected`. There is no "almost valid" middle state.

The full public surface is enumerated in
`bob_tools/planfile/__init__.py`. The design reference is
[`design/planfile.md`](../../design/planfile.md) at the workspace
root.

### `bob-plan` — the planfile CLI

`pip install` adds a `bob-plan` script to your environment. Five
subcommands, all operating on a `PLAN.md` path:

```
bob-plan validate PATH                  # parse and validate; exit 1 on failure
bob-plan fmt PATH                       # load, migrate, save in canonical form
bob-plan next PATH                      # print the next actionable T-NNNNNN: text
bob-plan done PATH TASK_ID              # validate, complete, save; emit Settlements as JSON
bob-plan fail PATH TASK_ID --reason ... # validate, fail, save; emit the Settlement as JSON
```

Exit codes are uniform across subcommands: `0` on success, `1` on
parse or validation failure, `2` if a referenced task id is not in
the plan, `3` on any other error. Errors go to stderr; JSON payloads
from `done` and `fail` go to stdout so the output can be piped into
the ledger or another tool. This is what makes `bob-plan` composable
into scripts and CI workflows.

### `bob_tools.ledger` — the Plan Ledger

The Plan Ledger is an append-only typed event log plus a deterministic
projector that turns `PLAN.events.jsonl` into `PLAN.state.json`. It is
the bob ecosystem's audit trail and the substrate for self-improvement:
every action the system takes — every task completion, every commit,
every check outcome, every phase transition — is recorded as a typed
event, with each event tagged to the `PLAN.md` element that caused it.

The ledger captures execution evidence and design reasoning so plans
can be re-authored from the ledger rather than re-derived from the
codebase. When McLoop commits, when Duplo reauthors, when Vroom
reflects (in the designed system), the ledger is what they read and
what they write.

The schema lives in `bob_tools/ledger/SCHEMA.md`. The design docs are
`design/plan-ledger.md` plus per-slice notes
(`design/plan-ledger-slice-{b,c,d}.md`).

#### Threshold rules

`bob_tools.ledger.thresholds` ships seven rules. The evaluator's job
is to classify ledger events as no-op or "the plan needs
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
into `findings_unattributed`; this rule fires when that list grows.
The commit is execution work the plan does not account for.
Recommended action: `reauthor_plan`.

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
boundaries need to be redrawn. Recommended action: `reauthor_phase`.

**`invariant_declared`** — fires when the projector surfaces a new
invariant on `PlanState`. New correctness invariants typically
reframe what "done" means for adjacent phases, so prior phases
authored without them in view may need revision. Recommended action:
`reauthor_plan`.

**`assumption_falsified`** — fires on an `assumption_falsified`
event. The case it catches is "next planned phase depends on
assumption falsified by execution": the phase that relied on the
assumption has lost its foundation. Recommended action:
`reauthor_phase`.

**`exploratory_count_exceeded`** — fires when the running count of
*exploratory* commits crosses `exploratory_commit_limit` (default 5,
configurable). An exploratory commit is a `commit_landed` with no
`attributed_phase_id` whose `change_class` is not `plan_artifact`;
plan-refresh commits are out of scope by construction. Recommended
action: `reauthor_plan`.

The split between `reauthor_phase` and `reauthor_plan` follows the
scope of the evidence. Phase-scoped events (abandoned, superseded,
topology, assumption falsified) recommend `reauthor_phase`;
cross-cutting signals (uncovered commits, new invariants) recommend
`reauthor_plan`. Both currently route through the same `auto_reauthor`
→ `duplo.reauthor.reauthor_plan` call site, so the choice is
denormalized for future use rather than acted on today.

The `since` cursor (event_id of the most recent `plan_reauthored`)
gates re-firing: only crossings whose evidence has `event_id > since`
are emitted, and for the count rule the threshold must be crossed
*after* `since` (a log already over the limit at the cursor does not
re-fire). Each successful re-author implicitly resets the slice for
the next pass.

Consumers can disable individual rules per-environment via
`ThresholdParams.enabled_rules` without code changes.

## Layout

```
bob-tools/
  pyproject.toml             editable install for the bob-tools package
  README.md                  this file
  design/                    slice design docs (plan-ledger.md plus slice-{b,c,d})
  bob_tools/
    __init__.py
    planfile/
      __init__.py            public re-exports — the planfile API surface
      model.py               typed Plan / Phase / Task / Outcome / etc.
      parser.py              markdown → Plan
      renderer.py            Plan → canonical markdown
      operations.py          deterministic mutations (complete, fail, add, ...)
      fileio.py              load / save / update with concurrent-update detection
      plan_artifact.py       sanitize_plan_artifact + PlanArtifactRejected
      cli.py                 bob-plan entry point
      tests/                 unit tests
    ledger/
      __init__.py            public re-exports
      events.py              Event / EventType, payload builders
      projector.py           events → PlanState (deterministic projection)
      storage.py             append-only event log on disk + writer-id allocation
      schema.py              JSON Schema + validator
      thresholds.py          threshold-rule evaluator + record_crossings
      _uuid7.py              local UUIDv7 generator (no external dep)
      SCHEMA.md              human-readable schema reference
      tests/                 unit tests
```

## Install

bob-tools is part of the bob workspace. The natural way to install it
is via the workspace:

```bash
git clone https://github.com/mhcoen/bob.git
cd bob
uv sync
```

This installs every workspace package — including bob-tools — in
editable mode, with internal cross-package dependencies resolved
locally. `bob-plan` lands on `PATH`.

For standalone development on bob-tools alone:

```bash
git clone https://github.com/mhcoen/bob-tools.git
cd bob-tools
pip install -e '.[dev]'
```

Either way, consumers can then `import bob_tools` and the `bob-plan`
CLI is available.

Requires Python 3.12 or newer.

## Quality gates

```
pytest
ruff check bob_tools
mypy --strict bob_tools
```

`mypy --strict` and `ruff` both run clean over the entire `bob_tools`
package on every commit. The library is the determinism substrate for
the rest of the ecosystem; it stays clean by policy, not by
aspiration.

## License

Copyright 2026 Michael Coen. All rights reserved.
