# Per-project state file architecture for the bob ecosystem

## Status

Open design proposal. Revision 3, incorporating Codex's independent
review (`bob/design/per-project-state-file-architecture-review-codex.md`)
and GPT's independent review
(`bob/design/per-project-state-file-architecture-review-gpt.md`). Not
yet implemented. Documents the question of which state files
(`PLAN.md`, `BUGS.md`, `CLAUDE.md`, `NOTES.md`, `IDEAS.md`,
`MAINTAIN.md`) live at which scope after the four-repo consolidation
into the unified bob repo, plus the McLoop changes that scoping
requires.

Both reviews converged on the load-bearing direction: explicit scope
as structured metadata, hybrid root/package state where content
exists at both levels, layered manifests and invariants, and a single
workspace-root audit trail. Both reviews also pushed on specific
implementation claims that revision 2 over-promised — scope
classification reliability, CLAUDE.md resolver simplicity, ledger
migration ease, and the inventory of McLoop call sites that need
adapting. Revision 3 absorbs those corrections.

Audience: a final independent review (third lineage) against the
revised text before this transitions from architecture proposal to
implementation work.

## Context

The bob ecosystem currently exists as five separate repositories
(`mcloop`, `duplo`, `orchestra`, `bob-tools`, and `bob` itself).
The five repos carry different subsets of the per-project state
files today: mcloop has the full set (`PLAN.md`, `BUGS.md`,
`CLAUDE.md`, `MAINTAIN.md`, `NOTES.md`, `IDEAS.md`); duplo has four
of them; orchestra has two; bob-tools has three; bob has none.
Post-consolidation, the proposal commits to a uniform shape, but the
starting state is heterogeneous.

The post-consolidation layout collapses the four sibling repos into
`packages/<name>/` subdirectories under one bob repository. The
question this document settles: which of those state files live at
the bob root, which live per-package, and which are hybrid or
layered across both scopes — together with the McLoop changes,
ledger schema versioning, and scope-validation contracts that scoping
requires.

The recursive-improvement trajectory (M1 self-diagnosing, M2
self-repairing, M3 self-extending; described in
`bob/design/recursive-improvement.md`) drives the architectural
requirements. Failure records are an output of diagnosis, not an
input to it; the system increasingly authors its own work; vroom
will eventually dispatch McLoop autonomously rather than the human
running it. The state-file architecture has to support that
trajectory, not just today's manual workflow.

A note on vroom: today's vroom (`vroom/__main__.py:106-194`;
`vroom/orchestrator.py:12-26`) is an artifact auditor — it reads
a text artifact, runs auditors in parallel, coalesces findings,
optionally writes a corrected artifact. The dispatcher/backlog/
scheduler/executor/reflector roles described in
`vroom/README.md:9-26, :40-58` are designed but not implemented.
The vroom implications named in this proposal are forward-looking
contracts for the designed system, not claims about current source.
The proposal labels them as such throughout.

## Core abstraction: explicit scope

The proposal's load-bearing primitive is that scope is **explicit
structured metadata**, not inferred from the current working
directory. McLoop, duplo, and vroom each operate in terms of a small
set of distinct properties:

- **`workspace_root`**: the git root and root state/log/ledger home.
  Post-consolidation this is the unified bob repository's root.
  Single per system.
- **`scope`**: either `root` or a package name (e.g. `orchestra`,
  `mcloop`). The unit of work-ownership.
- **`scope_root`**: `workspace_root` when scope is root;
  `workspace_root/packages/<scope>` otherwise. The directory that
  holds the scope's state files (`PLAN.md`, `BUGS.md`, package
  `CLAUDE.md`, etc.).
- **`plan_path`**: the specific `PLAN.md` being advanced this run.
  Resolves from `scope_root/PLAN.md` by default but can be
  overridden.
- **`execution_cwd`**: where tests, builds, and check commands run.
  Typically `scope_root`. Distinct from `workspace_root` so package
  checks don't accidentally operate on workspace-wide state.

