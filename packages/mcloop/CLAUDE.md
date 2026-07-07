# Project Manifest

## Plan Layout

mcloop works PLAN.md and BUGS.md directly; the old split-plan design (a CURRENT_PLAN.md extracted per phase) is fully retired and its machinery (`ensure_current_plan`, `transition_phase`, `mark_phase_complete`) no longer exists in the code.

**PLAN.md** is the roadmap: every phase (`## Stage N:` / `## Phase phase_NNN:` headings) with its tasks, in one canonical planfile-format file. The loop reads and checks off tasks through the bob-tools planfile API (`_planfile_compat`), which preserves canonical form on every write. At startup PLAN.md must pass the runtime preflight (`_enforce_canonical_inputs` -> `preflight_runtime_plan`): a cleanly-migratable non-canonical file is auto-migrated in place, and a corrupt one raises with a fix-by-hand diagnostic. BUGS.md is deliberately NOT canonical-gated -- it is a loose bug queue read with a tolerant parse.

**BUGS.md** is a standalone bug backlog with checkbox items. It is treated as bug-only mode by run_loop: when any item is unchecked, only bug tasks are worked, and feature tasks in PLAN.md are blocked until BUGS.md is empty. Reviewer findings and crash diagnostics append to BUGS.md; completed bug tasks are purged from the file. The audit system's structured report file is separate (see below) and does not collide with BUGS.md.

### Phase boundaries

1. The loop selects tasks from BUGS.md (priority) and the current PLAN.md phase until no actionable unchecked items remain in the phase.
2. At the phase boundary it runs the full test suite (unscoped) and the build.
3. On success it advances into the next phase and keeps going by default; `--stop-after-stage` stops at the boundary instead. Per-task checks remain scoped to the files touched by each commit.
4. When no phases remain, the post-loop runs the audit cycle.

### Bugs vs. audit reports

**BUGS.md** holds checklist tasks that the loop must work through. **`.mcloop/audit-report.md`** is the structured prose output written by `_run_audit_fix_cycle` for human review; it is not parsed as a checklist and is not consumed by the loop's task-selection logic.

## Core Modules

**mcloop/__init__.py** - Package marker (empty).

**mcloop/__main__.py** - `python -m mcloop` entry point; delegates to `main.main()`.

**mcloop/app_interact.py** - macOS GUI app interaction via osascript/System Events.

**mcloop/audit.py** - Audit functions: run checks, commit passing changes, report failures. Writes output to `.mcloop/audit-report.md`. Defines `AuditResult` enum (no_bugs, fixed, failed, skipped).

**mcloop/checks.py** - Run a project's test/lint suite. Side-effect-free; `run_autofix` is a separate function. Scopes ruff and pytest to changed files via helpers in `targeted`.

**mcloop/claude_md_check.py** - Freshness check for the project manifest and LLM-driven diff summarization. `check_claude_md_freshness` reads CLAUDE.md only. `auto_update_claude_md` sends the git diff to a cheap LLM and appends the summary to NOTES.md; CLAUDE.md is never written to.

**mcloop/claude_md_sync.py** - Deferred sync glue with a pending queue capped at one entry. Wraps `auto_update_claude_md`, retries on transient failures, halts with a Telegram notification when the cap is exceeded.

**mcloop/config.py** - Reviewer configuration loading from `.mcloop/config.json`.

**mcloop/coverage_verify.py** - Coverage-proven verification: the primary fallback when an unmapped behavioral Python change has no namesake test. Discovers a scoped dependent-test candidate set via a transitive first-party import walk, runs pytest with `pytest-cov` over only that set (never the full suite), and asserts the change's diff-hunk lines (vs the task's pre-edit baseline) were executed. Non-Python inputs cannot pass this path.

**mcloop/waivers.py** - Append-only test-verification waiver records in `.mcloop/test-verification-waivers.jsonl` (task label, changed input, pre-edit baseline SHA, reason, UTC timestamp). `record_waiver`/`has_waiver`/`load_waivers`; waivers are written only via the explicit `mcloop waive` subcommand and consulted by the `run_checks` gate, never written silently.

