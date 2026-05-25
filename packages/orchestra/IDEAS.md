# Ideas

## Resume vs retry: separate the two operations

The current `orchestra resume` command conflates two distinct user intents:

1. **True resume**: continue an interrupted run with the same config. Safe
   when nothing has changed between crash and resume.

2. **Retry with changes**: re-execute (possibly from a chosen point) after
   the user has edited the config — most commonly to swap the model because
   the original one couldn't solve the problem.

Resume correlates with the user wanting to change something. Killing a run,
crashing, or hitting a wall the model can't get past are exactly the
moments a user reaches for a different model. So a significant fraction of
"resume" invocations are really "retry under a swapped config."

The current resume path silently re-executes model states under whatever
config is live at resume time. If the user swapped models, the committed
artifact from model A gets potentially overwritten with model B's output,
with no signal that the run is no longer the same run.

Sketch of a cleaner design:

- `orchestra resume <run>` continues the run only if the resolved config
  for the resuming run matches the config that produced the existing
  artifacts. Refuses with a clear error if anything has drifted.
- `orchestra retry <run> [--from <state>]` re-executes from a chosen state
  boundary, accepting that downstream artifacts will be regenerated under
  whatever config is live now.

Note on the `--from <state>` argument: there are no sub-state checkpoints.
States are atomic — model/agent/transform/shell calls are one indivisible
unit, with the artifact commit and state_exit log entry at the boundary.
So `--from` only ever names a state boundary, not a position inside a
state. Less of a nightmare than it first sounds.

Open question: how does `resume` detect config drift without recording
the full resolved config in the log? Recording it in the log is brittle
(schema versioning, hash format drift, false rejections on harmless
adapter version changes). Possibly the answer is: don't try to detect it.
Just document that editing `.orchestra/config.json` between crash and
resume is unsupported, and provide `retry` as the supported path for
"try again with different settings."

Status: backlog. The pass-2 audit shipped a conservative refuse-resume
for agent states with stranded commits (commit c55650e). This idea
extends the framing to the broader resume-vs-retry distinction.


## Fan-out child guard scope: snapshot-fully vs forbid-in-grammar

The pass-6 audit identified that fan-out child transition guards can
reference sibling and other-state counters (`attempts.<state>`,
`retries.<state>`) and other-state envelopes. The pass-6 fix makes
those references read from a snapshot captured at fan_out_start, so
the routing decision is deterministic regardless of sibling thread
scheduling.

Open grammar question: should fan-out child guards be allowed to
reference sibling state at all? Two paths:

1. **Snapshot-fully (current).** The guard sees a frozen view of
   pre-fan-out counters and envelopes. Deterministic, matches the
   existing snapshot pattern for artifact reads, requires no grammar
   change. The shipped fix.

2. **Forbid-in-grammar.** The validator rejects fan-out child guards
   that reference any state other than self/counters. Structurally
   cleaner because it prevents authors from writing routing that
   _looks_ like it depends on sibling progress (it never can, given
   snapshot semantics). May break workflows that already use these
   refs deliberately — though the codebase ships no such workflow
   today.

Recommendation pending decision: keep the snapshot-fully behavior,
revisit if a real workflow surfaces that wants the cross-state
reference and the snapshot semantics are too surprising. If grammar
restriction wins later, the validator change is small (extend the
existing fan-out child rule in `_phase5_state_validation`).

Status: backlog. Pass-6 fix landed snapshot-fully via commit
extending FanOutSnapshot with attempts and retries.


## Subprocess transcript location: per-project vs per-run

Subprocess adapters write each invocation's raw stdout/stderr to a
debug transcript at `project_dir/.mcloop/logs/<timestamp>.log`. The
file holds the model's full output: any secret, customer snippet,
internal doc excerpt, tool output, or stderr diagnostic the model
emitted. The pass-9 fix tightened the file mode to 0600 and the
parent directories to 0700 so other local users on a shared host
cannot read them. The location convention itself is unchanged: one
`.mcloop/logs/` per project across runs, designed for cross-run
debugging visibility.

The structural question pass-9 surfaced: should transcripts move
into the run directory entirely (so they live and die with the
run, get cleaned up when a run is rolled back, and inherit the
run-directory's pass-8 mode discipline by construction)? Or should
they become opt-in / debug-only (default off, tooling that wants
them sets a flag)?

Current arguments for per-project:
- mcloop reads transcripts by convention; moving them under the
  run directory breaks that without an mcloop-side change.
- A user looking at a project's recent debug history wants
  cross-run visibility, not per-run isolation.
- Old transcripts persist after a run completes, which is useful
  for offline post-mortem.

Current arguments for per-run:
- Run-directory mode discipline (0700) becomes the only knob; no
  separate per-project enforcement to forget.
- Run lifecycle = transcript lifecycle. Rolling back a run clears
  its transcripts. No "stale debug logs from six runs ago"
  accumulation.
- Cleaner threat model: every byte the run produced lives under
  one tree, and removing the tree removes everything.

Current arguments for opt-in/debug-only:
- Cuts the persistence surface entirely for production use.
  Transcripts only exist when explicitly requested.
- The pass-7/8/9 redaction discipline becomes unnecessary in the
  default path because the file is not produced.

Recommendation pending decision: hold per-project, transcripts at
0600 / 0700 directories, until a real workflow surfaces a problem
that the per-run or opt-in approach would have prevented. The
mode-discipline fix from pass-9 closes the immediate leak; the
structural change is only worth doing if cross-run debugging
visibility turns out to be unused or the cleanup burden becomes
real.

Status: backlog. Pass-9 fix landed mode-discipline only.
