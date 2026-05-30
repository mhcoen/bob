# Bob

<p align="center">
  <img src="images/bob-poster.png" alt="Bob is back. Vibe coding does not have Bob." width="40%">
<p align="center">
<video width="320" height="240" controls>
  <source src="video/bob_is_back_dont_let_friends_vibe_code.mp4" type="video/mp4">
  Your browser does not support the video tag.
</video>
</p>

Bob is a tool that autonomously builds non-slop software. You describe what you want; Bob builds it. Here's how.

Software engineering used to have a guy named Bob. Bob's job was to
look at what you were doing, ask why, and tell you to slow the hell
down. Bob read the spec before the code. Bob read the code before the
commit. Bob read the commit before the deploy. Bob was annoying. Bob
was correct. I miss Bob.

Vibe coding does not have Bob.

## What Bob actually believes

This is the part that makes Bob different from every coding agent you
have been sold, so it gets said plainly.

A language model is a slot machine that talks. Every call is one
sample: confident, fluent, and wrong often enough to matter. No prompt
fixes this, because it is a property of sampling, not of skill. The
fashionable response is to hand that slot machine *more* control — let
the agent decide what to do next, manage its own memory, steer its own
loop. Bob thinks that is exactly backwards. You do not put the
unreliable thing in charge of deciding what happens next.

So here is Bob's one rule, the one everything else falls out of:

> **The stochastic actor never holds the control flow. The framework
> is deterministic. The model is sealed inside a replaceable cell with
> a decidable check on the door. Nothing the model produces is trusted
> until something that is not a model has checked it.**

What that buys you, and what nobody shipping a single-agent IDE has:

- **The design is the program.** Plan quality is causally and
  unfakeably linked to output quality, because the agent interprets a
  plan instead of inventing one. A bad plan fails loudly; it cannot be
  charmed into looking good.
- **The repo is the only state.** No hidden conversational memory. The
  process is replayable and crash-proof because there is nothing to
  corrupt that git does not already hold.
- **Disagreement is a primitive, not a product.** Drafting, critique,
  and reconciliation are configuration. Substitutability — a council
  *is* a responder, a pair *is* a judge — is a first-class property,
  not an afterthought bolted on later.
- **The audit runs on evidence.** A typed, deterministic, append-only
  ledger lets a plan be re-derived from execution history. The drift
  detector fires on explicit events, never a hunch, because in a drift
  detector a false positive just trains you to ignore it.

And the part that is easy to miss: **Bob is not a better agent. Bob is
not an agent at all.** Claude Code, Cursor, and the rest of the
autonomous-IDE pack are instances of the exact thing Bob refuses to
do — they let the model drive. They are point tools. Bob is a process
with a position, assembled end to end. There is no peer to line it up
against feature by feature, because the comparison is a category
error: you do not benchmark a seatbelt against a faster car.

None of the individual mechanisms is new, and Bob will be the first to
say so. Acceptance gates are old. Replayable state is old. Ensembling
and adjudication are an active research line. Careful human teams have
always worked this way — a senior reviews the junior's PR, a design
doc gets read before it is committed to, one person finds the bug and
another verifies the fix. The mistake-catching long predates LLMs.
What Bob contributes is the synthesis: those four things, combined
under the single rule that the model never steers, applied across the
whole loop. No piece survives a "show me the prior art" challenge
alone. The combination, end to end, is the contribution — and Bob
would rather state that honestly than oversell it.

The sophistication here is not a clever algorithm. It is the choice of
what to hold fixed: repo-as-only-state, text-file contracts with a
typed marker grammar, design and execution separated by making the
design executable, exactly one mutating step per attempt, a
deterministic projector over an append-only log. A less disciplined
design meets the same requirements with a database, a session manager,
and an orchestration framework, and then cannot survive being killed
mid-run. The restraint is the point.

## Bob's four main interacting components

Each is the rule above applied at a different scale. They interact: McLoop runs against plans Duplo authored, Orchestra wraps McLoop's per-task edits, and McLoop calls Duplo back to re-author the plan when the ledger says the current approach is not working.

