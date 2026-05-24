# Parallel Implementation: Feasibility & Prior Art

## What is being proposed

Drive multiple editor agents simultaneously, each in its own git
worktree on its own branch, working on tasks designed to be mergeable.
On task completion, merge each branch back to the trunk. Goal:
substantially reduce wall-clock for stage completion, especially for
sets of independent tasks within a stage.

The motivating observation: Phase C's 30-minute editor sessions are a
serialization point. Many Phase C tasks were independent of each other
in content (different files, different concerns) but ran sequentially
because the loop has no parallel-execution capability. Phase D will be
similar — FailureRecord schema, emitters at different sites, the
diagnose CLI, the trust ladder definition — all independent enough to
run in parallel.

The question this document examines is: **is this realistic, what
exists already, and what's the smallest tractable starting point**.

## Two distinct goals (separating them matters)

**Mergeable design** — structuring tasks so their outputs don't
conflict by construction. Different files, different functions,
explicit non-overlap. The merge is a no-op or near-no-op.

**Reactive conflict resolution** — letting tasks proceed in parallel
and dealing with conflicts as they arise.

These are different problems. The first is hard at design time
(partitioning the PLAN.md correctly) but trivial at run time. The
second is easy at design time (just spawn agents) but hard at run time
(automated semantic merge is unsolved).

Recommendation: **target mergeable-by-construction first**. It's the
tractable path. Reactive conflict resolution is a research problem
(see prior art below) and likely not worth solving directly; better
to refine partitioning to make it rare.

## Prior art survey

### Mature, off-the-shelf

**`git worktree`** — built into git since 2015. `git worktree add <path>
<branch>` creates a separate working directory backed by the same `.git`
dir. Each worktree has its own checkout, its own index, its own branch.
Mature, stable, well-documented. No need to reinvent.

**`git rerere`** ("reuse recorded resolution") — when you resolve a
merge conflict, git records the resolution. Next time the same
conflict pattern appears, git auto-applies the recorded resolution.
Useful for repetitive structured conflicts (e.g., import-list merges).
Built-in, opt-in via `git config rerere.enabled true`.

**`git merge-tree`** — performs a three-way merge in memory without
touching any worktree. Useful for "would these branches merge cleanly"
checks before actually doing it. Built-in.

**Sapling (Meta)** — git-compatible source control with first-class
stacked branches and conflict resolution. Open source. Not a
multi-agent orchestrator but provides better primitives for
concurrent work than vanilla git.

**Jujutsu (`jj`)** — git-compatible VCS with conflict-tolerant
semantics: conflicts can exist as first-class objects rather than
being resolved immediately. Working on conflicted states is supported.
Probably overkill for this project; mentioned for completeness.

### Stacked-PR tooling

**GitButler** — virtual branches that let you work on multiple
branches simultaneously on the same physical worktree. Designed for
human developers, not agents, but the primitives (branch slicing,
diff partitioning) are interesting. Open source.

**Graphite, Aviator, Stacked PRs (Sapling)** — workflow tools for
managing chains of dependent PRs. Useful pattern for the loop's
output (stage = stack of related task PRs that land together).
Mostly UI-level, but the underlying convention is portable.

**Mergify, Kodiak** — automated PR merging based on rules. Could be
useful when the loop produces PRs (not direct commits to main).
Probably not relevant if mcloop continues committing directly.

### Multi-agent / AI-driven

