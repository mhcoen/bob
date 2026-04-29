# Orchestra: Runner Architecture

## What this document is

This is the runner-architecture follow-on to `orchestra-design.md`,
`orchestra-result-schemas.md`, and `orchestra-grammar.md`. It
specifies what the runner is, what its components are, and the
contracts between them. It is the document an implementer reads
before writing code.

The runner is the program that loads a workflow file, validates
it, and executes it. It is the only thing in the Orchestra
ecosystem that has a process and a clock. The language documents
specify what a workflow says; this document specifies what
happens when a workflow runs.

The reader should already be familiar with the three preceding
documents. This document does not re-derive their conclusions; it
turns them into a runtime architecture.

## Goals

1. Specify the actor adapter interface every actor backing
   implements.
2. Specify the artifact store interface and the versioning
   semantics it provides.
3. Specify the profile registry and the validation pipeline that
   loads a workflow file.
4. Specify the execution loop: state entry, actor invocation,
   parser dispatch, transition selection, postcondition checks,
   counter updates.
5. Specify the log format in detail (record types, required and
   optional fields, write ordering).
6. Specify resume semantics: how the runner reads a partial log
   and either continues from the next transition or re-enters the
   last state.

## Non-goals

1. Choice of implementation language. Python, Rust, TypeScript,
   Go, or anything else can implement these contracts. The
   document specifies behavior, not bindings.
2. Storage backend. The artifact store and log storage are
   defined as interfaces; the choice between SQLite, the local
   filesystem, an embedded key-value store, or something else is
   a v0 implementation decision.
3. Specific adapter implementations. Adapters for Claude API,
   `claude -p`, `codex exec`, the relay tool, the Telegram
   notification backend, and so on each get their own short
   adapter document later. This document specifies what an
   adapter must do, not how any particular adapter does it.
4. Performance and concurrency tuning. The execution loop is
   described in single-runner terms. Distributed execution is a
   v1 non-goal per the design document.
5. The full set of profile-provided result parsers. The code
   profile's check-errors parser is the worked example because
   the acid tests require it; other profiles' parsers are
   specified alongside the profiles themselves.

## Architecture overview

The runner has six components, each addressing one of the goals
above:

1. **Loader and validator.** Reads a workflow file, parses it
   against the grammar, applies all validation rules from the
   design document and from registered profiles, and produces an
   in-memory workflow representation suitable for execution.
2. **Profile registry.** Holds the set of profiles known to the
   runner, the registrations each profile contributes (artifact
   types, actor backings, backing-scoped keywords, postconditions,
   guard predicates, result parsers, validation rules, default
   policies, resume hooks), and the conflict-detection logic.
3. **Artifact store.** Holds typed, versioned artifact values for
   the current run. Provides read access by name (latest version)
   or by name and version ID. Provides write access through new
   versions only; never mutates an existing version. All artifact
   mutation in the running workflow goes through this component;
   adapters do not write to the store directly.
4. **Adapter set.** One adapter per registered actor backing.
   Each adapter accepts an invocation request from the executor
   and returns a payload. Adapters are stateless from the
   executor's perspective; any continuity (agent conversation
   history, subprocess sessions) is reported back to the executor
   through the payload, not committed to the artifact store by
   the adapter.
5. **Executor.** The state-machine loop. Picks the next state to
   enter, builds the invocation context, calls the appropriate
   adapter, runs profile-registered postconditions and result
   parsers, finalizes the result envelope, evaluates the
   transition table, and routes to the next state. The executor
   is the only component that calls the artifact store's
   commit-tentative path.
6. **Logger.** Writes JSON Lines records of every state entry,
   actor start and end, parser run, postcondition outcome,
   transition choice, and counter update to a per-run log file.

The components communicate through narrow interfaces specified
below. The loader produces the workflow representation. The
executor consults the profile registry, calls adapters, reads
and writes the artifact store, runs profile-registered code, and
emits log records as it goes. Resume reads the log to reconstruct
state and resumes the executor.

The runner is a single process. There is one executor per
workflow run; multiple runs may execute in parallel processes,
but the design does not depend on this and v0 implementations
may be single-run.

## Actor adapter interface

An adapter is the runtime component that turns an actor
invocation request into a payload. The runner has one adapter
per registered actor backing (model, agent, shell, human, plus
profile-registered backings). Adapters are pluggable: the
profile registry tells the runner which adapter to instantiate
for a given backing.

### Adapter contract

Every adapter implements the same conceptual contract. The
contract has four operations:

```
adapter.prepare(invocation_request) -> prepared_invocation
adapter.invoke(prepared_invocation) -> payload
adapter.cancel(prepared_invocation) -> ()
adapter.describe() -> backing_metadata
```

`prepare` accepts an invocation request and returns a prepared
invocation. The prepared invocation is whatever internal state
the adapter needs to perform the actual call: a constructed
prompt, a normalized command line, a notification payload. The
runner does not inspect the prepared invocation; it passes it
back to `invoke`. Splitting `prepare` from `invoke` lets the
runner record the prepared content (resolved prompt, exact
command string) in the log before any side effects happen.

