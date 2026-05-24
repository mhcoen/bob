# Independent adversarial validation -- GPT

Headline verdict: revision 2 converges with Codex on the main
correction: cwd is not a safe architecture after consolidation, and
root/package scope must be explicit structured state. The revised
framing mostly holds, especially for hybrid root/package files and a
workspace-root audit trail, but the most important remaining correction
is to downgrade "ownership classification is robust" and "ledger
migration is straightforward" into explicit implementation gates. Scope
classification needs validation and repair paths, and the ledger schema
change is not just additive decoration because current event validation
is intentionally narrow.

Object under validation:
`bob/design/per-project-state-file-architecture.md` revision 2.

Inputs read:

- `bob/design/per-project-state-file-architecture.md`
- `bob/design/per-project-state-file-architecture-review-codex.md`
- `bob/design/repository-consolidation-checklist.md`
- `bob/design/recursive-improvement.md`
- `bob/design/mcloop-desplit-integration-plan.md` Sections 5, 6, 6.1
- current source spans named in the prompt
- current `vroom/README.md` and implementation source

## Section A: Verdict Per Scoping Decision

| Item | Verdict | Evidence and load-bearing reason |
|---|---|---|
| Explicit scope abstraction | Right | The abstraction is necessary. Current McLoop still derives `project_dir = checklist_path.parent`, puts `log_dir`, `PLAN.md`, and `BUGS.md` under it, and then uses it for checks and git setup (`mcloop/main.py:835-840`, `:893-914`). `_ensure_git` checks only `project_dir/.git` and runs `git init` there if absent (`mcloop/git_ops.py:49-63`). In a monorepo package directory, cwd inference alone would create or expect a nested repo. Revision 2 correctly moves to explicit `workspace_root`, `scope`, `scope_root`, `plan_path`, and `execution_cwd` (`per-project-state-file-architecture.md:42-87`). |
| `PLAN.md` | Worth revisiting | Hybrid root plus package plans are right: consolidation imports sibling repos into `packages/<name>/` (`repository-consolidation-checklist.md:3-4`, `:27-40`), and recursive improvement needs work that spans mcloop, orchestra, bob-tools, and duplo (`recursive-improvement.md:5-10`, `:162-167`). The revised ownership rule is a real improvement over path containment (`per-project-state-file-architecture.md:100-137`), but it is not yet reliable enough to be load-bearing without validation. Task creators can misclassify ownership before diagnosis is complete, and the proposal itself leaves this as an open question (`per-project-state-file-architecture.md:481-488`). |
| `BUGS.md` | Worth revisiting | Hybrid topology is correct, but failure-ownership classification is harder than the proposal implies. McLoop gives unchecked bugs priority and switches into bug-only mode before feature work (`mcloop/main.py:1122-1160`), so a root/package misfiled bug can either stall too much work or hide a cross-package failure. M1 FailureRecords are designed to carry classification, evidence pointers, lineage, and attempt number (`recursive-improvement.md:130-137`), but that machinery is not implemented yet (`recursive-improvement.md:238-246`). Until it exists, bug scope needs explicit verification and relocation semantics. |
| `CLAUDE.md` | Worth revisiting | Layered root plus package manifests are right; the current manifests are genuinely different, and revision 2 correctly moves from root-plus-one-package to task-context assembly (`per-project-state-file-architecture.md:172-235`). The implementation cost is still under-specified. Current `run_task` does not read `CLAUDE.md`; it passes `session_context` to either `invoke_code_edit` or a prompt builder and runs the CLI in `project_dir` (`mcloop/runner.py:711-789`). Conflict policy and token budget are named (`per-project-state-file-architecture.md:205-215`), but "package conventions override root" is too blunt for root safety rules. |
| `MAINTAIN.md` | Right | Revision 2 fixes the prior ambiguity. Current maintain mode is one-file/one-context: `project_dir = maintain_path.parent`, checks come from that directory, and the log is `<project_dir>/.mcloop/maintain-log.json` (`mcloop/maintain.py:240-275`, `:185-218`). Root invariants under workspace checks and package invariants under package checks (`per-project-state-file-architecture.md:258-267`) match the source behavior better than pretending both layers are one file. |
| `NOTES.md` | Right | Hybrid is correct. Current prompts tell the agent to append observations to `NOTES.md` (`mcloop/runner.py:427-444`), and CLAUDE sync appends diff summaries to `NOTES.md` (`mcloop/claude_md_check.py:1-9`). Under explicit scope, package tasks naturally write package notes and root tasks naturally write root notes (`per-project-state-file-architecture.md:269-286`). |
| `IDEAS.md` | Right | Hybrid is correct. Current `mcloop idea` appends to `project_dir / "IDEAS.md"` (`mcloop/idea_cmd.py:18-27`), so root and package variants fall out of explicit scope cleanly. Revision 2 correctly restores a root scratch layer for ecosystem ideas (`per-project-state-file-architecture.md:288-302`). |
| Everything log | Worth revisiting | Single workspace-root audit trail is right because recursive improvement needs machine-queryable history and cross-run pattern detection (`recursive-improvement.md:69-71`, `:197-203`). But the revised proposal overstates the ease of migration. The current event envelope has only envelope fields and `payload` (`bob_tools/ledger/schema.py:109-138`); `test_id`, `phase_id`, `touched_paths`, and commit metadata live in payloads and emitter logic (`bob_tools/ledger/schema.py:246-295`; `mcloop/ledger_emit.py:384-513`). Extra envelope and payload fields are rejected (`bob_tools/ledger/schema.py:26-29`, `:113`, `:249`, `:281`), and storage validates before append (`bob_tools/ledger/storage.py:1-10`). This is a versioned ledger-schema change, not a trivial additive default. |

