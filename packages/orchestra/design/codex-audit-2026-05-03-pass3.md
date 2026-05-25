# Codex Audit Pass 3 — Orchestra

Date: 2026-05-03
Scope: full repository, third pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
742c797. Pytest run: 407 passed, 2 skipped. Findings filtered to SERIOUS
only across four classes. Prior reports treated as out-of-scope unless a
fix proved incomplete or introduced a regression.

## Findings

### 1. [design] Fan-out children can declare subgraphs that the executor silently skips

Location: orchestra/executor/executor.py:1746-1842; orchestra/loader/validator.py:238-276

Issue: The validator accepts a fan-out child whose own `on complete`
transition is another `fan_out`, but `_fan_out_child_worker()` runs only
that child's body plus local retry handling. It never dispatches the
child's transition graph. Verified with a nested fan-out workflow:
execution finished `done`, but the log contained only launch, child, and
final; the configured `grand` and `child_join` states never ran.

Smallest fix: Reject nested fan-out and non-terminal child subgraphs in
validation with a clear error.

Decision: rejection, not implementation. The semantics of "a model running
inside itself" are unmanageable; nested fan-out is the case that violates
the rule "a state cannot appear inside its own execution scope." Iteration
loops via the transition graph (e.g. iterate_until_acceptable) are
unaffected because each entry is a separate scope.

Confidence: verified.
Relation to prior audits: new, adjacent to pass-2 #3.

### 2. [correctness] Stranded-commit refusal misses the pre-artifact_write crash window

Location: orchestra/executor/executor.py:408-425; orchestra/resume/resume.py:186-195; orchestra/cli.py:291-321

Issue: commit_tentative() commits store rows before the executor writes
artifact_write records. The pass-2 refusal logic only populates
committed_without_exit from artifact_write, so a kill after store commit
but before the first artifact_write leaves durable committed versions
with no replay marker. Verified by creating a committed state_invocation
row after state_enter and before artifact_write: replay reported
current_state='edit', last_state_completed=False, and
committed_without_exit=[].

Smallest fix: Make resume consult the store for committed versions
belonging to the incomplete state-enter invocation id, not just the log's
artifact_write records. A small store query keyed by invocation_id, used
before re-entering an incomplete agent state, would close this exact
window.

Confidence: verified.
Relation to prior audits: regression in pass-2 #1.

### 3. [correctness] Resume replays against a modified workflow file without detection

Location: orchestra/api.py:910-921; orchestra/cli.py:257-280; orchestra/executor/executor.py:1437-1485

Issue: run_start stores only workflow_path and metadata, and cmd_resume()
reloads whatever .orc file is currently at that path. If the process
dies after state_exit but before transition, resume_pending_transition()
selects the missing transition from the modified workflow, not the
workflow that produced the durable envelope. Verified by changing a
workflow from `a => b` to `a => c` after truncating between a.state_exit
and a.transition; resume wrote ('a', 'c') and ran c.

Smallest fix: Record a workflow digest at run_start, refuse resume if
the current file differs. Source snapshotting gives stronger
reproducibility but is larger; digest refusal is the smallest safe
behavior and is sufficient to close the silent-divergence path.

Confidence: verified.
Relation to prior audits: new, adjacent to pass-2 #1/#2.

## Summary

3 serious findings: 2 correctness, 1 design. Two ship-blockers (#2 and
#3) for crash-safe resume integrity. #1 is a ship-blocker if nested
fan-outs are intended to be valid .orc; per the user decision, they are
not, so the fix is validator rejection.

The pass-2 #1 fix is incomplete (#2 above). No regressions found in the
transcript discriminator, project_dir threading, or dominance checks.

origin/main should not be left as-is for resume integrity. #2 in
particular reopens the exact silent-agent-re-execution failure mode
pass-2 #1 was meant to close, just through a slightly different crash
window.

## Recommended fix order

1. **#2 first.** Regression on pass-2 #1. Same surface as c55650e but
   query the store, not the log. Smallest fix that closes the actual
   ship-blocker. Conservative refuse-resume path matches the c55650e
   precedent.

2. **#3 second.** Workflow digest at run_start, refuse on mismatch on
   resume. Small commit, schema-stable (one new field). Closes the
   .orc-drift case without preempting the broader resume-vs-retry
   redesign logged to IDEAS.md.

3. **#1 third.** Validator rejection of nested fan-out. Per user
   decision, the rule is "a state cannot appear inside its own execution
   scope." Iteration loops via the transition graph are unaffected.

## Constraints for the fix work

- One commit per finding. Do not bundle.
- Empirical verification per CLAUDE.md inviolate rule #1.
- mypy --strict, ruff, pytest must all pass after each commit.
- Per standing rules: never mention Claude, Claude Code, or Anthropic
  in any commit message.
- For #1, ensure the rejection error names the offending state and
  explains the rule clearly so workflow authors can fix it.
- For #3, the digest is for resume safety, not reproducibility.
  Do not expand scope into source snapshotting in the same commit.
