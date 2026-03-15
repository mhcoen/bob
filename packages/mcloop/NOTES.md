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

## Hypotheses

## Eliminated
