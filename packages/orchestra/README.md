# Orchestra

A deterministic shell around stochastic actors.

Orchestra coordinates multi-LLM workflows by wrapping nondeterministic
model calls in a fully deterministic harness. The state machine,
artifact store, log, and replay are deterministic and auditable. The
nondeterminism lives at the adapter boundary, where it belongs.

## Status

Slice 1. The runner spine is in place with mock adapters: loader,
validator, profile registry, artifact store, executor, logger, resume.
No real LLM, shell, git, or notification integrations yet.

See `design/orchestra-implementation-plan.md` for what slice 1 covers
and what it deliberately does not.

## Layout

```
orchestra/
  loader/        # parser + validator
  store/         # artifact store (SQLite-backed)
  registry/      # profile registry
  executor/      # state machine + parser dispatch
  adapters/      # adapter interface + mock adapters
  log/           # JSONL logger and reader
  resume/        # log replay + resume hook dispatch
  cli.py         # command-line entry point
tests/
  fixtures/slice1/   # echo.orc and prompt files
  test_*.py          # unit and end-to-end tests
design/              # design documents
```

## Usage

Install in development mode:

```
pip install -e '.[dev]'
```

Run a workflow:

```
orchestra run tests/fixtures/slice1/echo.orc --input topic="hello world"
```

Resume a crashed run:

```
orchestra resume <run_id>
```

Run tests:

```
pytest
```

## Reading order

The design documents in `design/`:

1. `orchestra-design.md`, conceptual model.
2. `orchestra-result-schemas.md`, the result envelope.
3. `orchestra-grammar.md`, surface syntax.
4. `orchestra-runner.md`, runtime architecture.
5. `orchestra-implementation-plan.md`, what slice 1 covers.

The runner code follows the architecture in document 4 and the slice-1
scope in document 5. Where the code disagrees with a design document,
the design document is the source of truth and the code is wrong.
