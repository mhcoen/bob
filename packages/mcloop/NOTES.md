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

## Hypotheses

## Eliminated
