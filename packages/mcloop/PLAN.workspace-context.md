# McLoop adaptation for the consolidated bob workspace

This plan adapts McLoop so it operates correctly when the four sibling
repos (mcloop, duplo, orchestra, bob-tools) are consolidated into one
bob workspace as `packages/<name>/` subdirectories. The adaptation
runs in **compatibility mode**: every change must leave existing
single-repo behavior provably unchanged. The proof is that all
existing tests pass throughout, plus new tests that exercise the
single-repo case explicitly against the new code paths.

The load-bearing primitive introduced by this plan is the
`WorkspaceContext` object, a small dataclass with five fields:
`workspace_root` (the git root and the home of the everything log),
`scope` (either the literal string `"root"` or a package name),
`scope_root` (the directory holding the scope's state files —
`PLAN.md`, `BUGS.md`, the package `CLAUDE.md`), `execution_cwd`
(where tests, builds, and check commands run), and `plan_path` (the
specific `PLAN.md` being advanced this run). On a standalone repo,
`workspace_root == scope_root == execution_cwd` and `scope == "root"`;
this is the compatibility-mode invariant.

The reason this matters: McLoop today derives `project_dir` from the
PLAN file's parent and treats it as the unified "this is where
everything happens" directory. It writes logs, bugs, checks,
`.mcloop/`, ledger config, git operations, and run summaries against
`project_dir`. Crucially, `_ensure_git` in `git_ops.py` initializes a
new git repo if `project_dir/.git` is absent. Post-consolidation,
running mcloop from `packages/orchestra/` would create a nested git
repo inside the bob workspace — exactly the failure mode this
adaptation prevents.

The work is not just adapting git operations and subcommand dispatch.
The core run loop (`run_loop` in `main.py`), the task runner
(`runner.py`), the code-edit backend (`code_edit.py`), checks, builds,
logging, NOTES sync, CLAUDE sync, the reviewer, the dependency
preflight, and the auto-wrap path all use `project_dir` today. Every
one of these must take a `WorkspaceContext` and route paths to the
appropriate field (`workspace_root` for git, `scope_root` for state,
`execution_cwd` for tests and builds). Leaving any of these on
`project_dir` would mean package-scoped runs operate through the
old model and the consolidation is incomplete.

Python 3.11+. Ruff for linting, pytest for tests. Each task must
leave the repo in a passing state: `ruff check .` and `pytest` must
both pass before a commit is made. Prefer small, focused changes per
task. Write unit tests for new functionality. New tests must
explicitly exercise the single-repo compatibility case so behavioral
parity is provable, not assumed.

Top-level constraints that apply to every task in this plan:

[RULEDOUT] Do not modify `_ensure_git` to create a git repository in
any directory other than the resolved `workspace_root`. The
consolidation failure mode this entire plan prevents is the creation
of nested `.git` directories inside `packages/<name>/`. If a task
makes `_ensure_git` look at any path other than `workspace_root`,
the task is wrong.

[RULEDOUT] Do not introduce a `project_dir`-vs-`workspace_root`
split where some call sites take the new abstraction and others
still take `project_dir`. The adaptation must be coherent at every
intermediate state. If a call site receives a `WorkspaceContext`,
its callees that need workspace-rooted paths must receive a
`WorkspaceContext` (or the appropriate field) too.

[RULEDOUT] Do not silently fall back to old behavior when
`WorkspaceContext` resolution fails or returns ambiguous values.
Silent fallback would mask consolidation bugs. Failure must be
loud, structured, and refuse to proceed.

[RULEDOUT] Do not break the compatibility-mode invariant. On a
standalone repo, `workspace_root == scope_root == execution_cwd`
must hold. If a task introduces a code path that violates this on a
standalone layout, the task is wrong.

