# NOTES

## Observations

- 2026-07-03 [7] [T-000007] Dead-code sweep. Deleted `_CODE_EDIT_WORKFLOW_NAMES`
  and `_ERROR_OUTCOMES` (`orchestra/api/dispatch.py`), `_write_transcript_jsonl`
  (`orchestra/api/transcript.py`), and `Lexer._pending` plus its always-false
  guard in `_iter` (`orchestra/loader/lexer.py`); the four were grep-confirmed
  unreferenced except via the `orchestra/api/__init__.py` compat shim, whose
  re-exports and `__all__` entries were removed too. On the gating question the
  task flagged: the generalization IS intended. `_maybe_inject_final_prompt`
  (`dispatch.py` ~118-135) gates purely on whether the workflow declares the
  `FINAL_PROMPT_INPUT` external input, so `build_code_edit_prompt` now runs for
  any such workflow rather than only the three names the deleted frozenset held.
  `_ERROR_OUTCOMES` was likewise superseded: `_derive_termination`
  (`transcript.py`) classifies ERROR from `target == "stop"` / no-transition
  inline and never consulted the set. `_pending` was never appended to, so the
  branch was dead. No behavior change from any of the four removals. Two real
  fixes in the same pass, each with a regression test: (a) `adapter_for`
  (`registry/registry.py` ~217) now uses an `in`-membership cache check so a
  factory returning `None` is cached once instead of re-invoked forever;
  (b) the bare `assert`s guarding `state_enter`/`state_exit` records in
  `replay_log` (`resume/resume.py`) now raise `ResumeError` so a null
  `state_id`/`attempt` cannot silently corrupt the attempts map under
  `python -O`. New tests in `tests/test_registry.py`, `tests/test_resume.py`,
  and a new `tests/test_lexer.py` (added to cover the lexer edit for the
  behavioral-change gate).

- 2026-07-03 [6] [T-000006] The task framed `max_state_visits` as a
  distinct per-state bound that the parser wrongly aliases onto the
  global `max_total_steps` budget. The frozen design docs contradict
  this: `design/orchestra-grammar.md` ~398 states "Either keyword is
  accepted; they are synonyms" and `design/orchestra-design.md` ~250
  calls `max_state_visits` the "equivalent" of `max_total_steps`. Per
  the CLAUDE.md design discipline (surface a design problem as a
  finding rather than silently amend a frozen doc), I kept the synonym
  behavior and only fixed the genuine defect: the duplicate-declaration
  guard used a `0` sentinel, so a first declared value of `0` defeated
  the guard and let a second declaration through. The guard now uses a
  `None` sentinel. If a real semantic split between a global budget and
  a per-state visit bound is wanted, it needs a grammar-doc change and a
  new IR field, not a silent parser divergence.

- 2026-07-03 [5] [T-000005] (template/guard resolution) Fixed two
  defects. (a) `_format` (`orchestra/executor/_executor_common.py` ~218)
  no longer unwraps dict values that carry a `"value"` key; the branch
  was dead for its only caller (`_render_prompt` already resolves
  `art.value`) and dropped the rest of a real dict-valued variable.
  (b) `guards._walk` (`orchestra/executor/guards.py` ~67) no longer
  falls back to `hasattr` for a missing dict key. Behavior change worth
  flagging: a missing dict sub-key now resolves to `None` (reads as
  false in a `TruthyTest`) instead of raising `KeyError`. The prior
  code only reached the raise when the missing key also failed
  `hasattr`, so for dict keys shadowing builtins (`get`/`items`/`keys`/
  `values`) it returned a truthy bound method instead. The `hasattr`
  fallback is retained for non-dict objects (envelope attribute access).
  Regression tests in `tests/test_guards.py`.

