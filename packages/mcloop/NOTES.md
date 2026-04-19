# Notes

## Observations

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

## Hypotheses

## Eliminated

6c95f88: Parallelized check execution to improve performance by running independent commands concurrently. Updated tests to handle non-deterministic execution order and added a concurrency test to verify parallel behavior.

756c468: Re-enabled the CHECK COMMANDS block in the runner prompt generation, which provides mandatory check instructions to the inner Claude when check_commands are supplied. Updated corresponding tests to verify the block appears when check_commands are provided and is omitted when they are not. This restores the ability for the inner session to run checks and catch failures itself.