## Section B: Answers To The Five Open Questions

1. The ownership rule is not reliable enough by itself. Keep it, but make
   scope verification a required part of task creation and completion.
   `planfile` should validate declared parent/scope consistency before
   accepting a generated task, and McLoop should emit or record a
   scope-mismatch finding if post-run touched paths contradict the task
   envelope. Do not rely on `touched_paths` as the creation-time source
   of truth; it does not exist yet. Use ownership as the declaration and
   post-run evidence as the audit check.

2. The context resolver is justified, but only as a bounded resolver, not
   as "concatenate every relevant full manifest and warn." Duplicating
   root conventions into every package will drift. The correct
   implementation is root plus one package by default, root-only for
   root design tasks, and explicit multi-package inclusion only when
   the task envelope names packages. Exceeding the token budget should
   fail closed or produce a deterministic summary artifact; a warning is
   not enough for autonomous dispatch.

3. The schema migration is not accurately described as straightforward
   additive fields with defaults. Current validation rejects additional
   envelope properties and additional payload properties
   (`bob_tools/ledger/schema.py:26-29`, `:113`, `:249`, `:281`), and the
   `Event.to_json()` serializer enumerates the envelope fields
   explicitly (`bob_tools/ledger/events.py:156-187`). This can be made
   backward-compatible, but it needs a ledger schema/version plan:
   either a versioned envelope extension plus projector compatibility,
   or a per-event `task_context` payload extension on the event types
   McLoop emits. It is not a bob-tools planfile canonical-form issue;
   it is a ledger event contract issue.

4. The McLoop changes should land before consolidation in a
   compatibility mode. Add the scope/workspace abstraction while each
   repo is still standalone, where `workspace_root == scope_root` and
   behavior can be proven unchanged. Then the consolidation commit can
   be mostly layout plus config. Landing the McLoop adaptation
   "alongside" consolidation creates the same kind of atomic-risk window
   the de-split plan tried to avoid in Section 5, and Section 6.1 shows
   what happens when forced ordering is not actually followed
   (`mcloop-desplit-integration-plan.md:1017-1039`).

5. Specify the minimal vroom-facing contract now, but do not design the
   whole dispatcher inside this proposal. Current vroom is only
   `vroom audit`: it reads an artifact, runs auditors in parallel, and
   optionally generates a corrected artifact (`vroom/__main__.py:106-194`,
   `vroom/orchestrator.py:12-26`). The README says daemon/backlog/
   executor/reflector are planned, not implemented (`vroom/README.md:9-26`,
   `:40-58`). The state-file architecture should reserve explicit
   `scope`, `plan_path`, `task_id`, and parent/failure links so vroom can
   use them later. It should not freeze backlog schema, scheduler
   behavior, or reflection queries beyond those minimum lineage fields.

## Section C: Convergence With Codex's Review

