# Plan Ledger Slice D: McLoop Pause-on-Threshold

## Purpose

Slices A, B, and C produce the ledger, threshold detection, and
Duplo re-authoring. Slice D closes the loop: McLoop emits events
to the ledger as it executes, evaluates thresholds between tasks,
and pauses to invoke Duplo re-authoring when crossings warrant it.

This is where Plan Ledger stops being an offline analysis tool
and becomes a runtime control mechanism.

## Position relative to existing work

Plan Ledger is now four shippable components:

- Slice A: events + envelope + projector + storage (commit 9e6e223)
- Slice B: threshold evaluator + record_crossings (commits d546d81 + 873eec2 + 9c84d8d)
- Slice C: Duplo re-author mode + lineage validation (commits 11d3f71 + 044cafb + d528011)
- Slice D: McLoop pause-on-threshold (this design)

Tonight's session built A through C. Slice D is the final piece
of the closed-loop design control system Bob is meant to be.

## Scope boundary

**In scope:**

- McLoop emits typed events to the project's ledger as it runs
  tasks: `phase_started`, `phase_completed`, `phase_abandoned`,
  `commit_landed`, `test_failed`, `finding_observed`.
- McLoop evaluates thresholds between tasks (specifically: after
  each `invoke_code_edit` returns, before starting the next task).
- McLoop pauses execution when a threshold crossing has
  `recommended_action ∈ {reauthor_phase, reauthor_plan}`.
- McLoop invokes `duplo.reauthor.reauthor_plan` programmatically,
  passing the project's PLAN.md and ledger dir.
- After re-authoring completes, McLoop reads the new PLAN.md and
  resumes with the updated phase sequence.
- Events emitted by McLoop's own runner have `writer_id="mcloop"`
  to distinguish them from events Duplo writes.

**Out of scope:**

- Concurrent McLoop instances writing to the same ledger. Slice D
  assumes one McLoop runner per project; multi-runner coordination
  is a future concern. See "Multi-runner future path" below for
  the design sketch even though no implementation lands here.
- Event emission from non-task McLoop subsystems (audit, sync,
  bug-verify). These can be added incrementally; Slice D ships the
  task path.
- Custom threshold rule sets per project. Slice D uses Slice B's
  default ThresholdParams. Per-project overrides via
  `.orchestra/config.json` extension are a follow-up.