[Duplo](packages/duplo/) is Bob creating the spec. Tell Duplo what you want, point it at a
product URL, drop in screenshots, PDFs, or a demo video, whatever you
have. Duplo produces a phased build plan. The quality of the output is
a direct function of the quality of the plan — and that is on purpose.
The plan is not notes for the agent. The plan *is* the program; the
agent is just the thing that runs it. Write a vague plan and you get
vague code, plainly, with nowhere for the model to hide it. That is
the incentive to design carefully, which the field has been busy
losing.

[McLoop](packages/mcloop/) is Bob running, testing, and debugging the build while you
sleep or binge Netflix. Autonomous coding sessions for hours or days:
fresh context per task, tests and lint after every change, only clean
code committed, automatic audit when the queue is done. Fresh context
per task is not a performance trick — it is the whole game. The state
is the repository and its git history, nothing else. There is no
private conversation only the model has seen, which is why a `kill -9`
is a shrug instead of a catastrophe. McLoop builds what Duplo
designed, and it never lets the model decide what happens next.

[Orchestra](packages/orchestra/) is grumpy Bob fighting LLM slop. Any single LLM can fail
spectacularly and sound delighted doing it. Bob doesn't like that.
Orchestra hands different models different jobs and makes them argue,
interact, and sing harmonies before anything touches the workspace.
And the argument is not hardcoded: a council can be collapsed to a
single responder, a pair promoted to a judge, a one-model edit swapped
for a five-model brawl — by changing configuration, not by rewriting
anything. Multi-model disagreement is a dial Bob can turn, not a
feature someone soldered on.

Vroom is Bob reading what shipped and asking what he should have done
differently. Vroom runs parallel auditors over the work, coalesces
what they find, and proposes a corrected or expanded plan — and it
does it from the evidence, not from vibes. Every run leaves a typed,
append-only ledger, so the plan can be re-derived from what actually
happened instead of hand-patched into looking fine. Eventually Vroom
is Bob running the whole loop himself: proposing changes on branches,
gating them on verification, merging what survives. You sleep through
that too. (How done that actually is: see Status, below. Bob does not
lie about status.)

Bring the kind of skepticism the field used to have before it fired
Bob.

## The Loop

```text
reference material
  -> Duplo: spec and phased plan        (the design becomes the program)
  -> McLoop: implementation, tests, commits   (sealed cells, gated)
  -> Orchestra: multi-model review and judgment  (composed, configurable)
  -> Vroom: audit, reflection, correction    (re-derived from the ledger)
  -> next plan
```

Every arrow crosses a check. Nothing stochastic gets across without
passing something deterministic.

## Why This Exists

AI coding tools are powerful, but a single model acting alone is not a
software engineering process. It can skip design, miss edge cases,
hallucinate APIs, repeat bad approaches, and commit plausible
nonsense — cheerfully. The problem is not that the model is weak. It
is that nothing deterministic stands between the model's output and
your repository.

Bob is the missing process layer:

- design before execution — the plan is the program
- fresh context per task — state is the repo, not a chat log
- tests and lint after every change — a real check on every boundary
- review before trust — independent draws where one gate is not enough
- audit after shipping — the ledger decides whether to re-plan, not
  the model
- explicit recovery when something breaks — because the only state is
  on disk and in git, there is always a defined state to recover to

The point is not to make coding faster at any cost. It is to make
autonomous coding slower where it must be slower and faster where it
can be faster — and to put design back at the front of software
engineering, where it lived before the field stopped writing specs.

## Status (Bob does not lie about this)

- **Duplo, McLoop, Orchestra** — in active use and built with each
  other. Real, used daily, building this.
- **The Plan Ledger** — shipped, in `bob-tools`, with its projector
  and threshold rules. This is the evidence substrate Vroom stands on.
- **Vroom as the fully closed loop** — Bob proposing changes on
  branches, gating them, merging what survives, unattended — is the
  designed end state and is being closed, not yet closed. The
  scaffolding is real; the last quarter of the loop is still being
  wired, and that is stated here on purpose, because epistemic
  discipline is the entire point of the project.

