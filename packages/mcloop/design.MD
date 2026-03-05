# mcloop

mcloop grinds through a markdown checklist using AI coding CLIs, committing as it goes and notifying you of progress.

## What it does

- You write a markdown checklist (`PLAN.md` in the repo) with `- [ ]` items, optionally nested as subtasks
- You run one command
- mcloop picks the next unchecked item, launches a fresh Claude Code (preferred) or Codex session to do it, runs tests/lint, commits if it passes, checks the box
- Repeats until everything is checked off or remaining items are stuck
- Sends you Telegram and iMessage notifications on completions, failures, and when it pauses/resumes

## Checklist format

Standard markdown checkboxes in a file in the repo (default: `PLAN.md`). No special syntax.

```markdown
- [ ] Add user authentication
- [ ] Set up database migrations
  - [ ] Create users table
  - [ ] Create sessions table
- [ ] Write API endpoint for login
- [x] Initialize project structure
```

- Supports nested subtasks (indented checkboxes under a parent)
- mcloop checks off items as they complete
- Already-checked items are skipped
- When a parent has subtasks, complete the subtasks first. Parent gets checked when all children are done

## The loop

```
while unchecked items remain:
    1. Parse checklist, find next unchecked item (depth-first, top-down)
    2. Launch a fresh CLI session (Claude Code preferred, Codex as fallback)
    3. Give it the task description + relevant repo context
    4. When session finishes, run checks (tests, lint, whatever the project uses)
    5. If checks pass → commit, check the box, notify user, continue
    6. If checks fail → retry with failure context (up to max retries)
       - Notify on each failure
       - After max retries, mark item as stuck and stop
    7. If rate-limited on current CLI → fall over to the other
       - If both limited → pause, wait for reset, notify user
```

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
- All tasks done

## Logging

- One log file per task attempt in a `logs/` directory
- Each log captures the CLI output and check results
- Nothing fancy, just dump everything to a file

## Tech

- Python
- Invokes `claude` and/or `codex` CLI via subprocess
- Keep it simple, stdlib where possible:
  - `argparse` for CLI
  - `subprocess` for running CLIs and checks
  - `json` for any state
  - `pathlib` for paths
  - `re` for parsing
- `ruff` for linting, `pytest` for testing (of mcloop itself)