**mcloop/conftest_guard.py** - Inject an autouse pytest fixture into the target project's `tests/conftest.py` that blocks unmocked `claude -p` / `codex exec` subprocess calls.

**mcloop/errors.py** - Error/crash handling: inspect errors.json, diagnose failures, append fix tasks to BUGS.md.

**mcloop/formatting.py** - Terminal output formatting (user prompts, auto observations, system actions, errors).

**mcloop/git_ops.py** - Git operations: checkpoint, commit, push, change detection. Includes `_worktree_status`, `_has_uncommitted_changes`, and `_changed_files` (uses `git diff --name-only HEAD` + `git ls-files --others`).

**mcloop/idea_cmd.py** - Append timestamped ideas to IDEAS.md.

**mcloop/install_cmd.py** - Install and uninstall subcommands.

**mcloop/investigate_cmd.py** - Investigation subcommand and helpers.

**mcloop/investigator.py** - Generate investigation plans and gather bug context. Contains the debugging playbook and crash report filtering by process name.

**mcloop/lifecycle.py** - Process lifecycle: interrupt state, orphan cleanup, active-process tracking. `_check_interrupted` accepts active_paths so skip/describe prompts target the right file (PLAN.md vs BUGS.md). The signal handler writes an "interrupted" run summary before `os._exit(130)` via the writer `run_loop` registers with `set_interrupt_summary_writer`; `_build_and_write_summary` clears the hook when a run writes its terminal summary normally.

**mcloop/main.py** - CLI entry point and run loop. Defines `RunStatus`, `BuildResult`. Orchestrates task execution, checks, commits, reviewer spawn, audit, and run-summary writing. Supports `--stop-after-stage`, `--stop-after-one`, `--timeout`, and the `maintain` subcommand.

**mcloop/maintain.py** - Maintain mode: run each MAINTAIN.md invariant in its own CLI session, commit fixes, log to `.mcloop/maintain-log.json`. `stop_after_one` exits after first satisfied or fixed invariant.

**mcloop/notify.py** - Telegram and iMessage notifications.

**mcloop/output.py** - Display functions: task status, error tails, diff summaries, terminal run summary.

**mcloop/_planfile_compat.py** - Compatibility layer over `bob_tools.planfile`: canonical fence-aware parsing, checkbox check-off/mark-failed writers, and the plan/bugs routing helpers that superseded the retired `checklist.py` / `plan_split.py` modules.

**mcloop/process_monitor.py** - Launch, monitor, and inspect subprocesses. `kill_process_group` and `start_new_session=True` prevent shell=True orphans. GUI launch tracks pre-existing PIDs to avoid killing unrelated user instances.

**mcloop/prompts.py** - Prompt builders and output parsers for AI CLI sessions. Audit prompts reference `.mcloop/audit-report.md`.

**mcloop/pytest_optimizations.py** - Ensure the target project's `pyproject.toml` has `[tool.pytest.ini_options]` addopts with `-n auto`, a `timeout`, and `pytest-xdist` / `pytest-timeout` / `pytest-cov` dev deps (pytest-cov backs the coverage-proven verification fallback and works under `-n auto` with no extra config). Idempotent; called once at `run_loop()` startup after `ensure_conftest_guard()`.

**mcloop/ratelimit.py** - Rate limit detection and CLI fallover.

**mcloop/review_integration.py** - Reviewer subprocess spawn/collect/cleanup. Reviewer findings append to BUGS.md. `_purge_all_reviews` removes review files when reviewer is disabled.

**mcloop/reviewer.py** - AI-powered diff reviewer using an OpenAI-compatible API.

**mcloop/runner.py** - Run AI CLI subprocesses and capture output. `_run_session` enforces the per-task timeout (default 3600s = 60 minutes) and returns TIMEOUT_EXIT_CODE (-102, outside the signal range) on timeout.