`invoke` performs the call and returns a payload. The payload
shape is the per-backing payload shape from the result-schemas
document. The adapter is responsible for producing every field
in the payload, including derived fields (verdict extraction
from a model's structured response, aggregate counts in a
shell's command list).

Adapters do not mutate the artifact store. Any artifact a state
declares as a write is populated by the executor through the
result-parser dispatch path (step 6 of the execution loop), not
by the adapter. An adapter that needs to propose new artifact
content (for instance, an agent adapter appending a turn to a
running messages artifact) reports the proposed content in its
payload; a registered result parser reads the payload and
produces a tentative write that the executor commits.

`cancel` is invoked when the runner needs to abort an
in-progress invocation (workflow cancellation, timeout). The
adapter is responsible for terminating any subprocess, closing
any open connection, and freeing any resources. After `cancel`
returns, the adapter must not produce any further side effects
for that invocation.

`describe` returns metadata about the adapter: the backing name,
supported features (whether `cancel` is meaningful, whether the
adapter produces token and cost counts, whether the adapter
supports streaming), and any backing-specific configuration the
runner should know about. The runner reads this at startup and
uses it to set up backing-appropriate defaults.

### Invocation request shape

The invocation request the executor passes to `prepare` contains
everything the adapter needs to do its job, drawn from the
state's declaration and the runtime context:

```
{
  state_id:           string,
  attempt:            integer,    # the value envelope.attempt will hold
  actor_binding:      object,     # see result-schemas "Actor binding record"
  reads:              dict,       # {artifact_name: artifact_value}
                                  # latest versions of declared reads
  external_inputs:    dict,       # {input_name: value}
                                  # workflow-level external inputs
  prompt_artifact:    string | null,  # resolved prompt artifact text;
                                      # null for non-LLM backings
  schema:             object | null,  # parsed JSON Schema; null when no
                                      # schema is bound
  backing_options:    dict,       # backing-scoped clauses from the state
                                  # body (e.g. {"runs": [...], "continue_on_fail": true})
  timeout_ms:         integer | null  # state's declared timeout, or null
                                      # if none declared
}
```

The adapter sees the resolved prompt (built by the runner from
the state's prompt source) and the parsed schema (loaded by the
runner from the schema artifact) but does not see the raw prompt
source or the schema's filesystem path. Resolving these is the
runner's job, not the adapter's.

The `reads` dict contains the latest committed versions of every
artifact named in the state's `reads` declaration, including any
artifact the adapter needs as input context (for instance, an
agent adapter receives its running messages artifact in `reads`).
The adapter's view of the artifact store is read-only and is
limited to the values the executor passed in this request.

`backing_options` contains the values of any backing-scoped
clauses the state declared. The names match the keywords from the
grammar. The adapter validates these against its own contract
(for instance, the shell adapter requires either `command` or
`runs` to be present) at `prepare` time.

### Per-backing adapter requirements

Each backing imposes additional requirements on its adapter
beyond the generic contract.

**Model adapter.** Accepts the `prompt_artifact` and `schema`
fields. Produces a model payload (per the result-schemas
document) containing `output`, `verdict` (when a schema is in
use), `fields` (the structured response when a schema is in
use), and token and cost metrics when the underlying provider
reports them. The adapter is responsible for translating between
the runner's canonical message representation and the provider's
wire format.

**Agent adapter.** Wraps a model adapter with conversation-
history-aware preparation. At `prepare` time, the adapter reads
the agent's running messages artifact from the `reads` dict
(which the executor populates from the artifact store), appends
the state's input as a new turn in its working copy, and submits
the resulting conversation to the underlying model. After
invocation, the adapter reports in its payload (a) the model's
response and (b) the new conversation turn (or turns) that
should be appended to the agent's messages artifact. The
adapter does not write to the artifact store. A
profile-registered result parser scoped to agent invocations
reads the payload, produces a tentative write extending the
running messages artifact with the new turn or turns, and the
executor commits the tentative through the normal commit path.

After commit, the executor populates the `history_artifact` and
`history_version` fields of the payload (per the result-schemas
document) with the artifact name and the new committed version
ID. These fields are written into the envelope after the parser
runs, not by the adapter.

Compression-policy enforcement happens through the same path:
when the policy's trigger condition fires during `prepare`, the
adapter requests a compression run from the runner-level
compression machinery, receives the compressed history as a
proposed update, and includes the proposed update plus the
`compression_event` description in its payload. A
compression-aware result parser turns the proposed update into a
tentative write on the messages artifact and produces the
compression-event record the executor logs.

The point of routing every messages-artifact mutation through
the parser-and-commit path is uniformity: agent history
participates in postcondition checks, parser-failure rollback,
artifact-write logging, and resume reconstruction the same way
every other declared write does. The agent adapter has no path
that bypasses the artifact store.

**Shell adapter.** Accepts `runs` (or `command`) plus
`continue_on_fail` from `backing_options`. Spawns each command
in turn, captures stdout and stderr to runner-provided file
paths, records exit codes and per-command durations, and
constructs the shell payload's `commands` and `aggregate`
sub-objects. The adapter respects the `continue_on_fail` flag:
when false, the first nonzero exit short-circuits the rest, and
unrun commands are recorded with `skipped: true`.

**Human adapter.** Accepts the `options` list from the state's
declaration (carried in `backing_options`) and the resolved
prompt artifact. Sends a notification through the registered
notification backend, blocks until the human responds, and
returns the human payload. The notification backend is itself an
internal interface (Telegram, email, terminal prompt, mock) the
runner exposes to the human adapter; v0 ships with at least the
Telegram backend that mcloop already uses.

**Subworkflow adapter (v1).** Reserved. Will accept a
subworkflow workflow name and external inputs, run the
subworkflow as a child execution, and return the inner workflow's
condensed terminal envelope. Not implemented in v0.

### Adapter lifecycle and isolation

Adapters are instantiated once per runner process, not per
invocation. The runner asks the profile registry for the adapter
class registered against a given backing, instantiates it at
startup, and reuses the instance for every invocation that uses
that backing. Adapters that hold persistent state (a long-lived
relay connection, a cached subprocess pool) manage that state
internally and clean up when the runner shuts down.

Adapters do not share state with other adapters. The agent
adapter does not see the shell adapter's subprocesses; the human
adapter does not see model adapters' API keys. Cross-adapter
coordination happens through artifacts in the artifact store via
the executor's commit path, not through direct adapter-to-adapter
calls or direct artifact-store calls.

## Artifact store and versioning interface

The artifact store holds typed, versioned artifact values for
the current run. It is the substrate that the design document's
"Artifact" section describes; this section specifies the
operations and their semantics.

### Operations

```
store.declare(name, type, qualifiers) -> ()
store.read_latest(name) -> {value, version_id, type} | null
store.read_version(name, version_id) -> {value, version_id, type} | null
store.list_versions(name) -> [{version_id, written_at, written_by}]
store.tentative_write(name, value) -> tentative_handle
store.commit_tentative(handles) -> [version_id]
store.discard_tentative(handles) -> ()
```

`declare` registers an artifact with the store at workflow load
time. `qualifiers` carries `source` and `initial` declarations
from the artifact's source-level declaration. After `declare`,
reads against the artifact are valid (returning the initial
value or the loaded source content as version 0; or null when
neither is present and no write has happened).

`read_latest` returns the most recent committed version of an
artifact by name. Returns null when the artifact has never been
written and has no initial value or source-loaded content.

`read_version` returns a specific version by ID. Used by the
log replay logic during resume and by audit tools.

`list_versions` returns the version history, ordered by
write time. Used by audit tools and by the log writer.

The `tentative_*` operations are how the executor mutates
artifacts. They support the result parser failure rollback rule
from the result-schemas document. Profile result parsers produce
tentative writes during invocation finalization; if any parser
fails, all tentative writes for that invocation are discarded.
If all parsers succeed and postconditions pass, the executor
commits all tentatives at once. The store guarantees atomicity
at the granularity of one `commit_tentative` call: either every
handle in the list becomes a committed version, or none does.

There is no public unconditional `write` operation. All
artifact mutation goes through `tentative_write` followed by
`commit_tentative`. This gives the executor a single
chokepoint through which postcondition checks, parser failure
rollback, log emission, and resume reconstruction all flow.

### Storage layout per type

The store handles different artifact types with type-appropriate
storage:

- **`text`, `json`, `messages`, `prompt`, `schema`**: stored
  inline in the runner's primary data store. Each version
  records the value verbatim. The primary data store is a
  single SQLite database file or equivalent (the choice is a
  v0 implementation decision).
- **`file`, `directory`**: stored as references to filesystem
  paths. The store records the path string and a content hash
  computed at write time; readers receive the path and the hash.
  Versioning of `file` and `directory` artifacts is by content
  hash: writing the same content produces the same version ID,
  and readers can verify the file has not changed externally
  by recomputing the hash.
- **`git-workspace`**: stored as a reference to a working
  directory plus the runner's checkpoint refs (under
  `refs/orchestra/checkpoints/`). Each "version" of a workspace
  artifact corresponds to a checkpoint commit on the workspace's
  git repo. The versioned-workspace profile is responsible for
  the actual checkpoint mechanics; the artifact store records
  the version-to-checkpoint-ref mapping.