These properties together form a `WorkspaceContext` object that
flows through McLoop, duplo, and the eventual vroom dispatcher.
Where current source uses `project_dir` as the unified "this is
where everything happens" directory, the post-consolidation code
takes a `WorkspaceContext` and resolves each operation against the
appropriate property.

Why this distinction is load-bearing: today McLoop derives
`project_dir` from the PLAN file's parent (`mcloop/main.py:835-840`)
and treats it as the unified directory for logs, bugs, checks,
`.mcloop/`, ledger config, git operations, and run summaries.
Crucially, `_ensure_git` initializes a new git repo if
`project_dir/.git` is absent (`mcloop/git_ops.py:49-63`). Under a
consolidated layout where `packages/orchestra/` is a subdirectory
of the bob git repo rather than its own repo, this behavior would
create a nested git repo inside the workspace — exactly the failure
mode consolidation is supposed to prevent.

Convenience layer on top: when the user runs `mcloop` from a
package directory, scope can be inferred from cwd by walking upward
to find `workspace_root` (the directory containing `.git/` and a
workspace-level `pyproject.toml` declaring `[tool.uv.workspace]`),
then computing `scope_root` and `scope` from the cwd's relationship
to `packages/`. The cwd-inference is a shortcut over the explicit
abstraction, not a substitute. For vroom-driven dispatch, scope is
passed explicitly as part of the task envelope — there is no
"current directory" for vroom.

## The files

### `PLAN.md` and `BUGS.md` — hybrid, by declared scope with validation

`PLAN.md` is the authoritative build document; `BUGS.md` is the
paired bug backlog. Both files use the same scope-resolution rule
because they describe work at the same level.

**Hybrid: one at the bob root for root-scoped work, one per package
for package-scoped work.**

Scope is **declared at task creation time** and **validated against
evidence at task completion time**. Declaration is by ownership:

- Root scope: ecosystem objectives, cross-package migrations,
  workspace policy, trust-ladder changes (M2), validation-corpus
  changes (M3), vroom/dispatcher work, root coordination tasks,
  and recursive-improvement milestones (M1/M2/M3 deliverables).
- Package scope: leaf work that can be run, checked, and committed
  under one package's execution context. Refactors, bug fixes,
  feature additions, and component-internal improvements that
  don't change the contract with other packages.

**Scope-classification reliability is not assumed; it is verified.**
Codex and GPT both pushed on this and they are right: humans will
file symptom-scoped bugs, duplo can generate a package leaf for
what is really a contract change, and future vroom can see a
failure cluster but choose the wrong package owner before diagnosis
converges. The architecture needs validation and repair paths, not
just a classification rule.

**Required scope-validation contract at task intake.** Every task
created in `PLAN.md` or `BUGS.md` (whether by human, duplo, or
vroom) carries:

- `scope`: declared scope (root or package name)
- `scope_confidence`: one of `declared` (human-chosen),
  `inferred` (heuristically derived from inputs), or `provisional`
  (created before full diagnosis; subject to validation)
- `scope_rationale`: a short explanation of why the scope is what
  it is
- `parent_task_ref`: optional pointer to a parent task (root
  objective to package leaf, or M2 fix-task to its originating
  FailureRecord)
- `declared_package_set`: for root-scoped tasks, the packages the
  task is expected to touch; for package-scoped tasks, the single
  package

These fields belong to the planfile API, not the markdown surface.
The planfile validator refuses to accept a task whose declared
scope is inconsistent with its declared parent (a package leaf
whose parent is a different package's task is rejected; a root
task whose parent is a package task is rejected).

**Validation at completion time.** When a task completes, McLoop
compares the touched paths against the declared scope. A
package-scoped task that touched files outside its declared
package emits a `scope_mismatch` finding to the everything log.
The finding records the declared and actual scopes and optionally
proposes a relocation task (move the bug or task to the correct
scope). McLoop does not silently relocate; the relocation is a
proposal the human or vroom reviews.

