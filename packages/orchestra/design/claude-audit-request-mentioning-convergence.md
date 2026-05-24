Audit the orchestra repository at /Users/mhcoen/proj/orchestra for SERIOUS
issues only. Produce a numbered list of findings; the user decides what to
fix. Review only — do not modify the repository.

This is the seventh audit pass. Prior reports:
  design/codex-audit-2026-05-03.md       (pass 1, 8 findings)
  design/codex-audit-2026-05-03-pass2.md (pass 2, 5 findings, 1 regression on pass 1)
  design/codex-audit-2026-05-03-pass3.md (pass 3, 3 findings, 1 regression on pass 2)
  design/codex-audit-2026-05-03-pass4.md (pass 4, 2 findings, 1 regression on pass 3)
  design/codex-audit-2026-05-03-pass5.md (pass 5, 3 findings, 2 regressions on pass 4)
  design/codex-audit-2026-05-03-pass6.md (pass 6, 1 finding, 1 regression on pass 5)

Pass-6 fix shipped on origin/main between fd38f19 and 4429c4a. Confirm
the exact commit list with git log when you start. The pass-6 commits
were:

  3059030  Fan-out child guards: snapshot attempts and retries at
           fan_out_start (#1, regression on pass-5 #3)
  4429c4a  Pass-6 audit report committed

Scope: the entire repository. Fresh eyes on the whole codebase, including
the fan-out snapshot extension and code that was not touched.

Severity bar — SERIOUS means at least one of:

1. Correctness: a code path that produces wrong output, corrupts state,
   drops data silently, deadlocks, crashes the process, or violates a
   documented invariant. Edge cases real users will hit.

2. Security: credential leakage, command injection through prompt content
   or role bindings, path traversal in any file write or read, sandbox
   escape via adapter subprocess flags, log writes that include secrets,
   deserialization of untrusted JSON/TOML/YAML into executable structures.

3. Concurrency: race conditions, lost progress events, broken locking
   around shared mutable state, unsafe use of asyncio primitives across
   thread boundaries.

4. Design / API: abstraction leaks that will force breaking changes soon,
   API surfaces where the documented contract and the implementation
   disagree, places where adding a new adapter or pattern requires
   touching five files, places where the .orc grammar permits
   configurations the executor cannot run.

Do NOT report:

- Style nits, ruff catches, naming preferences, line length issues.
- Type annotations mypy --strict would flag but are not actually unsound.
- "Could be more elegant" refactors. Only design issues that will bite
  within the next year.
- Test coverage gaps unless the gap hides one of the four issue classes.
- Documentation typos.
- Performance unless it crosses into correctness.
- Any finding from the prior six audits unless you have empirical
  evidence the fix is incomplete or introduced a regression. If you find
  such evidence, flag it explicitly as "regression in pass-N #M" with
  the failing case.
- The known limitations documented in IDEAS.md (resume-vs-retry
  distinction, model-swap detection, broader config-drift, fan-out
  child guard sibling-counter grammar). Do not re-flag these. Adjacent
  surfaces with empirical failure cases not covered there are in scope.

Methodology:

1. Read README.md, design/orchestra-design.md, and IDEAS.md.
2. Read all six prior audit reports. Treat their findings as
   out-of-scope unless you have evidence of incompleteness or regression.
3. For each new finding, identify: file and line range, the issue, why
   it is serious (which of the four classes and the concrete failure
   mode), and the smallest change that would resolve it. Do not make
   the change.
4. If you suspect an issue but cannot confirm without running code,
   mark it "needs verification" and explain what would confirm or
   disconfirm it. Pytest is available; use it. Do not assert a bug
   exists without empirical grounding.
5. Pay particular attention to the fan-out snapshot extension shipped
   in 3059030. The fix extended FanOutSnapshot from {artifacts,
   envelopes} to {artifacts, envelopes, attempts, retries}. Verify:
   a. The capture site in _run_fan_out_group: dicts are copied under
      self._attempt_lock inside the existing log+store critical
      section, just before the children-retry-counter reset. Verify
      the copy is deep enough — if attempts/retries values are mutable
      containers themselves, a shallow copy lets later mutations leak
      into the snapshot.
   b. The resume_fan_out symmetry: snapshot reconstructed from live
      state minus completed siblings' counters. Verify "completed
      siblings" is the same set as the existing envelope-exclusion
      logic uses, not a parallel computation that could drift.
   c. The current-child layered-on-top rule for self-references. The
      child sees its own just-incremented counter and just-completed
      envelope. Verify that "self" is identified by state name, not
      by transition or invocation id, since multiple states could
      share part of a state's identity through aliasing.
   d. The regression test (synthetic FanOutSnapshot, asserts
      deterministic routing). Verify the test actually exercises the
      snapshot path and not a mock that bypasses it. The test is
      load-bearing for confidence in the fix.
6. The broader fan-out snapshot mechanism is now the canonical pattern
   for "give the worker a stable view of executor state." Verify that
   any other piece of state that guards or transitions might read is
   either in the snapshot already or provably uninteresting. Candidates:
   - The store's artifact write history (snapshot covers current values
     only).
   - The log's transition history (could a guard reference past
     transition outcomes?).
   - Any per-run mutable state on Executor that workers can reach
     through self.
