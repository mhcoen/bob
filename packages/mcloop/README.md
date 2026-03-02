# Loop

Loop grinds through a markdown checklist using AI coding CLIs, committing as it goes and notifying you of progress.

You write a `PLAN.md` in your repo — a project description followed by a checklist. Loop picks up the next unchecked item, launches a fresh Claude Code session to do it, runs your project's tests and linter, and only commits and checks off the item if everything passes. Task status is tracked in `PLAN.md`; per-attempt logs go to `logs/`.

## Usage

```bash
python -m loop                    # Start (reads PLAN.md by default)
python -m loop --file other.md    # Use a different file
python -m loop --dry-run          # Show what would run without doing anything
python -m loop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
```

## Writing a PLAN.md

A `PLAN.md` has two parts: a project description in plain English, then a checklist. The description gives the CLI context for every task — what the project is, what technologies to use, any constraints. The checklist is what Loop executes.

You can write it yourself, or have an AI generate it:

```
Prompt: "Write a PLAN.md for a CLI tool that converts CSV files to JSON.
         Python, no dependencies, with tests. Break it into small tasks."
```

### Example

```markdown
# csv2json

A CLI tool that reads CSV files and outputs JSON. Python 3.11+, no external
dependencies. Use argparse for the CLI, csv module for parsing. Output should
be pretty-printed by default with a --compact flag. Include tests with pytest.

- [ ] Set up project scaffolding (pyproject.toml, src layout, empty test file)
- [ ] Implement CSV-to-dict parsing with type inference (int, float, string)
- [ ] Add CLI with argparse (input file, --output, --compact flags)
- [ ] Add --array mode (list of lists) vs default --object mode (list of dicts)
- [ ] Handle edge cases (empty files, missing values, quoted commas)
- [ ] Write tests for each conversion mode and edge case
```

### Markers

| Marker | Meaning |
|--------|---------|
| `- [ ]` | Pending — Loop will pick this up |
| `- [x]` | Completed |
| `- [!]` | Failed — Loop gave up after max retries |

When a parent has subtasks, Loop completes the subtasks first. The parent is auto-checked when all children are done.

## How it works

```
while unchecked items remain:
    1. Parse PLAN.md, find next unchecked item (depth-first)
    2. Launch a fresh Claude Code session with the project description + task
    3. Run project checks (tests, lint — auto-detected)
    4. If checks pass  -> commit, check the box, notify, continue
    5. If checks fail  -> retry (up to --max-retries)
    6. If retries exhausted -> mark [!], notify, stop
    7. If rate-limited  -> pause, wait for reset, resume
```

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
```

Or set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables.

### iMessage

Works automatically on macOS via Messages.app (osascript).

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
- macOS (for iMessage notifications; Telegram works anywhere)

## Author

**Michael H. Coen**
Email: mhcoen@gmail.com | mhcoen@alum.mit.edu
GitHub: [@mhcoen](https://github.com/mhcoen)
