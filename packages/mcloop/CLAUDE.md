# Project Manifest

## Plan Layout (Split-Plan Design)

mcloop separates a project's roadmap across three files to keep per-session token usage low while preserving full context for human readers.

**PLAN.md** is the master roadmap containing every phase. It is only modified at phase transitions, when `mark_phase_complete` bulk-checks the just-finished phase. It is safe for the user to edit during a run; the loop never writes to it on per-task commits.

**CURRENT_PLAN.md** holds only the active phase, extracted from PLAN.md by `ensure_current_plan` at startup or by `transition_phase` when a phase finishes. Each Claude Code session works exclusively against this file plus BUGS.md. Users must not edit CURRENT_PLAN.md while mcloop is running, because checkpoint commits will overwrite their edits.

**BUGS.md** is a standalone bug backlog with checkbox items. It is treated as bug-only mode by run_loop: when any item is unchecked, only bug tasks are worked, and feature tasks in CURRENT_PLAN.md are blocked until BUGS.md is empty. Reviewer findings and crash diagnostics append to BUGS.md. The audit system's structured report file is separate (see below) and does not collide with BUGS.md.

### Phase transition lifecycle

1. The loop selects tasks from BUGS.md (priority) and CURRENT_PLAN.md until both have no actionable unchecked items.
2. When CURRENT_PLAN.md is fully checked off, the loop runs the full test suite (unscoped) and the build at the phase boundary.
3. On success, `transition_phase` bulk-checks the completed phase in PLAN.md, extracts the next unchecked phase from PLAN.md into CURRENT_PLAN.md (or unlinks CURRENT_PLAN.md if no phases remain), and the loop breaks. Every phase boundary ends the run; the user re-runs `mcloop` to start the next phase. Per-task checks remain scoped to the files touched by each commit.
4. When `transition_phase` returns None (no more phases), the post-loop runs the audit cycle.

### Bugs vs. audit reports

**BUGS.md** holds checklist tasks that the loop must work through. **`.mcloop/audit-report.md`** is the structured prose output written by `_run_audit_fix_cycle` for human review; it is not parsed as a checklist and is not consumed by the loop's task-selection logic.

## Core Modules

**mcloop/__init__.py** - Package marker (empty).

**mcloop/__main__.py** - `python -m mcloop` entry point; delegates to `main.main()`.

**mcloop/app_interact.py** - macOS GUI app interaction via osascript/System Events.

**mcloop/audit.py** - Audit functions: run checks, commit passing changes, report failures. Writes output to `.mcloop/audit-report.md`. Defines `AuditResult` enum (no_bugs, fixed, failed, skipped).

**mcloop/checklist.py** - Markdown checklist parser: read and write `- [ ]` items in PLAN.*md files. `purge_completed_bugs` removes checked items from BUGS.md.

**mcloop/checks.py** - Run a project's test/lint suite. Side-effect-free; `run_autofix` is a separate function. Scopes ruff and pytest to changed files via helpers in `targeted`.

**mcloop/claude_md_check.py** - Freshness check for the project manifest and LLM-driven diff summarization. `check_claude_md_freshness` reads CLAUDE.md only. `auto_update_claude_md` sends the git diff to a cheap LLM and appends the summary to NOTES.md; CLAUDE.md is never written to.

**mcloop/claude_md_sync.py** - Deferred sync glue with a pending queue capped at one entry. Wraps `auto_update_claude_md`, retries on transient failures, halts with a Telegram notification when the cap is exceeded.

**mcloop/config.py** - Reviewer configuration loading from `.mcloop/config.json`.

**mcloop/conftest_guard.py** - Inject an autouse pytest fixture into the target project's `tests/conftest.py` that blocks unmocked `claude -p` / `codex exec` subprocess calls.

**mcloop/errors.py** - Error/crash handling: inspect errors.json, diagnose failures, append fix tasks to BUGS.md.

**mcloop/formatting.py** - Terminal output formatting (user prompts, auto observations, system actions, errors).

**mcloop/git_ops.py** - Git operations: checkpoint, commit, push, change detection. Includes `_worktree_status`, `_has_uncommitted_changes`, and `_changed_files` (uses `git diff --name-only HEAD` + `git ls-files --others`).

**mcloop/idea_cmd.py** - Append timestamped ideas to IDEAS.md.

**mcloop/install_cmd.py** - Install and uninstall subcommands.

**mcloop/investigate_cmd.py** - Investigation subcommand and helpers.

**mcloop/investigator.py** - Generate investigation plans and gather bug context. Contains the debugging playbook and crash report filtering by process name.

**mcloop/lifecycle.py** - Process lifecycle: interrupt state, orphan cleanup, active-process tracking. `_check_interrupted` accepts active_paths for split-plan skip/describe targeting.

**mcloop/main.py** - CLI entry point and run loop. Defines `RunStatus`, `BuildResult`. Orchestrates task execution, checks, commits, reviewer spawn, audit, and run-summary writing. Supports `--stop-after-stage`, `--stop-after-one`, `--timeout`, and the `maintain` subcommand.

**mcloop/maintain.py** - Maintain mode: run each MAINTAIN.md invariant in its own CLI session, commit fixes, log to `.mcloop/maintain-log.json`. `stop_after_one` exits after first satisfied or fixed invariant.

**mcloop/notify.py** - Telegram and iMessage notifications.

**mcloop/output.py** - Display functions: task status, error tails, diff summaries, terminal run summary.

