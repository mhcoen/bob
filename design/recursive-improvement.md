# Recursive Self-Improvement: Design

## Vision

The stated long-term goal of this project is for the development pipeline
itself — mcloop, orchestra, bob-tools, duplo — to become recursively
self-improving. mcloop drives editor agents through PLAN.md tasks today.
The horizon: mcloop driving editor agents through a PLAN.md that
describes improvements to mcloop, orchestra, and bob-tools themselves,
with each completed cycle reducing the human effort needed for the next.

This is not a metaphor. It is a concrete engineering objective with a
single metric and a tractable path. The user's framing ("singularity")
is taken literally: a development pipeline where the rate of system
improvement per unit of human effort grows over time, because the system
increasingly authors, executes, and validates its own improvements. The
horizon is not "no human" but "human-as-objective-setter,
system-as-implementer."

## The Metric (a thermometer, not a target)

**Human interventions per N tasks completed.**

An intervention is any human action other than:
- providing the high-level objective (a PLAN.md task),
- reviewing a proposed change before it lands.

Things that count as interventions today:
- Ctrl-C'ing a hung mcloop
- removing a stale `.mcloop/active-pid`
- hand-running `bob-plan done` to mark a task complete
- editing a plan file directly to fix a typo or unstick a gate
- hand-patching a discovered infrastructure bug
- reading logs to figure out what failed
- restarting after an env/auth issue

Today (mid-Phase-C, 2026-05-21), the rate is roughly **1-2 interventions
per task completed.** The number is a thermometer: it reports the
current state of the loop. It is not a milestone gate, and there is no
fixed schedule for it to drop. The interesting question is not "did we
hit interventions/N = 0.5 yet" but "what's the next category of
intervention we can eliminate, and what does eliminating it teach us
about what to build next."

The qualitative shift that matters more than the number: the type of
intervention changes over time. Early interventions are "this is
broken, fix it." Late interventions are "consider this objective."
The number conflates both, but the latter is closer to the actual
goal — a system that needs direction, not repair.

## What "Closed Loop" Means Operationally

A closed loop has four properties:

1. **Self-diagnosing.** When something fails, the loop produces a
   structured record of what failed, why, and what would unblock it —
   without a human reading raw logs.

2. **Self-repairing.** When the loop hits an infrastructure bug in its
   own code, it can propose a PLAN.md task that, when executed, fixes
   the bug. The fix lands through the same task-execution mechanism
   the loop uses for any other change.

3. **Self-extending.** The loop reads its own failure history,
   identifies patterns that aren't single-bug fixes but missing
   capabilities, and writes tasks to add those capabilities. The
   ratchet by which the system gets better at improving itself.

4. **Self-validating.** Each cycle produces evidence (commits, tests,
   typed acceptance artifacts) that subsequent cycles can read and
   reason about. The history is machine-queryable, not folkloric.

## Current State (2026-05-21)

The pieces:

- **mcloop**: task driver. Reads PLAN.md, picks next unchecked task,
  invokes editor agent, validates via configurable check_commands,
  commits result, advances. The orchestrator.
- **orchestra**: workflow runtime. Hosts the editor agent and manages
  the inner `claude` subprocess. Owns transcript capture, timeouts,
  watchdog, pending-approval signaling.
- **bob-tools/planfile**: deterministic PLAN.md parser, renderer,
  validator, mutation API. The structural foundation. Makes plan
  format machine-owned and machine-verifiable.
- **duplo**: generates PLAN.md files from product specs (typed, not
  markdown). The plan-authoring side of the pipeline.

The loop already closes once per task at minimum. Phase C Stages 1-23
are evidence. But the human intervention rate is the bottleneck.

### Categories of intervention observed in this session

| Category | Specific manifestations |
|---|---|
| Auth / env | subscription not logged in; ambient `ANTHROPIC_API_KEY` masking issues; broken `~/.local/bin/claude` symlink |
| Hangs | orchestra reader thread stuck on dead subprocess; missing idle-progress detection; 30-min wall-clock too short for substantive tasks |
| False-negative gates | no-op acceptance gate inspecting wrong data window; canonical-validation too strict for legacy plans; check-tool-name requirement on terse final summaries |
| Cross-repo coordination | mcloop's `changed_files` detector blind to edits in duplo while running from bob-tools |
| Plan format drift | bob-plan `done`/`fail` rejecting their own load output under canonical save default |
| Human routing | Telegram permission hook prompts that get missed → 10-min timeout chains |
| Infrastructure typos | `--input-format text` breaking implicit stdin-as-prompt; 208MB binary accidentally committed via malformed `ln -sf` |
| State propagation | stale `CURRENT_PLAN.md` slice not regenerated after master changes; "Complete:" commit reports without master `[x]` update |

