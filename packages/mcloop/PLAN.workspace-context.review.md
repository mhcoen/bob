# Review: PLAN.workspace-context.md

Verdict: **GO WITH SPECIFIC CORRECTIONS**. The plan has the right core abstraction and mostly matches the high-level architecture, but it is not ready to hand to McLoop as-is. The two load-bearing corrections are:

1. Add the missing `CLAUDE.md` task-context resolver work from the architecture proposal's McLoop implication item 9.
2. Add an explicit stage for the main run/session path (`run_loop`, `runner.py`, `code_edit.py`, checks/build/logs/notes/reviewer/CLAUDE sync). Today the plan adapts git/state/subcommands, but leaves the core task execution path using `project_dir`.

## Section A - Citation Verification

| Plan/reference claim | Verdict | Current source state |
|---|---:|---|
| `mcloop/git_ops.py:49-63` / `_ensure_git` checks `project_dir/.git` and would initialize in `project_dir` | **FAIL, partial stale range** | Lines 49-63 define `_ensure_git`, compute `git_dir = project_dir / ".git"`, and return if it exists. The actual `git init` is lines 69-72, and initial commit handling continues through line 112. The claim is true, but the cited range is incomplete. Correct range for the failure mode is `mcloop/git_ops.py:49-112`. |
| `mcloop/main.py:835-840` derives `project_dir` from the PLAN parent | **PASS** | `project_dir = checklist_path.parent`, lifecycle `_project_dir`, `log_dir`, `plan_path`, and `bugs_path` are all derived there. |
| `mcloop/main.py:436-485` subcommand dispatch derives from `checklist_path.parent` | **PASS with omission** | The range dispatches `wrap`, `install`, `uninstall`, `idea`, `ack-orchestra-override`, `maintain`, `sync`, `audit`, and `investigate`. The plan lists only `idea`, `maintain`, `install`, `wrap`, `sync`, `audit`, `investigate`; it omits current `uninstall` and `ack-orchestra-override`. |
| `mcloop/main.py:2324-2396` CLI surface accepts `--file` and lacks workspace/scope flags | **PASS** | The top-level parser includes `--file` and other existing flags; subparsers begin at 2397. No `--workspace`, `--scope`, or `--plan-path` exists. |
| `mcloop/main.py:1122-1160` bug-only mode | **PASS** | The range parses `BUGS.md` and `PLAN.md`, computes unchecked-bug state, and switches `_run_mode` to `"bug-only"`. |
| `mcloop/run_summary.py:75-97` writes summaries under `<project_dir>/.mcloop/runs/` | **PASS** | `write_run_summary(project_dir, summary)` creates `project_dir / ".mcloop" / "runs"` and writes timestamped plus `latest.json` files. |
| `mcloop/lifecycle.py:39-45, :107-119` lifecycle state uses project-level globals and writes `.mcloop/interrupted.json` | **PASS, partial for active-pid** | Lines 39-45 define `_project_dir`; 107-119 begins `_save_interrupt_state` and creates `_project_dir / ".mcloop"`. Active-pid handling is not in the cited range: `_unlink_active_pid_file` uses lines 136-148; `_kill_orphan_sessions` uses lines 377-388. |
| `mcloop/maintain.py:185-218, :240-275` maintain log and command use `project_dir` | **PASS** | `_write_maintain_log` writes `project_dir / ".mcloop" / "maintain-log.json"`; `run_maintain` derives `project_dir = maintain_path.parent`, sets `log_dir`, git operations, and `project_checks`. Commit and final log writes continue later at lines 325-363. |
| `mcloop/idea_cmd.py:18-27` appends to `project_dir / "IDEAS.md"` | **PASS** | Exact current behavior. |
| `mcloop/ledger_emit.py:384-513` emits task lifecycle events and uses `project_dir` for git metadata | **PASS for git metadata, FAIL for ledger location** | The cited range uses `project_dir` for git HEAD, diff stats, parents, branch, author, and subject. It does not derive ledger location. Ledger location is `default_ledger_dir(project_dir)` at `ledger_emit.py:568-570`, and current runtime resolution goes through `ledger_config.py:70-138` and `main.py:969-987`. The plan's statement that `ledger_emit.py` derives log location from `project_dir` is incomplete. |
| `mcloop/ledger_config.py` participates in ledger location | **PASS** | `load_plan_ledger_settings(project_dir=...)` reads `<project_dir>/.orchestra/config.json`, defaults plan path to `<project_dir>/PLAN.md`, and defaults ledger dir to `<project_dir>/.duplo/ledger`. |
| `mcloop/audit.py` uses `project_dir` for audit report/hash/git/checks | **PASS** | `AUDIT_HASH_FILE`, `AUDIT_REPORT_FILE`, `_should_skip_audit`, `_save_audit_hash`, and `_run_single_audit_round` all use `project_dir`. The CLI wrapper is in `main.py:2560-2579`, not `audit.py`. |
| `mcloop/sync_cmd.py` derives `project_dir = checklist_path.parent` | **PASS** | Lines 11-28 kill orphans, ensure git, set `log_dir`, and run sync against `project_dir`. |
| `mcloop/investigate_cmd.py` uses `project_dir` and worktrees | **PASS** | `_cmd_investigate` derives `project_dir = checklist_path.parent` at 598-605; worktree create uses `cwd=project_dir` at 621-624; merge/diff use `project_dir` at 304-359. |
| `mcloop/install_cmd.py` has project-level state | **PASS with scope clarification needed** | `_cmd_install(project_dir)` uses project state only for reviewer config and orchestra override acknowledgement checks. Most install artifacts are global under `~/.claude` or `~/.mcloop`, which should not be moved. |
| `mcloop/wrap.py` instruments under `project_dir` and records crash reports in `.mcloop/errors.json` | **PASS** | Language/entry detection walks `project_dir`; injection bakes `str(project_dir)` into wrappers; canonical wrappers land at `project_dir / ".mcloop" / "wrap"`. The embedded crash writer uses `.mcloop/errors.json`. |
| GPT review Section D3 enumerates McLoop call sites | **PASS, but the plan covers only the examples** | D3 names CLI parsing, subcommand dispatch, git helpers, run summaries, and lifecycle state. It also says every `project_dir` call site must be audited into `workspace_root`, `scope_root`, `execution_cwd`, `state_root`, or `plan_path`; the plan does not yet perform that exhaustive audit. |
| Architecture "Implications for McLoop" items 1-9 | **PARTIAL** | The plan covers items 1-8 partially. It does not cover item 9, `CLAUDE.md` context resolver, at all. |

