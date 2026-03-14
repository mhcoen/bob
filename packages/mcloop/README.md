# McLoop

McLoop lets you run Claude Code for hours at a time without babysitting it. You write a task list in `PLAN.md`. McLoop works through it continuously, launching a fresh CLI session per task. Each session writes unit tests for the code it generates, runs your tests and linter, and fixes any failures before moving on. Only clean, passing code is committed. After all tasks complete, McLoop audits the entire codebase for bugs, verifies each finding, and fixes confirmed defects. You get notified of progress throughout. When it needs authorization to run a command, it sends you a Telegram message with Approve and Deny buttons so you can respond from your phone.

### Features at a glance

- **Continuous task execution** with a fresh context per session and rolling summaries between tasks
- **Automatic bug audit** after all tasks complete: find, verify, and fix confirmed defects in two rounds
- **Telegram notifications** for progress, failures, and remote command approval from your phone
- **Interrupt and resume** with state capture: Ctrl-C saves what was happening so the next run can pick up where you left off
- **Investigation mode** for runtime bugs that survive the build/test cycle
- **Self-healing apps** with automatic crash instrumentation (Swift and Python)
- **Task batching** with `[BATCH]` to combine well-specified subtasks into a single session
- **Failed approach tracking** with `[RULEDOUT]` so the agent never repeats what already failed
- **Model fallback** from a cheaper model to a stronger one when tasks fail
- **Stages** for phased execution with testing between stages
- **Continuous code review** of every commit via a second AI model, without blocking the main loop
- **Targeted testing** after each task (full suite only at stage boundaries)
- **Syncing** PLAN.md with the codebase after manual changes
- **Visual verification** with deterministic app screenshots

