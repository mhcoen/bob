# Orchestra

Orchestra is two things in one package:

- **A command-line tool** for asking multiple LLMs to deliberate on a
  question. You type a verb and a question; orchestra runs a workflow
  of one or more models and prints the answer.
- **A Python library** for embedding multi-model workflows inside other
  tools. Other applications (like McLoop, an autonomous coding loop)
  call into orchestra at decision points where one model isn't enough.

Both surfaces share the same workflow definitions, the same model
adapters, and the same configuration file. Configure once, use
everywhere.

## What it looks like

A simple question, answered by one model:

```
$ orchestra ask what is the capital of france
Paris.
```

The same question routed through a council of three models that
propose, critique, and synthesize:

```
$ orchestra council should I rewrite this service in rust or stay in go
[council answer text, written after a proposer model drafts a take,
a critic model challenges it, and a synthesizer model produces the
final response]
```

A pair pattern where one model drafts and a second adjudicates before
the final answer:

```
$ orchestra pair explain liskov substitution to someone who knows oop basics
[final answer, after a drafter and an adjudicator]
```

For multi-turn back-and-forth, run bare `orchestra` with no arguments
to drop into an interactive REPL:

```
$ orchestra
orchestra REPL. /help for commands, /exit to quit.
orchestra> what is the capital of france
Paris.
orchestra> what about its population
About 2.1 million in the city proper.
orchestra> /use council
workflow -> council
orchestra (council)> should I rewrite this service in rust or stay in go
[council answer, with prior turns threaded into the prompt as context]
orchestra (council)> /save session.md
saved 3 turn(s) to session.md
orchestra (council)> /exit
```

No quotes around the question. The CLI takes the verb plus the rest
of the line. The model's text response is the only thing printed; the
full multi-model log lands at `~/.orchestra/runs/<run_id>/log.jsonl`
if you ever need to see the intermediate steps.

## Why it exists

Single-model loops are brittle. Models confidently produce wrong
answers, miss edge cases, fixate on bad approaches. The fix is having
a second (or third) model in the loop: drafting, critiquing,
arbitrating. But hand-built multi-model glue gets messy fast — every
project ends up with its own ad-hoc shell scripts.

Orchestra makes the multi-model pattern itself a first-class
declarative thing. A workflow is a `.orc` file with named states,
roles, and transitions. Models, prompts, and parameters are
configuration. Swapping a single-model edit for a draft-then-adjudicate
council is a config change, not a rewrite.

A deterministic shell around stochastic actors. The state machine,
artifact store, log, and replay are deterministic and auditable. The
nondeterminism lives at the adapter boundary, where it belongs.

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

The role bindings for `ask_council` (`framer`, the five lens roles,
`reviewer`, `chairman`) are eight separate bindings; the validator
rejects an `ask_council` invocation that does not bind all eight. The
other conversational verbs need fewer bindings: `ask` only `responder`;
`refine` only `proposer`, `critic`, and `synthesizer`; `pair` only
`drafter` and `adjudicator`. Drop the bindings you do not use.

Try it:

```
orchestra help
orchestra ask what is the capital of france
orchestra council should I rewrite this in rust
```

That's the whole CLI surface for ad-hoc questions.

## Choosing model bindings

The role-to-model bindings in the quick-start config above are not
arbitrary, but they are not benchmarks either. They reflect a single
bug-finding example on the McLoop codebase, where four models were
each asked to find bugs in the same code independently. The matrix:

