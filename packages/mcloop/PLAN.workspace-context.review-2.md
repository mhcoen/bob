# Convergence Check: PLAN.workspace-context.md

Headline verdict: **GO WITH RESIDUAL CORRECTIONS**.

The revised plan absorbed the first review's substantive corrections. It now has explicit CLI/context-construction sequencing, an expanded git-helper inventory, a new main-run-loop Stage 5, ledger schema precondition language, comprehensive subcommand coverage, and a CLAUDE.md resolver stage. Two residual corrections remain before handoff:

1. Stage 3 should not say `run_loop` constructs `WorkspaceContext` from `args.*` unless `run_loop` is also changed to receive those values. The plan should say `_main` constructs `wsctx` and passes it into `run_loop`, or add explicit `workspace_override`, `scope_override`, and `plan_path` parameters to `run_loop`.
2. Stage 5 names `errors.py` and `investigator.py`, but does not fully map their current `project_dir` uses. Add explicit mappings for `errors.py` diagnostic flow and `investigator.py` app/run detection.

## Section F Corrections

| Prior correction | Status | Revised text and judgment |
|---|---:|---|
| 1. Add a stage before git refactoring that constructs `WorkspaceContext` in `main.py`/`run_loop` and preserves current `--file` semantics. | **PARTIALLY RESOLVED** | Revised Stage 3 is now before git refactoring and says it "constructs the `WorkspaceContext` at the top of `run_loop`" and "`--file` becomes an alias for `--plan-path`." It also preserves plan-parent semantics in Stage 2. Residual issue: Stage 3 says `run_loop` uses `args.plan_path`, `args.file`, `args.workspace`, and `args.scope`, but current `run_loop` does not receive `args`; `_main` does. |
| 2. Fix resolver semantics for explicit `plan_path` in standalone repos. | **RESOLVED** | Stage 2 defines the upward-walk anchor as "`plan_path.parent` when `plan_path` is given explicitly, otherwise `cwd`" and says standalone uses "`plan_path.parent` if plan_path is given, else `cwd`." Tests include "`mcloop --file /other/repo/PLAN.md` resolves to `/other/repo/`." |
| 3. Move CLI/context construction earlier than Stage 3, or merge it into Stage 3. | **RESOLVED** | The revised plan makes CLI/context construction Stage 3 and git operations Stage 4: "It runs before the git stage because every later stage needs a `WorkspaceContext` to receive." |
| 4. Expand the git stage to include every git helper in `git_ops.py`. | **RESOLVED** | Stage 4 lists `_checkpoint`, `_push_or_die`, `_stage_safe`, `_commit`, `_changed_files`, `_has_meaningful_changes`, `_has_uncommitted_changes`, `_get_diff`, `_worktree_status`, `_committed_files`, `_get_committed_diff`, `_snapshot_worktree`, and `_get_git_hash`, and requires every git subprocess to use `workspace_root`. |
| 5. Add an explicit main-run/session stage covering runner, code_edit, checks/build/dependency preflight, logs, reviewer state, NOTES/CLAUDE sync, output, and auto-wrap/reinject. | **PARTIALLY RESOLVED** | Stage 5 now exists and names `run_loop`, `runner.py`, `code_edit.py`, checks/build/dependency modules, `output.py`, `claude_md_sync.py`, `claude_md_check.py`, reviewer modules, `errors.py`, `ledger_pause.py`, `investigator.py`, and `orchestra_override.py`. It is strong overall, but under-specifies `errors.py` and `investigator.py` mappings; details below. |
| 6. Add the missing `CLAUDE.md` context resolver stage. | **RESOLVED** | Stage 9 is "CLAUDE.md task-context resolver (Phase 1)" and creates `mcloop/claude_md_resolver.py` with `assemble_claude_context(wsctx)`, precedence classes, conflict detection, token budget checks, runner wiring, and tests. |
| 7. Expand state-path work to all `.mcloop` files and current `logs/` behavior. | **PARTIALLY RESOLVED** | Stage 6 covers `active-pid`, `interrupted.json`, `eliminated.json`, run summaries, maintain log, pending state, errors clearing, wrapper sources, reviewer state, and CLAUDE sync pending state. Stage 5 maps `log_dir` to `scope_root / "logs"`. Residual issue: `errors.py` diagnostic logs and source/context reads are not fully mapped in the Stage 5 `errors.py` task. |
| 8. Clarify ledger schema/config preconditions and test `ledger_config.py`. | **RESOLVED** | Top-level RULEDOUT and Stage 7 both say ledger schema migration is a precondition, out of scope for this plan, and McLoop must fail closed if absent. Stage 7 explicitly refactors `ledger_config.py` and adds `tests/test_ledger_config.py`. |
| 9. Add `uninstall` and `ack-orchestra-override` to subcommand dispatch coverage. | **RESOLVED** | Stage 8 is "Subcommand dispatch (comprehensive)" and explicitly includes "`uninstall` and `ack-orchestra-override`" plus tasks for both. |
| 10. Strengthen compatibility/consolidated tests to assert concrete path/cwd decisions under mocked execution. | **RESOLVED** | Stage 10 requires mocked cycles and concrete assertions for task cwd, check cwd, git cwd, ledger dir, `.mcloop` artifacts, plan/BUGS/NOTES paths, changed-files output, and CLAUDE context. It also requires hermetic tests with no real LLM or network. |

