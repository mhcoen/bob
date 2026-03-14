# McLoop

McLoop lets you run Claude Code for hours at a time without babysitting it.
You write a task list in PLAN.md. McLoop works through it continuously,
launching a fresh CLI session per task, running your tests and linter,
committing only if everything passes, and notifying you of progress.

Python 3.11+, stdlib only, no external dependencies. Ruff for linting, pytest
for tests. Each task should leave the repo in a passing state: ruff check and
pytest must both pass before a commit is made. Prefer small, focused changes
per task. Write unit tests for new functionality. Keep modules short and avoid
over-abstraction.

## Stage 1: Core

- [x] Project scaffolding (pyproject.toml, .gitignore, mcloop package, __main__.py)
- [x] Markdown checklist parser
  - [x] Parse tasks from markdown checkboxes, including nested subtasks
  - [x] Find the next unchecked item (depth-first, top-down)
  - [x] Check off completed items in the file
  - [x] Mark failed items with [!] after max retries
- [x] CLI subprocess runner
  - [x] Launch a fresh Claude Code session with project description and task
  - [x] Capture output and exit code
  - [x] Write per-attempt log files to logs/ directory
- [x] Auto-detect and run project checks
  - [x] Detect ruff from pyproject.toml and run ruff check
  - [x] Detect pytest from pyproject.toml and run pytest
  - [x] Detect npm test from package.json
  - [x] Detect make check from Makefile
- [x] Telegram and iMessage notifications
  - [x] Load credentials from ~/.claude/telegram-hook.env or environment
  - [x] Notify on task completion, failure, rate limit, and queue finished
  - [x] NOTIFY_VIA setting to choose between Telegram (default) and iMessage
- [x] Rate limit detection
  - [x] Detect rate limit from CLI output
  - [x] Pause and wait for reset
  - [x] Notify user on pause and resume
- [x] Main loop: parse, execute, verify, commit, notify, repeat
  - [x] Git commit with task description on success
  - [x] Retry failed tasks up to max-retries
  - [x] Stop on stuck task (tasks may have implicit dependencies)
  - [x] Auto-check parent when all children are done
- [x] CLI interface
  - [x] --file flag for custom checklist path
  - [x] --dry-run to show what would run
  - [x] --max-retries flag (default: 3)
- [x] Unattended operation
  - [x] Telegram permission hook for remote approval of tool calls
  - [x] settings.example.json with sandbox config and hook setup
- [x] Add a safety commit to the main loop before processing any tasks
  - [x] In run_loop(), before the while loop, stage and commit all tracked modified files with a message like "mcloop: checkpoint before run"
  - [x] Skip if the working tree is clean
  - [x] Do not stage untracked files
- [x] Push to origin after each successful commit
  - [x] Add git push to _commit() after git commit
  - [x] If no remote exists, skip the push silently
  - [x] Create the remote repo with gh repo create if it does not exist
- [x] Support a mcloop.json config file for custom check commands
  - [x] If mcloop.json exists with a "checks" array, run those commands instead of auto-detecting
  - [x] Fall back to auto-detection when no config file is present
  - [x] Document mcloop.json in the README
- [x] Add a mcloop sync command
  - [x] Add sync subcommand to the CLI argument parser
  - [x] Launch a single Claude Code session that reads PLAN.md, README.md, CLAUDE.md, the git log, file tree, and source code
  - [x] Prompt Claude to add checked items for features, fixes, or changes reflected in the code but not in PLAN.md, matching existing granularity, appending only, never modifying existing items
  - [x] Prompt Claude to flag problems: checked items with no corresponding code, unchecked items that appear already implemented, description drifting from the codebase
  - [x] Show a diff of proposed PLAN.md changes before writing
- [x] After all checklist tasks are complete, automatically run a bug audit/fix cycle
  - [x] Add a function that runs a Claude Code session to audit the codebase and write BUGS.md listing only actual defects (crashes, incorrect behavior, unhandled errors, security issues), not style or refactoring
  - [x] If BUGS.md contains bugs, run a second session scoped to fixing only the bugs listed in BUGS.md, then delete BUGS.md
  - [x] Run this cycle once (no open-ended looping), then send the "All tasks completed" notification
  - [x] Add a --no-audit flag to skip the bug audit cycle
