# McLoop:

McLoop lets you run AI coding agents for hours at a time without babysitting them. You write a task list in `PLAN.md`. McLoop works through it continuously, launching a fresh CLI session per task. Each session writes unit tests for the code it generates, runs your tests and linter, and fixes any failures before moving on. Only clean, passing code is committed. After all tasks complete, McLoop audits the entire codebase for bugs, verifies each finding, and fixes confirmed defects. You get notified of progress throughout. When it needs authorization to run a command, it sends you a Telegram message with Approve and Deny buttons so you can respond from your phone. McLoop supports Claude Code and OpenAI Codex as backends.

## What makes McLoop different

Every other agent runner treats the task file as a hint to the model. McLoop treats it as a specification.

`PLAN.md` is a formal document with a defined grammar, canonical structure, and machine-enforced consistency rules. Tasks carry stable identifiers (`T-NNNNNN`) that persist across edits and are how the system refers to them in commits, logs, and the audit trail. Phases have IDs and provenance. Mutations go through a deterministic API that refuses invalid states — you cannot accidentally check off a task whose children are incomplete, introduce a duplicate ID, or break a phase boundary, because the operations reject those moves. The file round-trips through its canonical form on every save, so diffs between runs are semantically meaningful rather than noise from whitespace or reordering.

What this buys you: the agent's stochastic output is forced through a deterministic gate before any state changes. The agent can be confused, can produce edits that look right but aren't, can hallucinate completion — none of that matters at the plan layer, because the plan layer doesn't trust the agent's word for anything. It trusts the diff, the tests, the commit, and the canonical structure of the file. Stochastic agent on top, deterministic control plane underneath.

You still author `PLAN.md` as a markdown checklist with prose descriptions. The hand-written surface is unchanged. What's changed is what happens once McLoop touches the file: on first run it canonicalizes the document (assigns task IDs, normalizes structure), and from that point forward the file is a structured artifact that the system reasons about precisely. Most users don't need to think about that day-to-day. The point is that it's true, and it's why McLoop works on real projects of real size, where naive agent runners produce confident garbage.

### Features at a glance

