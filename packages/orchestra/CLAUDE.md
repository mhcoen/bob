# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Orchestra has moved past design. The design phase is complete; six design documents are on disk under `design/`. The runner spine for slice 1 is implemented under `orchestra/` (at the repo root) with unit tests and end-to-end tests under `tests/`. Slice 2 (versioned-workspace profile + real shell adapter) is the next planned increment but is not started.

The implementation is Python 3.12. The build is editable: `pip install -e '.[dev]'` from the repo root. Tests run with `pytest` from the repo root.

## Repository layout

```
orchestra/                             # repo root (also the Python package name)
  pyproject.toml                       # editable install, pytest/ruff/mypy config
  README.md
  CLAUDE.md
  design/                              # design documents (frozen for slice 1)
    orchestra-design.md                # conceptual model + factoring
    orchestra-result-schemas.md        # result envelope, payload shapes, counters
    orchestra-grammar.md               # EBNF + reserved words + reference resolution
    orchestra-runner.md                # runner architecture
    orchestra-implementation-plan.md   # slice 1 plan (the document this code implements)
    orchestra-acid-tests.md            # cover for the three acid-test workflows
    orchestra-acid-test-1-design-loop.md
    orchestra-acid-test-2-council.md
    orchestra-acid-test-3-mcloop.md
  orchestra/                           # implementation (Python package)
    spine.py                           # IR dataclasses + result envelope types
    errors.py                          # exception hierarchy
    adapters/                          # adapter contract + slice-1 mocks
    executor/                          # state machine, parsers, guards
    loader/                            # lexer, parser, validator
    log/                               # JSONL writer + truncation-tolerant reader
    registry/                          # profile registry + core registrations
    resume/                            # log replay + resume hook dispatch
    store/                             # SQLite-backed artifact store
    cli.py                             # `orchestra run` and `orchestra resume`
  tests/
    fixtures/slice1/echo.orc           # the slice-1 workflow under test
    test_store.py                      # unit tests
    test_log.py
    test_registry.py
    test_loader.py
    test_adapters.py
    test_resume.py
    test_e2e.py                        # tests A, B, C from the impl plan
    test_e2e_determinism.py            # byte-identical-log check
```

## Documents and reading order

When picking up the project, read in this order:

1. `design/orchestra-design.md` — conceptual model. The four-way factoring (model / role / prompt source / state), agents, artifacts, profiles, validation rules, deferrals. Treat its commitments as load-bearing.
2. `design/orchestra-result-schemas.md` — result envelope shape, payload shapes per backing, counter semantics.
3. `design/orchestra-grammar.md` — EBNF + reserved words.
4. `design/orchestra-runner.md` — runner architecture (loader, validator, executor, adapters, parsers, store, log, resume).
5. `design/orchestra-implementation-plan.md` — slice 1 plan. The implementation in `orchestra/` is built against this.
6. The acid-test sketches if you need workflow-level grounding for slices 2-6.

## Review discipline

For code review, always inspect the current working-tree files from disk immediately before making claims. If the user says files were edited, discard prior analysis and re-open the files. Every finding about existing code must be backed by current file and line evidence from the working tree. Do not rely on fetched refs, prior commits, cached snapshots, summaries, or previous review text.

## Slice 1 status

Implemented. The slice exercises the spine end-to-end with mocks: loader -> validator -> executor -> adapter -> result parser -> artifact store -> log -> resume.

Known limitations and notes worth carrying forward:

- **Prompt paths are quoted strings in the parser, not bare paths.** The grammar doc's worked example uses bare paths (`prompt file prompts/designer.md`), but the slice-1 lexer treats `/` as an unknown character. The fixture `tests/fixtures/slice1/echo.orc` uses quoted paths to compensate. Slice 2+ should reconcile this — either by extending the lexer to recognize a path-shaped token after `prompt file` / `prompt template`, or by amending the grammar doc's worked example to use quoted strings.
- **Step budget resets on resume.** The executor's `_step_count` is per-instance, not persisted. A resumed run starts fresh at zero. This is acceptable for slice 1 (no test exercises a long-running budget across resume) but should be fixed in slice 2 by replaying the count from the log.
- **Crash between `state_exit` and `transition` is treated as case 2 by replay.** The state would be re-entered on resume, duplicating work. The window is small (single fsync cycle) but the case is wrong. A future slice should add a `state_committed` log record or detect this case explicitly.
- **`_dispatch_parsers` doesn't return tentative handles to the caller if the parser raises after a partial write.** The slice's identity parser writes nothing before raising, so this is moot for slice 1; a future slice that adds parsers which write multiple artifacts before potentially raising must structure handle accumulation differently.
- **Resume hooks dispatch is exercised with an empty hook set.** No `resume_hook` records are emitted in slice 1. The dispatch path is wired and tested.

