# Per-project state file architecture: independent review

Reviewer: Codex. Claims below were re-derived from current working-tree
source and the cited design docs; I did not trust the proposal's own
framing on input. Headline verdict: the proposal's top-level instinct
holds, but its operational model does not. Root plus package state is
the right direction for plans, manifests, invariants, and the audit
trail, but the "lowest containing scope" rule is not sufficient, and
the proposed `cd packages/<name> && mcloop` convention is actively
unsafe against current McLoop because McLoop treats the PLAN parent as
the project root and initializes git there if `.git/` is absent.

Object under review:
`bob/design/per-project-state-file-architecture.md`.

Context read:

- `bob/design/repository-consolidation-checklist.md`
- `bob/design/recursive-improvement.md`
- `bob/design/mcloop-desplit-integration-plan.md`, especially Section 6
- current source in `mcloop`, `duplo`, `bob-tools`, `orchestra`, and
  the current standalone `vroom` repo

## Section A: Verdict Per State File

| File | Verdict | Load-bearing reason |
|---|---|---|
| `PLAN.md` | Worth revisiting | The hybrid root plus per-package shape is right, but the proposal's placement rule is too path-surface-driven. Consolidation really does create `packages/<name>/` homes for the four sibling repos (`repository-consolidation-checklist.md:3-4`, `:27-40`), and recursive self-improvement really does need a plan that can describe improvements to mcloop, orchestra, and bob-tools together (`recursive-improvement.md:5-10`, `:162-167`). But "lowest scope that fully contains its change surface" (`per-project-state-file-architecture.md:56-61`) is not deterministic until after diagnosis for many self-repair tasks, and it does not say where root policy, root docs, workspace config, or package fan-out tasks live. The de-split closure confirms a cross-repo/package example by freezing separate mcloop and bob-tools refs (`mcloop-desplit-integration-plan.md:983-994`), so "no root plan" is wrong; the issue is the rule, not the hybrid. |
| `BUGS.md` | Worth revisiting | Pairing `BUGS.md` mechanically with `PLAN.md` is too simple. Current McLoop gives BUGS priority and blocks feature work while unchecked bugs exist (`mcloop/main.py:1122-1160`), so a misplaced root bug can stall ecosystem work and a misplaced package bug can hide a cross-package failure pattern. The recursive-improvement design says the loop needs typed FailureRecords with classification, evidence pointers, severity, lineage, task_id, and attempt number (`recursive-improvement.md:130-137`), then a pattern detector over the failure corpus (`recursive-improvement.md:197-203`). A hybrid bug file can work, but its rule should be failure-corpus/query ownership, not just "same scope as PLAN". |
| `CLAUDE.md` | Worth revisiting | Layering root plus package is directionally right, because current package manifests contain materially different instructions: duplo says it operates on cwd and owns target-project files (`duplo/CLAUDE.md:18-24`, `:39-47`), while orchestra documents Python 3.12, slice status, strict design commitments, and different test commands (`orchestra/CLAUDE.md:5-10`, `:78-95`, `:108-120`). But the proposal says McLoop should assemble root plus package files (`per-project-state-file-architecture.md:121-130`), while current McLoop does not read or concatenate `CLAUDE.md`; it launches the agent in `project_dir` and relies on the CLI/session environment (`mcloop/runner.py:711-789`). The honest context unit is task scope: root plus the package(s) implicated by the task, sometimes with design docs, not always exactly root plus one package. |
| `MAINTAIN.md` | Worth revisiting | Layered invariants are plausible, but the execution model is underspecified. Current maintain mode parses one file, sets `project_dir = maintain_path.parent`, uses that directory's checks, and logs to that directory's `.mcloop/maintain-log.json` (`mcloop/maintain.py:240-275`, `:185-218`). A root invariant such as workspace Python version or cross-package import policy should be checked from the workspace root with workspace checks; a package invariant should use package checks. Running both layers as if they were one package-local maintain file would blur those execution contexts. |
| `NOTES.md` | Wrong | Per-package notes are useful, but "no root file" is wrong. McLoop explicitly instructs sessions to append observations to `NOTES.md` with the current task label (`mcloop/runner.py:427-444`), and its CLAUDE sync path appends diff summaries to `NOTES.md` as a human-readable changelog (`mcloop/claude_md_check.py:1-9`). A root-level cross-cutting task or vroom/recursive-improvement task can surface ecosystem observations that are not package-specific. Forcing those into an arbitrary package would recreate the same dishonesty the proposal rejects for cross-cutting `PLAN.md`. |
| `IDEAS.md` | Wrong | Package ideas should remain package-local, but the proposal's "future cross-cutting idea goes directly into root PLAN.md once ready" (`per-project-state-file-architecture.md:190-193`) skips the whole purpose of an idea scratchpad. Current `mcloop idea` is already cwd/project-scoped and appends to `project_dir / "IDEAS.md"` (`mcloop/idea_cmd.py:18-27`); there is no mechanical reason to ban a root `IDEAS.md` for ecosystem ideas that are not ready to become tasks. Use hybrid, not per-package-only. |
| Everything log | Right, with implementation corrections | A single audit trail is the right architecture. Recursive improvement needs machine-queryable history (`recursive-improvement.md:69-71`) and later a pattern detector over the failure corpus (`recursive-improvement.md:197-203`). But the proposal overstates current source: McLoop's plan ledger default is `<project_dir>/.duplo/ledger`, enabled by project-local auto-detection (`mcloop/ledger_config.py:12-16`, `:99-117`), and the ledger schema currently has no `scope` field in the envelope (`bob_tools/ledger/schema.py:109-138`). The current event model writes `PLAN.events.jsonl` inside one ledger directory (`bob_tools/ledger/storage.py:1-4`, `:43-44`). So the unitary-root decision is right, but it requires an explicit schema/location migration. |