## Demo Flow

1. Start from a product URL, screenshot, PDF, video, or prose request.
2. Use Duplo to produce a spec and phased PLAN.md.
3. Use McLoop to execute the plan task by task.
4. Show tests, lint, commits, notifications, and crash recovery.
5. Use Orchestra to route a decision through multi-model disagreement.
6. Use Vroom to audit an artifact and propose a corrected version.
7. Close the loop: the corrected output becomes the next plan.

## Thesis

A coding agent is not a software engineering process. A deterministic
framework that confines, gates, and composes stochastic actors is —
and that framework, not the actors inside it, is where every
correctness guarantee in this system lives. Bob is that framework.
Also, Bob would have caught that bug in review.

## Packages

The bob ecosystem lives in this repository as a uv workspace. Each
tool is a package under `packages/`:

- **[Duplo](packages/duplo/)** — design extraction and phased plan
  generation.
- **[McLoop](packages/mcloop/)** — the autonomous execution loop.
- **[Orchestra](packages/orchestra/)** — the declarative multi-model
  workflow runner.
- **[bob-tools](packages/bob-tools/)** — shared infrastructure; the
  Plan Ledger and the formal `PLAN.md` library live here.
- **Vroom** — post-ship audit and the closing of the loop — part of
  the ecosystem, not yet public.

Each package carries its own README with the full surface and its own
honest status.

## Installation

Bob is a uv workspace. Clone the repo and run `uv sync`:

```bash
git clone https://github.com/mhcoen/bob.git
cd bob
uv sync
```

This installs every package — Duplo, McLoop, Orchestra, bob-tools —
in editable mode, with internal cross-package dependencies resolved
locally. The CLIs (`duplo`, `mcloop`, `orchestra`, `bob-plan`) land
on `PATH`.

## Subscribe

Subscribe at
[buttondown.com/bringbackbob](https://buttondown.com/bringbackbob)
for releases, demos, and first invitations to use it. Announcements
only.

## Built By

Bob is built by [Michael Coen](https://github.com/mhcoen), a computer
scientist and ML researcher whose work spans software agents,
self-supervised learning, AI security, and LLM infrastructure. He
earned his S.B., S.M., and Ph.D. from MIT, received the Sprowls
Award for outstanding dissertation in computer science, was on the
faculty at the University of Wisconsin-Madison, and co-founded
several fintech startups.

His current work focuses on hardening AI systems and working toward
1,000 useful commits/day on GitHub.

Bob is built with Bob. The system is its own existence proof: a
non-trivial multi-package codebase carried through the
Duplo → McLoop → Orchestra path is the most direct evidence available
that a deterministic framework around stochastic actors produces code
good enough to build the framework.

## Combined Telegram + RTK hook

For anyone who wants one Claude Code `PreToolUse` hook instead of two
competing ones, `packages/mcloop/telegram-permission-hook.py` does both:
Telegram approve/deny of tool calls in McLoop sessions, and automatic
[RTK](https://github.com/rtk-ai/rtk) command rewriting (`pytest` to
`rtk pytest`, `.venv/bin/pytest` to `RTK_BIN=... rtk pytest`, etc.) for
token savings in every session. A single hook avoids Claude Code's
multi-hook `updatedInput` race, and RTK rewriting is skipped automatically
when `rtk` is not on `PATH`, so the hook is safe to install with or
without RTK present.

To install, copy it into your Claude hooks directory:

```bash
cp packages/mcloop/telegram-permission-hook.py ~/.claude/hooks/
```

then register it as a `PreToolUse` hook in `~/.claude/settings.json`:

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
  { "type": "command",
    "command": "python3 ~/.claude/hooks/telegram-permission-hook.py" } ] } ] } }
```

## License

Each package carries its own license. The narrative and coordination
material in this workspace, including the abstract, the loop
description, and the framing of the Bob toolchain, is copyright 2026
Michael Coen, all rights reserved.