- [x] Integration tests
  - [x] Add a tests/integration/ directory gated behind pytest -m integration
  - [x] Test a minimal run: temp git repo, simple PLAN.md, verify file created, task checked off, commit made
  - [x] Test no-op detection: task that produces no file changes is treated as failure
  - [x] Test subtask ordering: depth-first execution with parent auto-checking
  - [x] Test resume after kill: run partway, kill, restart, verify it picks up where it left off
  - [x] Test failing task: verify retry behavior and [!] marking after max retries
- [x] Stage support in PLAN.md
  - [x] Parse `## Stage N:` headers and assign each task a stage
  - [x] `find_next()` only returns tasks from the first incomplete stage
  - [x] Stop at stage boundary, print stage completion in summary
  - [x] Audit only runs when all stages are complete
  - [x] `--dry-run` shows stage labels and which stage the next task is in
  - [x] Backward compatible with plans that have no stage headers
- [x] Elapsed time tracking per task and total run
- [x] Session context: rolling summary shared between task sessions within a run
- [x] Check commands passed in task prompt so Claude Code self-checks before finishing
- [x] Whitelist suggestions from Telegram session approvals
- [x] NOTES.md: Claude Code appends observations during tasks, summary tracks changes
- [x] Hash-based audit skipping (no changes since last audit)
- [x] BUGS.md resume: skip audit if BUGS.md already exists
- [x] Checkpoint commits include next task label
- [x] Multi-language check detection (Swift, Rust, Go, Java, Ruby, Make)
- [x] Auto-detect build and run commands
- [x] RTK proxy unwrapping in permission hook
- [x] MCP tool blocking in McLoop sessions via permission hook
- [x] Telegram approval waiting indicator in console output
- [x] Debugging instruction in task prompt (read crash reports first)
- [x] CLAUDE.md update instruction in task prompt
- [x] Visual verification with bin/appshot
- [x] Retry on session limit: poll every 10 minutes instead of sleeping forever, resume the loop when the limit resets
- [x] Post-fix verification: after each bug fix succeeds and checks pass, run a focused review session on only the changed files to verify the fix did not introduce new bugs. Feed it the original bug description and the diff. If it finds a problem, feed it back into the fix loop before committing.
- [x] Pre-fix bug verification: after the audit writes BUGS.md, run a separate verification session that reads each reported bug and checks it against the actual source code. Remove any bug that is incorrect (code doesn't match the description, the issue was already handled, the bug is hypothetical). Print to the terminal: "Verifying N bugs..." then for each bug either "CONFIRMED: <file:line> <title>" or "REMOVED: <file:line> <title> (reason)". Rewrite BUGS.md with only confirmed bugs before the fix cycle runs. If all bugs are removed, skip the fix cycle and print "All reported bugs were false positives."
- [x] Two-round audit cycle: run the full audit/verify/fix cycle twice. The second round catches bugs introduced by the first round's fixes. After the second round completes, save the audit hash and stop. Do not loop beyond two rounds.
- [x] Non-destructive BUGS.md: mcloop audit must append new findings to an existing BUGS.md, not overwrite it. Include the audit prompt instruction to read the existing BUGS.md first and only report bugs not already listed.
- [x] Fix ctrl-c/ctrl-z: claude -p takes over the terminal foreground process group, so ctrl-c is sent to claude instead of mcloop. After launching the subprocess with start_new_session=True, mcloop must reclaim the foreground process group with os.tcsetpgrp() so ctrl-c reaches mcloop's signal handler.
- [x] Clearer terminal output: suppress individual tool calls (Read, Edit, Write, Glob, Grep, TodoWrite) entirely. Only print Bash commands. Add a progress indicator (a dot every few seconds) while a claude -p session is running so it's clear mcloop is alive. During task sessions, parse Claude Code's streaming text to extract conceptual descriptions of what it's doing and print those as clean status lines instead of raw tool calls. Example flow:
  - ">>> [TASK 13.2] Extracting frames from video..." followed by a brief description like "Reading video extractor and scanner modules" then progress dots, then "Creating video_extractor.py with ffmpeg scene detection" then more dots, then ">>> [TASK 13.2] Complete [2m 29s]"
  - ">>> [CHECKS] Running ruff check, pytest..." then dots, then ">>> [CHECKS] Passed"
  - ">>> [AUDIT] Scanning for bugs..." then dots, then a numbered list of found bugs with file, severity, and title
  - ">>> [VERIFY] Verifying N bugs..." then for each bug print CONFIRMED or REMOVED with a one-line reason
  - ">>> [FIX] Fixing N bugs..." then for each bug as it's fixed, print the bug title and a brief explanation of the fix
  - Keep Bash commands visible since they show meaningful actions
- [x] Reduce Telegram notification frequency: only send notifications for events that require attention or mark real progress. Do not notify on individual retry failures (attempt 1/3, 2/3). Only notify when a task genuinely fails after all retries are exhausted, when a stage or the full run completes, when a session limit is hit, and when the audit cycle finishes. Combine stage completion and next stage start into a single message. Goal: no more than one notification every few minutes during normal operation.
- [x] Targeted testing: after each task, only run tests corresponding to changed files (e.g., changes to hasher.py runs test_hasher.py). Map source files to test files by naming convention. Run the full test suite only at stage boundaries and at the end of the run. This avoids running the entire test suite after every single task.
- [x] Skip Telegram permission hook for interactive sessions: the hook should check for the MCLOOP_TASK_LABEL environment variable (already set by runner.py) and exit 0 immediately if it's absent. This lets interactive Claude Code sessions use the normal terminal permission flow instead of sending Telegram approvals.
- [x] `--model` flag to select which Claude model to use (e.g., `--model opus`)
- [x] Sync `--dry-run` flag: show proposed PLAN.md changes without writing them
- [x] Standalone `audit` subcommand: run a bug audit without running the task loop
- [x] Permission denial kill: when a Telegram permission request is denied, immediately kill the running session and move on

## Stage 2: Investigation system (`mcloop investigate`)

Adds an interactive debugging mode for hard runtime bugs that survive
the build/test/audit cycle. The system creates a git worktree for
isolation, generates an investigation plan, runs it, and can interact
with the built app programmatically via accessibility APIs. The user
is in the terminal loop for observations the system cannot make
itself. Apps built by mcloop are instrumented with accessibility
labels from the start to enable automated UI testing.

The debugging playbook this enforces:
1. Reproduce the problem.
2. Instrument at stage boundaries.
3. Isolate subsystems with standalone probes.
4. Inspect live runtime behavior.
5. Only then patch production code.
6. Clean up temporary scaffolding after the fix.

- [x] Accessibility labels in task prompt
  - [x] Add instruction to the task prompt in runner.py: when building UI (SwiftUI, HTML, React, Qt, etc.), add accessibility identifiers to every interactive element (buttons, text fields, menu items, toggles). This makes every app mcloop builds programmatically testable.
  - [x] Add tests verifying the instruction is present in the prompt

- [x] Investigation NOTES.md structure
  - [x] Add instruction to the investigation plan description requiring three sections in NOTES.md: Observations (confirmed facts from runtime, docs, logs, or experiments), Hypotheses (candidate explanations not yet confirmed), and Eliminated (things ruled out, with the experiment that ruled them out)
  - [x] The investigation prompt must instruct the agent to check Eliminated before proposing any approach and refuse to repeat an eliminated approach unless new evidence contradicts the elimination

- [x] Process monitor module
  - [x] Create `mcloop/process_monitor.py` with functions to: launch a process from a run command, check if a process is alive by PID, detect a hung process (alive but not producing output for N seconds), sample a hung process on macOS (`sample <pid>`), kill a process, read the most recent crash report from `~/Library/Logs/DiagnosticReports/` matching a process name
  - [x] For CLI apps: launch with subprocess, capture stdout/stderr, detect crash (non-zero exit) or hang (no output timeout)
  - [x] For GUI apps: launch, check alive with pgrep, detect crash (process disappears) or hang (process alive but sample shows stuck main thread)
  - [x] Add tests with mock subprocesses

- [x] App interaction layer
  - [x] Create `mcloop/app_interact.py` with functions for macOS GUI app interaction via osascript/System Events: click button by accessibility label, select menu item by path, type text into focused field, read value of UI element by label, list all UI elements in a window, check if a window exists, take a screenshot of a specific window
  - [x] For CLI apps: send input to stdin, read stdout/stderr, send signals
  - [x] For web apps: detect if Playwright is available, launch headless browser, navigate to URL, click element, read page content, take screenshot
  - [x] Detect app type from mcloop.json (run command patterns: `open *.app` or `./run.sh` for GUI, bare binary or `python` for CLI, `npm start` or `flask run` for web)
  - [x] Add tests for each interaction type with mock targets

- [x] Investigation plan generator
  - [x] Create `mcloop/investigator.py` with a function that takes bug context (crash report, user description, failure history, source code summary) and produces an investigation PLAN.md following the debugging playbook
  - [x] The prompt for plan generation must include: the debugging playbook (reproduce, instrument, isolate, inspect, fix, clean up), instruction to create standalone probes for unclear subsystems, instruction to search the web for working examples before writing code, the "What has been tried" section populated from any available failure history
  - [x] The generated plan should include steps that use the process monitor and app interaction layer where applicable (e.g., "Launch the app and verify the menu bar icon appears" becomes a step that programmatically checks for the window/element)
  - [x] Add tests with sample bug descriptions verifying the generated plan contains research steps, isolation steps, and verification steps

- [x] Git worktree management
  - [x] Create `mcloop/worktree.py` with functions to: create a worktree from the current branch with a descriptive name and branch, check if a worktree already exists for a given investigation, list active investigation worktrees, merge an investigation branch back to the source branch, remove a worktree after successful merge
  - [x] Branch naming convention: `investigate-<slug>` where slug is derived from the bug description
  - [x] Directory naming convention: `../<project>-investigate-<slug>/` (sibling of the project directory)
  - [x] Handle the case where a worktree already exists (resume the investigation rather than creating a new one)
  - [x] Add tests for worktree creation, merge, and cleanup

- [x] The `investigate` subcommand
  - [x] Add `investigate` subcommand to argument parser with optional positional description argument and --log flag
  - [x] Gather bug context from multiple sources (DiagnosticReports, .mcloop/last-run.log, piped stdin, --log file, description argument)
  - [x] Create or resume a git worktree for the investigation
  - [x] If new: generate investigation PLAN.md via the plan generator, copy mcloop.json and .claude/ settings from the parent project
  - [x] Run mcloop as a subprocess in the worktree directory with --no-audit
  - [x] After mcloop completes: if all tasks passed, offer to merge back (show diff, ask confirmation). If tasks failed, print the investigation state (what was learned, what remains) and leave the worktree for the user to resume or review.

- [x] Interactive investigation loop
  - [x] When an investigation task requires user observation (the plan generator marks these with a keyword like `[USER]`), pause and print clearly formatted instructions for the user: what to do, what to look for, how to provide the result
  - [x] Accept free-form text input from the user at the terminal, incorporate it into the next session's context
  - [x] When the system can perform the observation itself (via process monitor or app interaction), do so automatically and feed the result into the next session
  - [x] Visual formatting: use clear visual separators to distinguish system actions from user prompts. User prompts should be impossible to miss in a scrolling terminal.

- [x] Automated verification after fix
  - [x] After the investigation produces a fix, automatically launch the app using the process monitor
  - [x] Use the app interaction layer to repeat the actions that triggered the original bug
  - [x] Verify the app survives (no crash, no hang, expected UI state)
  - [x] If verification fails, feed the new failure information back into the investigation loop
  - [x] If verification passes, proceed to merge

- [x] Integration with existing infrastructure
  - [x] Share bug context gathering code between investigate and any future fixbug command (same sources: DiagnosticReports, logs, piped input, description)
  - [x] Enable WebFetch and WebSearch tools for investigation sessions so the agent can research APIs and find working examples
  - [x] Enhanced testing instruction for investigation sessions: write tests that exercise real code with real inputs, do not mock core logic, test threading/async for deadlocks, handle system API permission cases gracefully
  - [x] Enhanced debugging instruction for investigation sessions: decompose before patching, search web for working examples, question assumptions when repeated approaches fail

- [x] Model fallback on task failure
  - [x] Add `--fallback-model` CLI flag (e.g. `mcloop --model sonnet --fallback-model opus`). Not the default; only active when explicitly provided.
  - [x] When a task exhausts all retries on the primary model and `--fallback-model` is set, retry the task from scratch using the fallback model (same retry count) before marking it failed.
  - [x] Print a clear message when falling back: "Primary model failed, retrying with <fallback-model>".
  - [x] If the fallback model also exhausts retries, mark the task failed as normal.
  - [x] Add tests covering the fallback path: primary fails all retries, fallback succeeds; both fail; fallback not set (no change in behavior).

- [x] Runtime error capture and self-healing (`mcloop wrap`)
  - [x] Add `mcloop wrap` subcommand that instruments a project's source files with error-catching hooks. Detects project language from PLAN.md description, file extensions, or build system. Supports Swift and Python initially.
  - [x] Swift instrumentation: inject `NSSetUncaughtExceptionHandler`, signal handlers (SIGSEGV, SIGABRT, SIGBUS), and an app-state dump that captures relevant `@Published` properties at crash time. Write structured error reports to `.mcloop/errors.json` with stack trace, app state, timestamp, and what the user was doing (last UI action if detectable).
  - [x] Python instrumentation: inject `sys.excepthook`, signal handlers, and logging integration that captures unhandled exceptions with full traceback, local variables in the crashing frame, and application state. Write to the same `.mcloop/errors.json` format.
  - [x] Delimit all injected code with markers (`// mcloop:wrap:begin` / `// mcloop:wrap:end` for Swift, `# mcloop:wrap:begin` / `# mcloop:wrap:end` for Python). Store canonical wrapper source in `.mcloop/wrap/` so it can be re-injected after edits.
  - [x] Re-injection after tasks: after every task that modifies instrumented source files, check whether markers are intact. If Claude Code stripped or damaged them, re-inject from `.mcloop/wrap/`. Run this check in `run_loop` after `_commit` and before moving to the next task.
  - [x] Error-to-task conversion: when `mcloop` starts (before any task work), read `.mcloop/errors.json`. If entries exist, print a summary with bug count, one-line description of each, and timestamps. Ask the user: "Fix these bugs before continuing? [Y/n]"
  - [x] If the user says yes: run a diagnostic `claude -p` session per error with the crash context, relevant source files, and git log. The session produces a fix description. Insert fix tasks into a `## Bugs` section in PLAN.md. This section has absolute priority: `find_next` returns bug tasks before any feature tasks.
  - [x] Bug-only mode: when `## Bugs` has unchecked items, `run_loop` works only those tasks. It does not fall through to feature tasks, does not start the next stage, does not run the audit cycle. It fixes, verifies (re-launches the app to confirm the error no longer occurs), and exits.
  - [x] After all bug tasks are complete and verified, clear the corresponding entries from `.mcloop/errors.json`. Print summary and exit. The user runs `mcloop` again for feature work.
  - [x] Loop limit: if the same error has triggered diagnostic insertion more than 3 times (tracked by a hash of the error signature in `.mcloop/errors.json`), mark it as unresolvable, print context, and stop. Do not loop indefinitely.
  - [x] `.mcloop/errors.json` format: array of objects, each with `id` (hash of stack trace), `timestamp`, `signal` or `exception_type`, `stack_trace`, `app_state` (dict of key-value pairs), `description` (one-line summary), `source_file` and `line` (crash location), `fix_attempts` (count of previous diagnostic insertions for this error).
  - [x] Add `find_next` priority logic: if any task under a `## Bugs` heading is unchecked, return that task regardless of position in the file. Feature tasks are only returned when `## Bugs` is empty or fully checked.
  - [x] Add tests: wrap injection for Swift and Python (markers present, re-injection after removal), error.json parsing, find_next priority with and without bug tasks, loop limit enforcement.

- [x] Auto-wrap: instrument apps automatically
  - [x] After the first successful task that results in a runnable app (detected via `detect_run` returning a non-empty command and no existing wrap markers in the project), automatically inject error-catching instrumentation. No `mcloop wrap` command needed. This happens once, silently, as part of the normal build flow. Print a one-line message: "Injected crash handlers."
  - [x] Bake the project directory path into the crash handler at injection time. When the app crashes, the handler prints to stderr: `[McLoop] Crash captured: <exception type> in <location>. Run mcloop from <project_dir> to fix this bug.` This tells the user exactly what to do.
  - [x] The `mcloop wrap` subcommand remains available for instrumenting projects that were NOT built by mcloop (existing codebases the user wants to add error capture to).
  - [x] Update the task prompt to tell Claude Code not to remove or modify code between mcloop:wrap markers.
  - [x] Add tests: auto-wrap triggers on first runnable task, does not trigger if markers already exist, does not trigger if no run command detected, crash message includes correct project path. (covered by existing test_wrap.py tests for wrap_project, detect_language, find_entry_point, and has_markers; _maybe_auto_wrap delegates to these)

- [x] Smarter no-op handling
  - [x] When a task session completes successfully (exit code 0) but produces no file changes, run the check commands before deciding whether it's a failure. If all checks pass, auto-check the task (the work was already done) and print "Task already satisfied (no changes needed)". If checks fail, treat it as a failure and retry as today. This prevents burning retries on tasks where the implementation already exists. Include tests for both paths: no changes + checks pass = auto-check, no changes + checks fail = retry.

- [x] Fix Ctrl-C: prevent claude from stealing the terminal foreground
  - [x] [USER] Prerequisite test: verify that `claude -p --output-format stream-json` produces correct output when launched with `stdin=/dev/null`. Run it manually from the shell with `claude -p "say hello" --output-format stream-json < /dev/null` or equivalent. If claude requires a tty on stdin (checks `isatty(0)`), this approach will not work and we need a different strategy. Do not proceed to the next subtask until this is confirmed.
  - [x] Rewrite `_run_session` in runner.py to sever claude from the real terminal. Remove all pty code (`pty.openpty`, `tty.setraw`, `os.read(master_fd, ...)`, raw byte buffering, slave_fd handling, EIO/EBADF handling). Spawn claude with: `stdin=subprocess.DEVNULL`, `stdout=subprocess.PIPE`, `stderr=subprocess.STDOUT`, `start_new_session=True`, `close_fds=True` (verify this is the Popen default, do not pass `pass_fds`). Restore the reader thread to read from `process.stdout` (a pipe, line-buffered) instead of raw fd reads. The combination of `start_new_session=True` (child gets a new session with no controlling terminal) and zero inherited terminal fds means claude cannot call `tcsetpgrp` on the real terminal. The real terminal stays under mcloop's exclusive control.
  - [x] Register an explicit `signal.signal(signal.SIGINT, handler)` in mcloop. The handler must call `os.killpg(process.pid, signal.SIGTERM)` to signal the entire child process group (not just the immediate child), then set a flag. If the group does not exit within 2 seconds, escalate to `os.killpg(process.pid, signal.SIGKILL)`. Do not rely on `KeyboardInterrupt` exception delivery through `queue.get()`. Also handle SIGTSTP (Ctrl-Z) the same way, or explicitly ignore it with `signal.signal(signal.SIGTSTP, signal.SIG_IGN)` so it does not silently background mcloop.
  - [x] Verify no fd leakage: add a temporary diagnostic that runs `lsof -p <child_pid>` (or `/usr/sbin/lsof`) immediately after spawning claude and logs the output. Confirm there are zero fds referencing `/dev/tty*` or `/dev/pts/*` in the child. Verify the watchdog subprocess does not accidentally inherit a terminal fd. Remove the diagnostic after confirming.
  - [x] [USER] Manual verification: user tests Ctrl-C, Ctrl-Z, and `kill <pid>` on a live mcloop run. Verify all three interrupt mcloop cleanly and kill the claude subprocess group. Verify stream-json output is still captured correctly in logs.

- [x] [RULEDOUT] tag for recording failed approaches in PLAN.md
  - [x] Add a field to the Task dataclass in checklist.py to store ruled out approaches
  - [x] Parse `[RULEDOUT]` lines in PLAN.md and attach them to the correct parent task based on indentation. Lines are associated with the nearest task at a strictly lower indent level.
  - [x] Add a function to collect all [RULEDOUT] entries for a task, including entries inherited from ancestor tasks in the tree
  - [x] Add a parameter to `run_task` in runner.py for ruled out approaches. When non-empty, append a "RULED OUT APPROACHES" block to the task prompt instructing the agent not to repeat any listed approach and to try a fundamentally different strategy.
  - [x] In `run_loop` in main.py, collect ruled out entries before the retry loop and pass them to `run_task`

- [x] Interrupt state capture and resumption
  - [x] In the signal handler, immediately print "Interrupted. Saving state..." before any other work. Write `.mcloop/interrupted.json` with: task label, timestamp, elapsed time, last 20 lines of captured output, and what phase mcloop was in (task, checks, audit, user_prompt). Kill the child process group. Print "State saved. Exiting." All of this is synchronous file I/O and process signals, no API calls.
  - [x] Track the current phase in a module-level variable (e.g. `_current_phase`) that is set at each transition point in `run_loop`: "task", "checks", "audit", "user_prompt". The signal handler reads this variable when writing `interrupted.json`.
  - [x] On startup in `run_loop`, check for `.mcloop/interrupted.json`. If present, show a summary and prompt: the task that was running, how long it had been active, last output lines, and the phase. Offer choices: (r)etry as-is, (d)escribe what went wrong, (s)kip, (q)uit. Single keypress for the common case.
  - [x] If the user picks "describe": accept free-form text, write a `[RULEDOUT]` entry to PLAN.md under the interrupted task, and append to `.mcloop/eliminated.json` (keyed by task label, with approach description, reason, timestamp). Optionally run a short Claude session against the captured output to generate a richer summary. Delete `interrupted.json`.
  - [x] If the user picks "retry": delete `interrupted.json`, proceed normally.
  - [x] If the user picks "skip": mark the task `[!]`, delete `interrupted.json`, move to the next task.
  - [x] Tailor the prompt to the interrupted phase. Audit interruptions offer (r)esume audit / (s)kip audit / (q)uit. User prompt interruptions just re-present the `[USER]` prompt with no special handling. Task interruptions get the full (r)etry / (d)escribe / (s)kip / (q)uit menu.

- [x] `mcloop install` and `mcloop uninstall` subcommands
  - [x] Add `install` and `uninstall` subcommands to the argument parser, both with `--dry-run` flags
  - [x] `install`: check that `claude` is on PATH, print version, stop with instructions if missing
  - [x] `install`: copy hook scripts (Telegram permission hook, session-start hook) to `~/.mcloop/hooks/`. Skip if already present.
  - [x] `install`: read `~/.claude/settings.json`, merge in PreToolUse and SessionStart hook entries pointing at `~/.mcloop/hooks/`. Skip entries that already exist. Preserve all other settings in the file.
  - [x] `install`: check for Telegram credentials. If `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in the environment, print that they will be used and skip prompting. If `~/.claude/telegram-hook.env` exists, print that existing credentials will be used and skip prompting. Otherwise, prompt interactively for bot token and chat ID and write `~/.claude/telegram-hook.env`. Print a recommendation to install the Telegram Desktop app alongside the mobile app.
  - [x] `install`: ask about ANTHROPIC_API_KEY. Default is no (mcloop strips the key so claude uses the subscription). If yes, mcloop will not strip it. Record choice in `~/.mcloop/config.json`.
  - [x] `install`: ask whether to enable Claude Code sandbox. Will enable but will never disable. Skip if already enabled.
  - [x] `install`: do not modify `permissions.allow`. Install `~/.mcloop/recommended-permissions.json` with the recommended baseline from `settings.example.json`. Print a message stating that McLoop does not modify runtime permissions and the recommended settings are provided for the user to merge manually.
  - [x] `install`: if `rtk` is on PATH, print a note that RTK was detected and its hooks should be configured separately via `rtk init`. Do not touch RTK hooks.
  - [x] `install`: print summary of everything configured, skipped, or needing manual action
  - [x] `install --dry-run`: print every file that would be created or modified, with diffs for JSON modifications, but make no changes
  - [x] `uninstall`: remove mcloop hook entries from `~/.claude/settings.json` (only entries pointing at `~/.mcloop/hooks/`, nothing else)
  - [x] `uninstall`: remove `~/.claude/telegram-hook.env`
  - [x] `uninstall`: remove `~/.mcloop/hooks/` and `~/.mcloop/config.json` and `~/.mcloop/recommended-permissions.json`
  - [x] `uninstall`: leave `permissions.allow` entries, project-level `.mcloop/` directories, PLAN.md files, and logs untouched. Will not disable the sandbox.
  - [x] `uninstall`: print what was removed and what was left in place
  - [x] `uninstall --dry-run`: print every file that would be deleted or modified, with diffs for JSON modifications, but make no changes

## Stage 3: Continuous code reviewer

- [x] [BATCH] Add reviewer module (`mcloop/reviewer.py`)
  - [x] Create `ReviewFinding` dataclass: file, line_range, severity (error/warning/info), description, confidence (high/medium/low)
  - [x] Create `ReviewRequest` dataclass: commit_hash, diff_text, project_description, task_label, task_text
  - [x] Create `run_review(request, config) -> list[ReviewFinding]`: POST to OpenAI-compatible chat/completions endpoint with model, base_url, and API key from config. System prompt instructs model to review a diff for bugs, unhandled errors, logic mismatches with the task spec, resource leaks, and missing edge cases. Response must be JSON array of findings. Parse with json.loads, return empty list on parse failure or HTTP error.
  - [x] Create `run_review_cli(commit_hash, project_dir)`: entry point for subprocess invocation. Reads config from `.mcloop/config.json`, computes diff with `git diff <hash>^..<hash>`, loads project description from PLAN.md, calls `run_review`, writes results to `.mcloop/reviews/{commit_hash}.json`.
  - [x] `python -m mcloop.reviewer <commit_hash> <project_dir>` invokes `run_review_cli` for standalone testing.
  - [x] Use only stdlib (`urllib.request`, `json`) for the HTTP call. No new dependencies.

- [x] [BATCH] Add reviewer config and loading
  - [x] Add `load_reviewer_config(project_dir) -> dict | None` to `mcloop/config.py`. Reads `.mcloop/config.json`, returns the `reviewer` dict if present and `OPENROUTER_API_KEY` env var is set. Returns None otherwise.
  - [x] Schema: `{"reviewer": {"model": "...", "base_url": "..."}}`. API key always from `OPENROUTER_API_KEY` env var.
  - [x] Add `format_reviewer_status(config) -> str`: returns `"{model} via {host} (API key set)"`, or `"configured but OPENROUTER_API_KEY not set (disabled)"`, or empty string if no config.

- [x] [BATCH] Integrate reviewer lifecycle into run_loop
  - [x] In `run_loop`, after existing startup output, print reviewer status using `format_reviewer_status` if non-empty.
  - [x] After `_commit()` succeeds, if reviewer is enabled, spawn `subprocess.Popen([sys.executable, "-m", "mcloop.reviewer", commit_hash, str(project_dir)])` with `stdout=DEVNULL`, `stderr=DEVNULL`, `start_new_session=True`. Store Popen object in a list. Do not wait.
  - [x] At the top of the `while True` loop, scan `.mcloop/reviews/` for `.json` files. Parse each, filter to high-confidence findings, delete after reading. If findings exist, append a "Review findings from previous tasks" block to session context. If 3+ high-confidence error-severity findings from one commit, insert a fix task into `## Bugs` section of PLAN.md instead.
  - [x] In the signal handler and atexit handler, terminate any active reviewer subprocesses from the stored list.
  - [x] On startup, remove stale `.mcloop/reviews/*.json` older than 24 hours.

- [x] Add reviewer to `mcloop install` summary: at the end of the install summary, if `.mcloop/config.json` has a reviewer section, print its status using `format_reviewer_status`. Do not prompt for OpenRouter credentials during install.

- [x] [BATCH] Update documentation
  - [x] Add "Continuous code reviewer" section under "Advanced features" in the mcloop README. Cover: what it does, how to enable it (config.json + env var), what providers work (OpenRouter, any OpenAI-compatible endpoint, Ollama), what it catches, how findings are delivered (context or Bugs escalation), that it never blocks the main loop.
  - [x] Add reviewer config example to the README.
  - [x] Update the "Features at a glance" list.
  - [x] Add a sentence to the duplo README noting that batched tasks are reviewed as a single diff after the batch commit.

## Stage 4: Secure session environment and Codex support

- [ ] [BATCH] Replace inherited environment with minimal allowlist
  - [x] Define `_PASSTHROUGH_VARS` set in `runner.py`: PATH, HOME, TERM, LANG, LC_ALL, TMPDIR, USER, LOGNAME, SHELL, XDG_CACHE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, COLORTERM, FORCE_COLOR, NO_COLOR
  - [x] Add `_build_session_env(task_label)` function that builds env from `_PASSTHROUGH_VARS` only, adds MCLOOP_TASK_LABEL, and reads `env_passthrough` list from mcloop config for user-specified extras
  - [x] Update `_run_session` to use `_build_session_env()` instead of `dict(env or os.environ)` with single-key stripping
  - [x] Update `run_task` to stop constructing its own env dict from `os.environ`
  - [x] Remove `keep_anthropic_api_key` config option from `_run_session`, `_setup_api_key` in main.py, and install flow
  - [x] Verify all `_run_session` call sites work with minimal env
  - [ ] Add tests: `_build_session_env` includes only allowlisted vars, `env_passthrough` adds specified vars, credentials (ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN) are excluded by default, MCLOOP_TASK_LABEL is present
  - [ ] Document `env_passthrough` in README

- [ ] Add Codex as a CLI backend
  - [ ] Add `--cli` argument to arg parser (choices: claude, codex, default: claude)
  - [ ] Add `cli` field to `.mcloop/config.json` as alternative to command line flag
  - [ ] Update `_build_command` codex branch: `codex exec --ask-for-approval never --sandbox workspace-write --path <dir> --model <model> "prompt"`
  - [ ] Update `_run_session` env building: for codex, also exclude CODEX_API_KEY unless user opts in via `env_passthrough`
  - [ ] Update `get_available_cli` to return the configured CLI instead of hardcoded "claude"
  - [ ] Update rate limit detection for Codex output patterns
  - [ ] Update session limit detection for Codex output patterns
  - [ ] Test `_build_command` produces correct codex exec invocation
  - [ ] Test `_build_command` produces correct claude invocation (no regression)
  - [ ] Document `--cli codex` in README with security model differences