- Auto-resume vs manual-resume semantics (see "What does pause
  mean" below). Slice D ships one mode; the other can be
  configurable later.

## What does "pause" mean

Three options for the pause semantics:

(i) **Hard stop.** McLoop pauses, prints the threshold crossing
    to stderr, exits with a distinct exit code (e.g., 5). User
    runs `duplo reauthor` manually and re-invokes McLoop. Simplest
    and most conservative; leaves the human in the loop.

(ii) **Auto-reauthor.** McLoop pauses, invokes `duplo reauthor`
     synchronously, waits for it to complete, reads the new
     PLAN.md, resumes with the updated phase sequence. Closes the
     loop fully; matches the design intent of "Plan Ledger
     replaces ad-hoc human-mediated consultation."

(iii) **Configurable per crossing.** Some rules trigger hard stop
      (assumption_falsified — needs human review), others
      auto-reauthor (exploratory_count_exceeded — mechanical
      response). Per-rule policy in `.orchestra/config.json`.

Recommend **(ii) auto-reauthor** as the default for Slice D, with
(i) as a configurable opt-out via env var or CLI flag.

Reasoning:

- The whole point of Plan Ledger is to automate what we did
  tonight by hand. Stopping for human input on every crossing
  reproduces tonight's pattern; auto-reauthor is the actual
  improvement.
- Re-authoring is auditable. The plan_reauthored event records
  the trigger, the lineage changes, and the council run_id.
  Nothing happens silently.
- The lineage validator's fail-closed semantics already prevent
  bad re-authors from landing. A failed re-author leaves PLAN.md
  unchanged and surfaces the error; user can intervene then.
- Hard-stop is still useful as an opt-out: `--no-auto-reauthor`
  flag for projects that want human-in-the-loop discipline.

(iii) is more flexible but adds policy surface before there's
evidence the per-rule distinction matters. Defer.

### Failure-mode contract (Codex review delta)

Auto-reauthor is the happy path. Failures must hard-stop, not
retry silently or paper over errors. Codex's framing: "auto by
default, hard-stop on reauthor/validation failure, manual opt-
out available."

Concretely:

- **reauthor invocation fails** (council error, network, etc.):
  McLoop hard-stops with a distinct exit code. The ledger
  contains the `threshold_crossed` event but no
  `plan_reauthored`. PLAN.md is unchanged.
- **lineage validation rejects the synthesized plan**:
  McLoop hard-stops. PLAN.md is unchanged. Slice C's atomicity
  guarantees that no lifecycle events from this attempt land
  on the ledger either.
- **any other reauthor exception**: McLoop hard-stops.
- **successful reauthor** (a `plan_reauthored` event landed and
  the new PLAN.md passed lineage validation): McLoop refreshes
  the plan/task mapping and continues.

The point of auto-reauthor is to close the loop on successful
re-planning. It is not to mask failures. A reauthor failure
under auto mode produces the same observable outcome as a
hard-stop under manual mode plus a clear signal in the ledger
of what was attempted.

## Where does threshold evaluation live in McLoop's loop

Three points where McLoop could evaluate:

(a) **Pre-task.** Before each `invoke_code_edit`, check whether
    prior events have crossed a threshold that the upcoming task
    should respect. Catches the case where a re-author is needed
    before continuing.

(b) **Post-task.** After each `invoke_code_edit` returns, emit
    events for what just happened (commit_landed, test_failed,
    etc.), then evaluate thresholds. Catches crossings produced
    by the task itself.

(c) **Both.** Pre-task for inherited state, post-task for fresh
    state.

Recommend **(b) post-task only** for Slice D.

Reasoning:

- Pre-task evaluation duplicates work: any crossing that existed
  before this run was already handled (or not) by the prior run's
  post-task evaluation. The only legitimate pre-task case is "user
  added events manually between McLoop runs," which is rare and
  can be handled by a one-shot evaluation at McLoop startup if
  needed.
- Post-task evaluation is the natural rhythm: task runs, events
  emit, threshold evaluates. Each task's effect on the plan is
  visible immediately.
- (c) doubles the threshold evaluation calls without clear
  benefit. evaluate_thresholds is cheap (pure function, no LLM),
  but the audit trail gets noisier with redundant
  threshold_crossed events.

Slice D adds one McLoop-startup threshold check (one-shot,
catches user-injected events) plus per-task post-evaluation. Not
"pre-task" in the loop sense.

## How does McLoop emit events

McLoop's task lifecycle maps onto ledger events as follows:

- **Task start** (entering `run_task`): emit `phase_started` if
  this task corresponds to a plan phase. Phase ID derived from
  task label or PLAN.md parsing.
- **Task success** (`invoke_code_edit` returns success=True):
  emit `commit_landed` with the changed files, `change_class`
  inferred from the file paths (e.g., paths in tests/ →
  change_class=test, paths in *.md → change_class=docs).
- **Task failure** (success=False): emit `test_failed` with
  failure summary, OR `finding_observed` if the failure is
  ambiguous (e.g., timeout, sandbox denial).
- **Task abandoned** (max retries exceeded, user gave up): emit
  `phase_abandoned` with reason.
- **PLAN.md updated by McLoop's own work** (e.g., a task that
  modified PLAN.md per a re-author): no event from McLoop;
  Duplo's reauthor emitted plan_reauthored at the time of the
  change.

Phase ID resolution is the trickiest part. McLoop's task labels
look like `task-001`, `bug-foo`, etc. Plan phases use
`phase_NNN` format (Slice C convention). Slice D needs a
mapping.

### Resolution contract (Codex review delta)

Codex's framing: "I would not rely on ordinal mapping as
normal behavior. It is acceptable as compatibility fallback,
not as the main resolution mechanism."

The contract Slice D ships:

- **Explicit `<!-- phase_id: phase_NNN -->` metadata in
  PLAN.md is REQUIRED for Duplo/McLoop-authored planned
  tasks.** This is the production path. Slice C's `reauthor_plan`
  output already carries explicit phase_id metadata for every
  phase, so plans authored or re-authored through Duplo
  satisfy this requirement by construction.
- **Ordinal fallback is LEGACY/DEGRADED mode only.** When
  McLoop hits a task without explicit phase_id metadata, it:
  - emits a warning to stderr naming the task and the
    fallback decision
  - emits a `finding_observed` event with the message
    "phase_id resolution fell back to ordinal mapping for
    task X" so the audit trail captures the degradation
  - proceeds with the ordinal-derived phase_id rather than
    failing
  This path exists for pre-Slice C plans that have not been
  migrated to explicit metadata. It is not the production
  path; the warnings are the migration prompt.
- **After ANY reauthor, McLoop MUST refresh the plan/task
  mapping before continuing.** Stale mappings against a
  superseded PLAN.md are incorrect; refresh is mandatory, not
  best-effort.

The metadata convention: `<!-- phase_id: phase_NNN -->` HTML
comment placed immediately after the phase header in PLAN.md.
Slice C's synthesizer template places phase_id in the
`## Phase <phase_id>: <title>` header itself, and the lineage
sidecar names it explicitly; McLoop's resolver reads from the
header (the visible source of truth) and treats absence of a
header-prefixed phase_id as the trigger for the degraded
ordinal path.