## Section B: Answers To The Open Questions

1. Keep the hybrid `PLAN.md`/`BUGS.md`, but replace the proposal's
   rule. A single root plan with `[scope: orchestra]` annotations would
   preserve one visual queue, but it would throw away the existing
   operational property that McLoop and Duplo operate on a concrete
   project/plan file (`mcloop/main.py:835-840`; `duplo/planner.py:699-737`).
   The better model is root objectives and cross-cutting epics at root,
   independently schedulable package leaf work in package plans, and
   explicit parent/lineage links between them. For bugs, use the same
   file topology but not the same placement rule: place by failure
   ownership and query semantics, not just touched paths.

2. Use layered `CLAUDE.md` and `MAINTAIN.md`; do not duplicate root
   content into every package. Duplication would create five stale
   copies of workspace policy, Python version, import rules, and ledger
   semantics. The cost to pay is not "read two files"; the cost is
   implementing an explicit context resolver. For `CLAUDE.md`, the
   resolver should assemble root plus task-relevant package manifests.
   For `MAINTAIN.md`, it should run root invariants under root checks
   and package invariants under package checks, then record enough
   scope in the maintain log to explain which layer failed.

3. The canonical convention should be root execution with an explicit
   scope argument or task-scope argument, not `cd` as the source of
   truth. Human convenience aliases can still infer scope from cwd, but
   vroom-driven dispatch needs a stable API: `mcloop --scope orchestra`,
   `mcloop --plan packages/orchestra/PLAN.md --workspace .`, or an
   equivalent structured invocation. Current source makes this
   non-negotiable: `run_loop` takes the PLAN parent as `project_dir`
   (`mcloop/main.py:835-840`), and `_ensure_git` initializes a git repo
   when `project_dir/.git` is absent (`mcloop/git_ops.py:49-63`). In a
   consolidated package directory that would create a nested repo unless
   McLoop is changed to distinguish workspace root from package scope.