Each row is a known intervention type with a known fix shape. None are
fundamental. All are the consequence of the loop not yet having the
machinery to diagnose itself.

## The Path: Three Workstreams (Co-Developable)

These are named M1, M2, M3 for reference but are not sequential gates.
Pieces of M2 and M3 can be co-developed with M1 — and should be, since
some of the M3 infrastructure (the FailureRecord corpus storage) is
shared with M1's emitter design. Sequencing them strictly would
artificially serialize work that can run in parallel.

The intervention-rate metric is a thermometer, not a gate. It says
how much capacity has been freed; it does not say a workstream is
"done." Some interventions will resist reduction even after the
infrastructure exists, and that's information about what to build next.

### M1 — Self-Diagnosing

**Goal:** when the loop fails, it produces a structured failure record
that says what happened and recommends a fix, without a human reading
raw logs.

**Deliverables:**

- **Failure-record schema** (`mcloop.failure_record.FailureRecord`):
  classification (auth_failure / hang_subprocess_dead / hang_idle /
  no_op_gate_false_negative / canonical_validation_drift /
  cross_repo_blind / preflight_failed / unknown), evidence pointers
  (log paths, pid file, transcript URIs, run_summary path, git refs
  before/after), recommended next action (free-form text plus a
  machine field naming the suggested fix template), severity, lineage
  (run_id, task_id, attempt_number).
- **Emitters at every existing failure site:** orchestra's wall-clock
  and idle timeouts; mcloop's no-op gate; bob-tools' canonical
  validator; mcloop's preflight; the auto-mode classifier denials.
  Each writes a FailureRecord JSON next to the run summary.
- **`mcloop --diagnose-last-failure`** prints the most recent record
  in human-friendly form and lists the recommended fix steps.
- **All 8 intervention categories from this session** detectable
  from artifacts already on disk.