**mcloop/run_summary.py** - RunSummary / TaskEntry / CheckEntry schema and JSON writer. Produces dated summaries plus `latest.json` on every run_loop exit.

**mcloop/session_context.py** - Rolling session context shared between task sessions.

**mcloop/sync_cmd.py** - `sync` subcommand: update PLAN.md to match the codebase.

**mcloop/targeted.py** - Map changed source files to matching test files; `is_scoped_python_linter` and `targeted_linter_command` scope ruff check / ruff format --check to changed .py files.

**mcloop/web_interact.py** - Web app interaction via Playwright (optional dependency).

**mcloop/worktree.py** - Git worktree management for investigation branches.

**mcloop/wrap.py** - Instrument project source files with error-catching hooks (Swift, Python) using mcloop:wrap markers.

**telegram-permission-hook.py** - Telegram permission hook for interactive sessions. Includes session memory with command prefix extraction for Bash tools.

## Test Files

**tests/test_app_interact.py** - Tests for macOS GUI app interaction.

**tests/test_args.py** - Tests for CLI argument parsing and run_loop helpers: RunStatus, exit codes, BuildResult, worktree status, autofix metadata-only detection, terminal_failure sentinel, run summary schema, stop-after-stage / stop-after-one, task routing (BUGS.md + PLAN.md), and phase-boundary messaging.

**tests/test_checks.py** - Tests for check command detection, pytest normalization, side-effect-free run_checks, and autofix separation.

**tests/test_claude_md_check.py** - Tests for freshness check, the diff-summary → NOTES.md pipeline, and `_parse_llm_response`. Pins the invariant that CLAUDE.md is not written.

**tests/test_claude_md_sync.py** - Tests for `handle_sync` and `reconcile_pending`: pending-queue persistence, cap-of-one behavior, and halt notification.

**tests/test_config.py** - Tests for reviewer configuration loading.

**tests/test_coverage_verify.py** - Tests for `mcloop.coverage_verify`: diff-hunk new-line parsing, coverage-JSON parsing, `changed_new_lines` against a real git baseline, transitive dependent-test discovery, scoped `_run_coverage` (mocked subprocess writing a coverage JSON), and the `verify_change_covered` orchestrator (exercised passes, not-exercised fails, no-candidate fails, non-Python cannot pass, missing baseline fails closed).

**tests/test_waivers.py** - Tests for `mcloop.waivers`: required-field completeness of recorded waivers, append-only behavior, input+baseline matching, empty-baseline never matches, and corrupt-line skipping.

**tests/test_formatting.py** - Tests for terminal output formatting.

**tests/test_hook.py** - Tests for the telegram permission hook: interactive session skip, bash prefix extraction, tool-name pattern matching.

**tests/test_idea_cmd.py** - Tests for the idea subcommand (IDEAS.md creation and append).

**tests/test_integration.py** - Integration tests: full loop with mocked subprocesses, `_make_project` helper that sets up a canonical PLAN.md.

**tests/test_investigator.py** - Tests for investigation plan generation and bug context.

**tests/test_lifecycle.py** - Tests for interrupt state, orphan cleanup, and per-file skip targeting.

**tests/test_maintain.py** - Tests for maintain mode: MAINTAIN.md parsing, prompt building, output parsing, MaintainSummary, log writing, run_maintain loop, MAINTAIN_TOOLS propagation, and `stop_after_one`.

**tests/test_notify.py** - Tests for Telegram and iMessage notifications.

**tests/test_output.py** - Tests for output and display functions, including `_print_summary` stop_reason precedence.

**tests/test_process_monitor.py** - Tests for subprocess launching and monitoring: GUI launch with pre-existing PIDs, kill_process_group, start_new_session, run_cli group-kill paths.

**tests/test_pytest_optimizations.py** - Tests for `ensure_pytest_optimizations`: missing pyproject no-op, missing pytest section added, existing section with missing xdist dep, fully configured idempotent, partial addopts extension, and malformed TOML no-op.