### Version IDs

Version IDs are content-addressable hashes for inline types and
for `file` and `directory`. For `git-workspace`, version IDs are
the commit SHAs of the checkpoints. For other types where
content addressing is impractical, the store generates a UUID.

The point is that version IDs are stable: a given version ID
always refers to the same artifact content for the lifetime of
the run. The log records version IDs at every read and write,
making the run fully auditable.

### Initial values and source-loaded content

When an artifact is declared with an `initial` qualifier, the
store treats the initial value as version 0. `read_latest`
returns version 0 until a write happens; subsequent writes
produce versions 1, 2, and so on.

When an artifact is declared with a `source file <path>`
qualifier, the store reads the file at workflow start, stores
the content as version 0, and treats subsequent writes the same
way. The file is read once at startup; later changes to the file
on disk do not affect the artifact's value during the run. (For
`source path` on a `git-workspace`, the workspace is bound to
the directory and version 0 is the workspace's HEAD commit at
startup; subsequent writes are checkpoints on top of that.)

When an artifact is declared with both `source` and `initial`,
the validator rejects the declaration. The two qualifiers are
mutually exclusive.

### Concurrent writes

Per the design document's validation rule, parallel states
writing to the same artifact are a load error. The store does
not need to handle concurrent writes from different states. The
only concurrency it must handle is within a single multi-actor
state, where multiple actors run in parallel and a profile-
registered aggregate-aware result parser populates the artifact
from their combined results. That parser runs once, after the
multi-actor state's join completes, so from the store's
perspective the write is still a single transaction.

