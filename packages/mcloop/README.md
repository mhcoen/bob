# McLoop

McLoop lets you run Claude Code for hours at a time without babysitting it. You write a task list in `PLAN.md`. McLoop works through it continuously, launching a fresh CLI session per task, running your tests and linter, committing only if everything passes, and notifying you of progress. When it needs authorization to run a command, it sends you a Telegram message with Approve and Deny buttons so you can respond from your phone.

Each session starts with a clean context, with no memory of previous sessions. The CLI sees your project description, the current task, and whatever is in your codebase: source files, markdown docs, tests, configuration. That's it. Good results depend on the code and docs in your repo being the source of truth, not on conversation history.

McLoop is designed for the long haul. Start with a few tasks, let it run
while you do something else, add more tasks when you think of them, re-run.
It's a persistent task queue backed by a text file, not a one-shot build
script. All state lives in the repository: PLAN.md, source code,
documentation, configuration, and git history. If McLoop is interrupted,
killed, or hits a rate limit, just run `mcloop` again. It finds the next
unchecked task and picks up exactly where it left off. No session files, no
databases, nothing to reset.

## Install

```bash
pip install mcloop
```

## Quickstart

```bash
mcloop                    # Run (reads PLAN.md by default)
mcloop --file other.md    # Use a different file
mcloop --dry-run          # Show what would run, don't execute
mcloop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
mcloop --model opus       # Use a specific Claude model
mcloop --no-audit         # Skip the post-completion bug audit
mcloop sync               # Sync PLAN.md with the codebase
mcloop audit              # Run a standalone bug audit
```

## Writing a PLAN.md

A `PLAN.md` has two parts: a **project description**, then a **checklist**.

```markdown
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

- [ ] Project scaffolding (pyproject.toml, .gitignore, loop package, __main__.py)
- [ ] Markdown checklist parser (parse tasks, find next unchecked, check off items)
- [ ] Telegram and iMessage notifications
- [ ] Auto-detect and run project test/lint suites
- [ ] Rate limit detection and CLI fallover
- [ ] CLI subprocess runner with logging
- [ ] Main loop: parse, execute, verify, commit, notify, repeat
```

This is the PLAN.md that was used to build McLoop itself. See
[PLAN.EXAMPLE.md](PLAN.EXAMPLE.md) for the full version with subtasks.

The description runs from the top of the file down to the first checkbox. It's
included in every CLI invocation, so every session has context about what the
project is, what technologies to use, and what constraints matter. **Without a
description, the CLI has no context and will make worse decisions.**

You don't need to duplicate your README or code comments in the description.
Most of the context Claude Code needs is already in your codebase: the README,
CLAUDE.md, inline comments, and the code itself. Claude reads these during
each session. That said, a reasonably detailed description is fine and often
helpful, especially for technology choices, constraints, conventions, or
anything you want every session to keep in mind.

Because each session starts fresh, the CLI can only work from what's in the
repo at that moment. Keep your description, inline comments, and any other
markdown docs current. They are the CLI's only memory of decisions made in
previous sessions.

PLAN.md is a task queue, not a complete record of how the project was built.
Changes made outside McLoop, whether in an editor, an interactive Claude Code
session, or by hand, are not reflected in the file. The codebase itself is the
source of truth. PLAN.md drives what happens next, but it cannot reproduce what
already happened.

You can partially close this gap by asking Claude Code to review the codebase
and update PLAN.md with any work that isn't already captured, adding checked
items for features or fixes it finds in the code. This won't catch everything,
but it makes the file a more accurate record of what has actually been built.

**Tip:** You can use your favorite chat interface (e.g., Claude, ChatGPT) to
help write the PLAN.md file. Feed it the README.md along with a description of
your project, have it ask any questions it has, and output the markdown file.

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
0. Safety commit all tracked modified files (skipped if clean)
while unchecked items remain:
    1. Find next unchecked item (depth-first)
    2. Launch a fresh Claude Code session with a clean context.
       The CLI receives: project description + current task + your codebase.
       On retries, the previous error output is included so Claude can fix it.
    3. Verify the session produced meaningful file changes
    4. Run project checks (tests, lint, auto-detected from project files)
    5. If checks pass  -> commit, push, check the box, notify, continue
    6. If checks fail  -> retry with error context (up to --max-retries)
    7. If retries exhausted -> mark [!], notify, stop
    8. If rate-limited -> pause, wait for reset, resume
