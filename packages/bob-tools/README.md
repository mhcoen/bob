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

Two grammar features worth calling out. Markdown code fences are
first-class: a ``` line toggles a fence at any indent, everything
inside is verbatim example content (a `- [ ]` line inside a fence is
never a task), and an unclosed fence at end-of-file is a loud parse
error naming the opening line rather than a silent swallow of
everything after it. And tasks may carry `trailing_lines` — prose or
fenced output blocks under the task line — which round-trip
byte-for-byte through canonical save.

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

- **Concurrent-update detection.** `update` re-reads the file under
  an exclusive sidecar lock and compares it byte-for-byte against the
  text its plan was parsed from; a mismatch raises
  `ConcurrentUpdateError` rather than silently last-write-wins
  clobbering. `save` locks and writes atomically; `load` is a plain
  strict read.

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

#### Per-file task namespaces and cross-file resolution

Task IDs default to the bare `T-NNNNNN` form, which is unique within
one `PLAN.md` but ambiguous across the workspace: `T-000007` in the
root plan and `T-000007` in `packages/orchestra/PLAN.md` collide if
anyone wants to refer to "task 7" without naming the file. The
per-file namespace extension closes that gap.

A plan opts in by declaring a two-letter namespace once in the
preamble, before the first phase or `## Bugs` heading:

```markdown
<!-- bob-plan-format: 1 -->

<!-- task_namespace: AB -->

# My Plan
```

Once declared, canonical task IDs in that file take the
`T-XX-NNNNNN` form (`T-AB-000001`, `T-AB-000002`, ...). The
namespace travels through every API surface:

- **Parser.** `parse_plan` reads the `task_namespace` comment from
  the preamble and stores it on `Plan.task_namespace`. The
  declaration is consumed, not retained as prose. Task IDs in either
  `T-NNNNNN` or `T-XX-NNNNNN` form parse and are preserved verbatim
  on `Task.task_id`.
- **Renderer.** `render_plan` emits the `<!-- task_namespace: XX -->`
  comment immediately after the magic line. The declaration
  round-trips byte-for-byte.
- **ID allocation.** `add_task`, `add_phase_task`, `add_bug_task`,
  and `migrate` all consult `Plan.task_namespace` when minting new
  IDs: in a namespaced plan the next ID is
  `T-{namespace}-{N:06d}`, otherwise `T-{N:06d}`. The numeric
  counter is global across the file, so namespaced and legacy IDs
  share the same sequence space and never collide.
- **Canonical validator.** `assert_mcloop_canonical` warns once
  per file (via `warnings.warn`, not a raise) when a namespaced
  plan still contains legacy unprefixed IDs. The warning is a
  migration nudge — writes are not blocked. Files predating the
  namespace scheme (no declaration) stay quiet.

Grammar: `XX` is exactly two ASCII letters (`[A-Za-z]{2}`). Case
is preserved on round-trip and is not normalized; pick a casing
convention and stick to it. Two case-sensitive letters give 2,704
namespace slots, which is overkill for the bob workspace and
deliberately too short to encourage hierarchy — namespaces identify
files, not topics.

##### `resolve_global` — cross-file task lookup

Once IDs are globally unambiguous, a workspace-level resolver
becomes meaningful. `resolve_global` takes a fully-qualified
`T-XX-NNNNNN` id and a workspace root, walks every `PLAN.md`
recursively (sorted by path for determinism), and returns the
`(file, task)` pair for the first plan that carries the id:

```python
from pathlib import Path
from bob_tools.planfile import resolve_global, TaskNotFoundError

try:
    plan_path, task = resolve_global("T-OR-000007", Path("/path/to/workspace"))
except TaskNotFoundError:
    ...  # no PLAN.md under the root carries this id
```

The resolver is intentionally strict about its input form:

- Only `T-XX-NNNNNN` is addressable. Passing a legacy `T-NNNNNN`
  raises `ValueError` — these IDs are ambiguous across files by
  construction, which is precisely the reason the namespace
  prefix was added.
- `TaskNotFoundError` is raised when no plan carries the id;
  the exception carries `task_id` and `root` for diagnostics.
- Parse errors from any walked `PLAN.md` propagate unchanged so
  malformed files are visible to the caller rather than silently
  skipped. A caller that wants tolerant scanning can catch
  `PlanSyntaxError` around the call.

The search descends every `PLAN.md` under the root, including
nested per-package plans (`packages/<name>/PLAN.md`). Sorted-path
iteration means the result is deterministic when the same id
appears in more than one file (a duplicate which itself indicates
a namespace-allocation bug worth flagging).

### `bob-plan` — the planfile CLI

`pip install` adds two scripts: `bob-plan` (below) and `bob`, the
workspace umbrella CLI (`bob install` wires the Telegram permission
hook). `bob-plan` has five subcommands, all operating on a `PLAN.md`
path:

```
bob-plan validate PATH                  # parse and validate; exit 1 on failure
bob-plan fmt PATH                       # parse leniently, migrate, save canonical
bob-plan next PATH                      # print the next actionable T-NNNNNN: text
bob-plan done PATH TASK_ID              # validate, complete, save; emit Settlements as JSON
bob-plan fail PATH TASK_ID --reason ... # validate, fail, save; emit Settlements as JSON
```

`fmt` deliberately parses leniently (a magic-lined file with id-less
tasks is exactly what it exists to repair) and REFUSES with exit 1 —
file untouched — when unfenced incomplete checkboxes sit outside any
`## Stage` / `## Phase` heading, because formatting would silently
drop them. `done` and `fail` both print a JSON *array* of Settlement
objects on stdout.

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
codebase. Appends are durable: each event line is fsync'd before the
lock releases, and the append that creates the events file also
fsyncs the ledger directory so the file itself survives a power loss. When McLoop commits, when Duplo reauthors, when Vroom
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
    bob_cli.py               the `bob` umbrella CLI (hook install)
    planfile/
      __init__.py            public re-exports — the planfile API surface
      model.py               typed Plan / Phase / Task / Outcome / etc.
      parser.py              markdown → Plan (fence-aware; loud on unclosed fences)
      renderer.py            Plan → canonical markdown
      _shared.py             shared regexes + the fence rule (is_fence_line,
                             iter_unfenced_lines, count_unfenced_incomplete_checkboxes)
      status.py              complete / fail / reset / clear / purge mutations
      task_addition.py       add_task / add_phase_task / add_bug_task
      migration.py           migrate + replace_phase (id assignment)
      construction.py        make_task + constructed-mode validation
      validation.py          validate_plan (incl. canonical-save invariants)
      canonical.py           assert_mcloop_canonical + task-context resolution
      semantic_diff.py       render/reparse semantic-equality oracle
      scheduling.py          next_tasks
      iteration.py           bug_count and tree walks
      preflight.py           preflight_runtime_plan (runtime read gate)
      backfill.py            created_at backfill from git history
      operations.py          backward-compat re-export shim over the above
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