## Profile registry and validation pipeline

The profile registry is the runner's catalog of what is
available beyond the core. It is consulted by the loader during
workflow validation and by the executor during invocation.

### What the registry holds

For each profile loaded into the runner, the registry holds:

- The profile's name and version.
- The set of registered artifact types (name, validation rules,
  storage backend selector).
- The set of registered actor backings (name, adapter class).
- The set of registered backing-scoped keywords (name, scope
  predicate, value type, semantics callback).
- The set of registered postconditions (name, scope predicate,
  evaluation callback).
- The set of registered guard predicates (name, evaluation
  callback).
- The set of registered result parsers (name, applicable backing
  filter, applicable artifact type filter, parse callback).
- The set of registered validation rules (name, rule callback).
- The set of registered default policies (timeout defaults,
  retry defaults, mode defaults).
- The set of registered resume hooks (artifact-type filter,
  pre-reentry callback). See "Resume hooks" under "Resume
  semantics" below.

A "scope predicate" is a function that decides whether a
registration applies to a given state. For backing-scoped
keywords, the predicate typically checks the state's `actor`
binding. For postconditions like `require_diff`, the predicate
checks the state's `actor` and the artifact types it writes.
For resume hooks, the filter checks the artifact types the
state declares as writes.

### Profile loading

When the loader encounters a `uses profile <name>` declaration,
it asks the registry to load the named profile. Profile
loading happens once per runner process; multiple workflows that
use the same profile share the loaded registrations.

After all `uses profile` declarations are processed, the loader
runs conflict detection: for every registration category
(artifact types, actor backings, keywords, postconditions, guard
predicates, result parsers, validation rules, resume hooks), it
checks whether any two loaded profiles registered the same name.
If any collision is found, the workflow is rejected with a load
error naming the conflicting profiles and registrations.

### Validation pipeline

The loader runs the following validation phases in order. A
failure in any phase produces a load error and aborts loading;
the workflow does not proceed to execution.

**Phase 1: parse.** The loader runs the grammar parser
(specified in the grammar document) against the workflow file.
Parse errors include malformed indentation, unterminated
strings, and disallowed identifier characters.

**Phase 2: profile load.** The loader processes every
`uses profile` declaration, loading each profile into the
registry and running conflict detection. This phase establishes
which artifact types, actor backings, and keywords are
recognized.

**Phase 3: declaration resolution.** The loader checks every
top-level declaration: models exist in the model registry, roles
have valid prompt sources, agents reference declared models,
groups reference declared roles or agents, artifact types are
known to the profile registry, schemas (where declared) refer to
loadable files. External input declarations are recorded.

**Phase 4: name uniqueness.** The loader checks that all
declared names (states, artifacts, external inputs, models,
roles, agents, groups) are globally unique within the workflow.
This is the resolution-order rule from the grammar document.

**Phase 5: state validation.** For each state, the loader runs
the core validation rules from the design document (rules 5-11)
plus any registered profile validation rules. This includes
checking that:
- transition targets are declared states or terminal targets;
- prompt sources resolve;
- multi-actor states declare a join policy;
- retryable outcomes have transitions;
- schema-backed states have transitions for every verdict in the
  schema's enum;
- backing-scoped keywords are used only in states whose backing
  the registering profile covers;
- writes that profiles can populate have a registered parser
  available.

**Phase 6: dataflow.** The loader builds a dataflow graph
relating reads to writes across states. It checks that every
artifact a state reads is either declared with an `initial` or
`source` qualifier or written by some state in the workflow.
This is where the loop-progress pattern from the design document
becomes a load-time check: if a state's `reads` includes an
artifact that no state writes (or initializes), the loader
issues a warning. It does not error, because the artifact might
be deliberately initialized by a profile-provided mechanism not
visible to the static check, but a warning is appropriate
because the more common cause is a typo or an oversight.

**Phase 7: cycle bounds.** The loader detects directed cycles
in the state graph and runs the lint check from validation
rule 11: every cycle should include at least one transition
guarded by a counter, a guard on workflow state the cycle can
change, a human gate that can exit, or a verdict that routes
out. Cycles with no termination mechanism on any transition are
warnings, not errors. The workflow-level `max_total_steps`
declaration is the hard ceiling.

