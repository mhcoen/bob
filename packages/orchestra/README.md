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

Orchestra is a library, not a runner. It is invoked at decision
points by other tools (mcloop, Duplo) where a single-agent loop is
too brittle.

## Status

Slice 1 plus the mcloop integration surface. The runner spine is in
place: loader, validator, profile registry, artifact store, executor,
logger, resume. Real adapters for Claude Code (text-role and
edit-agent variants) are wired and tested. The library API
(`orchestra.run_workflow`) is available with config-driven role
binding, per-role dispatch, and a `WorkflowRunResult` shaped for
direct integration into existing tools.

Three workflow patterns ship as packaged `.orc` files:

- `single`: one edit-agent performs the edit.
- `draft_then_adjudicate`: text-role drafts, text-role adjudicates,
  one edit-agent performs the edit.
- `propose_critique_synthesize`: text-role proposes, text-role
  critiques, text-role synthesizes, one edit-agent performs the
  edit.

For the initial code-edit integration, exactly one invocation per
orchestra call mutates the workspace; earlier roles are advisory.

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

The project's `.orchestra/config.json` controls which pattern
`code_edit` resolves to and which models, adapters, and parameters
each role gets. Without a config file, `code_edit` defaults to
`single` with the current Claude Code agent backing.

## CLI use

The CLI is independent of the library API and remains the canonical
command-line surface for direct workflow execution:

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
  prompts.py      # prompt builders (verbatim lifts from mcloop)
  api.py          # run_workflow entry point + WorkflowRunResult
  config.py       # .orchestra/config.json schema and loader
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
6. `orchestra-mcloop-integration-plan.md`, the mcloop integration
   contract this implementation follows.

Where the code disagrees with a design document, the design document
is the source of truth and the code is wrong.