## Section B - Task Ordering

**Stage 1: Defensive guard**

This can leave McLoop working if implemented narrowly, but the guard criteria are too heuristic. "Ancestor named `packages` with a sibling named `bob`" is not a reliable description of `/path/bob/packages/<name>`, and "any ancestor contains both `.git` and `packages/`" can match normal standalone repos that happen to contain a `packages/` directory. Because the guard is retired later, this is acceptable only as a very small protective patch with tests proving it does not block a standalone root that owns `packages/`.

The compatibility invariant is not affected because `WorkspaceContext` does not exist yet.

**Stage 2: WorkspaceContext primitive**

Hidden prerequisite: the resolver semantics need to match current `--file` behavior before any caller uses it. Current McLoop derives the project from `checklist_path.parent`, not from `cwd`. The proposed fallback says that when no workspace root is found, `workspace_root = scope_root = execution_cwd = cwd`, even if `plan_path` points somewhere else. That breaks compatibility for `mcloop --file /some/repo/PLAN.md` run from another directory.

Correction: in standalone mode, either derive the compatibility root from `plan_path.parent` when an explicit plan path is supplied, or fail loudly if `cwd` and `plan_path.parent` imply different roots. Do not silently choose `cwd`.

**Stage 3: Thread WorkspaceContext through git operations**

This stage has a hidden prerequisite in Stage 6/main dispatch: callers cannot pass `WorkspaceContext` until `main.py`/`run_loop` constructs one. Stage 3 must either include internal context construction in `run_loop` or Stage 6 must move earlier.