After all phases pass, the loader produces an in-memory
workflow representation: the state graph, the resolved
declarations, the registered profile capabilities, and the
initial artifact store contents. This representation is what the
executor runs.

## Execution loop

The executor takes a validated workflow representation and runs
it. The loop is single-threaded for a single workflow run;
concurrent invocations within a multi-actor state are managed by
the executor itself, not by spawning multiple executors.

### Run initialization

When a run starts, the executor:

1. Generates a fresh run ID.
2. Initializes the artifact store with declared artifacts: those
   with `initial` qualifiers get their initial values as version
   0; those with `source file` qualifiers get their loaded file
   contents as version 0; those with `source path` qualifiers
   are bound to the named directory and recorded with the
   current state of that directory as version 0.
3. Initializes the counter table: `attempts.<state>` and
   `retries.<state>` are zero for every declared state.
4. Records the external inputs supplied by the runner caller in
   the run-level metadata.
5. Sets the current state to the start state (the first state
   declared in the workflow body, per validation rule 3).
6. Writes the run-start log record.

### Per-state execution

Entering a state runs the following sequence, in order. Each
step is logged.

**Step 1: increment counters.** The executor increments
`attempts.<current_state>`. If the entry is the result of an
on-error or on-timeout retry from the same state, it also
increments `retries.<current_state>`. If the entry is from any
other source, `retries.<current_state>` is reset to zero. The
post-increment value of `attempts` is the value
`envelope.attempt` will hold for this invocation.

**Step 2: build invocation request.** The executor constructs
the invocation request by:

- Reading every artifact named in the state's `reads` from the
  store, recording the version IDs.
- Resolving the state's prompt source: a `prompt file` is read
  from disk, a `prompt template` is rendered against the
  state's reads and external inputs, a `prompt from` reference
  resolves to a prior state's prompt artifact.
- Loading the schema artifact (if a `schema` clause is present).
- Collecting backing-scoped clause values into `backing_options`.
- Recording the start time.

**Step 3: prepare invocation.** The executor calls
`adapter.prepare(invocation_request)` against the adapter
registered for the state's actor backing. The runner logs the
prepared invocation: resolved prompt content (or a reference to
the prompt artifact), normalized command lines for shell, the
notification message for human. Logging the prepared invocation
before any side effects happen is the basis for resume's
correctness: if the runner crashes after `prepare` but before
`invoke`, resume can re-prepare from the same inputs and produce
the same result.

**Step 4: invoke.** The executor calls
`adapter.invoke(prepared_invocation)`. The adapter performs the
actual call (model API request, subprocess spawn, notification
send) and returns a payload. The executor writes the full
payload to the run's `payloads/` directory and records the end
time and `duration_ms`. The log's `actor_invoke_end` record
includes a payload summary plus a `payload_ref` pointing at the
on-disk payload file.

