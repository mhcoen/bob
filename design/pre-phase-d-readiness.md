# Pre-Phase-D Readiness

## Purpose

Phase C exposed Phase D's blockers. mcloop drove 23 stages to completion
with ~1.5 human interventions per task. Phase D — building the
self-improvement infrastructure (FailureRecord, fix-proposer, trust
ladder) per `recursive-improvement.md` — requires that intervention rate
to be substantially lower at the start, not the end. Otherwise Phase D
is the same pain Phase C was, only this time mcloop is editing mcloop
itself, which makes the failure modes more dangerous.

This document enumerates the issues observed in Phase C plus the gaps
those issues exposed, organized by tier. Not all need to land before
Phase D — Tier 1 is the blocker set; Tier 2 is high-leverage but not
required; Tier 3 is M1-or-later territory captured here so it does not
get lost.

This is a discussion document. Each item is sketched enough to argue
about. None are yet PLAN.md tasks. After review, the agreed subset
becomes a Phase C.5 ("Hardening") slice in `bob-tools/PLAN.md`.

## How to read this

Each issue has four lines:

- **What we hit**: the concrete observed pain point.
- **Why it matters for Phase D**: why it gets worse, not better, when
  mcloop edits its own infrastructure.
- **Sketch of fix**: enough to know it's tractable. Not the
  implementation.
- **Effort estimate**: rough — small (≤1 day), medium (2-5 days), large
  (1-2 weeks).

---

## Tier 1 — Blockers

The loop is not safely runnable for Phase D until these land.

### 1.1 Visibility into what the editor agent is doing right now

**What we hit.** mcloop's progress output during an editor session is a
ticker:

```
[1/1] editor (claude_code_agent:opus) ... still running, 30.0s elapsed
[1/1] editor (claude_code_agent:opus) ... still running, 60.0s elapsed
...
[1/1] editor (claude_code_agent:opus) ... still running, 1800.2s elapsed
```

The user has no idea what the agent is doing. Reading files? Editing?
Running tests? Looping? Stuck on a permission prompt? Today this is
debugged by `cat`-ing orchestra-runs log.jsonl, `tail`-ing editor logs,
and grep-ing for events. That is a regression to manual log forensics
on every long-running task.

**Why it matters for Phase D.** Phase D tasks (FailureRecord schema,
fix-proposer) are mcloop-internal. The cost of being wrong about
"working vs stuck" goes up, because a wedged self-modification can
leave the loop in a state that the loop itself can't recover from. The
user needs real-time signal to intervene before the loop wedges
itself.

**Sketch of fix.** The data is already there. orchestra writes
`logs/orchestra-runs/<run-id>/log.jsonl` in real time. mcloop's progress
printer should tail that file (or subscribe to a sidechannel from
orchestra) and surface human-readable activity:

```
[1/1] editor (claude_code_agent:opus) ... 1m 47s
       reading bob_tools/planfile/operations.py
[1/1] editor (claude_code_agent:opus) ... 2m 31s
       editing duplo/council.py (3 edits)
[1/1] editor (claude_code_agent:opus) ... 3m 04s
       running pytest tests/test_council.py
[1/1] editor (claude_code_agent:opus) ... 3m 22s
       4 passed
```

Update on event arrival, not on a 30s timer. Tool-use events become
one-liners. File edits become one-liners with counts. Test output
becomes pass/fail summary. Existing detailed log remains untouched —
this is a surface, not a replacement.

**Effort.** Medium. Stream-json event parser already exists in
`orchestra/_subprocess.py:extract_final_text`; generalize and emit
to mcloop's progress channel.

---

### 1.2 Self-monitoring of progress (stuck vs working detection)

**What we hit.** orchestra's idle-timeout (just landed) fires when no
stream event arrives for 10 minutes. That's binary. The richer signal
the loop should compute:

- Time since last tool call event
- Time since last file edit
- Time since last test run
- Number of tool calls in the last N minutes (loop detector)
- Repetition of the same tool call with the same args (also loop
  detector)

Today none of this is computed. The first time mcloop knows it's stuck
is when wall-clock or idle fires.

