# McLoop

McLoop lets you run Claude Code (or Codex) for hours at a time without babysitting it. You write a task list in `PLAN.md`. McLoop works through it continuously, launching a fresh CLI session per task, running your tests and linter, committing only if everything passes, and notifying you of progress. When it needs authorization to run a command, it alerts you in real time via Telegram so you can approve from your phone and it keeps going.

Each session starts with a clean context, with no memory of previous sessions. The CLI sees your project description, the current task, and whatever is in your codebase: source files, markdown docs, tests, configuration. That's it. Good results depend on the code and docs in your repo being the source of truth, not on conversation history.

McLoop is designed for the long haul. Start with a few tasks, let it run while you do something else, add more tasks when you think of them, re-run. It's a persistent task queue backed by a text file, not a one-shot build script.

## Quickstart

```bash
python -m mcloop                    # Run (reads PLAN.md by default)
python -m mcloop --file other.md    # Use a different file
python -m mcloop --dry-run          # Show what would run, don't execute
python -m mcloop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
```

## Writing a PLAN.md

A `PLAN.md` has two parts: a **project description**, then a **checklist**.

```markdown
# McLoop

A Python CLI that grinds through a markdown checklist using AI coding CLIs.
Read PLAN.md, find the next unchecked task, launch a fresh CLI session to do
it, run the project's tests and linter, commit if everything passes, check off
the item, and repeat. Notify the user via Telegram and iMessage on completions,
failures, and rate limits.

Python 3.11+, stdlib only, no external dependencies. Ruff for linting, pytest
for tests. Each task should leave the repo in a passing state: ruff check and
pytest must both pass before a commit is made. Prefer small, focused changes
per task. Write unit tests for new functionality. Keep modules short and avoid
over-abstraction. This is a simple tool and should stay that way.

- [ ] Project scaffolding (pyproject.toml, .gitignore, loop package, __main__.py)
- [ ] Markdown checklist parser (parse tasks, find next unchecked, check off items)
- [ ] Telegram and iMessage notifications
- [ ] Auto-detect and run project test/lint suites
- [ ] Rate limit detection and CLI fallover
- [ ] CLI subprocess runner with logging
- [ ] Main loop: parse, execute, verify, commit, notify, repeat
```

This is the PLAN.md that was used to build McLoop itself.

The description runs from the top of the file down to the first checkbox. It's
included in every CLI invocation, so every session has context about what the
project is, what technologies to use, and what constraints matter. **Without a
description, the CLI has no context and will make worse decisions.**

Because each session starts fresh, the CLI can only work from what's in the
repo at that moment. Keep your description, inline comments, and any other
markdown docs current. They are the CLI's only memory of decisions made in
previous sessions.

The checklist is what McLoop executes. Each item should be a meaningful unit of
work, such as a feature, a subsystem, or a named refactor, not a single function
or line.

**You don't have to write the whole checklist upfront.** McLoop picks up
wherever you left off. Add tasks as you think of them, reorder them, break
them into subtasks. When McLoop finishes the current queue, just add more and
re-run. This makes it equally useful for iterative refinement of an existing
codebase as for building something from scratch.

### Subtasks

Nest subtasks with indentation. McLoop completes children before parents, and
auto-checks the parent when all children are done.

```markdown
- [ ] Set up database
  - [ ] Create users table
  - [ ] Create sessions table
  - [ ] Add indexes
- [ ] Write login endpoint
```

### Task markers

| Marker | Meaning |
|--------|---------|
| `- [ ]` | Pending. McLoop will pick this up. |
| `- [x]` | Completed |
| `- [!]` | Failed. McLoop gave up after max retries. |

You can manually edit any marker. To retry a failed task, change `[!]` back to
`[ ]` and re-run.

## How McLoop works

```
while unchecked items remain:
    1. Find next unchecked item (depth-first)
    2. Launch a fresh Claude Code (or Codex) session with a clean context.
       The CLI receives: project description + current task + your codebase.
    3. Run project checks (tests, lint, auto-detected from project files)
    4. If checks pass  -> commit, check the box, notify, continue
    5. If checks fail  -> retry (up to --max-retries)
    6. If retries exhausted -> mark [!], notify, stop
    7. If rate-limited -> pause, wait for reset, resume
```

McLoop stops when a task fails all retries. It does not continue to the next
task, since tasks may have implicit dependencies.

## Unattended operation

McLoop is built to run without interaction. The recommended setup uses Claude
Code's sandbox mode combined with the included permission hook.

**Sandbox mode** (`"sandbox": {"enabled": true}` in `settings.json`) restricts
what Claude Code can do. Network access is limited to an allowlist of domains,
and filesystem writes outside the project require explicit permission. This means
McLoop can run for hours without you watching it and can't do anything
catastrophic by accident.

**The permission hook** (`telegram-permission-hook.py`) intercepts every tool
call Claude Code makes as a `PreToolUse` hook:

- **Whitelisted commands** (in `permissions.allow`) pass through automatically.
- **Everything else** sends you a Telegram message describing exactly what Claude
  Code wants to do, then pauses and waits for your approval.

To approve or deny from your phone, use the **Remote Control** feature in the
Claude Code mobile app. McLoop resumes immediately once you respond.

### Setup

Copy `settings.example.json` from this repo to `~/.claude/settings.json` (or
merge it with your existing settings), then update the hook path:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/mcloop/telegram-permission-hook.py",
            "timeout": 600000
          }
        ]
      }
    ]
  }
}
```

The timeout is 10 minutes, which is enough time to pick up your phone and
respond. Add any commands you always trust to `permissions.allow` so they pass
through without a notification. See `settings.example.json` for a recommended
baseline.

## Notifications

McLoop sends Telegram and iMessage notifications for task completions, failures,
rate limits, and when all tasks are done.

Create `~/.claude/telegram-hook.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
IMESSAGE_ID=your-phone-or-email
```

All fields are optional. Telegram requires both `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`. iMessage requires `IMESSAGE_ID` (phone number or Apple ID)
and only works on macOS. You can also set these as environment variables.

## Project checks

McLoop auto-detects what to run:

| File present | Command run |
|---|---|
| `pyproject.toml` with ruff config | `ruff check .` |
| `pyproject.toml` with pytest config | `pytest` |
| `package.json` with test script | `npm test` |
| `Makefile` | `make check` |

## Logging

One log file per task attempt in `logs/`, named `{timestamp}_{task-slug}.log`.
Each log captures the full CLI output and exit code.

## Requirements

- Python >= 3.11
- `claude` CLI on PATH
- macOS for iMessage notifications (Telegram works anywhere)

## Development

```bash
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## Author

**Michael H. Coen**  
mhcoen@gmail.com | mhcoen@alum.mit.edu  
[@mhcoen](https://github.com/mhcoen)