**Step 5: postcondition checks.** For each registered
postcondition that applies to this state (per the registering
profile's scope predicate), the executor runs the postcondition
callback against the payload and the artifact store. If any
postcondition fails, the executor sets the envelope's `status`
to `error`, `outcome` to `error`, and `error.kind` to
`postcondition_failure` with detail naming the failed
postcondition. Step 6 (parser dispatch) is skipped in this case;
no artifacts are written.

**Step 6: result parser dispatch.** For each registered result
parser whose backing filter matches the state's actor backing
and whose artifact-type filter matches one of the state's
declared writes (or whose registration covers adapter-proposed
content like the agent messages-append parser), the executor
runs the parser callback against the payload. Each parser
produces tentative writes for the artifacts it is responsible
for. If any parser fails, the executor sets `status` to `error`,
`outcome` to `error`, `error.kind` to `parser_failure`, discards
all tentative writes for this invocation, and proceeds to step
8.

**Step 7: commit writes.** If postconditions and parsers all
succeeded, the executor commits all tentative writes through
`store.commit_tentative`, recording each new version ID in the
envelope's `artifacts_written`. Where the payload references
written artifacts (the agent payload's `history_artifact` and
`history_version` fields), the executor populates those payload
fields from the committed version IDs.

**Step 8: finalize envelope.** The executor builds the result
envelope per the result-schemas document: `state_id`, `attempt`,
`actor_binding`, `status`, `outcome`, `started_at`, `ended_at`,
`duration_ms`, `inputs_read`, `artifacts_written`, `payload`,
`error`. The envelope is logged.

**Step 9: transition selection.** The executor walks the state's
`on <outcome>` transitions in declaration order, evaluating any
guard against the runtime context (the just-finalized envelope,
the counter table, external inputs, the artifact store). The
first matching transition determines the next state. If no
transition matches, the workflow exits with an error (the
validator should have caught this at load time, but the
executor checks at runtime as a safety net).

**Step 10: check step budget.** The executor checks
`state_entries_so_far` against `max_total_steps`. If exhausted,
the workflow transitions to `stop` regardless of the chosen
transition target, with a recorded reason.

**Step 11: route.** The executor sets the current state to the
chosen transition target and returns to step 1. Terminal targets
(`done`, `stop`) end the run.

### Multi-actor states

A state that invokes a group runs the group's members in
parallel during step 4. The executor builds one invocation
request per member (each with its own resolved prompt under
that member's role's default prompt or the state-level
override), calls `adapter.prepare` and `adapter.invoke` for
each, and waits for the join policy to be satisfied:

- `join all`: wait for every member to return.
- `join any`: return as soon as one member returns successfully.
- `join quorum N`: return as soon as N members return
  successfully.

For `join any` and `join quorum`, the executor calls
`adapter.cancel` on outstanding invocations once the join policy
is satisfied.

The aggregate envelope is built per the result-schemas
document's "Aggregate results for multi-actor states" section.
Postcondition checks (step 5) and result parser dispatch (step
6) for multi-actor states use aggregate-aware variants where
the parser sees the parent envelope with member envelopes
inside.

### Cancellation

The runner accepts a cancellation signal (SIGINT, an explicit
API call, or a runner-internal cancellation flag). On
cancellation, the executor:

1. Calls `adapter.cancel` on the current invocation, if one is
   in progress.
2. Sets the envelope's `status` and `outcome` to `cancelled`.
3. Logs the envelope.
4. Routes to `stop`.

Cancellation is best-effort: an adapter that does not implement
`cancel` (or whose `cancel` returns without actually
terminating the underlying subprocess or API call) may leave the
side effect in progress. The runner does not wait indefinitely.

## Log format

Logs are JSON Lines, one record per line, written to a per-run
log file. Records are appended only; the runner never rewrites
or deletes a log line. The file is fsynced after each record
is written, so a crash leaves a complete, truncated log rather
than a partial last record.

### Common record fields

Every record includes:

- `ts`: timestamp in ISO 8601 with millisecond precision.
- `run_id`: the run's identifier.
- `seq`: a monotonic integer, starting at 0 for the run-start
  record and incrementing for every subsequent record.
- `event`: the event type (see below).
- `state_id`: the current state, when the event is state-
  scoped. Null for run-level events.
- `attempt`: the value of `attempts.<state_id>` at the time of
  the event. Null for run-level events.

### Event types

The runner emits the following event types. The list groups
state-scoped events by their position in the per-state execution
sequence, starting with case-2 resume actions that run before
re-entry.

- `run_start`: emitted once at the start of the run. Includes
  the workflow file path, the workflow name, the spec version,
  the loaded profiles, the external inputs, and the run ID.
- `resume_hook`: emitted on case-2 resume, once per
  profile-registered resume hook execution, before the
  `state_enter` of the re-entered state. Tagged with the
  interrupted state's `state_id` so the hook execution is
  state-scoped in the log even though it runs prior to
  re-entry. Includes the hook name, the artifact it acted on,
  the artifact version it restored to, and the action taken.
  See "Resume hooks" below.
- `state_enter`: emitted at step 1 of per-state execution.
  Includes the just-incremented counters.
- `actor_prepare`: emitted at step 3, after `adapter.prepare`
  returns. Includes the prepared invocation summary (resolved
  prompt artifact ID for LLM, command list for shell, options
  list for human).
- `actor_invoke_start`: emitted at the start of step 4, when
  `adapter.invoke` is about to be called. Includes the actor
  binding.
- `actor_invoke_end`: emitted at the end of step 4. Includes
  a payload summary (output length and verdict for LLM,
  per-command exit codes for shell, chosen option for human)
  and a `payload_ref` that points at the on-disk payload file
  in the run's `payloads/` directory. The summary is enough
  for routine log inspection; the full payload is read by
  following the `payload_ref`.
- `postcondition_check`: emitted at step 5, once per registered
  postcondition that applies. Includes the postcondition name
  and its result.
- `parser_run`: emitted at step 6, once per registered parser
  that applies. Includes the parser name, the artifacts it
  populated, and any failure detail.
- `artifact_write`: emitted at step 7, once per artifact
  committed. Includes the artifact name and the new version ID.
- `state_exit`: emitted at step 8. Includes the compact
  envelope (status, outcome, duration_ms, inputs_read,
  artifacts_written, error) and the same `payload_ref` the
  matching `actor_invoke_end` carried, so consumers reading
  only the `state_exit` record can still locate the full
  payload.
- `transition`: emitted at step 9. Includes the chosen outcome,
  the matching transition (with the guard expression that
  resolved to true, if any), and the target state.
- `step_budget_exhausted`: emitted at step 10 if the budget is
  exhausted. Routes to `stop`.
- `cancelled`: emitted on cancellation. Includes the reason
  (SIGINT, API call, internal flag).
- `compression_event`: emitted by the executor when an agent
  invocation triggered a compression pass. Includes the
  `compression_event` payload from the result-schemas document.
- `notification_sent`: emitted by the human adapter when a
  notification is dispatched.
- `choice_received`: emitted by the human adapter when a human
  response arrives.
- `run_end`: emitted once at the end of the run. Includes the
  terminal state (done or stop), the total duration, and a
  summary of artifact versions at end-of-run.

### Multi-actor logging

Multi-actor states emit per-member `actor_invoke_start` and
`actor_invoke_end` records, each tagged with the member name in
addition to the state name. Each per-member `actor_invoke_end`
carries its own `payload_ref` to the per-member payload file.
The aggregate `state_exit` record follows once the join policy
has been satisfied and the aggregate envelope has been
finalized; its `payload_ref` points at the aggregate payload
file (the parent envelope with member envelopes inside).

### Log retention and storage

v0 stores logs as plain JSON Lines files in a per-run directory
under the runner's data root. The data root layout is:

```
<data_root>/
  runs/
    <run_id>/
      log.jsonl              # the run's log
      artifacts/             # inline artifact storage (text, json, etc.)
      payloads/              # full per-invocation payload files,
                             # referenced by payload_ref from
                             # actor_invoke_end and state_exit records
      transcripts/           # full LLM transcripts referenced from
                             # model and agent payloads
      shell_output/          # captured stdout/stderr for shell actors
```

Logs may contain sensitive data (resolved prompts, model
responses, shell stdout). The design document flags this and
defers retention policy to a later document; v0 logs to local
files only.

## Resume semantics

A run can crash mid-execution (process killed, host rebooted,
runner bug) and be resumed by re-launching the runner against
the same run ID. The runner reads the log to reconstruct state.

### Resume entry point

Resume starts from the data root's per-run directory. The
runner reads the log file and replays it, rebuilding:

1. The artifact store, by re-applying every committed
   `artifact_write` record in order. Inline artifacts are
   re-loaded from `artifacts/`. `git-workspace` artifacts are
   re-attached by checkpoint ref. `file` and `directory`
   artifacts are re-validated by content hash.
2. The counter table, by replaying every `state_enter` record.
3. The current state, by reading the most recent `transition`
   record's target state. If no transition record exists for
   the most recent state entry, the current state is the state
   that entry referenced.

### Two cases

After replay, the runner is in one of two cases:

**Case 1: the last state completed.** The most recent log
records for the last state visited are
`state_enter ... actor_invoke_end ... state_exit ... transition`.
The transition record names the next state. The runner sets the
current state to that target and resumes the executor at step 1.

**Case 2: the last state did not complete.** The most recent
log records for the last state visited are
`state_enter ...` followed by some prefix of the per-state
sequence that does not include `state_exit`. The state was
interrupted mid-execution. The runner runs any applicable
profile-registered resume hooks (see "Resume hooks" below),
emitting one `resume_hook` log record per execution, and then
re-enters the same state. The `state_enter` record for the new
attempt is written after all `resume_hook` records have been
emitted. Re-entry increments `attempts.<state>` again, so the
next envelope's `attempt` is one greater than the partial
attempt's.

In case 2, the runner does not attempt to recover the partial
invocation's payload. Even if `actor_invoke_end` was logged,
without a corresponding `state_exit` the runner cannot be
confident that postconditions and parsers ran to completion;
re-entering and re-invoking is correct and simple. Tentative
writes from a crashed invocation are not committed (since
`commit_tentative` produces `artifact_write` records, the
absence of those records means nothing was committed) and are
discarded along with the rest of the partial state.

### Resume hooks

A resume hook is a profile-registered callback that runs after
log replay completes and before the executor re-enters an
interrupted state in case 2. The hook is the mechanism by which
a profile prepares external state (the working directory, an
external system) for safe re-invocation of an interrupted
state.

A resume hook is registered with:

- A name (for logging).
- An artifact-type filter: which artifact types this hook acts
  on (for instance, `git-workspace`).
- A scope predicate: which states' interrupted re-entry triggers
  this hook (typically "states that declare a write of the
  filtered artifact type").
- A pre-reentry callback: receives the artifact name, the latest
  committed version ID for that artifact, and a handle to the
  artifact store. The callback's job is to bring the external
  state in line with that version ID.

When the runner is preparing to re-enter an interrupted state,
it walks the state's writes. For each write whose artifact type
matches a registered hook's filter and whose state matches the
scope predicate, the runner invokes the hook with the latest
committed version of that artifact. The hook executes its
restoration logic and returns. The runner emits a `resume_hook`
log record per hook execution, tagged with the interrupted
state's `state_id`. Once all applicable hooks have run, the
runner writes the new `state_enter` record and resumes the
per-state execution sequence at step 1.

The ordering is strict: every applicable `resume_hook` record
appears in the log before the `state_enter` record for the
re-entered attempt. A reader reconstructing the run's history
sees that hooks ran first, then re-entry happened.

### Versioned-workspace resume hook

The versioned-workspace profile registers a resume hook on
`git-workspace` artifacts. When the runner re-enters an
interrupted state that writes a `git-workspace` artifact, the
hook restores the workspace's working tree to the commit
recorded as the latest committed version of the artifact at the
time of the crash. The mechanics are profile-internal (a
`git reset --hard` against the checkpoint ref, plus
`git clean` of untracked changes), but the contract is the
runner-visible part: after the hook runs, the working tree
matches the committed artifact version, and the interrupted
state's re-invocation starts from a known clean state rather
than from a partial mutation left by the crashed invocation.

Without this hook, a shell command that mutated the workspace,
crashed before `state_exit`, and resumed would re-run on top of
the dirty partial mutation. The checkpoint mechanism does not
protect against accumulated partial mutations unless the runner
explicitly restores. The hook makes the restore explicit.

The hook applies to states that declare `writes <name>
git-workspace` and whose actor backing performs side effects on
the working tree. In practice, this is shell-actor states and
LLM states under `mode readwrite` from the code profile. States
that read but do not write the workspace are not in scope: their
re-invocation does not require the workspace to be restored,
because they do not mutate it.

### Resume and side effects

Re-invoking is not idempotent in general. A shell command that
modified the workspace will run again (after the resume hook
has restored the workspace). A model call that cost money will
be made again. A human notification that was sent may already
have been answered, in which case re-sending is inappropriate.

Per-backing handling:

- **Model and agent adapters**: re-invoke unconditionally.
  Re-running an LLM call wastes tokens but does not corrupt
  state. The agent's running messages artifact is at the latest
  committed version, which excludes any uncommitted turns from
  the crashed invocation.
- **Shell adapters**: re-invoke unconditionally, after any
  applicable resume hooks have run. For workspace-writing
  shell states, the versioned-workspace resume hook has reset
  the working tree to the latest committed checkpoint before
  the adapter is invoked.
- **Human adapters**: the adapter is responsible for detecting
  duplicate notifications. v0 implementations should record a
  notification ID in the prepared invocation, log it before
  sending, and on resume check whether a response has already
  been received for that notification ID before re-sending.
  This is an adapter-internal concern; the runner does not
  arbitrate it.

The design accepts that some duplicated work happens on resume
in exchange for a simple, easy-to-audit recovery mechanism.

### Resume of multi-actor states

A multi-actor state interrupted mid-invocation re-runs all of
its members on resume. Members that completed before the crash
are re-run, their previous envelopes discarded. This is wasteful
but correct under the same reasoning as single-actor resume:
without a `state_exit` record, the runner cannot be sure the
join policy was satisfied or that aggregate-aware parsers ran.

Resume hooks run before re-entry of multi-actor states the same
way they run for single-actor states: the runner walks the
state's writes and invokes any applicable hooks once before
re-invoking the members. All `resume_hook` records appear
before the `state_enter` for the re-entered attempt.

## Open questions

The following are deferred to adapter-specific or
implementation-specific follow-ons.

1. **Notification backend interface.** The human adapter relies
   on a notification backend for delivering and receiving
   choice-gate prompts. The interface this backend exposes (push
   API, pull API, persistence model) is not specified here. mcloop
   uses Telegram; v0 should keep the interface narrow enough that
   email, terminal prompt, and a mock backend can plug in.

2. **Compression actor invocation path.** When an agent
   adapter's context policy triggers a compression pass, the
   adapter requests compression from the runner-level
   compression machinery. Whether that machinery is itself a
   special-case actor (with its own adapter and its own
   envelope), a shared library function the executor calls
   directly during step 6 parser dispatch, or something in
   between, is an implementation question. The constraint
   established here is that the compressed history reaches the
   messages artifact through the same parser-and-commit path as
   any other artifact write.

3. **Adapter packaging and discovery.** v0 ships with a fixed
   set of adapters compiled into the runner. A future runner
   version may load adapters dynamically (Python entry points,
   Go plugins, separate processes communicating over an IPC
   protocol). The contract specified here is intended to admit
   either model.

4. **Artifact store consistency under resume.** The store's
   guarantees during normal execution are well-defined
   (`commit_tentative` is atomic). During resume, the store's
   replay logic must reconstruct exactly the committed history
   from the log. Edge cases (a crash partway through writing
   `artifact_write` records for a single `commit_tentative`
   call, with some records flushed and others not) need a
   defined behavior. v0 should make `commit_tentative` write a
   single batch record rather than per-artifact records, so
   atomicity at the log level matches atomicity at the store
   level.

5. **Log rotation and run cleanup.** Long-running mcloop-like
   workflows over many tasks will produce large logs. v0 keeps
   per-run directories under the data root indefinitely; a
   future version needs a retention policy. Out of scope here.

6. **Failure of the runner itself, mid-log-write.** If the
   runner crashes between writing a record and fsyncing, the
   record may be lost. The runner's resume logic must handle a
   log that ends mid-record (truncated last line is discarded
   on replay). Implementation detail; flagging.

7. **Resume hook failure.** A resume hook may itself fail (for
   instance, the versioned-workspace hook cannot reset the
   working tree because a file is locked or the repository is
   corrupt). v0 treats hook failure as a fatal resume error: the
   runner aborts resume, writes a record naming the failed
   hook, and exits without re-entering the interrupted state.
   Recovery in that case requires manual intervention.

8. **Orphaned payload files.** Payload files in `payloads/` are
   written before the corresponding `state_exit` is logged. A
   crash between the payload write and the `state_exit` write
   leaves an orphaned payload file referenced only by the
   `actor_invoke_end` record (if that was logged) or by
   nothing. v0 retains orphans for diagnostic purposes; a
   future garbage-collection policy is out of scope.