Because McLoop runs Claude Code sessions continuously, it will use
your plan allowance faster than if you used it interactively. See
[Best practices](#best-practices) for how to get the most from it.

Each session starts with a clean context, with no memory of previous sessions. The CLI sees your project description, the current task, and whatever is in your codebase: source files, markdown docs, tests, configuration. That's it. This also keeps token usage low, since each session pays only for the current task's context rather than accumulating conversation history from every previous task. Good results depend on the code and docs in your repo being the source of truth, not on conversation history.

McLoop creates a few files in the project that serve as shared memory
between sessions:

- **CLAUDE.md**: A manifest describing every source file. Sessions read
  it first to understand the codebase without searching, and update it
  when they add or change files.
- **NOTES.md**: Observations, edge cases, and design decisions that
  sessions notice during tasks. Accumulates across sessions for you to
  review.
- **BUGS.md**: Written by the audit cycle, lists confirmed defects for
  the fix session to act on. Deleted after bugs are fixed.

These files live in the repo alongside your code and are the mechanism
by which one session's knowledge reaches the next.

McLoop is designed for the long haul. Start with a few tasks, let it run
while you do something else, add more tasks when you think of them, re-run.
It's a persistent task queue backed by a text file, not a one-shot build
script. All state lives in the repository: PLAN.md, source code,
documentation, configuration, and git history. If McLoop is interrupted,
killed, or hits a rate limit, just run `mcloop` again. It finds the next
unchecked task and picks up exactly where it left off. No session files, no
databases, nothing to reset.

## Design first, then execute

A longstanding rule of thumb in software engineering is to spend
two-thirds of your time on design before starting any significant
coding effort. Many developers cut this short. Among those doing
AI-assisted "vibe coding," where you sit down at a prompt and start
building immediately, the percentage is likely much higher.

McLoop turns this on its head by making the design phase directly
executable. The PLAN.md is your design document: the decomposition,
the ordering, the constraints, the desired behavior. But instead of
handing it to a developer to interpret, McLoop hands it to Claude
Code to execute literally. This restores the incentive to design
carefully, because the quality of the output is a direct function of
the quality of the plan. A vague task produces vague code. A
well-decomposed task with clear constraints produces exactly what you
described.

The plan doesn't need to come from you alone. There are several ways
to create one:

- **AI-assisted design.** Use one or more AIs to help write the plan.
  Bounce it between Claude, ChatGPT, Gemini, whatever. Each brings
  different perspectives. Iterate on the design until you're
  satisfied.
- **Human-directed design.** You write the plan yourself, or take an
  AI-generated plan and reshape it. You decide the decomposition, the
  ordering, the constraints. The AI coding tool is purely an executor
  of your design decisions.
- **Automated extraction with [Duplo](https://github.com/mhcoen/duplo).**
  Point Duplo at a product URL and it scrapes the site, downloading
  text, images, and demo videos. It extracts frames from videos at
  scene-change points, analyzes screenshots for visual design details
  (colors, fonts, layout), pulls features from documentation, and
  generates a phased PLAN.md for McLoop to execute. This lets you
  reproduce existing software, SaaS products, or websites by letting
  Duplo do the design extraction and plan generation automatically.
- **Hybrid.** Start with AI-generated plans, edit them, add your own
  tasks, remove what you don't want, reorder priorities. The plan is a
  living text file you own completely.

In each case, the human controls the design. McLoop separates
design from execution cleanly enough that you can use whatever process
works for you on the design side, and the execution is mechanical.

McLoop is also not limited to building code from scratch. Any sequence
of well-defined steps that Claude Code can execute is a valid plan:
refactoring a module, migrating a database schema, setting up CI/CD,
auditing dependencies, generating documentation, running a series of
analyses, or performing scheduled maintenance. If you can describe it
clearly enough for a person to follow, McLoop can execute it.

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
mcloop sync --dry-run     # Show sync changes without applying
mcloop audit              # Run a standalone bug audit
mcloop investigate "crash on wake from sleep"  # Debug a specific bug
mcloop investigate --log crash.log             # Debug from a log file
mcloop wrap                                    # Instrument an existing project for error capture
mcloop --model sonnet --fallback-model opus    # Fall back to opus if sonnet fails
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
over-abstraction.

- [ ] Project scaffolding (pyproject.toml, .gitignore, loop package, __main__.py)
- [ ] Markdown checklist parser (parse tasks, find next unchecked, check off items)
- [ ] Telegram and iMessage notifications
- [ ] Auto-detect and run project test/lint suites
- [ ] Rate limit detection and CLI fallover
- [ ] CLI subprocess runner with logging
- [ ] Main loop: parse, execute, verify, commit, notify, repeat
```

This is the PLAN.md that was used to bootstrap the initial version
of McLoop. See [PLAN.EXAMPLE.md](PLAN.EXAMPLE.md) for the current
version with subtasks.

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

You can close this gap with `mcloop sync`, which launches a Claude Code
session to review the codebase and git history, check off tasks that are
already implemented, append items for work not yet in the plan, and flag
discrepancies. See [Syncing PLAN.md](#syncing-planmd) for details.

The checklist is what McLoop executes. Each item should be a meaningful unit of
work, such as a feature, a subsystem, or a named refactor, not a single function
or line.

**You don't have to write the whole checklist upfront.** McLoop picks up
wherever you left off. Add tasks as you think of them, reorder them, break
them into subtasks. When McLoop finishes the current queue, just add more and
re-run. This makes it equally useful for iterative refinement of an existing
codebase as for building something from scratch.

**Tip:** You can use your favorite chat interface (e.g., Claude, ChatGPT) to
help write the PLAN.md file. Feed it the README.md along with a description of
your project, have it ask any questions it has, and output the markdown file.

**Do not write separate "add tests" tasks.** Every task session is
instructed to write unit tests as part of its work. A dedicated test
task at the end of a group will find the tests already written,
produce no file changes, and fail as a no-op. If specific test
coverage matters, include it in the implementation task (e.g.
"Implement X with unit tests covering Y and Z").

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
| `[USER]` | Requires human action. McLoop pauses and sends a Telegram notification. |
| `[AUTO:<action>]` | Automated observation (process monitor, app interaction). |
| `[RULEDOUT]` | Records a failed approach so it is not repeated. |

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
    4. Run targeted checks (lint + tests for changed files only)
    5. If checks pass  -> commit, push, check the box, notify, continue
    6. If checks fail  -> retry with error context (up to --max-retries)
    7. If retries exhausted -> mark [!], notify, stop
    8. If rate-limited -> pause, wait for reset, resume
       If session-limited -> poll every 10 minutes, resume when limit resets
    9. At stage boundaries -> run full test suite
10. Run bug audit/fix cycle (unless --no-audit)
11. Print summary with elapsed time and whitelist suggestions
```

Tasks within a single run share a rolling session context. After each task
completes, McLoop summarizes what changed, including which files were
created or modified, and feeds that summary into the next task's prompt.
This gives later tasks awareness of what earlier tasks did without
carrying over the full conversation history. This rolling summary
resets when you restart McLoop, though McLoop does remember what it
was doing if interrupted (see [Interrupting and resuming](#interrupting-and-resuming)).

Each task is numbered (e.g., "Task 3.2)") and shows progress dots as the
session works. Tool output is suppressed to keep the terminal clean. Elapsed
time is shown for each completed task and in the final summary.

When a task or check fails, McLoop prints the error output directly in the
terminal and includes it in the prompt for the next retry so Claude can fix
the problem rather than repeating the same mistake.

McLoop stops when a task fails all retries. It does not continue to the next
task, since tasks may have implicit dependencies.

### Interrupting and resuming

When you press Ctrl-C (or Ctrl-Z, or send SIGTERM), McLoop
immediately acknowledges the interrupt, saves its state to
`.mcloop/interrupted.json`, kills the child process group, and
exits. The state includes which task was running, how long it had
been active, the last 20 lines of output, and what phase McLoop
was in (task session, checks, audit, or user prompt).

The next time you run `mcloop`, it detects the saved state and
prompts you:

```
  Previous run was interrupted during task phase (2026-03-13T11:02:44)
  Task 14.2: Add unit conversion parser
  Running for 3m 12s
  Last output:
    Running pytest... 8 tests failed in test_parser.py

  (r)etry / (d)escribe what went wrong / (s)kip / (q)uit
```

**Retry** proceeds normally, picking up the unchecked task.
**Describe** lets you type what went wrong. McLoop records your
description as a `[RULEDOUT]` entry in PLAN.md under the task and
appends it to `.mcloop/eliminated.json`, so the next attempt knows
not to repeat the same approach. **Skip** marks the task as failed
(`[!]`) and moves on. **Quit** exits.

The prompt adapts to the interrupted phase. Audit interruptions
offer resume/skip/quit. User prompt interruptions resume
automatically with no prompt.

### Model fallback

Use `--fallback-model` to automatically escalate to a stronger model
when the primary model fails:

```bash
mcloop --model sonnet --fallback-model opus
```

When a task exhausts all retries on the primary model, McLoop retries
it from scratch using the fallback model (with the same retry count)
before marking it failed. This lets you run most tasks on a cheaper
or faster model and only use the stronger model for tasks that need
it. If no `--fallback-model` is set, behavior is unchanged.

After each successful commit, McLoop pushes to the remote. If the
push fails, McLoop stops immediately rather than continuing with
work that has no remote safety net. If no remote exists, it creates
a private GitHub repo with `gh repo create` and sets up the origin
automatically.

Before any tasks run, McLoop commits all pending changes and pushes
them to the remote. If this pre-flight push fails, McLoop exits
with an error telling you to fix the remote. This ensures the remote
is always up to date before new work begins.

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
- **MCP tools** are blocked entirely during McLoop sessions. Claude Code
  sessions should only use local tools (Bash, Read, Edit, Write, etc.).
- **Everything else** sends you a Telegram message with **Approve**, **Deny**,
  and **Allow All Session** buttons describing exactly what Claude Code wants to
  do, then pauses and waits for your response. McLoop resumes immediately once
  you tap a button. **Allow All Session** remembers the approved command pattern
  for 24 hours, so identical commands pass through automatically for the rest of
  the session. If you **deny** a command, McLoop kills the session immediately
  and treats the task as failed. If no response is received within 10 minutes,
  the command is denied automatically.

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
permission requests, and when all tasks are done.

**Tip:** Installing the [Telegram Desktop](https://desktop.telegram.org/)
app alongside the mobile app is highly recommended. Both receive
notifications simultaneously, so you can approve permission requests
from whichever device is nearest. The desktop app is particularly
convenient when you are already at your computer and McLoop is
running in another terminal.

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

McLoop automatically detects how to check your project based on files
it finds (like `pyproject.toml` or `package.json`). No configuration
is needed for common setups. Use `mcloop.json` at the project root to
override or extend the defaults.

To avoid running the entire test suite after every task, McLoop runs
targeted tests after each task: only tests corresponding to changed
files (e.g., changes to `hasher.py` runs `test_hasher.py`). The full
test suite runs at stage boundaries and at the end of the run. This
keeps individual tasks fast while still catching cross-module
regressions before moving on.

### Explicit checks

If `mcloop.json` has a `checks` array, McLoop runs those commands in
order and skips auto-detection entirely:

```json
{
  "checks": ["ruff check .", "ruff format --check .", "pytest"]
}
```

### Auto-detection rules

If no `checks` array is present (or `mcloop.json` doesn't exist),
McLoop auto-detects from built-in rules (Python via `pyproject.toml`,
Node via `package.json`) and from marker-based rules in the `detect`
array:

```json
{
  "detect": [
    {"marker": "Cargo.toml", "commands": ["cargo clippy -- -D warnings", "cargo test"]},
    {"marker": "Package.swift", "commands": ["swift build"]},
    {"marker": "go.mod", "commands": ["go vet ./...", "go test ./..."]},
    {"marker": "Makefile", "commands": ["make check"]}
  ]
}
```

Each rule maps a marker file to a list of commands. If the marker
exists in the project directory, the commands are added to the check
list. Add rules for any language by editing this file.

### Build and run

After all tasks and the audit complete, McLoop runs the `build`
command and shows the `run` command in the summary:

```json
{
  "build": "./build-app.sh",
  "run": "open MarkdownLook.app"
}
```

If `build` succeeds, the summary prints "To run: open MarkdownLook.app"
so you know exactly how to launch what was built. Both fields are
optional.

See [CHECKS.md](CHECKS.md) for complete examples.

### Stages

PLAN.md can be divided into stages using `## Stage N:` headers.
McLoop completes all tasks in the current stage, then stops. Run
`mcloop` again to start the next stage. This lets you test between
stages and give feedback before continuing.

```markdown
## Stage 1: Scaffold
- [ ] Create project structure
- [ ] Add empty window

## Stage 2: Core feature
- [ ] Add audio recording
- [ ] Add playback
```

Without stage headers, McLoop runs all tasks in one go as before.

McLoop also verifies that each task produces meaningful file changes beyond
PLAN.md and logs. If a session completes without writing any code, the task
is treated as failed and retried.

## Advanced plan features

### User tasks

Mark any task with `[USER]` when it requires human action that Claude
Code cannot perform: testing Ctrl-C in a terminal, observing a GUI,
confirming behavior on a physical device. When McLoop reaches a
`[USER]` task, it pauses, prints instructions in the terminal, and
sends a Telegram notification so you know to check in. You type your
observation at the terminal and McLoop records it and continues.

This is not limited to the investigation system. Any task in any
PLAN.md can use `[USER]`:

```markdown
- [ ] [USER] Verify the app launches and the menu bar icon appears
- [ ] [USER] Test Ctrl-C, Ctrl-Z, and kill on a live run
```

### Batching subtasks

Mark a parent task with `[BATCH]` to combine all its unchecked
children into a single Claude Code session:

```markdown
- [ ] [BATCH] mcloop install and uninstall subcommands
  - [ ] Add subcommands to parser with --dry-run flags
  - [ ] Check claude is on PATH, print version
  - [ ] Copy hooks to ~/.mcloop/hooks/
  - [ ] Merge settings.json entries
  - [ ] Prompt for Telegram credentials
  - [ ] [USER] Manual verification
```

McLoop collects all unchecked children up to the first `[USER]`
or `[AUTO]` boundary, combines their text into a single numbered
prompt ("Do all of the following in order: 1. ... 2. ... 3. ..."),
and runs one session. If checks pass, all batched children are
checked off in a single commit. If the batch fails, McLoop
automatically falls back to running each subtask individually.

Batching is most effective for late-stage, well-specified tasks
where each subtask is essentially pseudocode. Early-stage tasks
with significant design decisions should not be batched. Without
a `[BATCH]` tag, behavior is unchanged: each subtask runs in its
own session.

### Recording failed approaches

When an approach has been tried and ruled out, add a `[RULEDOUT]`
line under the task. McLoop parses these and injects them into the
task prompt so Claude Code knows not to repeat them:

```markdown
- [ ] Fix Ctrl-C: prevent claude from stealing the terminal foreground
  [RULEDOUT] pty isolation via pty.openpty(): Ctrl-C still ignored
  [RULEDOUT] tcsetpgrp/_reclaim_foreground: race condition
  - [x] Rewrite _run_session with stdin=DEVNULL
  - [x] Add signal handlers
```

Subtasks inherit `[RULEDOUT]` entries from their parent. The agent
sees the full list of ruled out approaches for the current task and
all its ancestors, with an explicit instruction not to repeat any
of them.

You can add `[RULEDOUT]` lines manually, or McLoop can add them
automatically when you describe a failure during the interrupt
resumption prompt (see [Interrupting and resuming](#interrupting-and-resuming)).

## Bug audit

After all checklist tasks complete, McLoop automatically runs two rounds
of bug auditing (unless `--no-audit` is passed). Each round follows the
same cycle:

1. **Find bugs.** A Claude Code session reads the entire codebase and
   writes findings to `BUGS.md`. Only actual defects are included:
   crashes, incorrect behavior, unhandled errors, and security issues.
   Style issues and refactoring suggestions are excluded. If BUGS.md
   already exists, new findings are appended rather than replacing
   what's there.

2. **Verify they are real.** A separate session reads each reported bug
   and checks it against the actual source code. Bugs that are incorrect
   are removed. The terminal shows which bugs were confirmed and which
   were removed with reasons.

3. **Fix them.** A fix session addresses only the confirmed bugs.

4. **Verify the fixes.** A post-fix review session examines the changed
   files to verify the fixes didn't introduce new bugs. If problems are
   found, they're fed back into the fix loop.

5. **Test.** The checks run. If a test fails because of the bug fix,
   the fix session corrects the test.

The second round catches bugs introduced by the first round's fixes.
After both rounds complete, the audit hash is saved.

If McLoop starts and finds an existing `BUGS.md`, it skips the audit and
resumes the fix cycle directly.

To prevent the audit from running on unchanged code, McLoop writes the
current git hash to `.mcloop-last-audit` after a successful audit cycle.
On the next run, if no source files have changed since that hash, the
audit is skipped. Delete `.mcloop-last-audit` to force a re-audit, or
run `mcloop audit` for a standalone audit at any time.

## Investigating bugs

The build/test/audit cycle catches most defects, but some bugs only
appear at runtime: a menu bar icon that vanishes after sleep, a
crash triggered by specific user input, a deadlock under load.
These require a different approach. You need to reproduce the
problem, observe what happens, form hypotheses, and eliminate them
one by one. `mcloop investigate` does this.

```bash
mcloop investigate "menu bar icon disappears after wake from sleep"
mcloop investigate --log ~/Library/Logs/DiagnosticReports/MyApp-*.ips
cat traceback.txt | mcloop investigate "segfault on resize"
```

McLoop gathers bug context from every source it can find: the
description you provide, macOS crash reports from
`~/Library/Logs/DiagnosticReports/`, the most recent mcloop task
log, a log file you point to with `--log`, and anything piped to
stdin. It then searches the web for the specific errors, stack
traces, and symptoms in the bug report. If the crash log mentions
`EXC_BAD_ACCESS` in `NSStatusBarButton`, it searches for that. If
the traceback shows a specific framework API failing, it searches
for known issues with that API. This is how a person would debug:
start by understanding what other people have encountered with the
same symptoms before writing any code.

From this context, McLoop generates an investigation plan that
follows a strict debugging playbook:

1. **Reproduce** the problem with a minimal trigger.
2. **Instrument** at stage boundaries to narrow the location.
3. **Isolate** subsystems with standalone probes.
4. **Inspect** live runtime behavior (process sampling, crash
   reports, UI state).
5. **Fix** the production code only after the cause is confirmed.
6. **Clean up** temporary scaffolding.

The investigation runs in an isolated git worktree
(`../project-investigate-slug/`) so it cannot damage the main
codebase. McLoop creates a branch, copies your project settings,
generates the investigation PLAN.md, and runs it.

Some investigation steps require human observation: "Launch the
app, put the machine to sleep for 10 seconds, wake it, and
describe what you see." These are marked `[USER]` in the plan.
When McLoop reaches one, it pauses with clearly formatted
instructions and waits for you to type your observation at the
terminal. Your response is fed into the next session's context.

Other steps can be performed automatically. McLoop includes a
process monitor that can launch apps, detect crashes and hangs
(via macOS `sample`), and read crash reports. It also includes
an app interaction layer that can click buttons, read UI elements,
and take screenshots using macOS accessibility APIs. Every app
built by McLoop is instrumented with accessibility identifiers
from the start, which makes this programmatic interaction
possible.

After the investigation produces a fix, McLoop automatically
launches the app, replays the reproduction steps, and verifies
the app survives without crashing or hanging. If verification
fails, it feeds the new failure back into the investigation for
another round (up to three). If it passes, McLoop shows the diff
and offers to merge the investigation branch back into main.

If the investigation does not fully resolve the bug, McLoop
prints what was learned (from NOTES.md), what tasks remain, and
leaves the worktree in place. Run `mcloop investigate` again
with the same description to resume where it left off.

## Self-healing apps

Every app McLoop builds is automatically instrumented with crash
handlers. You do not need to do anything to enable this. After the
first task that produces a runnable app, McLoop injects
error-catching hooks into the source code. If the app crashes during
normal use, the instrumentation captures the full context and tells
you exactly what to do:

```
[McLoop] Crash captured: SIGABRT in Qwen3ASREngine.loadModel()
  Run mcloop from ~/proj/mcwhisper to fix this bug.
```

The next time you run `mcloop`, it reads the captured errors before
doing anything else:

```
2 runtime bugs detected:

  1. SIGABRT in Qwen3ASREngine.loadModel() -- model path was nil
     when selecting Parakeet TDT model (3 hours ago)

  2. Audio levels stuck at 0.0 during push-to-talk recording,
     waveform never animated (1 hour ago)

Fix these bugs before continuing? [Y/n]
```

If you say yes, McLoop runs a diagnostic session per error, inserts
fix tasks into a `## Bugs` section in PLAN.md, and works only those
tasks. It does not touch feature tasks, start the next stage, or run
the audit cycle. It fixes, verifies (by relaunching the app to
confirm the error no longer occurs), and exits. You run `mcloop`
again for feature work once bugs are clear.

If you say no, McLoop skips the bugs and continues with normal
feature work. The bugs stay in `.mcloop/errors.json` for next time.

The `## Bugs` section in PLAN.md has absolute priority. If it
contains unchecked items, `find_next` returns those before any
feature tasks.

### How it works

McLoop detects the project language and injects error-catching
code into source files, delimited with markers
(`// mcloop:wrap:begin` / `// mcloop:wrap:end` for Swift,
`# mcloop:wrap:begin` / `# mcloop:wrap:end` for Python). The
canonical wrapper source is stored in `.mcloop/wrap/` so McLoop
can re-inject it if Claude Code strips the markers during a task.

Swift instrumentation includes `NSSetUncaughtExceptionHandler`,
signal handlers (SIGSEGV, SIGABRT, SIGBUS), and an app-state dump
that captures relevant `@Published` properties at crash time.

Python instrumentation includes `sys.excepthook`, signal handlers,
and logging integration that captures unhandled exceptions with
full tracebacks and local variables in the crashing frame.

Both write structured error reports to `.mcloop/errors.json` with
stack traces, app state, timestamps, crash location, and a one-line
description. The project directory path is baked into the handler
at injection time so the crash message can tell the user where to
run mcloop.

After every task that modifies instrumented source files, McLoop
checks whether the markers are intact and re-injects from
`.mcloop/wrap/` if they were removed. The wrapper survives Claude
Code edits automatically.

If the same error triggers diagnostic insertion more than 3 times,
McLoop marks it as unresolvable, prints the context, and stops
rather than looping indefinitely.

To instrument a project that was NOT built by McLoop, use
`mcloop wrap` manually from that project's directory.

## Continuous code reviewer

McLoop can run a second AI model as a reviewer on every commit. After
each successful commit, McLoop spawns a background process that sends
the diff to an OpenAI-compatible API for review. The reviewer checks
for bugs, logic errors, unhandled exceptions, resource leaks, and
missing edge cases. This never blocks the main loop — the review runs
in a detached subprocess while McLoop continues to the next task.

Findings are written to `.mcloop/reviews/` as JSON. At the start of
each loop iteration, McLoop collects any completed reviews. Low- and
medium-confidence findings are added to the rolling session context so
the next task is aware of them. If a single commit produces three or
more high-confidence error-severity findings, McLoop escalates by
inserting a fix task into the `## Bugs` section of PLAN.md, which has
absolute priority over feature tasks.

The reviewer is disabled by default. To enable it, add a `reviewer`
section to `.mcloop/config.json` in your project directory and set
the `OPENROUTER_API_KEY` environment variable:

```json
{
  "reviewer": {
    "model": "google/gemini-2.5-flash",
    "base_url": "https://openrouter.ai/api/v1"
  }
}
```

```bash
export OPENROUTER_API_KEY=your-key-here
```

Any OpenAI-compatible endpoint works: [OpenRouter](https://openrouter.ai),
a direct provider API, or a local server like
[Ollama](https://ollama.com) (set `base_url` to
`http://localhost:11434/v1` and `OPENROUTER_API_KEY` to any non-empty
string). The model is your choice — a fast, cheap model works well
since it only reviews diffs, not full codebases.

McLoop prints the reviewer status at startup when configured (e.g.,
`Reviewer: google/gemini-2.5-flash via openrouter.ai (API key set)`).
Stale review files older than 24 hours are cleaned up automatically.

## Syncing PLAN.md

Run `mcloop sync` to reconcile PLAN.md with the actual codebase. This
launches a Claude Code session that reads the project files, git history, and
existing plan, then:

1. Appends checked items for any features, fixes, or changes reflected in the
   code but not yet in PLAN.md, matching the granularity of existing items.
2. Checks off unchecked items that are already implemented in the codebase.
3. Flags problems: checked items with no corresponding code, and descriptions
   that have drifted from what the code actually does.

Before writing, McLoop shows a diff of the proposed changes and asks for
confirmation. No existing items are deleted.

Use `mcloop sync --dry-run` to see the proposed changes without modifying
PLAN.md.

This is useful for keeping PLAN.md accurate after manual edits, interactive
Claude Code sessions, or any other changes made outside McLoop.

## Summary and whitelist suggestions

When McLoop finishes (whether all tasks completed or one failed), it prints a
summary showing completed tasks with elapsed times, the failed task with error
details, remaining task count, and total elapsed time.

If you approved any commands via Telegram during the run, McLoop suggests
adding them to your allowlist in the format used by `settings.json`. Dangerous
commands (like `rm`, `sudo`, `chmod`) are never suggested even if approved.

## Visual verification

McLoop includes `bin/appshot`, a utility for capturing deterministic
screenshots of macOS app windows. Use it to verify that GUI applications
built by McLoop render correctly. It works with any app that puts a
window on screen: Swift, Electron, Qt, Java, React Native, anything.

```bash
bin/appshot "AppName" screenshot.png
bin/appshot "AppName" screenshot.png --launch .build/debug/AppName
bin/appshot "AppName" screenshot.png --wait 2
bin/appshot "AppName" screenshot.png --setup 'tell app "AppName" to activate'
```

Claude Code sessions are instructed via CLAUDE.md to always use appshot
for visual verification rather than reinventing screenshot capture.
Requires macOS Screen Recording permission (granted once).

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
- `git` on PATH (McLoop requires git for checkpointing and recovery.
  If no `.git` directory exists, McLoop initializes one automatically
  before the first task. All git errors are reported to the terminal
  and via Telegram.)
- `claude` CLI on PATH
- `gh` CLI on PATH (for automatic GitHub repo creation)
- macOS for iMessage notifications (Telegram works anywhere)
- Playwright (optional, for web app investigation only)

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

## Best practices

McLoop does not require its own API key or tokens, does not extract
or borrow OAuth tokens from Claude Code, and does not violate
Anthropic's Terms of Service. It iteratively runs Claude Code in a
controlled way through the public `claude -p` CLI, using whatever
plan you already have (Pro, Max, etc.). There is nothing extra to
provision.

That said, McLoop will use your plan allowance aggressively. A single
McLoop run can consume in a few hours what you would normally spread
across days of interactive use. Each task launches a full Claude Code
session that reads files, writes code, runs tests, and iterates on
failures. The audit cycle after task completion adds further usage.
This is by design, but you should be aware of it.

Practical advice for getting the most out of your allowance:

**Use [RTK](https://github.com/rtk-ai/rtk).**  RTK is a CLI proxy
that compresses command output before it reaches Claude Code's
context, reducing token consumption by 60-90%. Install it and run
`rtk init --global`. McLoop's Telegram permission hook already
handles RTK-wrapped commands, so no additional configuration is
needed. This is one of the most effective ways to extend your plan
usage.

**Write detailed task descriptions.** Vague tasks cause Claude Code
to explore, guess, and backtrack, all of which burn tokens. A
well-specified task with clear constraints completes faster and in
fewer tokens. Spend time on the plan.

**Break large tasks into small ones.** Each task gets a fresh
context. A task that tries to do too much will hit context limits,
lose track of what it was doing, and waste retries. Small, focused
tasks complete reliably on the first attempt.

**Whitelist safe commands.** Every command that is not whitelisted
sends you a Telegram notification and idles until you respond.
Whitelisting commands you always approve avoids the interruptions
and keeps sessions moving. McLoop prints whitelist suggestions at
the end of each run.

**Use stages for large projects.** Divide PLAN.md into stages with
`## Stage N:` headers. McLoop completes one stage and stops, giving
you a chance to test and give feedback before it consumes more of
your allowance on the next stage.

**Run overnight or during off-peak hours.** If your plan has
time-based rate limits, long McLoop runs benefit from starting when
you are not using Claude Code interactively.

**Monitor with `rtk gain`.** If RTK is installed, run `rtk gain`
after a McLoop session to see how many tokens were saved. This helps
you gauge whether the compression is working and how much headroom
you have.

## Suggested reviewer models

Any OpenAI-compatible API works as a reviewer. The model does not
need to generate code, only read diffs and identify problems, so
strong reasoning matters more than code generation benchmarks.
Cheaper models are practical because the reviewer runs in the
background on every commit.

| Model | Provider | Input /1M | Output /1M | SWE-bench | Context | Notes |
|-------|----------|-----------|------------|-----------|---------|-------|
| DeepSeek V3.2 | OpenRouter | $0.28 | $0.42 | 73.1% | 128K | Best value. 90% cache discount on repeated context. |
| GLM-5 | OpenRouter | $0.72 | $2.30 | 95.8% | 200K | Strongest open model. Near-zero hallucination rate. |
| Kimi K2.5 | OpenRouter | $0.60 | $2.40 | 76.8% | 256K | Highest open-source SWE-bench. Strong at debugging. |
| Gemini 2.5 Flash | Google | $0.30 | $2.50 | N/A | 1M | Fast, cheap, very large context window. |
| Gemini 2.5 Pro | Google | $1.25 | $10.00 | 63.8% | 1M | Strong reasoning, 1M context. Free tier available. |
| Claude Sonnet 4.6 | Anthropic | $3.00 | $15.00 | 79.6% | 200K | For comparison. McLoop's default task executor. |
| Claude Opus 4.6 | Anthropic | $5.00 | $25.00 | 80.8% | 200K | For comparison. Strongest overall but 60x DeepSeek output cost. |

To use any of these, set the OpenRouter model string in
`.mcloop/config.json`:

```json
{"reviewer": {"model": "zhipu/glm-5", "base_url": "https://openrouter.ai/api/v1"}}
```

For Google models, use `google/gemini-2.5-pro` or
`google/gemini-2.5-flash` through OpenRouter (same config format).
Pricing may vary by provider and change over time. Check
[OpenRouter](https://openrouter.ai) for current rates.

## License

MIT. See [LICENSE](LICENSE).

## Author

**Michael H. Coen**  
mhcoen@gmail.com | mhcoen@alum.mit.edu  
[@mhcoen](https://github.com/mhcoen)