## Running the tests

```
pip install -e '.[dev]'
pytest                       # all tests
pytest tests/test_e2e.py     # end-to-end A/B/C only
pytest tests/test_e2e_determinism.py  # determinism check
```

The CLI entry point:

```
orchestra run tests/fixtures/slice1/echo.orc --input topic="hello world"
orchestra resume <run_id>
```

Run state lives in `~/.orchestra/runs/<run_id>/` by default; override with `--data-root <path>` on the top-level command.

## Slice ordering

Per the implementation plan's "Slice 2 preview" section:

- **Slice 2:** versioned-workspace profile (git-workspace artifact, `mode` keyword, checkpoint mechanism, resume hook), real shell adapter, a trimmed Test 3 fixture exercising shell + workspace + interrupted-shell-state resume.
- **Slice 3:** code profile (`require_diff`, `runs`, `continue_on_fail`, the check-errors parser).
- **Slice 4:** real model adapters (Claude API first, then `claude -p` and `codex exec` subprocess adapters).
- **Slice 5:** persistent agents and the agent-history parser.
- **Slice 6:** multi-actor states and join semantics, exercised by Test 2 (council).

Beyond slice 6: real human adapters (Telegram), verdict schemas, retry policy. Order is governed by the next acid-test workflow's needs, not by feature completeness.

## Design commitments that are easy to drift from

When discussing or writing about the design, these positions are deliberate — do not reframe them as open questions:

- **Roles and models are orthogonal.** The same model can play different roles; the same role can be played by different models. Do not collapse to "agent = model + prompt".
- **Prompt source vs prompt artifact** are distinct. A source is a recipe; an artifact is the resolved text logged at invocation time.
- **Per-agent (not per-role) history scoping** in v0. An agent invoked first as designer and then as arbiter sees the prior designer turns. Per-role isolation within one agent is deferred.
- **The core grammar is closed.** Profiles register artifact types, actor backings, postconditions, guards, parsers, validation rules, defaults — but cannot add top-level keywords, state types, or transition syntax.
- **`max_total_steps` is mandatory** at the workflow level; omitting it is a load error. Per-state cycle guards (`attempts.<state>`) are a separate, lint-recommended mechanism.
- **Parallel writes to the same artifact are a v0 load error**, not a merge problem to define semantics for.
- **Adapters never write to the artifact store directly.** Adapters return payloads. Profile parsers stage tentative writes. The executor commits them. This is the chokepoint through which postcondition checks, parser-failure rollback, log emission, and resume reconstruction all flow.
- **The artifact store has no public unconditional `write` method.** Mutation is `tentative_write` followed by `commit_tentative` or `discard_tentative`.
- The v0 non-goals list in `orchestra-design.md` (dynamic spawning, expression language, recursion, distributed execution, browser automation, grand unified ontology) is a real boundary, not a wishlist.

## Discipline that governs design changes

If a design change is needed during implementation:

- The implementation plan is frozen for slice 1. If a slice-1 task surfaces a real design problem, surface it as a finding rather than silently amending the design doc.
- Findings get a brief explanation in the relevant design doc's "open questions" section, not a wholesale revision of the doc.
- The grammar doc, runner doc, and result-schemas doc are referenced by the code's comments and docstrings. If you change one, search for references and reconcile.

## Author conventions

- Commit messages: never mention Claude, Claude Code, or Anthropic.
- Prose style across the design docs is plain, declarative, no marketing voice. Match it.
- No en-dashes, em-dashes, or semicolons in design docs or in code comments. (The author dislikes these. Code itself, including string literals and identifiers, is unaffected.)
- Code style: ruff and mypy strict. The slice's pyproject.toml configures both.
