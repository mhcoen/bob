# Codex Audit Pass 5 — Orchestra

Date: 2026-05-03
Scope: full repository, fifth pass
Methodology: Codex re-audit against /Users/mhcoen/proj/orchestra at HEAD
d0a9622. Pytest run: 419 passed, 2 skipped. Findings filtered to SERIOUS
only across four classes. Prior reports treated as out-of-scope unless a
fix proved incomplete or introduced a regression.

## Findings

### 1. [correctness] Symlink retargeting bypasses prompt-manifest resume refusal

Location: orchestra/manifest.py:80-91; orchestra/cli.py:351-364; orchestra/executor/executor.py:589-598

Issue: compute_prompt_manifest() keys entries by path.resolve(), which
follows symlinks, but the executor later opens the original declared path.
Verified: a run where templates/dummy.md initially pointed to a.md;
truncating before resume, then retargeting the symlink to b.md left a.md
unchanged, cmd_resume returned 0, and the resumed actor_prepare used the
retargeted prompt. This is a prompt-drift bypass of the pass-4 manifest
fix.

Smallest fix: Record and compare the actual declared absolute prompt path
the executor opens, not only its resolved target. If symlinks are
supported as a feature, include symlink metadata or hash through the
declared path so retargeting changes the manifest result.

Confidence: verified.
Relation to prior audits: regression in pass-4 #2.

### 2. [correctness] Relative workflow paths can resume against a different source directory

Location: orchestra/cli.py:225-227; orchestra/cli.py:290-388

Issue: cmd_run records workflow_path as str(args.workflow). Verified:
running `orchestra run w.orc` from one directory, truncating before a
state re-entry, then resuming from another directory containing
byte-identical w.orc but different templates/dummy.md passes both digest
and manifest gates; resume finished successfully and the actor saw the
other directory's prompt. The manifest checked the original absolute
prompt path, but load_workflow(workflow_path) loaded the resume-time
relative path and changed workflow.source_dir.

Smallest fix: Resolve the workflow path to an absolute canonical path
before loading and before writing run_start; resume should load exactly
that recorded path. Apply the same discipline anywhere run_start metadata
records workflow paths.

Confidence: verified.
Relation to prior audits: adjacent to pass-4 #2 and pass-3 #3.

### 3. [correctness] Fan-out child retry ignores transition declaration order

Location: orchestra/executor/executor.py:1829-1839

Issue: Fan-out child retry logic scans for any transition with the
child's outcome and retry_max, instead of selecting the first matching
transition as documented. Verified: a child with `on error => stop`
followed by `on error retry max 1 then stop` retried anyway, succeeded
on the second call, and the parent joined successfully instead of taking
the fan-out error path. This violates the first-match transition
contract and can route a workflow to the wrong terminal result.

Smallest fix: Reuse the normal transition-selection semantics for the
child outcome, including declaration order and guards, then retry only
when the selected transition is a retry transition and budget remains.

Confidence: verified.
Relation to prior audits: regression in pass-4 #1.

## Summary

3 serious findings, all correctness. Findings #1 and #2 are ship-blockers
for resume integrity; #3 is a ship-blocker for fan-out validator/executor
soundness. The one-regression-per-pass streak continues: pass-4 #1 and #2
both have incomplete fixes.

origin/main is not stable enough to leave as-is if resume safety and
fan-out transition semantics are release criteria.

The mock-dependent test concern flagged in pass-3 #9 was checked
explicitly this pass: the suite consistently uses mock/recording adapters
for live workflow tests, no concrete unmocked LLM invocation case found.
That methodology question is now closed for orchestra. (Note: an
unrelated mcloop test-suite incident on the same day exhibited exactly
this failure shape against the orchestra wrapper integration, fixed in
mcloop separately.)

## Convergence note

Yields: 8 -> 5 -> 3 -> 2 -> 3.

The expected-decreasing curve flattened or possibly inverted. Pass-5
found three findings and two are regressions.

Two readings:

1. The manifest mechanism is the unstable surface. Pass-4 #2 was the
   original prompt-file digest. Pass-5 found two distinct bypasses of
   that exact mechanism (#1 symlink retargeting, #2 relative workflow
   paths). The third finding (fan-out retry order) is unrelated. So if
   the manifest-bypass class is treated as one underlying bug surfacing
   through different vectors, pass-5's "real" yield is 1 unique class
   plus the fan-out regression. Under that read, convergence is still
   happening, just slower.

2. The audit-fix loop has found its floor. Each fix introduces one
   regression, audit catches it, fix introduces another. Pass-6 evidence
   will tell.

Reading 1 is more charitable; reading 2 is more honest until pass-6
evidence rules it out. Recommendation in the next section.

## Recommendation: design pass on the manifest mechanism

Two audits in a row have found bypasses of the prompt-manifest fix.
Pass-4 introduced the manifest; pass-5 found two distinct ways the
"recorded path identity" and "executor-opened path identity" can drift.
This suggests the abstraction isn't quite right, not that we got
unlucky twice.

Before commit-fixing #1 and #2 in series, consider one explicit design
pass on the manifest mechanism's full input surface:

- What identity does the manifest pin? The declared path string? The
  resolved absolute path? The path the executor will eventually open?
  These three differ in exactly the cases pass-5 found.
- What changes between run_start and resume that should invalidate
  the manifest? The .orc file (covered by digest). Prompt file contents
  (covered by manifest hash). Prompt file path resolution (NOT covered;
  bypass #1 and #2). Working directory (NOT covered; bypass #2).
  Symlink targets (NOT covered; bypass #1).
- What is the right abstraction? One option: snapshot every input file
  into the run directory at run_start and resolve resume against the
  snapshot, not against the live filesystem. This eliminates the path-
  identity question entirely at the cost of disk space.

The fix-then-audit cycle has now found three manifest-related bugs
across two passes (pass-4 #2, pass-5 #1, pass-5 #2). One more iteration
of "patch the leak" risks a pass-6 finding a fourth vector. A design
pass that picks the right abstraction once is likely cheaper than
chasing the leaks.

## Recommended fix order

If proceeding with patch-the-leaks: #1 first, #2 second, #3 third.

If proceeding with design-pass-then-fix: hold #1 and #2 pending design
decision, ship #3 (fan-out retry, unrelated to manifest mechanism) as
a standalone commit.

## Constraints for the fix work

- One commit per finding if patch-the-leaks path.
- Empirical verification per CLAUDE.md inviolate rule #1.
- mypy --strict, ruff, pytest must all pass after each commit.
- Per standing rules: never mention Claude, Claude Code, or Anthropic
  in any commit message.
- For #1, the fix must address declared-path identity, not just resolved-
  path identity. Test must cover the symlink retargeting case Codex
  verified.
- For #2, canonicalization must happen at run_start (not just at resume),
  so the recorded workflow_path is unambiguous from the moment the run
  starts.
- For #3, the fix must reuse normal transition-selection semantics. The
  current scan-for-retry pattern is structurally wrong; replacing it
  with first-match-then-check-retry is the right shape.
