# Codex Audit Pass 4 — Orchestra

Date: 2026-05-03
Scope: full repository, fourth pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
5d41e63. Pytest run: 415 passed, 2 skipped. Packaged workflows validate
through the API pre-load registry; current codex/claude help still accepts
the adapter flags in use. Findings filtered to SERIOUS only across four
classes. Prior reports treated as out-of-scope unless a fix proved
incomplete or introduced a regression.

## Findings

### 1. [design] Fan-out child self-loops still load but are silently skipped

Location: orchestra/loader/validator.py:292-325; orchestra/executor/executor.py:1746-1842

Issue: The pass-3 validator treats any fan-out child transition targeting
the child's own name as "local retry," but retry is represented by
retry_max, not by a plain self-target. A workflow with child `a` declaring
`on complete => a` loads successfully; execution enters `a` once, then the
parent fan-out joins and finishes, so the child self-loop is never
followed. Verified with a temp workflow: state entries were launch, a, j,
and transitions were only launch -> j, j -> done.

Smallest fix: Replace the `child_t.target == child_decl.name` exemption
with a retry-specific check. For fan-out children, allow only terminal
targets (done/stop) plus bounded retry transitions whose post-retry target
is terminal; reject plain self-targets and retry exhaustion targets that
point back into the graph.

Confidence: verified.
Relation to prior audits: regression in pass-3 #1.

### 2. [correctness] Workflow digest does not cover prompt files used on resume

Location: orchestra/api.py:910-916; orchestra/cli.py:288-325; orchestra/executor/executor.py:589-598

Issue: New runs record a digest of the .orc file only. File and template
prompts are read from disk at invocation time, so changing templates/*.md
after a crash but before resume changes the actor input while the workflow
digest still matches. Verified with a temp run containing state_enter but
no state_exit: after changing only templates/dummy.md, cmd_resume succeeded
and the resumed actor_prepare prompt preview contained the new prompt
text.

Smallest fix: Record and check a manifest of every file-backed prompt
source after role/template overrides are resolved, or snapshot those files
into the run directory and point resume at the snapshot. The manifest
should include config-supplied instruction template files as well as
.orc-relative prompt files. Manifest-and-refuse is the smaller commit and
matches the pass-3 #3 precedent.

Confidence: verified.
Relation to prior audits: adjacent to pass-3 #3.

## Summary

2 serious findings: 1 design, 1 correctness. Both are ship-blockers. #1 is
a regression on pass-3 #1 — the validator/executor contract is unsound in
exactly the class of failure pass-3 was meant to close, just narrower.
#2 is a ship-blocker for crash-safe resume when workflows use file-backed
prompts, which the shipped workflows (council, anonymous reviewers,
draft_then_adjudicate, propose_critique_synthesize) all do.

origin/main is more stable than at any prior pass, but should not be left
as-is if validator soundness and resume integrity under prompt-file edits
are release criteria.

No regressions found in the transcript discriminator, project_dir
threading, dominance checks, or codex_text sandboxing.

## Convergence note

Pass-1 yield: 8 findings.
Pass-2 yield: 5 findings (1 regression on pass-1).
Pass-3 yield: 3 findings (1 regression on pass-2).
Pass-4 yield: 2 findings (1 regression on pass-3).

Yield is decreasing pass over pass, but each pass continues to find one
regression introduced by the previous pass's fix. The pattern is
"shrinking but persistent" rather than "converged." Pass-5 should be
expected.

## Recommended fix order

1. **#1 first.** Regression on pass-3 work that just shipped. Validator
   soundness is the foundation everything else trusts. The fix is small
   and precise (retry-aware rule instead of name-match exemption); the
   regression test should be the workflow Codex verified with.

2. **#2 second.** Extends the digest mechanism from one file to a
   manifest. Schema-additive, backward-compat the same way the original
   digest was. Worth doing before any consumer (mcloop, vroom) starts
   trusting workflow_digest as a soundness guarantee.

## Constraints for the fix work

- One commit per finding. Do not bundle.
- Empirical verification per CLAUDE.md inviolate rule #1.
- mypy --strict, ruff, pytest must all pass after each commit.
- Per standing rules: never mention Claude, Claude Code, or Anthropic
  in any commit message.
- For #1, the fix must be expressed as a retry-policy check, not as a
  structural pattern match. The pass-3 attempt failed because it pattern-
  matched on target name; the correct rule examines retry_max and bounds
  the post-exhaustion target.
- For #2, scope is manifest-and-refuse only. Do not expand into
  source-snapshotting in the same commit. Manifest must cover both
  .orc-relative prompt files and config-supplied instruction templates.
