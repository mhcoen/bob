# Orchestra

Orchestra is a tool for controlling how multiple LLMs interact. You
declare a workflow — who drafts, who critiques, who reconciles, who
acts — and Orchestra runs it. The workflow is a small declarative
file, and the models, prompts, and parameters are configuration.
Swapping a single-model edit for a draft-then-adjudicate pair, or a
five-model council, is a config change, not a rewrite.

It comes with a library of standard architectures and the ability to
write your own. It is built to be embedded in other systems as a
library, and it has a CLI for direct interactive use.

## Two ways to use it

**As a library, embedded in other tools.** Other systems call
`orchestra.run_workflow(...)` at decision points where one model is
not enough. McLoop (an autonomous coding loop) uses Orchestra to
route each per-edit invocation through a draft-then-adjudicate pair
or a propose-critique-synthesize trio instead of a single model.
Duplo (a planning tool) uses it for plan-extraction decisions.
Anything that calls into Orchestra inherits the same workflow library
and configuration surface.

**Directly, from the command line.** A REPL and a verb-style CLI for
testing models, comparing how different patterns answer the same
question, and building intuition for which architectures help on
which problems. This is the right surface when the goal is to
understand what the models do — not to ship something. Configuration
is shared with the library surface, so a binding tested at the CLI
moves into a library consumer unchanged.

## Why Orchestra exists

LLMs make mistakes. They confidently produce wrong answers, miss
edge cases, fixate on bad approaches, and hallucinate facts that
look plausible. Every current model does this, including the
strongest ones. A single-model loop has no mechanism for catching
these mistakes before they propagate, and the result is exactly the
buggy unmaintainable junk that gives AI-generated code its
reputation.

Multi-model architectures are not an optimization. They are a
requirement for getting usable output from these tools. A drafter
writes; an adjudicator reads the draft with fresh eyes and the
original problem statement, and rewrites what is wrong. A proposer
takes a position; a critic argues against it; a synthesizer
reconciles. A council surfaces independent perspectives before any
of them are anonymized for review. An iterative loop refines a
draft until it meets a standard. Each of these adds a checkpoint
that catches a class of mistake the prior step would otherwise
ship.

This structure is not novel. Careful human teams already work this
way. A senior engineer reviews a junior's pull request. A design
document goes through multiple readers before being committed to.
A bug report is investigated by one person and verified by another
before code changes. The mistake-catching is the whole point.
Orchestra applies the same structure to LLM workflows because the
alternative — trusting a single model's first output on real coding,
design, and debugging tasks — does not produce maintainable code.

Orchestra exists because this is the difference between LLM tools
you can ship with and LLM tools you cannot.

## Models and providers

Orchestra does not necessarily call model APIs directly. It can
drive CLI tools the user already has installed and pays for. The
shipped adapter set today is Claude Code (subscription billing, no
API tokens). Codex adapters using a ChatGPT subscription are being
added; once present, any role in any workflow — a Council
panelist, a draft-then-adjudicate drafter, anything — binds to
Codex by name, the same way it binds to Claude.

Third-party models (DeepSeek, Moonshot/Kimi, GLM, Gemini, others) are
reached by pointing Claude Code at an OpenRouter endpoint with
`OPENROUTER_API_KEY`. Those calls are billed per token by OpenRouter
at the underlying provider's rate. The role binding mechanism is the
same regardless of billing — the model name in the binding determines
the provider, and environment variables determine the endpoint.

Some providers offer free access. Hugging Face's Inference API has a
free tier covering many open-weight models. Google's Gemini has a
free tier with rate limits. Local servers like Ollama are free at the
cost of running the model on your own hardware. Any of these can be
plugged in by pointing the adapter's base URL at the appropriate
endpoint.