4. Scope must be a separate structured field, not composed into
   `task_id`. Composing `orchestra:T-000001` into the ID makes filtering
   shorter on the wire but worse everywhere else: task IDs, package
   scope, plan path, phase ID, run ID, and parent/root objective are
   distinct query dimensions. The current ledger envelope already treats
   top-level event fields as schema-governed structure (`bob_tools/ledger/schema.py:109-138`);
   extend that discipline rather than hiding scope inside a string.
   The minimum useful event lineage is `scope`, `plan_path`, `task_id`,
   `phase_id`, `run_id`, and, for fan-out, a root/parent task reference.

5. Use per-package `NOTES.md`/`IDEAS.md` plus root files for ecosystem
   notes and ideas. A single root file for all notes would be noisy, but
   per-package-only is too narrow. Cross-cutting work, root policy,
   vroom behavior, trust-ladder design, and workspace-level failures all
   produce observations and ideas before they are ready to become
   `PLAN.md` tasks. Root notes/ideas are the correct scratch layer for
   those cases; package notes/ideas remain correct for component-local
   cases.

## Section C: Structural Findings

### C1. The hybrid PLAN.md decomposition needs a stronger ownership model

The proposal's hybrid is the right answer to the wrong formalization.
"Lowest containing scope" sounds deterministic, but it is deterministic
only for already-understood edits. Recursive improvement often starts
from a failure signature, not a known patch set. M1 wants FailureRecords
with classification, evidence, severity, lineage, task_id, and attempt
number (`recursive-improvement.md:130-137`); M2 turns those records into
PLAN tasks (`recursive-improvement.md:162-167`); M3 clusters failures
into capability proposals (`recursive-improvement.md:197-203`). In that
flow the change surface is an output of diagnosis, not an input.

The root/package split should instead model ownership:

- Root `PLAN.md`: ecosystem objectives, cross-package migrations,
  workspace policy, trust-ladder changes, validation-corpus changes,
  vroom/dispatcher work, and root coordination tasks.
- Package `PLAN.md`: leaf work that can be run, checked, and committed
  under one package's execution context.
- Links: root tasks may spawn package tasks, but this needs structured
  parent/child lineage. A prose "explicit reference to the root task ID"
  is not enough once vroom or M2 proposes tasks automatically.

The de-split is good evidence for this: the closure has separate
verified mcloop and bob-tools refs (`mcloop-desplit-integration-plan.md:991-994`),
but the work was one migration with cross-repo correctness criteria
(`mcloop-desplit-integration-plan.md:996-1013`). Under the proposed
hybrid, that is a root objective with package leaf work. The proposal
should say how completion, dependency, and acceptance evidence roll up.

### C2. `CLAUDE.md` layering should be task-context layering, not package layering

Root plus one package is often right, but it is not the general rule.
An agent session needs the conventions for the task it is executing.
Sometimes that is one package. Sometimes it is root docs plus two
packages. Sometimes it is root-only design work. Sometimes the right
context is a short root manifest plus selected package manifests, not
the full text of every package's `CLAUDE.md`.

The current source also does not support the proposal's mechanical
claim. McLoop does not assemble `CLAUDE.md`; `run_task` passes a prompt
and runs the CLI in `project_dir` (`mcloop/runner.py:711-789`), and a
grep of the run path shows no root/package `CLAUDE.md` read. So the
proposal should not describe this as a "small" change until it names the
context resolver, precedence rules, conflict behavior, and token budget.

A more honest design:

- root `CLAUDE.md`: workspace map, global invariants, how to resolve
  package manifests, and cross-package rules
- package `CLAUDE.md`: package build/test conventions and package-local
  architecture
- task context resolver: given `scope`, `plan_path`, and optional
  package set, assemble exactly the relevant manifest set root-first
- cross-cutting root tasks: include package manifests only for packages
  named by the task or discovered during planning, not all packages by
  default

### C3. The proposed working-directory convention is the main architectural bug

