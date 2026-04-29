# Orchestra: Implementation Plan, Slice 1

## What this document is

This is the implementation plan for the first vertical slice of
the Orchestra runner. It is the next deliverable after the four
specification documents (`orchestra-design.md`,
`orchestra-result-schemas.md`, `orchestra-grammar.md`,
`orchestra-runner.md`). It does not add design. It identifies
the smallest piece of working code that exercises the runner
spine end to end, lists the deliverables, and sequences the
work.

The purpose of slice 1 is to validate that the architecture
specified in the four documents actually composes when assembled
into running code. The spine is:

```
loader -> validator -> executor -> adapter -> result parser
       -> artifact store -> log -> resume
```

If the spine is wrong, every adapter and profile built on top of
it inherits the bug. Slice 1 builds the spine with mock
components in every position where a mock is sufficient, so that
the only thing the slice can fail to do is exercise the spine.

The reader should already be familiar with the four
specification documents. This plan does not re-derive them.

## Goals

1. Produce a runner that loads a workflow file, validates it,
   executes it against mock adapters, writes a complete log, and
   resumes correctly from a synthetic interrupted state.
2. Cover the spine: every architectural component named above
   participates in the slice.
3. Avoid every integration that has its own design surface. No
   real LLM, no real shell, no git, no Telegram, no UI
   automation. Mocks for actors; the local filesystem for
   storage; deterministic fakes for anything that would otherwise
   be nondeterministic.
4. Produce something runnable from a command line that an
   implementer can iterate on.

## Non-goals

1. Versioned-workspace profile. Deferred to slice 2.
2. Real adapters (Claude, Codex, Telegram, relay). Deferred to
   slice 3 onward.
3. Multi-actor states. The acid tests show this is needed, but
   slice 1 does not exercise it; the join machinery has its own
   complications and gets its own slice.
4. Persistent agents and conversation history. Slice 1 uses
   stateless model invocations only. Agent payloads, compression
   events, and the agent-history parser are slice-2 work.
5. Verdict schemas. The slice's mock model adapter returns plain
   `complete` outcomes, not verdicts.
6. Performance. The slice is correctness-only.
7. Production-grade error reporting. Errors abort with a
   stack trace; pretty diagnostics are a later concern.

## Implementation language

Python 3.12. The choice is justified by:

- Fast iteration (no compile step).
- Good standard library (subprocess, sqlite3, hashlib,
  pathlib, json, dataclasses, typing).
- Existing mcloop is Python; staying in the same language
  reduces context switching during the migration that ends the
  Orchestra implementation phase.
- The runner's hot path is not Python-bound; the slow steps are
  LLM calls and shell commands, both of which spend negligible
  time in the runner itself.

A future v1 may rewrite the runner in Rust or Go for distribution
reasons, but the v0 contract is language-agnostic per the runner
spec's non-goal 1, so a Python v0 is fine.

## What slice 1 builds

### One workflow file

A trivial workflow that exercises every component on the spine
without exercising anything else. The workflow file lives at
`tests/fixtures/slice1/echo.orc`.

```
spec 0.1

workflow echo

  external_input topic text

  max_total_steps 10

  model mock-llm

  role responder
    prompt file prompts/responder.md

  artifact response text

  state respond
    actor model mock-llm
    role responder
    prompt template prompts/responder.md with topic
    reads topic
    writes response text
    on complete => confirm
    on error => stop
    on timeout => stop

  state confirm
    actor human
    prompt file prompts/confirm.md
    reads response
    options accept, reject
    on accept => done
    on reject => stop
    on timeout => stop
    on cancelled => stop
```

The accompanying prompt files
(`prompts/responder.md`, `prompts/confirm.md`) contain
placeholder text. The `mock-llm` model and the `human` actor are
served by mock adapters described below.

This workflow exercises:

- File parsing and indentation.
- One external input declaration and one template substitution.
- One model declaration, one role declaration, one artifact
  declaration.
- Two states with distinct actor backings (model, human).
- A `prompt template` and a `prompt file` (different states).
- Three transition forms (`=> done`, `=> stop`,
  `=> <state>`).
- The human-actor `options` declaration.
- One artifact write through the parser-and-commit path.
- One artifact read across states (`confirm` reads `response`).

It does not exercise: groups, joins, profiles, schemas,
backing-scoped clauses, agent history, retry policy, cycles, or
multi-version artifacts. Those are slice-2 and later.

### Five Python packages

The runner is structured as five packages corresponding to the
spine components. Names are tentative.

```
orchestra/
  loader/        # parse + validate
  store/         # artifact store
  registry/      # profile registry
  executor/      # state machine, parser dispatch, postcondition checks
  adapters/      # adapter interface + mock adapters
  log/           # JSONL logger and reader
  resume/        # log replay + resume hook dispatch
```

