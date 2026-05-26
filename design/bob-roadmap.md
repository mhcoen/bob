# Bob roadmap

## Lens

This document orders bob-ecosystem work by its contribution to the path from
"primitives that work" to "an ecosystem that does the engineering" — better
designs going in, fewer interventions during execution, structured handling
of the failures that do occur, and eventually self-improvement from the
corpus of those failures. Autonomy (measured as interventions per task
completed) is one outcome, not the through-line. Design quality, operator
situational awareness, and input quality to the pipeline are co-equal axes.

Sources cited in brackets: `pre-phase-d` = `bob/design/pre-phase-d-readiness.md`;
`recursive-improvement` = `bob/design/recursive-improvement.md`;
`BACKLOG` = `bob/design/BACKLOG.md`; `mcloop/IDEAS`, `mcloop/NOTES`,
`duplo/NOTES`, `duplo/REDESIGN-overview` are top-level files in those
packages.

---

## Top priority — design-quality loop

These four items unlock cross-model iterative design as a first-class
operation. Empirical observation: cross-model design conversation
(Opus ↔ Codex/Kimi) catches structural flaws before code is written far
more reliably than single-model design. The current bob ecosystem does not
have this as a configured workflow, only as a primitive substrate.

**0a. Iterative author↔critic role in orchestra.** Multi-round design
conversation between two different-model roles, with state threaded
across rounds. Convergence on two signals: (a) critic declares no
serious issues remaining; (b) hard round cap. Critic prompt locks
register to structural flaws, incorrect behavior, and unrecoverable
state — not stylistic, naming, or scope-expansion comments. Author and
critic share a symmetric definition of done. Emits final artifact and
full transcript for provenance.

Tested twice:
- *Mechanical:* orchestra integration test — does the role thread state,
  honor the round cap, parse the stop signal, emit a transcript?
- *Empirical:* run it on a real duplo design task that would otherwise
  be hand-mediated. The hand-run is the baseline. This is the test
  that decides whether the pattern is worth productizing.

**0b. Wire the iterative role into duplo's design phase.** The caller
that motivates 0a. Without 0b, 0a is infrastructure with no use.
Duplo today produces plans without cross-model design review; this
puts the review on the path.

**0c. Audit existing orchestra multi-role patterns before building 0a.**
The consolidated `bob/packages/mcloop/README.md` claims orchestra
provides `draft_then_adjudicate`, `propose_critique_synthesize`, and
council patterns wired into mcloop's pre-commit Reviewer slot. These
appear to be single-pass, but confirm in code before reimplementing.
If one is closer to iterative than the README suggests, extend it
rather than building parallel machinery.

**0d. Verify orchestra's `design/` folder is fully consolidated.** The
mcloop README links to `raw.githubusercontent.com/.../packages/orchestra/design/figures/triad.png`,
but `bob/packages/orchestra/design/` may not exist in the consolidated
tree — only a `.scratch/consolidation/orchestra-import/` staging copy
is confirmed present. Either the link is broken in the consolidated
repo, or the folder was dropped during consolidation. Small, independent.

---

## Wave 0 — Foundations

Everything downstream depends on these.

- **Real-time visibility into editor agent activity.** Stream orchestra's
  `log.jsonl` events into mcloop's progress channel as one-liners
  (reading X, editing Y, running pytest, N passed). Replaces the
  "still running, 1800s elapsed" ticker. [pre-phase-d 1.1 — named in
  the source as the single highest-leverage item]
- **Common `run_id` across mcloop / orchestra / editor logs.** Adopt
  orchestra's hex dir name as canonical; write into mcloop's
  `run_summary.json` and the editor log filename. Hard prerequisite
  for M1 FailureRecord lineage. [pre-phase-d 3.5; promoted from Tier 3]

## Wave 1 — Intervention-reduction quick wins

Small to medium individually; together eliminate roughly half of
observed intervention categories.

- **State coherence: `mcloop --reconcile` + transactional state writes.**
  Regenerate `CURRENT_PLAN.md` from master; PID-alive check on stale
  `active-pid`; verify recent `Complete:` commits against master `[x]`
  state. Every state-write is write-then-read-back-verify. Run reconcile
  by default at preflight. Eliminates the `rm CURRENT_PLAN.md` /
  `rm active-pid` intervention class. [pre-phase-d 1.3]
- **Tooling gaps: `bob-plan edit`, `bob-plan unstick`, `mcloop --status`,
  `mcloop --kill`.** Each was hand-rolled repeatedly during Phase C.
  ~30–100 LOC each. [pre-phase-d 1.5]
- **`mcloop --recover` from stuck-state signatures.** Enumerates known
  stuck-state patterns (dead PID + active-pid present; stale
  CURRENT_PLAN.md; orphan checkpoint commits) and offers recovery
  actions. Possibly fold with state coherence and tooling gaps into
  one umbrella effort. [pre-phase-d 2.1]
