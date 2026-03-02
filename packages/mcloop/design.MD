# Loop

Loop grinds through a markdown checklist using AI coding CLIs, committing as it goes and notifying you of progress.

## How it works

1. You write a markdown checklist (`TODO.md` in your repo) with `- [ ]` items
2. You run `loop`
3. Loop picks the next unchecked item, launches a fresh Claude Code (or Codex) session to do it, runs your project's tests/lint, commits if they pass, checks the box
4. Repeats until everything is done or remaining items are stuck
5. Sends you notifications (Telegram + iMessage) on completions, failures, and pauses

## Checklist format

Standard markdown checkboxes in a file in the repo (default: `TODO.md`). No special syntax.

```markdown
- [ ] Add user authentication
- [ ] Set up database migrations
  - [ ] Create users table
  - [ ] Create sessions table
- [ ] Write API endpoint for login
- [x] Initialize project structure
```

- Supports nested subtasks (indented checkboxes under a parent)
- Loop checks off items as they complete
- Already-checked items are skipped

## The loop

```
while unchecked items remain:
    1. Parse checklist, find next unchecked item (top-down order)
    2. Launch a fresh CLI session (Claude Code preferred, Codex as fallback)
    3. Give it the task description + relevant repo context
    4. When session finishes, run checks (tests, lint — whatever the project uses)
    5. If checks pass → commit, check the box, notify user, continue
    6. If checks fail → retry with failure context (up to max_retries)
       - Notify on each failure
       - After max retries, mark item as stuck and move on
    7. If rate-limited on current CLI → fall over to the other
       - If both limited → pause, wait for reset, notify user
```

## Subtask handling

When a parent item has nested subtasks:
- Complete all subtasks first (top-down)
- Parent is checked off only when all its children are done
- If any subtask gets stuck, the parent is stuck too

## Rate limit handling

- Detect when Claude Code or Codex hits its usage limit
- Fall over to the other CLI (Claude Code is preferred, Codex is fallback)
- If both are limited, pause and wait for whichever resets first
- Notify user on pause and resume

## Notifications

Telegram and iMessage notifications for:
- Task completed successfully
- Task failed (each retry, and when giving up)
- Rate-limited / paused / resuming
- All tasks done (or all remaining are stuck)

## Logging

- One log file per task attempt in a `logs/` directory
- Each log captures the CLI output and check results
- Nothing fancy — just dump everything to a file

## Tech

- Python
- Invokes `claude` and/or `codex` CLI via subprocess
- Keep it simple — stdlib where possible:
  - `argparse` for CLI
  - `subprocess` for running CLIs and checks
  - `json` for any state
  - `pathlib` for paths
  - `re` for parsing
- `ruff` for linting, `pytest` for testing (of Loop itself)

## Defaults

- `max_retries`: 3
- CLI preference: Claude Code first, Codex fallback
- Checklist file: `TODO.md`
- Log directory: `logs/`