7. The pass-5 prompt-source snapshot redesign held up under pass-6.
   Re-verify against pass-7 with one specific lens: are there resume
   paths or workflow-loading paths that bypass restore_prompt_snapshots?
   For example, a workflow loaded fresh by a non-resume code path
   (REPL?) that then somehow continues an existing run, or a test
   fixture that constructs an Executor without going through
   cmd_run/api.run_workflow.
8. Cross-cutting: every previous audit pass found a regression on
   the previous pass's smaller commit, not on the larger architectural
   change. Pass-6 was a single small commit. If pass-7 finds a
   regression, it would necessarily be on that commit (the snapshot
   extension). Look there first if any concurrency or correctness
   smell is in fan-out.
9. Adjacent code untouched in pass-6:
   - The non-fan-out path through _select_transition_decl. The default
     snapshot=None branch should still read live state. Verify that
     the linear path didn't accidentally start using snapshots, which
     would be a subtle wrong-direction regression.
   - The retry-counter reset under _attempt_lock. If the snapshot
     captures attempts/retries before the reset, but the reset itself
     can be observed by a non-fan-out caller, that's a separate
     race surface.
10. The mock-dependent test concern was answered in pass-5 for orchestra
    (no unmocked LLM cases). Skip re-checking. Already closed.

Output format:

Numbered findings, each with:
  N. [class: correctness|security|concurrency|design] short title
     Location: <file>:<line range>
     Issue: <2-4 sentence description of the actual failure mode>
     Smallest fix: <one paragraph>
     Confidence: <verified | needs verification + what would confirm>
     Relation to prior audits: <new | regression in pass-N #M | adjacent to pass-N #M>

After the numbered list, a one-paragraph summary:
  - total findings by class,
  - whether any are ship-blockers,
  - whether any prior fix introduced a regression,
  - whether origin/main is stable enough to leave as-is.

Convergence note: yields are 8 -> 5 -> 3 -> 2 -> 3 -> 1. The streak of
one-regression-per-pass has held for six consecutive passes. Pass-7 is
the test of whether the cycle has converged. Zero findings would close
the cycle for this codebase. One or more would extend the streak to
seven and the marginal value of an eighth pass should be reconsidered
explicitly rather than assumed.

If you find zero serious issues, say so plainly. Do not pad. After six
passes against this codebase, the honest zero is the most valuable
result available — it terminates a costly cycle. The ritual finding
costs the next fix-then-audit round and risks introducing the next
regression.

Stop when the list is complete. Review only — do not modify the
repository.
