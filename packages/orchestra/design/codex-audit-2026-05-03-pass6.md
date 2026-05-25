# Codex Audit Pass 6 — Orchestra

Date: 2026-05-03
Scope: full repository, sixth pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
fd38f19. Pytest run: 431 passed, 2 skipped. Findings filtered to SERIOUS
only across four classes. Prior reports treated as out-of-scope unless a
fix proved incomplete or introduced a regression.

## Findings

### 1. [correctness] Fan-out child retry guards can observe live sibling state

Location: orchestra/executor/executor.py:951-996; orchestra/executor/executor.py:1796-1858; orchestra/executor/executor.py:2369-2379; orchestra/loader/validator.py:555-574

Issue: FanOutSnapshot carries only artifacts and envelopes, and
_select_transition_decl(..., snapshot=snapshot) uses that snapshot only
for artifact values. Guard evaluation still receives live self._attempts,
self._retries, and live self._envelopes, while the validator allows
attempts.<any-state> and retries.<any-state> in guards. A fan-out child
can therefore place

    on error when attempts.slow > 0 => stop
    on error retry max 1 then stop

before the retry transition; whether it retries depends on whether the
sibling thread has entered slow before this child selects its transition.

Verified: loaded an outside-repo workflow with that exact guarded
transition shape. Same failed child envelope selects the retry transition
when attempts.slow == 0, but selects the earlier non-retry transition
when attempts.slow == 1. Full pytest still passes (431 passed, 2 skipped)
because no shipped workflow uses this pattern.

Smallest fix: Extend FanOutSnapshot to capture attempts and retries at
fan_out_start. Make _select_transition_decl build the entire GuardContext
from the snapshot in fan-out child mode, layering only the current child's
just-produced envelope on top. If sibling-counter visibility in fan-out
child guards is not part of the supported contract, also reject those
refs in the validator so the grammar cannot express schedule-dependent
routing.

Confidence: verified.
Relation to prior audits: regression in pass-5 #3.

## Summary

1 serious finding, correctness. Ship-blocker if fan-out child guarded
retries are part of the supported contract because the same workflow
can retry or not retry based on thread scheduling.

The pass-5 #3 fix introduced this regression by reusing the linear
first-match transition selector in fan-out children without extending
the fan-out snapshot to cover the full guard context.

The pass-5 prompt-source snapshot redesign survived this pass clean. No
findings against orchestra/prompt_snapshot.py, the three-tier backward
compatibility, the canonicalized workflow_path, or the snapshot-and-
rewrite contract. The redesign was correct.

origin/main is close, but should not be left as-is if fan-out child
guarded retries are in scope.

## Convergence note

Yields: 8 -> 5 -> 3 -> 2 -> 3 -> 1.

Pass-6 found one. The streak of one-regression-per-pass continues, but
yield broke down to 1, and the regression is in pass-5's smaller commit
(fan-out retry, f30e2e9), not the larger architectural change
(snapshot redesign, 220d81f). The snapshot redesign held up under
audit.

Pass-7 is the test of whether convergence has actually happened. Zero
findings would close the cycle; one would extend the streak.

## Recommended fix

Single commit: extend FanOutSnapshot to include attempts and retries
captured at fan_out_start, route them through _select_transition_decl
in fan-out child mode along with the existing artifact snapshot. Keep
the live envelope layered on top for the current child only. Regression
test the workflow Codex verified with.

The validator-side question (forbid sibling/future counter refs in
fan-out child guards entirely vs allow but snapshot them) is a
grammar design question. Snapshot-fully is the smaller commit and
matches the snapshot mechanism's existing pattern. Forbid-the-grammar
is structurally cleaner but breaks any workflow that already uses
these refs (probably none, but worth checking before deciding).
Recommendation: snapshot-fully now, log the grammar question to
IDEAS.md as a separate design decision.

## Constraints for the fix work

- Single commit.
- Empirical verification per CLAUDE.md inviolate rule #1. The
  regression test must reproduce the schedule-dependent routing
  Codex demonstrated, then assert deterministic routing post-fix.
- mypy --strict, ruff, pytest must all pass.
- Per standing rules: never mention Claude, Claude Code, or
  Anthropic in any commit message.
- The snapshot extension must not break the existing
  test_fan_out_sibling_reads_use_snapshot_not_live_store invariant
  (no live-store reads from worker threads). The fix extends what
  the snapshot covers, not how it is consumed.