[RULEDOUT] Do not derive standalone scope from cwd when an explicit
`--file` or `--plan-path` points at a different `PLAN.md`. Current
McLoop's compatibility behavior is plan-parent based: `mcloop --file
/other/repo/PLAN.md` resolves to `/other/repo/`. The new resolver
must preserve that behavior. When cwd is inside a workspace and
plan_path is outside it (or vice versa), the resolver must fail
loudly rather than guess.

[RULEDOUT] Do not leave any current `project_dir` reference
unaudited. Every surviving path parameter must be renamed or
explicitly documented as one of `workspace_root`, `scope_root`,
`execution_cwd`, `state_root` (= `scope_root/.mcloop`), or
`plan_path`. Tests must assert the chosen mapping for every
adapted call site.

[RULEDOUT] Do not stop after adapting git operations and
subcommands. The main run loop (`run_loop` in `main.py`), the
task runner (`runner.py`), the code-edit backend (`code_edit.py`),
checks/build execution, logs, NOTES sync, CLAUDE sync, reviewer
state, dependency preflight, and auto-wrap/reinject are part of
this migration. Stopping after the easy refactors would leave
McLoop's session setup running on the old `project_dir` model.

[RULEDOUT] Do not omit the `CLAUDE.md` task-context resolver. The
architecture's McLoop implications require root-plus-package
context assembly with explicit precedence classes. Relying on
the Claude Code session's own ancestor search for CLAUDE.md is
the old behavior and is not sufficient for the consolidated layout.

[RULEDOUT] Do not treat the ledger relocation as only a directory
change. The everything-log schema migration (adding `scope`,
`plan_path`, structured `task_id`, `parent_task_id`, and
`failure_record_id` fields) lives in `bob-tools` and is explicitly
out of scope for this McLoop plan. If those fields are not
available when McLoop emits ledger events from a package-scoped
run, McLoop must fail closed (refuse to emit) rather than write
ambiguous workspace-root events. The schema readiness is a
precondition for the ledger-relocation tasks; if it has not landed,
those tasks must surface the precondition and stop.

## Stage 1: Defensive guard against nested git repo creation

This stage lands an immediate protective check that makes the
nested-repo failure mode loud rather than silent. It does not
introduce `WorkspaceContext` yet — it is a narrow defensive change
that buys time and protects against the failure mode even if the
rest of this plan stalls. The guard is retired in Stage 4 once
`_ensure_git` becomes WorkspaceContext-aware.

- [ ] Add a `_refuse_nested_init` check at the top of `_ensure_git`
      in `mcloop/git_ops.py`. The guard walks upward from
      `project_dir` looking for a `pyproject.toml` whose contents
      contain `[tool.uv.workspace]`. If such a file is found at
      any strict ancestor of `project_dir`, the guard refuses to
      proceed with `git init`. The guard does NOT use ad-hoc
      patterns like "ancestor named `packages`" — the workspace
      pyproject declaration is the only authoritative signal that
      `project_dir` is inside a bob-style monorepo. On refusal,
      the function raises a structured error naming the workspace
      root and instructing the user to run mcloop from the
      workspace root instead of the package subdirectory. The
      function exits 1 on this refusal, as it does on git-init
      failure today.
- [ ] Unit tests in `tests/test_git_ops.py` covering the new
      guard. Required cases:
      - Standalone repo with no `.git` and no workspace pyproject
        in any ancestor: proceeds normally, creates a new repo.
      - Standalone repo with `.git`: returns early, does not call
        `git init`.
      - Workspace layout with `bob/pyproject.toml` declaring
        `[tool.uv.workspace]`, `bob/.git` present, and a
        `packages/orchestra/` directory without `.git`: the
        guard raises the structured error and does not create a
        nested `.git` in `packages/orchestra/`.
      - A standalone repo that contains a `packages/` directory
        for its own reasons (not bob-style) and has no workspace
        pyproject: the guard does NOT block. This is the
        false-positive case the guard's pyproject-based signal is
        designed to avoid.
      - A pyproject.toml at an ancestor that does NOT declare
        `[tool.uv.workspace]`: the guard does NOT block. The
        signal is specifically the workspace declaration, not the
        pyproject presence.

## Stage 2: WorkspaceContext primitive

This stage introduces the abstraction without changing any existing
behavior. Nothing in McLoop's call paths uses `WorkspaceContext`
yet. The resolver is exercised by tests only.

- [ ] Create `mcloop/workspace_context.py` defining the
      `WorkspaceContext` dataclass with five fields:
      `workspace_root: Path`, `scope: str`, `scope_root: Path`,
      `execution_cwd: Path`, `plan_path: Path`. The dataclass is
      frozen and has a `__post_init__` that asserts the
      compatibility-mode invariant when `scope == "root"`
      (`workspace_root == scope_root == execution_cwd`).
- [ ] Add `resolve_workspace_context(cwd: Path, plan_path: Path |
      None, *, workspace_override: Path | None = None, scope_override:
      str | None = None) -> WorkspaceContext` to the same module.
      Resolution rules, in order:
      1. If `workspace_override` is given, use it as
         `workspace_root` directly. Otherwise walk upward from the
         **anchor** (defined below) looking for a directory that
         contains both a `.git` directory and a `pyproject.toml`
         declaring `[tool.uv.workspace]`. If no such ancestor is
         found, the standalone case applies.
      2. The **anchor** for the upward walk is `plan_path.parent`
         when `plan_path` is given explicitly, otherwise `cwd`.
         This preserves current McLoop behavior where
         `mcloop --file /other/repo/PLAN.md` operates against
         `/other/repo/` regardless of where it was invoked.
      3. **Ambiguity check.** If `plan_path` is explicit and `cwd`
         is inside a workspace different from the one containing
         `plan_path.parent`, the resolver raises a structured
         error rather than picking one. Same if `workspace_override`
         disagrees with the ancestor walk's result. Failure is
         loud, never silent.
      4. **Standalone case** (no workspace ancestor found): set
         `workspace_root = scope_root = execution_cwd =
         plan_path.parent` if plan_path is given, else `cwd`.
         `scope = "root"`. `plan_path` defaults to
         `scope_root/PLAN.md` if not given.
      5. **Consolidated case, workspace ancestor found**: if the
         anchor is `workspace_root` itself, `scope = "root"`,
         `scope_root = workspace_root`. If the anchor is
         `workspace_root/packages/<name>/...`, `scope = "<name>"`,
         `scope_root = workspace_root/packages/<name>`. Other
         layouts are rejected with a structured error.
         `execution_cwd = cwd`. `plan_path` defaults to
         `scope_root/PLAN.md` if not given.
      6. `scope_override`, if given, must match the resolved scope
         or the resolver raises a structured error.
- [ ] Unit tests in `tests/test_workspace_context.py` covering
      every resolution path:
      - Standalone, cwd-anchored (no plan_path).
      - Standalone, plan_path-anchored (explicit `--file`-style
        invocation).
      - Standalone, cwd and plan_path.parent disagree, no
        workspace: standalone case still applies because no
        workspace exists. Resolver picks `plan_path.parent` (the
        explicit input wins).
      - Workspace ancestor exists; cwd is at workspace root;
        plan_path defaults: `scope = "root"`.
      - Workspace ancestor exists; cwd is inside
        `packages/orchestra/`; plan_path defaults:
        `scope = "orchestra"`, `scope_root` matches.
      - Workspace ancestor exists; plan_path explicit and inside a
        DIFFERENT workspace: resolver raises structured error.
      - Workspace ancestor exists; `--scope` override matches
        resolved scope: accepted.
      - Workspace ancestor exists; `--scope` override disagrees
        with resolved scope: structured error.
      - `--workspace` override matches: accepted.
      - `--workspace` override disagrees with ancestor walk:
        structured error.
      - Compatibility-mode invariant is asserted in every
        standalone case.

## Stage 3: CLI surface and context construction at run-loop entry

This stage extends the CLI parser, constructs the `WorkspaceContext`
at the top of `run_loop`, and threads it through immediately. It
runs before the git stage because every later stage needs a
`WorkspaceContext` to receive. Stage 3 is invasive but does not
break compatibility: every internal caller of `project_dir` continues
to work because it derives `project_dir` from `wsctx.scope_root` at
the top of the function.

- [ ] Add `--workspace`, `--scope`, and `--plan-path` flags to
      `mcloop/main.py`'s top-level parser (around line 2324–2396).
      `--file` becomes an alias for `--plan-path` (existing
      semantics preserved). Help text is updated.
- [ ] Construct `WorkspaceContext` in `_main()` (in `mcloop/main.py`),
      where the parsed `args` object is in scope. The construction
      reads `args.workspace`, `args.scope`, and `args.plan_path` (or
      `args.file` as the back-compat alias) and calls
      `resolve_workspace_context(cwd=Path.cwd(), plan_path=...,
      workspace_override=..., scope_override=...)`. The resolved
      `WorkspaceContext` is then passed into `run_loop` as a new
      keyword argument (`wsctx: WorkspaceContext`). `run_loop`'s
      existing positional `checklist_path` parameter is preserved
      for compatibility (its value matches `wsctx.plan_path`), but
      new code reads paths from `wsctx`.
- [ ] At the top of `run_loop` (around line 835–840 where
      `project_dir` is currently derived from `checklist_path.parent`),
      derive the legacy `project_dir` from `wsctx.scope_root` instead.
      Other derivations in that block (`log_dir`, `plan_path`,
      `bugs_path`) likewise come from `wsctx`. The `project_dir`
      variable is preserved as a compatibility shim during this
      stage so existing internal code paths continue to work; it is
      removed in Stage 5 once the run-loop migration completes.
- [ ] Update conflict behavior: `--file` and `--plan-path` together
      with different values is a structured error.
      `--scope mcloop` without `--workspace` walks upward from cwd
      and requires the resolved scope to match; if it doesn't,
      structured error. Combinations are tested explicitly.
- [ ] In `_main()`'s subcommand dispatch block (around
      `mcloop/main.py:436-485`), construct `WorkspaceContext` once
      (the same construction step as for the bare-loop path) and
      pass it as a new keyword argument to each subcommand entry
      point (`_cmd_wrap`, `_cmd_install`, `_cmd_uninstall`,
      `_cmd_idea`, `_cmd_ack_orchestra_override`, `_cmd_maintain`,
      `_cmd_sync`, `_cmd_audit`, `_cmd_investigate`). Each subcommand
      function accepts the new argument and derives its existing
      `project_dir` parameter from `wsctx.scope_root` as a
      compatibility shim during this stage. Full subcommand refactor
      against `WorkspaceContext` fields lives in Stage 8.
- [ ] CLI tests in `tests/test_main.py`:
      - Bare `mcloop` from a standalone repo: WorkspaceContext is
        constructed and compatibility invariant holds.
      - `mcloop --file other/PLAN.md` from cwd unrelated to
        `other/`: resolver uses `other/` as the anchor, behavior
        identical to current `--file`.
      - `mcloop --workspace /path/to/bob --scope mcloop` resolves
        without consulting cwd.
      - `mcloop --workspace /path/to/bob --scope orchestra
        --plan-path /path/to/bob/packages/orchestra/PLAN.md`
        succeeds.
      - `mcloop --workspace /path/to/bob --plan-path /elsewhere/PLAN.md`
        (workspace and plan disagree): structured error.
      - `mcloop --file A --plan-path B` (both, different):
        structured error.

## Stage 4: Thread WorkspaceContext through git operations

This stage replaces `project_dir` with `WorkspaceContext` in all of
`mcloop/git_ops.py`. Every git subprocess call uses
`wsctx.workspace_root` as `cwd`. The defensive guard from Stage 1 is
retired because `_ensure_git` now structurally cannot target a
package subdirectory.

- [ ] Refactor `_ensure_git` to take a `WorkspaceContext`, check
      `wsctx.workspace_root / ".git"`, and initialize at
      `wsctx.workspace_root` if absent. The `_refuse_nested_init`
      guard from Stage 1 is removed in this task — the new
      signature makes the guard structurally unnecessary.
- [ ] Refactor every git helper in `mcloop/git_ops.py` to take a
      `WorkspaceContext` (or just `workspace_root: Path` for
      helpers that don't need other fields). The complete list:
      `_checkpoint`, `_push_or_die`, `_stage_safe`, `_commit`,
      `_changed_files`, `_has_meaningful_changes`,
      `_has_uncommitted_changes`, `_get_diff`, `_worktree_status`,
      `_committed_files`, `_get_committed_diff`,
      `_snapshot_worktree`, `_get_git_hash`. Every git subprocess
      uses `workspace_root` as `cwd`. Function bodies that previously
      used `project_dir` as the subprocess `cwd` are updated.
- [ ] Update every caller of the refactored functions to pass
      `WorkspaceContext` or `workspace_root` as appropriate. The
      compatibility shim in Stage 3 (`project_dir =
      wsctx.scope_root`) means most call sites can pass `wsctx`
      directly; tasks that previously passed `project_dir` for git
      operations are updated to pass `wsctx.workspace_root` instead.
- [ ] Changed-files detection (`_changed_files`,
      `_committed_files`, `_get_committed_diff`) emits paths
      relative to `workspace_root`. On a consolidated layout, a
      change in `packages/mcloop/foo.py` is reported as
      `packages/mcloop/foo.py`, not as `foo.py`. Tests assert this
      directly.
- [ ] New tests in `tests/test_git_ops.py` covering the
      WorkspaceContext-threaded behavior for every helper:
      - Standalone layout: compatibility invariant holds, every
        git call hits the same directory as the old behavior.
      - Simulated consolidated layout (workspace root with
        `.git`, `packages/orchestra/` without `.git`):
        `_ensure_git` does not create a nested repo; all git
        operations target `workspace_root`; changed-files paths
        are workspace-relative.
      - Existing tests in `tests/test_git_ops.py` continue to pass
        without modification (because the compatibility shim
        preserves their setup).

## Stage 5: Thread WorkspaceContext through the main run loop, runner, and code edit

This is the stage that addresses Codex's load-bearing correction.
`run_loop` in `main.py`, `runner.py`, `code_edit.py`, checks, builds,
the dependency preflight, output, NOTES/CLAUDE sync, the reviewer,
and the auto-wrap/reinject path all currently consume `project_dir`.
Each is migrated to the appropriate `WorkspaceContext` field, with
explicit attention to which field maps to which use.

- [ ] Audit `run_loop` in `mcloop/main.py` end-to-end. Every
      `project_dir` reference is replaced with the appropriate
      `wsctx` field:
      - `log_dir`: derived from `wsctx.scope_root / "logs"` (state
        for the scope).
      - `BUGS.md` path: `wsctx.scope_root / "BUGS.md"`.
      - lifecycle state, run summaries, active-pid: `scope_root`.
      - git operations: `workspace_root`.
      - checks, builds, dependency validation, pytest runs:
        `execution_cwd`.
      - reviewer config, orchestra config lookup: `scope_root`
        (project-local) plus `workspace_root` precedence (when
        applicable).
      - ledger settings, ledger emission: `workspace_root` (see
        Stage 7 precondition).
      - auto-wrap/reinject path and `.mcloop/errors.json` clearing:
        `scope_root`.
      - audit cycle wrapper: passes `wsctx` forward (see Stage 8
        for `audit.py` internals).
      The compatibility shim from Stage 3 (`project_dir =
      wsctx.scope_root`) is removed at the end of this task.
- [ ] Refactor `mcloop/runner.py`. `run_task` and the specialized
      run helpers take `WorkspaceContext`. Subprocess `cwd` for the
      CLI session is `wsctx.execution_cwd`. Session preflight
      (checking that the session produced meaningful changes, that
      logs landed correctly) is rooted at `execution_cwd` for the
      session itself but uses `workspace_root` for any git-based
      change detection.
- [ ] Refactor `mcloop/code_edit.py`. Backend selection (direct vs
      orchestra) reads project-local orchestra config from
      `scope_root` (project-local override) with a precedence rule
      against `workspace_root` if relevant. The direct backend's
      subprocess `cwd` is `execution_cwd`. The orchestra `project_dir`
      input is set to `execution_cwd` so per-edit operations target
      the right directory. Changed-files detection routes through
      the `git_ops` helpers refactored in Stage 4.
- [ ] Refactor `mcloop/checks.py`, `mcloop/test_runner.py`,
      `mcloop/targeted.py`, `mcloop/dep_validator.py`,
      `mcloop/conftest_guard.py`, `mcloop/pytest_optimizations.py`.
      These run subprocesses (`pytest`, `ruff`, build commands) and
      should operate against `execution_cwd`, not `workspace_root`.
      Function signatures that previously took `project_dir` take
      `execution_cwd` (or `WorkspaceContext` if more than one field
      is needed). Argument names are updated explicitly so future
      readers can see the intended scope.
- [ ] Refactor `mcloop/output.py`. NOTES.md snapshot/update and
      "To run" detection operate on `scope_root` for NOTES.md and
      `execution_cwd` for any subprocess invocation.
- [ ] Refactor `mcloop/claude_md_sync.py` and
      `mcloop/claude_md_check.py`. Pending sync state lives in
      `scope_root/.mcloop`; NOTES writes target `scope_root`; git
      diffs and committed-files queries use `workspace_root`.
      The CLAUDE.md task-context resolver (the
      precedence-class-based assembly) is NOT in this stage — it
      lives in Stage 9. This stage preserves current per-package
      CLAUDE.md sync behavior with paths corrected.
- [ ] Refactor `mcloop/config.py`, `mcloop/review_integration.py`,
      `mcloop/reviewer.py`. Project-local reviewer state and
      review-result files live in `scope_root/.mcloop`. Git/file
      inspection routes through `workspace_root` for git and
      `execution_cwd` for the inspected files.
- [ ] Refactor `mcloop/errors.py`. `_check_errors_json` reads
      `.mcloop/errors.json` from `scope_root/.mcloop/errors.json`
      (the crash-handler instrumentation writes there at
      injection time; that injection path is already scope-aware
      via `wrap.py`'s refactor in Stage 8). `_insert_bugs_section`
      mutates `scope_root/BUGS.md` through the planfile API (not
      string replacement). The diagnostic session it spawns via
      `run_diagnostic` (from `runner.py`) executes with
      `execution_cwd` as the subprocess cwd. The auto-wrap
      reinjection path called from `run_loop` reads canonical
      wrapper sources from `scope_root/.mcloop/wrap/` and writes
      injected wrappers into source files under `execution_cwd`.
- [ ] Refactor `mcloop/ledger_pause.py`. The auto-reauthor call
      passes `wsctx` to duplo (or the appropriate fields). Duplo
      itself receives this through whatever API surface it offers;
      McLoop's responsibility ends at handing the right paths
      across the boundary. If duplo's current API only accepts a
      single project directory, this task uses `scope_root` as the
      conservative default and surfaces a follow-up for duplo to
      accept a `WorkspaceContext`-equivalent.
- [ ] Refactor `mcloop/investigator.py` helpers. The bug-context
      gathering reads from `scope_root/.mcloop/` for mcloop task
      logs and from the OS crash-reports directory as before. App
      and run-command detection (`detect_app_type`, `detect_run`
      from `checks.py`) operate against `execution_cwd` because
      they look for the project's build artifacts, entry points,
      and platform markers in the directory holding the app under
      investigation. On a standalone layout `execution_cwd ==
      scope_root` so the detection target is unchanged; on a
      consolidated layout investigating a specific package,
      `execution_cwd` is the package directory and detection
      correctly identifies that package's app type rather than the
      workspace's root profile. The investigation PLAN.md is
      written under `scope_root` for the scope being investigated.
      The investigation git worktree is created relative to
      `workspace_root` per the worktree task in Stage 8.
- [ ] Refactor `mcloop/orchestra_override.py`. Project-local
      override files (`<scope_root>/.orchestra/config.json`) and
      override-acknowledgement files
      (`<scope_root>/.mcloop/orchestra-override-ack`) live in
      `scope_root`.
- [ ] New tests in `tests/test_main_run_loop.py` (or equivalent)
      that exercise the run-loop entry on a standalone fixture and
      a consolidated fixture under mocked LLM/subprocess calls.
      Assertions are concrete: subprocess `cwd`, log directory
      path, run-summary path, active-pid path, ledger directory
      path, plan path, BUGS path, NOTES path, and changed-files
      output. The test does not depend on real model calls.
- [ ] New per-module tests in `tests/test_runner.py`,
      `tests/test_code_edit.py`, `tests/test_checks.py`,
      `tests/test_output.py`, `tests/test_claude_md_sync.py`,
      `tests/test_reviewer.py`. Each test asserts the field-to-path
      mapping for that module on both standalone and consolidated
      fixtures.

## Stage 6: Thread WorkspaceContext through state paths

This stage covers the remaining `.mcloop/` state writes that Stage 5
did not touch (because Stage 5 was focused on the run-loop and
session-setup path). The audit hash, the maintain log, the run-summary
directory, the lifecycle state files, and any pending-state directories
all land at `scope_root/.mcloop/`.

- [ ] Refactor `mcloop/run_summary.py`. Summaries land at
      `scope_root/.mcloop/runs/`; the `latest.json` stable filename
      and timestamped archives both live there. Function signatures
      accept `WorkspaceContext` or `scope_root`. Existing callers
      updated.
- [ ] Refactor `mcloop/lifecycle.py`. All lifecycle state files
      (`active-pid`, `interrupted.json`, `eliminated.json`) live in
      `scope_root/.mcloop/`. Atexit handlers, signal-driven save
      paths, and orphan-session detection (`_kill_orphan_sessions`)
      are all updated coherently. No save path uses `project_dir`
      while another uses `scope_root`.
- [ ] Refactor `mcloop/maintain.py`. Maintain log writes target
      `scope_root/.mcloop/maintain-log.json`. The maintain command
      accepts `WorkspaceContext`; root-level invariants execute
      with `workspace_root` as `execution_cwd`; package-level
      invariants execute with `scope_root` as `execution_cwd`. The
      maintain-log entries record which scope the invariant came
      from. (Note: this assumes per-package MAINTAIN.md exists; in
      the standalone case, only the scope_root MAINTAIN.md is read.)
- [ ] Audit pending-state writes:
      `<scope_root>/.mcloop/pending` and any pending-sync state
      live at `scope_root`. The errors-clearing path that empties
      `.mcloop/errors.json` operates on `scope_root`.
- [ ] New tests in `tests/test_state_paths.py` (or extend the
      per-module tests) asserting every `.mcloop/` artifact lands
      at `scope_root/.mcloop/` on both standalone and consolidated
      fixtures. Required artifacts checked: `active-pid`,
      `interrupted.json`, `eliminated.json`, `runs/latest.json`,
      a timestamped run summary, `maintain-log.json`,
      `audit-report.md` (after Stage 8), `errors.json`, wrapper
      canonical sources in `.mcloop/wrap/`, reviewer state in
      `.mcloop/reviews/`, claude_md_sync pending state.

## Stage 7: Ledger emission relocation, with schema precondition

The everything log lives at `workspace_root` regardless of which
scope is executing, because its purpose is to be the unified audit
trail across all components. Today `mcloop/ledger_emit.py` and
`mcloop/ledger_config.py` derive ledger location from `project_dir`.
They move to `workspace_root`.

**Schema precondition.** The state-file architecture proposal
specifies that the ledger envelope needs additional fields
(`scope`, `plan_path`, structured `task_id`, `parent_task_id`,
`failure_record_id`) to make cross-package queries meaningful.
That schema migration lives in `bob-tools` and is out of scope for
this plan. If it has not landed by the time this stage executes,
the tasks must fail closed (refuse to relocate the ledger and
surface the precondition) rather than write ambiguous workspace-root
events that lack the new fields.

- [ ] Check the `bob-tools` planfile/ledger schema for the
      `task_context` payload extension (`scope`, `plan_path`,
      etc.). If the extension is absent, halt this stage with a
      structured error referencing the schema migration and stop.
      Do not proceed to the next task in this stage.
- [ ] Refactor `mcloop/ledger_emit.py`. The functions in
      `ledger_emit.py:384-513` continue to use `project_dir` for
      git metadata extraction (HEAD, diff stats, parents, branch,
      author, subject) — git metadata reads should target
      `workspace_root` so the metadata is workspace-rooted, not
      package-local. The ledger writes themselves go through
      `default_ledger_dir(workspace_root)` rather than
      `default_ledger_dir(project_dir)`. The function signature
      accepts `WorkspaceContext` and pulls fields as needed.
- [ ] Refactor `mcloop/ledger_config.py`.
      `load_plan_ledger_settings` accepts `WorkspaceContext`. The
      ledger directory defaults to `workspace_root/.duplo/ledger`
      (or whatever the bob-tools-canonical workspace path is —
      verify against bob-tools' current convention before
      hard-coding). The plan path resolution continues to use
      `wsctx.plan_path`, which already accounts for scope.
- [ ] Every event emitted by McLoop carries the `task_context`
      payload populated with `scope`, `plan_path`, `task_id`,
      `phase_id`, `run_id`, `parent_task_id` (if applicable), and
      `failure_record_id` (currently always None until M1 lands).
- [ ] New tests in `tests/test_ledger_emit.py` and
      `tests/test_ledger_config.py`:
      - Standalone repo: ledger lands at the same on-disk path as
        today (because `workspace_root == project_dir` in
        compatibility mode). Schema fields are populated with
        `scope="root"` and `plan_path` matching `wsctx.plan_path`.
      - Consolidated layout, scope=package: ledger lands at
        `workspace_root/.duplo/ledger/`. Events carry
        `scope="<package>"` and the correct `plan_path`.
      - Schema-missing case: stage halts with structured error
        rather than writing ambiguous events.

## Stage 8: Subcommand dispatch (comprehensive)

Every subcommand entry point dispatches with `WorkspaceContext`.
This stage covers the subcommands as published from
`mcloop/main.py:436-485`, including the two Codex flagged that were
omitted from the original plan: `uninstall` and
`ack-orchestra-override`.

- [ ] Refactor `mcloop/idea_cmd.py`. `mcloop idea "..."` appends
      to `wsctx.scope_root / "IDEAS.md"`. On a standalone layout,
      this is unchanged. On a consolidated layout with cwd in
      `packages/orchestra/`, the idea lands in
      `packages/orchestra/IDEAS.md`.
- [ ] Refactor `mcloop/maintain.py` subcommand entry point (the
      `run_maintain` callable). Already covered structurally in
      Stage 6's maintain refactor; this task wires the subcommand
      surface to construct `WorkspaceContext` and pass it through.
- [ ] Refactor `mcloop/install_cmd.py`. `_cmd_install` accepts
      `WorkspaceContext`. Most install artifacts remain global
      (`~/.claude/`, `~/.mcloop/`) and are not moved by this
      refactor. Project-local state — reviewer config in
      `<scope_root>/.mcloop/config.json` and orchestra-override-ack
      in `<scope_root>/.mcloop/orchestra-override-ack` — lives in
      `scope_root`. The split between global and scoped artifacts
      is documented in code comments so future readers see the
      design intent.
- [ ] Refactor `mcloop/main.py`'s `uninstall` dispatch (the
      `_cmd_uninstall` callable in `main.py`). Same global/scoped
      split as install. Global artifacts are removed globally.
      Project-local artifacts under `scope_root/.mcloop/` are
      preserved by default (matches current behavior).
- [ ] Refactor `mcloop/main.py`'s `ack-orchestra-override`
      dispatch. The ack file lives at
      `<scope_root>/.mcloop/orchestra-override-ack`; the override
      file inspected is `<scope_root>/.orchestra/config.json`.
- [ ] Refactor `mcloop/wrap.py`. `mcloop wrap` accepts
      `WorkspaceContext`. Source instrumentation operates under
      `execution_cwd` (the package being wrapped); canonical
      wrapper sources land at `scope_root/.mcloop/wrap/`; the
      project-directory path baked into the wrapper at injection
      time is `execution_cwd` so the crash message points the
      user at the right directory.
- [ ] Refactor `mcloop/sync_cmd.py`. `mcloop sync` reconciles
      `wsctx.plan_path` with the codebase under `execution_cwd`.
      Git operations use `workspace_root`. Mutations to PLAN.md
      go through the planfile API (already true) and are recorded
      in the ledger at `workspace_root`.
- [ ] Refactor `mcloop/audit.py`. Audit-hash file
      (`.mcloop-last-audit`) lives in `scope_root`. Audit-report
      file (`.mcloop/audit-report.md`) lives in
      `scope_root/.mcloop/`. Git operations use `workspace_root`;
      audit session subprocess cwd is `execution_cwd`. The
      `main.py:2560-2579` audit CLI wrapper passes
      `WorkspaceContext` to `audit.py` functions.
- [ ] Refactor `mcloop/investigate_cmd.py`. `_cmd_investigate`
      accepts `WorkspaceContext`. The investigation git worktree
      is created relative to `workspace_root` (so the worktree
      shares the same `.git` as the main repo); the
      investigation PLAN.md lives under `scope_root` for the
      scope being investigated; subprocess cwd for the
      investigation session is `execution_cwd`.
- [ ] Integration tests in `tests/test_subcommand_dispatch.py`:
      simulate a standalone repo and a consolidated layout;
      invoke every subcommand (including `uninstall` and
      `ack-orchestra-override`) against both; assert that the
      standalone invocation produces unchanged behavior and the
      consolidated-layout invocation routes state to
      `scope_root` and git operations to `workspace_root` as
      specified.

## Stage 9: CLAUDE.md task-context resolver (Phase 1)

Per the state-file architecture proposal's McLoop implication
item 9. The resolver assembles `CLAUDE.md` context for the
agent at the start of each task session, with explicit
precedence classes between the workspace-root CLAUDE.md and the
per-package CLAUDE.md.

**Phase 1 scope** (per the architecture proposal): root plus
exactly one package. The default assembly for a package-scoped
task. Phase 2 (multi-package assembly) is not in this plan.

- [ ] Create `mcloop/claude_md_resolver.py`. The resolver's API:
      `assemble_claude_context(wsctx: WorkspaceContext) -> str`.
      Returns the assembled CLAUDE.md content the session should
      receive (or an empty string if no CLAUDE.md is found at
      any layer). The function is structurally pure given the
      filesystem and wsctx as inputs.
- [ ] Define precedence classes. Each section of either
      CLAUDE.md may be tagged (via a structured marker that
      doesn't interfere with markdown readability) with one of:
      `safety` (root-only, no override permitted), `workspace`
      (root-only by default; package-overridable only with
      explicit acknowledgment), `package` (package-specific,
      overrides root entries with the same heading),
      `task` (task-specific overlay; passed through from the
      task envelope, not from CLAUDE.md files). Sections without
      explicit tags are treated as `package`-class.
- [ ] Implement conflict detection. If the root CLAUDE.md and
      the package CLAUDE.md both define content under the same
      heading and the root content is tagged `safety` or
      `workspace` (without acknowledgment), the resolver fails
      closed with a structured error naming the conflict. No
      silent assembly that contradicts the safety/workspace
      class.
- [ ] Implement a token budget check. The assembled context
      must fit within a configurable threshold (defaults
      reasonable for current Claude Code context windows). If
      the budget would be exceeded, the resolver fails closed
      with a structured error rather than producing a
      summarization or truncation. (Summarization is Phase 2
      work and out of scope.)
- [ ] Wire the resolver into `mcloop/runner.py` at the point
      where `run_task` constructs the per-session prompt. The
      assembled CLAUDE.md content is passed into the session's
      initial context. On standalone layouts (no
      `workspace_root` CLAUDE.md), the resolver returns the
      `scope_root/CLAUDE.md` content unchanged, preserving
      current behavior.
- [ ] Tests in `tests/test_claude_md_resolver.py`:
      - Standalone layout, only scope_root has CLAUDE.md:
        resolver returns it unchanged.
      - Standalone layout, no CLAUDE.md anywhere: resolver
        returns empty string.
      - Consolidated layout, both root and package CLAUDE.md
        exist, no conflicts: resolver returns root content
        followed by package content (root precedes package).
      - Consolidated layout, package CLAUDE.md defines a
        section under the same heading as a root `safety`-class
        section: resolver fails closed with structured error.
      - Consolidated layout, package CLAUDE.md overrides a root
        `package`-class section: resolver returns the package
        version of that section.
      - Token-budget overrun: resolver fails closed, error
        message names the threshold and the assembled size.

## Stage 10: Compatibility-mode and consolidated-layout verification

The compatibility-mode invariant is the load-bearing claim of this
entire plan: on a standalone repo, McLoop's behavior is provably
unchanged. This stage adds comprehensive end-to-end parity tests.

- [ ] Add `tests/test_compatibility_mode.py`. The test sets up a
      single-repo fixture (no workspace pyproject, no
      `packages/` directory of significance), runs a complete
      mocked McLoop cycle against it (start, one task with a
      mocked LLM session, the synthetic edit, the commit, the
      check-off, the run summary, the ledger write, the
      reviewer queue), and asserts concrete decisions: subprocess
      `cwd` for the task session; subprocess `cwd` for checks;
      git subprocess `cwd`; ledger directory; `.mcloop/runs/`
      path; `.mcloop/active-pid` path;
      `.mcloop/maintain-log.json` path (after a `mcloop maintain`
      cycle); `.mcloop/audit-report.md` path; `.mcloop-last-audit`
      path; plan path; BUGS path; NOTES path; changed-files output
      format. Every assertion compares against the path the legacy
      `project_dir` model would have produced. Any divergence
      means the compatibility invariant is broken.
- [ ] Add `tests/test_consolidated_layout.py`. The test sets up
      a simulated consolidated layout: a `workspace/` directory
      with `pyproject.toml` declaring `[tool.uv.workspace]`,
      `workspace/.git` present, and `workspace/packages/mcloop/`
      containing a `PLAN.md` but no `.git`. Runs the same mocked
      McLoop cycle against it from cwd =
      `workspace/packages/mcloop/`. Asserts:
      - No nested `.git` was created in
        `workspace/packages/mcloop/`.
      - `.mcloop/` artifacts land at
        `workspace/packages/mcloop/.mcloop/`.
      - Ledger writes land at `workspace/.duplo/ledger/` (or
        whatever the workspace-rooted ledger path resolves to).
      - Git subprocesses all used `workspace/` as their cwd.
      - Check subprocesses (pytest, ruff) used
        `workspace/packages/mcloop/` as their cwd
        (`execution_cwd`).
      - Changed-files output uses
        `packages/mcloop/...`-relative paths.
      - The assembled CLAUDE.md context contains both the
        `workspace/CLAUDE.md` (if present) and
        `workspace/packages/mcloop/CLAUDE.md` (if present), per
        the resolver's precedence rules.
- [ ] Both tests must be hermetic: no real LLM calls, no real
      git remote pushes (use a local-only remote in a tmpdir),
      no real network. Subprocess calls are either mocked or
      directed at hermetic targets.

After Stage 10 passes, McLoop is ready for the bob workspace
consolidation to proceed. The consolidation itself is a separate
piece of work (per the checklist at
`bob/design/repository-consolidation-checklist.md`) and is not in
scope for this plan.