## API shape

### Python

McLoop gains a small ledger module:

    mcloop/ledger_emit.py

With one main function:

    def emit_task_lifecycle_events(
        task_label: str,
        phase_id: str | None,
        result: CodeEditResult,
        project_dir: Path,
    ) -> list[str]:
        """Emit ledger events for one task's lifecycle.
        Returns the event_ids of events written."""

Plus a threshold-check helper:

    def evaluate_and_maybe_pause(
        project_dir: Path,
        ledger_dir: Path,
        last_event_id: str | None,
    ) -> PauseDecision | None:
        """Evaluate thresholds since last_event_id. If any crossing
        recommends reauthor_phase or reauthor_plan, return a
        PauseDecision; else None.

        Side effect: records all crossings to the ledger
        regardless of pause decision."""

The auto-reauthor invocation:

    def auto_reauthor(
        decision: PauseDecision,
        plan_path: Path,
        ledger_dir: Path,
        project_dir: Path,
    ) -> ReauthorResult:
        """Invoke duplo.reauthor.reauthor_plan with the
        triggering crossing event_id. Blocks until complete.
        Returns ReauthorResult."""

### Configuration

`.orchestra/config.json` gains a `plan_ledger` section:

    {
      "plan_ledger": {
        "enabled": true,
        "ledger_dir": ".duplo/ledger",
        "auto_reauthor": true,
        "threshold_params": {
          "exploratory_commit_limit": 5
        }
      }
    }

Default: enabled=true if `.duplo/ledger/` exists at McLoop
startup; otherwise disabled silently. auto_reauthor=true.

### CLI

Two new flags on `mcloop`:

    --no-plan-ledger          disable Plan Ledger emission for this run
    --no-auto-reauthor        emit events but don't auto-reauthor on threshold

Env vars: `MCLOOP_NO_PLAN_LEDGER=1` and
`MCLOOP_NO_AUTO_REAUTHOR=1` mirror the flags.

## Internal flow

McLoop's task loop gains four touch points:

1. **Startup.** If Plan Ledger is enabled, evaluate thresholds
   on the existing ledger. Catches crossings injected since the
   last McLoop run. If a crossing demands reauthor, do it before
   starting the first task.

2. **Per-task post-success.** After `invoke_code_edit` returns
   successfully, emit lifecycle events. Evaluate thresholds since
   the prior post-task evaluation. If a crossing demands
   reauthor, pause and invoke `duplo reauthor`. After reauthor
   completes, re-read PLAN.md, refresh the task driver's view
   of the phase sequence.

3. **Per-task post-failure.** Same as post-success but with
   different events (test_failed instead of commit_landed). The
   threshold evaluation is the same; some rules (e.g.,
   assumption_falsified) might fire only on failures.

4. **Shutdown.** Emit a `work_observed` event summarizing the
   run for audit.

## Tests

### Unit tests

- emit_task_lifecycle_events for each task outcome (success,
  failure, abandonment).
- Phase ID resolution: explicit metadata, fallback to ordinal,
  mismatch handling.
- evaluate_and_maybe_pause with each threshold rule firing
  individually.
- auto_reauthor wiring: mock duplo.reauthor.reauthor_plan,
  verify the right inputs are passed.
- Config loading: enabled/disabled, auto_reauthor on/off,
  custom ledger_dir.

### Integration tests

- End-to-end: McLoop runs a task that produces an
  unattributable_commit, threshold fires, auto-reauthor mocked
  to return a known plan, McLoop continues with the new plan.
- Hard-stop mode: same as above but with auto_reauthor=false,
  McLoop exits with code 5, ledger contains the threshold
  crossing event, PLAN.md unchanged.
- Real-API smoke: equivalent of the Slice C smoke but driven
  by McLoop instead of a standalone harness. Manual gate, not
  CI.

### Phase ID resolution tests

- Task with explicit phase_id metadata → events use that ID.
- Task without metadata, ordinal mapping → events use the
  PLAN.md-ordered phase ID.
- PLAN.md changed mid-run → ordinal mapping refreshed; explicit
  metadata still wins.

Estimated test count: 30-40.

## Files to create

    mcloop/ledger_emit.py            new
    mcloop/ledger_pause.py           new (or merged into ledger_emit.py)
    tests/test_ledger_emit.py        new
    tests/test_ledger_pause.py       new
    tests/test_integration_slice_d.py new