- **Per-tool trust ladder for the permission hook.** `Read`/`Grep`/`Glob`
  auto-approve; `Bash(ruff/pytest/mypy)` in workspace auto-approve;
  `Edit`/`Write` in workspace auto-approve; destructive/network
  commands route to human. YAML/JSON-driven. Eliminates the
  missed-Telegram-prompt intervention class. [pre-phase-d 2.3]
- **Self-monitoring: `SessionHealth` + repetition detector.** Stateful
  reducer over the Wave 0 visibility stream — `last_tool_use_ts`,
  `tool_use_count_5min`, `repeated_tool_signatures`,
  `current_activity_class`. Alert (not kill) on repetition or
  thinking > 5 min. Detects the failure mode M1 most needs to catch:
  loop produces output but makes no progress. Idle-timeout alone
  misses it. [pre-phase-d 1.2]
- **Refine attribution-blocking hook.** Match attribution patterns
  (`Co-Authored-By:`, `Authored by Claude`) instead of bare words.
  Eliminates the split-`git add`/`git commit` workaround currently
  required. [pre-phase-d 2.4]
- **mcloop `changed_files` spans the whole workspace.** Originally
  framed as "merge cross-repo diffs across sibling repos"; after
  consolidation the underlying issue is smaller — mcloop's detector
  must see edits across all `packages/<name>/`, not just `project_dir`.
  [pre-phase-d 1.4, reframed post-consolidation]

## Wave 2 — M1: Self-diagnosis

Turns Wave 0+1 signals into structured records.

- **`FailureRecord` schema.** Classification enum; evidence pointers;
  recommended next action with a machine field; severity;
  run_id/task_id/attempt lineage. Storage: JSON-per-run for M1,
  SQLite later for M3. [recursive-improvement M1]
- **Emitters at the 5 known failure sites.** Orchestra wall-clock and
  idle timeouts; mcloop no-op gate; bob-tools canonical validator;
  mcloop preflight; auto-mode classifier denial. Each writes JSON
  next to the run summary. [recursive-improvement M1]
- **`mcloop --diagnose-last-failure`.** Reader + human-readable
  summarizer + recommended-fix lister. [recursive-improvement M1]
- **Regression harness for the 8 known intervention categories** from
  pre-phase-d's table. Each category must reproduce in the harness
  and produce a correctly-classified FailureRecord. Acceptance gate
  for "M1 is done." [recursive-improvement M1]