## Proposed [RULEDOUT] Entries

| Proposed entry | Included? | Strength |
|---|---:|---|
| Do not derive standalone scope from cwd when explicit `--file`/`--plan-path` points elsewhere. | **Yes, equivalent and stronger.** | Included almost verbatim. It preserves plan-parent behavior and adds a workspace/cwd ambiguity failure case. Strong enough. |
| Do not leave any current `project_dir` reference unaudited. | **Yes, equivalent.** | Included as "Every surviving path parameter must be renamed or explicitly documented..." and tests must assert mappings. Strong enough, though Stage 5 still needs two mapping additions to satisfy it. |
| Do not stop after git operations and subcommands; main run/session setup is in scope. | **Yes, equivalent.** | Included with explicit modules: `run_loop`, `runner.py`, `code_edit.py`, checks/build, logs, NOTES/CLAUDE sync, reviewer state, dependency preflight, auto-wrap/reinject. Strong enough. |
| Do not omit the CLAUDE.md resolver. | **Yes, equivalent.** | Included and strengthened by saying Claude Code ancestor search is the old behavior and insufficient. Strong enough. |
| Do not treat ledger relocation as only a directory change. | **Yes, equivalent and stronger.** | Included with explicit bob-tools schema fields, out-of-scope statement, and fail-closed requirement. Strong enough. |

## Stage 5 Detail

Stage 5 now names the right broad file set:

- `main.py` `run_loop`
- `runner.py`
- `code_edit.py`
- `checks.py`, `test_runner.py`, `targeted.py`, `dep_validator.py`, `conftest_guard.py`, `pytest_optimizations.py`
- `output.py`
- `claude_md_sync.py`, `claude_md_check.py`
- `config.py`, `review_integration.py`, `reviewer.py`
- `errors.py`
- `ledger_pause.py`
- `investigator.py`
- `orchestra_override.py`

It does identify the major `project_dir` categories in most of these files: git to `workspace_root`, scope state to `scope_root`, and subprocess/test/build cwd to `execution_cwd`. But it does **not** identify every current `project_dir` reference in two named files.

**Residual gap: `errors.py`**

Current `errors.py` uses `project_dir` for multiple distinct purposes:

