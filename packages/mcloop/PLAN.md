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

- [x] Add a safety commit to the main loop before processing any tasks
  - [!] In run_loop(), before the while loop, stage and commit all tracked modified files with a message like "mcloop: checkpoint before run"
  - [!] Skip if the working tree is clean
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
- [ ] Integration tests
  - [x] Add a tests/integration/ directory gated behind pytest -m integration
  - [x] Test a minimal run: temp git repo, simple PLAN.md, verify file created, task checked off, commit made
  - [x] Test no-op detection: task that produces no file changes is treated as failure
  - [x] Test subtask ordering: depth-first execution with parent auto-checking
  - [x] Test resume after kill: run partway, kill, restart, verify it picks up where it left off
  - [ ] Test failing task: verify retry behavior and [!] marking after max retries