**Anthropic Claude Code's Task tool** — spawns parallel subagents
within a single Claude Code session. We've used it. It parallelizes
tool calls inside one task; it does not parallelize tasks themselves
across worktrees. Useful primitive for in-task fan-out (e.g., "have N
subagents each explore a file") but not for our parallel-implementation
goal.

**Aider** — AI pair-programming. Multi-file edits within one session.
Single-agent. Has a `--watch-files` mode but not multi-worktree.

**Cursor / Continue** — IDE-level AI. Single-agent per session.
Multi-window setups exist but no orchestration.

**Devin (Cognition)** — closed product claiming parallel agent
execution. Proprietary, no inspectable design. Probably similar to
what we'd build, but no reusable code.

**SWE-agent, AutoCodeRover, CodeAct, ReAct** — academic single-agent
systems on isolated tasks. Useful for understanding agent loop design,
not for parallel orchestration.

**OpenDevin / OpenHands** — open-source attempts at the Devin pattern.
Worth looking at for orchestration patterns even if not directly
reusable.

**MetaGPT, AutoGen, CrewAI** — multi-agent frameworks where agents
have roles (planner, coder, reviewer). Mostly conversational
orchestration rather than git-worktree-level parallelism. The "roles"
pattern might be reusable for partitioning (e.g., one agent's role is
"editor of bob_tools", another is "editor of duplo").

### Distributed-build tooling (orthogonal but informative)

**Bazel / Buck2 remote execution** — parallel build distribution.
Conceptually similar to "parallel agents on independent units" but
solving a different problem (compile graph, not edit graph). The
build-graph dependency model is worth borrowing for PLAN.md tasks.

### What does not exist yet

- An open-source orchestrator that drives N parallel coding agents
  through git worktrees with structured merge and PLAN.md-driven
  task partitioning.
- "Mergeable by construction" task partitioning where the plan format
  itself ensures non-conflicting parallel work.
- Shared-context multi-agent where one agent's discoveries feed
  another's prompt.

These are active research questions and they intersect several open
problems: multi-agent LLM coordination, semantic merge of structured
edits, scheduling under uncertainty, and the broader question of
self-improving development pipelines. None of these are settled.
There is published work on pieces of each, but no proven end-to-end
system. Building this is novel investigation into open problems.

## The argument for scaling: induction, not prediction

The current state is effectively N = 1.x — one mcloop session at a
time, plus occasional informal parallelism via codex on the side that
requires manual tracking of non-interference. Moving to N = 2 is both
an immediate doubling of throughput AND removes the human-tracked
coordination tax.

But the more important reason to target 2 first is structural: **N = 1
→ N = 2 is the same shape of step as N = k → N = k + 1**. Whatever it
takes to make two parallel agents safely converge — partitioning,
clean-merge gates, state isolation, conflict-halt semantics — is the
same machinery scaled out. Each additional agent stresses the same
seams.

So the right framing is an induction argument:

- **Base case.** Demonstrate two agents running concurrently in
  separate worktrees on a single stage, producing changes that merge
  cleanly to trunk, with acceptance running on the merged result. The
  current N=1.x state plus codex-on-the-side already meets a
  degraded version of this in practice; formalizing it removes the
  human coordination tax.
- **Inductive step.** Demonstrate N = k + 1 by adding one agent to a
  working N = k configuration, characterizing exactly what changed:
  rate-limit headroom consumed, conflict rate, coordination latency,
  state coherence under contention.
- **The argument is not that N grows without bound.** It's that we
  don't get to claim a ceiling without trying the next step. Each
  successful N = k → N = k + 1 is evidence; each failure is data
  about a specific characterizable problem (rate limit hit, conflict
  rate spiked, coordinator lag dominates), which is a problem to
  solve and not a fixed wall.

This means the design doesn't bake in a target N. It bakes in an
*experimental protocol* for finding what the actual ceiling is and
moving it.

## What we don't yet know (and how we'd find out)

Things that will matter at scale but are not measured at N = 1:

- **How rate-limit headroom degrades with N.** Two opus sessions in
  parallel may halve the time-to-rate-limit; four may quarter it. Or
  the relationship may be non-linear because cache hits cluster. Find
  out by running N = 2 and recording the rate-limit-event events
  stream-json already emits.
- **How conflict rate grows with N.** With perfect partitioning,
  conflict rate is zero at any N. With imperfect partitioning, it
  scales with the probability of two agents touching the same file.
  Measure by trial: at N = 2, count pre-flight merge-tree failures
  across a 50-task corpus; estimate the rate; predict and then
  measure at N = 3.
- **Where coordination latency dominates.** At N = 2 the coordinator
  is essentially free. At N = 10 it may not be. Instrument the
  coordinator's wall-clock contribution per task and watch the trend.
- **Where semantic merge becomes necessary.** With strict
  partitioning, textual three-way merge is sufficient. But some
  "non-conflicting" changes may produce broken merged behavior (e.g.,
  both agents add to a registry; the textual merge concatenates;
  but the registry's order matters). Detect by running full acceptance
  on the merged trunk every time, not just on each branch.
- **How partitioning quality scales with PLAN.md design.** Today
  partitioning is a human-curated annotation. At scale it has to be
  inferred or constrained by plan structure. Measure how often the
  human annotation disagrees with what trial merges show.

These are the experiments the work actually consists of. The "is N
feasible" question collapses into them.

## What gates parallelism (constraints, not ceilings)

These are real and need solving; none of them are intrinsic limits:

1. **Task independence.** Tasks that touch disjoint files can run
   in parallel safely. The plan format we're already building (typed
   planfile, declared dependencies, structured task semantics) gives
   us the foundation to express this as a property of the plan, not
   as a property of clever human annotation.
2. **Test independence.** If A imports something B adds, A can't be
   verified before B merges. Solvable by ordering merges by stated
   dependency, or by running acceptance only on merged trunk.
3. **State independence.** mcloop's CURRENT_PLAN.md, active-pid,
   ledger — all assume one running session. Requires either per-task
   state directories or a redesigned single-source-of-truth (see
   readiness doc 2.5 — possibly the cleaner fix).
4. **Branch hygiene.** Each parallel task on its own branch named
   after the task_id (`task/T-000190`). Trunk updated by merging
   completed branches in plan-order.
5. **Acceptance check coordination.** A's acceptance might require
   B to be in trunk first. Solvable by merging in plan-order and
   running acceptance after each merge, accepting that some agents
   finish-then-wait.
6. **Rate-limit-aware scheduling.** When rate-limit headroom drops,
   the coordinator throttles new spawns. Not a categorical limit; a
   feedback signal.

## Specific risks

1. **Hidden dependencies.** Two tasks marked independent that actually
   share a file deep in the dependency tree (a shared utility
   modified by both). Detection: pre-flight `git merge-tree` check
   before spawning, or trial-merge in a scratch worktree.

2. **Test pollution.** Both agents run pytest in their respective
   worktrees, both pass. Merged result fails because of an interaction.
   Mitigation: after merge, run full suite on trunk before declaring
   stage done.

3. **State explosion in mcloop.** Currently mcloop has one
   `.mcloop/active-pid`, one CURRENT_PLAN.md, one ledger. N parallel
   sessions need separate state per session, with a coordinator
   tracking which task is in which worktree. Substantial mcloop
   refactor.

4. **Cost overruns.** Two simultaneous opus sessions burn the rate
   limit faster. If both hit 30-min tasks, we may exhaust the
   5-hour window mid-run. Mitigation: rate-limit-aware scheduling.

5. **Merge order matters.** If A and B both modify the same file in
   non-conflicting ways, the merge result depends on order. Different
   orders may both apply cleanly but produce different semantic
   outcomes. Mitigation: prefer rebase-style sequential application
   so the merged result has a single linear history.

6. **Self-modification risk.** When parallel agents are editing
   mcloop/orchestra themselves (Phase D), an in-flight session may
   pick up a modified library mid-run. Mitigation: pin the
   coordinator's view of mcloop/orchestra to a specific commit at the
   start of each parallel batch; do not update mid-batch.

7. **Visibility scales worse.** The Tier 1 visibility issue from
   `pre-phase-d-readiness.md` gets N times harder with N agents. The
   user already can't tell what one agent is doing; what about three?
   Visibility (1.1) and self-monitoring (1.2) must land before
   parallelism is attempted.

## Approach: prove N=2, characterize, scale

Not a tiered roadmap with fixed milestones — an experimental protocol
where each step's outcome determines the next.

### Prerequisites (these are real; not optional)

- **Visibility** (readiness 1.1) — without it, supervising parallel
  agents is opaque to the user. Already needed at N=1; the cost goes
  up with N.
- **Self-monitoring** (readiness 1.2) — the system has to detect a
  stuck agent. Today's idle timeout is binary; richer signals (tool
  call rate, repeated-signature loops) are needed before scaling
  multiplies the failure surface.
