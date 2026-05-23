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
<!-- phase_id: phase_001 -->

- [x] T-000001: Project scaffolding (pyproject.toml, .gitignore, mcloop package, __main__.py)
- [x] T-000006: Markdown checklist parser
  - [x] T-000002: Parse tasks from markdown checkboxes, including nested subtasks
  - [x] T-000003: Find the next unchecked item (depth-first, top-down)
  - [x] T-000004: Check off completed items in the file
  - [x] T-000005: Mark failed items with [!] after max retries
- [x] T-000010: CLI subprocess runner
  - [x] T-000007: Launch a fresh Claude Code session with project description and task
  - [x] T-000008: Capture output and exit code
  - [x] T-000009: Write per-attempt log files to logs/ directory
- [x] T-000015: Auto-detect and run project checks
  - [x] T-000011: Detect ruff from pyproject.toml and run ruff check
  - [x] T-000012: Detect pytest from pyproject.toml and run pytest
  - [x] T-000013: Detect npm test from package.json
  - [x] T-000014: Detect make check from Makefile
- [x] T-000019: Telegram and iMessage notifications
  - [x] T-000016: Load credentials from ~/.claude/telegram-hook.env or environment
  - [x] T-000017: Notify on task completion, failure, rate limit, and queue finished
  - [x] T-000018: NOTIFY_VIA setting to choose between Telegram (default) and iMessage
- [x] T-000023: Rate limit detection
  - [x] T-000020: Detect rate limit from CLI output
  - [x] T-000021: Pause and wait for reset
  - [x] T-000022: Notify user on pause and resume
- [x] T-000028: Main loop: parse, execute, verify, commit, notify, repeat
  - [x] T-000024: Git commit with task description on success
  - [x] T-000025: Retry failed tasks up to max-retries
  - [x] T-000026: Stop on stuck task (tasks may have implicit dependencies)
  - [x] T-000027: Auto-check parent when all children are done
- [x] T-000032: CLI interface
  - [x] T-000029: --file flag for custom checklist path
  - [x] T-000030: --dry-run to show what would run
  - [x] T-000031: --max-retries flag (default: 3)
- [x] T-000035: Unattended operation
  - [x] T-000033: Telegram permission hook for remote approval of tool calls
  - [x] T-000034: settings.example.json with sandbox config and hook setup
- [x] T-000039: Add a safety commit to the main loop before processing any tasks
  - [x] T-000036: In run_loop(), before the while loop, stage and commit all tracked modified files with a message like "mcloop: checkpoint before run"
  - [x] T-000037: Skip if the working tree is clean
  - [x] T-000038: Do not stage untracked files
- [x] T-000043: Push to origin after each successful commit
  - [x] T-000040: Add git push to _commit() after git commit
  - [x] T-000041: If no remote exists, skip the push silently
  - [x] T-000042: Create the remote repo with gh repo create if it does not exist
- [x] T-000047: Support a mcloop.json config file for custom check commands
  - [x] T-000044: If mcloop.json exists with a "checks" array, run those commands instead of auto-detecting
  - [x] T-000045: Fall back to auto-detection when no config file is present
  - [x] T-000046: Document mcloop.json in the README
- [x] T-000053: Add a mcloop sync command
  - [x] T-000048: Add sync subcommand to the CLI argument parser
  - [x] T-000049: Launch a single Claude Code session that reads PLAN.md, README.md, CLAUDE.md, the git log, file tree, and source code
  - [x] T-000050: Prompt Claude to add checked items for features, fixes, or changes reflected in the code but not in PLAN.md, matching existing granularity, appending only, never modifying existing items
  - [x] T-000051: Prompt Claude to flag problems: checked items with no corresponding code, unchecked items that appear already implemented, description drifting from the codebase
  - [x] T-000052: Show a diff of proposed PLAN.md changes before writing
- [x] T-000058: After all checklist tasks are complete, automatically run a bug audit/fix cycle
  - [x] T-000054: Add a function that runs a Claude Code session to audit the codebase and write BUGS.md listing only actual defects (crashes, incorrect behavior, unhandled errors, security issues), not style or refactoring
  - [x] T-000055: If BUGS.md contains bugs, run a second session scoped to fixing only the bugs listed in BUGS.md, then delete BUGS.md
  - [x] T-000056: Run this cycle once (no open-ended looping), then send the "All tasks completed" notification
  - [x] T-000057: Add a --no-audit flag to skip the bug audit cycle
- [x] T-000065: Integration tests
  - [x] T-000059: Add a tests/integration/ directory gated behind pytest -m integration
  - [x] T-000060: Test a minimal run: temp git repo, simple PLAN.md, verify file created, task checked off, commit made
  - [x] T-000061: Test no-op detection: task that produces no file changes is treated as failure
  - [x] T-000062: Test subtask ordering: depth-first execution with parent auto-checking
  - [x] T-000063: Test resume after kill: run partway, kill, restart, verify it picks up where it left off
  - [x] T-000064: Test failing task: verify retry behavior and [!] marking after max retries
- [x] T-000072: Stage support in PLAN.md
  - [x] T-000066: Parse `## Stage N:` headers and assign each task a stage
  - [x] T-000067: `find_next()` only returns tasks from the first incomplete stage
  - [x] T-000068: Stop at stage boundary, print stage completion in summary
  - [x] T-000069: Audit only runs when all stages are complete
  - [x] T-000070: `--dry-run` shows stage labels and which stage the next task is in
  - [x] T-000071: Backward compatible with plans that have no stage headers
