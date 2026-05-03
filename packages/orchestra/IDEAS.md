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