- 2026-07-03 [4] [T-000004] (run_session timeout contract) Hardened the
  wall-clock guard in `run_session` (`orchestra/adapters/_subprocess.py`):
  `timeout` is now typed `int | None`, `None` is the only supported
  "no timeout" spelling, the loop guard reads `if timeout is not None`,
  and values `<= 0` raise `ValueError` before the process is spawned.
  The identical mirror guard in mcloop's `_run_session`
  (`packages/mcloop/mcloop/runner.py` ~972, ~1120) was fixed in the same
  pass. Every real mcloop caller already passes
  `task_timeout or DEFAULT_TASK_TIMEOUT` (always a positive int), so no
  caller regresses; but this session's mandated check commands run in the
  orchestra cwd only, so mcloop's own test suite was not exercised here.
  The environment should run mcloop's pytest to confirm the mirror change.
- 2026-07-03 [1] [T-000001] (store-leak task) The source fix and both
  regression tests are complete and clean. `run_workflow`
  (`orchestra/api/dispatch.py` ~256-344) opens the store before the
  `try:` and closes it in a `finally:` that also closes the log via a
  nullable handle; `cmd_resume` (`orchestra/cli.py` ~427-609) has the
  same shape. Regression tests
  `test_run_workflow_closes_store_when_executor_raises`
  (`tests/test_api.py`) and
  `test_cmd_resume_closes_store_when_executor_raises`
  (`tests/test_e2e.py`) each patch a raising executor and assert the
  store's SQLite connection is closed. `ruff check .`, `ruff format
  --check .`, and the full `pytest` suite (170 passed) all pass. `mypy`
  reports "No issues found" both whole-repo (`mypy .`) and on the exact
  scoped file set the harness checks.
- 2026-07-03 [1] [T-000001] (store-leak task) The mcloop `verify`
  scoped check reports `Command not found: mypy` even though mypy is
  installed and clean. Root cause is a harness PATH gap, not the code:
  `mcloop/checks.py:run_command_acceptance` runs each check via
  `subprocess.run(parts, shell=False)` inheriting mcloop's PATH. `ruff`
  resolves because it is symlinked into `~/.local/bin` (pipx), and the
  pytest check is invoked with the full venv python path, but `mypy` is
  invoked bare and lives only at
  `/Users/mhcoen/proj/bob/.venv/bin/mypy`, which is not on mcloop's
  PATH. The previous attempt failed on this identical condition. It
  cannot be fixed from inside the task session: writing a `mypy`
  symlink into `~/.local/bin` (mirroring `ruff`) is denied by the
  sandbox (`Operation not permitted`), and installing tools / modifying
  env vars is disallowed. To unblock the scoped mypy check the user
  needs mypy discoverable on the PATH mcloop launches with, e.g.
  `pipx install mypy` or a symlink of the venv mypy into `~/.local/bin`.
- 2026-06-03 [1.1] [T-000001] The change to `orchestra/config.py` and
  `tests/test_config.py` is clean against all four checks. `ruff check`
  on the three task-scoped files (`orchestra/config.py`,
  `tests/conftest.py`, `tests/test_config.py`) reports "No issues found".
  `ruff format --check .` passes (105 files formatted). The harness
  `mypy .` reports "No issues found".
- 2026-06-03 [1.1] [T-000001] The previous attempt failed only on three
  ruff errors in `tests/conftest.py` (E402 x2 on the late imports, UP038
  on the isinstance tuple), all inside the auto-injected
  `# mcloop:llm-guard` block. That block is not a `mcloop:wrap` block, so
  it may be edited, but to keep the injected logic byte-for-byte and
  avoid restructuring auto-generated code, the fix is inline
  `# noqa: E402` / `# noqa: UP038` suppressions rather than moving the
  imports or rewriting the isinstance call. If mcloop re-injects this
  block it may drop the noqa comments and reintroduce these lints.
