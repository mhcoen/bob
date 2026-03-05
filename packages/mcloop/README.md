# Loop

Loop grinds through a markdown checklist using AI coding CLIs, committing as it goes and notifying you of progress.

You write a `PLAN.md` in your repo with a project description and a checklist. Loop picks up the next unchecked item, launches a fresh Claude Code session to do it, runs your project's tests and linter, and only commits and checks off the item if everything passes. Task status is tracked in `PLAN.md`. Per-attempt logs go to `logs/`.

## Usage

```bash
python -m mcloop                    # Start (reads PLAN.md by default)
python -m mcloop --file other.md    # Use a different file
python -m mcloop --dry-run          # Show what would run without doing anything
python -m mcloop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
```

## Writing a PLAN.md

A `PLAN.md` has two parts: a project description in plain English, then a checklist. The description gives the CLI context for every task: what the project is, what technologies to use, any constraints. The checklist is what Loop executes.

You can write it yourself, or have an AI generate it:

```
Prompt: "Write a PLAN.md for a CLI tool that converts CSV files to JSON.
         Python, no dependencies, with tests. Start with a high-level
         project description, then break it into a natural sequence of
         feature-level tasks as markdown checkboxes. Each task should be
         a meaningful unit of work (e.g. 'add input validation'), not a
         single function or line of code."
```

### Example

This is the PLAN.md that was used to build Loop itself:

```markdown
# Loop

A Python CLI that grinds through a markdown checklist using AI coding CLIs.
Read PLAN.md, find the next unchecked task, launch a fresh CLI session to do
it, run the project's tests and linter, commit if everything passes, check off
the item, and repeat. Notify the user via Telegram and iMessage on completions,
failures, and rate limits. Python 3.11+, stdlib only, ruff for linting, pytest
for tests.

- [ ] Project scaffolding (pyproject.toml, .gitignore, loop package, __main__.py)
- [ ] Markdown checklist parser (parse tasks, find next unchecked, check off items)
- [ ] Telegram and iMessage notifications
- [ ] Auto-detect and run project test/lint suites
- [ ] Rate limit detection and CLI fallover
- [ ] CLI subprocess runner with logging
- [ ] Main loop: parse, execute, verify, commit, notify, repeat
```

### Markers

| Marker | Meaning |
|--------|---------|
| `- [ ]` | Pending. Loop will pick this up |
| `- [x]` | Completed |
| `- [!]` | Failed. Loop gave up after max retries |

When a parent has subtasks, Loop completes the subtasks first. The parent is auto-checked when all children are done.

## How it works

```
while unchecked items remain:
    1. Parse PLAN.md, find next unchecked item (depth-first)
    2. Launch a fresh Claude Code session with the project description + task
    3. Run project checks (tests, lint, auto-detected)
    4. If checks pass  -> commit, check the box, notify, continue
    5. If checks fail  -> retry (up to --max-retries)
    6. If retries exhausted -> mark [!], notify, stop
    7. If rate-limited  -> pause, wait for reset, resume
```

Each CLI session is instructed to write unit tests where they make sense. Loop then runs the project's test suite and linter before committing, so tasks only pass if the tests pass.

Loop stops when a task fails all retries. Tasks may have implicit dependencies, so continuing past a failure is not safe.

## Notifications

Loop sends Telegram and iMessage notifications for:

- Task completed
- Task failed (each attempt, and when giving up)
- Rate-limited / paused / resuming
- All tasks done

### Telegram setup

Create `~/.claude/telegram-hook.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
IMESSAGE_ID=your-phone-or-email
```

Or set these as environment variables. All are optional. Telegram needs both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. iMessage needs `IMESSAGE_ID` (a phone number or Apple ID email) and only works on macOS.

## Logging

One log file per task attempt in `logs/`, capturing CLI output and exit codes. Files are named `{timestamp}_{task-slug}.log`.

## Project checks

Loop auto-detects what to run based on project files:

| File | Commands |
|------|----------|
| `pyproject.toml` with ruff config | `ruff check .` |
| `pyproject.toml` with pytest config | `pytest` |
| `package.json` with test script | `npm test` |
| `Makefile` | `make check` |

## Development

```bash
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## Requirements

- Python >= 3.11
- `claude` CLI on PATH
- macOS (for iMessage notifications, Telegram works anywhere)

## Author

**Michael H. Coen**
Email: mhcoen@gmail.com | mhcoen@alum.mit.edu
GitHub: [@mhcoen](https://github.com/mhcoen)