**mcloop/plan_split.py** - Split-plan management: extract next phase from PLAN.md into CURRENT_PLAN.md, mark completed phases, transition between phases, ensure BUGS.md and CURRENT_PLAN.md exist on startup.

**mcloop/process_monitor.py** - Launch, monitor, and inspect subprocesses. `kill_process_group` and `start_new_session=True` prevent shell=True orphans. GUI launch tracks pre-existing PIDs to avoid killing unrelated user instances.

**mcloop/prompts.py** - Prompt builders and output parsers for AI CLI sessions. Audit prompts reference `.mcloop/audit-report.md`.

**mcloop/pytest_optimizations.py** - Ensure the target project's `pyproject.toml` has `[tool.pytest.ini_options]` addopts with `-n auto`, a `timeout`, and `pytest-xdist` / `pytest-timeout` dev deps. Idempotent; called once at `run_loop()` startup after `ensure_conftest_guard()`.

**mcloop/ratelimit.py** - Rate limit detection and CLI fallover.

**mcloop/review_integration.py** - Reviewer subprocess spawn/collect/cleanup. Reviewer findings append to BUGS.md. `_purge_all_reviews` removes review files when reviewer is disabled.

**mcloop/reviewer.py** - AI-powered diff reviewer using an OpenAI-compatible API.

**mcloop/runner.py** - Run AI CLI subprocesses and capture output. `_run_session` enforces the per-task timeout (default 30 minutes) and returns exit code -2 on timeout.

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

**tests/test_args.py** - Tests for CLI argument parsing and run_loop helpers: RunStatus, exit codes, BuildResult, worktree status, autofix metadata-only detection, terminal_failure sentinel, run summary schema, stop-after-stage / stop-after-one, split-plan task routing (BUGS.md + CURRENT_PLAN.md), and phase-boundary messaging.

**tests/test_checklist.py** - Tests for checklist parsing, manipulation, and fallback nearest-match logic.

**tests/test_checks.py** - Tests for check command detection, pytest normalization, side-effect-free run_checks, and autofix separation.

**tests/test_claude_md_check.py** - Tests for freshness check, the diff-summary → NOTES.md pipeline, and `_parse_llm_response`. Pins the invariant that CLAUDE.md is not written.

**tests/test_claude_md_sync.py** - Tests for `handle_sync` and `reconcile_pending`: pending-queue persistence, cap-of-one behavior, and halt notification.

**tests/test_config.py** - Tests for reviewer configuration loading.

**tests/test_formatting.py** - Tests for terminal output formatting.

**tests/test_hook.py** - Tests for the telegram permission hook: interactive session skip, bash prefix extraction, tool-name pattern matching.

**tests/test_idea_cmd.py** - Tests for the idea subcommand (IDEAS.md creation and append).

**tests/test_integration.py** - Integration tests: full loop with mocked subprocesses, `_make_project` helper that creates PLAN.md and CURRENT_PLAN.md together.

**tests/test_investigator.py** - Tests for investigation plan generation and bug context.

**tests/test_lifecycle.py** - Tests for interrupt state, orphan cleanup, and split-plan skip targeting.

**tests/test_maintain.py** - Tests for maintain mode: MAINTAIN.md parsing, prompt building, output parsing, MaintainSummary, log writing, run_maintain loop, MAINTAIN_TOOLS propagation, and `stop_after_one`.

**tests/test_notify.py** - Tests for Telegram and iMessage notifications.

**tests/test_output.py** - Tests for output and display functions, including `_print_summary` stop_reason precedence.

**tests/test_plan_split.py** - Tests for `mcloop.plan_split`: extract_next_phase, mark_phase_complete, get_current_phase_name, ensure_current_plan, transition_phase, ensure_bugs_file, full three-phase round-trip, idempotency, and corruption surfacing.

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
`.mcloop/run-summary/latest.json` and through the per-task log file
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
- `cat .mcloop/run-summary/latest.json` shows the task entry's
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
- `cat .mcloop/run-summary/latest.json` shows the task entry's
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

Run. Start mcloop. While the inner CLI is streaming (you will see
streaming output in the terminal), send SIGINT (Ctrl-C) once. Wait
for mcloop to print the interrupt summary and exit. Do not press
Ctrl-C a second time.

Pass criteria, all required:

- Mcloop exits within ten seconds of the SIGINT.
- `.mcloop/active-pid` does not exist (`ls .mcloop/active-pid` returns
  `No such file or directory`). The wrapper's signal path cleans up
  on the way out.
- `.mcloop/interrupted.json` exists and was written during this run
  (`stat -f %m .mcloop/interrupted.json` is more recent than the
  step 3 start time).
- The most recent session log (the path captured from
  `.mcloop/run-summary/latest.json`'s last task entry) exists and
  has non-zero size.
- The same task entry in `latest.json` reports a non-zero exit code
  representing the interrupt: `exit_code: 130` (POSIX SIGINT) or
  `exit_code: -2` (mcloop's timeout/abort sentinel). Any other code
  is a failure.
- The orchestra run directory under `RUN2_LOG_DIR/orchestra-runs/`
  exists, the inner session log exists, and the run directory has no
  files matching `pid` or `*.pid` and no files matching `watchdog*`.
- `pgrep -f 'mcloop.*watchdog'` returns no results, and
  `pgrep -f 'claude -p'` returns no results that started during the
  step. Any leftover watchdog or inner CLI process is a failure.

This is a one-time gate per environment. Once all three steps pass
with every criterion above marked yes, the offline tests are
sufficient regression coverage until the wrapper or the signal path
changes.

**tests/test_wrap.py** - Tests for source file instrumentation.
