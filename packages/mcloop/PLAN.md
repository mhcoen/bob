# McLoop

McLoop lets you run Claude Code for hours at a time without babysitting it.
You write a task list in PLAN.md. McLoop works through it continuously,
launching a fresh CLI session per task, running your tests and linter,
committing only if everything passes, and notifying you of progress.

Python 3.11+, stdlib only, no external dependencies. Ruff for linting, pytest
for tests. Each task should leave the repo in a passing state: ruff check and
pytest must both pass before a commit is made. Prefer small, focused changes
per task. Write unit tests for new functionality. Keep modules short and avoid
over-abstraction. This is a simple tool and should stay that way.

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
- [ ] Pre-fix bug verification: after the audit writes BUGS.md, run a separate verification session that reads each reported bug and checks it against the actual source code. Remove any bug that is incorrect (code doesn't match the description, the issue was already handled, the bug is hypothetical). Print to the terminal: "Verifying N bugs..." then for each bug either "CONFIRMED: <file:line> <title>" or "REMOVED: <file:line> <title> (reason)". Rewrite BUGS.md with only confirmed bugs before the fix cycle runs. If all bugs are removed, skip the fix cycle and print "All reported bugs were false positives."
- [ ] Two-round audit cycle: run the full audit/verify/fix cycle twice. The second round catches bugs introduced by the first round's fixes. After the second round completes, save the audit hash and stop. Do not loop beyond two rounds.
- [ ] Non-destructive BUGS.md: mcloop audit must append new findings to an existing BUGS.md, not overwrite it. Include the audit prompt instruction to read the existing BUGS.md first and only report bugs not already listed.
- [ ] Fix ctrl-c/ctrl-z: claude -p takes over the terminal foreground process group, so ctrl-c is sent to claude instead of mcloop. After launching the subprocess with start_new_session=True, mcloop must reclaim the foreground process group with os.tcsetpgrp() so ctrl-c reaches mcloop's signal handler.
- [ ] Clearer terminal output: suppress individual tool calls (Read, Edit, Write, Glob, Grep, TodoWrite) entirely. Only print Bash commands. Add a progress indicator (a dot every few seconds) while a claude -p session is running so it's clear mcloop is alive. During task sessions, parse Claude Code's streaming text to extract conceptual descriptions of what it's doing and print those as clean status lines instead of raw tool calls. Example flow:
  - ">>> [TASK 13.2] Extracting frames from video..." followed by a brief description like "Reading video extractor and scanner modules" then progress dots, then "Creating video_extractor.py with ffmpeg scene detection" then more dots, then ">>> [TASK 13.2] Complete [2m 29s]"
  - ">>> [CHECKS] Running ruff check, pytest..." then dots, then ">>> [CHECKS] Passed"
  - ">>> [AUDIT] Scanning for bugs..." then dots, then a numbered list of found bugs with file, severity, and title
  - ">>> [VERIFY] Verifying N bugs..." then for each bug print CONFIRMED or REMOVED with a one-line reason
  - ">>> [FIX] Fixing N bugs..." then for each bug as it's fixed, print the bug title and a brief explanation of the fix
  - Keep Bash commands visible since they show meaningful actions