9. Run bug audit/fix cycle (unless --no-audit)
10. Print summary with elapsed time and whitelist suggestions
```

McLoop streams Claude Code's output in real time, showing text, tool calls,
and results as they happen. Each task is numbered (e.g., "Task 3.2)") to make
it easy to track progress through the checklist. Elapsed time is shown for
each completed task and in the final summary.

When a task or check fails, McLoop prints the error output directly in the
terminal and includes it in the prompt for the next retry so Claude can fix
the problem rather than repeating the same mistake.

McLoop stops when a task fails all retries. It does not continue to the next
task, since tasks may have implicit dependencies.

After each successful commit, McLoop pushes to the remote. If no remote
exists, it creates a private GitHub repo using `gh repo create` and sets up
the origin automatically.

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
- **Everything else** sends you a Telegram message with **Approve**, **Deny**,
  and **Allow All Session** buttons describing exactly what Claude Code wants to
  do, then pauses and waits for your response. McLoop resumes immediately once
  you tap a button. **Allow All Session** remembers the approved command pattern
  for 24 hours, so identical commands pass through automatically for the rest of
  the session. If no response is received within 10 minutes, the command is
  denied automatically.

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

Each shell command gets its own approval. McLoop instructs Claude Code to avoid
chaining commands with `&&` or `;` so every operation is individually gated.

Add any commands you always trust to `permissions.allow` so they pass through
without a notification. Safe read-only commands like `ls`, `cat`, `head`,
`tail`, `which`, and `stat` are good candidates. See `settings.example.json`
for a recommended baseline.

If you use [RTK](https://github.com/rtk-ai/rtk) to reduce token usage, the
hook automatically unwraps `rtk proxy` commands before matching. So
`Bash(ruff:*)` in your allowlist will also permit `rtk proxy ruff check .`.

## Notifications

McLoop sends Telegram notifications for task completions, failures, rate limits,
permission requests, and when all tasks are done. Set `NOTIFY_VIA=imessage` to use iMessage instead.

Create `~/.claude/telegram-hook.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
IMESSAGE_ID=your-email
```

All fields are optional. Telegram requires both `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`. iMessage requires `IMESSAGE_ID` (Apple ID email)
and only works on macOS. You can also set these as environment variables.

## Project checks

McLoop auto-detects what to run:

| File present | Command run |
|---|---|
| `pyproject.toml` with ruff config | `ruff check .` |
| `pyproject.toml` with pytest config | `pytest` |
| `package.json` with test script | `npm test` |
| `Package.swift` | `swift build` |
| `Makefile` | `make check` |

### mcloop.json

To override auto-detection, add an `mcloop.json` file to your project root
with a `checks` array:

```json
{
  "checks": [
    "ruff check .",
    "pytest",
    "mypy src/"
  ]
}
```

When `mcloop.json` is present with a `checks` array, McLoop runs those
commands in order and skips auto-detection entirely. If the file is absent or
malformed, McLoop falls back to auto-detection.

McLoop also verifies that each task produces meaningful file changes beyond
PLAN.md and logs. If a session completes without writing any code, the task
is treated as failed and retried.

## Bug audit

After all checklist tasks complete, McLoop automatically runs a bug audit
cycle (unless `--no-audit` is passed). It launches a Claude Code session that
reads the entire codebase and writes a `BUGS.md` file listing actual defects:
crashes, incorrect behavior, unhandled errors, and security issues. Style
issues and refactoring suggestions are excluded.

If bugs are found, McLoop launches a fix session scoped to only the bugs in
`BUGS.md`. If the fix introduces check failures (e.g., a lint error), the
error output is appended to `BUGS.md` and the fix is retried up to 3 times.
On success, `BUGS.md` is deleted and the fix is committed. On failure,
`BUGS.md` is left in place so you can see what was found.

If McLoop starts and finds an existing `BUGS.md`, it skips the audit and
resumes the fix cycle directly.

To prevent the audit from running endlessly on the same code, McLoop writes
the current git hash to `.mcloop-last-audit` after a successful audit cycle.
On the next run, if no source files have changed since that hash, the audit
is skipped. Delete `.mcloop-last-audit` to force a re-audit, or run
`mcloop audit` for a standalone audit at any time.

## Syncing PLAN.md

Run `mcloop sync` to reconcile PLAN.md with the actual codebase. This
launches a Claude Code session that reads the project files, git history, and
existing plan, then:

1. Appends checked items for any features, fixes, or changes reflected in the
   code but not yet in PLAN.md, matching the granularity of existing items.
2. Flags problems: checked items with no corresponding code, unchecked items
   that appear already implemented, and descriptions that have drifted from
   what the code actually does.

Before writing, McLoop shows a diff of the proposed changes and asks for
confirmation. No existing items are modified or removed.

This is useful for keeping PLAN.md accurate after manual edits, interactive
Claude Code sessions, or any other changes made outside McLoop.

## Summary and whitelist suggestions

When McLoop finishes (whether all tasks completed or one failed), it prints a
summary showing completed tasks with elapsed times, the failed task with error
details, remaining task count, and total elapsed time.

If you approved any commands via Telegram during the run, McLoop suggests
adding them to your allowlist in the format used by `settings.json`. Dangerous
commands (like `rm`, `sudo`, `chmod`) are never suggested even if approved.

## Implementation notes

During each task session, Claude Code may notice edge cases, design decisions,
assumptions, potential issues, or anything worth revisiting later. When it
does, it appends a note to `NOTES.md` with the current date and a reference
to the task being worked on (e.g., "[3.2] Parse Markdown to HTML").

McLoop does not act on NOTES.md. It is purely for you to review between runs.
Notes accumulate chronologically across sessions, giving you a running log of
things the agent thought were worth mentioning but weren't part of the task.
When McLoop finishes, it reminds you if NOTES.md exists.

## Logging

One log file per task attempt in `logs/`, named `{timestamp}_{task-slug}.log`.
Each log captures the full CLI output and exit code.

## Requirements

- Python >= 3.11
- `claude` CLI on PATH
- `gh` CLI on PATH (for automatic GitHub repo creation and push)
- macOS for iMessage notifications (Telegram works anywhere)

## Development

```bash
git clone https://github.com/mhcoen/mcloop.git
cd mcloop
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

```bash
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## Author

**Michael H. Coen**  
mhcoen@gmail.com | mhcoen@alum.mit.edu  
[@mhcoen](https://github.com/mhcoen)