| Codex finding | Revision status | Validation |
|---|---|---|
| C1: hybrid `PLAN.md` decomposition needs ownership, not path surface | Partially addressed | Revision 2 replaces "lowest containing scope" with ownership and adds root/package parent-child lineage (`per-project-state-file-architecture.md:100-137`). That addresses the main Codex critique. It remains partial because the proposal asserts the rule is robust when classification determines scope before the change surface is known (`:112-119`), but it leaves reliability and verification as an open question (`:481-488`). My independent view matches Codex's direction but pushes harder on required validation. |
| C2: `CLAUDE.md` should be task-context layering, not package layering | Partially addressed | Revision 2 explicitly says task-context assembly, not strict root-plus-package (`per-project-state-file-architecture.md:191-197`), and names resolver inputs, conflict policy, and token budget (`:205-215`). This is a real improvement. It is still partial because current McLoop does not read or concatenate `CLAUDE.md` (`mcloop/runner.py:711-789`), and the proposed conflict policy is not precise enough for safety or autonomous dispatch. |
| C3: cwd convention is the main architectural bug | Addressed | Revision 2 makes scope explicit, says cwd inference is only a convenience, and identifies the nested-git failure mode (`per-project-state-file-architecture.md:42-87`, `:371-410`). This converges with Codex and with source: `project_dir` is still the PLAN parent (`mcloop/main.py:835-840`) and `_ensure_git` would initialize there (`mcloop/git_ops.py:49-63`). |
| C4: vroom implications are under-specified | Partially addressed | Revision 2 adds a task envelope and reflection queries (`per-project-state-file-architecture.md:426-449`) and acknowledges current vroom is an artifact auditor in the open questions (`:513-520`). It still uses "vroom dispatches McLoop" in the body as if current, while current source is only the audit pipeline (`vroom/__main__.py:106-194`) and the README marks dispatch/backlog/reflector as planned (`vroom/README.md:9-26`). The proposal now has the right minimum contract, but it should consistently label it future-facing. |
| C5: prior version locked down decisions left open by recursive-improvement | Partially addressed | Revision 2 adds an explicit out-of-scope section deferring storage format/path, self-improvement plan home, trust-ladder mechanics, cross-scope `.mcloop/runs/` aggregation, and FailureRecord schema specifics (`per-project-state-file-architecture.md:451-477`). That is substantial convergence. Remaining over-commitment: it still states `parent_task_id` and `failure_record_id` as required lineage fields before choosing whether those belong in the ledger envelope, specific payloads, or a separate FailureRecord corpus (`:326-346`). |

## Section D: Structural Findings

### D1. Ownership scoping needs validation and repair paths

The revised rule is better than the original path-surface rule, but it
is still easy to misclassify. Humans will file symptom-scoped bugs.
Duplo can generate a package leaf for what is really a contract change.
Future vroom can see a failure cluster but choose the wrong package
owner before diagnosis converges.

This matters most for `BUGS.md`, because current McLoop's bug-only mode
blocks feature work when unchecked bug tasks exist (`mcloop/main.py:1122-1160`).
A root bug incorrectly filed under one package becomes invisible to the
root reflection loop; a package bug incorrectly filed at root can stall
ecosystem-level work.

Required correction: define scope validation as part of task intake.
Minimum fields: `scope`, `scope_confidence`, `parent_task_ref`,
`declared_package_set`, and optional `scope_rationale`. On completion,
compare touched paths and failure evidence against the declared scope.
Mismatch should not silently move the task after the fact, but it should
emit a structured finding and optionally propose a relocation task.

### D2. The CLAUDE.md resolver is feasible only if it is deliberately smaller

The proposal says package conventions override root conventions and the
resolver warns on token budget (`per-project-state-file-architecture.md:205-215`).
That is not enough. Some root rules are safety rules and must not be
overridden by a package manifest. Some package manifests will contain
old standalone-repo assumptions, such as "operates on cwd" in duplo
(`duplo/CLAUDE.md:18-24`), that become ambiguous inside a monorepo.

Implement the resolver in two phases:

1. Root plus exactly one package, explicit precedence classes
   (`safety`, `workspace`, `package`, `task`) and fail-closed conflict
   detection.
2. Multi-package assembly only after tasks have explicit package sets
   and the resolver has a deterministic summarization or excerpting
   strategy.

Current McLoop already has a narrow insertion point: `run_task` passes
`session_context` into `invoke_code_edit` and prompt builders
(`mcloop/runner.py:714-728`, `:742-772`). Use that before inventing a
larger agent-context subsystem.

### D3. Explicit scope is not over-engineered, but it is incomplete

The five-part abstraction is the right minimum to prevent nested git
repos. It is not over-engineering. But the proposal undercounts the
places where current `project_dir` semantics are baked in.

Examples:

- CLI parsing has only `--file`, not `--workspace` or `--scope`
  (`mcloop/main.py:2324-2396`).