It also under-scopes `git_ops.py`. The plan names `_ensure_git`, `_checkpoint`, `_push_or_die`, `_stage_safe`, `_commit`, and `_changed_files`, but current git-sensitive helpers also include `_has_meaningful_changes`, `_has_uncommitted_changes`, `_get_diff`, `_worktree_status`, `_committed_files`, `_get_committed_diff`, `_snapshot_worktree`, and `_get_git_hash`. Those are used by checks, rollback, CLAUDE/NOTES sync, audit, reviewer, ledger emission, and wrapper reinjection. Leaving them on package-local `project_dir` would keep the nested/incorrect-git problem alive.

**Stage 4: Thread WorkspaceContext through state paths**

The named targets are right but incomplete. Lifecycle active-pid and interrupted state need coherent conversion, but lifecycle also writes `.mcloop/eliminated.json`. Current main also writes/cleans `.mcloop/pending`, clears `.mcloop/errors.json`, uses review state, and delegates pending CLAUDE/NOTES sync state to `claude_md_sync.py`. These are not later-stage prerequisites; they belong in this state-path stage or in a dedicated "main run state paths" stage.

**Stage 5: Ledger emission**

Ordering is plausible after git operations, because ledger commit metadata must be read from `workspace_root`. But the architecture explicitly says the ledger schema migration is a precondition for workspace-rooted everything-log writes to be meaningful. The plan only touches McLoop and does not say whether schema/config changes are in scope, already landed, or intentionally deferred. As written, an agent may move the directory while leaving event scope metadata and validation unresolved.

**Stage 6: CLI surface extension**

This should come before or be merged into Stage 3. The work that updates callers to pass `WorkspaceContext` needs a single context-construction point. Stage 6 also must define conflict behavior for `--file` versus `--plan-path`, explicit `--workspace` plus absolute `--plan-path`, and `--scope` plus cwd inference.

The compatibility invariant can break here if bare `--file other/PLAN.md` continues to mean "scope is cwd" instead of "scope is the plan's parent" in standalone repos.

**Stage 7: Subcommand dispatch**

This properly depends on Stage 6, but it omits current subcommands `uninstall` and `ack-orchestra-override`. It also describes `install` as if project-level state is broadly written there; current install is mostly global, with project-local checks for reviewer/orchestra override state. The stage should say exactly which project-local install/ack paths use `scope_root`.

**Stage 8: Compatibility verification**

This is the right final gate, but it cannot be the only compatibility proof. Earlier stages need targeted tests because otherwise McLoop can be broken for several stages before Stage 8 exists. The "complete cycle" test also needs to be explicitly mocked; a real McLoop cycle would invoke LLM CLIs and external tools.

## Section C - Completeness

Against architecture items 1-9:

1. **WorkspaceContext object:** covered, but resolver compatibility with explicit `plan_path` is wrong/incomplete.
2. **CLI surface extension:** covered, but should move earlier and must specify `--file`/`--plan-path` conflict and standalone explicit-plan behavior.
3. **Subcommand dispatch:** partially covered; misses `uninstall` and `ack-orchestra-override`.
4. **`_ensure_git` targets `workspace_root`:** covered.
5. **All git helpers take `workspace_root`:** partially covered; misses several current git helpers.
6. **Cross-cutting `changed_files` detection:** too weak. Stage 3 says all operations target root, but no task requires `_changed_files`/code-edit changed-files output to preserve `packages/<name>/...` paths and detect changes spanning package directories.
7. **`.mcloop/` writes target `scope_root`:** partially covered; misses several `.mcloop` paths.
8. **Everything log writes target `workspace_root`:** partially covered; lacks schema/config precondition and exact `ledger_config.py` behavior.
9. **`CLAUDE.md` context resolver:** missing.

Current `project_dir` references not adequately touched by the plan:

| File | Judgment |
|---|---|
| `mcloop/main.py` | **Missed task.** The core `run_loop` session setup still derives `project_dir`, `log_dir`, `BUGS.md`, lifecycle state, checks, dependency validation, pending cleanup, reviewer config, ledger settings, task execution, builds, audit cycle, wrapper reinjection, and summaries from that variable. The plan mentions CLI and subcommand dispatch but not the main run path as a coherent refactor. |
| `mcloop/runner.py` | **Missed task.** `run_task` and specialized run helpers use `project_dir` as subprocess cwd and session preflight cwd. This is the session-setup call path the architecture requires to distinguish `execution_cwd` from `workspace_root`. |
| `mcloop/code_edit.py` | **Missed task.** Backend selection, project-local orchestra config lookup, direct backend cwd, orchestra `project_dir` input, and changed-files detection all use `project_dir`. These must be mapped deliberately, likely `scope_root` for config/state and `execution_cwd` for session cwd, with git detection rooted at `workspace_root`. |
| `mcloop/git_ops.py` | **Missed partial task.** The plan covers only some helpers. `_has_meaningful_changes`, `_has_uncommitted_changes`, `_get_diff`, `_worktree_status`, `_committed_files`, `_get_committed_diff`, `_snapshot_worktree`, and `_get_git_hash` also need `workspace_root`. |
| `mcloop/checks.py`, `mcloop/test_runner.py`, `mcloop/targeted.py`, `mcloop/dep_validator.py`, `mcloop/conftest_guard.py`, `mcloop/pytest_optimizations.py` | **Legitimate execution-cwd uses, but still unaddressed.** These should probably continue to operate on package execution context, not workspace root. The plan needs an explicit audit item saying these are converted to `execution_cwd` or intentionally remain path parameters named accordingly. |
| `mcloop/output.py` | **Missed task.** `NOTES.md` snapshot/update and "To run" detection should be scope/execution aware. |
| `mcloop/claude_md_sync.py`, `mcloop/claude_md_check.py` | **Missed task.** Pending sync state is under `.mcloop`; NOTES writes are scope state; git diffs/committed files must use `workspace_root`; the architecture's CLAUDE context resolver is absent. |
| `mcloop/config.py`, `mcloop/review_integration.py`, `mcloop/reviewer.py` | **Missed or needs explicit classification.** These read/write project-local `.mcloop` reviewer state and inspect project files. Decide `scope_root` for state and `workspace_root` or `execution_cwd` for git/file inspection as appropriate. |
| `mcloop/errors.py`, `mcloop/wrap.py` | **Partially covered.** Stage 7 covers wrap, but main's auto-wrap/reinject paths and errors clearing also need conversion. |
| `mcloop/orchestra_override.py`, `mcloop/install_cmd.py`, `mcloop/main.py` ack command | **Missed partial task.** Project-local orchestra override and ack files should be scoped, but global install artifacts stay global. |
| `mcloop/ledger_pause.py` | **Missed task.** Auto-reauthor passes `project_dir` into duplo. That may need `scope_root`, `workspace_root`, or a future duplo workspace context; it cannot remain unanalyzed. |
| `mcloop/investigator.py`, `mcloop/investigate_cmd.py` | **Partially covered.** Investigation command is covered, but helpers that gather bug context from project logs/state need scope-aware paths. |
| `mcloop/audit.py`, `mcloop/sync_cmd.py`, `mcloop/maintain.py`, `mcloop/idea_cmd.py`, `mcloop/run_summary.py`, `mcloop/lifecycle.py`, `mcloop/ledger_emit.py`, `mcloop/ledger_config.py` | **Covered in name, but require the expanded path mapping above.** |

## Section D - Testability

Stage 1 tests cover the intended guard, but they should assert the exact ancestor chosen and include a repo root that legitimately contains `packages/` while being the actual project. Otherwise the guard may block valid standalone projects.

Stage 2 tests are too weak unless `plan_path` override includes the compatibility case where cwd differs from `plan_path.parent`. That is the current `--file` behavior and must not regress. Tests should also assert that ambiguous explicit scope/cwd combinations fail loudly.

Stage 3 tests need to cover every git helper, not just the named six. The consolidated-layout test must assert that changed files under `packages/mcloop/...` are detected with repo-relative paths and that no helper checks for `scope_root/.git`.

Stage 4 tests should assert all `.mcloop` state files, not only run summaries/lifecycle/maintain log: `active-pid`, `interrupted.json`, `eliminated.json`, pending sync state, errors, wrapper canonical sources, reviewer state, and any pending directory cleanup.

