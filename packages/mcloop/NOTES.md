# Notes

## Observations

### [14.3] [T-000386] verify adapter treats an empty changed-set as fail-closed (2026-06-01)
`mcloop/verify_cmd.run_verify` distinguishes three outcomes from
`git_ops._changed_files_since`: `None` (cannot resolve — empty baseline,
no repo, or git error), `[]` (baseline resolves but nothing changed), and
a non-empty list. Both `None` and `[]` exit non-zero (`EXIT_FAIL_CLOSED`)
and never reach `run_checks`. The `[]` case is a deliberate design choice:
calling `run_checks(project_dir, changed_files=[])` would skip the test and
lint commands (no targeted tests, no changed .py) and return a vacuous
`passed=True`, which the task explicitly forbids ("fail closed rather than
... an empty pass"). So an in-session adapter run with no detectable edits
is reported as a failure, not a silent green. If a legitimate
zero-change verification is ever needed, that branch is the single place to
relax.

### [14.2] [T-000385] Signal predicate counts only passed+failed (2026-06-01)
`pytest_signal_verdict` in `mcloop/pytest_signal.py` defines valid signal as
`passed + failed >= 1`, matching the task's literal wording ("at least one
test executed to a pass or fail outcome"). This means a run that produced
*only* xfailed/xpassed outcomes (tests genuinely ran, just as
expected-fail / unexpected-pass) is currently judged as no-signal and would
fail `run_checks`. Such pure xfail/xpass runs are rare in practice and were
not among the four required invalid cases, so the simpler literal predicate
was chosen. If this ever bites, fold xfailed/xpassed into the "executed"
count — the structured counts are already parsed and available.

### [76.2] Codex CLI flag change (2026-03-14)
Codex CLI no longer accepts `--ask-for-approval never --sandbox workspace-write`.
The replacement is `--full-auto` (convenience alias for `-a on-request, --sandbox workspace-write`).
Updated `_build_command` in runner.py accordingly.

### [76.2] Codex panics inside Claude Code sandbox (2026-03-14)
Running `codex exec` inside Claude Code's sandbox causes a Rust panic in
`system-configuration-0.6.1/src/dynamic_store.rs:154` ("Attempted to create
a NULL object"). This happens even when run directly (not via mcloop).
The integration test `test_real_codex_creates_file_and_commits` is correctly
gated behind `MCLOOP_INTEGRATION=1` so it won't affect normal test runs,
but it cannot pass until Codex is run outside the sandbox or the Codex bug
is fixed.

### [7.3.1-7.3.3] Maintain prompt had conflicting check instructions (2026-04-09)
`_build_maintain_prompt` embedded its own CHECK COMMANDS section while
`run_task` → `_build_normal_prompt` added "ABSOLUTELY FORBIDDEN: do not
run any tests". These conflicting instructions could confuse the session.
Fixed by removing embedded check commands from the maintain prompt and
passing `check_commands` to `run_task` properly, so `_build_shared_parts`
handles it.

### [7.3.1-7.3.3] mcloop maintain cannot connect to API from spawned session (2026-04-09)
Both attempts to run `mcloop maintain` failed with `FailedToOpenSocket`
after exhausting all 10 retries. The spawned Claude Code subprocess
cannot reach the API. This is an infrastructure issue — the maintain
mechanism itself parsed invariants, built prompts, and handled failures
correctly. Live verification blocked until API connectivity is resolved.

### [2] shell=True orphan fix only covers run_cli (2026-04-17)
`launch()` now passes `start_new_session=True` so every shell-wrapped
child is its own process-group leader, and `run_cli`'s hang/timeout
paths use the new `kill_process_group` helper (SIGTERM→SIGKILL on the
group). Other callers of `kill(pid)` in the codebase (notably
`run_gui`'s kill_on_return path and `lifecycle.cleanup_orphan_processes`)
still target single PIDs. Those paths either already resolve the true
app PID via pgrep or do their own targeted verification, so they were
left alone to keep this fix minimal, but anything that launches via
`launch()` and kills via single-PID `kill()` could leak orphans the
same way. Audit those call sites if a similar bug recurs.

Reproduced the original bug with `sleep 120 & echo $! > pidfile; wait`
launched via `shell=True` with no `start_new_session`: killing only the
shell pid leaves the `sleep` child running. With the fix, killpg on the
group takes out both. Reproduction script kept at
`/tmp/claude/verify_orphan_fix.py` for reference (not committed).

### [3] Interrupted skip now targets the active split-plan file (2026-04-17)
`_check_interrupted` previously called `mark_failed(checklist_path, t)` with
checklist_path = master PLAN.md. Under the split-plan design the loop only
reads CURRENT_PLAN.md and BUGS.md, so the [!] landed in a file the loop no
longer consults and the "skipped" task was retried on the next run.

Fix: added an `active_paths` parameter (priority-ordered: BUGS.md,
CURRENT_PLAN.md, PLAN.md) and the skip/describe branches now mutate the
first path in that list that contains the task as unchecked. The fallback
to `[checklist_path]` preserves pre-split-plan test behavior. run_loop
filters active_paths to existing files before passing; on a fresh clone
with only PLAN.md this correctly degrades to master-only.

Edge case left intentional: if the task text matches in multiple split
files (e.g. duplicated between BUGS.md and CURRENT_PLAN.md), only the
first unchecked hit is marked. The bug-priority ordering matches how
find_next picks tasks in run_loop, so behavior stays consistent.

### [12.2] [T-000380] resolve_workspace_context edge case: consolidated root scope with cwd outside workspace_root (2026-05-22)
Rule (5) says ``execution_cwd = cwd`` in the consolidated case. When
``plan_path`` is explicit and points at the workspace root itself
(scope resolves to ``"root"``) but ``cwd`` is somewhere else (e.g.
``/tmp``), the resolver constructs a ``WorkspaceContext`` with
``workspace_root == scope_root != execution_cwd``, which trips the
compatibility-mode invariant in ``__post_init__`` and surfaces as an
``AssertionError`` rather than a ``WorkspaceResolutionError``. Rule (3)
does not catch this because the ambiguity check only fires when
``cwd`` is inside a *different* workspace, not when it is in no
workspace at all. T-000381's listed cases do not exercise this path
(its consolidated root case uses ``cwd == workspace_root``), so the
behavior is accepted as-is for now. If a future stage needs a
friendlier error here, the resolver should either coerce
``execution_cwd`` to ``workspace_root`` when ``scope == "root"`` in
the consolidated case or raise a structured error pre-construction.

## Hypotheses

## Eliminated

6c95f88: Parallelized check execution to improve performance by running independent commands concurrently. Updated tests to handle non-deterministic execution order and added a concurrency test to verify parallel behavior.

756c468: Re-enabled the CHECK COMMANDS block in the runner prompt generation, which provides mandatory check instructions to the inner Claude when check_commands are supplied. Updated corresponding tests to verify the block appears when check_commands are provided and is omitted when they are not. This restores the ability for the inner session to run checks and catch failures itself.

a05a67d: The CLAUDE.md sync process was moved to a background thread to prevent blocking the main loop during LLM calls. The sync function now returns a thread reference instead of waiting for completion, and error handling was updated to log failures without crashing. Tests were added to verify non-blocking behavior and proper exception handling in the background thread.

02dec40: Added mypy type checking support. The tool now automatically runs 'mypy .' if a project contains either a [tool.mypy] section in pyproject.toml or a mypy.ini file. This ensures type checking is included in the validation pipeline alongside existing ruff and pytest commands.

e60af39: Improved error handling for screencapture failures and bug verification. Fixed bug filtering to use exact title matches, preventing unrelated bugs from being dropped. Enhanced checklist marker clearing to avoid corrupting prose. Added better timeout handling and noqa comment detection for style checks. Made CLAUDE.md sync wait for completion to prevent race conditions. Improved worktree detection to avoid false positives. Enhanced output buffering to preserve both head and tail of long sessions. Fixed crash handler injection for Swift apps without an init(). Updated tests to cover new edge cases.

cba4873: Changed how user-reported failures are recorded in BUGS.md: instead of flattening and truncating the observation, it now preserves the full multi-line observation verbatim inside a fenced code block. Updated tests to verify the new behavior and removed unnecessary line breaks in test data strings.

5e6fe77: Added automatic archiving of completed bug reports. When bug entries are marked as done, they are now moved to a separate "BUGS-resolved.md" file instead of being deleted. This preserves historical resolution records while keeping the active bug queue concise. The resolved file is created only when there are done bugs to archive.

e8df686: Added safety check to refuse git init inside a uv workspace package subdirectory, preventing nested repository creation that would break cross-package operations. Updated README to clarify phase boundary behavior and exit notifications for stop flags.

9b9ae06: Added a new WorkspaceContext class to manage workspace and scope adaptation during migration. It enforces a compatibility-mode invariant for standalone repo runs, ensuring workspace_root, scope_root, and execution_cwd are identical when scope is "root". Includes comprehensive tests for the dataclass behavior and invariant validation.

b751f8f: Fixed an edge case in workspace resolution where specifying a plan at the workspace root while the current directory is outside the workspace would cause an assertion error. Added structured error handling with WorkspaceResolutionError to provide clearer diagnostics instead of crashing.

5e92f65: Updated git initialization to walk up the directory tree and use an existing parent repository if found, preventing nested git repos in consolidated workspaces. The existing guard against uv workspace packages remains as a defense-in-depth measure. Added corresponding tests for consolidated layouts and worktree scenarios.

733d88e: Updated git helpers to support consolidated workspace layouts by ensuring all file paths are returned relative to the current working directory. Added `--relative` flags to git diff commands and adjusted `_worktree_status` to strip workspace prefixes, maintaining consistent package-relative paths across functions. Added comprehensive tests for both standalone and consolidated workspace scenarios.
