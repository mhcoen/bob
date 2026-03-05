# McLoop

McLoop lets you run Claude Code for hours at a time without babysitting it.
You write a task list in PLAN.md. McLoop works through it continuously,
launching a fresh CLI session per task, running your tests and linter,
committing only if everything passes, and notifying you of progress. When it
needs authorization to run a command, it alerts you in real time via Telegram
so you can approve from your phone and it keeps going.

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