The proposal recommends `cd packages/orchestra && mcloop`
(`per-project-state-file-architecture.md:133-141`, `:223-226`). That
does not scale to vroom, and it does not match current McLoop safety
properties.

Current McLoop derives `project_dir` from the PLAN file's parent
(`mcloop/main.py:835-840`). It then uses that directory for logs, bugs,
checks, `.mcloop`, ledger config, git operations, and run summaries
(`mcloop/main.py:837-840`, `:888-914`, `:969-987`). Git handling is
especially important: `_ensure_git` checks only `project_dir / ".git"`
and initializes a new git repo if it is missing (`mcloop/git_ops.py:49-63`).
After consolidation, `packages/orchestra/` should not contain `.git/`.
So the recommended convention would either fail the intended monorepo
model or create nested package repos.

The required abstraction is:

- `workspace_root`: the git root and root state/log/ledger home
- `scope`: root or package name
- `scope_root`: `workspace_root` for root tasks, otherwise
  `workspace_root/packages/<scope>`
- `plan_path`: the specific PLAN.md being advanced
- `execution_cwd`: usually `scope_root` for checks, but git operations
  and ledger writes must know `workspace_root`

Once those are distinct, human cwd inference can be a convenience. It
cannot be the architecture.

### C4. The vroom implications are under-specified

The proposal says vroom dispatches McLoop and only needs
`<scope>:<task_id>` tagging (`per-project-state-file-architecture.md:265-275`).
That is not grounded in current vroom source or the recursive design.
Current `vroom` is an artifact auditor: it loads a `vroom.toml` by
walking upward from cwd, runs configured auditors in parallel over an
artifact, coalesces findings, and optionally generates a corrected
artifact (`vroom/__main__.py:106-194`; `vroom/orchestrator.py:12-26`;
`vroom/config.py:184-194`). It is not currently a McLoop dispatcher.

The recursive-improvement document describes a richer loop than "pick
task ID, run McLoop". M1 emits FailureRecords, M2 proposes fix tasks
from those records, and M3 detects patterns across the failure corpus
(`recursive-improvement.md:122-152`, `:154-187`, `:189-220`). That loop
needs queries like:

- failures by package and by cross-package pattern
- failures by failure class and fix template
- tasks generated from a failure record
- commits and tests that supplied acceptance evidence
- root objective to package task fan-out
- repeated failures across packages over time
- changes to trust ladder, validation corpus, or self-improvement driver

A scoped task tag is necessary but nowhere near sufficient. The ledger
needs explicit dimensions for `scope`, `plan_path`, `task_id`,
`phase_id`, `run_id`, parent/root objective, touched paths, and
FailureRecord linkage. Current ledger emission has `task_label`,
`phase_id`, `run_id`, `touched_paths`, and commit metadata
(`mcloop/ledger_emit.py:384-513`), but no package scope or task ID in
the event envelope. That is the real schema gap.

### C5. The proposal locks down several decisions too early

The recursive design deliberately leaves storage and self-improvement
plan home open: "Phase D in bob-tools/PLAN.md, or a new repo bob" and
"JSON for M1, SQLite for M3" are still open or provisional
(`recursive-improvement.md:283-286`). The state-file proposal should
not prematurely freeze:

- the final everything-log path
- JSON vs SQLite for failure/pattern storage
- whether `.mcloop/runs/` stays purely per-package once root dispatch
  exists
- whether root `NOTES.md` and `IDEAS.md` are forbidden
- whether root-plus-one-package is the only `CLAUDE.md` layering mode
- whether cwd remains the dispatcher API

The safer decision now is the minimum architecture that preserves
future freedom:

- use explicit scope metadata everywhere
- allow root and package state where both are meaningful
- keep package leaf execution separate from workspace git/ledger state
- avoid composed IDs
- add parent/root objective lineage before vroom or M2 starts generating
  tasks automatically

That keeps the proposal aligned with the recursive-improvement path
without baking in a dispatcher model before vroom is actually operating
as a dispatcher.