Single-file modules where the package would be one file are
fine; the package boundaries above are the architectural
boundaries, not necessarily the file boundaries.

### Loader and validator

A hand-written recursive-descent parser against the grammar in
`orchestra-grammar.md`. Slice 1 does not need the full grammar;
it needs the subset the echo workflow uses. The parser is
written to handle the full v0 grammar so that slice 2 does not
need a parser rewrite, but the slice's tests only cover the
subset.

Validation phases 1 through 7 from the runner spec, with the
following slice-1 reductions:

- Phase 2 (profile load): no profiles in slice 1, so this phase
  is a no-op. The mechanism is wired up; the registry is
  populated only with the core actor backings.
- Phase 7 (cycle bounds): the echo workflow has no cycles, so
  the lint check finds nothing. The mechanism is wired up.

Other phases run normally.

### Profile registry

Implemented as the data structure described in the runner spec.
Slice 1 has no profiles, but the core actor backings (model,
human, shell) are registered the same way profiles register
their backings, so that slice 2's profile additions are a matter
of data, not of machinery.

The registry holds: artifact types (the core types: text, json,
messages, prompt, schema, document, file, directory; not
git-workspace, which is profile-registered), actor backings
(model, human, shell; mock implementations in slice 1), result
parsers (one core parser; see below), validation rules (the
core rules from the design document), backing-scoped keywords
(empty in slice 1), postconditions (empty), guard predicates
(empty beyond the core comparisons), default policies, resume
hooks (empty).

### Artifact store

Implemented against SQLite. Schema:

```
CREATE TABLE artifacts (
  name        TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  qualifiers  JSON NOT NULL
);

CREATE TABLE versions (
  artifact     TEXT NOT NULL REFERENCES artifacts(name),
  version_id   TEXT NOT NULL,
  value        BLOB NOT NULL,         -- inline value for text/json/messages/prompt/schema
  written_at   TEXT NOT NULL,
  written_by   TEXT NOT NULL,         -- state_id + attempt
  is_tentative INTEGER NOT NULL,      -- 0 = committed, 1 = tentative
  tentative_handle TEXT,              -- handle for grouping tentatives
  PRIMARY KEY (artifact, version_id)
);

CREATE INDEX versions_by_artifact ON versions(artifact, written_at);
```

Operations from the runner spec implemented as SQL:
- `read_latest`: SELECT the most recent committed version.
- `read_version`: SELECT by artifact + version_id.
- `tentative_write`: INSERT with is_tentative=1, return generated handle.
- `commit_tentative`: UPDATE is_tentative=0 for the named handles
  in a transaction; return version IDs.
- `discard_tentative`: DELETE rows with the named handles.
- `list_versions`: SELECT all committed versions ordered by
  written_at.

Version IDs for inline types are content hashes (SHA-256 of the
canonicalized value). Slice 1 has no `file`, `directory`, or
`git-workspace` artifacts, so the only version-ID code path is
the inline one.

### Adapters

Three mock adapters in slice 1. All three implement the four-
operation contract from the runner spec.

**Mock model adapter** (`adapters/mock_model.py`). Returns a
deterministic response based on the prompt. The default
behavior is to echo the prompt with a fixed prefix:

```
mock_model.invoke(prepared) -> {
  output: f"[mock-llm response to: {prepared.prompt[:80]}]",
  verdict: null,
  fields: {},
  tokens_in: len(prepared.prompt),
  tokens_out: len(prepared.prompt) + 30,
  cost_usd: null,
  transcript_ref: null
}
```

The adapter accepts an optional configuration knob (an
environment variable or a config file) that lets a test override
the response. This is how the resume test injects a specific
response and how future tests will exercise verdict schemas
deterministically.

**Mock human adapter** (`adapters/mock_human.py`). Returns a
pre-scripted choice. Slice 1's tests run the human adapter in
"scripted" mode, where the test passes a list of choices to make
in order. The adapter consumes one choice per invocation and
returns it as the human payload. There is no real notification
backend in slice 1.

**Mock shell adapter** (`adapters/mock_shell.py`). The echo
workflow does not use shell, but the adapter is implemented to
prove the shell payload shape and the multi-command `runs` block
parsing. The mock executes its `runs` commands by string-matching
against a configured response table (so a test can map
`"echo hi"` to `(0, "hi\n", "")` and `"false"` to `(1, "", "")`).
Real subprocess execution is slice 3.

The mock adapters are deterministic by design. No clock, no
random numbers, no subprocess output. This makes the slice's
tests reproducible.

### Result parsers

Slice 1 needs one parser: the **identity model-output parser**.
When a model state declares `writes <name> text`, this parser
reads the model payload's `output` field and produces a
tentative write of that text to the named artifact.