- **State coherence** (readiness 1.3) — drift sources at N=1 become
  drift catastrophes at N=k. Fix before scaling.
- **Cross-repo workspace** (readiness 1.4) — the cleanest first
  partition boundary is one agent per repo. Without it, "parallel"
  has to be within-repo, which is harder.

### Probe: N=2 with explicit partitioning

The first experiment. Specifically:

- PLAN.md tasks carry a `[PARALLEL:G1]` annotation (or equivalent —
  may end up being declared via the planfile's typed dependency
  graph rather than as a flat tag). Tasks with the same group run
  concurrently.
- mcloop spawns one worktree per parallel task: `git worktree add
  /Users/mhcoen/proj/<repo>.worktrees/<task_id> <branch>`.
- Each agent runs in its worktree with its own active-pid, its own
  log directory, its own session state. (The state-coherence work
  has to land first.)
- The coordinator (mcloop's main process or a new sub-component)
  polls all running agents, surfaces aggregated status, waits for
  completion.
- On completion: pre-flight `git merge-tree` to detect conflict; if
  clean, merge in plan-order; if conflict, halt the batch and
  surface to the user with the conflict location.
- Acceptance: run on the merged trunk, not on each branch
  independently.

**What we measure at this scale:**

- Wall-clock vs the same workload run serially.
- Rate-limit headroom consumed (rate_limit_event from stream-json).
- Conflict rate across the trial corpus.
- Coordinator wall-clock as a fraction of total.
- Number of merged-trunk-acceptance failures that wouldn't have
  shown up at single-branch acceptance.
- Number of human interventions per task (the existing readiness
  metric).

**What we decide next:** based on the measurements, whether to:

- Add an agent (induct to N=3) with the partitioning method
  unchanged, watching how the measured quantities change.
- Improve a specific measured weakness before scaling (e.g.,
  rate-limit-aware scheduling if rate-limit headroom became a
  binding constraint).
- Step back to N=1 on certain task classes that turned out to
  resist clean partitioning (and characterize what class those
  are).

### Inductive step: N=k → N=k+1

Same pattern. Add one agent. Re-measure. The interesting outputs
are not "N=k+1 works" but the *change* in each measured quantity.
A clean step has rate-limit consumption growing roughly linearly,
conflict rate growing slowly (signal that partitioning quality is
holding), coordinator overhead growing sub-linearly. A bad step
has one of those breaking.

When a step breaks, that's a characterized problem with a name —
not a fence. Examples of what each would imply:

- Rate-limit hits become binding → multi-account routing, request
  batching, smarter scheduling, or model-tier mixing (some agents
  on sonnet, some on opus).
- Conflict rate spikes → improve partitioning (move from human
  annotation to inferred from the planfile dependency graph), or
  add semantic-merge support for the conflict class that's
  dominating.
- Coordinator overhead dominates → split coordinator state out of
  mcloop main, add caching, parallelize the coordinator's own
  housekeeping.
- Merged-trunk-acceptance failures appear → identify the
  interaction class (e.g., both agents adding to a shared
  registry); add a property to the planfile validator that catches
  the class statically; or add a "registry-add" merge primitive.

Each of these is a piece of work. None is a ceiling.

### What gets co-designed alongside

These don't have to be sequential:

- **Inferred partitioning.** Static analysis of task texts (which
  files they're likely to touch) plus a conflict pre-check (trial
  merge in scratch worktrees) for the cases where the human
  annotation is missing or wrong. Can be developed in parallel
  with the probe.
- **Semantic merge primitives for known structured edits.** When the
  edits are typed (planfile mutations) or follow a known pattern
  (add-import, add-function), structured merge is tractable. Build
  case by case as conflicts surface; not a single grand solution.
- **Rate-limit-aware scheduling.** When measurements show rate
  limits becoming binding, add the feedback loop.
- **Cost telemetry.** Per-task and per-stage cost surfaced in the
  status command. Useful at any N.

The point of listing these is not to schedule them. It's to say
none of them is gated by "first prove N=2"; some can run alongside.

## Concrete first probe

After the prerequisites land, the first probe:

1. **Pick a stage with 2 obviously independent tasks.** Phase D's
   FailureRecord schema work has natural candidates: schema dataclass
   and emitter-at-no-op-gate are different files, no shared imports
   between them. Other candidate pairs exist throughout the Phase D
   stages.
2. **Mark them parallel.** Initially via hand-annotation; the typed
   plan format being built makes this potentially expressible as a
   structural property (declared dependency between tasks) rather
   than a flat tag.
3. **Add minimal coordinator logic to mcloop.** When it sees a
   parallel pair, spawn two worktrees, run both sessions concurrently,
   wait for both to complete, merge sequentially (clean merges only),
   run acceptance on merged trunk.
4. **Measure.** Wall-clock vs serial baseline, rate-limit headroom
   consumed, conflict rate (in this case probably zero), interventions
   per task, total cost.
5. **Then run N = 3.** Same shape, one more agent. Same
   measurements. The deltas — what changed when we added the agent —
   are the actual finding.

Total scope for the probe: probably 1-2 weeks of work after the
prerequisites land, mostly in mcloop's run_loop and a new coordinator
module. The probe is small on purpose. The point is to get to the
measurements quickly, not to build a comprehensive parallel
orchestrator before knowing what it needs to handle.

## Open questions

1. **Branch naming convention.** `task/T-NNNNNN`? `parallel/G<N>/T-NNNNNN`?
   Needs to be stable across worktrees.

2. **Worktree location.** `/Users/mhcoen/proj/<repo>.parallel/<task>`?
   Inside the repo as a `.parallel/<task>` subdir (gitignored)? Outside
   the repo entirely? Probably outside to keep git status clean.

3. **CURRENT_PLAN.md per worktree.** If mcloop's split-file model
   continues (issue 2.5 in readiness doc), each worktree needs its
   own CURRENT_PLAN.md slice. That multiplies the drift surface.
   Argues for either (a) eliminating CURRENT_PLAN.md entirely first,
   or (b) carefully versioning it per task.

4. **Reviewer in parallel context.** If a reviewer is enabled (issue
   3.4), does it run on each branch independently or on the merged
   trunk? Independent is more parallel but doesn't catch interaction
   bugs.

5. **Merge order vs lineage.** When tasks A, B, C all complete in
   parallel and want to merge, what order? Trunk linear history
   argues for the order they finished; semantic stability might
   argue for canonical PLAN.md order. Probably the latter.

6. **Failure of one parallel task.** Does it abort the whole group,
   or only that one? Probably only that one; the others can land and
   the failed one becomes a serial retry.

7. **Visibility for the user when N agents run.** A single ticker per
   agent? Aggregated dashboard? Need to design before scaling N > 2.

8. **Cross-repo parallel.** Workspace (issue 1.4) makes per-repo
   parallelism natural. Two agents, one in bob-tools, one in duplo,
   on independent tasks. That's the cleanest first cut and may be
   the right first experiment instead of within-repo parallelism.

## Summary

- **Concrete first goal: prove N=2 working.** Two parallel agents
  in two worktrees, clean merge to trunk, acceptance on merged
  trunk. Even N=2 alone doubles throughput and removes the
  user-tracked coordination tax of the current N=1.x state
  (mcloop plus informal codex-on-the-side).
- **The argument for scaling is inductive, not predictive.** N=1
  → N=2 is the same structural step as N=k → N=k+1. Whatever
  works at N=2 is the machinery; each additional agent stresses
  the same seams and produces measurements that say what to
  improve next. We don't claim a ceiling without trying.
- **Prior art: `git worktree` is the mature primitive; nothing
  off-the-shelf orchestrates this end-to-end for AI agents.**
  Several pieces (`git rerere`, `git merge-tree`, stacked-PR
  tooling, multi-agent frameworks for the role/coordinator pattern)
  are reusable as building blocks. The end-to-end orchestrator
  for parallel coding agents through worktrees with structured
  merge and plan-driven partitioning is novel investigation into
  open problems.
- **Prerequisites: visibility, self-monitoring, cross-repo
  workspace, state coherence (Tier 1 of the readiness doc).**
  Without these, parallelism multiplies pain. With them, N=2 is
  immediately achievable.
- **Don't pre-build automated semantic merge.** Treat any conflict
  at N=2 as a halt + human review event; characterize the conflict
  classes as they appear; add structured merge primitives case by
  case when a class proves recurring. This is incremental, not
  deferring the problem — it's how the typed planfile work already
  positions us to solve semantic merge for known structured edit
  classes.
- **Constraints to measure and address, not ceilings to accept:**
  rate-limit headroom, coordinator overhead, conflict rate,
  partitioning quality, semantic-merge tractability per edit class.
  Each is something to characterize as N grows; each gives the
  next thing to improve.

This is a Phase D-or-later capability. It belongs in the Phase D
readiness conversation as a workstream that runs in parallel with
M1 (FailureRecord) and M2 (fix proposer) — and importantly,
making M1/M2 itself amenable to parallel-agent work is a natural
co-design with this effort, since FailureRecord emitters at
multiple sites are exactly the shape of work that should run in
parallel once the orchestration exists.

## Related documents

- `recursive-improvement.md` — the long-range goal this serves.
- `pre-phase-d-readiness.md` — the tier-1 items that must land
  before parallelism is attempted, and the Phase C.5 hardening
  that creates the conditions for the probe.