- **Continuous task execution** against a formally structured plan, with a fresh context per session and rolling summaries between tasks
- **Deterministic mutation** of plan state — task completion, phase advancement, bug filing, and retry resets all go through validated API calls that refuse invalid moves
- **Automatic bug audit** after all tasks complete: find, verify, and fix confirmed defects in two rounds
- **Telegram notifications** for progress, failures, and remote command approval from your phone
- **Interrupt and resume** from structured state: Ctrl-C captures what was happening and the next run picks up exactly where you left off, identified by task ID
- **Investigation mode** for runtime bugs that survive the build/test cycle
- **Builds self-healing apps** with automatic crash instrumentation (Swift and Python)
- **Task batching** with `[BATCH]` to combine well-specified subtasks into a single session
- **Failed approach tracking** with `[RULEDOUT]` so the agent never repeats what already failed
- **Model fallback** from a cheaper model to a stronger one when tasks fail or hit rate limits
- **Stages** for phased execution with testing between stages
- **Continuous code review** of every commit via a second AI model, without blocking the main loop
- **Multi-model coding patterns** via [Orchestra](https://github.com/mhcoen/bob/tree/main/packages/orchestra) integration: opt-in per project to route each edit attempt through a draft-then-adjudicate or propose-critique-synthesize pattern instead of a single-model invocation
- **Targeted testing** after each task (full suite only at stage boundaries)
- **Syncing** PLAN.md with the codebase after manual changes
- **Visual verification** with deterministic app screenshots
- **Guided setup** with `mcloop install` and `mcloop uninstall`
- **Token auditing** with `bin/mcloop-audit` for per-task cost breakdowns
- **Maintain mode** (`mcloop maintain`) enforces invariants from `MAINTAIN.md` independently of the main task loop
- **Ideas scratchpad** (`mcloop idea "..."`) appends timestamped notes to `IDEAS.md` for capturing future work without polluting `PLAN.md`
- **Structured run artifacts** written to `.mcloop/runs/` after every run, with a stable `latest.json` for automation and postmortems

Because McLoop runs CLI sessions continuously, it will use
your plan allowance faster than if you used the agent interactively. See
[Best practices](#best-practices) for how to get the most from it.

Each session starts with a clean context, with no memory of previous sessions. The CLI sees your project description, the current task (identified by its task ID), and whatever is in your codebase: source files, markdown docs, tests, configuration. That's it. This also keeps token usage low, since each session pays only for the current task's context rather than accumulating conversation history from every previous task. Good results depend on the code and docs in your repo being the source of truth, not on conversation history.

## Where McLoop fits

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/orchestra/design/figures/triad.png" alt="McLoop architecture" width="85%">
</p>

The diagram is McLoop's runtime shape. Each labeled element corresponds to a real code path:

- **Input** is the current `PLAN.md`. McLoop's loop pulls the next actionable task (`bob_tools.planfile.next_tasks`) and hands it to the Architect role.
- **Architect** is the planning surface that decides what happens next. In McLoop today this is the combination of the canonical `PLAN.md` (the design), the threshold evaluator in `mcloop.ledger_pause.evaluate_and_maybe_pause` (which reads the Plan Ledger after every task settles), and — when a threshold rule fires — the recursive invocation of [Duplo's re-author path](https://github.com/mhcoen/bob/tree/main/packages/duplo) via `mcloop.ledger_pause.auto_reauthor` &rarr; `duplo.reauthor.reauthor_plan`. The plan changes when the evidence in the ledger says the current approach is not working. This is the dotted blue arrow at the bottom of the figure: McLoop &rarr; Duplo &rarr; new Input.
- **Reviewers** are McLoop's pre- and post-commit review surfaces. The pre-commit reviewers are the orchestra-backed multi-model coding patterns described below (`draft_then_adjudicate`, `propose_critique_synthesize`, the council patterns) — a configurable number of reviewer roles read the Architect's framing and produce text-only critiques before any file is touched. The post-commit reviewer is the continuous code reviewer (`mcloop.reviewer`, enabled per project) that sends each commit's diff to a second model and writes findings to `.mcloop/reviews/` for the next loop iteration to absorb.
- **Coders** are the per-task code-edit sessions (`mcloop.code_edit.invoke_code_edit`). Each task launches a fresh CLI session with a clean context. When orchestra is configured, the coder role is the edit-agent at the end of a multi-role workflow; otherwise it is a direct Claude Code or Codex invocation. Either way exactly one invocation per attempt mutates the workspace.
- **Worktree** is the project tree the coder writes into. McLoop's investigation mode (`mcloop investigate`) creates a sibling git worktree per investigation (`mcloop.worktree.create`), isolating exploratory work from the main branch.
- **Git** is the convergence point. Every Coder commits to Git; the canonical mutation through `bob_tools.planfile` updates `PLAN.md` in the same commit. Nothing crosses into committed state without passing the test/lint/build gate.
- The **loop arc** wrapping McLoop is the iteration: tasks settle, evidence accumulates in the Plan Ledger, the threshold evaluator fires when warranted, Duplo re-authors, McLoop continues against the new plan. This is the recursive invocation that distinguishes McLoop from agent runners that treat the task file as a static input.

McLoop is part of the [bob ecosystem](https://github.com/mhcoen/bob), a deterministic control plane for stochastic agents. The other components:

- **[bob-tools](https://github.com/mhcoen/bob/tree/main/packages/bob-tools)** provides the `planfile` library that defines `PLAN.md`'s formal grammar, the canonical-form validator, the mutation API, and the everything log that captures every action the system takes. McLoop is built on this foundation.
- **[duplo](https://github.com/mhcoen/bob/tree/main/packages/duplo)** generates plans from product specifications and re-authors plans against accumulated execution evidence. McLoop calls into duplo when re-author thresholds fire.
- **[orchestra](https://github.com/mhcoen/bob/tree/main/packages/orchestra)** is the workflow runtime McLoop uses for multi-model coding patterns (draft-then-adjudicate, council, anonymous reviewers).

McLoop runs against your project. The rest of the bob stack runs under it. Install the full ecosystem by cloning the bob workspace and running `uv sync`.

## State files

McLoop maintains files in your project that serve as shared memory
between sessions:

- **PLAN.md**: The authoritative build document. A formal structured plan after McLoop canonicalizes it (assigns task IDs, normalizes structure), but it remains hand-editable — you can add tasks, reorder them, write `[RULEDOUT]` lines, change descriptions. McLoop re-validates on every load and refuses to operate on a malformed file.
- **BUGS.md**: A standalone bug backlog with checkbox items, populated by
  the reviewer (when enabled) and by the crash-handler diagnostic flow.
  When BUGS.md has unchecked items, McLoop enters bug-only mode and works
  those tasks before any feature work in PLAN.md. Same formal-document
  semantics as PLAN.md.
- **CLAUDE.md**: A manifest describing every source file. Sessions read
  it first to understand the codebase without searching, and update it
  when they add or change files.
- **NOTES.md**: Observations, edge cases, and design decisions that
  sessions notice during tasks. Accumulates across sessions for you to
  review.
- **`.mcloop/audit-report.md`**: The structured prose output written by the
  audit cycle for human review. Distinct from BUGS.md (the audit does not
  use the checklist mechanism).
- **IDEAS.md**: A flat scratchpad for ideas not yet ready to become
  PLAN.md tasks. Unlike the files above, McLoop never reads or modifies
  IDEAS.md during runs — it is purely human-owned state. Use
  `mcloop idea "some text"` to append a timestamped entry from the
  command line, or edit the file directly. When an idea matures, pipe
  it into [duplo](https://github.com/mhcoen/bob/tree/main/packages/duplo)
  to generate an implementable PLAN.md
  (`echo "idea text" | duplo init --from-description -`,
  then `duplo`), or move it into PLAN.md as a task by hand.
- **MAINTAIN.md**: A list of invariants — statements of desired state
  that should always hold. Unlike PLAN.md, which is a feature backlog
  of tasks to execute once, MAINTAIN.md is a set of ongoing constraints
  to enforce repeatedly. Run `mcloop maintain` to check each invariant
  in its own session: if it holds, nothing happens; if it's broken, the
  session fixes it and commits. Failures don't stop the run. Results
  are logged to `.mcloop/maintain-log.json`.

  Invariants are not limited to simple style or structural checks.
  Each runs in a full Claude Code session with the standard tool set
  plus WebFetch, so an invariant can do real work: query an external
  catalog and upgrade a dependency, verify a config file matches the
  latest API schema, ensure the project uses the most capable current
  release of a model from a provider, regenerate a manifest from the
  current source tree, check that pinned versions match the latest
  stable releases, or anything else that can be expressed as "the
  desired state is X; check it; fix it if it isn't." When an invariant
  needs human judgment (e.g. an ambiguous choice between multiple new
  options), the session asks via Telegram and waits up to ten minutes
  before falling back to its best independent decision.

  Example invariants:

  ```markdown
  - [ ] Every .py file in mcloop/ has a corresponding entry in CLAUDE.md
  - [ ] The reviewer model in .mcloop/config.json is the most capable
        current DeepSeek model on OpenRouter (use WebFetch on
        https://openrouter.ai/api/v1/models to verify, update if newer
        is available)
  - [ ] pyproject.toml requires Python 3.11 or newer
  - [ ] No deprecated ruff rules are enabled in pyproject.toml
  ```

These files live in the repo alongside your code and are the mechanism
by which one session's knowledge reaches the next.

**CLAUDE.md is tracked in git on purpose.** It contains project build
instructions, conventions, and the source-file manifest, not personal
configuration or transient state. It updates infrequently, only when
the build, platform, or application functionality changes in a way
collaborators need to see. Anyone working on the project benefits from
reading it, whether or not they use Claude Code.

McLoop is designed for the long haul. Start with a few tasks, let it run
while you do something else, add more tasks when you think of them, re-run.
It's a persistent task queue backed by a formally structured document,
not a one-shot build script. All state lives in the repository: PLAN.md,
source code, documentation, configuration, and git history. If McLoop is
interrupted, killed, or hits a rate limit, just run `mcloop` again. It
reads its own structured state, finds the next unchecked task by ID, and
picks up exactly where it left off. No session files, no databases,
nothing to reset.

**Do not edit PLAN.md or BUGS.md while mcloop is running.** McLoop
reads, modifies, and commits these files during execution (checking off
tasks, auto-checking parents, safety checkpoints). Edits made while mcloop
is running can be silently overwritten by an in-progress mutation. Kill
mcloop first, make your edits, then restart.

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
- **Automated extraction with [duplo](https://github.com/mhcoen/bob/tree/main/packages/duplo).**
  Point duplo at a product URL and it scrapes the site, downloading
  text, images, and demo videos. It extracts frames from videos at
  scene-change points, analyzes screenshots for visual design details
  (colors, fonts, layout), pulls features from documentation, and
  generates a phased PLAN.md for McLoop to execute. This lets you
  reproduce existing software, SaaS products, or websites by letting
  duplo do the design extraction and plan generation automatically.
- **Hybrid.** Start with AI-generated plans, edit them, add your own
  tasks, remove what you don't want, reorder priorities. The plan is a
  living text file you own completely.

In each case, the human controls the design. McLoop separates
design from execution cleanly enough that you can use whatever process
works for you on the design side, and the execution is mechanical.

McLoop is also not limited to building code from scratch. Any sequence
of well-defined steps that an AI coding agent can execute is a valid plan:
refactoring a module, migrating a database schema, setting up CI/CD,
auditing dependencies, generating documentation, running a series of
analyses, or performing scheduled maintenance. If you can describe it
clearly enough for a person to follow, McLoop can execute it.

## Install

McLoop is part of the bob workspace and is not installable standalone. Clone the bob repo and run `uv sync`:

```bash
git clone https://github.com/mhcoen/bob.git
cd bob
uv sync
```

This installs every workspace package (McLoop, Duplo, Orchestra, bob-tools) in editable mode with internal cross-package dependencies resolved locally. The `mcloop`, `duplo`, `orchestra`, and `bob-plan` CLIs land on `PATH`.

## Quickstart

Flags on the bare loop configure how that run executes:

```bash
mcloop                    # Run (reads PLAN.md by default)
mcloop --file other.md    # Use a different file
mcloop --dry-run          # Show what would run, don't execute
mcloop --max-retries 5    # Retry failed tasks up to 5 times (default: 3)
mcloop --model opus       # Use a specific Claude model
mcloop --cli codex        # Use Codex instead of Claude Code
mcloop --no-audit         # Skip the post-completion bug audit
mcloop --reviewer         # Enable background code reviewer
mcloop --allow-web-tools  # Enable WebFetch and WebSearch tools for sessions
mcloop --retry            # Reset failed [!] markers and retry
mcloop --stop-after-stage # Complete current stage then exit
mcloop --stop-after-one   # Run exactly one task then exit
mcloop --timeout 3600     # Per-task timeout in seconds (default: 1800)
mcloop --no-plan-ledger   # Disable Plan Ledger writes for this run
mcloop --no-auto-reauthor # Disable automatic Plan re-author on threshold crossing
mcloop --model sonnet --fallback-model opus    # Fall back to opus if sonnet fails
```

These bare-loop flags ONLY apply to the bare-loop action. Using one
with an unrelated subcommand is a parser error (e.g.
`mcloop --dry-run sync` is wrong; write `mcloop sync --dry-run`).

Subcommands change what runs. Each has its own flags:

```bash
mcloop sync               # Sync PLAN.md with the codebase
mcloop sync --dry-run     # Show sync changes without applying
mcloop audit              # Run a standalone bug audit
mcloop investigate "crash on wake from sleep"  # Debug a specific bug
mcloop investigate --log crash.log             # Debug from a log file
mcloop wrap                                    # Instrument an existing project for error capture
mcloop maintain                                # Enforce invariants from MAINTAIN.md
mcloop maintain --cli codex                    # Run maintain with the Codex backend
mcloop maintain --model opus --stop-after-one  # One maintain task, with model override
mcloop idea "some thought"                     # Append a timestamped idea to IDEAS.md
mcloop install            # Guided setup: hooks, sandbox, Telegram, permissions
mcloop install --dry-run  # Show what install would do without changing anything
mcloop uninstall          # Remove hooks and credentials installed by mcloop
mcloop uninstall --dry-run                     # Preview what uninstall would remove
```

## Writing a PLAN.md

A `PLAN.md` has two parts: a **project description**, then a **checklist**.

```markdown
# McLoop

McLoop lets you run AI coding agents for hours at a time without
babysitting them. You write a task list in PLAN.md. McLoop works through
it continuously, launching a fresh CLI session per task, running your
tests and linter, committing only if everything passes, and notifying
you of progress.

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
Most of the context the agent needs is already in your codebase: the README,
CLAUDE.md, inline comments, and the code itself. Claude reads these during
each session. That said, a reasonably detailed description is fine and often
helpful, especially for technology choices, constraints, conventions, or
anything you want every session to keep in mind.

Because each session starts fresh, the CLI can only work from what's in the
repo at that moment. Keep your description, inline comments, and any other
markdown docs current. They are the CLI's only memory of decisions made in
previous sessions.

PLAN.md is a task queue, not a complete record of how the project was built.
Changes made outside McLoop, whether in an editor, an interactive
session, or by hand, are not reflected in the file. The codebase itself is the
source of truth. PLAN.md drives what happens next, but it cannot reproduce what
already happened.

You can close this gap with `mcloop sync`, which launches a
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

### Task identifiers

The first time McLoop processes a PLAN.md, it assigns each task a stable identifier of the form `T-NNNNNN`. The identifier persists across edits — you can reorder, rephrase, or recategorize tasks and the ID stays the same. Commits reference tasks by ID. Every entry in McLoop's audit log tags back to the task ID that produced it. The ID is the durable name for a unit of work.

You don't write the IDs yourself. McLoop assigns them on the first canonical save. You can see them in the file afterward; if you add new tasks by hand, the next canonicalization pass assigns IDs to those too.

### Subtasks

Nest subtasks with indentation. McLoop completes children before
parents, and auto-checks the parent when all children are done.
The parent task text is never sent to a CLI session. It serves as
a label for grouping. Only leaf children (the deepest unchecked
tasks) are executed.

```markdown
- [ ] Set up database
  - [ ] Create users table
  - [ ] Create sessions table
  - [ ] Add indexes
- [ ] Write login endpoint
```

The deterministic API behind McLoop refuses to manually check off a parent whose subtasks are incomplete. Parents auto-check when (and only when) all their children check off.

### Task markers

| Marker | Meaning |
|--------|---------|
| `- [ ]` | Pending. McLoop will pick this up. |
| `- [x]` | Completed |
| `- [!]` | Failed. McLoop gave up after max retries. |
| `[USER]` | Requires human action. McLoop pauses and sends a Telegram notification. |
| `[AUTO:<action>]` | Automated observation (process monitor, app interaction). |
| `[BATCH]` | Combines a parent and its subtasks into a single session. |
| `[RULEDOUT]` | Records a failed approach so it is not repeated. |

Markers must appear at the beginning of the task description, immediately after the task ID. Inline mentions in prose are not interpreted as markers; the grammar is strictly leading-position. You can manually edit the checkbox state. To retry a failed task, change `[!]` back to `[ ]` and re-run, or use `mcloop --retry` to reset all failed tasks at once.

## How McLoop works

```
1. Safety commit all tracked modified files (skipped if clean)
while unchecked items remain in PLAN.md or BUGS.md:
    2. Find next unchecked item (BUGS.md first, then PLAN.md)
    3. Launch a fresh CLI session with a clean context.
       The agent receives: project description + current task + your codebase.
       On retries, the previous error output is included so Claude can fix it.
    4. Verify the session produced meaningful file changes
    5. Run targeted checks (lint + tests for changed files only)
    6. If checks pass  -> commit, push, check the box (via the deterministic
                          mutation API), notify, continue
    7. If checks fail  -> retry with error context (up to --max-retries)
    8. If retries exhausted -> mark [!], notify, stop
    9. If rate-limited -> pause, wait for reset, resume
       If session-limited -> poll every 10 minutes, resume when limit resets
   10. At a phase boundary (current phase fully checked):
       run full test suite, run build. If both pass, advance to the
       next phase and continue. If `--stop-after-stage` is set, break
       here instead. If the completed phase was the last phase, break
       and fall through to the audit cycle.
11. When all phases are complete, run bug audit/fix cycle (unless --no-audit)
12. Print summary with elapsed time and whitelist suggestions
```

Checking off a task is not a string replacement. It's a mutation through the deterministic planfile API: the task's state field is updated, the canonical form is rewritten, the audit log records the transition with the task ID, and the file is committed. The agent never writes to PLAN.md directly; only McLoop's mutation path does, and that path enforces every invariant the grammar requires.

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
exits. The state includes the task ID of what was running, how long it
had been active, the last 20 lines of output, and what phase McLoop
was in (task session, checks, audit, or user prompt).

The next time you run `mcloop`, it detects the saved state and
prompts you:

```
  Previous run was interrupted during task phase (2026-03-13T11:02:44)
  T-000142: Add unit conversion parser
  Running for 3m 12s
  Last output:
    Running pytest... 8 tests failed in test_parser.py

  (r)etry / (d)escribe what went wrong / (s)kip / (q)uit
```

**Retry** proceeds normally, picking up the unchecked task by ID.
**Describe** lets you type what went wrong. McLoop records your
description as a `[RULEDOUT]` entry in PLAN.md under the task and
appends it to `.mcloop/eliminated.json`, so the next attempt knows
not to repeat the same approach. **Skip** marks the task as failed
(`[!]`) and moves on. **Quit** exits.

Resumption uses structured task state, not text matching. The interrupted task's ID identifies it precisely; McLoop looks up that task in the canonical PLAN.md and resumes against it regardless of any edits you made to the surrounding tasks while McLoop was stopped.

The prompt adapts to the interrupted phase. Audit interruptions
offer resume/skip/quit. User prompt interruptions resume
automatically with no prompt.

### Model fallback

Use `--fallback-model` to automatically escalate to a stronger model
when the primary model fails or is rate-limited:

```bash
mcloop --model sonnet --fallback-model opus
```

The fallback triggers in two situations. First, if the primary model
is rate-limited mid-task, McLoop switches to the fallback model
immediately and continues the retry loop. When the rate limit clears,
it switches back to the primary model. Second, if a task exhausts
all retries on the primary model, McLoop retries the task from
scratch using the fallback model (with the same retry count) before
marking it failed. This lets you run most tasks on a cheaper or
faster model and only use the stronger model for tasks that need
it. If no `--fallback-model` is set, behavior is unchanged.

### Checkpoint exits

Two flags let you limit how far a single mcloop invocation goes:

```bash
mcloop --stop-after-stage   # Complete the current stage, then exit
mcloop --stop-after-one     # Run exactly one task, then exit
```

**`--retry`** resets all failed-task markers (`[!]` back to `[ ]`) in
PLAN.md and BUGS.md before starting, so previously failed tasks
are retried. Use after fixing the underlying cause of a failure. The reset goes through the planfile API and is recorded in the audit log.

**`--stop-after-stage`** runs the current stage to completion (including
the full-suite check and build at the stage boundary), then exits with
success instead of advancing to the next stage. Use this for overnight
runs where you want to review the output of each stage before continuing,
or to validate that a stage passes before committing to the next one.
This flag is ignored in bug-only mode (no stages); mcloop prints a
warning and proceeds normally. Without this flag, mcloop's default at
a phase boundary is to advance to the next phase and continue.

**`--stop-after-one`** runs exactly one checkable leaf task and exits.
If the next task is part of a `[BATCH]` parent, the batching logic is
bypassed: only that single task runs in its own session and is committed
normally. Use this to inspect one change at a time, or to test that the
first task in a plan works before letting mcloop run the rest. This flag
works in all modes including bug-only and maintain.

Both flags produce a distinct exit notification. `--stop-after-one`
emits "Stopped after one task as requested". `--stop-after-stage`
emits "{phase name} complete. Run mcloop again to start {next phase}."
so you can distinguish a stage-checkpoint exit from a normal
completion or a failure. The stop check happens at a clean boundary:
after a successful commit and check-off, before pulling the next task.

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

The easiest way to configure unattended operation is `mcloop install`:

```bash
mcloop install            # Interactive guided setup
mcloop install --dry-run  # Preview what would be changed
```

This walks you through the full setup: verifying that Claude Code is
installed, copying hook scripts to `~/.mcloop/hooks/`, merging hook
entries into `~/.claude/settings.json`, prompting for Telegram
credentials (or detecting them from the environment), configuring
the sandbox, and installing a recommended permissions baseline to
`~/.mcloop/recommended-permissions.json` for you to review and merge
manually. It also installs a `SessionStart` hook
(`session-start-hook.py`) that checks for pending relay messages
when a Claude Code session begins.

To undo the setup, run `mcloop uninstall` (or `mcloop uninstall
--dry-run` to preview). This removes hook entries from
`~/.claude/settings.json`, deletes `~/.claude/telegram-hook.env`,
`~/.mcloop/hooks/`, `~/.mcloop/config.json`, and
`~/.mcloop/recommended-permissions.json`. It leaves project-level
`.mcloop/` directories, PLAN.md files, logs, `permissions.allow`
entries, and the sandbox setting untouched.

If you prefer to configure manually, copy `settings.example.json`
from this repo to `~/.claude/settings.json` (or merge it with your
existing settings), then update the hook path. Note that
`settings.example.json` uses the `"matcher"` wrapper format with a
timeout, while `mcloop install` writes a simpler flat format without
`"matcher"` or `"timeout"`. Both work. The `"matcher": "Bash"` form
restricts the hook to Bash tool calls only, while the flat form runs
on all tool calls. The flat form is what McLoop needs since the
permission hook handles all tool types (including MCP blocking).

Manual example using the `settings.example.json` format:

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

```json
{
  "checks": ["ruff check .", "ruff format --check .", "pytest"],
  "check_timeout": 600
}
```

The `check_timeout` key sets the per-command timeout in seconds
(default: 300). Increase this for projects with large test suites.

Check commands run in parallel using `concurrent.futures`, so the
total check time is the duration of the slowest command, not the
sum of all commands.

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

### Pytest optimizations

At the start of every run, mcloop checks the target project's
`pyproject.toml` for parallel test execution support. If
`pytest-xdist` is not configured, mcloop adds it along with
`pytest-timeout` to the project's dependencies and configures
`addopts = "-n auto"` and a 60-second per-test timeout in
`[tool.pytest.ini_options]`. This is idempotent and ensures every
project mcloop works on benefits from parallel, timeout-guarded
tests from the first run.

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

Stages are part of the formal grammar. Each stage has a stable phase identifier that McLoop's audit log records against every action taken during that stage. Without stage headers, McLoop runs all tasks in one go as before.

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
children into a single CLI session:

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
task prompt so the agent knows not to repeat them:

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

After all phases complete, McLoop automatically runs two rounds of bug
auditing (unless `--no-audit` is passed). Each round follows the
same cycle:

1. **Find bugs.** A session reads the entire codebase and writes
   findings to `.mcloop/audit-report.md`. Only actual defects are
   included: crashes, incorrect behavior, unhandled errors, and
   security issues. Style issues and refactoring suggestions are
   excluded. If the report already exists, new findings are appended
   rather than replacing what's there.

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

If McLoop starts and finds an existing `.mcloop/audit-report.md`, it
skips the audit and resumes the fix cycle directly.

The audit report file is distinct from BUGS.md. BUGS.md is the
checkbox-driven backlog the run loop pulls tasks from (populated by the
reviewer and crash diagnostics); `.mcloop/audit-report.md` is the
structured prose output the audit cycle produces and consumes
internally.

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

Investigation sessions have WebFetch and WebSearch tools enabled
by default so the agent can research APIs and look up known issues.
For regular task sessions, these tools are disabled unless you pass
`--allow-web-tools`.

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
fix tasks into BUGS.md, and works only those tasks. It does not touch
feature tasks in PLAN.md, start the next phase, or run the
audit cycle. It fixes, verifies (by relaunching the app to confirm the
error no longer occurs), and exits. You run `mcloop` again for feature
work once bugs are clear.

If you say no, McLoop skips the bugs and continues with normal
feature work. The bugs stay in `.mcloop/errors.json` for next time.

Bug tasks (from BUGS.md) are treated differently from feature tasks:

- **Mandatory code changes.** Bug tasks receive a prompt that
  explicitly states the described behavior is confirmed broken and
  code modifications are required. A session that exits without
  changing files is treated as a failure, not as "already satisfied."
  This prevents sessions from falsely concluding a bug is fixed
  because a named function already exists.

- **No auto-check on zero diff.** For feature tasks, if a session
  produces no file changes but all checks pass, mcloop infers the
  work was already done and auto-checks the task. This heuristic
  is disabled for bug tasks. If you filed a bug, it's broken;
  a zero-diff session means the fix wasn't applied.

BUGS.md has absolute priority. If it contains unchecked items,
`find_next` returns those before any feature tasks in PLAN.md.

### How it works

McLoop detects the project language and injects error-catching
code into source files, delimited with markers
(`// mcloop:wrap:begin` / `// mcloop:wrap:end` for Swift,
`# mcloop:wrap:begin` / `# mcloop:wrap:end` for Python). The
canonical wrapper source is stored in `.mcloop/wrap/` so McLoop
can re-inject it if the agent strips the markers during a task.

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
the diff out for review. The reviewer checks for bugs, logic errors,
unhandled exceptions, resource leaks, and missing edge cases. This
never blocks the main loop. The review runs in a detached subprocess
while McLoop continues to the next task.

Three review backends ship today, selected by the `backend` field in
the reviewer config:

- `rest` (the default): hits any OpenAI-compatible HTTP endpoint
  (OpenRouter, a direct provider API, Ollama). Requires `base_url`
  in the config and `OPENROUTER_API_KEY` in the environment. Bills
  per token through whichever endpoint you point it at.
- `claude_code`: routes the same prompt through the user's existing
  Claude Code subscription via the orchestra ClaudeCodeTextAdapter.
  No API token in the environment, no per-token billing for review.
- `codex`: routes through the user's ChatGPT subscription via the
  orchestra CodexTextAdapter. Same subscription billing story as
  claude_code.

Findings are written to `.mcloop/reviews/` as JSON. At the start of
each loop iteration, McLoop collects any completed reviews. Low- and
medium-confidence findings are added to the rolling session context so
the next task is aware of them. If a single commit produces three or
more high-confidence error-severity findings, McLoop escalates by
appending a fix task to BUGS.md, which has absolute priority over
feature tasks in PLAN.md.

The reviewer sends both the diff and the enclosing functions from
each changed file (imports plus only the functions containing
changes, with line numbers). This gives the model enough context to
avoid false positives about undefined variables or missing imports
without sending entire files.

The reviewer is disabled by default. To enable it, add a `reviewer`
section to `.mcloop/config.json` in your project directory with
`"enabled": true`. The required fields depend on the backend.

Rest backend (the default when `backend` is omitted):

```json
{
  "reviewer": {
    "enabled": true,
    "model": "deepseek/deepseek-v3.2",
    "base_url": "https://openrouter.ai/api/v1"
  }
}
```

```bash
export OPENROUTER_API_KEY=your-key-here
```

Claude Code subscription backend:

```json
{
  "reviewer": {
    "enabled": true,
    "backend": "claude_code",
    "model": "claude-opus-4-7"
  }
}
```

Codex (ChatGPT subscription) backend:

```json
{
  "reviewer": {
    "enabled": true,
    "backend": "codex",
    "model": "gpt-5.5"
  }
}
```

The two subscription backends do not consume API tokens. They reuse
the auth your `claude` or `codex` CLI already holds, so you pay once
through the subscription rather than per-review. The `base_url` and
`OPENROUTER_API_KEY` fields are ignored for these backends.

Alternatively, pass `--reviewer` on the command line to enable the
reviewer without setting `"enabled": true` in the config.

For the rest backend, any OpenAI-compatible endpoint works:
[OpenRouter](https://openrouter.ai), a direct provider API, or a
local server like [Ollama](https://ollama.com) (set `base_url` to
`http://localhost:11434/v1` and `OPENROUTER_API_KEY` to any non-empty
string). The model is your choice. A fast, cheap model works well
since it only reviews diffs and surrounding functions, not full
codebases.

McLoop prints the reviewer status at startup when configured. The
status format depends on the backend: `Reviewer: deepseek/deepseek-v3.2
via openrouter.ai (API key set)` for rest, `Reviewer: gpt-5.5 via
Codex (subscription)` for codex, `Reviewer: claude-opus-4-7 via Claude
Code (subscription)` for claude_code. Stale review files older than 24
hours are cleaned up automatically.

## Multi-model coding patterns via Orchestra

McLoop can route the per-edit invocation through
[Orchestra](https://github.com/mhcoen/bob/tree/main/packages/orchestra),
a deterministic runner that coordinates multiple models per coding
decision. Instead of one model writing each fix, two or three text
models advise on the approach before a single edit-capable model
performs the actual file changes. The outer loop (retry, rate-limit
detection, success classification, Telegram approval, audit cycle) is
unchanged. Only the inner edit invocation goes through Orchestra.

Three patterns ship out of the box:

- **`single`**: one edit-agent performs the edit. Equivalent to the
  default direct backend; useful as a parity baseline.
- **`draft_then_adjudicate`**: a text-role drafts the approach, a
  second text-role adjudicates or rewrites it, then one edit-agent
  performs the edit. Three model calls per edit attempt.
- **`propose_critique_synthesize`**: a text-role proposes, a
  text-role critiques, a text-role synthesizes, then one edit-agent
  performs the edit. Four model calls per edit attempt.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/orchestra/design/figures/draft_then_adjudicate.png" alt="Draft then Adjudicate architecture" width="75%">
</p>

Draft then Adjudicate, illustrated above, is the natural default for
McLoop. The drafter writes a candidate edit; the adjudicator reads
the drafter's output with the original problem statement in hand and
revises before any file is touched. The edit-agent at the end is the
only invocation that mutates the workspace.

For every pattern, exactly one invocation per attempt mutates the
workspace; the earlier roles produce text-only advice that the
final edit-agent receives in its prompt. This avoids needing
multi-writer arbitration, which Orchestra defers.

### Why this helps

The single-model failure mode that costs McLoop the most retries is
"confident draft, wrong approach." A model produces an edit that
looks reasonable, makes the change, fails the checks, then retries
with a slightly different version of the same wrong idea. A
separate adjudicator reading the draft with the original problem
statement in hand catches a class of these mistakes before any
file is touched.

A small example illustrates the idea:

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/mcloop/design/images/model-bug-finding-matrix.png" alt="Bug-finding scorecard" width="75%">
</p>

In this one example, Kimi and Opus surfaced different bugs, which is the kind of
complementarity `draft_then_adjudicate` is meant to exploit. One run on one
codebase is not evidence that any particular pairing generalizes; rerun before
relying on it. The orchestra README has the longer discussion at
[Choosing model bindings](https://github.com/mhcoen/bob/tree/main/packages/orchestra#choosing-model-bindings).

### Enabling Orchestra

Orchestra is enabled by configuring it once at `~/.orchestra/config.json`.
McLoop reads that global config to decide whether to dispatch the inner
edit through orchestra. The selection rules are simple. If the global
config declares a `code_edit` workflow with a pattern other than
`direct`, McLoop dispatches through orchestra. If the workflow is
absent or set to `direct`, McLoop uses the direct backend. Any error
loading the config falls back to direct with a stderr warning so a
misconfigured environment still makes progress.

A project-local `<project>/.orchestra/config.json` is supported as an
advanced override for individual projects that genuinely need
different bindings, but most setups should not use one. When McLoop
sees a project-local file at the start of a run, it prints a
multi-line banner to stderr that names the file and reminds the user
that the project is shadowing the global config. Delete the local
file if the override was unintended. `mcloop install` performs the
same check at install time and surfaces the banner there too, so an
accidental override does not slip past install.

If you created the local file deliberately, run
`mcloop ack-orchestra-override` from the project directory to
acknowledge it. The command writes a sha256 fingerprint of the
local config bytes to `<project>/.mcloop/orchestra-override-ack`
and silences the per-run banner from then on. Edits to the local
config change the fingerprint, invalidate the ack, and the banner
returns on the next run until you re-acknowledge.

Prerequisites:

- Install orchestra in the same Python environment as mcloop. If you
  cloned the bob workspace, `uv sync` already handles this; otherwise
  `pip install -e /path/to/orchestra`. McLoop imports orchestra
  directly via `from orchestra import run_workflow`. There is no
  subprocess boundary; orchestra has to be importable.
- A populated `~/.orchestra/config.json` with role bindings and the
  workflow pattern you want.

Minimal global config that wires `code_edit` to the
`draft_then_adjudicate` pattern with sensible defaults:

```json
{
  "roles": {
    "drafter":     { "adapter": "claude_code_text",  "model": "kimi-k2.6", "parameters": {} },
    "adjudicator": { "adapter": "claude_code_text",  "model": "opus",     "parameters": {} },
    "editor":      { "adapter": "claude_code_agent", "model": "opus",     "tools": "default", "parameters": {} }
  },
  "workflows": {
    "code_edit": { "pattern": "draft_then_adjudicate" }
  }
}
```

Save that to `~/.orchestra/config.json` and every project mcloop runs
against will pick it up. No per-project setup is required.

The `claude_code_text` adapter runs Claude Code in a constrained,
read-only configuration (no Edit, Write, Bash, or web tools). Only
the `claude_code_agent` adapter for the `editor` role mutates the
workspace. This is the workflow contract: text roles advise, the
agent role acts.

Orchestra also ships `codex_text` and `codex_agent` adapters that
run OpenAI Codex through a ChatGPT subscription, with no API
tokens consumed. They follow the same contract — `codex_text` is
read-only, `codex_agent` mutates the workspace under
`--sandbox workspace-write`. Any role in any pattern can be bound
to Codex by swapping the adapter name. For example, a Codex-driven
drafter with a Claude adjudicator:

```json
{
  "roles": {
    "drafter":     { "adapter": "codex_text",        "model": "gpt-5.5",  "parameters": {} },
    "adjudicator": { "adapter": "claude_code_text",  "model": "opus",     "parameters": {} },
    "editor":      { "adapter": "claude_code_agent", "model": "opus",     "tools": "default", "parameters": {} }
  }
}
```

This is the cross-vendor feedback loop multi-model architectures are
designed for: the drafter and adjudicator come from different model
families, with different training data and different failure modes,
so each catches mistakes the other misses.

To run the same pattern on a different model mix without editing
the global config, use `role_overrides` in the project config:

```json
{
  "workflows": {
    "code_edit": {
      "pattern": "draft_then_adjudicate",
      "role_overrides": {
        "drafter": { "model": "deepseek-v4-pro" }
      }
    }
  }
}
```

Overrides replace top-level binding keys for that workflow only.
See orchestra's README for the full two-tier merge rules.

### Verifying the integration

After enabling orchestra, mcloop's per-task log file shows
additional records when the orchestra backend ran (state
transitions, per-state durations, per-model invocation summaries).
The `CodeEditResult` mcloop receives back carries the same
`success`, `exit_code`, `log_path`, and `changed_files` fields the
direct backend produces, so retry and rate-limit logic are
unchanged. If the orchestra backend produces a structured summary,
mcloop surfaces it in the run summary at `.mcloop/runs/latest.json`
under the task's entry.

To opt out for a single project after enabling, set the pattern to
`"direct"`:

```json
{
  "workflows": {
    "code_edit": { "pattern": "direct" }
  }
}
```

McLoop falls back to the direct backend with no behavior change
from that project's perspective.

### Bug verification

The `bug_verify` workflow uses the same wiring shape but is not yet
available in orchestra's packaged workflow set. Until it lands,
leave `bug_verify` out of the project config (or explicitly set
`"pattern": "direct"`); the direct bug-verify path remains the
working default.

Note that orchestra's `orchestra run <workflow.orc>` CLI is
restricted to mock, human, and shell workflows; packaged workflows
that use `agent` actors or built-in transforms must go through the
verb surface or the library API. Mcloop uses the library API
(`from orchestra import run_workflow`) and is unaffected, but it is
worth knowing if you are exploring orchestra workflows directly
from a shell.

### Cost and latency

Multi-role patterns multiply the per-edit token usage and wall
clock by the number of roles. `draft_then_adjudicate` is roughly
3x a single-model edit; `propose_critique_synthesize` is roughly
4x. The retries-saved benefit has to outweigh that cost for the
pattern to pay off. Run on a representative task batch and
compare the run summaries before deciding which pattern to keep
on for production work.

### Beyond the per-edit patterns

McLoop only uses the three per-edit patterns above, but Orchestra is
a general workflow runner. The same machinery expresses much more
elaborate architectures. Two examples — neither currently wired into
McLoop, but available as part of Orchestra's library — show what the
shape can do.

**Council.** A framer reformulates the question. Five lens advisors
(contrarian, first principles, expansionist, outsider, executor)
answer in parallel. A chairman receives the panel outputs with their
roles known and synthesizes a verdict. Seven model calls per
invocation.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/orchestra/design/figures/council.png" alt="Council architecture" width="85%">
</p>

**Anonymous Reviewers.** A framer reformulates the question. Five
panelists (typically different models, or the same model under
different prompts) answer in parallel. Their outputs are anonymized
to letters A through E. Five reviewers critique the anonymized
panel. A synthesizer reconciles the reviews into a verdict. Twelve
model calls per invocation. Useful when the goal is to evaluate the
substance of competing answers without knowing which model or which
role produced each one.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/orchestra/design/figures/anonymous_reviewers.png" alt="Anonymous Reviewers architecture" width="85%">
</p>

**Iterate Until Acceptable** *(under development)*. A responder
writes a draft. A judge decides whether it is good enough. If not,
the judge sends it back with feedback for another round, capped at N
rounds. The responder and judge slots are themselves workflows — a
council can play the responder, a draft-then-adjudicate pair can play
the judge. The substitutability is the point.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/bob/main/packages/orchestra/design/figures/iterate_until_acceptable.png" alt="Iterate Until Acceptable architecture" width="90%">
</p>

These exist to illustrate that the per-edit patterns McLoop uses
today are the simple end of what Orchestra can express. Whether any
of these architectures are worth the cost for a given coding task is
an empirical question — measure before relying on one.

## Syncing PLAN.md

Run `mcloop sync` to reconcile PLAN.md with the actual codebase. This
launches a session that reads the project files, git history, and
existing plan, then:

1. Appends checked items for any features, fixes, or changes reflected in the
   code but not yet in PLAN.md, matching the granularity of existing items.
2. Checks off unchecked items that are already implemented in the codebase.
3. Flags problems: checked items with no corresponding code, and descriptions
   that have drifted from what the code actually does.

Before writing, McLoop shows a diff of the proposed changes and asks for
confirmation. No existing items are deleted, and all mutations go through the
deterministic planfile API so the file's canonical structure and task IDs are
preserved.

Use `mcloop sync --dry-run` to see the proposed changes without modifying
PLAN.md.

This is useful for keeping PLAN.md accurate after manual edits,
interactive sessions, or any other changes made outside McLoop.

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

Claude Code sessions are instructed via CLAUDE.md to use appshot
for visual verification rather than reinventing screenshot capture.
Requires macOS Screen Recording permission (granted once).

## Implementation notes

During each task session, the agent may notice edge cases, design decisions,
assumptions, potential issues, or anything worth revisiting later. When it
does, it appends a note to `NOTES.md` with the current date and a reference
to the task being worked on (e.g., "[T-000034] Parse Markdown to HTML").

McLoop does not act on NOTES.md. It is purely for you to review between runs.
Notes accumulate chronologically across sessions, giving you a running log of
things the agent thought were worth mentioning but weren't part of the task.
When McLoop finishes, it reminds you if NOTES.md exists.

## Logging

One log file per task attempt in `logs/`, named `{timestamp}_{task-slug}.log`.
Each log captures the full CLI output and exit code.

## Run summaries

Every `run_loop()` invocation writes a JSON summary to
`.mcloop/runs/`. The file is written on all exit paths: success,
failure, and interruption. Two files are produced:

- **`YYYYMMDD_HHMMSS_run-summary.json`** — timestamped archive.
- **`latest.json`** — a copy of the most recent summary so
  automation has a stable filename to read.

The summary schema:

| Field | Type | Description |
|---|---|---|
| `run_start` | string | ISO 8601 UTC start time |
| `run_end` | string | ISO 8601 UTC end time |
| `elapsed_seconds` | float | Total wall-clock seconds |
| `mode` | string | `"plan"`, `"bug-only"`, or `"maintain"` |
| `tasks` | array | Per-task entries (see below) |
| `checks` | array | Per-check entries |
| `full_suite_passed` | bool/null | Full test suite result |
| `build_passed` | bool/null | Build result |
| `audit_result` | string/null | `"no_bugs"`, `"fixed"`, `"failed"`, `"skipped"` |
| `terminal_status` | string | `"success"`, `"failure"`, `"interrupted"`, or `"stopped"` |
| `failure_detail` | string | Why the run failed (empty on success) |
| `stop_reason` | string | `"stop_after_stage"` or `"stop_after_one"` when `terminal_status == "stopped"`; empty otherwise |
| `stuck` | array | Task IDs and texts that could not be completed |
| `commit_hashes` | array | Git hashes for all commits produced |

Each task entry contains: `task_id`, `label`, `text`, `outcome`
(`"success"` or `"failed"`), `elapsed` (seconds), `model`, `attempts`,
and `commit_hash` (empty if the task did not produce a commit).

Each check entry contains: `command`, `passed`, and `elapsed`
(seconds).

## Token auditing

`bin/mcloop-audit` parses log directories and produces per-task cost
breakdowns including model, turn count, token usage, estimated cost,
and RTK adoption. Pass one or more log directories:

```bash
bin/mcloop-audit ~/proj/mcloop/logs ~/proj/duplo/logs
bin/mcloop-audit ~/proj/*/logs     # Glob all projects
```

Output includes per-task detail, per-project summaries, a grand
total, and an RTK report showing whether commands are being
rewritten through the proxy.

## Session environment

mcloop runs each CLI session (Claude Code or Codex) with a minimal
environment. Only essential variables are passed through: PATH, HOME,
TERM, LANG, and a few others needed for tools and terminal rendering.
API keys, cloud credentials, tokens, and other secrets are excluded
by default. This prevents the agent from accidentally using API
credits instead of a subscription, and prevents credential leakage
to commands the agent runs.

If your project needs additional environment variables (for example,
a database URL or a custom tool path), add them to `env_passthrough`
in `~/.mcloop/config.json`:

```json
{
  "env_passthrough": ["DATABASE_URL", "CUSTOM_TOOL_PATH"]
}
```

Variables listed in `env_passthrough` are copied from your shell
into each CLI session alongside the built-in allowlist. Credentials
like `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `AWS_SECRET_ACCESS_KEY`
are still excluded regardless of `env_passthrough`.

By default, the CLI uses your subscription. To use API billing
instead, set `"billing": "api"` in `~/.mcloop/config.json`:

```json
{
  "billing": "api"
}
```

mcloop automatically passes the appropriate API key for the active
CLI: `ANTHROPIC_API_KEY` for Claude Code, `OPENAI_API_KEY` for
Codex. No other credentials are passed through.

To route sessions through [OpenRouter](https://openrouter.ai), set
`"billing": "openrouter"`:

```json
{
  "billing": "openrouter"
}
```

This sets `ANTHROPIC_BASE_URL` to OpenRouter's API, passes your
`OPENROUTER_API_KEY` as `ANTHROPIC_AUTH_TOKEN`, and clears
`ANTHROPIC_API_KEY`. The `OPENROUTER_API_KEY` environment variable
must be set in your shell.

### CLI backend

mcloop supports two CLI backends: Claude Code (default) and OpenAI
Codex. Set the backend with `--cli` or in `~/.mcloop/config.json`:

```bash
mcloop --cli codex
mcloop --cli codex --model gpt-5.4
```

Codex sessions use `codex exec --ask-for-approval never --sandbox
workspace-write`, which gives the agent write access to the project
directory and `/tmp` but no network access and no ability to modify
files outside the workspace. Claude Code sessions use PreToolUse
hooks for permission control instead of an OS-level sandbox.

Both backends default to subscription billing. Set `"billing": "api"`
to use API credits instead.

### Model configuration

Set a default model in `~/.mcloop/config.json` so you don't need
`--model` on every invocation:

```json
{
  "model": "sonnet"
}
```

The `--model` flag overrides the config. If the model is not in
mcloop's known-good list for the active CLI, a warning is printed
but execution continues. Both short aliases (`opus`, `sonnet`,
`haiku`, `opusplan`) and full model strings
(`claude-opus-4-6`, `gpt-5.4`) are accepted.

### Configuration reference

All keys in `~/.mcloop/config.json`:

```json
{
  "cli": "claude",
  "model": "sonnet",
  "billing": "subscription",
  "batch": true,
  "env_passthrough": [],
  "executor": {
    "model": "deepseek/deepseek-v4-pro",
    "provider": "deepseek",
    "base_url": "https://openrouter.ai/api",
    "fallback": {"model": "sonnet"}
  },
  "sync": {
    "model": "deepseek/deepseek-v4-flash",
    "provider": "deepseek",
    "base_url": "https://openrouter.ai/api/v1",
    "fallback": {"model": "haiku"}
  },
  "reviewer": {
    "enabled": true,
    "model": "deepseek/deepseek-v3.2",
    "base_url": "https://openrouter.ai/api/v1"
  }
}
```

The legacy flat `model` key and the project-level `reviewer` section
continue to work when the role-based blocks are absent. When the new
schema is present, each role (executor, sync, reviewer) has independent
`model`, `provider`, `base_url`, and `fallback` settings, so you can run
a cheap model for sync, a stronger model for the executor, and a third
for review.

| Key | Values | Default | Description |
|-----|--------|---------|-------------|
| `cli` | `"claude"`, `"codex"` | `"claude"` | CLI backend. Override with `--cli`. |
| `model` | Any model string | None (CLI default) | Default model. Override with `--model`. |
| `billing` | `"subscription"`, `"api"`, `"openrouter"` | `"subscription"` | Billing mode. `"openrouter"` routes through OpenRouter. |
| `batch` | `true`, `false` | `true` | Whether `[BATCH]` tags are honored. Set `false` to run all subtasks individually. |
| `env_passthrough` | Array of strings | `[]` | Extra environment variable names to pass through to CLI sessions. |
| `executor.model` | Model string | Falls back to flat `model` | Model used for coding tasks. |
| `executor.provider` | `"deepseek"`, `"moonshotai"`, `"openai"`, ... | None (Anthropic) | Third-party provider for the executor. Set when the model string indicates a non-Anthropic provider. |
| `executor.base_url` | URL | `https://openrouter.ai/api` | Anthropic-compatible endpoint for the executor. |
| `executor.fallback.model` | Model string | None | Fallback model for the executor when the primary fails. |
| `sync.model` | Model string | Falls back to `reviewer.model` | Model used for NOTES.md diff summaries. |
| `sync.provider` | Provider name | None | Provider for the sync endpoint. |
| `sync.base_url` | URL | Falls back to `reviewer.base_url` | OpenAI-compatible endpoint for sync. |
| `sync.fallback.model` | Model string | `"sonnet"` | Model passed to the `claude -p` fallback when the primary sync provider fails. |
| `reviewer.enabled` | `true`, `false` | `false` | Enable background code review. |
| `reviewer.model` | Model string | Required if reviewer enabled | Model for the reviewer endpoint. |
| `reviewer.base_url` | URL | Required if reviewer enabled | OpenAI-compatible API endpoint. |

### Routing Claude Code through a third-party provider

To run the executor against a third-party Anthropic-compatible
endpoint (DeepSeek, Moonshot/Kimi, OpenAI, etc.), drop one of these
shell functions into your `~/.zshrc` or `~/.bashrc`. They start a
shell session with the right environment variables so any
`claude` invocation in that subshell talks to the chosen provider:

```bash
deepseek() {
  ANTHROPIC_BASE_URL="https://openrouter.ai/api" \
  ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY" \
  ANTHROPIC_MODEL="deepseek/deepseek-v4-pro" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek/deepseek-v4-pro" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek/deepseek-v4-pro" \
  ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek/deepseek-v4-flash" \
  CLAUDE_CODE_SUBAGENT_MODEL="deepseek/deepseek-v4-pro" \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1" \
  ENABLE_TOOL_SEARCH="1" \
  zsh
}

kimi() {
  ANTHROPIC_BASE_URL="https://openrouter.ai/api" \
  ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY" \
  ANTHROPIC_MODEL="moonshotai/kimi-k2.6" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="moonshotai/kimi-k2.6" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="moonshotai/kimi-k2.6" \
  ANTHROPIC_DEFAULT_HAIKU_MODEL="moonshotai/kimi-k2.6" \
  CLAUDE_CODE_SUBAGENT_MODEL="moonshotai/kimi-k2.6" \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1" \
  ENABLE_TOOL_SEARCH="1" \
  zsh
}
```

What each variable does:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_BASE_URL` | Anthropic-compatible endpoint that receives requests instead of api.anthropic.com. |
| `ANTHROPIC_AUTH_TOKEN` | Bearer token sent to the endpoint. Set this when using OpenRouter or another provider that gives you a non-Anthropic key. |
| `ANTHROPIC_MODEL` | Default model id Claude Code asks for. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_MODEL` / `_HAIKU_MODEL` | Routes the `opus` / `sonnet` / `haiku` aliases to provider-specific model ids so existing prompts keep working. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Model used when Claude Code spawns sub-agents. |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | Suppresses telemetry calls back to Anthropic when you are not using their API. |
| `ENABLE_TOOL_SEARCH` | Enables the deferred-tool-search feature so Claude Code can lazily load tool schemas, which most third-party endpoints support. |

Inside that subshell, `mcloop` will pick up the variables. mcloop also
applies these variables automatically when the executor model string
matches a third-party provider prefix (`deepseek/`, `moonshotai/`,
`openai/`) or one of the short aliases (`deepseek-v4-pro`,
`deepseek-v4-flash`, `kimi-k2.6`); the shell function is for invoking
`claude` interactively outside of mcloop.

## Requirements

- Python >= 3.12
- `git` on PATH (McLoop requires git for checkpointing and recovery)
- `claude` CLI on PATH (or `codex` CLI when using `--cli codex`)
- `gh` CLI on PATH (for automatic GitHub repo creation)
- macOS for iMessage notifications (Telegram works anywhere)
- Playwright (optional, for web app investigation only)

## Security notes

Prompts and model outputs may be persisted in several places McLoop
writes during a run:

- per-task transcript logs under the configured `log_dir` (default
  `logs/`), which capture the full subprocess stdout/stderr
- `.mcloop/active-pid` while a session is running, which carries
  the full command line (including the prompt for the direct path)
- when the direct path is active, the prompt also appears in live
  process listings (`ps` output) for the duration of the inner CLI
  invocation. The orchestra-backed path pipes prompts via stdin and
  does not expose them through `ps`.
- when the orchestra wrapper is in use, the JSONL run log
  (`log.jsonl` under the orchestra run directory) records state
  envelopes and adapter outputs for resume. The orchestra run
  directory is created with mode 0700 and prompt-source snapshots
  are written with mode 0600.

McLoop's own run summaries at `.mcloop/runs/latest.json` and the
dated archives record task IDs, labels, outcomes, elapsed times, model
names, commit hashes, and changed-file lists. They do not contain
prompt or model-output content.

Do not run McLoop against codebases containing live credentials,
customer data, or other sensitive material without first reviewing
what gets persisted and where.

## Development

McLoop lives in the bob workspace alongside its sibling packages (Duplo, Orchestra, bob-tools) and cannot be developed against in isolation — its dependencies (`orchestra`, `bob-tools`, `duplo`) resolve through the workspace, not PyPI.

```bash
git clone https://github.com/mhcoen/bob.git
cd bob
uv sync
```

McLoop, its sibling packages, and all dev dependencies are installed editable in a single `.venv/` at the workspace root. Run lint, format, and tests from the McLoop package directory:

```bash
cd packages/mcloop
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## Best practices

McLoop does not require its own API key or tokens, does not extract
or borrow OAuth tokens from the CLI, and does not violate
Anthropic's or OpenAI's Terms of Service. It runs the CLI in a
controlled way through the public `claude -p` or `codex exec`
interface, using whatever plan you already have (Pro, Max, etc.).
There is nothing extra to provision.

That said, McLoop will use your plan allowance aggressively. A single
McLoop run can consume in a few hours what you would normally spread
across days of interactive use. Each task launches a full CLI
session that reads files, writes code, runs tests, and iterates on
failures. The audit cycle after task completion adds further usage.
This is by design, but you should be aware of it.

Practical advice for getting the most out of your allowance:

**Use [RTK](https://github.com/rtk-ai/rtk).**  RTK is a CLI proxy
that compresses command output before it reaches the agent's
context, reducing token consumption by 60-90%. Install it and run
`rtk init --global`. McLoop's Telegram permission hook already
handles RTK-wrapped commands, so no additional configuration is
needed. This is one of the most effective ways to extend your plan
usage.

**Write detailed task descriptions.** Vague tasks cause the agent
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
you are not using the CLI interactively.

**Monitor with `rtk gain`.** If RTK is installed, run `rtk gain`
after a McLoop session to see how many tokens were saved. This helps
you gauge whether the compression is working and how much headroom
you have.

## Suggested reviewer models

The continuous reviewer described above sends diffs to an
OpenAI-compatible REST endpoint. That covers OpenRouter, direct
provider APIs, and local servers like Ollama. It does not cover
Claude Code or Codex used through their subscriptions, because
those are CLI-driven, not REST endpoints. Wiring subscription-based
Claude Code or Codex into the reviewer is on McLoop's roadmap via
the Orchestra adapters, but is not yet shipped.

The reviewer model does not need to generate code, only read diffs
and identify problems, so strong reasoning matters more than code
generation benchmarks. Cheaper models are practical because the
reviewer runs in the background on every commit.

| Model | Provider | Input /1M | Output /1M | SWE-bench | Context | Notes |
|-------|----------|-----------|------------|-----------|---------|-------|
| DeepSeek V3.2 | OpenRouter | $0.28 | $0.42 | 73.1% | 128K | Best value. 90% cache discount on repeated context. |
| GLM-5 | OpenRouter | $0.72 | $2.30 | 95.8% | 200K | Strongest open model. Near-zero hallucination rate. |
| Kimi K2.6 | OpenRouter | $0.50 | $2.80 | 76.8% | 256K | Highest open-source SWE-bench. Strong at debugging. See note below. |
| Gemini 2.5 Flash | Google | $0.30 | $2.50 | N/A | 1M | Fast, cheap, very large context window. |
| Gemini 2.5 Pro | Google | $1.25 | $10.00 | 63.8% | 1M | Strong reasoning, 1M context. Free tier available. |
| GPT-5.5 | OpenAI | $5.00 | $30.00 | 88.7% | 1M | Frontier OpenAI model. Highest published SWE-bench Verified. |
| Claude Sonnet 4.6 | Anthropic | $3.00 | $15.00 | 79.6% | 200K | For comparison. McLoop's default task executor. |
| Claude Opus 4.6 | Anthropic | $5.00 | $25.00 | 80.8% | 200K | For comparison. Strong but superseded by Opus 4.7. |
| Claude Opus 4.7 | Anthropic | $5.00 | $25.00 | 87.6% | 1M | Current Anthropic frontier. Same input price as 4.6, larger context. |

To use any of these as the reviewer, set the model string in
`.mcloop/config.json`:

```json
{"reviewer": {"enabled": true, "model": "z-ai/glm-5", "base_url": "https://openrouter.ai/api/v1"}}
```

OpenRouter model strings: `deepseek/deepseek-v3.2`, `z-ai/glm-5`,
`moonshotai/kimi-k2.6`, `google/gemini-2.5-flash`,
`google/gemini-2.5-pro`, `openai/gpt-5.5`,
`anthropic/claude-opus-4-7`. Pricing may vary by provider and
change over time. Check [OpenRouter](https://openrouter.ai) for
current rates.

Numbers in the table above come from each provider's published
SWE-bench Verified scores (or, in some cases, the closest available
variant); independent third-party benchmarks differ. Treat them as
order-of-magnitude guides, not as an apples-to-apples ranking. For
a local observation about how these models behaved on this codebase
on one occasion, see the bug-finding example in [Multi-model coding
patterns via Orchestra](#multi-model-coding-patterns-via-orchestra).
It is one data point, not a benchmark.

## License

MIT. See [LICENSE](LICENSE).

## Author

**Michael H. Coen**  
mhcoen@gmail.com | mhcoen@alum.mit.edu  
[@mhcoen](https://github.com/mhcoen)