**tests/test_ratelimit.py** - Tests for rate limit detection.

**tests/test_review_integration.py** - Tests for reviewer subprocess integration, `_purge_all_reviews`, and findings append to BUGS.md.

**tests/test_reviewer.py** - Tests for the AI diff reviewer.

**tests/test_reviewer_live.py** - Live test of the reviewer against a real commit.

**tests/test_runner.py** - Tests for the AI CLI subprocess runner and audit / bug-fix prompt contents.

**tests/test_sync_diff.py** - Tests for sync diff and confirmation logic.

**tests/test_targeted.py** - Tests for source-to-test mapping and scoped ruff / pytest commands.

**tests/test_web_interact.py** - Tests for web app interaction via Playwright.

**tests/test_worktree.py** - Tests for git worktree management.

## Smoke testing the orchestra integration

The wrapper tests (`tests/test_code_edit_wrapper.py`) cover prompt and
result-shape parity offline. They cannot exercise the live CLI stream
format, watchdog and PID timing, signal delivery, or Telegram approval
flow. Run a one-time live gate per environment before trusting the
orchestra backend in production. Each step below has explicit
yes/no checks; the gate passes only when every check is yes.

The plan's parity contract requires both backends to produce the same
`CodeEditResult` shape: `success` (bool), `exit_code` (int), `log_path`
(an existing non-empty file), and `changed_files` (a list). Mcloop
surfaces those fields through the run summary JSON at
`.mcloop/runs/latest.json` and through the per-task log file
under the configured `log_dir` (default `logs/`). Use those two
artifacts to evaluate the criteria below.

### Step 1: direct baseline

Setup. Pick a project with a clean working tree and no
`.orchestra/config.json`. Set PLAN.md to one item:
`- [ ] Add a hello-world comment to README.md`. Note `RUN1_LOG_DIR`,
the `log_dir` mcloop will use (default `./logs/`).

Run. Invoke mcloop and let it process the single item.

Pass criteria, all required:

- `git log -1 --oneline` shows a new commit landed.
- `git status` reports a clean working tree.
- `cat .mcloop/runs/latest.json` shows the task entry's
  `success: true` and `exit_code: 0`.
- The task's `log_path` from the same JSON entry exists and
  `wc -c "$log_path"` is greater than zero.
- The task's `changed_files` list in the JSON contains `README.md`.

Capture for step 2: copy the JSON entry's `success`, `exit_code`,
`log_path`, and `changed_files` values for later comparison. Reset
the working tree (`git reset --hard <pre-run-sha>`) so the orchestra
run starts from the same base.

### Step 2: orchestra path

Setup. Add `.orchestra/config.json` to the same project:

    {
      "workflows": {
        "code_edit": {
          "pattern": "single",
          "roles": {
            "editor": {
              "adapter": "claude_code_agent",
              "model": "<the same model name used in step 1>",
              "tools": "default",
              "parameters": {}
            }
          }
        }
      }
    }

PLAN.md must contain the same single item from step 1. Note
`RUN2_LOG_DIR`. Run mcloop again.

Pass criteria, all required:

- `git log -1 --oneline` shows a new commit landed on top of the
  pre-step-1 base.
- `git status` reports a clean working tree.
- `cat .mcloop/runs/latest.json` shows the task entry's
  `success: true` and `exit_code: 0`, and these match the captured
  values from step 1 exactly.
- The orchestra run wrote a session log under the directory
  specified by the JSON entry's `log_path`, the file exists, and
  `wc -c "$log_path"` is greater than zero.
- The task entry's `changed_files` list matches the step 1 list
  element by element in the same order. A different ordering or a
  missing or extra file is a failure.
- An orchestra run directory exists under `RUN2_LOG_DIR/orchestra-runs/`
  and contains a non-empty session log inside the run directory.
- No leftover PID file: `ls .mcloop/active-pid` returns
  `No such file or directory`.

### Step 3: interrupt path