DeepSeek and Kimi (Moonshot) deserve a specific mention. Both are
orders of magnitude cheaper per token than Claude Opus or Sonnet,
and on the [bug-finding example](#choosing-model-bindings) below
Kimi found more unique bugs than Opus did. One observation does not
generalize, but the price difference is enough that they are worth
trying for any role where you would otherwise default to a premium
model.

Models currently in development use:

- **Claude Opus** and **Claude Sonnet** (Anthropic, via subscription).
  Used for synthesis-shaped roles where the goal is reconciling or
  judging.
- **Kimi K2.6** (Moonshot, via OpenRouter). Used for divergent-thinking
  roles — drafter, proposer, contrarian.
- **DeepSeek V4 Pro** (DeepSeek, via OpenRouter). Used as a third
  opinion when confirmation matters more than novelty.

This is a snapshot of what works on this codebase today, not a
recommendation. Model versions change. Providers add and remove
endpoints. Re-evaluate before relying on any specific binding.

## The architectures

These are the patterns that ship today, plus two designs currently
under development. All of them are concrete `.orc` files (or will be)
and all of them run on the same executor.

### Single

One model answers. Useful as a parity baseline and for queries where
deliberation is not worth the latency.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/single.png" alt="Single architecture" width="50%">
</p>

### Draft then Adjudicate

A drafter writes a first answer. An adjudicator reviews and revises.
The adjudicator catches the drafter's obvious mistakes before they
ship. Three model calls.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/draft_then_adjudicate.png" alt="Draft then Adjudicate architecture" width="70%">
</p>

### Propose, Critique, Synthesize

A proposer takes a position. A critic argues against it. A
synthesizer reconciles the two. Useful for contested or open-ended
questions where the value comes from the disagreement, not the
agreement. Four model calls.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/propose_critique_synthesize.png" alt="Propose, Critique, Synthesize architecture" width="85%">
</p>

### Council

A framer reformulates the question. Five lens advisors (contrarian,
first principles, expansionist, outsider, executor) answer in
parallel. Their outputs are anonymized to letters A through E. Five
reviewers critique the anonymized panel. A chairman synthesizes a
verdict. Twelve model calls. Useful for high-leverage decisions
where a single perspective is not enough and the structured
disagreement matters.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/council.png" alt="Council architecture" width="85%">
</p>

### Iterate Until Acceptable *(under development)*

A responder writes a draft. A judge decides whether it is good
enough. If not, the judge sends it back with feedback for another
round, capped at N rounds. The responder and judge slots are
themselves workflows — a council can play the responder, a
draft-then-adjudicate pair can play the judge. The substitutability is
the point.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/iterate_until_acceptable.png" alt="Iterate Until Acceptable architecture" width="90%">
</p>

### Parallel Thinking *(under development)*

N models analyze the input in parallel. A reconciler digests their
outputs into a single response. The slots are deliberately
underspecified: an analyst can be doing analysis, summarization,
translation, scoring, or anything else expressible as a prompt. A
reconciler can synthesize, vote, measure inter-model agreement, flag
divergence, or check whether one model's output looks like a
paraphrase of another's. The shape is fan-out then fan-in; the
behavior comes from the prompts in the configuration.

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/figures/parallel_thinking.png" alt="Parallel Thinking architecture (N=2 and N=3)" width="70%">
</p>

### Custom architectures

The architectures above are the ones in the library. They are not
the ones you are limited to. A workflow is a `.orc` file with named
states, roles, and transitions; the surface syntax is documented in
`design/orchestra-grammar.md`. Writing one looks like writing a small
state machine. The packaged workflows in `orchestra/workflows/` are
the working examples.

The point of the library is not to enumerate every useful pattern.
It is to make the patterns trivial to express, so the interesting
question is not "how do I wire three models together" but "which
three models, with which prompts, for this problem."

## Quick start

Install:

```
pip install -e '.[dev]'
```

Create `~/.orchestra/config.json`:

```json
{
  "verbs": {
    "ask":     { "workflow": "ask_single" },
    "refine":  { "workflow": "ask_propose_critique_synthesize" },
    "council": { "workflow": "ask_council" },
    "pair":    { "workflow": "ask_draft_then_adjudicate" }
  },
  "roles": {
    "responder":   { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "drafter":     { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "adjudicator": { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "proposer":    { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "critic":      { "adapter": "claude_code_text", "model": "sonnet", "parameters": {} },
    "synthesizer": { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "framer":         { "adapter": "claude_code_text", "model": "sonnet", "parameters": {} },
    "contrarian":     { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "first_principles":{ "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "expansionist":   { "adapter": "claude_code_text", "model": "sonnet", "parameters": {} },
    "outsider":       { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "executor_lens":  { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "reviewer":       { "adapter": "claude_code_text", "model": "sonnet", "parameters": {} },
    "chairman":       { "adapter": "claude_code_text", "model": "opus", "parameters": {} }
  },
  "workflows": {
    "ask_single":                       { "pattern": "ask_single" },
    "ask_propose_critique_synthesize":  { "pattern": "ask_propose_critique_synthesize" },
    "ask_council":                      { "pattern": "ask_council" },
    "ask_draft_then_adjudicate":        { "pattern": "ask_draft_then_adjudicate" }
  }
}
```

Then:

```
orchestra ask what is the capital of france
orchestra pair explain liskov substitution to someone who knows oop basics
orchestra council should I rewrite this service in rust or stay in go
```

The model's response is the only thing printed. The full multi-model
log lands at `~/.orchestra/runs/<run_id>/log.jsonl` if you want to see
the intermediate steps.

The role bindings for `ask_council` (framer, the five lens roles,
reviewer, chairman) are eight separate bindings. The validator
rejects an `ask_council` invocation that does not bind all eight.
The other conversational verbs need fewer: `ask` only `responder`;
`refine` only `proposer`, `critic`, `synthesizer`; `pair` only
`drafter` and `adjudicator`. Drop the bindings you do not use.

## CLI surface

### Verb invocations

```
orchestra <verb> <question>
```

No quoting around the question. The verb names a workflow; the rest
of the line is the question. Verbs are user-defined in
`~/.orchestra/config.json` — rename, add, remove freely.

### Interactive REPL

Bare `orchestra` (no arguments) drops you into a prompt:

```
$ orchestra
orchestra REPL. /help for commands, /exit to quit.
orchestra> what is the capital of france
Paris.
orchestra> what about its population
About 2.1 million in the city proper.
orchestra> /use council
workflow -> council
orchestra (council)> should I rewrite this service in rust
[council answer, with the prior two turns threaded in as context]
orchestra (council)> /save session.md
saved 3 turn(s) to session.md
```

The REPL threads the running transcript into each prompt as a
`Prior conversation:` block, so models see what was just discussed.

You can also dispatch a single turn to a different workflow without
switching the session, by typing the workflow name as the first word
of the line:

```
orchestra> council should I rewrite this service in rust
[council answer]
orchestra> what about latency
[ask answer; session workflow is still ask, council was per-turn]
```

Slash commands (in-process, no model calls):

- `/help` — list slash commands and configured workflows.
- `/use [name]` — show or switch the active workflow.
- `/clear` — drop the in-memory transcript.
- `/history` — print the transcript so far.
- `/save <path>` — write the transcript to disk. `.json` for JSON,
  anything else for markdown.
- `/exit`, `/quit` — leave the REPL. Ctrl-D also exits.

A single Ctrl-C cancels the current input line. A second Ctrl-C
within one second exits the REPL.

### Direct workflow execution

Bypass verbs and run a workflow file directly:

```
orchestra run path/to/workflow.orc --input key=value
orchestra resume <run_id>
```

Useful for custom `.orc` files not named in a verb binding, or for
debugging.

### Help

```
orchestra help
orchestra help <verb>
```

The first lists every configured verb and the workflow it runs. The
second shows the required roles for that verb's workflow and the
binding configured for each, flagging unbound roles as
`NOT CONFIGURED`.

## Library surface

Embed Orchestra in another tool by importing `run_workflow`:

```python
from pathlib import Path
from orchestra import run_workflow
from orchestra.config import load_config

result = run_workflow(
    "code_edit",
    inputs={
        "instruction": "...",
        "context": "...",
        "prior_errors": "",
        "eliminated": [],
        "project_dir": "/path/to/project",
        "description": "...",
        "task_label": "T1",
        "check_commands": ["pytest"],
        "is_bug_task": False,
    },
    config=load_config(Path("/path/to/project")),
    invocation_options={
        "model": "opus",
        "timeout": 1800,
        "log_dir": "/path/to/project/logs",
    },
)
print(result.summary)
```

`load_config(project_dir)` returns the merged view of the global and
project configs; `load_config()` with no argument returns the global
config alone. The library API is opaque to where the config came
from — it runs against whatever config object you pass in.

When neither a global nor a project config exists, `code_edit`
defaults to the `single` pattern with a default editor binding so
consumers like McLoop work out of the box without configuration.

McLoop integrates through this surface. See
`design/orchestra-mcloop-integration-plan.md` for the contract.

## Configuration

Orchestra reads up to two config files and merges them:

1. `~/.orchestra/config.json` — global. Roles, verbs, and workflows
   shared across all projects.
2. `<project>/.orchestra/config.json` — project, optional. Overrides
   specific entries.

The merge rule is replace, not nest: a role, verb, or workflow
defined in the project config replaces the global entry of the same
name in full. Entries the project does not redefine are inherited.

A project that wants to override only the editor model:

```json
{
  "roles": {
    "editor": {
      "adapter": "claude_code_agent",
      "model": "deepseek-v4-pro",
      "tools": "default",
      "parameters": {}
    }
  }
}
```

That project keeps every other role from the global config and just
swaps the editor's binding for itself. The CLI and any library
consumer (McLoop, Duplo, your own tool) both pick up the merged view,
so the override applies consistently.

### Verbs

Verb names are user-defined. Each verb names which workflow runs.

```json
"verbs": {
  "fast":    { "workflow": "ask_single" },
  "careful": { "workflow": "ask_propose_critique_synthesize" }
}
```

Then `orchestra fast ...` and `orchestra careful ...` work.

### Roles

A role binding has three required keys (plus an optional fourth):

- `adapter` — which adapter implementation. Currently shipped:
  `claude_code_text` (read-only Claude Code, used for text-role
  states) and `claude_code_agent` (full edit-capable Claude Code,
  used for edit-agent states).
- `model` — model name passed to the adapter (`opus`, `sonnet`,
  `kimi-k2.6`, `deepseek-v4-pro`, etc.).
- `parameters` — adapter-specific extras, usually `{}`.
- `tools` — edit-agent roles only. Tool restriction for the agent,
  e.g. `"default"` for the standard McLoop tool set.

The packaged ask workflows use `responder` for the final-answer state
(read-only, text adapter). The packaged code-edit workflows use
`editor` for the final-edit state (workspace-mutating, agent
adapter). Different role names keep the two adapter kinds from
colliding under one binding. A single config can define both:

```json
"roles": {
  "responder": { "adapter": "claude_code_text",  "model": "opus", "parameters": {} },
  "editor":    { "adapter": "claude_code_agent", "model": "opus", "tools": "default", "parameters": {} }
}
```

## Choosing model bindings

The role-to-model bindings in the quick-start config are not
arbitrary, but they are not benchmarks either. A small example
illustrates the kind of model-complementarity these patterns are
designed to exploit:

<p align="center">
  <img src="https://raw.githubusercontent.com/mhcoen/orchestra/main/design/images/model-bug-finding-matrix.png" alt="Bug-finding scorecard" width="60%">
</p>

In this one example, four models each looked for bugs in the McLoop
codebase independently. Different models surfaced different bugs.
That is the kind of complementarity `draft_then_adjudicate` and
`propose_critique_synthesize` are meant to exploit — pairing models
whose failure modes don't overlap.

This is one observation on one codebase. It is not a benchmark, and
the rankings will differ on other tasks and as model versions update.
Re-evaluate before relying on any specific binding.

## Layout

```
orchestra/
  loader/         # parser + validator + workflow lookup
  store/          # artifact store (SQLite-backed)
  registry/       # profile registry
  executor/       # state machine + parser dispatch
  adapters/       # adapter interface + Claude Code adapters + mocks
  log/            # JSONL logger and reader
  resume/         # log replay + resume hook dispatch
  workflows/      # packaged .orc files and prompt templates
  prompts.py      # prompt builders (verbatim lifts from McLoop)
  api.py          # run_workflow entry point + WorkflowRunResult
  config.py       # .orchestra/config.json schema, loader, merge
  cli.py          # command-line entry point
tests/
  fixtures/slice1/   # echo.orc and prompt files
  test_*.py          # unit, end-to-end, and parity tests
design/              # design documents and figures
```

## Tests

```
pytest
ruff check .
mypy orchestra
```

All three pass on every commit.

## Design documents

In `design/`:

1. `orchestra-design.md` — conceptual model.
2. `orchestra-result-schemas.md` — the result envelope.
3. `orchestra-grammar.md` — surface syntax.
4. `orchestra-runner.md` — runtime architecture.
5. `orchestra-implementation-plan.md` — what slice 1 covers.
6. `orchestra-mcloop-integration-plan.md` — the McLoop integration
   contract.
7. `orchestra-shared-role-bindings-proposal.md` — the two-tier config
   schema and the global-plus-project merge layer above it.

Where the code disagrees with a design document, the design document
is the source of truth and the code is wrong.

## License

MIT.

## Author

**Michael H. Coen**  
mhcoen@gmail.com | mhcoen@alum.mit.edu  
[@mhcoen](https://github.com/mhcoen)