- [x] T-000073: Elapsed time tracking per task and total run
- [x] T-000074: Session context: rolling summary shared between task sessions within a run
- [x] T-000075: Check commands passed in task prompt so Claude Code self-checks before finishing
- [x] T-000076: Whitelist suggestions from Telegram session approvals
- [x] T-000077: NOTES.md: Claude Code appends observations during tasks, summary tracks changes
- [x] T-000078: Hash-based audit skipping (no changes since last audit)
- [x] T-000079: BUGS.md resume: skip audit if BUGS.md already exists
- [x] T-000080: Checkpoint commits include next task label
- [x] T-000081: Multi-language check detection (Swift, Rust, Go, Java, Ruby, Make)
- [x] T-000082: Auto-detect build and run commands
- [x] T-000083: RTK proxy unwrapping in permission hook
- [x] T-000084: MCP tool blocking in McLoop sessions via permission hook
- [x] T-000085: Telegram approval waiting indicator in console output
- [x] T-000086: Debugging instruction in task prompt (read crash reports first)
- [x] T-000087: CLAUDE.md update instruction in task prompt
- [x] T-000088: Visual verification with bin/appshot
- [x] T-000089: Retry on session limit: poll every 10 minutes instead of sleeping forever, resume the loop when the limit resets
- [x] T-000090: Post-fix verification: after each bug fix succeeds and checks pass, run a focused review session on only the changed files to verify the fix did not introduce new bugs. Feed it the original bug description and the diff. If it finds a problem, feed it back into the fix loop before committing.
- [x] T-000091: Pre-fix bug verification: after the audit writes BUGS.md, run a separate verification session that reads each reported bug and checks it against the actual source code. Remove any bug that is incorrect (code doesn't match the description, the issue was already handled, the bug is hypothetical). Print to the terminal: "Verifying N bugs..." then for each bug either "CONFIRMED: <file:line> <title>" or "REMOVED: <file:line> <title> (reason)". Rewrite BUGS.md with only confirmed bugs before the fix cycle runs. If all bugs are removed, skip the fix cycle and print "All reported bugs were false positives."
- [x] T-000092: Two-round audit cycle: run the full audit/verify/fix cycle twice. The second round catches bugs introduced by the first round's fixes. After the second round completes, save the audit hash and stop. Do not loop beyond two rounds.
- [x] T-000093: Non-destructive BUGS.md: mcloop audit must append new findings to an existing BUGS.md, not overwrite it. Include the audit prompt instruction to read the existing BUGS.md first and only report bugs not already listed.
- [x] T-000094: Fix ctrl-c/ctrl-z: claude -p takes over the terminal foreground process group, so ctrl-c is sent to claude instead of mcloop. After launching the subprocess with start_new_session=True, mcloop must reclaim the foreground process group with os.tcsetpgrp() so ctrl-c reaches mcloop's signal handler.
- [x] T-000095: Clearer terminal output: suppress individual tool calls (Read, Edit, Write, Glob, Grep, TodoWrite) entirely. Only print Bash commands. Add a progress indicator (a dot every few seconds) while a claude -p session is running so it's clear mcloop is alive. During task sessions, parse Claude Code's streaming text to extract conceptual descriptions of what it's doing and print those as clean status lines instead of raw tool calls. Example flow:
  - ">>> [TASK 13.2] Extracting frames from video..." followed by a brief description like "Reading video extractor and scanner modules" then progress dots, then "Creating video_extractor.py with ffmpeg scene detection" then more dots, then ">>> [TASK 13.2] Complete [2m 29s]"
  - ">>> [CHECKS] Running ruff check, pytest..." then dots, then ">>> [CHECKS] Passed"
  - ">>> [AUDIT] Scanning for bugs..." then dots, then a numbered list of found bugs with file, severity, and title
  - ">>> [VERIFY] Verifying N bugs..." then for each bug print CONFIRMED or REMOVED with a one-line reason
  - ">>> [FIX] Fixing N bugs..." then for each bug as it's fixed, print the bug title and a brief explanation of the fix
  - Keep Bash commands visible since they show meaningful actions
- [x] T-000096: Reduce Telegram notification frequency: only send notifications for events that require attention or mark real progress. Do not notify on individual retry failures (attempt 1/3, 2/3). Only notify when a task genuinely fails after all retries are exhausted, when a stage or the full run completes, when a session limit is hit, and when the audit cycle finishes. Combine stage completion and next stage start into a single message. Goal: no more than one notification every few minutes during normal operation.
- [x] T-000097: Targeted testing: after each task, only run tests corresponding to changed files (e.g., changes to hasher.py runs test_hasher.py). Map source files to test files by naming convention. Run the full test suite only at stage boundaries and at the end of the run. This avoids running the entire test suite after every single task.
- [x] T-000098: Skip Telegram permission hook for interactive sessions: the hook should check for the MCLOOP_TASK_LABEL environment variable (already set by runner.py) and exit 0 immediately if it's absent. This lets interactive Claude Code sessions use the normal terminal permission flow instead of sending Telegram approvals.
- [x] T-000099: `--model` flag to select which Claude model to use (e.g., `--model opus`)
- [x] T-000100: Sync `--dry-run` flag: show proposed PLAN.md changes without writing them
- [x] T-000101: Standalone `audit` subcommand: run a bug audit without running the task loop
- [x] T-000102: Permission denial kill: when a Telegram permission request is denied, immediately kill the running session and move on

## Stage 2: Investigation system (`mcloop investigate`)
<!-- phase_id: phase_002 -->

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

- [x] T-000105: Accessibility labels in task prompt
  - [x] T-000103: Add instruction to the task prompt in runner.py: when building UI (SwiftUI, HTML, React, Qt, etc.), add accessibility identifiers to every interactive element (buttons, text fields, menu items, toggles). This makes every app mcloop builds programmatically testable.
  - [x] T-000104: Add tests verifying the instruction is present in the prompt

- [x] T-000108: Investigation NOTES.md structure
  - [x] T-000106: Add instruction to the investigation plan description requiring three sections in NOTES.md: Observations (confirmed facts from runtime, docs, logs, or experiments), Hypotheses (candidate explanations not yet confirmed), and Eliminated (things ruled out, with the experiment that ruled them out)
  - [x] T-000107: The investigation prompt must instruct the agent to check Eliminated before proposing any approach and refuse to repeat an eliminated approach unless new evidence contradicts the elimination

- [x] T-000113: Process monitor module
  - [x] T-000109: Create `mcloop/process_monitor.py` with functions to: launch a process from a run command, check if a process is alive by PID, detect a hung process (alive but not producing output for N seconds), sample a hung process on macOS (`sample <pid>`), kill a process, read the most recent crash report from `~/Library/Logs/DiagnosticReports/` matching a process name
  - [x] T-000110: For CLI apps: launch with subprocess, capture stdout/stderr, detect crash (non-zero exit) or hang (no output timeout)
  - [x] T-000111: For GUI apps: launch, check alive with pgrep, detect crash (process disappears) or hang (process alive but sample shows stuck main thread)
  - [x] T-000112: Add tests with mock subprocesses

- [x] T-000119: App interaction layer
  - [x] T-000114: Create `mcloop/app_interact.py` with functions for macOS GUI app interaction via osascript/System Events: click button by accessibility label, select menu item by path, type text into focused field, read value of UI element by label, list all UI elements in a window, check if a window exists, take a screenshot of a specific window
  - [x] T-000115: For CLI apps: send input to stdin, read stdout/stderr, send signals
  - [x] T-000116: For web apps: detect if Playwright is available, launch headless browser, navigate to URL, click element, read page content, take screenshot
  - [x] T-000117: Detect app type from mcloop.json (run command patterns: `open *.app` or `./run.sh` for GUI, bare binary or `python` for CLI, `npm start` or `flask run` for web)
  - [x] T-000118: Add tests for each interaction type with mock targets

- [x] T-000124: Investigation plan generator
  - [x] T-000120: Create `mcloop/investigator.py` with a function that takes bug context (crash report, user description, failure history, source code summary) and produces an investigation PLAN.md following the debugging playbook
  - [x] T-000121: The prompt for plan generation must include: the debugging playbook (reproduce, instrument, isolate, inspect, fix, clean up), instruction to create standalone probes for unclear subsystems, instruction to search the web for working examples before writing code, the "What has been tried" section populated from any available failure history
  - [x] T-000122: The generated plan should include steps that use the process monitor and app interaction layer where applicable (e.g., "Launch the app and verify the menu bar icon appears" becomes a step that programmatically checks for the window/element)
  - [x] T-000123: Add tests with sample bug descriptions verifying the generated plan contains research steps, isolation steps, and verification steps

- [x] T-000130: Git worktree management
  - [x] T-000125: Create `mcloop/worktree.py` with functions to: create a worktree from the current branch with a descriptive name and branch, check if a worktree already exists for a given investigation, list active investigation worktrees, merge an investigation branch back to the source branch, remove a worktree after successful merge
  - [x] T-000126: Branch naming convention: `investigate-<slug>` where slug is derived from the bug description
  - [x] T-000127: Directory naming convention: `../<project>-investigate-<slug>/` (sibling of the project directory)
  - [x] T-000128: Handle the case where a worktree already exists (resume the investigation rather than creating a new one)
  - [x] T-000129: Add tests for worktree creation, merge, and cleanup

- [x] T-000137: The `investigate` subcommand
  - [x] T-000131: Add `investigate` subcommand to argument parser with optional positional description argument and --log flag
  - [x] T-000132: Gather bug context from multiple sources (DiagnosticReports, .mcloop/last-run.log, piped stdin, --log file, description argument)
  - [x] T-000133: Create or resume a git worktree for the investigation
  - [x] T-000134: If new: generate investigation PLAN.md via the plan generator, copy mcloop.json and .claude/ settings from the parent project
  - [x] T-000135: Run mcloop as a subprocess in the worktree directory with --no-audit
  - [x] T-000136: After mcloop completes: if all tasks passed, offer to merge back (show diff, ask confirmation). If tasks failed, print the investigation state (what was learned, what remains) and leave the worktree for the user to resume or review.

- [x] T-000142: Interactive investigation loop
  - [x] T-000138: When an investigation task requires user observation (the plan generator marks these with a keyword like `[USER]`), pause and print clearly formatted instructions for the user: what to do, what to look for, how to provide the result
  - [x] T-000139: Accept free-form text input from the user at the terminal, incorporate it into the next session's context
  - [x] T-000140: When the system can perform the observation itself (via process monitor or app interaction), do so automatically and feed the result into the next session
  - [x] T-000141: Visual formatting: use clear visual separators to distinguish system actions from user prompts. User prompts should be impossible to miss in a scrolling terminal.

- [x] T-000148: Automated verification after fix
  - [x] T-000143: After the investigation produces a fix, automatically launch the app using the process monitor
  - [x] T-000144: Use the app interaction layer to repeat the actions that triggered the original bug
  - [x] T-000145: Verify the app survives (no crash, no hang, expected UI state)
  - [x] T-000146: If verification fails, feed the new failure information back into the investigation loop
  - [x] T-000147: If verification passes, proceed to merge

- [x] T-000153: Integration with existing infrastructure
  - [x] T-000149: Share bug context gathering code between investigate and any future fixbug command (same sources: DiagnosticReports, logs, piped input, description)
  - [x] T-000150: Enable WebFetch and WebSearch tools for investigation sessions so the agent can research APIs and find working examples
  - [x] T-000151: Enhanced testing instruction for investigation sessions: write tests that exercise real code with real inputs, do not mock core logic, test threading/async for deadlocks, handle system API permission cases gracefully
  - [x] T-000152: Enhanced debugging instruction for investigation sessions: decompose before patching, search web for working examples, question assumptions when repeated approaches fail

- [x] T-000159: Model fallback on task failure
  - [x] T-000154: Add `--fallback-model` CLI flag (e.g. `mcloop --model sonnet --fallback-model opus`). Not the default; only active when explicitly provided.
  - [x] T-000155: When a task exhausts all retries on the primary model and `--fallback-model` is set, retry the task from scratch using the fallback model (same retry count) before marking it failed.
  - [x] T-000156: Print a clear message when falling back: "Primary model failed, retrying with <fallback-model>".
  - [x] T-000157: If the fallback model also exhausts retries, mark the task failed as normal.
  - [x] T-000158: Add tests covering the fallback path: primary fails all retries, fallback succeeds; both fail; fallback not set (no change in behavior).

- [x] T-000173: Runtime error capture and self-healing (`mcloop wrap`)
  - [x] T-000160: Add `mcloop wrap` subcommand that instruments a project's source files with error-catching hooks. Detects project language from PLAN.md description, file extensions, or build system. Supports Swift and Python initially.
  - [x] T-000161: Swift instrumentation: inject `NSSetUncaughtExceptionHandler`, signal handlers (SIGSEGV, SIGABRT, SIGBUS), and an app-state dump that captures relevant `@Published` properties at crash time. Write structured error reports to `.mcloop/errors.json` with stack trace, app state, timestamp, and what the user was doing (last UI action if detectable).
  - [x] T-000162: Python instrumentation: inject `sys.excepthook`, signal handlers, and logging integration that captures unhandled exceptions with full traceback, local variables in the crashing frame, and application state. Write to the same `.mcloop/errors.json` format.
  - [x] T-000163: Delimit all injected code with markers (`// mcloop:wrap:begin` / `// mcloop:wrap:end` for Swift, `# mcloop:wrap:begin` / `# mcloop:wrap:end` for Python). Store canonical wrapper source in `.mcloop/wrap/` so it can be re-injected after edits.
  - [x] T-000164: Re-injection after tasks: after every task that modifies instrumented source files, check whether markers are intact. If Claude Code stripped or damaged them, re-inject from `.mcloop/wrap/`. Run this check in `run_loop` after `_commit` and before moving to the next task.
  - [x] T-000165: Error-to-task conversion: when `mcloop` starts (before any task work), read `.mcloop/errors.json`. If entries exist, print a summary with bug count, one-line description of each, and timestamps. Ask the user: "Fix these bugs before continuing? [Y/n]"
  - [x] T-000166: If the user says yes: run a diagnostic `claude -p` session per error with the crash context, relevant source files, and git log. The session produces a fix description. Insert fix tasks into a `## Bugs` section in PLAN.md. This section has absolute priority: `find_next` returns bug tasks before any feature tasks.
  - [x] T-000167: Bug-only mode: when `## Bugs` has unchecked items, `run_loop` works only those tasks. It does not fall through to feature tasks, does not start the next stage, does not run the audit cycle. It fixes, verifies (re-launches the app to confirm the error no longer occurs), and exits.
  - [x] T-000168: After all bug tasks are complete and verified, clear the corresponding entries from `.mcloop/errors.json`. Print summary and exit. The user runs `mcloop` again for feature work.
  - [x] T-000169: Loop limit: if the same error has triggered diagnostic insertion more than 3 times (tracked by a hash of the error signature in `.mcloop/errors.json`), mark it as unresolvable, print context, and stop. Do not loop indefinitely.
  - [x] T-000170: `.mcloop/errors.json` format: array of objects, each with `id` (hash of stack trace), `timestamp`, `signal` or `exception_type`, `stack_trace`, `app_state` (dict of key-value pairs), `description` (one-line summary), `source_file` and `line` (crash location), `fix_attempts` (count of previous diagnostic insertions for this error).
  - [x] T-000171: Add `find_next` priority logic: if any task under a `## Bugs` heading is unchecked, return that task regardless of position in the file. Feature tasks are only returned when `## Bugs` is empty or fully checked.
  - [x] T-000172: Add tests: wrap injection for Swift and Python (markers present, re-injection after removal), error.json parsing, find_next priority with and without bug tasks, loop limit enforcement.

- [x] T-000179: Auto-wrap: instrument apps automatically
  - [x] T-000174: After the first successful task that results in a runnable app (detected via `detect_run` returning a non-empty command and no existing wrap markers in the project), automatically inject error-catching instrumentation. No `mcloop wrap` command needed. This happens once, silently, as part of the normal build flow. Print a one-line message: "Injected crash handlers."
  - [x] T-000175: Bake the project directory path into the crash handler at injection time. When the app crashes, the handler prints to stderr: `[McLoop] Crash captured: <exception type> in <location>. Run mcloop from <project_dir> to fix this bug.` This tells the user exactly what to do.
  - [x] T-000176: The `mcloop wrap` subcommand remains available for instrumenting projects that were NOT built by mcloop (existing codebases the user wants to add error capture to).
  - [x] T-000177: Update the task prompt to tell Claude Code not to remove or modify code between mcloop:wrap markers.
  - [x] T-000178: Add tests: auto-wrap triggers on first runnable task, does not trigger if markers already exist, does not trigger if no run command detected, crash message includes correct project path. (covered by existing test_wrap.py tests for wrap_project, detect_language, find_entry_point, and has_markers; _maybe_auto_wrap delegates to these)

- [x] T-000181: Smarter no-op handling
  - [x] T-000180: When a task session completes successfully (exit code 0) but produces no file changes, run the check commands before deciding whether it's a failure. If all checks pass, auto-check the task (the work was already done) and print "Task already satisfied (no changes needed)". If checks fail, treat as terminal failure (no retry, since retrying a no-op with failing checks would produce the same result). This prevents burning retries on tasks where the implementation already exists.

- [x] T-000187: Fix Ctrl-C: prevent claude from stealing the terminal foreground
  - [x] T-000182: [USER] Prerequisite test: verify that `claude -p --output-format stream-json` produces correct output when launched with `stdin=/dev/null`. Run it manually from the shell with `claude -p "say hello" --output-format stream-json < /dev/null` or equivalent. If claude requires a tty on stdin (checks `isatty(0)`), this approach will not work and we need a different strategy. Do not proceed to the next subtask until this is confirmed.
  - [x] T-000183: Rewrite `_run_session` in runner.py to sever claude from the real terminal. Remove all pty code (`pty.openpty`, `tty.setraw`, `os.read(master_fd, ...)`, raw byte buffering, slave_fd handling, EIO/EBADF handling). Spawn claude with: `stdin=subprocess.DEVNULL`, `stdout=subprocess.PIPE`, `stderr=subprocess.STDOUT`, `start_new_session=True`, `close_fds=True` (verify this is the Popen default, do not pass `pass_fds`). Restore the reader thread to read from `process.stdout` (a pipe, line-buffered) instead of raw fd reads. The combination of `start_new_session=True` (child gets a new session with no controlling terminal) and zero inherited terminal fds means claude cannot call `tcsetpgrp` on the real terminal. The real terminal stays under mcloop's exclusive control.
  - [x] T-000184: Register an explicit `signal.signal(signal.SIGINT, handler)` in mcloop. The handler must call `os.killpg(process.pid, signal.SIGTERM)` to signal the entire child process group (not just the immediate child), then set a flag. If the group does not exit within 2 seconds, escalate to `os.killpg(process.pid, signal.SIGKILL)`. Do not rely on `KeyboardInterrupt` exception delivery through `queue.get()`. Also handle SIGTSTP (Ctrl-Z) the same way, or explicitly ignore it with `signal.signal(signal.SIGTSTP, signal.SIG_IGN)` so it does not silently background mcloop.
  - [x] T-000185: Verify no fd leakage: add a temporary diagnostic that runs `lsof -p <child_pid>` (or `/usr/sbin/lsof`) immediately after spawning claude and logs the output. Confirm there are zero fds referencing `/dev/tty*` or `/dev/pts/*` in the child. Verify the watchdog subprocess does not accidentally inherit a terminal fd. Remove the diagnostic after confirming.
  - [x] T-000186: [USER] Manual verification: user tests Ctrl-C, Ctrl-Z, and `kill <pid>` on a live mcloop run. Verify all three interrupt mcloop cleanly and kill the claude subprocess group. Verify stream-json output is still captured correctly in logs.

- [x] T-000193: [RULEDOUT] tag for recording failed approaches in PLAN.md
  - [x] T-000188: Add a field to the Task dataclass in checklist.py to store ruled out approaches
  - [x] T-000189: Parse `[RULEDOUT]` lines in PLAN.md and attach them to the correct parent task based on indentation. Lines are associated with the nearest task at a strictly lower indent level.
  - [x] T-000190: Add a function to collect all [RULEDOUT] entries for a task, including entries inherited from ancestor tasks in the tree
  - [x] T-000191: Add a parameter to `run_task` in runner.py for ruled out approaches. When non-empty, append a "RULED OUT APPROACHES" block to the task prompt instructing the agent not to repeat any listed approach and to try a fundamentally different strategy.
  - [x] T-000192: In `run_loop` in main.py, collect ruled out entries before the retry loop and pass them to `run_task`

- [x] T-000201: Interrupt state capture and resumption
  - [x] T-000194: In the signal handler, immediately print "Interrupted. Saving state..." before any other work. Write `.mcloop/interrupted.json` with: task label, timestamp, elapsed time, last 20 lines of captured output, and what phase mcloop was in (task, checks, audit, user_prompt). Kill the child process group. Print "State saved. Exiting." All of this is synchronous file I/O and process signals, no API calls.
  - [x] T-000195: Track the current phase in a module-level variable (e.g. `_current_phase`) that is set at each transition point in `run_loop`: "task", "checks", "audit", "user_prompt". The signal handler reads this variable when writing `interrupted.json`.
  - [x] T-000196: On startup in `run_loop`, check for `.mcloop/interrupted.json`. If present, show a summary and prompt: the task that was running, how long it had been active, last output lines, and the phase. Offer choices: (r)etry as-is, (d)escribe what went wrong, (s)kip, (q)uit. Single keypress for the common case.
  - [x] T-000197: If the user picks "describe": accept free-form text, write a `[RULEDOUT]` entry to PLAN.md under the interrupted task, and append to `.mcloop/eliminated.json` (keyed by task label, with approach description, reason, timestamp). Optionally run a short Claude session against the captured output to generate a richer summary. Delete `interrupted.json`.
  - [x] T-000198: If the user picks "retry": delete `interrupted.json`, proceed normally.
  - [x] T-000199: If the user picks "skip": mark the task `[!]`, delete `interrupted.json`, move to the next task.
  - [x] T-000200: Tailor the prompt to the interrupted phase. Audit interruptions offer (r)esume audit / (s)kip audit / (q)uit. User prompt interruptions just re-present the `[USER]` prompt with no special handling. Task interruptions get the full (r)etry / (d)escribe / (s)kip / (q)uit menu.

- [x] T-000219: `mcloop install` and `mcloop uninstall` subcommands
  - [x] T-000202: Add `install` and `uninstall` subcommands to the argument parser, both with `--dry-run` flags
  - [x] T-000203: `install`: check that `claude` is on PATH, print version, stop with instructions if missing
  - [x] T-000204: `install`: copy hook scripts (Telegram permission hook, session-start hook) to `~/.mcloop/hooks/`. Skip if already present.
  - [x] T-000205: `install`: read `~/.claude/settings.json`, merge in PreToolUse and SessionStart hook entries pointing at `~/.mcloop/hooks/`. Skip entries that already exist. Preserve all other settings in the file.
  - [x] T-000206: `install`: check for Telegram credentials. If `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in the environment, print that they will be used and skip prompting. If `~/.claude/telegram-hook.env` exists, print that existing credentials will be used and skip prompting. Otherwise, prompt interactively for bot token and chat ID and write `~/.claude/telegram-hook.env`. Print a recommendation to install the Telegram Desktop app alongside the mobile app.
  - [x] T-000207: `install`: ask about ANTHROPIC_API_KEY. Default is no (mcloop strips the key so claude uses the subscription). If yes, mcloop will not strip it. Record choice in `~/.mcloop/config.json`.
  - [x] T-000208: `install`: ask whether to enable Claude Code sandbox. Will enable but will never disable. Skip if already enabled.
  - [x] T-000209: `install`: do not modify `permissions.allow`. Install `~/.mcloop/recommended-permissions.json` with the recommended baseline from `settings.example.json`. Print a message stating that McLoop does not modify runtime permissions and the recommended settings are provided for the user to merge manually.
  - [x] T-000210: `install`: if `rtk` is on PATH, print a note that RTK was detected and its hooks should be configured separately via `rtk init`. Do not touch RTK hooks.
  - [x] T-000211: `install`: print summary of everything configured, skipped, or needing manual action
  - [x] T-000212: `install --dry-run`: print every file that would be created or modified, with diffs for JSON modifications, but make no changes
  - [x] T-000213: `uninstall`: remove mcloop hook entries from `~/.claude/settings.json` (only entries pointing at `~/.mcloop/hooks/`, nothing else)
  - [x] T-000214: `uninstall`: remove `~/.claude/telegram-hook.env`
  - [x] T-000215: `uninstall`: remove `~/.mcloop/hooks/` and `~/.mcloop/config.json` and `~/.mcloop/recommended-permissions.json`
  - [x] T-000216: `uninstall`: leave `permissions.allow` entries, project-level `.mcloop/` directories, PLAN.md files, and logs untouched. Will not disable the sandbox.
  - [x] T-000217: `uninstall`: print what was removed and what was left in place
  - [x] T-000218: `uninstall --dry-run`: print every file that would be deleted or modified, with diffs for JSON modifications, but make no changes

## Stage 3: Continuous code reviewer
<!-- phase_id: phase_003 -->

- [x] T-000226: [BATCH] Add reviewer module (`mcloop/reviewer.py`)
  - [x] T-000220: Create `ReviewFinding` dataclass: file, line_range, severity (error/warning/info), description, confidence (high/medium/low)
  - [x] T-000221: Create `ReviewRequest` dataclass: commit_hash, diff_text, project_description, task_label, task_text
  - [x] T-000222: Create `run_review(request, config) -> list[ReviewFinding]`: POST to OpenAI-compatible chat/completions endpoint with model, base_url, and API key from config. System prompt instructs model to review a diff for bugs, unhandled errors, logic mismatches with the task spec, resource leaks, and missing edge cases. Response must be JSON array of findings. Parse with json.loads, return empty list on parse failure or HTTP error.
  - [x] T-000223: Create `run_review_cli(commit_hash, project_dir)`: entry point for subprocess invocation. Reads config from `.mcloop/config.json`, computes diff with `git diff <hash>^..<hash>`, loads project description from PLAN.md, calls `run_review`, writes results to `.mcloop/reviews/{commit_hash}.json`.
  - [x] T-000224: `python -m mcloop.reviewer <commit_hash> <project_dir>` invokes `run_review_cli` for standalone testing.
  - [x] T-000225: Use only stdlib (`urllib.request`, `json`) for the HTTP call. No new dependencies.

- [x] T-000230: [BATCH] Add reviewer config and loading
  - [x] T-000227: Add `load_reviewer_config(project_dir) -> dict | None` to `mcloop/config.py`. Reads `.mcloop/config.json`, returns the `reviewer` dict if present and `OPENROUTER_API_KEY` env var is set. Returns None otherwise.
  - [x] T-000228: Schema: `{"reviewer": {"model": "...", "base_url": "..."}}`. API key always from `OPENROUTER_API_KEY` env var.
  - [x] T-000229: Add `format_reviewer_status(config) -> str`: returns `"{model} via {host} (API key set)"`, or `"configured but OPENROUTER_API_KEY not set (disabled)"`, or empty string if no config.

- [x] T-000236: [BATCH] Integrate reviewer lifecycle into run_loop
  - [x] T-000231: In `run_loop`, after existing startup output, print reviewer status using `format_reviewer_status` if non-empty.
  - [x] T-000232: After `_commit()` succeeds, if reviewer is enabled, spawn `subprocess.Popen([sys.executable, "-m", "mcloop.reviewer", commit_hash, str(project_dir)])` with `stdout=DEVNULL`, `stderr=DEVNULL`, `start_new_session=True`. Store Popen object in a list. Do not wait.
  - [x] T-000233: At the top of the `while True` loop, scan `.mcloop/reviews/` for `.json` files. Parse each, filter to high-confidence findings, delete after reading. If findings exist, append a "Review findings from previous tasks" block to session context. If 3+ high-confidence error-severity findings from one commit, insert a fix task into `## Bugs` section of PLAN.md instead.
  - [x] T-000234: In the signal handler and atexit handler, terminate any active reviewer subprocesses from the stored list.
  - [x] T-000235: On startup, remove stale `.mcloop/reviews/*.json` older than 24 hours.

- [x] T-000237: Add reviewer to `mcloop install` summary: at the end of the install summary, if `.mcloop/config.json` has a reviewer section, print its status using `format_reviewer_status`. Do not prompt for OpenRouter credentials during install.

- [x] T-000242: [BATCH] Update documentation
  - [x] T-000238: Add "Continuous code reviewer" section under "Advanced features" in the mcloop README. Cover: what it does, how to enable it (config.json + env var), what providers work (OpenRouter, any OpenAI-compatible endpoint, Ollama), what it catches, how findings are delivered (context or Bugs escalation), that it never blocks the main loop.
  - [x] T-000239: Add reviewer config example to the README.
  - [x] T-000240: Update the "Features at a glance" list.
  - [x] T-000241: Add a sentence to the duplo README noting that batched tasks are reviewed as a single diff after the batch commit.

## Stage 4: Secure session environment and Codex support
<!-- phase_id: phase_004 -->

- [x] T-000251: [BATCH] Replace inherited environment with minimal allowlist
  - [x] T-000243: Define `_PASSTHROUGH_VARS` set in `runner.py`: PATH, HOME, TERM, LANG, LC_ALL, TMPDIR, USER, LOGNAME, SHELL, XDG_CACHE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, COLORTERM, FORCE_COLOR, NO_COLOR, RTK_DB_PATH, RTK_TEE, RTK_TEE_DIR (RTK needs these to record token savings)
  - [x] T-000244: Add `_build_session_env(task_label)` function that builds env from `_PASSTHROUGH_VARS` only, adds MCLOOP_TASK_LABEL, and reads `env_passthrough` list from mcloop config for user-specified extras
  - [x] T-000245: Update `_run_session` to use `_build_session_env()` instead of `dict(env or os.environ)` with single-key stripping
  - [x] T-000246: Update `run_task` to stop constructing its own env dict from `os.environ`
  - [x] T-000247: Remove `keep_anthropic_api_key` config option from `_run_session`, `_setup_api_key` in main.py, and install flow
  - [x] T-000248: Verify all `_run_session` call sites work with minimal env
  - [x] T-000249: Add tests: `_build_session_env` includes only allowlisted vars, `env_passthrough` adds specified vars, credentials (ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN) are excluded by default, MCLOOP_TASK_LABEL is present
  - [x] T-000250: Document `env_passthrough` in README

- [x] T-000262: Add Codex as a CLI backend
  - [x] T-000252: Add `--cli` argument to arg parser (choices: claude, codex, default: claude)
  - [x] T-000253: Add `cli` field to `~/.mcloop/config.json` as alternative to command line flag
  - [x] T-000254: Update `_build_command` codex branch: `codex exec --ask-for-approval never --sandbox workspace-write --path <dir> --model <model> "prompt"`
  - [x] T-000255: Pass `cli` parameter through to `_build_session_env` so billing key matches the active CLI
  - [x] T-000256: Update `get_available_cli` to return the configured CLI instead of hardcoded "claude"
  - [x] T-000257: Update rate limit detection for Codex output patterns
  - [x] T-000258: Update session limit detection for Codex output patterns
  - [x] T-000259: Test `_build_command` produces correct codex exec invocation
  - [x] T-000260: Test `_build_command` produces correct claude invocation (no regression)
  - [x] T-000261: Document `--cli codex` in README with security model differences

- [x] T-000268: [BATCH] Add model config and validation
  - [x] T-000263: Read default model from `"model"` field in `~/.mcloop/config.json` when `--model` is not passed on command line
  - [x] T-000264: Define known-good model lists per CLI: claude (opus, sonnet, haiku, opusplan plus versioned variants), codex (gpt-5.4, gpt-5.4-pro, gpt-5.3-codex, gpt-5.3-codex-spark, gpt-5.2-codex, gpt-5.2, gpt-5.1-codex-max, gpt-5.1-codex, gpt-5-codex, gpt-5-codex-mini, gpt-4.1, gpt-4.1-mini)
  - [x] T-000265: At startup, if configured model is not in the known-good list for the active CLI, print one warning line and continue
  - [x] T-000266: `--model` flag overrides config, validation runs on whichever value is active
  - [x] T-000267: Add tests for model defaulting from config and for the warning message

- [x] T-000273: [BATCH] OpenRouter billing mode
  - [x] T-000269: Add `"billing": "openrouter"` option to `_build_session_env`: set `ANTHROPIC_BASE_URL` to `https://openrouter.ai/api`, set `ANTHROPIC_AUTH_TOKEN` from `OPENROUTER_API_KEY` in parent env, set `ANTHROPIC_API_KEY` to empty string
  - [x] T-000270: Add `"batch": false` config key. When false, `run_loop` ignores `[BATCH]` tags and runs all children individually
  - [x] T-000271: Add tests for openrouter env setup and batch disable
  - [x] T-000272: Document in README

## Stage 5: Integration tests
<!-- phase_id: phase_005 -->

- [x] T-000278: Create stub CLI for deterministic integration testing
  - [x] T-000274: Create `tests/stubs/stub_cli.py`: a script that reads a prompt from argv, consults a scenario file (JSON) to determine what files to create/modify, what output to print, what exit code to return, and how long to wait before responding
  - [x] T-000275: Scenario file format: list of behaviors keyed by prompt substring match (e.g. if prompt contains "create hello.txt", write hello.txt with "hello", exit 0; if prompt contains "rate limit", print rate limit message, exit 1)
  - [x] T-000276: The stub must be invocable as both `claude -p` and `codex exec` (detect which mode from argv and behave accordingly)
  - [x] T-000277: Add a test fixture that sets up a temp git repo with a PLAN.md, points `_build_command` at the stub, and runs `run_loop`

- [x] T-000289: [BATCH] Stub-based integration tests
  - [x] T-000279: Test: single task completes, gets checked off, files committed
  - [x] T-000280: Test: task fails, retries up to max_retries, marked failed
  - [x] T-000281: Test: check command fails, task retries with check output as prior_errors
  - [x] T-000282: Test: rate limit output triggers pause and retry
  - [x] T-000283: Test: session limit output triggers polling
  - [x] T-000284: Test: [BATCH] parent with multiple children runs one session, all children checked off
  - [x] T-000285: Test: batch failure falls back to individual execution
  - [x] T-000286: Test: no-op task (no file changes, checks pass) gets auto-checked
  - [x] T-000287: Test: reviewer spawned after successful commit when reviewer config is present
  - [x] T-000288: Test: stage boundary triggers full test suite and stops

- [x] T-000294: Real CLI integration tests (gated behind MCLOOP_INTEGRATION=1)
  - [x] T-000290: Test: trivial Claude Code task ("create a file called hello.txt containing hello") completes and commits
  - [x] T-000291: Test: trivial Codex task (same) completes and commits
  - [x] T-000292: Test: task with intentional check failure ("create hello.txt" but check command is "test -f goodbye.txt") retries and eventually fails
  - [x] T-000293: Skip all tests when MCLOOP_INTEGRATION is not set

## Stage 6: Structural cleanup
<!-- phase_id: phase_006 -->

- [x] T-000298: Extract install/uninstall from main.py into mcloop/install_cmd.py
  - [x] T-000295: Move _cmd_install, _cmd_uninstall, _setup_telegram, _setup_sandbox, _setup_env_security, _install_hooks, _install_recommended_permissions, _merge_settings, _unmerge_settings, _remove_telegram_env, _remove_hooks_dir, _remove_config_json, _remove_recommended_perms, _check_rtk, _check_reviewer, _load_mcloop_config, _print_install_summary, _print_uninstall_summary, and _print_file_diff
  - [x] T-000296: Update main.py imports to delegate to install_cmd
  - [x] T-000297: Verify mcloop install and mcloop uninstall still work (including --dry-run)
- [x] T-000302: Extract signal handling and interrupt state from main.py into mcloop/interrupt.py
  - [x] T-000299: Move _save_interrupt_state, _check_interrupted, _kill_orphan_sessions, _kill_active_process, _graceful_kill_active_process, and the module-level state they depend on (_active_process, _current_phase, _interrupted_task, etc.)
  - [x] T-000300: Expose a register_signal_handlers(process_ref) entry point that run_loop calls at startup
  - [x] T-000301: [USER] Verify Ctrl-C, Ctrl-Z, and kill still work correctly on a live run
- [x] T-000305: Extract run summary and display helpers from main.py into mcloop/display.py
  - [x] T-000303: Move _print_summary, _print_error_tail, _print_notes_update, _task_label, _format_elapsed, _tail, _snapshot_notes, _dry_run
  - [x] T-000304: These are pure formatting functions with no orchestration side effects
- [x] T-000308: Extract reviewer lifecycle from main.py into mcloop/reviewer_lifecycle.py
  - [x] T-000306: Move _get_commit_hash, _spawn_reviewer, _cleanup_stale_reviews, _collect_review_findings, _terminate_reviewers
  - [x] T-000307: reviewer.py already owns the review logic itself; this module owns spawning and collecting results within run_loop
- [x] T-000312: Remove pytest-of-mhcoen/ from version control
  - [x] T-000309: Add pytest-of-*/ to .gitignore
  - [x] T-000310: git rm -r --cached pytest-of-mhcoen/
  - [x] T-000311: Commit the removal and .gitignore update

## Stage 7: Maintain mode and ideas scratchpad
<!-- phase_id: phase_007 -->

- [x] T-000317: [BATCH] IDEAS.md scratchpad mechanism
  - [x] T-000313: Create a top-level IDEAS.md file in the repo root with a brief header explaining its purpose: a flat scratchpad for ideas not yet ready to become PLAN.md tasks
  - [x] T-000314: mcloop must not parse, execute, or modify IDEAS.md during normal runs (it is human-only state)
  - [x] T-000315: Add an `mcloop idea "some text"` subcommand that appends a timestamped line to IDEAS.md in the project root, creating the file if it does not exist
  - [x] T-000316: Document IDEAS.md in the README, contrasting it with PLAN.md (PLAN.md is executable, IDEAS.md is a scratchpad)

- [x] T-000329: [BATCH] MAINTAIN.md parser and maintain mode
  - [x] T-000318: Create a MAINTAIN.md parser that reuses the existing checklist parser. Each entry is an invariant (a statement of desired state), not a task
  - [x] T-000319: Add an `mcloop maintain` subcommand that loops over MAINTAIN.md entries independently. Each invariant gets its own Claude Code session
  - [x] T-000320: The maintenance prompt must instruct the session to: check whether the invariant holds, fix it if not, run project checks, and report one of three outcomes: satisfied, fixed, or failed
  - [x] T-000321: Failure of one invariant must not stop the run. Each invariant is independent. Continue to the next on failure
  - [x] T-000322: On `fixed`, commit with a message like `maintain: <invariant text>`. On `satisfied`, do nothing. On `failed`, surface the failure in the run summary
  - [x] T-000323: When the session needs human judgment, send a Telegram message with the question via the same PreToolUse hook flow used elsewhere. The 10-minute hook ceiling applies, same as everywhere else in mcloop
  - [x] T-000324: If no Telegram reply within 10 minutes, the session proceeds with its best independent judgment and notes the autonomous decision in the commit message
  - [x] T-000325: Write all maintain decisions (satisfied, fixed, failed, autonomous) to .mcloop/maintain-log.json for post-hoc audit
  - [x] T-000326: Print a maintain run summary at the end: X satisfied, Y fixed, Z failed, and a list of any autonomous decisions made without user confirmation
  - [x] T-000327: Maintain mode is a distinct lifecycle from the PLAN.md run_loop. Implement it as a separate code path in main.py, not by overloading run_loop
  - [x] T-000328: Document MAINTAIN.md in the README, contrasting it with PLAN.md (PLAN.md is a feature backlog, MAINTAIN.md is a list of invariants)

- [x] T-000333: [BATCH] First proof-of-concept maintain invariant
  - [x] T-000330: Add a single concrete, checkable invariant to mcloop's own MAINTAIN.md to validate the mechanism. Suggested: "All top-level modules in mcloop/ are listed in CLAUDE.md" or "pyproject.toml requires Python 3.11 or newer"
  - [x] T-000331: Run `mcloop maintain` against mcloop itself to verify the invariant is detected and either reported as satisfied or fixed
  - [x] T-000332: Iterate on the prompt template until the satisfied/fixed/failed outcomes are reliably distinguishable

- [x] T-000339: [BATCH] DeepSeek model currency invariant
  - [x] T-000334: Add a maintain invariant that ensures mcloop.json uses the most capable current DeepSeek model available on OpenRouter
  - [x] T-000335: The maintenance session must be allowed to use WebFetch to query the OpenRouter model catalog
  - [x] T-000336: The prompt must define "most capable" concretely enough to be checkable: prefer the highest version number in the deepseek-v family, breaking ties by recency of release
  - [x] T-000337: When the choice is ambiguous (multiple new models with tradeoffs), use the Telegram ask-then-fall-back-to-autonomous flow from the maintain mechanism
  - [x] T-000338: Update mcloop.json and run the project checks before committing. If checks fail with the new model, roll back and report failed

## Stage 8: Structured run artifacts
<!-- phase_id: phase_008 -->

- [x] T-000340: [BATCH] Per-run structured summary written to disk
- [x] T-000346: Define a run-summary schema covering: run start/end timestamps, total elapsed seconds, mode (plan, bug-only, maintain), per-task entries (label, text, outcome, elapsed, model, attempts, commit hash if any), per-check entries (command, passed, elapsed), full-suite check result, build result, audit result (if run), terminal status (success, failure, interrupted), failure detail string, and any stuck task list
  - [x] T-000341: At the end of every run_loop() invocation, write a dated file to .mcloop/runs/YYYYMMDD_HHMMSS_run-summary.json containing the schema above. The file must be written on all exit paths: success, failure, interrupted, terminal failures from any source
  - [x] T-000342: Also maintain .mcloop/runs/latest.json as a copy of the most recent summary, so automation has a stable filename to read
  - [x] T-000343: Capture commit hashes for every commit produced during the run (per-task commits, batch commits, audit fix commits, maintain commits) by extending the existing _commit() flow to return the new HEAD hash
  - [x] T-000344: Document the run summary schema and the .mcloop/runs/ directory in the README, including the latest.json convention
  - [x] T-000345: Add tests covering: a successful run produces a complete summary, a failed run still produces a summary with the failure detail, an interrupted run produces a summary, all expected fields are populated

## Stage 9: Checkpoint flags for unattended runs
<!-- phase_id: phase_009 -->

- [x] T-000353: [BATCH] First-class stop-after controls
  - [x] T-000347: Add `--stop-after-stage` CLI flag. When set, mcloop runs through the current stage normally (full-suite check, build, stage-complete notification), then exits cleanly with success status instead of advancing into the next stage. The summary and notification must clearly indicate the run stopped because of the flag, not because tasks ran out
  - [x] T-000348: Add `--stop-after-one` CLI flag. When set, mcloop runs exactly one checkable leaf task and then exits. If the next task is part of a `[BATCH]` parent, the batching logic must be bypassed for that single task: run only the one task in its own session, commit it normally, then exit. Do not run the rest of the batch
  - [x] T-000349: Both flags must produce a distinct exit notification (e.g. "Stopped after stage as requested" or "Stopped after one task as requested") so the user can distinguish a checkpoint exit from a normal run completion or a failure
  - [x] T-000350: `--stop-after-stage` is meaningless in bug-only mode (no stages). When both bug-only mode and `--stop-after-stage` apply, print a warning and ignore the flag. `--stop-after-one` works in all modes including bug-only and maintain
  - [x] T-000351: The stop check must happen at a clean boundary: after a successful commit and check-off, before pulling the next task. Never stop mid-task or mid-batch
  - [x] T-000352: Document both flags in the README with concrete use cases (overnight runs with tighter human checkpoints, inspecting one change at a time, validating a single stage before continuing)

## Stage 10: Multi-model executor support
<!-- phase_id: phase_010 -->

- [x] T-000360: [BATCH] Per-role model configuration
  - [x] T-000354: Define a new config schema in ~/.mcloop/config.json that separates model config by role: executor (coding tasks), sync (NOTES.md summarization), reviewer (background code review), with independent model, provider, and fallback settings per role. The old flat "model" and "reviewer" keys must continue to work as before when the new schema is absent
  - [x] T-000355: Add shell function templates (deepseek, kimi) to README documenting the ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_MODEL, ANTHROPIC_DEFAULT_*_MODEL, CLAUDE_CODE_SUBAGENT_MODEL, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, and ENABLE_TOOL_SEARCH environment variables needed to route Claude Code through third-party Anthropic-compatible endpoints
  - [x] T-000356: Update _build_command in runner.py to read the executor role config and set the appropriate environment variables on the subprocess when the model string indicates a non-Anthropic provider (deepseek/*, moonshotai/*, openai/*)
  - [x] T-000357: Decouple sync and reviewer model config: claude_md_check.py currently reads from the shared reviewer.* stanza. Give sync its own config key (sync.model, sync.provider, sync.fallback) so users can run cheap models for sync and better models for review independently
  - [x] T-000358: Make the Sonnet fallback in claude_md_check.py configurable via sync.fallback.model instead of hardcoding "sonnet". Preserve the claude -p subprocess fallback path as default when no fallback is explicitly configured
  - [x] T-000359: Add the model strings "deepseek-v4-pro", "deepseek-v4-flash", "kimi-k2.6" to _KNOWN_MODELS in runner.py with appropriate provider mappings

- [x] T-000376: [BATCH] Bug fixes discovered by multi-model audit (Kimi K2.6, DeepSeek V4 Pro, Claude Opus, GPT-5.5)
  - [x] T-000361: main.py:1511 -- bug-only verification failure silently returns success. When _launch_app_verification() returns a failure string after all BUGS.md tasks are checked off, set terminal_failure so the run exits with failure status instead of reporting success. Include a test that verifies a failed app launch produces RunStatus("failure"). Discovered independently by Kimi (Critical), Claude (High), and Codex (High)
  - [x] T-000362: audit.py:224 -- bidirectional substring match in REMOVED verdict filter silently drops confirmed bugs whose titles share a substring with an unrelated REMOVED header. Replace with exact match or anchored comparison. Include a test with line numbers that are prefixes of each other (e.g., line 4 and line 42). Discovered by DeepSeek (High) and Codex (Medium-High)
  - [x] T-000363: claude_md_sync.py:89 -- background NOTES.md sync daemon thread can write to the working tree after the next task has already started, contaminating change detection and commits. Ensure sync completes or is fenced before the next task begins. Include a test that verifies no working tree mutation occurs after handle_sync returns. Discovered by Claude (High)
  - [x] T-000364: wrap.py:695 -- Swift wrapper injects crash handlers but never calls them in @main App structs that lack an explicit init(). Synthesize an init() when none exists, or use an alternative injection point. Include a test with a minimal SwiftUI @main struct without init(). Discovered by Claude (High)
  - [x] T-000365: checks.py:95 -- run_autofix crashes with FileNotFoundError when ruff is not on PATH. Catch FileNotFoundError alongside TimeoutExpired. Include a test that patches subprocess.run to raise FileNotFoundError. Discovered by Kimi (High) and DeepSeek (Medium)
  - [x] T-000366: prompts.py:497 -- bugs_md_has_bugs returns false negative when audit report contains "No bugs found." as substring. Use a more robust check such as parsing for actual bug entry markers. Include a test with a bug description containing the literal phrase "No bugs found.". Discovered by Kimi (Medium) and DeepSeek (Medium)
  - [x] T-000367: checks.py:188 -- try_salvage_style_failures noqa regex matches comments containing "noqa" as a substring, corrupting comment text. Anchor the regex to match only standalone # noqa pragmas. Include a test with a comment like "# This is a noqa-like workaround". Discovered by Kimi (Medium)
  - [x] T-000368: runner.py:681 -- permission-denial kill raises ProcessLookupError if the subprocess has already exited. Catch ProcessLookupError. Discovered by Kimi (Medium)
  - [x] T-000369: wrap.py:622 -- strip_markers removes user code lines that contain marker strings inside string literals or comments. Use line-start anchoring or a state machine to distinguish real markers from incidental matches. Discovered by Kimi (Medium)
  - [x] T-000370: wrap.py:519 -- _detect_from_extensions follows directory symlinks via rglob, causing infinite recursion on circular symlinks. Pass follow_symlinks=False or filter symlinks. Discovered by Kimi (Medium)
  - [x] T-000371: app_interact.py:146 -- screencapture return code and stderr are not checked, silently ignoring permission denial or invalid window id. Check the return code and propagate failure. Discovered by Claude (Medium)
  - [x] T-000372: checks.py:288 -- check_timeout crashes with ValueError on non-integer config values. Wrap in try/except or validate at config load time. Discovered by Kimi (High)
  - [x] T-000373: worktree.py:101 -- exists() uses substring match against worktree path instead of exact branch name comparison. Align with create() which correctly compares branch names. Discovered by Codex (Medium)
  - [x] T-000374: checklist.py:607 -- clear_failed_markers regex replaces "- [!]" on any line, not just checkbox lines, corrupting non-checkbox content. Anchor the match to checkbox syntax. Discovered by Kimi (Lower)
  - [x] T-000375: runner.py:624 -- log truncation drops the beginning of sessions producing more than 100k lines. Either keep both head and tail, or rotate to a file earlier. Discovered by Kimi (Medium)

## Stage 11: Workspace-context adaptation - defensive guard
<!-- phase_id: phase_011 -->

This stage and the next nine adapt McLoop so it operates correctly when the four sibling repos (mcloop, duplo, orchestra, bob-tools) are consolidated into one bob workspace as `packages/<name>/` subdirectories. The adaptation runs in compatibility mode: every change must leave existing single-repo behavior provably unchanged. The proof is that all existing tests pass throughout, plus new tests that exercise the single-repo case explicitly against the new code paths.

The load-bearing primitive introduced by this work is the `WorkspaceContext` object, a small dataclass with five fields: `workspace_root` (the git root and home of the everything log), `scope` (either `"root"` or a package name), `scope_root` (the directory holding the scope's state files), `execution_cwd` (where tests, builds, and check commands run), and `plan_path` (the specific PLAN.md being advanced this run). On a standalone repo, `workspace_root == scope_root == execution_cwd` and `scope == "root"`; this is the compatibility-mode invariant.

The reason this matters: McLoop today derives `project_dir` from the PLAN file's parent and treats it as the unified "this is where everything happens" directory. It writes logs, bugs, checks, `.mcloop/`, ledger config, git operations, and run summaries against `project_dir`. Crucially, `_ensure_git` in `git_ops.py` initializes a new git repo if `project_dir/.git` is absent. Post-consolidation, running mcloop from `packages/orchestra/` would create a nested git repo inside the bob workspace -- exactly the failure mode this adaptation prevents.

The work is not just adapting git operations and subcommand dispatch. The core run loop (`run_loop` in `main.py`), the task runner (`runner.py`), the code-edit backend (`code_edit.py`), checks, builds, logging, NOTES sync, CLAUDE sync, the reviewer, the dependency preflight, and the auto-wrap path all use `project_dir` today. Every one of these must take a `WorkspaceContext` and route paths to the appropriate field (`workspace_root` for git, `scope_root` for state, `execution_cwd` for tests and builds). Leaving any of these on `project_dir` would mean package-scoped runs operate through the old model and the consolidation is incomplete.

Cross-stage constraints (apply to every task in stages 11 through 20):

- Do not modify `_ensure_git` to create a git repository in any directory other than the resolved `workspace_root`. The consolidation failure mode this entire migration prevents is the creation of nested `.git` directories inside `packages/<name>/`. If a task makes `_ensure_git` look at any path other than `workspace_root`, the task is wrong.
- Do not introduce a `project_dir`-vs-`workspace_root` split where some call sites take the new abstraction and others still take `project_dir`. The adaptation must be coherent at every intermediate state. If a call site receives a `WorkspaceContext`, its callees that need workspace-rooted paths must receive a `WorkspaceContext` (or the appropriate field) too.
- Do not silently fall back to old behavior when `WorkspaceContext` resolution fails or returns ambiguous values. Silent fallback would mask consolidation bugs. Failure must be loud, structured, and refuse to proceed.
- Do not break the compatibility-mode invariant. On a standalone repo, `workspace_root == scope_root == execution_cwd` must hold.
- Do not derive standalone scope from cwd when an explicit `--file` or `--plan-path` points at a different `PLAN.md`. Current McLoop's compatibility behavior is plan-parent based: `mcloop --file /other/repo/PLAN.md` resolves to `/other/repo/`. The new resolver must preserve that behavior. When cwd is inside a workspace and plan_path is outside it (or vice versa), the resolver must fail loudly rather than guess.
- Do not leave any current `project_dir` reference unaudited. Every surviving path parameter must be renamed or explicitly documented as one of `workspace_root`, `scope_root`, `execution_cwd`, `state_root` (= `scope_root/.mcloop`), or `plan_path`. Tests must assert the chosen mapping for every adapted call site.
- Do not stop after adapting git operations and subcommands. The main run loop, the task runner, the code-edit backend, checks/build execution, logs, NOTES sync, CLAUDE sync, reviewer state, dependency preflight, and auto-wrap/reinject are part of this migration.
- Do not omit the `CLAUDE.md` task-context resolver. The architecture requires root-plus-package context assembly with explicit precedence classes. Relying on the Claude Code session's own ancestor search for CLAUDE.md is the old behavior and is not sufficient for the consolidated layout.
- Do not treat the ledger relocation as only a directory change. The everything-log schema migration (adding `scope`, `plan_path`, structured `task_id`, `parent_task_id`, and `failure_record_id` fields) lives in bob-tools and is out of scope for this McLoop work. If those fields are not available when McLoop emits ledger events from a package-scoped run, McLoop must fail closed rather than write ambiguous events.

Stage 11 itself lands an immediate protective check that makes the nested-repo failure mode loud rather than silent. It does not introduce `WorkspaceContext` yet -- it is a narrow defensive change that buys time even if the rest of this migration stalls. The guard is retired in Stage 14 once `_ensure_git` becomes WorkspaceContext-aware.

- [x] T-000377: Add a `_refuse_nested_init` check at the top of `_ensure_git` in `mcloop/git_ops.py`. The guard walks upward from `project_dir` looking for a `pyproject.toml` whose contents contain `[tool.uv.workspace]`. If such a file is found at any strict ancestor of `project_dir`, the guard refuses to proceed with `git init` and raises a structured error naming the workspace root and instructing the user to run mcloop from the workspace root instead. The guard does NOT use ad-hoc patterns like "ancestor named `packages`" -- the workspace pyproject declaration is the only authoritative signal that `project_dir` is inside a bob-style monorepo. The function exits 1 on this refusal, as it does on git-init failure today.
- [x] T-000378: Unit tests in `tests/test_git_ops.py` covering the new guard. Required cases: standalone repo with no `.git` and no workspace pyproject in any ancestor proceeds normally and creates a new repo; standalone repo with `.git` returns early without calling git init; workspace layout with `bob/pyproject.toml` declaring `[tool.uv.workspace]`, `bob/.git` present, and a `packages/orchestra/` directory without `.git` raises the structured error and does not create a nested `.git`; a standalone repo that contains a `packages/` directory for its own reasons (not bob-style) and has no workspace pyproject does NOT block (the false-positive case the pyproject-based signal is designed to avoid); a pyproject.toml at an ancestor that does NOT declare `[tool.uv.workspace]` does NOT block (the signal is specifically the workspace declaration, not the pyproject presence).

## Stage 12: Workspace-context adaptation - WorkspaceContext primitive
<!-- phase_id: phase_012 -->

This stage introduces the abstraction without changing any existing behavior. Nothing in McLoop's call paths uses `WorkspaceContext` yet; the resolver is exercised by tests only.

- [x] T-000379: Create `mcloop/workspace_context.py` defining the `WorkspaceContext` dataclass with five fields: `workspace_root: Path`, `scope: str`, `scope_root: Path`, `execution_cwd: Path`, `plan_path: Path`. The dataclass is frozen and has a `__post_init__` that asserts the compatibility-mode invariant when `scope == "root"` (`workspace_root == scope_root == execution_cwd`).
- [x] T-000380: Add `resolve_workspace_context(cwd, plan_path, *, workspace_override=None, scope_override=None) -> WorkspaceContext` to the same module. Resolution rules in order: (1) if `workspace_override` is given, use it as `workspace_root` directly, otherwise walk upward from the anchor looking for a directory that contains both a `.git` directory and a `pyproject.toml` declaring `[tool.uv.workspace]`; if no such ancestor is found, the standalone case applies. (2) The anchor for the upward walk is `plan_path.parent` when `plan_path` is given explicitly, otherwise `cwd` -- this preserves current behavior where `mcloop --file /other/repo/PLAN.md` operates against `/other/repo/` regardless of cwd. (3) Ambiguity check: if `plan_path` is explicit and `cwd` is inside a workspace different from the one containing `plan_path.parent`, raise structured error; same if `workspace_override` disagrees with the ancestor walk. (4) Standalone case (no workspace ancestor found): set `workspace_root = scope_root = execution_cwd = plan_path.parent` if plan_path is given, else `cwd`; `scope = "root"`; `plan_path` defaults to `scope_root/PLAN.md`. (5) Consolidated case, workspace ancestor found: if anchor is `workspace_root` itself then `scope = "root"` and `scope_root = workspace_root`; if anchor is `workspace_root/packages/<name>/...` then `scope = "<name>"` and `scope_root = workspace_root/packages/<name>`; other layouts are rejected with structured error; `execution_cwd = cwd`; `plan_path` defaults to `scope_root/PLAN.md`. (6) `scope_override`, if given, must match the resolved scope or the resolver raises structured error.
- [ ] T-000381: Unit tests in `tests/test_workspace_context.py` covering every resolution path: standalone cwd-anchored; standalone plan_path-anchored; standalone with cwd and plan_path.parent disagreeing (resolver picks plan_path.parent since the explicit input wins); workspace ancestor exists with cwd at workspace root (scope=root); workspace ancestor with cwd inside `packages/orchestra/` (scope=orchestra); workspace exists with plan_path explicit and inside a DIFFERENT workspace (structured error); workspace exists with `--scope` override matching resolved scope (accepted); workspace exists with `--scope` override disagreeing (structured error); `--workspace` override matches (accepted); `--workspace` override disagrees with ancestor walk (structured error). Compatibility-mode invariant is asserted in every standalone case.

## Stage 13: Minimal consolidation fix - git operations work from package subdirectories
<!-- phase_id: phase_013 -->

This stage lands the minimum change needed for McLoop to operate correctly when run from a package subdirectory of a consolidated bob workspace. Two surgical changes to `mcloop/git_ops.py`. First, `_ensure_git` walks upward from `project_dir` looking for an existing `.git` entry; if any ancestor has one, the function returns early without printing or initializing. This prevents the nested-`.git` failure mode that consolidation would otherwise create. Second, the path-emitting git helpers (`_changed_files`, `_committed_files`, `_get_committed_diff`, `_worktree_status`, `_snapshot_worktree`) pass `--relative` to their underlying git invocations so emitted paths stay relative to the subprocess `cwd` rather than the actual repo root. This preserves the existing contract with callers like `run_checks`, which expect changed-file paths to be usable from the cwd they passed in.

All other git helpers (`_checkpoint`, `_push_or_die`, `_stage_safe`, `_commit`, `_has_meaningful_changes`, `_has_uncommitted_changes`, `_get_diff`, `_get_git_hash`) require no change because git itself walks upward from any subdirectory to find the repo root. A `git commit` invoked from `packages/mcloop/` commits to the workspace repo correctly without any McLoop-side awareness of the workspace root.

The Stage 11 `_refuse_nested_init` guard becomes redundant after T-000382 lands (because `_ensure_git` returns early before the guard would fire), but the guard is left in place as defense-in-depth.

The larger architectural migration (`WorkspaceContext` primitive, scope/execution_cwd separation, `CLAUDE.md` task-context resolver, ledger relocation) is deferred indefinitely. It would improve the system but is not required to unblock consolidation.

- [ ] T-000382: Modify `_ensure_git` in `mcloop/git_ops.py` to walk upward from `project_dir` looking for a `.git` entry (file or directory -- `.git` can be a file in worktrees). If any strict ancestor of `project_dir` has `.git`, return early without printing the warning or running `git init`. Existing behavior preserved: if `project_dir/.git` itself exists, return early as today; if neither `project_dir` nor any ancestor has `.git`, fall back to current behavior (print the warning, run `git init`, handle the result). Unit tests in `tests/test_git_ops.py`: standalone repo with no `.git` and no workspace ancestor initializes as today; standalone repo where `project_dir/.git` exists returns early as today; consolidated layout where `project_dir = workspace/packages/mcloop` and `workspace/.git` exists returns early without creating a nested `.git`; the existing `_refuse_nested_init` guard from Stage 11 continues to pass its tests (the guard becomes a defense-in-depth backstop but does not regress).
- [ ] T-000383: Modify `_changed_files`, `_committed_files`, `_get_committed_diff`, `_worktree_status`, and `_snapshot_worktree` in `mcloop/git_ops.py` to pass `--relative` to their underlying `git diff` / `git status` / `git show` invocations. This makes emitted paths relative to the subprocess `cwd` regardless of where the actual repo root is. On a standalone layout (where `cwd` is already the repo root) the output is unchanged. On a consolidated layout (where `cwd` is `packages/mcloop` and the repo root is the workspace), `--relative` keeps paths package-relative as callers (`run_checks`, `handle_sync`, batch rollback) expect. Unit tests in `tests/test_git_ops.py`: standalone fixture, output unchanged from current behavior; consolidated fixture (`workspace/.git` exists, cwd = `workspace/packages/mcloop`, a file `workspace/packages/mcloop/foo.py` modified) -- `_changed_files` returns `["foo.py"]` not `["packages/mcloop/foo.py"]`, `_worktree_status` lines reference `foo.py` not `packages/mcloop/foo.py`, and round-trip with `run_checks` resolves to a path that exists at `cwd/foo.py`.
