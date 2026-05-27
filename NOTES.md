# NOTES

## Observations

- 2026-05-26 [1.2] [T-000002]: After backfilling `created_at` across the
  four workspace PLAN.md files, `pytest` passes deterministically with
  `-p no:randomly` (6137 passed, 118 skipped). Run with the default
  randomized order it surfaces 30-42 pre-existing failures and 6-117
  errors that vary run-to-run — purely test-isolation flakiness in
  packages/duplo/tests (e.g. `AttributeError: module 'duplo' has no
  attribute 'spec_writer'` from `monkeypatch.setattr` reaching for a
  submodule the test order has not yet imported). The check command as
  listed (`/Users/mhcoen/proj/bob/.venv/bin/pytest`) honors the
  randomized order set by `pyproject.toml`, so the orchestrator's
  verification will hit the same flakiness regardless of this task.
- 2026-05-26 [1.2] [T-000002]: Backfill was implemented as a surgical
  line-rewrite (script in `/tmp/backfill_created_at.py`, run once and
  discarded) rather than a parse + render round-trip, to avoid
  introducing canonical-form normalization changes on PLAN.md files
  that predate strict mode (notably `packages/duplo/PLAN.md`, which is
  compat-mode with no task IDs).
- 2026-05-26 [1.2] [T-000002]: For compat-mode tasks (no `T-NNNNNN`
  prefix) the pickaxe signature is the entire stripped task line; for
  strict-mode tasks the signature is just the `T-NNNNNN` token. Both
  resolved every task: 19/19, 383/383, 197/197, 785/785 across the
  four PLAN.md files. No task fell through to a null `created_at`. The
  earliest dates land in March 2026 (duplo bootstrapping); workspace
  PLAN.md tasks share the recent commit date because the file itself
  was first added today.
- 2026-05-26 [1.2] [T-000002]: Re-applied the backfill after the prior
  session's edits were never committed (the script output from the
  earlier attempt sat in `git stash` and the checkpoint commits only
  toggled PLAN.md's checkbox between `[ ]` and `[!]`). The current run
  re-executes `/tmp/backfill_created_at.py` against the same four
  files and reaches the same fill counts (1384/1384, 0 skipped). All
  files re-parse cleanly through `bob_tools.planfile.parser.parse_plan`
  with `with_created_at == total` per file.
- 2026-05-26 [1.2] [T-000002]: The randomized-order pytest flakiness
  from the prior note is reproduced exactly: run 1 surfaced 31 failures
  in `packages/duplo/tests/test_phase5_integration.py`; run 2 surfaced
  22 errors in `packages/duplo/tests/test_pipeline.py` instead. The
  failing tests pass in isolation (e.g. `test_cross_origin_url_not_fetched`
  green when run alone). The diff between the runs touches only the
  four PLAN.md files plus this NOTES.md, so the flakiness is genuinely
  pre-existing and not introduced by the backfill.

- 2026-05-26 [1.3] [T-000003]: The canonical-validator warning is gated
  on `Plan.task_namespace is not None` — files that never opted in to
  the namespace scheme stay silent so the existing corpus does not
  acquire a deprecation drumbeat. The warning fires only when a
  namespaced plan still carries an unprefixed id, which is the
  migration-aid use case. Open question for the reviewer: should bare
  `T-NNNNNN` in a non-namespaced plan also warn (i.e. signal that the
  preamble should declare a namespace at all)? Left silent for now;
  the task description reads "legacy unprefixed IDs continue to parse",
  which I interpret as no-warning-without-declared-namespace.
- 2026-05-26 [1.3] [T-000003]: `task_namespace` lives on `Plan` and is
  recognized in the preamble before the first phase/bugs heading. The
  parser does not enforce uniqueness across declarations — a repeated
  `<!-- task_namespace: ... -->` comment last-write-wins, matching the
  existing `_PHASE_ID_COMMENT_RE` policy. The structural-sanity check
  was deliberately not extended to flag duplicates; if that ends up
  mattering, the canonical validator is the better hook than parse
  time.
- 2026-05-26 [1.3] [T-000003]: Pre-existing test-isolation flakiness
  in `packages/duplo/tests/` from notes [1.2] is unchanged. The bare
  `pytest` invocation surfaces 20-30 failures/errors in the duplo
  pipeline/status/phase5_integration files that all pass in isolation
  and have no relationship to the planfile namespace work — most
  surface as `AttributeError: module 'duplo' has no attribute 'main'`
  from `_clean_argv` fixture's `monkeypatch.setattr("duplo.main.
  _check_migration", ...)` when the duplo.main submodule hasn't been
  imported yet in the xdist worker. Running `pytest packages/bob-tools`
  alone is fully green: 707 passed, 5 skipped, including the new
  `test_task_namespace.py`.
- 2026-05-26 [1.3] [T-000003]: Re-confirmed the flakiness signature on
  two back-to-back full-workspace runs of the check command. The prior
  attempt's failure set (11 in `packages/duplo/tests/test_spec_writer.py`)
  did not repeat; this session's runs surfaced 23 failures + 5 errors
  spread across `test_reauthor.py`, `test_saver.py`,
  `test_platform_integration.py`, and `test_main.py` instead. The
  shifting failure set across runs with no intervening code change is
  itself the diagnostic — every failure traces to xdist worker order
  and `monkeypatch.setattr("duplo.main.…", …)` reaching for a submodule
  the worker has not yet imported, not to any T-000003 change.

- 2026-05-26 [2] [T-000002]: Root cause of the workspace-pytest pollution
  was `packages/mcloop/tests/test_ledger_pause.py` and
  `packages/mcloop/tests/test_integration_slice_d.py`. Both used raw
  `sys.modules["duplo"] = types.ModuleType("duplo")` /
  `sys.modules["duplo.reauthor"] = fake_mod` to mock the duplo import
  path for `mcloop.ledger_pause.auto_reauthor`. The assignment was
  never restored, so the real `duplo` package (with its eager
  submodule imports) got replaced by an empty stub for the remainder
  of the xdist worker's life. Any subsequent test on the same worker
  that referenced `duplo.main`, `duplo.spec_writer`, etc. via
  `monkeypatch.setattr("duplo.main.X", ...)` raised `AttributeError:
  module 'duplo' has no attribute 'main'`. Random test order made the
  failure set shift run-to-run because xdist assigns tests to workers
  arbitrarily — sometimes the polluting test ran before duplo tests
  on the same worker, sometimes not. Fix: convert every assignment
  to `monkeypatch.setitem(sys.modules, ...)` so pytest restores the
  prior entries on teardown; thread `monkeypatch` through the helper
  methods (`_install_fake_duplo`, `_CapturingFakeReauthorModule.install`)
  and the test methods that previously didn't take it. Three
  back-to-back workspace-root `pytest` runs now report 6179 passed,
  118 skipped with no errors. The new
  `TestFakeDuploInstallCleanup` class pins the cleanup behavior so
  the next person who copies the install pattern gets caught if they
  go back to raw assignment.

## Hypotheses

## Eliminated

c38a500: Added live activity tracking for agent-routed sessions. The subprocess adapter now parses tool_use events from the CLI's JSON stream and surfaces them as a second line under the elapsed-time progress ticker. This shows users what the agent is currently doing (e.g., "Read /path/to/file") without coupling the reporter to the subprocess module. The feature is wired by default in the API, CLI, and REPL.

c646bc9: Added a public API function `run_role` to execute iterative design workflows via role bindings, returning a structured result with termination status, transcript, and error details. Updated the config schema to support nested role bindings for judge and reviewer models. Marked the corresponding development task as completed.