- `.mcloop/errors.json` at `errors.py:54`: `scope_root/.mcloop/errors.json`.
- `PLAN.md`/`BUGS.md` at `errors.py:139` and `:219`: should be `wsctx.plan_path` and `scope_root/BUGS.md`.
- `git log` cwd at `errors.py:150-153`: should be `workspace_root`.
- diagnostic `log_dir` at `errors.py:162`: should be `scope_root/logs`.
- source-file reads at `errors.py:174`: should be against `execution_cwd` unless the source path is workspace-relative.
- `run_diagnostic(project_dir, log_dir, ...)` at `errors.py:186-193`: subprocess cwd should be `execution_cwd`, logs under `scope_root`.

Stage 5 only says "Refactor `mcloop/errors.py` clearing and the auto-wrap reinjection path called from `run_loop`. `.mcloop/errors.json` is `scope_root` state..." That is too narrow. It misses the diagnostic flow and plan/bug/git/source mappings.

**Residual gap: `investigator.py`**

Current `investigator.py` uses `project_dir` to derive process/app context:

- `_derive_process_name(project_dir)` calls `detect_run(project_dir)` and falls back to `project_dir.name`.
- `gather_bug_context(project_dir)` reads `.mcloop/last-run.log`, calls `_derive_process_name(project_dir)`, and calls `detect_app_type(project_dir)`.

Stage 5 says bug-context gathering reads from `scope_root/.mcloop/` and OS crash reports. That covers `.mcloop/last-run.log`, but it omits `detect_run`/`detect_app_type`, which should use `execution_cwd`, and process-name fallback, which should probably use `scope_root.name` or `execution_cwd.name` explicitly.

## Mapping Spot Checks

**`runner.py`: mapping is accurate.**

Current `run_task` takes `project_dir`, passes it to `invoke_code_edit`, uses it for subscription preflight cwd, and calls `_run_session(..., project_dir, ...)`. Stage 5 maps CLI session subprocess cwd to `wsctx.execution_cwd` and git-based change detection to `workspace_root`. That matches the source's mixed responsibilities.

**`code_edit.py`: mapping is accurate.**

Current direct backend uses `project_dir` for `_run_session` cwd and `_detect_changed_files`; orchestra backend uses it for `load_config(project_dir)`, subscription preflight, `inputs["project_dir"]`, `invocation_options["project_dir"]`, `run_workflow(... project_dir=project_dir)`, and `data_root=log_dir / "orchestra-runs"`. Stage 5 correctly splits these: project-local orchestra config from `scope_root`, direct subprocess cwd as `execution_cwd`, orchestra per-edit `project_dir` as `execution_cwd`, and changed-files through Stage 4 git helpers.

**`checks.py` / `test_runner.py` / `dep_validator.py`: mapping is accurate.**

These modules inspect `pyproject.toml`, `mcloop.json`, `.venv`, `run.sh`, test directories, and execute test/check commands with `cwd=project_dir`. Stage 5 maps them to `execution_cwd`, which is the right field for package-local checks and dependency validation.

**`review_integration.py` / `reviewer.py`: mapping is mostly accurate.**

Current review integration uses git to get the current commit, `.mcloop/reviews` for state, `BUGS.md` for inserted findings, and reviewer CLI `project_dir` for git diff, changed-file content, `PLAN.md`, logs, and result output. Stage 5's split is right: reviewer state/results under `scope_root/.mcloop`, git inspection under `workspace_root`, inspected files under `execution_cwd`. The eventual implementation must pass enough context to the reviewer subprocess; a single `project_dir` argument will no longer be sufficient.

**`errors.py`: mapping is incomplete.**

As above, Stage 5 maps only `.mcloop/errors.json` clearing. It must explicitly map diagnostic git, plan/bugs, source reads, log dir, and diagnostic session cwd.

**`investigator.py`: mapping is incomplete.**

As above, Stage 5 maps `.mcloop` log reads but misses `detect_run` and `detect_app_type`, which are execution-cwd operations.

## Final Readiness

The revision is close enough to proceed after the two residual corrections are patched into the plan text. The architecture and sequencing are now sound; the remaining issue is making Stage 5 precise enough that an autonomous executor does not leave `errors.py` and `investigator.py` half-migrated or implement `run_loop` context construction against unavailable `args`.