Stage 5 tests should exercise `ledger_config.py` and `open_mcloop_storage`, not just `ledger_emit.py`. A test should show that a package-scoped run finds/writes the ledger at `workspace_root` and still reads the correct scoped plan path. If the schema migration is out of scope, tests must assert the current code fails closed rather than silently emitting scope-less events.

Stage 6 CLI tests should include: `--file` as an alias for `--plan-path`; both flags together; explicit plan path outside cwd in a standalone repo; `--scope` without `--workspace`; and conflicts between `--workspace`, `--scope`, and `--plan-path`.

Stage 7 integration tests are useful only if they mock all LLM/subprocess/global-install behavior. They must include `uninstall` and `ack-orchestra-override`, and for `install` must distinguish global hook writes from scoped project-state reads/writes.

Stage 8 compatibility-mode tests are directionally right but not load-bearing unless they compare specific path decisions, not merely "a run succeeded." The test should capture subprocess cwd, git cwd, ledger dir, log dir, state dir, plan path, BUGS path, NOTES path, and changed-files output. The consolidated test must assert both no nested `.git` and all git subprocesses used `workspace_root`.

## Section E - [RULEDOUT] Adequacy

The four existing top-level `[RULEDOUT]` entries are necessary but not sufficient. They prevent the most obvious nested-git and silent-fallback mistakes, but they do not prevent an agent from implementing only the easy helper refactors while leaving session setup and context assembly on `project_dir`.

Add these top-level entries before execution:

```text
[RULEDOUT] Do not derive standalone scope from cwd when an explicit --file or --plan-path points at a different PLAN.md. Current McLoop's compatibility behavior is plan-parent based. The new resolver must preserve that behavior or fail loudly on ambiguity.

[RULEDOUT] Do not leave any current project_dir reference unaudited. Every surviving path parameter must be renamed or documented as one of workspace_root, scope_root, execution_cwd, state_root, or plan_path, and tests must assert the chosen mapping.

[RULEDOUT] Do not stop after adapting git operations and subcommands. The main run loop, runner/session setup, code_edit backend boundary, checks/build execution, logs, NOTES/CLAUDE sync, reviewer state, and dependency preflight are part of the workspace-context migration.

[RULEDOUT] Do not omit the CLAUDE.md task-context resolver. The architecture's McLoop implications require root-plus-package context assembly with precedence classes; relying on cwd/session defaults is the old behavior.

[RULEDOUT] Do not treat ledger relocation as only a directory change. If event schema/config support for scope is not implemented in this plan, McLoop must fail closed or explicitly defer ledger emission rather than writing ambiguous workspace-root events.
```

## Section F - Readiness Verdict

**GO WITH SPECIFIC CORRECTIONS.** Do not hand the plan to McLoop until these corrections are made:

1. Add a stage, before git refactoring, that constructs `WorkspaceContext` in `main.py`/`run_loop` and preserves current `--file` semantics.
2. Fix resolver semantics for explicit `plan_path` in standalone repos.
3. Move CLI/context construction earlier than Stage 3, or merge it into Stage 3.
4. Expand the git stage to include every git helper in `git_ops.py`.
5. Add an explicit main-run/session stage covering `runner.py`, `code_edit.py`, checks/build/dependency preflight, logs, reviewer state, NOTES/CLAUDE sync, output, and auto-wrap/reinject.
6. Add the missing `CLAUDE.md` context resolver stage from architecture item 9.
7. Expand state-path work to all `.mcloop` files and current `logs/` behavior, not just run summaries/lifecycle/maintain.
8. Clarify ledger schema/config preconditions and test `ledger_config.py`, not only `ledger_emit.py`.
9. Add `uninstall` and `ack-orchestra-override` to subcommand dispatch coverage.
10. Strengthen Stage 8 tests so they assert concrete path/cwd decisions under mocked execution rather than merely running a broad cycle.

With those corrections, the plan is executable and aligned with the bob workspace consolidation. Without them, McLoop can still run package-scoped sessions with package-local git assumptions and no layered CLAUDE context, which is exactly the class of consolidation failure this work is supposed to prevent.