Plus updates to:

    mcloop/runner.py                 hook into the task lifecycle
    mcloop/code_edit.py              return changed_files reliably
                                     (already does for direct backend;
                                     verify for orchestra)
    mcloop/main.py                   add --no-plan-ledger,
                                     --no-auto-reauthor flags
    mcloop/config.py                 read plan_ledger section from
                                     .orchestra/config.json
    duplo/reauthor.py                ensure programmatic invocation is
                                     stable as a Python API (already
                                     is per Slice C, but verify the
                                     return shape works for McLoop)

## Multi-runner future path (Codex review delta)

Multi-runner coordination stays out of scope for Slice D, but
the design path is documented here so reviewers do not read
"out of scope" as "not considered."

Slice D ships under a single-writer assumption: one McLoop
runner per project at a time. Within that assumption, the
existing writer_id / sequence machinery is enough.

When multi-runner support eventually lands, the additions
will be:

- **runner_id field on every emitted event.** The current
  writer_id distinguishes writers ("mcloop", "duplo-reauthor",
  ...); a per-instance runner_id under a writer scopes events
  to a specific runner instance. Slice D's events are still
  valid because writer_id already pins their origin to McLoop;
  multi-runner adds finer-grained attribution within that.
- **Ledger write lock or write lease per runner.** A runner
  acquires a lease before appending; lease holders publish
  the latest_event_id they observed. Lease loss surfaces as
  an explicit error rather than a silent stale-state continue.
- **Optimistic crossing recording keyed by latest_event_id.**
  When evaluate_thresholds runs, the resulting crossings are
  tagged with the latest_event_id observed at evaluation time.
  Recording a crossing whose evaluation snapshot is older than
  the current latest is rejected; the runner re-evaluates
  against the new latest and emits crossings keyed to that.
  Stale crossings never land.

Migration path: when multi-runner support lands, Slice D's
existing events remain valid because writer_id already
distinguishes them. The runner_id addition is additive at the
event-payload level; existing consumers ignore the field.

## Quality gates

ruff, mypy --strict, pytest. Same discipline as prior slices.
Real-API smoke at the end (manual, not CI), gated like Slice C.

## Codex review: resolved positions

The original draft surfaced five open questions for Codex
review. Codex's review came back with deltas on Q1, Q3, and Q4
plus confirmations on Q2 and Q5. The body of this document has
been updated; the resolutions are summarized here for audit.

**Q1 — Pause semantics.** Resolved: (ii) auto-reauthor by
default with (i) hard-stop as opt-out, plus an explicit
failure-mode contract: any reauthor invocation failure,
lineage-validation rejection, or other reauthor exception
hard-stops McLoop. The point of auto-reauthor is closing the
loop on success; failures get the same observable outcome as
manual hard-stop plus a clear ledger signal of what was
attempted. See "Failure-mode contract" above.

**Q2 — Threshold evaluation point.** Confirmed: (b) post-task
only, with a one-shot startup check for externally injected
events. Codex's framing: "task emits events, thresholds
evaluate, McLoop either continues or reauthors. A pre-task
loop check is redundant except for externally injected ledger
events, and startup covers that case."

**Q3 — Phase ID resolution.** Resolved with a stricter
contract than the original draft proposed: explicit
`<!-- phase_id: phase_NNN -->` metadata is REQUIRED for
Duplo- or McLoop-authored planned tasks; ordinal fallback is
LEGACY/DEGRADED mode only and emits a stderr warning plus a
`finding_observed` event when it fires. After any reauthor,
the plan/task mapping MUST be refreshed before McLoop
continues. See "Resolution contract" above. Codex's framing:
"I would not rely on ordinal mapping as normal behavior. It
is acceptable as compatibility fallback, not as the main
resolution mechanism."

**Q4 — Multi-runner coordination.** Resolved: out of scope
for Slice D, with a documented future design path covering
runner_id, write leases, and optimistic crossing recording.
See "Multi-runner future path" above. Codex wanted the design
path documented even though no implementation lands; the
section above satisfies that.

**Q5 — Auto-reauthor on test_failed.** Confirmed and tightened.
Slice B's rules fire on `assumption_falsified`, not directly
on `test_failed`. Codex's framing: "A failing test is
evidence; a falsified assumption is a semantic claim." McLoop
must NOT auto-derive `assumption_falsified` from a `test_failed`
event alone; that derivation requires explicit assumption
metadata (an `assumption_id` declared in PLAN.md or a sidecar
plus a reliable test-to-assumption mapping). For Slice D's
INITIAL implementation: McLoop emits `test_failed` always for
evidence; `assumption_falsified` events come from Duplo or
human curation. Slice D's threshold-evaluator picks up
`assumption_falsified` events written by other actors. A
follow-up slice can wire the test-to-assumption mapping once
the metadata convention exists and is exercised.
