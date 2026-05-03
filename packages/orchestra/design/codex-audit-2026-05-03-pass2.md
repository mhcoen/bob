# Codex Audit Pass 2 — Orchestra

Date: 2026-05-03
Scope: full repository, second pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
61c84c2. Findings filtered to SERIOUS only across four classes: correctness,
security, concurrency, design. Prior audit at
design/codex-audit-2026-05-03.md treated as out-of-scope unless a fix proved
incomplete or introduced a regression.

## Findings

### 1. [correctness] Committed artifacts before state_exit are replayed as incomplete work

Location: orchestra/executor/executor.py:399-461; orchestra/resume/resume.py:112-140

Issue: If the process dies after commit_tentative() but before state_exit is
written, replay sees only state_enter, marks the invocation pending, hides
the already-committed artifact version, and re-enters the state. For agent
states, the workspace mutation already happened, so resume can run the
mutating actor a second time. Verified on a temporary run: the committed
version existed but read_latest() returned None after replay because
visibility stayed pending.

Smallest fix: Add the documented case-1.5 recovery path. On replay, detect
committed non-tentative versions tagged with written_by="<state>#<attempt>"
or matching invocation_id after a state_enter with no state_exit, synthesize
or repair the missing completion record, and route without re-running the
actor. For non-idempotent agent states, prefer refusing resume over silent
re-entry if the envelope/payload cannot be reconstructed safely.

Confidence: verified.
Relation to prior audit: adjacent to fix #2/#3.

### 2. [correctness] Resume after a durable retry transition grants an extra retry

Location: orchestra/executor/executor.py:219-228; orchestra/resume/resume.py:213-218; orchestra/executor/executor.py:719-731

Issue: Live retry accounting depends on _last_state and _last_outcome, which
_close_pending_transition() sets in memory. Replay restores current_state,
attempts, and retries, but not _last_state/_last_outcome. After a crash
immediately after `on error retry ... => same_state`, the resumed entry
resets retries[state] to 0 instead of incrementing it. Verified: a
`retry max 1` workflow performed attempts 2 and 3 after resume, where only
attempt 2 should have been allowed.

Smallest fix: Persist or reconstruct the last transition's
(state_id, outcome, target) into ReplayState and pass it into Executor, or
derive retry-entry state during executor initialization when the last
durable transition targets the same state on error/timeout. The derive path
is smaller and avoids a log schema change. Add a resume test for
crash-after-retry-transition.

Confidence: verified.
Relation to prior audit: adjacent to fix #2.

### 3. [correctness] Dominance validation ignores prompt-template and guard dependencies

Location: orchestra/loader/validator.py:471-676; orchestra/executor/executor.py:574-606; orchestra/executor/executor.py:942-978

Issue: The new must-reach analysis only considers state.reads. Prompt
template variables are independently read from external inputs / store, and
transition guards independently read artifacts and prior envelopes. A
workflow can pass validation while a prompt receives None for an unwritten
artifact, or a guard crashes after actor side effects because it references
a future state envelope. Both cases verified on temporary workflows.

Smallest fix: Treat prompt template variables and guard references as real
data dependencies during validation. Artifact and external refs should go
through the same must-reach check as reads. State-envelope refs should be
rejected unless the referenced state dominates the guarding state, with
special handling for self/counter refs.

Confidence: verified.
Relation to prior audit: regression in #8.

### 4. [concurrency] Parallel adapter transcripts overwrite each other

Location: orchestra/adapters/_subprocess.py:399-415

Issue: Adapter transcript log filenames are only `<second timestamp>_<slug>.log`.
Fan-out children commonly share the same task_label and finish in the same
second, so multiple subprocess adapters write the same transcript path;
later writes silently overwrite earlier transcripts, and multiple payloads
can point at the same final log file. Verified: two immediate
write_log(..., "same task", ...) calls returned the same path and the first
body was lost.

Smallest fix: Include a collision-proof component in transcript filenames
such as invocation_id, state name/attempt, monotonic nanoseconds, or a UUID.
Thread the executor's invocation_id into adapter logging if transcript
correlation matters. invocation_id is the cleanest choice since the
executor already threads it.

Confidence: verified.
Relation to prior audit: adjacent to fix #1.

### 5. [design] Verb and REPL paths ignore project-local workflow overrides

Location: orchestra/cli.py:487-522; orchestra/api.py:993-1040; orchestra/loader/lookup.py:52-79

Issue: resolve_workflow_path() documents project-local
.orchestra/workflows/<name>.orc precedence, and _dispatch_verb() loads
project-local config using Path.cwd(). But run_verb() resolves the workflow
with project_dir=None and calls run_workflow() without project_dir, so CLI
verbs and the REPL run the packaged workflow even when a project override
exists. That is a documented API contract mismatch that can silently run
the wrong workflow.

Smallest fix: Add a project_dir parameter to run_verb(), pass Path.cwd()
from CLI/REPL dispatch, use it for both the history-input introspection
load and the final run_workflow() call. Cover with a project override test
that drops a custom .orc in .orchestra/workflows/ and confirms it overrides
the packaged version.

Confidence: verified by code path and lookup probe.
Relation to prior audit: new.

## Summary

5 serious findings: 3 correctness, 1 concurrency, 1 design. Findings #1 and
#2 are ship-blockers for durable resume integrity. Finding #3 is a
ship-blocker for trusting the strengthened validator: workflows can pass
validation today and still hit the silent-None failure mode the validator
was supposed to eliminate.

The prior audit's #8 fix is incomplete (#3 above). The codex_text sandbox
fix and the direct-execution rejection are clean — no regressions found
there.

origin/main currently has known data-corruption (re-execution of mutating
actors on resume), known wrong-output (validator passes workflows that hit
silent None reads through prompts and guards), and known data-loss
(transcript overwrites under fan-out). It should not be considered stable
for any consumer that depends on resume integrity, validator soundness, or
fan-out transcript correlation.

## Recommended fix order

Fix in this sequence, one commit per group except where noted:

1. **#3 first.** Validator soundness regression. The strengthened validator
   currently provides a false sense of safety, which is worse than the
   pre-fix state because users will trust it. Restoring soundness is
   highest priority.

2. **#1 + #2 in one commit, or #1 then #2.** Both are resume-layer.
   Conservative default for #1: refuse resume when an agent state has a
   committed artifact but no state_exit, fix the log-repair path in a
   follow-up. #2 is a small diff on the same surface, safe to bundle if
   the commit stays focused.

3. **#4** in a separate commit. Mechanical fix, same shape as the prior
   audit's #1.

4. **#5** in a separate commit. Smallest fix; clean separation because
   it is a documented-API contract issue, not a bug-shape issue.

## Constraints for the fix work

- One commit per group above. Do not bundle across groups.
- Empirical verification per inviolate rule #1 in CLAUDE.md: every fix
  must be run, not just reasoned about.
- mypy --strict, ruff, and pytest must all pass after each commit.
- Per standing rules: never mention Claude, Claude Code, or Anthropic in
  any commit message.
- For #1, the conservative refuse-resume path is acceptable as the first
  ship; the log-repair path can land separately. Do not default to the
  silent-recovery path without explicit confirmation.

## Note on Codex methodology this pass

Codex initially reported pytest unavailable in its environment. The user
corrected this — pytest is available, Codex was looking in the wrong place.
The findings above were verified via direct runtime probes per Codex's
description. A fix-time pytest run on the resume tests is still required
before pushing.
