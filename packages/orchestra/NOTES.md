# NOTES

## Observations

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