**Evidence we'd look for:** each of the 8 categories reproduces in a
test harness and produces a correctly-classified FailureRecord. The
human-readable summary names the actual root cause. The intervention
rate observed in subsequent runs drops to a measurable degree (this
is the thermometer reading, not a pass/fail gate — if it doesn't drop,
that's data about what to build next).

### M2 — Self-Repairing

**Goal:** when the loop hits an infrastructure bug in its own code,
it proposes a PLAN.md task that fixes the bug. The next loop iteration
executes the proposed task. Human role shifts from author to reviewer.

**Deliverables:**

- **`mcloop --propose-fix-from-failure <record-id>`** converts a
  FailureRecord into a candidate PLAN.md task: failure signature,
  suspected file:line, regression-reproducing test sketch, acceptance
  criteria, links to the original record.
- **Self-improvement PLAN.md** for mcloop / orchestra / bob-tools
  themselves. Proposed fix tasks accumulate here.
- **Trust ladder:** an enumerated list of fix classes that auto-promote
  from proposed to approved without human review. Start narrow:
  test-fixture re-formats via `bob-plan fmt`, ruff/format auto-fixes,
  stale-active-pid cleanups. Each addition to the ladder is itself a
  human-reviewed decision.
- **Auto-execution gate:** approved tasks (manual or via trust ladder)
  run through the same mcloop loop that runs any other task. The
  diagnostic loop and the execution loop share infrastructure.

**Co-development with M1:** the FailureRecord schema's `recommended
next action` field is the seed of the fix-proposer. Designing it
M2-aware from the start avoids a schema migration later. The fix
templates and the failure classifications are two sides of the same
table.

**Evidence we'd look for:** a planted bug in
`orchestra/adapters/_subprocess.py` is caught, proposed, approved
(manual or via ladder), and landed by mcloop with no further
intervention. Then: real bugs (not planted) start being caught and
auto-proposed at observable rate.

### M3 — Self-Extending

**Goal:** the loop reads its own failure history across many runs,
detects patterns that aren't single-bug fixes but missing capabilities,
and writes tasks to add those capabilities.

**Deliverables:**

- **Pattern detector** over the FailureRecord corpus: clusters records
  by signature, surfaces patterns ("5 failures across 30 days all hit
  the canonical-validation strictness wall — implies missing
  `validation_legacy` mode").
- **Capability-proposal generator:** turns a pattern into a multi-task
  PLAN.md proposal (the same shape we wrote by hand for Stage 16's
  "canonical save default" work).
- **The loop driving its own PLAN.md** the way duplo drives bob-tools'
  PLAN.md: structured Plan objects via the bob_tools.planfile API,
  validated, lineage-tracked, persisted via `save(validation="canonical")`.
- **Cross-validation corpus** that locks in current guarantees so
  self-modifications can't degrade existing validation.

**Co-development with M1/M2:** the corpus that the pattern detector
reads is the corpus the M1 emitters produce. Storing FailureRecords
in a structure designed for pattern queries from day one is a small
cost; retrofitting it later is large. The capability-proposal
generator and the fix-proposer share most of their machinery — both
turn evidence into PLAN.md tasks; only the input shape differs.

**Evidence we'd look for:** capability gaps detected from failure
history, proposed as multi-task PLAN slices, reviewed/approved by a
human, landed by mcloop. The human's contribution to those tasks was
setting an objective and approving an output, not writing the text.

## Architecture Implications

Pieces of today's infrastructure already lie on the M1+ path:

- **bob-tools/planfile** as deterministic structured format is the
  lingua franca for self-modifying plans. Without it the loop cannot
  reason about its own plans. Phase C is building this.
- **mcloop's TaskEntry records** in `.mcloop/runs/<timestamp>_run-summary.json`
  are prototypes of structured records. Need richer schemas
  (failure classification, evidence pointers).
- **orchestra's stream-json transcripts** are machine-readable agent
  transcripts. Need to be promoted from logs to first-class artifacts
  the failure detector can index.
- **The structured ledger events** (`commit_landed`, `work_observed`)
  are the audit trail. Need lifecycle events for failures too.

What is NOT in place:

- FailureRecord schema and emitters.
- A pattern detector across run_summary.json files.
- A "propose fix from failure" task generator.
- A separate self-improvement PLAN.md for mcloop's own infrastructure
  (currently bob-tools/PLAN.md is the only driver).
- A trust ladder defining auto-approval classes.
- A frozen acceptance corpus that pins the current set of guarantees
  across self-improvements.

## How mcloop Builds This

The bootstrap: this document gets translated into a new PLAN.md (a
Phase D extension to bob-tools/PLAN.md, or a separate plan in a new
home — see Open Questions). Stages cover:

1. FailureRecord schema and emitters (M1 part 1)
2. `--diagnose-last-failure` and the human-readable summarizer (M1 part 2)
3. Regression harness for the 8 known failure categories (M1 acceptance)
4. `--propose-fix-from-failure` task generator (M2 part 1)
5. Self-improvement PLAN driver (M2 part 2)
6. Trust ladder (M2 part 3)
7. Planted-bug acceptance test (M2 acceptance)
8. Pattern detector across FailureRecord corpus (M3 part 1)
9. Capability-proposal generator (M3 part 2)
10. Frozen acceptance corpus + cross-validation harness (M3 part 3)
11. 30-day evidence run (M3 acceptance)

Each stage runs through mcloop the same way Stages 1-23 of Phase C do.

The recursion begins at M1 acceptance: once the failure detector exists
and emits typed FailureRecords, the next mcloop run that hits an
infrastructure bug auto-emits a structured record. M2 makes that
record actionable. M3 makes recurring patterns auto-generate work.

## Scope and safety properties

The "singularity" framing names a real property: improvement-rate
growth from internal work. We're not pre-committing to where that
growth tops out, because we don't know — that's the point of building
the system. Statements like "this is not AGI" or "the loop will be
bounded" are not claims we get to make in advance; they're outcomes
we'll observe as we build.

What we DO want to preserve, regardless of where the capability
trajectory leads:

- **Every change comes from a task.** No untracked mutation. Provides
  auditability at any capability level.
- **Every task has a T-id, a stable provenance, and acceptance
  evidence.** Provides traceability at any capability level.
- **The system cannot relax its own validation without passing a
  frozen acceptance corpus.** This is the load-bearing safety
  property — it prevents the loop from degrading the gates it's
  trying to pass. The corpus is human-curated; updating it is a
  human-reviewed decision.
- **Human-approved-by-default for any task that touches the trust
  ladder, the validation corpus, or the self-improvement PLAN
  driver itself.** The meta-level is reviewed even when the
  object-level becomes automated.

These properties are framed as invariants that hold as capability
grows, not as a ceiling on capability. The intent is that as the
loop becomes more capable, the human's role shifts from author to
reviewer to objective-setter, and the safety properties define what
"reviewer" and "objective-setter" mean in operational terms — not
to slow the trajectory but to keep it interpretable as it advances.

## Open Questions

1. **Home for the self-improvement plan.** Phase D in `bob-tools/PLAN.md`,
   or a new repo `bob` carrying it? Cleaner separation argues for
   new home; reuse of existing infra argues for Phase D. Defaulting
   to Phase D unless someone names a stronger reason for split.

2. **FailureRecord storage.** Per-run JSON files (current pattern) or
   SQLite ledger (queryable across runs)? Pattern detection in M3
   needs the latter. Probably JSON for M1, SQLite for M3.

3. **Trust ladder bootstrapping.** Which classes go in first? Proposed
   initial set: `bob-plan fmt` re-formats, ruff `--fix` outputs,
   `rm .mcloop/active-pid` on confirmed-dead PID, `pip install -e`
   on declared but missing deps.

4. **Schema sharing.** Should FailureRecord share fields with the
   existing TaskEntry / settlement events? Probably yes — common
   evidence pointers, common task_id/run_id lineage.

5. **Cross-validation corpus.** What pins the current guarantees?
   Probably: a frozen set of (input plan, expected validation outcome)
   pairs plus a frozen set of (failure scenario, expected
   FailureRecord classification) pairs.

6. **Editor agent introspection.** Should the editor agent be able to
   read its own past FailureRecords as context for the current task?
   Probably yes at M2 — gives it the memory to avoid repeating the
   class of mistake.

## Immediate Next Steps

1. Finish Stages 21-23 of Phase C (current). Closes the duplo migration,
   deletes `plan_document.py`, demonstrates end-to-end canonical
   round-tripping with no `bob-plan migrate` in the duplo path.

2. Draft the M1 milestone as a Phase D PLAN.md slice. Use the same
   typed `bob_tools.planfile` API duplo now uses post-Stage-18.
   Validate with `validate_plan(constructed=True)`. Persist via
   `save(validation="canonical")`.

3. Stage D.1: FailureRecord schema + emitters at the 5 known failure
   sites we already touched this session (orchestra timeouts, mcloop
   no-op gate, canonical validator, preflight, classifier denial).

4. Stage D.2: `mcloop --diagnose-last-failure` reader + summarizer.

5. Stage D.3: regression harness reproducing each of the 8
   intervention categories from this session's table. Each must
   produce a correctly-classified FailureRecord.

After D.3 lands, measure intervention/N over the next 10 Phase D
tasks. If it dropped from 1.5 to ≤0.5, M1 is met and we plan M2.
If it didn't drop, refine M1 before proceeding.

## Closing Note

The loop has already closed once per Phase C task with substantial
human effort. Reducing that effort is the immediate work. The
machinery to do it (FailureRecord schema, fix-proposer, pattern
detector, trust ladder, cross-validation corpus) is buildable with
the infrastructure already being assembled (planfile, ledger,
stream-json transcripts, structured task ids).

How far the trajectory goes is something we'll observe, not predict.
The "singularity" framing names the property we're aiming at —
marginal cost of each self-improvement trending downward — without
committing to where that trend tops out. The work is to make the
trend visible, then to push it.

The pieces are M1 (self-diagnosing), M2 (self-repairing), M3
(self-extending), the parallel-implementation probe (worktrees), and
the Phase C.5 readiness items that create the conditions for all of
the above. None of these are strictly sequential; co-developing
shared infrastructure is faster and produces a more coherent system.

The metric (interventions/N) is the thermometer. The deliverable is
mcloop running on its own PLAN.md to improve mcloop, with humans
setting objectives and reviewing outputs. How much beyond that the
system reaches depends on how the pieces actually behave under
scale — and that's information we get by building, not by
predicting.
