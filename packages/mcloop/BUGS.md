<!-- bob-plan-format: 1 -->

## Bugs

- [x] T-000001: Fix `mcloop`'s `parse_auto_task` so that an automated run-cli task (action `run_cli`) executes only the backtick-quoted command extracted from the task text, not the full prose description. If the task text contains a single backtick-delimited command, run exactly that; if it contains none, return a clear failure rather than passing prose to the shell. Do not change parsing for other automated actions. [fix: "run_cli extracts backtick-quoted command from task prose"] <!-- completed_at: 2026-06-05T02:55:25Z -->
- [ ] T-000002: Add tests: an automated run-cli task of the form "Run `<cmd>` to confirm ..." executes exactly `<cmd>` (assert the prose words are not passed to the shell); a run_cli task with no backtick command yields a clear failure, not a 127 from shell-parsing prose. [fix: "regression: run_cli command extraction"]