The parser is registered against the model backing with an
artifact-type filter of `text`. The runner spec's worked example
(the code profile's check-errors parser) is more complex; the
identity parser is the simplest possible parser and is sufficient
for slice 1.

The agent messages-append parser, the schema-extracting parser,
and the shell check-errors parser are deferred.

### Logger

JSONL writer with the record types from the runner spec, scoped
to the events slice 1 actually emits:

- `run_start`, `state_enter`, `actor_prepare`,
  `actor_invoke_start`, `actor_invoke_end`, `parser_run`,
  `artifact_write`, `state_exit`, `transition`, `run_end`.

Other event types (`postcondition_check`, `compression_event`,
`step_budget_exhausted`, `cancelled`, `notification_sent`,
`choice_received`, `resume_hook`) are wired up but not exercised
in slice 1. The `resume_hook` event in particular is wired up
but not emitted because no hooks are registered; the
resume-hook dispatch path is exercised in test C with an empty
hook set, so the dispatch logic is covered without any
`resume_hook` records appearing in the log. The other listed
events are exercised in later slices.

The writer fsyncs after each record. The reader handles
truncated last lines per the runner spec's open question 6.

Payload files are written to `payloads/` per the runner spec.
The slice's `payload_ref` is the relative path
`payloads/<run_id>-<seq>.json`.

### Resume

Implemented per the runner spec. The runner accepts a `--resume`
flag pointing at a run directory; on startup it reads the log,
rebuilds the artifact store, the counter table, and the current
state, then either continues from the next transition (case 1)
or runs resume hooks and re-enters the last state (case 2).

Slice 1 has no profile-registered resume hooks (the
versioned-workspace hook is slice 2). The resume mechanism is
exercised by a synthetic test that truncates the log mid-state
and verifies the runner re-enters cleanly. The resume-hook
dispatch is invoked with an empty hook set; no `resume_hook`
records are emitted, but the dispatch path runs and the
ordering invariant ("hooks before state_enter on re-entry") is
satisfied vacuously.

## Tests

The slice ships with three end-to-end tests, in this order.

### Test A: happy path

Run the echo workflow start to finish. Assert:

1. The workflow loads without errors.
2. `respond` runs, the mock model returns the expected echo,
   the identity parser writes a `response` artifact.
3. `confirm` runs with the scripted human input `accept`.
4. The workflow ends in `done`.
5. The log file contains exactly the expected event sequence in
   the expected order.
6. The artifact store contains exactly one committed version of
   `response`.
7. The `payloads/` directory contains exactly two payload files
   (one per invocation).

### Test B: parser failure rollback

Modify the test setup so that the identity parser raises an
error for the `respond` invocation (this is done by injecting a
faulty parser, not by changing the mock model). Assert:

1. The state's envelope has `status = error`,
   `outcome = error`, `error.kind = parser_failure`.
2. No `artifact_write` log record is emitted for this
   invocation.
3. The artifact store has no committed `response` version.
4. The transition table routes on `error` and the workflow ends
   in `stop`.
5. The log records `parser_run` with the failure detail.

### Test C: resume from interrupted state

Run the echo workflow but kill the runner partway through. The
test does this by:

1. Configuring the mock human adapter to block on a sentinel
   value the test controls.
2. Running the workflow until it reaches the `confirm` state and
   blocks.
3. Truncating the log file at a point that simulates a crash
   between `actor_prepare` and `actor_invoke_start` (the human
   notification was prepared but not yet sent).
4. Restarting the runner with `--resume`.

Assert:

1. The runner reads the truncated log, rebuilds the artifact
   store with the committed `response` version, sets the current
   state to `confirm`, and identifies it as case 2.
2. No `resume_hook` records are emitted (no hooks are
   registered) but the resume-hook dispatch ran.
3. The new `state_enter` record for `confirm` has
   `attempt = 2`.
4. The runner re-prepares the human invocation, the scripted
   choice `accept` is consumed, and the workflow ends in `done`.
5. The final log contains both attempts of `confirm` plus the
   single `respond` attempt, with envelope `attempt` numbers
   matching the runner spec's "Counter semantics" rules.

## Sequencing the work

The work is broken into seven steps, each independently
testable. The ordering is meant to surface architectural
problems as early as possible.

**Step 1: artifact store.** Build the SQLite-backed store with
the operations from the runner spec, including
`tentative_write` / `commit_tentative` / `discard_tentative`.
Unit-test the store standalone: write, read, commit, discard,
read-after-commit, read-after-discard, atomicity of
`commit_tentative`.

**Step 2: logger and log reader.** Build the JSONL writer with
fsync semantics and the reader that handles truncated last
lines. Unit-test the writer-reader round trip and the
truncation-recovery case.

**Step 3: profile registry and core registrations.** Build the
registry data structure and register the core actor backings,
core artifact types, core validation rules. Unit-test the
registry's name-collision detection.

**Step 4: loader and validator.** Build the parser for the
grammar subset the echo workflow uses, plus the validation
phases. Unit-test loading the echo workflow and a handful of
intentionally malformed variants. Confirm that phase 6
(dataflow) catches a deliberately mis-typed `reads`.

**Step 5: mock adapters.** Build the three mock adapters. Unit-
test each in isolation against a synthetic invocation request.

**Step 6: executor and result parser dispatch.** Build the
state-machine loop with the eleven-step per-state sequence from
the runner spec. Wire in the identity model-output parser. Run
test A end to end.

**Step 7: resume.** Build the log replay logic and the
resume-hook dispatch. Run test C end to end. Run test B last as
a regression check that error paths still work after the resume
machinery is in place.

Each step ends with the corresponding tests passing. Steps 1
through 5 are independent; steps 6 and 7 depend on all earlier
steps.

## Definition of done

Slice 1 is complete when:

1. The seven steps are implemented and their unit tests pass.
2. Tests A, B, and C pass.
3. The log produced by test A is human-readable JSONL that can
   be inspected with `jq`.
4. A second test run produces a byte-identical log to the first
   (modulo timestamps and run IDs), confirming determinism of
   the slice.
5. The slice's code passes the project's lint and type-check
   sweep (the same one the code profile's `check` state will
   eventually run on real workflows; for slice 1 this is run by
   hand).

The "byte-identical log" criterion is the strongest signal that
the spine is correct. Anything that would corrupt determinism
(implicit ordering, hash-map iteration order leaking into
output, time-sensitive logic outside the timestamp fields) gets
caught here.

## What slice 1 explicitly does not validate

Listing this so the slice's success is not over-claimed:

- Multi-actor states. The join machinery is unexercised.
- Profiles. The registration mechanics work but no profile is
  loaded.
- Real LLMs. The mock model has no sense of what a real model
  call looks like; integration with a real adapter will surface
  issues the mock did not.
- Workspace artifacts and the resume hook. The resume hook
  dispatch is exercised with zero registered hooks; the
  versioned-workspace hook itself is slice-2 work.
- Persistent agents. Stateless model invocations only.
- Verdict schemas and schema-driven transitions.
- Retry policy on error and timeout.
- Cycle exit guards. The echo workflow has no cycles.
- Anything related to `git`, `claude`, `codex`, Telegram, or
  any external system.

These are explicitly the next slices' work, and slice 1's
purpose is to establish that the spine is correct *before* any
of them are touched.

## Slice 2 preview

After slice 1 lands, slice 2 adds:

- The versioned-workspace profile, including the `git-workspace`
  artifact type, the `mode` keyword, the checkpoint mechanism,
  and the resume hook.
- The shell adapter (real subprocess execution).
- A second test workflow that uses both. The natural choice is a
  trimmed version of Test 3 (mcloop) that exercises shell, the
  workspace, and the resume hook on an interrupted shell state.

Slice 3 adds the code profile (`require_diff`, `runs`,
`continue_on_fail`, the check-errors parser).

Slice 4 adds real model adapters (Claude API first, then
subprocess adapters for `claude -p` and `codex exec`).

Slice 5 adds persistent agents and the agent-history parser.

Slice 6 adds multi-actor states and join semantics, exercised
by Test 2 (council).

Slices beyond 6 add real human adapters (Telegram), verdict
schemas, retry policy, and so on. The order is governed by what
the next concrete acid-test workflow needs, not by feature
completeness.

## Open questions for slice 1 itself

1. **Project layout.** Whether the runner lives in
   `/Users/mhcoen/proj/orchestra/runner/` as a sibling to
   `design/`, or in a different repository, is open. v0
   recommendation: the same repository, under a `runner/`
   subdirectory, until the design and the implementation
   diverge enough to warrant separate repositories.

2. **Test framework.** pytest is the obvious choice given the
   Python decision. No reason to litigate.

3. **CLI shape.** The slice needs at least:
   - `orchestra run <workflow.orc> --input topic="..."`
   - `orchestra run <workflow.orc> --resume <run_id>`
   The detailed flags (logging verbosity, data-root override,
   adapter-config injection for tests) can be settled when the
   CLI module is written.

4. **Error reporting.** Slice 1 aborts with stack traces. A
   proper diagnostic format is its own work item; the runner
   spec's non-goal 3 covers this.

5. **Schema for mock-adapter configuration.** The mock model
   adapter accepts an override response; the mock human adapter
   accepts a script of choices; the mock shell adapter accepts
   a response table. The configuration format (env var, JSON
   file, pytest fixture) is a slice-1 implementation decision
   to make when step 5 is being written.
