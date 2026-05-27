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

- 2026-05-26 [2.8] [T-000013]: Verified (a) and (b) by inspection of
  `packages/orchestra/orchestra/executor/executor.py`. (a) The schema
  layer `_apply_schema_layer` converts invalid JSON
  (`_JsonExtractError` from `_extract_last_json_object`) and schema
  violations (`Invalid` from `spec.validate`) into
  `ErrorRecord(kind="actor_failure")` routed via `outcome="error"`.
  The executor's `decl.retry_max` mechanism (line ~745 linear,
  fan-out worker loop ~2426) implements retry-once-then-fail when
  the workflow declares `on error retry max 1 then stop`. (b)
  `prepare()` and `invoke()` exceptions are caught at executor.py
  ~527-577 and converted to error envelopes; `state_exit` is always
  written (line ~717) BEFORE the transition is selected. `LogWriter`
  fsyncs each record (`log.py:153`), and `api._derive_termination`
  classifies a `stop`-bound transition as `ERROR` with an
  ErrorRecord built from the last state_exit's error field.
- 2026-05-26 [2.8] [T-000013]: Added (c). Introduced
  `Executor.on_state_exit: Callable[[StateDecl, Envelope, str |
  None], None] | None` (executor.py). The callback fires from inside
  the three state_exit-producing paths (`_run_one_state`,
  `_execute_state_body`, `_execute_transform_body`,
  `_write_cancelled_state_exit`) AFTER the log record is fsynced and
  the visibility index updated. Exceptions raised inside the
  callback are swallowed so a misbehaving writer cannot abort an
  in-flight run. `api.run_workflow` now installs an
  `_IncrementalTranscriptWriter` that appends one `Turn` JSON line
  per state_exit to `<run_dir>/transcript.jsonl`, fsynced per line,
  guarded by a thread lock so concurrent fan-out completions cannot
  interleave bytes. `run_role` no longer rewrites the file at
  end-of-run — the file is already on disk. A crash mid-run leaves
  every role completion durably recorded.
- 2026-05-26 [2.8] [T-000013]: Pre-existing flake in
  `packages/orchestra/tests/test_fan_out_executor.py::test_cancellation_race_preserves_concurrent_success`.
  The test relies on `time.sleep(0.05)` between the controller's
  `request_cancel_all` and the worker's check; under 16-worker
  parallel pytest the timing can flip and the worker observes the
  cancel before reaching the assertion's gate. Failed once,
  immediately passed on rerun with no code change. The test
  pre-dates this task and is purely timing-sensitive; my changes
  are no-ops when `on_state_exit=None` (the default for that test).

- 2026-05-26 [2.9] [T-000014]: The compound `design` role binding's
  leaf keys must match the workflow's role names for the executor to
  resolve them. `design_loop.orc` declares `role judge_role` and `role
  reviewer`, so the canonical binding keys are `judge_role` and
  `reviewer`. The existing test fixture at
  `tests/test_api.py::test_run_role_unknown_role_raises` uses `judge`
  (not `judge_role`) but never reaches workflow execution, so the
  mismatch is latent. The same-actor validator
  (`_validate_design_distinct_actors`) accepts either spelling for the
  judge slot to stay compatible with the existing fixture, but a real
  run with the `judge` key would fail at role resolution. Leaving the
  fixture as-is and documenting the canonical keys in
  `orchestra/README.md`.
- 2026-05-26 [2.9] [T-000014]: `default_config()`'s new `design` entry
  only kicks in when neither `~/.orchestra/config.json` nor the
  project-local config exists. A user with a global config that omits
  `role_bindings` does NOT inherit the default `design` binding; the
  `load_config` merge function only falls back to `default_config`
  when both layers are absent. Worth revisiting if the inherited-vs-
  declared-default ergonomics surface as a real complaint — a "merge
  default_config as base" change has bigger blast radius than this
  task warrants.
- 2026-05-26 [2.9] [T-000014]: `BUILTIN_MODEL_IDENTIFIERS` is exposed
  both as a module-level constant and via `ProfileRegistry.model_identifiers`.
  The constant is the source of truth used by run_role's resolver; the
  registry copy is populated by `with_core()` so the data is visible
  to any future code that iterates the registry. Keeping both in sync
  is currently trivial (one-time population in `with_core()`), but if
  per-process customization of model identifiers becomes a real use
  case the resolver would need to read from the registry instance
  rather than the module constant.

## Hypotheses

## Eliminated

c38a500: Added live activity tracking for agent-routed sessions. The subprocess adapter now parses tool_use events from the CLI's JSON stream and surfaces them as a second line under the elapsed-time progress ticker. This shows users what the agent is currently doing (e.g., "Read /path/to/file") without coupling the reporter to the subprocess module. The feature is wired by default in the API, CLI, and REPL.

c646bc9: Added a public API function `run_role` to execute iterative design workflows via role bindings, returning a structured result with termination status, transcript, and error details. Updated the config schema to support nested role bindings for judge and reviewer models. Marked the corresponding development task as completed.

ef8a634: Improved max_rounds handling for design workflows. The round cap now defaults to 4, can be set in role bindings, or overridden per call. Validation ensures max_rounds is a positive integer before workflow start, preventing premature termination. The design_loop workflow now reads max_rounds as an external input for consistent behavior.

561010a: Added an incremental transcript writer that appends each state completion to a JSONL file as the run progresses, ensuring durability even after a crash. The executor now supports an optional on_state_exit callback for this purpose, called after each state_exit is logged. This eliminates the need to rebuild the transcript file at the end of a run.
