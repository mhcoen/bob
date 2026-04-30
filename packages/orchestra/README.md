# Orchestra

Orchestra organizes, coordinates, and directs interacting LLMs. It
is a meta-language and runtime for declaring multi-model workflows,
where models propose, critique, arbitrate, draft, and adjudicate
under deterministic control.

A deterministic shell around stochastic actors. The state machine,
artifact store, log, and replay are deterministic and auditable.
The nondeterminism lives at the adapter boundary, where it belongs.

Workflows are declarative `.orc` files that name states, roles, and
transitions. Models, prompts, and parameters are configuration, not
code. Swapping a single-model edit for a draft-then-adjudicate
council is a config change, not a rewrite.

Orchestra is invoked at decision points by other tools (McLoop,
Duplo) where a single-agent loop is too brittle, and is also usable
directly via a verb-style CLI for ad-hoc multi-model questions.

## Status

The runner spine is in place: loader, validator, profile registry,
artifact store, executor, logger, resume. Real adapters for Claude
Code (text-role and edit-agent variants) are wired and tested. The
library API (`orchestra.run_workflow`) is shipped along with a
verb-style CLI (`orchestra ask`, `orchestra council`, `orchestra
pair`) and a direct workflow execution CLI (`orchestra run` /
`orchestra resume`).

McLoop's code-edit decision point integrates through `run_workflow`,
verified by a parity smoke test against the legacy direct path.

Configuration is layered: a global `~/.orchestra/config.json`
defines the default roles, verbs, and workflows, and an optional
project-local `<project>/.orchestra/config.json` overrides specific
entries per project.

Six workflow patterns ship as packaged `.orc` files. Three are for
code edits, where the final state mutates the workspace:

- `single`: one edit-agent performs the edit.
- `draft_then_adjudicate`: text-role drafts, text-role adjudicates,
  one edit-agent performs the edit.
- `propose_critique_synthesize`: text-role proposes, text-role
  critiques, text-role synthesizes, one edit-agent performs the
  edit.

Three are ask-flavored variants used by the verb CLI. They are
read-only; no workspace is touched:

- `ask_single`: one model produces the answer.
- `ask_draft_then_adjudicate`: drafter, adjudicator, editor (all
  text-role).
- `ask_propose_critique_synthesize`: proposer, critic, synthesizer,
  editor (all text-role).

For the code-edit workflows, exactly one invocation per orchestra
call mutates the workspace; earlier roles are advisory.

## Library use

Install:

```
pip install -e '.[dev]'
```

Call from Python:

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

## CLI use

Two surfaces: the verb-style surface (short, conversational) and the
direct execution surface (`run` / `resume`).

### Verb-style

Type a verb and a question. Orchestra reads `~/.orchestra/config.json`,
maps the verb to a workflow, and runs it with the rest of the line as
the query.

```
orchestra ask what is the capital of france
orchestra council should I rewrite this in rust
orchestra pair explain the difference between liskov and dependency inversion
```

The model's text response prints to stdout. Nothing else: no run
ids, no run-dir paths, no terminal-state debug. The full log still
lands at `~/.orchestra/runs/<run_id>/log.jsonl` for forensics.

### Configuring verbs

Drop a config at `~/.orchestra/config.json` with a `verbs` table that
maps verb names to workflow names, plus the `roles` and `workflows`
the schema documents:

```json
{
  "verbs": {
    "ask":     { "workflow": "ask_single" },
    "council": { "workflow": "ask_propose_critique_synthesize" },
    "pair":    { "workflow": "ask_draft_then_adjudicate" }
  },
  "roles": {
    "editor":      { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "drafter":     { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "adjudicator": { "adapter": "claude_code_text", "model": "opus", "parameters": {} },
    "proposer":    { "adapter": "claude_code_text", "model": "kimi-k2.6", "parameters": {} },
    "critic":      { "adapter": "claude_code_text", "model": "sonnet", "parameters": {} },
    "synthesizer": { "adapter": "claude_code_text", "model": "opus", "parameters": {} }
  },
  "workflows": {
    "ask_single":                       { "pattern": "ask_single" },
    "ask_propose_critique_synthesize":  { "pattern": "ask_propose_critique_synthesize" },
    "ask_draft_then_adjudicate":        { "pattern": "ask_draft_then_adjudicate" }
  }
}
```

Verb names are user-defined. Rename, add, or remove freely. Each
verb just names which workflow runs.

### Global plus project configs

Orchestra reads up to two config files and merges them into a single
view:

1. `~/.orchestra/config.json` (global). Defines roles, verbs, and
   workflows shared across projects.
2. `<project>/.orchestra/config.json` (project, optional). Replaces
   specific entries from the global on a per-key basis.

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
swaps the editor's binding for itself. McLoop's
`invoke_code_edit(project_dir=...)` and the verb CLI both consume
the merged view, so the override applies consistently across
consumers.

### Help

```
orchestra help
```

Lists every configured verb plus the workflow it runs. `orchestra
help <verb>` shows the required roles and the binding configured for
each, flagging any role with no binding as `NOT CONFIGURED`.

### Direct execution

Bypass verbs and run a workflow file directly.

```
orchestra run tests/fixtures/slice1/echo.orc --input topic="hello world"
orchestra resume <run_id>
```

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
