# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Orchestra is shipped and in production use as the workflow runtime behind mcloop's multi-model coding patterns and duplo's council mode. The design phase (six documents under `design/`) is complete and the runner is implemented well beyond the original slice 1: real subprocess adapters (Claude Code and Codex, text and agent variants, plus direct-endpoint Kimi/DeepSeek text bindings), a public `run_workflow` API (`api/`), packaged `.orc` workflows (`workflows/`, 15 files including code_edit patterns, the ask/refine/pair conversational verbs, council_four, parallel_thinking, iterate_until_acceptable), a REPL, config-driven verbs and role bindings (`config.py`), progress reporting, and a calibration subpackage.

The implementation is Python 3.12. The build is editable: `pip install -e '.[dev]'` from the repo root (or use the bob workspace venv). Tests run with `pytest` from the package root (~756 tests).

## Repository layout

```
orchestra/                             # repo root (also the Python package name)
  pyproject.toml                       # editable install, pytest/ruff/mypy config
  README.md
  CLAUDE.md
  design/                              # design documents (~30: core six + slice plans + audits)
  orchestra/                           # implementation (Python package)
    spine.py                           # IR dataclasses + result envelope types
    errors.py                          # exception hierarchy
    adapters/                          # subprocess session layer (_subprocess.py) +
                                       #   claude_code_text/agent, codex_text/agent, mocks
    api/                               # run_workflow entry point, dispatch, registry,
                                       #   role bindings, validators, transcript
    workflows/                         # packaged .orc files + prompt templates
    executor/                          # state machine, parsers, guards
    loader/                            # lexer, parser, validator
    log/                               # JSONL writer + truncation-tolerant reader
    registry/                          # profile registry + core registrations
    resume/                            # log replay + resume hook dispatch
    store/                             # SQLite-backed artifact store
    calibration/                       # decision-consistency calibration harness
    config.py                          # ~/.orchestra/config.json + project overrides
    cli.py                             # `orchestra run/resume/help`, verbs, REPL entry
    repl.py                            # interactive REPL
    progress.py, transforms.py, prompts.py, schema.py, payloads.py,
    prompt_snapshot.py, visibility.py  # supporting modules
  tests/                               # ~40 test modules + fixtures/ + helpers/
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

## Implementation notes worth carrying forward

- Prompt paths in `.orc` files are quoted strings, not bare paths (the lexer treats `/` as an unknown character outside strings).
- The subprocess session layer (`adapters/_subprocess.py`) owns wall-clock and idle enforcement: kill sentinels are `TIMEOUT_KILL_EXIT = -102` and `IDLE_KILL_EXIT = -103` (outside the POSIX signal range; mirrored byte-for-byte by mcloop's runner), Telegram pending-approval files freeze the idle clock only while the child is alive, a stale `denied` file is cleared at session start, and the liveness bailout group-kills before closing the pipe so a grandchild holding stdout cannot outrun the timeout.
- Adapters declare `manages_own_timeout = True`; the executor never wraps them in a second timer.
- Adapters never write to the artifact store directly; parsers stage tentative writes and the executor commits them.

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

## Direction

Of the original implementation plan: slice 1 (spine), slice 4 (real model adapters), and slice 6 (multi-actor states and joins, exercised by parallel_thinking) are shipped; slice 3's code-profile grammar (`require_diff`, `runs`, `continue_on_fail`, `mode`) parses and validates but is served only by the mock shell adapter; slice 2's real shell adapter and versioned-workspace profile and slice 5's persistent agents were bypassed in favor of the api/ + subprocess-adapter path that production consumers actually needed (`context_policy` parses but nothing consumes it). Remaining direction: real shell/workspace if a consumer demands it, persistent agents, real human adapters beyond the Telegram approval hook, richer verdict schemas, and retry policy. Order is governed by what the next consumer workflow needs (mcloop patterns, duplo council), not by feature completeness.

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
