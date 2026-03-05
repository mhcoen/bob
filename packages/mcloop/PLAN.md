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

- [ ] Support a mcloop.json config file for custom check commands
  - [ ] If mcloop.json exists with a "checks" array, run those commands instead of auto-detecting
  - [ ] Fall back to auto-detection when no config file is present
  - [ ] Document mcloop.json in the README
- [ ] After all checklist tasks are complete, automatically run a bug audit/fix cycle
  - [ ] Add a function that runs a Claude Code session to audit the codebase and write BUGS.md listing only actual defects (crashes, incorrect behavior, unhandled errors, security issues), not style or refactoring
  - [ ] If BUGS.md contains bugs, run a second session scoped to fixing only the bugs listed in BUGS.md, then delete BUGS.md
  - [ ] Run this cycle once (no open-ended looping), then send the "All tasks completed" notification
  - [ ] Add a --no-audit flag to skip the bug audit cycle