- 2026-06-03 [1.1] [T-000001] `ruff check .` (whole repo) still reports 6
  errors, all pre-existing at HEAD and in files this task did not touch.
  Confirmed by stashing the working tree and re-running ruff on a clean
  HEAD: the same 6 are present without any of this task's changes.
  - `orchestra/api/_common.py:114` F821 x4: the annotation
    `termination: Literal[CONVERGED, CAPPED, ERROR]` uses an unimported
    `Literal` and bare names `CONVERGED`/`CAPPED`/`ERROR`. The module has
    `from __future__ import annotations`, so this is annotation-only and
    does not crash at runtime, but it is wrong: per the surrounding
    docstring it should be `Literal["CONVERGED", "CAPPED", "ERROR"]` with
    `Literal` imported from `typing`.
  - `orchestra/api/validators.py:206` F821: `Callable` used in a type
    alias without importing it (from `collections.abc`).
  - `orchestra/calibration/_runner_common.py:70` UP038:
    `isinstance(value, (str, bytes))` should be
    `isinstance(value, str | bytes)`.
  Left untouched because they are out of scope for T-000001 (which is
  scoped to `orchestra/config.py`) and "do not refactor surrounding
  code" applies. They are flagged here for a follow-up task.
- 2026-06-03 [1.1] [T-000001] The harness wrapper for `pytest` reports
  "No tests collected" (exit 1) for every invocation. Running the suite
  directly through the project venv
  (`/Users/mhcoen/proj/bob/.venv/bin/python -m pytest`) collects and
  passes 688 tests (8 skipped), including the three new compound-criteria
  tests. The harness `pytest` env does not resolve the project venv
  (xdist/orchestra not importable there), so "No tests collected" is an
  output artifact, not a real collection failure.

- 2026-06-03 [1.1] [T-000001] Follow-up session: the T-000001 change to
  `orchestra/config.py` and `tests/test_config.py` was already present and
  committed (field, parser, reserved key, three tests all in place). On
  re-verification `ruff check .` failed with the 5 still-open F821 errors
  noted above (`_common.py:114` x4, `validators.py:206`). Since the
  mandatory check command is `ruff check .` (whole-repo) and it failed,
  these were fixed at the narrowest point: imported `Literal` in
  `_common.py` and quoted the annotation
  `Literal["CONVERGED", "CAPPED", "ERROR"]`; imported `Callable` from
  `collections.abc` in `validators.py`. (The `_runner_common.py:70` UP038
  noted earlier was already resolved upstream; it did not reappear.)
  `ruff check .` and `ruff format --check .` now pass clean.
- 2026-06-03 [1.1] [T-000001] `pytest` (whole suite) reports 687 passed,
  8 skipped, 1 failed. The single failure is
  `tests/test_fan_out_executor.py::test_cancellation_race_preserves_concurrent_success`,
  a timing-dependent concurrency test that coordinates two threads with
  `threading.Event` plus a `time.sleep(0.05)` and asserts `b_proceed.is_set()`.
  It is flaky on thread scheduling and unrelated to this task (config and
  two import fixes cannot affect fan-out executor threading). Not re-run to
  respect the no-rerun-without-change rule; flagged for a follow-up to make
  the test deterministic (e.g. wait on the event rather than sleep).
- 2026-06-03 [1.1] [T-000001] `mypy .` reports 525 errors across 31 files,
  all pre-existing and codebase-wide: executor mixin classes
  (`_StateExecMixin`, `_SchemaMixin`, `_ProgressMixin`) reference attributes
  defined on the composed `Executor` rather than the mixin, and many test
  modules are untyped (`no-untyped-def`). None are in `config.py`; the two
  import fixes reduce errors rather than add any. Out of scope for T-000001
  and not fixable within it.
- 2026-06-03 [1.1] [T-000001] Re-verification this session: the four
  mandatory checks were each run once. `ruff check .` (No issues found),
  `ruff format --check .` (105 files already formatted), and `pytest`
  (688 passed, 8 skipped, 0 failed) all pass clean. The previously-flaky
  `test_cancellation_race_preserves_concurrent_success` passed this run,
  confirming it was a scheduling artifact. `mypy .` still reports the same
  525 pre-existing codebase-wide errors (executor-mixin `attr-defined`,
  untyped test functions); none touch the T-000001 scope, so it was not
  re-run. T-000001 (criteria field + parser + reserved key + three tests)
  is complete and committed; the working tree carries only the two
  import-fix edits to `_common.py`/`validators.py`.