**Parent/child lineage between root objectives and package leaf
tasks is a first-class first-class requirement of the planfile API.**
A cross-cutting root objective like "consolidate the four sibling
repos" decomposes into package-leaf tasks. The de-split is the
canonical example: one integration plan with stages spanning two
repos, closure with separate verified refs per repo
(`bob/design/mcloop-desplit-integration-plan.md:991-994`). Under
the hybrid, that decomposition is structurally represented: a root
task with explicit child references; package leaf tasks with
explicit parent references; acceptance evidence rolling up via the
everything log.

The lineage extension to bob-tools' planfile API has to land
before vroom or M2 starts generating tasks automatically — vroom
can't dispatch a multi-package fan-out without it, and M2 can't
roll up acceptance evidence without it.

Alternatives considered:

- **One bob-root `PLAN.md` for everything.** Loses per-component
  grouping; component-internal work visually swamps cross-cutting
  work. Rejected.
- **One `PLAN.md` per package, no root plan.** Forces cross-cutting
  work to be split or arbitrarily assigned. The de-split would have
  had to live in either `packages/mcloop/PLAN.md` or
  `packages/bob-tools/PLAN.md`; neither would have been honest.
  Rejected.

`BUGS.md` follows the same hybrid topology with the same intake and
validation discipline. The scoping rule is **failure ownership**,
not just touched paths. A bug caused by a cross-package interaction
is root-scoped even if its fix only touches one package; a bug
caused by orchestra's adapter is orchestra-scoped even if it
manifests in mcloop's session. This matters because M1's
FailureRecord schema is designed to classify the *cause*, and the
bug file's location should match the cause, not the symptom.

McLoop's bug-only mode (`mcloop/main.py:1122-1160`) gives unchecked
bugs priority and switches into bug-only mode before feature work.
A bug filed at the wrong scope can either stall too much work
(root bug filed at package scope blocks ecosystem-level work) or
hide a cross-package failure pattern (package bug that should have
been root). The intake contract above is the mitigation.

### `CLAUDE.md` — layered, with bounded task-context assembly

`CLAUDE.md` describes the codebase to the agent — source-file
manifest, build instructions, conventions, platform rules. Sessions
read it first to orient.

**Layered: a short bob-root `CLAUDE.md` for ecosystem-wide
conventions, plus per-package `CLAUDE.md` files for package-specific
conventions. McLoop assembles the relevant set per task, with
explicit precedence classes and bounded scope.**

The packages have genuinely distinct conventions. duplo's current
`CLAUDE.md` says it operates on cwd and owns target-project files
(`duplo/CLAUDE.md:18-24`, `:39-47`); orchestra's documents Python
3.12, slice status, and different test commands
(`orchestra/CLAUDE.md:5-10`, `:78-95`). A single bob-root
`CLAUDE.md` would be either too generic or too long. Per-package
is correct for the package-specific layer.

The root layer holds ecosystem-wide content: workspace structure,
Python version, where the everything log lives, monorepo
conventions, cross-package import rules. These don't change per
package and shouldn't be duplicated in five files.

**Precedence classes (load-bearing).** The conflict policy in
revision 2 — "package conventions override root" — is too blunt
and was correctly rejected by GPT's review. Some root rules are
safety rules and must not be overridden by a package manifest.
Some root rules are workspace conventions where package overrides
are legitimate. The resolver classifies content by precedence
class:

