# Loop

Loop grinds through a markdown checklist using AI coding CLIs, committing as it goes and notifying you of progress.

You write a checklist. Loop works through it — one task at a time — launching a fresh Claude Code session for each item, running your project's tests, committing on success, and notifying you along the way.

## Usage

```bash
python -m loop                    # Start (reads TODO.md by default)
python -m loop --file tasks.md    # Use a different checklist
python -m loop --dry-run          # Show what would run without doing anything
python -m loop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
```

## Checklist format

Standard markdown checkboxes. Nesting is supported.

```markdown
- [ ] Add user authentication
- [ ] Set up database migrations
  - [ ] Create users table
  - [ ] Create sessions table
- [ ] Write API endpoint for login
- [x] Initialize project structure
```

Three markers:

| Marker | Meaning |
|--------|---------|
| `- [ ]` | Pending — Loop will pick this up |
| `- [x]` | Completed |
| `- [!]` | Failed — Loop gave up after max retries |

When a parent has subtasks, Loop completes the subtasks first. The parent is auto-checked when all children are done.

## How it works

```
while unchecked items remain:
    1. Parse checklist, find next unchecked item (depth-first)
    2. Launch a fresh Claude Code session with the task description
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
pytest                    # Tests (36 unit + integration)
```

## Requirements

- Python >= 3.11
- `claude` CLI on PATH
- macOS (for iMessage notifications; Telegram works anywhere)

## Author

**Michael H. Coen**
Email: mhcoen@gmail.com | mhcoen@alum.mit.edu
GitHub: [@mhcoen](https://github.com/mhcoen)
