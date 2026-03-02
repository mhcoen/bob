# Loop

Loop grinds through a markdown checklist using AI coding CLIs (Claude Code, Codex), committing as it goes and notifying you of progress.

## Quick reference

```bash
# Run
python -m loop                    # Start the loop
python -m loop --file tasks.md    # Use a different checklist file
python -m loop --dry-run          # Parse and show what would run

# Dev
ruff check .                      # Lint
ruff format --check .             # Format check
pytest                            # Tests
```

## Architecture

Loop is a single Python program with a straightforward flow:

1. **Parse** — Read a markdown checklist file, find the next unchecked `- [ ]` item
2. **Execute** — Launch a fresh Claude Code (or Codex) subprocess to do the task
3. **Verify** — Run the project's tests/lint checks
4. **Commit** — If checks pass, commit changes and check off the item
5. **Notify** — Send Telegram/iMessage notifications on completions, failures, pauses
6. **Repeat** — Go back to step 1

### Key modules

- `loop/` — Main package
  - `main.py` — Entry point, the main loop
  - `checklist.py` — Markdown checklist parser (read/write `- [ ]` items)
  - `runner.py` — Subprocess management for Claude Code and Codex CLIs
  - `checks.py` — Run project tests/lint and evaluate results
  - `notify.py` — Telegram and iMessage notifications
  - `ratelimit.py` — Rate limit detection and CLI fallover logic

### Data flow

```
PLAN.md → parse → next task → CLI subprocess → run checks → commit → update PLAN.md → next
```

Logs go to `logs/` — one file per task attempt.

## Conventions

- Python, stdlib-heavy (argparse, subprocess, json, pathlib, re)
- `ruff` for linting, `pytest` for tests
- Keep it simple — no frameworks, no over-abstraction