- `safety`: root-only, no override permitted (e.g., "Never
  initialize a git repo inside `packages/`"; "Never commit secrets
  to the everything log")
- `workspace`: root content where package overrides are
  illegitimate by default but can be explicitly flagged
  (e.g., minimum Python version, ledger schema version)
- `package`: package-specific content (build commands, test
  commands, package-local architecture notes); root content here
  is overridable
- `task`: task-specific context the resolver passes through from
  the task envelope (e.g., a multi-package task names the relevant
  packages explicitly)

Conflict between a package and a higher-precedence root rule is
fail-closed: the resolver refuses to assemble the context and
emits a structured finding. The agent does not silently see a
contradicted ruleset.

**Phased implementation.** Per GPT's recommendation, the resolver
ships in two phases:

1. **Phase 1: root plus exactly one package.** The default
   assembly for a package-scoped task. Explicit precedence classes
   with fail-closed conflict detection. Token budget enforced; if
   exceeded, the resolver fails closed or produces a deterministic
   summarization artifact (not a warning that the agent could
   miss). This covers the majority of expected tasks.
2. **Phase 2: multi-package assembly.** Triggered only when the
   task envelope explicitly names a package set. The resolver
   assembles root plus the named packages with the same precedence
   rules. Token-budget overrun produces a deterministic excerpt or
   summarization, never a silent truncation.

The resolver lives in McLoop behind a clean API so duplo and vroom
can call the same assembly logic. Current `run_task` has the
natural insertion point: `session_context` is passed into
`invoke_code_edit` and the prompt builders
(`mcloop/runner.py:714-728`, `:742-772`). The resolver's output
extends `session_context` rather than introducing a separate
context-passing mechanism.

Alternatives considered:

- **One bob-root `CLAUDE.md`.** Rejected. Generic or long.
- **Per-package only, no root.** Rejected. Ecosystem-wide content
  would be duplicated or omitted.
- **Strict root-plus-one-package.** Rejected as the only mode, but
  it is the default mode in Phase 1.

### `MAINTAIN.md` — layered, with distinct execution contexts

`MAINTAIN.md` is the invariants file McLoop's maintain mode
enforces. Invariants have the same scope distinction as `CLAUDE.md`
content.

**Layered, parallel to `CLAUDE.md`. Root `MAINTAIN.md` for
ecosystem-wide invariants, per-package `MAINTAIN.md` for
component-specific ones, with distinct execution contexts.**

Current maintain mode parses one file, sets
`project_dir = maintain_path.parent`, uses that directory's checks,
and logs to that directory's `.mcloop/maintain-log.json`
(`mcloop/maintain.py:240-275`, `:185-218`). The proposal: root
invariants execute under `workspace_root` with workspace checks
(workspace `pyproject.toml`, root ruff config). Package invariants
execute under `scope_root` with package checks.

Two maintain logs result:
`workspace_root/.mcloop/maintain-log.json` for root invariants,
`scope_root/.mcloop/maintain-log.json` for package invariants.
They are distinct artifacts because they describe distinct
execution contexts.

### `NOTES.md` — hybrid

Codex's review correctly flagged that "per-package only" is wrong.
McLoop instructs sessions to append observations to `NOTES.md`
(`mcloop/runner.py:427-444`); the CLAUDE sync path appends diff
summaries (`mcloop/claude_md_check.py`). Both patterns work today
against a single per-project NOTES file.

**Hybrid: root `NOTES.md` for cross-cutting and ecosystem-level
observations, per-package `NOTES.md` for component-scoped
observations. Scope of the task determines which file the session
appends to.**

### `IDEAS.md` — hybrid

Same correction. Banning a root `IDEAS.md` was wrong;
ecosystem-level ideas need a scratch layer.

**Hybrid: root `IDEAS.md` for ecosystem-level scratch, per-package
`IDEAS.md` for component-scoped scratch.**

The `mcloop idea "text"` command resolves which file to append to
based on its execution scope (cwd inference for the convenience
case; explicit scope for vroom dispatch).

### The everything log — single, with versioned schema migration

The everything log is the unified audit trail of the system's
actions across all components. A single audit trail is the right
architecture: recursive improvement needs machine-queryable history
across packages (`bob/design/recursive-improvement.md:69-71`), and
M3 detects patterns across the failure corpus
(`bob/design/recursive-improvement.md:197-203`). Per-package logs
would fragment the corpus and defeat M3.

**Single, at the bob root.**

**The current ledger schema is insufficient and requires a
versioned migration.** GPT's review correctly identified that the
revision 2 claim of "straightforward additive fields with defaults"
was wrong. Current validation is intentionally narrow:

- Envelope rejects additional properties
  (`bob_tools/ledger/schema.py:26-29, :113`)
- Payloads reject additional properties
  (`bob_tools/ledger/schema.py:246-295, :281`)
- Storage validates before append
  (`bob_tools/ledger/storage.py:1-10`)
- The serializer enumerates envelope fields explicitly
  (`bob_tools/ledger/events.py:156-187`)

This is a versioned ledger schema migration, not a defaulted-field
addition.

**Migration shape.** Introduce a structured `task_context` payload
object that the events McLoop emits attach when relevant. Initially
the affected events are `commit_landed`, `test_failed`,
`work_observed`, and `finding_observed`. The `task_context`
contains:

- `scope`: root or package name
- `plan_path`: the file the task came from
- `task_id`: the canonical T-NNNNNN id, as a structured field
- `phase_id`: existing semantics, retained
- `parent_task_id`: optional reference to a parent task (for
  fan-out)
- `failure_record_id`: optional reference to a FailureRecord (M1+)

This is a per-event-type payload extension, with `task_context` as
a single nested object. The envelope itself doesn't change in this
phase. Projector compatibility is preserved: projectors that don't
know about `task_context` ignore it; projectors that do read it
extract structured query dimensions.

If a future requirement forces envelope changes (the M3 pattern
detector may want envelope-level lineage), the migration becomes
a `schema_version` bump from `1.0` to `1.1` with explicit
compatibility rules and projector versioning. The architecture
commitment now is: do not extend the envelope ad-hoc; do not
scatter scope fields across unrelated payloads; if envelope changes
are needed, version them.

**Path:** the exact location of the everything log is deferred to
the consolidation plan (see Out of Scope). The architectural
commitment is unitary scope at the workspace root.

## Summary table

| File | Scope | Rationale |
|---|---|---|
| `PLAN.md` | Hybrid (root + per-package) | Declared scope with validation at completion; explicit parent/child lineage |
| `BUGS.md` | Hybrid (root + per-package) | Scoped by failure ownership; same intake and validation discipline |
| `CLAUDE.md` | Layered (root + per-package), bounded resolver | Precedence classes (safety/workspace/package/task); fail-closed conflicts; phased implementation |
| `MAINTAIN.md` | Layered (root + per-package), distinct execution contexts | Root under workspace checks; package under package checks; distinct logs |
| `NOTES.md` | Hybrid (root + per-package) | Scope follows the task's scope |
| `IDEAS.md` | Hybrid (root + per-package) | Same; cwd inference for `mcloop idea` |
| Everything log | Single (workspace root), versioned schema migration | Unitary by design; `task_context` payload extension first, envelope version bump if needed later |

## Implications for McLoop

The proposal requires concrete McLoop changes. GPT's review
correctly noted that revision 2's implications section was
incomplete. The full inventory:

**Working-directory and scope changes:**

1. **Introduce `WorkspaceContext` object.** Resolves to
   `workspace_root`, `scope`, `scope_root`, `execution_cwd`,
   `plan_path`. Constructed from explicit arguments (vroom
   dispatch) or inferred from cwd (human invocation).
2. **CLI surface extension.** Today `mcloop` accepts `--file` for
   the plan path (`mcloop/main.py:2324-2396`). Add `--workspace`,
   `--scope`, and `--plan-path` for explicit invocation. Cwd
   inference remains the default when these are absent.
3. **Subcommand dispatch update.** Subcommands currently dispatch
   from `checklist_path.parent` (`mcloop/main.py:436-485`). All of
   `idea`, `maintain`, `install`, `wrap`, `sync`, `audit`,
   `investigate` need to take a `WorkspaceContext` argument rather
   than a single `project_dir`.

**Git operations:**

4. **`_ensure_git` targets `workspace_root`.** Today it checks
   `project_dir/.git` and initializes there
   (`mcloop/git_ops.py:49-63`). Post-consolidation must target
   `workspace_root/.git`, never the package subdirectory. This is
   the single most important change.
5. **All git helpers take `workspace_root`.** `_checkpoint`,
   `_push_or_die`, `_stage_safe`, `_commit`, `_changed_files`
   (`mcloop/git_ops.py:124-250, :365-420`) all currently take
   `project_dir` and run git there. They need to take
   `workspace_root` for git operations, with `execution_cwd` used
   only where file-system relative paths matter.
6. **Cross-cutting `changed_files` detection.** The de-split
   surfaced that mcloop's `changed_files` detector was blind to
   edits in sibling repos
   (`bob/design/recursive-improvement.md:97`). Post-consolidation
   this resolves naturally (everything is one git repo), but the
   detection logic should be verified to handle changes spanning
   `packages/<name>/` correctly.

**State paths:**

7. **`.mcloop/` writes target scope_root, not blanket
   `project_dir`.** Run summaries
   (`mcloop/run_summary.py:75-97`), lifecycle state
   (`mcloop/lifecycle.py:39-45, :107-119`), active-pid, and
   maintain logs all currently use `project_dir`. They should use
   `scope_root` so per-scope runs don't collide.
8. **Everything log writes target workspace_root.** Regardless of
   scope, ledger writes go to the workspace-rooted everything log
   so the unified audit trail is genuinely unified. The schema
   migration above is a precondition for this to be meaningful.

**Context assembly:**

9. **CLAUDE.md context resolver.** Implement the task-context
   assembly in the `CLAUDE.md` section. Phase 1 first (root plus
   one package); Phase 2 (multi-package) only after explicit
   task-envelope package-set support exists.

**Cumulative shape.** Per GPT's recommendation, the work is best
expressed as introducing the `WorkspaceContext` object and
auditing every existing `project_dir` call site into one of:
`workspace_root`, `scope_root`, `execution_cwd`, `state_root`
(equivalent to `scope_root/.mcloop`), or `plan_path`. The audit is
mechanical but exhaustive.

**Sequencing.** Per GPT's argument (open question 4), these
changes should land **before consolidation** in a compatibility
mode where `workspace_root == scope_root` and behavior is
provably unchanged on standalone repos. Then the consolidation
commit is mostly layout-plus-config rather than a large atomic
McLoop adaptation alongside a layout migration. The
de-split's §6.1 follow-up
(`bob/design/mcloop-desplit-integration-plan.md:1017-1039`) shows
what happens when forced ordering isn't followed; this proposal
explicitly avoids the same failure mode.

## Implications for duplo

duplo writes `PLAN.md` files at the project root today. Post-
consolidation, duplo writes plans at the scope its current
invocation targets — root plan if called from the workspace root,
package plan if called from a package. duplo's reauthor mode
follows the same rule, and reauthor events emit to the
workspace-rooted everything log with the appropriate scope tag.

duplo also needs to handle the parent/child lineage requirement
when generating cross-cutting plans. A root-level reauthor that
produces fan-out to multiple package leaves must emit the lineage
structure, not just the tasks.

## Implications for the future vroom dispatcher

(This section describes the designed vroom; today's vroom is an
artifact auditor as noted in Context.)

The future vroom dispatcher will pass a structured task envelope to
McLoop containing:

- workspace root path
- scope (root or package name)
- task_id and plan_path
- optional package-set for `CLAUDE.md` context assembly
- optional FailureRecord reference (M2+)
- declared parent_task_ref for fan-out

vroom's reflection loop will query the everything log to:

- find failures by scope and detect cross-package patterns
- track tasks generated from each failure record
- track commits and tests supplying acceptance evidence
- roll up acceptance evidence from package-leaf tasks to root
  objectives
- detect repeated failures across packages over time

The schema migration described in the everything-log section is
what makes these queries possible. The minimum vroom-facing
contract is the `task_context` payload — beyond that, the
backlog schema, scheduler behavior, and reflection query
language are vroom's own design space, not this proposal's.

## Out of scope for this proposal

Per Codex's C5 and GPT's D5, the proposal narrows scope to the
architectural commitments needed for the consolidation. Explicitly
deferred:

- **Final everything-log storage format and exact path.**
  Architectural commitment: unitary scope at workspace root with
  versioned schema. Storage backend (JSON vs SQLite) and exact
  path are downstream design.
- **Self-improvement PLAN home.** The recursive-improvement doc
  considers "Phase D in bob-tools/PLAN.md, or a new repo bob"
  (`bob/design/recursive-improvement.md:282-284`). This proposal
  supports either.
- **Trust ladder mechanics, FailureRecord schema, M3 pattern
  detector.** Live in the recursive-improvement design; this
  proposal only requires that the everything log can carry the
  necessary lineage fields.
- **`.mcloop/runs/` cross-scope aggregation.** Whether vroom needs
  a unified view across per-scope `.mcloop/runs/` directories is a
  vroom question, not a state-file question.
- **Scope-relocation automation.** This proposal commits to
  scope-mismatch findings being emitted. It does not commit to
  automatic relocation; that's a separate decision.

## Three gates before implementation

GPT's review identified three concrete design questions that have
to be settled before this transitions from architecture proposal
to implementation. Each warrants its own scoped design document.

1. **Scope-validation and relocation contract.** The intake and
   completion-validation discipline named in the `PLAN.md` /
   `BUGS.md` section is described in principle but not specified
   to the level the planfile API needs. The gate produces: the
   exact intake field schema, the exact validator behavior, the
   exact `scope_mismatch` finding payload, and the relocation
   proposal format.
2. **Bounded CLAUDE context resolver design.** The precedence
   classes, conflict-detection algorithm, token-budget handling,
   and summarization/excerpting strategy for Phase 1 are described
   in principle. The gate produces: the resolver's API, the
   precedence-class declaration syntax, the conflict-resolution
   algorithm in detail, the fail-closed and summarization paths.
3. **Versioned ledger schema migration plan.** The `task_context`
   payload extension is described in principle. The gate
   produces: the exact payload schema, the projector compatibility
   rules, the backward-compatibility tests against existing
   ledger artifacts, and the rollout sequence.

The three gates can be developed in parallel. Consolidation
execution itself does not require all three to be complete on day
one — gate 2 (CLAUDE resolver) and gate 3 (ledger migration) can
follow consolidation as long as gate 1 (scope validation) and the
McLoop changes in the Implications section land first.

## Open questions for the next review pass

Reduced from revision 2's five to three, focused on what remains
load-bearing after both prior reviews.

1. Is the precedence-class scheme for `CLAUDE.md`
   (safety/workspace/package/task) the right partition, or does it
   miss a class that current package manifests genuinely need?
   Specifically, where do design-doc references (orchestra's
   `design/orchestra-design.md`, mcloop's various design docs)
   fit? They are neither workspace-wide nor strictly package-local;
   they are referenced from package CLAUDE.md but the design docs
   themselves are repo-rooted artifacts.

2. The compatibility-mode sequencing (McLoop changes land before
   consolidation with `workspace_root == scope_root`) is plausible
   but adds work to the pre-consolidation window. Is the
   atomic-risk argument strong enough to justify the extra phase,
   or would landing them atomically with consolidation be
   acceptable given the de-split's behavioral-equivalence audit
   pattern is available?

3. Scope-mismatch findings are emitted to the everything log, but
   the proposal doesn't commit to whether they block or warn.
   For human-filed tasks, a warning seems right (humans can
   confirm or relocate). For vroom-dispatched tasks, blocking
   might be required (a misclassified scope means vroom's
   reflection loop has bad data). Should the policy depend on the
   task's origin?

## Closing note

The principle organizing all of these decisions: **the file's scope
should match the scope at which its content is meaningful, and
scope itself should be explicit metadata rather than inferred from
filesystem position.** A bug in orchestra's adapter is a bug at
the orchestra scope; a re-author of the M2 trust ladder is work at
the ecosystem scope. The file each lives in should reflect that,
and the system should carry scope as structured data so vroom can
dispatch against it, the everything log can query against it, and
the planfile API can validate it.

The cost of getting this wrong is interventions/N drift in the
recursive-improvement sense: contributors spending effort figuring
out where work belongs, McLoop sessions loading the wrong context,
vroom reading a fragmented audit trail, or worse, McLoop creating
nested git repos because it conflated workspace root with package
scope. The cost of getting it right is bounded operational work —
the McLoop changes named in the Implications section, the
versioned ledger schema migration, and the scope-validation
contract. The trade favors getting it right, because the
recursive-improvement trajectory amplifies both the cost and the
benefit over time.