- **mcloop graceful recovery from transient API/infrastructure failures.**
  Classify HTTP 5xx/429 + provider "try again" envelope as transient;
  back off and retry without consuming the task's genuine-failure
  budget. Natural fit as a FailureRecord classification + auto-action.
  [BACKLOG #4]
- **Test/gate infrastructure cleanup.** Run all PLAN.md / BUGS.md /
  fixtures through `bob-plan fmt` once; commit. Make phase-boundary
  check_commands configurable in PLAN.md. Require each task's
  check_commands to exit 0 with explicit acceptance condition.
  Do before the M1 corpus accumulates, so it isn't polluted by
  inconsistent gates. [pre-phase-d 1.6]

## Wave 3 — M2: Self-repair

Closes the loop.

- **`mcloop --propose-fix-from-failure <record-id>`.** Converts
  FailureRecord → candidate PLAN.md task with failure signature,
  suspected file:line, regression-reproducing test sketch, acceptance
  criteria, link to original record. [recursive-improvement M2]
- **Self-improvement PLAN.md** (mcloop / orchestra / bob-tools
  internal). Where proposed fix tasks accumulate. Open question:
  separate file vs Phase D of bob-tools/PLAN.md.
  [recursive-improvement M2]
- **Trust ladder of auto-promotable fix classes.** Initial set:
  `bob-plan fmt`, ruff `--fix`, `rm .mcloop/active-pid` on confirmed-
  dead PID, `pip install -e` on missing declared deps. Each addition
  is itself a human-reviewed decision. [recursive-improvement M2]
- **Planted-bug acceptance test.** Inject a known bug in (e.g.)
  `orchestra/adapters/_subprocess.py`; observe it caught, proposed,
  approved (manual or via ladder), and landed by mcloop with no
  further intervention. Acceptance gate. [recursive-improvement M2]
- **Deterministic bugfile layer.** Apply the planfile pattern to
  BUGS.md: schema with temporal/provenance fields, deterministic
  parser/renderer/operations, CLI layer. Trust-ladder operations on
  BUGS.md need a non-corruptible target. [BACKLOG #1]

## Wave 4 — M3: Self-extension

Gated on ~30+ FailureRecord corpus from Wave 2.

- **Executable spec / cross-validation corpus for continuous rebuilding.**
  Frozen (input plan, expected validation outcome) and
  (failure scenario, expected classification) pairs. Load-bearing
  safety property: self-modifications cannot weaken this. Merge
  BACKLOG #2 and recursive-improvement M3 — same artifact, two framings.
- **Pattern detector over the FailureRecord corpus.** Clusters by
  signature, surfaces patterns (e.g., "5 records hit the canonical-
  validation wall — missing `validation_legacy` mode").
  [recursive-improvement M3]
- **Capability-proposal generator.** Pattern → multi-task PLAN slice
  via the `bob_tools.planfile` API. Shares machinery with the M2
  fix-proposer; only the input shape differs. [recursive-improvement M3]

---

## Independent tracks

Parallel to the waves; not wave-gated.

- **mcloop branch/worktree isolation for normal runs.** Opt-in via
  `--worktree` first. Shrinks blast radius of mid-run crashes; safer
  substrate for M2 self-modification later. Same primitive as
  `parallel-implementation.md`, different framing. [mcloop/IDEAS #1]
- **`mcloop consult <doc> --reviewer codex`.** Single-pass cross-model
  review. *Note: top-priority item 0a is the iterative generalization
  of this; this independent item may collapse into 0a — verify after
  0a lands.* [mcloop/IDEAS #2]
- **Duplo redesign Phase 2: migration detection.**
  [duplo/REDESIGN-overview, named as the next thing to land]
- **Duplo redesign Phase 3: pipeline integration.** Largest phase.
  [duplo/REDESIGN-overview]
- **Duplo redesign Phase 4: drafter + `duplo init`.**
  [duplo/REDESIGN-overview]
- **Duplo redesign Phase 5: cleanup.** [duplo/REDESIGN-overview]
- **`duplo plan repair --in PLAN.md --out PLAN.md` CLI.** For
  structurally-corrupt prior plans the new `plan_document.py` rejects.
  [duplo/NOTES todo #1+#2, merged]
- **Session memory carry-over across editor sessions.**
  `.mcloop/session_memory.md` written by agent at task end; read as
  context next task. Multiplies M1's value once shipped (agent reads
  its own past FailureRecords). [pre-phase-d 3.1]
- **`.duplo/` self-targeting guard in duplo.** Investigate why
  `.duplo/` exists in duplo's own repo root; guard against
  self-targeting from there. Small. [BACKLOG #3]
- **mcloop orphan-process audit at other `launch()` call sites.**
  `run_gui` kill-on-return; `lifecycle.cleanup_orphan_processes`.
  Small defensive. [mcloop/NOTES item 2]

---

## Dropped (auditable log)

- **BACKLOG #5 — NOTES.md commit-log summarization provider selection.**
  Post-bugfix tuning, not a tracked design item. 5-minute config
  decision once the payload-bounding bug is fixed.
- **`parallel-implementation.md` as a standalone effort.** Worktree
  primitive subsumes into the mcloop/IDEAS #1 independent track.
  Multi-agent parallelism is throughput, not intervention reduction —
  defer until single-agent autonomy works.
- **Pre-phase-d Tier 3.2 (cost visibility), 3.3 (per-task model
  routing), 3.4 (reviewer integration audit), 3.6 (claude CLI
  pinning).** No autonomy or design-quality impact on current
  evidence. Revisit after Wave 3.
- **mcloop/NOTES item 12.2 — friendlier `resolve_workspace_context`
  error.** Cosmetic; no impact.
- **Pre-phase-d "Things this document does not cover (yet)" — all 10
  items** (editor prompt design, plan task granularity, BUGS.md
  lifecycle, lineage tracking, acceptance-evidence grammar,
  multi-operator support, audit-trail completeness, plan-level
  dependencies, MTBI measurement, cost-regression detection).
  All open-ended; revisit after Wave 3.

## Needs verification before final inclusion

These items are flagged uncertain. Resolve by direct source inspection.

- **Pre-phase-d 2.5 "Plan format reconsideration (CURRENT_PLAN.md
  drift)."** The desplit project may have eliminated `CURRENT_PLAN.md`
  or transformed how it's regenerated. If it no longer exists in the
  consolidated layout, this item is obsolete. If it does, Wave 1
  priority.
- **duplo/NOTES todo #3 "Migrate mcloop's `checklist.py` STAGE_RE to
  plan_document."** The desplit work was moving mcloop onto
  `bob_tools.planfile`. May already be addressed; confirm the specific
  STAGE_RE call site at `mcloop/checklist.py:~12`.
- **Pre-phase-d 1.6 sub-item: "phase-boundary check runs in 1s,
  suspiciously fast."** Worth a one-pass check of what actually runs
  at phase boundaries before this becomes a Wave 2 task.