**Why it matters for Phase D.** A loop running on its own code can
enter a self-modification cycle that produces output (so it looks
"alive" to the idle detector) but is making zero useful progress —
e.g., the agent keeps proposing the same edit, the gate keeps
rejecting it, the agent re-proposes, etc. The idle timeout would not
fire. A repetition detector would.

**Sketch of fix.** A `SessionHealth` object updated on each stream
event, exposing:

- `last_tool_use_ts`
- `tool_use_count_5min`
- `edits_in_session`
- `repeated_tool_signatures: dict[str, int]` (signature = tool name +
  hash of args)
- `current_activity_class`: enum (idle / reading / editing / running /
  thinking / blocked_on_permission)

When `current_activity_class == "thinking"` for >5 min, alert. When any
`repeated_tool_signatures` count crosses some threshold (5? 10?), alert.
Both stop short of killing; they surface a warning to the user and a
FailureRecord candidate event.

**Effort.** Medium. The data flows through one well-defined channel
(orchestra's log.jsonl). Implementation is a stateful reducer.

---

### 1.3 State coherence

**What we hit.** Several distinct drift sources:

- `CURRENT_PLAN.md` going stale relative to master `PLAN.md` after
  out-of-band changes. We rm'd it manually 5+ times this session to
  force regeneration.
- `.mcloop/active-pid` not cleaned up on abnormal exit. We rm'd it
  manually 4+ times.
- Master `PLAN.md` not getting `[x]` written even when mcloop reported
  "Completed Task N" — because the canonical-validation bug blocked the
  write silently. Caught only because the next task tried to start from
  the same `[ ]`.
- mcloop's run_summary.json says `success: false` while the actual work
  is committed and tests pass.

**Why it matters for Phase D.** mcloop editing mcloop must not produce
diverging state. Every drift source is a place where the loop's view
of "what's done" differs from on-disk truth.

**Sketch of fix.**

- `mcloop --reconcile` command: regenerates CURRENT_PLAN.md from master,
  removes stale active-pid (after PID-alive check), verifies that
  recent `Complete:` commits match `[x]` state in master, flags any
  divergence.
- mcloop pre-flight should run reconcile by default; `--no-reconcile`
  to skip.
- Every state-write site (mark `[x]`, write run_summary, update
  CURRENT_PLAN) should be a single transaction (write then read-back
  verify); failed verify means the write didn't land and the operation
  reports failure rather than silently succeeding.

**Effort.** Medium. Mostly small, scattered changes plus the reconcile
command.

---

### 1.4 Cross-repo workspace

**What we hit.** Phase C Stages 18-22 are duplo tasks driven from
bob-tools' PLAN.md. mcloop's `changed_files` detector looks only inside
`project_dir` (bob-tools). Every duplo edit was invisible to the
no-op gate; we worked around it with the log-aware evidence fix
(`4ec259c`) and manual commits. That's a workaround, not a solution.

**Why it matters for Phase D.** Phase D tasks span mcloop, orchestra,
and bob-tools. The default project_dir will be bob-tools or one of the
others; the work will land in two or three repos per task. Same
blindness.

**Sketch of fix.** Explicit workspace configuration in
`~/.mcloop/config.json` or per-project `.mcloop/workspace.json`:

```json
{
  "workspace": [
    {"path": "/Users/mhcoen/proj/bob-tools", "primary": true},
    {"path": "/Users/mhcoen/proj/duplo"},
    {"path": "/Users/mhcoen/proj/mcloop"},
    {"path": "/Users/mhcoen/proj/orchestra"}
  ]
}
```

`changed_files` becomes the union of git-diff across all workspace
repos. Acceptance commands can be configured per-repo. The editor
agent gets the workspace list as part of its prompt so it knows the
edit scope.

**Effort.** Medium. The hard part is making cross-repo commits not
race against each other; the rest is bookkeeping.

---

### 1.5 Tooling gaps in bob-plan and mcloop

**What we hit.** During Phase C we needed but did not have:

- `bob-plan edit <T-id> <new-text>` — to fix the T-000183 broken-glob
  typo without hand-editing PLAN.md.
- `bob-plan unstick` — combined: regenerate CURRENT_PLAN.md from
  master, clean stale pid, fix common drifts. One command instead of
  three.
- `mcloop --status` — single-command answer to "what's running, is it
  alive, what task is in progress, what was the last commit, what was
  the last failure". Today this is 5-10 separate `cat`/`grep`/`stat`/
  `kill -0` invocations across 4 files.
- `mcloop --kill` — clean termination (signal mcloop, wait for editor
  subprocess, remove pid, leave consistent state). Today this is
  Ctrl-C + rm active-pid + sometimes manual git operations.

**Why it matters for Phase D.** Every one of these was hand-rolled
multiple times in Phase C. The pattern is the user inferring the
intervention from log artifacts and executing a sequence of
non-trivial commands. Each one is a candidate for the M2 trust ladder
(auto-execute on clear FailureRecord). But the underlying tooling has
to exist first.

**Sketch of fix.** Add each as a command. Most are 30-100 LOC each.

**Effort.** Medium (collectively). Individually small.

---

### 1.6 Test/gate infrastructure

**What we hit.**

- `tests/test_planfile_compat.py::test_purge_completed_bugs_removes_done_bug_entries_atomically`
  fails on clean HEAD with a fixture-data issue (T-NNNNNN missing on
  line 3). Pre-existing. Sitting unfixed.
- mcloop's pre-flight check_commands run only in `project_dir`. For
  cross-repo tasks (Stages 18-22), bob-tools' suite was green but
  duplo's wasn't checked by mcloop — the editor agent ran duplo's
  pytest manually inside its session.
- "Full test suite (phase boundary)" runs once per phase boundary,
  reported as `passed [1s]`. Suspiciously fast. Worth checking what's
  actually being run.

**Why it matters for Phase D.** Phase D edits mcloop's own
test/validation infrastructure. If the gates are inconsistent with
what's actually being run, self-improvements could degrade the gates
silently.

**Sketch of fix.**

- Run all PLAN.md and BUGS.md files through `bob-plan fmt` once;
  commit. Fixture data also needs `fmt`. Confirm
  `pytest -q tests/test_planfile_compat.py` clean.
- Phase-boundary check should be configurable in PLAN.md (per-phase
  check_commands list).
- Each task's check_commands should be required to actually exit 0.
  Today the AUTO observation pattern (T-000183) ran a check that
  failed and was treated as "skip" via `bob-plan done`. The right
  pattern is the AUTO check declares an explicit acceptance condition
  not just an exit-code check.

**Effort.** Small (fmt + commit) + Medium (per-phase check config).

---

## Tier 2 — High leverage, not blocking

Things that will hurt during Phase D but won't stop it.

### 2.1 Recovery & cleanup

**What we hit.** When mcloop hangs and the user kills it, the cleanup
is manual: `rm active-pid`, sometimes commit half-done work, sometimes
revert. No "mcloop unwedge" that does the recovery from a known stuck
state.

**Sketch of fix.** `mcloop --recover` enumerates stuck-state
signatures (dead PID + active-pid present; CURRENT_PLAN.md stale vs
master; uncommitted work attributed to mcloop-checkpoint pattern) and
offers/executes recovery actions.

**Effort.** Small to medium.

---

### 2.2 User control during a running task

**What we hit.** Ctrl-C is the only intervention available during a
running editor session. There's no "pause and inject a note", "skip
this task and continue", "switch to a different task", "describe what
the agent should be doing differently". The `d` describe prompt is
only available AFTER mcloop has bailed.

**Sketch of fix.** A control channel (file under `.mcloop/control/`
that mcloop polls):

- `pause` — finish current tool use, then pause
- `inject <text>` — append text to the agent's next-message context
- `skip` — mark current task failed and advance
- `kill` — same as Ctrl-C but cleaner

These could be triggered by `mcloop --pause`, etc. The polling adds
overhead but it's cheap.

**Effort.** Medium.

---

### 2.3 Trust model granularity

**What we hit.** The Telegram permission hook is binary: every `Bash`
tool call goes through it. We missed prompts repeatedly because of
this. The auto-mode classifier (Claude Code's own gate) also fires
broadly, sometimes blocking work that a directive in scope should
allow.

**Sketch of fix.** A per-tool-class trust ladder:

- `Read`, `Grep`, `Glob`: auto-approve always (read-only)
- `Bash(ruff ...)`, `Bash(pytest ...)`, `Bash(mypy ...)`, `Bash(npm
  test)`: auto-approve in declared workspace
- `Bash(git status)`, `Bash(git log)`, `Bash(git diff)`: auto-approve
- `Edit`, `Write` in declared workspace: auto-approve
- `Bash(git push|commit|tag)`, `Bash(rm ...)` outside workspace,
  network commands: route to human
- Everything else: route to human

A YAML/JSON describing the ladder, consumed by the permission hook.

**Effort.** Medium.

---

### 2.4 Commit hygiene

**What we hit.** The attribution-blocking hook
(`~/.claude/hooks/block-commit-attribution.sh`) blocks any `git
commit` command whose text contains "claude"/"anthropic"/"happy"/
"co-authored-by" case-insensitive. That fires on the literal filename
`claude_code_agent.py` when staged in a chained `git add ... && git
commit ...`. We worked around this by splitting `git add` from `git
commit` into separate Bash invocations.

**Sketch of fix.** The hook should match on attribution patterns
(`Co-Authored-By:`, `Authored by Claude`, `Generated with Anthropic`)
rather than on the bare word. The current rule is over-coarse and
encourages workarounds (split into separate Bash calls) that themselves
are signal of a workaround.

**Effort.** Small.

---

### 2.5 Plan format reconsideration

**What we hit.** `CURRENT_PLAN.md` as a separate file from `PLAN.md`
is a persistent drift source. We have rm'd-and-regenerated it 5+ times.
The user asked "why does this file exist" mid-Phase-C and the answer
("it's mcloop's working slice; regenerated each run") satisfies the
mechanical question but not the design question — could mcloop just
operate on master with a slice cursor and avoid the divergent file
entirely?

**Sketch of fix.** Two options:

- **Option A: in-memory slice.** mcloop reads master PLAN.md, computes
  the current stage's slice in memory, never writes CURRENT_PLAN.md.
  Mutations go directly to master via `bob_tools.planfile.update`.
- **Option B: explicit cursor file.** Replace CURRENT_PLAN.md with a
  small `.mcloop/cursor.json` that just names `{phase_id, task_id}`.
  All reads/writes flow through master.

Option A is simpler architecturally; Option B is closer to the current
shape. Either is a substantial change to mcloop.

**Effort.** Medium to large.

---

## Tier 3 — Worth doing, M1+ territory

Capture so they're not forgotten; defer until after Tier 1 lands.

### 3.1 Memory & context carry-over

**What we hit.** Each editor session starts fresh. The agent re-reads
files, re-discovers project context. mcloop's `session_context.py`
exists but is rolling and limited. For Phase D where related tasks
build on each other (FailureRecord schema → emitters → detector), the
agent should carry richer context across tasks.

**Sketch.** A `session_memory.md` file under `.mcloop/`, written by the
agent at the end of each task, read by the next task as additional
prompt context. Curated, not raw — the agent decides what's worth
keeping.

**Effort.** Medium.

---

### 3.2 Cost & resource visibility

**What we hit.** Subscription is "unlimited"-ish but rate-limited. Each
~30-min task is significant token spend ($0.30-3.00 per task at
opus rates). No surfaced visibility on rate-limit headroom or session
cost.

**Sketch.** Parse `rate_limit_event` from stream-json; track and
display per-task cumulative usage; warn when rate limit headroom is
low.

**Effort.** Small.

---

### 3.3 Editor configuration / model routing per task class

**What we hit.** All tasks run claude-opus. Verify-style tasks
(T-000172, T-000174, T-000176, ...) are lighter than implementation
tasks and probably don't need opus. Today the model choice is global.

**Sketch.** Per-task model hint in PLAN.md (already supported via
[AUTO:...] syntax? Worth checking). Default opus; allow `[MODEL:sonnet]`
or similar to override per task.

**Effort.** Small.

---

### 3.4 Reviewer integration

**What we hit.** `.mcloop/config.json` configures a reviewer
(deepseek-v4-pro via openrouter) but its actual role and when it fires
during a Phase C task is opaque from outside. May not be firing at all
for some task types.

**Sketch.** Document what the reviewer does and when. If currently
unused, decide whether to remove or activate.

**Effort.** Small (audit) + variable.

---

### 3.5 Logging coherence

**What we hit.** Three log locations:

- `.mcloop/runs/*.json` — mcloop's run summaries
- `logs/orchestra-runs/<id>/log.jsonl` — orchestra's run events
- `logs/<timestamp>_<task>_edit_<...>.log` — editor session transcript

No common run_id linking them. Today this is fixed via timestamps and
guessing. Phase D's FailureRecord schema needs a stable identifier
linking all three.

**Sketch.** Adopt the orchestra run_id (the hex dir name) as the
canonical run identifier. Write it into mcloop's run_summary and into
the editor log filename.

**Effort.** Small.

---

### 3.6 claude CLI dependency management

**What we hit.**

- The auto-installer dropped a 208MB binary into bob-tools' working
  tree because of a broken self-referencing symlink.
- The `--input-format text` flag broke implicit stdin-as-prompt
  between claude versions.
- Subscription login state is opaque; only the preflight detects it.

**Sketch.** Pin the claude CLI version mcloop expects. Validate at
mcloop startup that the installed version matches. Surface
auto-installer activity (we saw the 208MB binary land silently).

**Effort.** Medium.

---

## Approach

The user has signaled this list is the tip. There are more. This
document is an opening sketch; expect a discussion pass to add at
least as many issues as it currently names.

After agreement, the Tier 1 items become PLAN.md tasks (Phase C.5
slice in bob-tools/PLAN.md or a new home — same open question as the
Phase D plan home). Tier 2 items become Phase C.6 candidates or
deferred to Phase D-readiness sub-phases. Tier 3 items become M1+
ledger entries.

The single biggest leverage item is **1.1 Visibility** — without it,
every other issue is harder to diagnose, because the user has no
real-time view of what the loop is doing. It should land first.

The second-biggest is **1.4 Cross-repo workspace** — it removes a
category of false-negatives that bit Stage 18-22 every single time.

## Open questions

- **Home for Phase C.5.** New `bob/PLAN.md` (separate from
  `bob-tools/PLAN.md`) for self-improvement work? Or extend
  bob-tools/PLAN.md with a Phase C.5 + Phase D? Cleaner separation
  argues for the former; reuse of existing infra argues for the
  latter. Decide once.
- **Is the workspace config per-user or per-PLAN?** If per-PLAN,
  needs to live in the repo that owns the PLAN. If per-user, needs
  `~/.mcloop/workspaces/<name>.json`. Probably per-PLAN for cross-repo
  PLANs; per-user for default workspace.
- **What is a FailureRecord for "user gave up"?** Phase C had cases
  where the user pre-emptively killed mcloop after staring at "still
  running" for too long, even though the run might have eventually
  succeeded. The visibility fix (1.1) should reduce this, but should
  the system distinguish user-killed from time-out-killed?
- **Self-reference safety.** When mcloop edits mcloop, can it modify
  the very gates that decide whether the edit was good? Probably yes,
  but only with frozen-corpus cross-validation. That mechanism doesn't
  exist yet.
- **Reviewer scope.** Should the reviewer (3.4) also vote on
  acceptance? Today it's unclear it runs at all. Adding it as a
  second-opinion gate could be either useful or noisy.

## Things this document does not cover (yet)

The user said "tip of the iceberg." Areas this draft has not yet
explored but probably should after discussion:

- Editor agent prompt design (what context is the agent given for a
  task; how is it curated; how does prompt drift get caught)
- Plan task granularity (sub-task structure, parent/child semantics,
  BATCH/[BATCH] handling)
- Bugs ledger lifecycle (when does BUGS.md grow vs shrink; how do
  bugs get re-opened vs deduped)
- Lineage tracking across reauthor (Stage 21 work)
- Acceptance evidence formal grammar (today: keyword soup; could be:
  declarative `accept_when: <expr>`)
- Multi-operator support (today: single user; future: team of
  reviewers/operators)
- Audit trail completeness (every change has provenance; today some
  paths drop provenance)
- Plan-level dependencies (task A blocks task B; expressed where?)
- Failure budget / mean time between intervention measurement
- Cost regression detection (when does a task class start costing 3x
  what it used to)

These belong in a follow-up pass after Tier 1+2 from this draft are
agreed.
