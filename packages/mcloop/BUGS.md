<!-- bob-plan-format: 1 -->

## Bugs

- [x] T-000001: In `mcloop`'s AUTO run_cli handler, when the command to run is a path ending in `.sh`, invoke it as `bash <path>` (with any trailing arguments preserved) rather than executing the path directly, so a non-executable (mode 644) script runs successfully instead of failing with exit 126. Leave non-`.sh` commands invoked exactly as they are today. [fix: "AUTO run_cli invokes .sh scripts via bash, not direct execution"] <!-- completed_at: 2026-06-05T04:51:26Z -->
- [ ] T-000002: Add a regression test: an `[AUTO:run_cli]` task pointing at a mode-644 (non-executable) `.sh` script runs successfully (exit 0, not 126); and a non-`.sh` command is still invoked directly (not bash-wrapped). [fix: "regression: 644 .sh runs via bash; non-.sh unchanged"]