- Subcommands dispatch from `checklist_path.parent`, including
  `idea`, `maintain`, `install`, `wrap`, `sync`, `audit`, and
  `investigate` (`mcloop/main.py:436-485`).
- Git helpers beyond `_ensure_git` require workspace handling:
  `_checkpoint`, `_push_or_die`, `_stage_safe`, `_commit`, and
  `_changed_files` all take `project_dir` and run git there
  (`mcloop/git_ops.py:124-250`, `:365-420`).
- Run summaries still write under `<project_dir>/.mcloop/runs`
  (`mcloop/run_summary.py:75-97`), and lifecycle state uses
  `_project_dir` to write `.mcloop/interrupted.json`
  (`mcloop/lifecycle.py:39-45`, `:107-119`).

So the McLoop changes section is directionally correct but incomplete.
It should name a `WorkspaceContext` object and require every current
`project_dir` call site to be audited into one of: `workspace_root`,
`scope_root`, `execution_cwd`, `state_root`, or `plan_path`.

### D4. The everything-log migration is a ledger contract migration, not a simple defaulted-field addition

The revised proposal says the migration is "straightforward (additive
fields with defaults)" (`per-project-state-file-architecture.md:344-346`).
That is the weakest technical claim in the revision.

Current ledger validation is intentionally narrow: the envelope rejects
additional properties (`bob_tools/ledger/schema.py:109-138`), payloads
reject extras (`bob_tools/ledger/schema.py:246-295`), and storage
validates before writing (`bob_tools/ledger/storage.py:1-10`). The
serializer also enumerates envelope fields explicitly
(`bob_tools/ledger/events.py:156-187`). Adding `scope`, `plan_path`,
`task_id`, `parent_task_id`, or `failure_record_id` to the envelope will
not "just work" with existing readers and tests.

Recommendation: introduce a single structured `task_context` object in
the payloads that need it first (`commit_landed`, `test_failed`,
`work_observed`, maybe `finding_observed`), or version the envelope to
`schema_version = 1.1` with explicit projector compatibility. Do not
scatter scope fields across unrelated payloads without a schema plan.

### D5. The proposal still blurs current vroom with planned vroom

The open question is honest that current vroom is an artifact auditor
(`per-project-state-file-architecture.md:513-520`), but the body says
"vroom dispatches McLoop against tasks" (`:426-429`). Current source
does not. `vroom audit` reads a text artifact, finds config from cwd,
runs auditors, coalesces findings, and maybe writes a proposed
artifact (`vroom/__main__.py:106-194`; `vroom/config.py:184-194`;
`vroom/orchestrator.py:12-26`). The README explicitly says dispatcher,
backlog, scheduler, executor, verifier, and reflector are planned
(`vroom/README.md:9-26`, `:40-58`).

This is not a reason to remove vroom-facing fields. It is a reason to
label them as "future dispatcher contract" and keep them minimal.
Otherwise the state-file architecture risks becoming a backdoor design
for vroom's backlog and reflection loop.

### D6. The revised proposal still has two smaller factual issues

First, it still says each current repo carries every per-project state
file (`per-project-state-file-architecture.md:18-25`). The working tree
does not: mcloop has all six, duplo has four, orchestra has
`CLAUDE.md` and `IDEAS.md`, bob-tools has `PLAN.md`, `BUGS.md`, and
`NOTES.md`, and bob currently has none. The architecture can still
choose a uniform post-consolidation shape, but the context paragraph
should stop claiming current uniformity.

Second, the everything-log section cites `bob_tools/ledger/schema.py:109-138`
as if it contains `task_label`, `phase_id`, `touched_paths`, and commit
metadata (`per-project-state-file-architecture.md:316-323`). That span
is only the envelope. The event-specific fields are in payload schemas
and emitter code (`bob_tools/ledger/schema.py:246-295`;
`mcloop/ledger_emit.py:384-513`). This matters because envelope
migration and payload migration have different compatibility costs.

## Overall Decision

GO on the revised architecture direction: explicit scope, hybrid
root/package state where content exists at both levels, layered
manifests/invariants, and a single workspace-root audit trail.

NO-GO on treating revision 2 as implementation-ready. Before it becomes
a plan, it needs three concrete gates: a scope-validation/relocation
contract for task creators, a bounded CLAUDE context resolver design,
and a versioned ledger schema migration plan.

## Audit Methodology Confirmation

- Source access: yes.
- Git operations performed: read-only status/source inspection only.
- Files written outside designated path: none.
- Only file written: `bob/design/per-project-state-file-architecture-review-gpt.md`.