![Matrix from a single bug-finding example. Kimi K2.6: 10 bugs found, 6 unique, 0 false positives. Claude Opus: 4 found, 3 unique, 0 false positives. Codex GPT: 2 found, 1 unique, many false positives requiring manual filtering. DeepSeek V4 Pro: 4 found, 0 unique, 0 false positives.](https://raw.githubusercontent.com/mhcoen/orchestra/main/design/images/model-bug-finding-matrix.png)

The headline observations from this one example: Kimi K2.6 produced
the most independent coverage (six unique finds, zero false
positives), Opus contributed complementary unique finds, DeepSeek
confirmed findings other models had already surfaced (zero unique),
and Codex required manual filtering to be usable. The per-bug
detail:

![Per-bug coverage detail from the same example, showing which of Kimi K2.6, DeepSeek, Claude, and Codex flagged each bug and at what severity.](https://raw.githubusercontent.com/mhcoen/orchestra/main/design/images/model-bug-finding-detail.png)

This is one example on one codebase. The rankings may differ on
architecture critique, code generation, or non-coding consultation;
model versions update; new candidate models will arrive. Re-evaluate
before making large binding changes.

The practical implications for the bindings above: Kimi K2.6 is a
reasonable default for divergent-thinking roles (`drafter`,
`proposer`, `contrarian`, `outsider`), Opus for synthesis-shaped
roles (`adjudicator`, `synthesizer`, `first_principles`,
`executor_lens`, `chairman`), and Sonnet for cheap-but-capable
roles (`framer`, `critic`, `expansionist`, `reviewer`). DeepSeek
fits as a confirming third opinion when you want a sanity check on
an already-flagged finding. Codex stays out of automated paths
because its triage cost compounds across many invocations.

For consumers like McLoop that integrate orchestra at the
edit-attempt level, the `draft_then_adjudicate` pattern with Kimi
as drafter and Opus as adjudicator is the natural starting point
from this example. See [the McLoop README](https://github.com/mhcoen/mcloop)
for the integration mechanics.

## Workflow patterns

Eight packaged patterns ship out of the box. Four are conversational
(used by the verb CLI):

- `ask_single`: one model produces the answer. Useful for quick
  factual queries where deliberation isn't worth the latency.
- `ask_draft_then_adjudicate`: one model drafts an answer, a second
  rewrites or refines it, a third produces the final response. The
  middle role catches obvious mistakes before they ship.
- `ask_propose_critique_synthesize`: a proposer drafts a take, a
  critic argues against it, a synthesizer reconciles. Useful for
  contested or open-ended questions.
- `ask_council`: a framer reformulates the question; five lens
  advisors (contrarian, first principles, expansionist, outsider,
  executor) answer in parallel; their outputs are anonymized to
  letters A through E; five reviewer invocations critique the
  anonymized panel; a chairman synthesizes a structured verdict.
  Twelve LLM calls per invocation; useful for high-leverage
  decisions where a single perspective is not enough.

Three are code-edit (used by McLoop and other coding tools):

- `single`: one edit-agent performs the edit.
- `draft_then_adjudicate`: text-role drafts, text-role adjudicates,
  one edit-agent performs the edit.
- `propose_critique_synthesize`: text-role proposes, text-role
  critiques, text-role synthesizes, one edit-agent performs the
  edit.

For the code-edit workflows, exactly one invocation per orchestra
call mutates the workspace; earlier roles are advisory. The ask
variants are read-only.

You can write your own `.orc` files too. The grammar is documented
in `design/orchestra-grammar.md`.

## Configuration

Orchestra reads up to two config files and merges them:

1. `~/.orchestra/config.json` (global). Defines roles, verbs, and
   workflows shared across all projects.
2. `<project>/.orchestra/config.json` (project, optional). Overrides
   specific entries.

The merge rule is replace, not nest: a role or verb or workflow
defined in the project config replaces the global entry of the same
name in full. Entries the project does not redefine are inherited
from the global.

A project that wants to override only the editor model:

```json
{
  "roles": {
    "editor": {
      "adapter": "claude_code_text",
      "model": "deepseek-v4-pro",
      "parameters": {}
    }
  }
}
```

That project keeps every other role from the global config and just
swaps the editor's binding for itself. The CLI and any library
consumer (McLoop, Duplo, your own tool) both pick up the merged
view, so the override applies consistently across consumers.

### Verbs

Verb names in `~/.orchestra/config.json` are user-defined. Rename,
add, or remove freely. Each verb just names which workflow runs.

```json
"verbs": {
  "fast":    { "workflow": "ask_single" },
  "careful": { "workflow": "ask_propose_critique_synthesize" }
}
```

Then `orchestra fast ...` and `orchestra careful ...` work.

### Roles

A role binding has three required keys (plus an optional fourth):

- `adapter`: which adapter implementation to use. Currently shipped:
  `claude_code_text` (read-only Claude Code, used for text-role
  states) and `claude_code_agent` (full edit-capable Claude Code,
  used for edit-agent states).
- `model`: model name passed to the adapter (e.g. `opus`, `sonnet`,
  `kimi-k2.6`, `deepseek-v4-pro`).
- `parameters`: adapter-specific extras, usually `{}`.
- `tools` (edit-agent roles only): tool restriction for the agent,
  e.g. `"default"` for the standard McLoop tool set.

The packaged ask workflows use the role name `responder` for the
final-answer state, since those workflows are read-only and need a
text adapter. The packaged code-edit workflows use the role name
`editor` for the final-edit state, since those workflows do mutate
the workspace and need an edit-agent adapter. Different role names
keep the two adapter kinds from colliding under one binding. A
single `~/.orchestra/config.json` can define both:

```json
"roles": {
  "responder": { "adapter": "claude_code_text",  "model": "opus", "parameters": {} },
  "editor":    { "adapter": "claude_code_agent", "model": "opus", "tools": "default", "parameters": {} }
}
```

A project-local config can still override either one for that project
without having to rename anything.

### Help

```
orchestra help
```

Lists every configured verb plus the workflow it runs.
`orchestra help <verb>` shows the required roles and the binding
configured for each, flagging any role with no binding as
`NOT CONFIGURED`.

## Interactive REPL

Bare `orchestra` (no arguments) drops you into a prompt that solves
shell quoting once and lets you ask follow-up questions that
reference prior turns:

```
$ orchestra
orchestra REPL. /help for commands, /exit to quit.
orchestra> what is the capital of france
Paris.
orchestra> what's the population of that city
About 2.1 million in the city proper.
```

The REPL threads the running transcript into each new prompt as a
``Prior conversation:`` block, so models see what was just discussed.
Switch workflows mid-session without losing context:

```
orchestra> /use council
workflow -> council
orchestra (council)> what would Brian Kernighan say about that
[council answer, with the prior two turns still visible to the models]
```

You can also dispatch a single turn to a different workflow without
switching the session by typing the workflow name as the first
word of the line. The next bare line still uses the session's
active workflow:

```
orchestra> council should I rewrite this service in rust
[council answer]
orchestra> what about latency
[ask answer; the session workflow is still ask, council was per-turn]
```

Slash commands (in-process, do not call any model):

- `/help` - list slash commands and configured workflows.
- `/use [name]` - show or switch the active workflow.
- `/clear` - drop the in-memory transcript.
- `/history` - print the transcript so far.
- `/save <path>` - write the transcript to disk. `.json` writes JSON;
  anything else writes markdown.
- `/exit`, `/quit` - leave the REPL. Ctrl-D also exits.

A single Ctrl-C cancels the current input line. A second Ctrl-C
within one second exits the REPL. The session transcript is in
memory only; it is not persisted across sessions unless you
`/save` it. The prompt_toolkit history file at
`~/.orchestra/history` records past commands so up-arrow recall
works across sessions, but it is not the conversational
transcript.

## Library use

Embed orchestra in another tool by importing `run_workflow`:

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
project configs. `load_config()` with no argument returns the global
config alone. The library API is opaque to where the config came
from; it just runs against whatever config object is passed in.

When neither a global nor a project config exists, `code_edit`
defaults to the `single` pattern with a default editor binding so
that consumers like McLoop work out of the box without any
configuration.

McLoop integrates through this surface. See
`design/orchestra-mcloop-integration-plan.md` for the contract.

## Direct workflow execution

Bypass verbs and run a workflow file directly:

```
orchestra run path/to/workflow.orc --input key=value
orchestra resume <run_id>
```

Useful for running custom `.orc` files that aren't named in a verb
binding, or for debugging workflows.

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
design/              # design documents
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

1. `orchestra-design.md`, conceptual model.
2. `orchestra-result-schemas.md`, the result envelope.
3. `orchestra-grammar.md`, surface syntax.
4. `orchestra-runner.md`, runtime architecture.
5. `orchestra-implementation-plan.md`, what slice 1 covers.
6. `orchestra-mcloop-integration-plan.md`, the McLoop integration
   contract.
7. `orchestra-shared-role-bindings-proposal.md`, the two-tier config
   schema and the global-plus-project merge layer above it.

Where the code disagrees with a design document, the design document
is the source of truth and the code is wrong.