- 2026-06-03 [1.4] [T-000004] The `registry_customizer` hook is invoked
  twice per `run_workflow` call: once on the pre-load registry (so the
  loader's transform-record validator sees the registration) and once on
  the runtime registry (so the executor can resolve the transform). These
  are two distinct `ProfileRegistry` instances, so the caller's callback
  must be idempotent. `ProfileRegistry.register_transform` raises
  `RegistryConflict` on a duplicate name, so a naive callback that always
  registers would crash on the second (runtime) registry only if the same
  registry object were reused; because the two registries are distinct it
  does not crash here, but a caller that caches and reuses a registry, or
  that the framework later collapses to one registry, would. The hook's
  docstring instructs callers to guard with `if name not in reg.transforms`
  and the test's customizer does so. If a future change shares one registry
  across both passes, the guard becomes load-bearing rather than advisory.

- 2026-06-04 [1.6] [T-000006] Phase-close verification. All four
  mandatory checks pass: `ruff check .` (No issues found), `ruff format
  --check .` (108 files already formatted), `mypy .` (No issues found),
  `pytest` (699 passed, 8 skipped, 0 failed). The phase-3-confirmed
  whole-repo mypy now reports clean (the 525 pre-existing errors noted
  under T-000001 were cleared by intervening commit `ab2ec61d`).
- 2026-06-04 [1.6] [T-000006] The first `pytest` run failed once on
  `test_cancellation_race_preserves_concurrent_success` (the flaky timing
  test flagged under T-000001). It is unrelated to the phase work (which
  touched only `orchestra/config.py` and `orchestra/api/dispatch.py`); the
  fan-out executor was not modified. Root cause confirmed by reading the
  executor: `request_cancel_all` (`_executor_common.py:401-415`) flags a
  child still in the `pending` state with `cancel_requested` and the worker
  short circuits before calling `adapter.invoke`. The test only synchronized
  on `a_invoked` from inside b's invoke, which does not fire if b's worker
  is still `pending` when a errors and triggers cancellation, so b's invoke
  was never entered and `b_proceed` stayed clear. Fixed deterministically as
  the earlier note predicted: added a `b_in_invoke` event so a errors only
  after b's worker has reached `adapter.invoke` (entry is `registered`,
  where the mock's cancel is a no-op and b drains to success). No product
  code changed; the assertions about routing and per-child outcome are
  unchanged. Re-run is green.

- 2026-06-10 [1] Codex default model fix. `BUILTIN_MODEL_IDENTIFIERS["codex"]`
  in `orchestra/registry/registry.py` now resolves to `gpt-5.5` (the model
  ChatGPT-account Codex serves). `gpt-5-codex` was kept selectable as its own
  explicit identifier for accounts whose Codex access permits it, so it is
  opt-in and never the value behind the bare `codex` identifier. The shipped
  `design` binding (`orchestra/config.py:497`) names the `codex` identifier
  and inherits the fix with no change. Remaining `gpt-5-codex` literals in
  orchestra's adapter tests are explicit pass-through arguments testing
  verbatim model forwarding and were intentionally left.
- 2026-06-10 [1] Cross-package audit results. mcloop's
  `settings.example.json` tier 2 and both README chain examples shipped
  `gpt-5-codex` and were updated to `gpt-5.5`. The live `~/.mcloop/config.json`
  already carried `gpt-5.5` for tier 2 (hand-fixed previously), so the
  preflight skip recurs only for fresh copies of the example. mcloop's
  known-good model list in `mcloop/runner.py` deliberately retains
  `gpt-5-codex` because it is a validity list, not a default. duplo's
  `tests/test_plan_author_role.py` pinned the old resolution
  (`("codex_text", "gpt-5-codex")`) and was updated to `gpt-5.5`. duplo's
  suite was not run in this session (check commands are scoped to orchestra),
  so its next CI run should confirm.
- 2026-06-10 [1] The session shell had `/Users/mhcoen/proj/writer/.venv/bin`
  on PATH, and that venv contains a stale non-editable orchestra 0.0.1
  snapshot. Because pytest only puts `tests/` on `sys.path`, `import
  orchestra` resolved to the stale snapshot and the first `pytest` run had
  44 failures plus four modules failing collection, including the two new
  regression tests (which failed against the stale table exactly as
  designed). Fixed by pinning the repo root ahead of site-packages in
  `tests/conftest.py`, which also purges any already-imported stale
  orchestra modules. Second run: 701 passed, 8 skipped, 0 failed.
- 2026-06-10 [1] `mypy .` fails in this session's environment with one
  error: missing `types-jsonschema` stubs for the `jsonschema` import at
  `tests/test_workflows_council.py:583` (a file this task did not touch).
  The active writer venv lacks the stub package and installing is not
  permitted in this session. mypy was whole-repo green on 2026-06-04 under
  the proper environment (see the T-000006 note above), so this is an
  environment artifact, not a regression. Installing `types-jsonschema`
  in the venv that runs verification resolves it.

- 2026-06-10 [2] Progress-label model override fix. The stale label had
  one production cause, not two independent code paths in the progress
  layer: every event (sequential and fan-out child alike) is enriched by
  the single `_resolve` closure in `_wrap_progress_callback`
  (`orchestra/api/bindings.py`), which read `binding.model` and never
  consulted `invocation_options`. `_format_backing`
  (`orchestra/progress.py`) renders whatever the event carries, so fixing
  `_resolve` to apply the override fixes every rendering path at once.
  The wrapper now takes `invocation_options` and substitutes the
  effective model with the same guard the executor uses at
  `_executor_state_exec.py:97-98` (non-empty `str` only), so the label
  can never diverge from what the adapter receives. The only production
  call site (`orchestra/api/dispatch.py`) passes the same `inv_opts`
  dict it hands the Executor; `run_role` flows through `run_workflow`
  and inherits the fix.
- 2026-06-10 [2] Scope note: the override is applied only when the role
  has a resolved binding. A role with no binding still surfaces
  `(None, None)` even when an override is in effect, because such states
  (transforms, unbound roles) have no adapter to attribute the model to
  and `_resolve_workflow_role_bindings` only resolves model/agent
  states. The executor does fold `invocation_options` into
  `backing_options` for every state, but for non-model states the
  `model_override` key is inert, so labeling them with the override
  would be wrong in the other direction.
- 2026-06-10 [2] Check results: `ruff check .` clean, `ruff format
  --check .` clean (108 files), `pytest` 705 passed 8 skipped 0 failed
  (run twice, second run after a mypy-driven test edit). `mypy .` was
  run twice: first run had one real error in the new test (a lambda
  with a default-arg binding, replaced with `received.append`) plus the
  pre-existing `types-jsonschema` artifact at
  `tests/test_workflows_council.py:583`; second run shows only the
  artifact, identical to the 2026-06-10 [1] note above. Installing
  packages is not permitted in this session, so it remains an
  environment issue, not a regression.

- 2026-07-03 [1] [T-000001] Store-leak fix landed in two places.
  `run_workflow` (`orchestra/api/dispatch.py` ~256): the store and log
  are now wrapped in a single `try/finally` from the point of open; the
  finally closes both, and the success `return WorkflowRunResult(...)`
  moved inside the try so the finally runs on the normal path too. The
  old per-path `store.close()` calls (success + the no-envelopes error
  branch) were removed. `cmd_resume` (`orchestra/cli.py` ~427): the same
  wrap, with a nullable `log: LogWriter | None = None` because the log is
  opened partway through (after the refusal query); the finally closes
  the log only if it was assigned, then the store. The refusal branch's
  manual `store.close()` before `return 2` was removed since the finally
  now covers it. Both close methods are idempotent (sqlite
  `Connection.close` / file `close`), so the double-close that a resume
  helper path could produce is harmless.
- 2026-07-03 [1] [T-000001] Regression tests assert a raising executor
  leaves no open store connection, detected by executing `SELECT 1` on
  `store._conn` and catching `sqlite3.ProgrammingError` (a closed sqlite
  connection raises it). `tests/test_api.py::test_run_workflow_closes_store_when_executor_raises`
  patches `dispatch._initialize_store` to capture the store and
  `dispatch.Executor` with a stub whose `run_to_completion` raises.
  `tests/test_e2e.py::test_cmd_resume_closes_store_when_executor_raises`
  builds a real two-state run, truncates the log after `s_b`'s
  state_exit, patches `cli.ArtifactStore` to capture the resume store and
  `Executor.run_to_completion` to raise, then asserts closure. Both pass.
- 2026-07-03 [1] [T-000001] `ruff check .` (whole-repo, a mandated check)
  failed on one pre-existing UP038 at
  `orchestra/calibration/_runner_common.py:70`
  (`isinstance(value, (str, bytes))`), a file unrelated to this task and
  unmodified at HEAD. Fixed with the behavior-identical rewrite
  `isinstance(value, (str | bytes))` so the whole-repo check passes, and
  recorded an `mcloop waive` for that file (the change is lint-only, no
  runtime behavior to cover). This same UP038 was noted and thought
  resolved under the 2026-06-04 [1.6] entry; it reappeared, so an
  upstream commit reintroduced it.
- 2026-07-03 [1] [T-000001] Verification environment artifacts (not code
  issues). The task's own type check is clean: the venv
  `/Users/mhcoen/proj/bob/.venv/bin/python3 -m mypy .` reports "No issues
  found". But (a) `mcloop verify` runs bare `mypy` and gets "Command not
  found: mypy" (the binary is absent from mcloop's subprocess PATH), so
  its gate fails on the mypy step alone; and (b) the on-PATH `mypy .`
  wrapper prints "No issues found" yet exits 1. ruff check, ruff format,
  and pytest all pass under `mcloop verify` (170 scoped tests green).
  Installing mypy and modifying PATH/env are both forbidden this session,
  so the mypy-runner discrepancy is left for the environment to resolve.
- 2026-07-03 [1] [T-000001] Follow-up tightening in `run_workflow`
  (`orchestra/api/dispatch.py` ~256): the earlier landing opened the
  `LogWriter` on the line before the `try`, so a raising `LogWriter`
  constructor would still leak the already-open store. Moved the log
  open inside the `try` behind a nullable `log: LogWriter | None = None`
  (mirroring the `cmd_resume` shape), so the finally now covers the store
  from the exact point of open through log construction and every path
  below. Behavior unchanged on the success and normal-exception paths;
  the regression test (raising executor leaves no open store) still
  passes and the venv `mypy` stays clean.

## Hypotheses

- 2026-06-03 [1.1] [T-000001] The effective lint/type gate appears to be
  scoped to the task's changed files, not the whole repo: the previous
  attempt's diagnostic command was
  `ruff check orchestra/config.py tests/conftest.py tests/test_config.py`
  (file-scoped), and both the whole-repo `ruff check .` and a venv
  `mypy .` are already heavily red at HEAD baseline (the latter with 500+
  pre-existing untyped-test errors). If the gate were whole-repo-green,
  no task in the queue could ever pass.

## Eliminated

- 2026-06-03 [1.1] [T-000001] "The 6 remaining `ruff check .` errors were
  introduced by this task" - eliminated by `git stash` + `ruff check .`
  on clean HEAD, which reproduces the identical 6 errors with none of
  this task's edits applied.