Setup. Reset the working tree to the pre-step-1 base again. Keep the
`.orchestra/config.json` from step 2. Restore PLAN.md to the same
single item.

Important: after SIGINT, `latest.json` IS updated. Mcloop's signal
handler in `mcloop/lifecycle.py` writes an interrupted run summary
(via the writer `run_loop` registers with
`set_interrupt_summary_writer`) immediately before `os._exit(130)`,
so `.mcloop/runs/latest.json` describes the interrupted run with
`terminal_status: "interrupted"`. Also verify interruption state
from the orchestra session log on disk and the process tree.

The same `os._exit(130)` also kills the entire process before
orchestra's executor finishes the in-flight state, so the executor
never writes `actor_invoke_end` or `state_exit` for the
interrupted state. The reliable signal is the absence of those
records, not the presence of an interrupt-tagged record. Mcloop's
direct backend writes its per-task log only after `_run_session`
returns, so an interrupted direct-backed run leaves NO per-task
log file on disk; the only direct-path signals are the process
tree and the missing log itself.

Run. Start mcloop. Watch the terminal for the line that prints the
orchestra run directory path (orchestra prints the run id at the
start of each run; the run directory is at
`RUN2_LOG_DIR/orchestra-runs/<run-id>/`). Save the directory as
`ORCHESTRA_RUN_DIR` and the log file as
`ORCHESTRA_LOG=$ORCHESTRA_RUN_DIR/log.jsonl`. While the inner CLI
is streaming, send SIGINT (Ctrl-C) once. Wait for mcloop to exit.
Do not press Ctrl-C a second time.

Pass criteria, all required:

- Mcloop exits within ten seconds of the SIGINT.
- `cat .mcloop/runs/latest.json` shows
  `"terminal_status": "interrupted"` (the signal handler writes the
  run summary before exiting).
- `.mcloop/active-pid` does not exist
  (`ls .mcloop/active-pid` returns `No such file or directory`).
- For orchestra-backed runs only: `$ORCHESTRA_LOG` exists and
  `wc -c "$ORCHESTRA_LOG"` reports greater than zero bytes.
- For orchestra-backed runs only: the log shows the interrupt
  shape: an `actor_invoke_start` was written but no matching
  `actor_invoke_end` and no `state_exit` followed. Concretely:
  - `grep -c '"event": "actor_invoke_start"' "$ORCHESTRA_LOG"`
    returns at least 1.
  - `grep -c '"event": "actor_invoke_end"' "$ORCHESTRA_LOG"` is
    strictly less than the `actor_invoke_start` count.
  - `tail -1 "$ORCHESTRA_LOG"` shows an event that is one of
    `state_enter`, `actor_prepare`, or `actor_invoke_start`
    (anything past `actor_invoke_start` for the latest state
    means the executor finished and this was not interrupted in
    flight).
- For orchestra-backed runs only: the run directory has no files
  matching `pid`, `*.pid`, or `watchdog*`.
  `ls "$ORCHESTRA_RUN_DIR" | grep -E '^pid$|\\.pid$|^watchdog'`
  returns nothing.
- `pgrep -f 'mcloop.*watchdog'` returns no results.
- `pgrep -f 'claude -p'` returns no results from this run.
  Identify by start time relative to the step's start. A
  long-lived unrelated `claude -p` from another shell is
  acceptable.
- For direct-backed runs only: no per-task log file appears under
  the configured `log_dir` for this task. `_run_session` is
  killed before `_write_log` runs, so the absence of a log file
  is the expected state.

Any leftover watchdog or inner CLI process from this run is a
failure. A complete `actor_invoke_end` followed by `state_exit`
in the session log means the run finished normally and was not
interrupted in flight, which is also a failure (the gate is
specifically testing mid-flight interrupt).

This is a one-time gate per environment. Once all three steps pass
with every criterion above marked yes, the offline tests are
sufficient regression coverage until the wrapper or the signal path
changes.

**tests/test_wrap.py** - Tests for source file instrumentation.
