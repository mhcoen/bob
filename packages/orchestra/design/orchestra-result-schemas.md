# Orchestra: Result Schemas

## What this document is

This is the follow-on to `orchestra-design.md` that pins down the
structured result every actor invocation produces. It is the next
deferred item the design document identifies, and it must precede
the grammar phase: references like `attempts.<state>`, `verdict`,
`<state>.outputs`, and `<state>.<actor>.<field>` are meaningful only
once the result shape they refer to is defined.

The document is narrow on purpose. It defines the shape of one
invocation result, the shape of an aggregate result for a multi-actor
state, and the contract by which profile-registered result parsers
turn results into declared artifacts. It does not introduce new
ontology, new state types, new transition machinery, or any concept
not already in the design document.

The reader should already be familiar with `orchestra-design.md`,
particularly the sections on Actor invocation contracts, Profiles
(including the Profile-registered result parsers subsection), the
Artifact section, validation, and logging.

## Goals

1. Define a universal result envelope every actor invocation
   produces, regardless of backing.
2. Define the per-backing payload shapes that fit inside the
   envelope (model, agent, shell, human, subworkflow).
3. Define the aggregate shape for multi-actor states that use
   `join all`, `join any`, or `join quorum N`.
4. Define how outcomes (and verdict values, when a verdict schema
   is in use) are derived from the result.
5. Define the contract between profile-registered result parsers
   and declared artifact writes.
6. State the implications for logging and resumability so the
   logging spec and the runner spec can be written against this.

## Non-goals

1. Wire format. The envelope is a logical structure; whether it
   serializes as JSON, msgpack, or the runner's internal records
   is a runner spec concern.
2. The full set of profile-registered result parsers. This document
   defines the contract, not the parsers themselves. The code
   profile's check-errors parser is given as a worked example
   because the acid tests require it; other profiles' parsers will
   be specified alongside the profiles.
3. Adapter implementation details. How `claude -p` is parsed into a
   model payload, or how the relay adapter normalizes Claude Desktop
   responses into a model payload, is the runner spec's job. This
   document defines what the payload must contain, not how it gets
   there.
4. Cost and budget enforcement semantics. Cost fields appear in the
   model payload because the design records cost in logs, but
   cost-based guards and budget enforcement are deferred per the
   design document.

## The actor/runner contract

A single invocation has two participants: the actor (the model, the
shell command, the human, the subworkflow) and the runner. The
result is built from contributions of both, and the contract between
them must be explicit so the envelope's fields each have a clear
source of authority.

The actor produces a payload. The payload is what the actor returned:
the response text and tokens for a model, the per-command exit codes
and stdout for a shell command, the chosen option for a human, and
so on. The actor knows nothing about the workflow's name, the state's
name, the attempt counter, or which artifacts were declared as reads
or writes.

The runner produces the envelope. The runner knows everything about
the invocation context: which state was entered, on which attempt,
what artifacts were in scope, when the invocation started and
finished, what the resolved outcome is. The runner builds the
envelope around the payload at the end of the invocation.

The result a downstream state references is the envelope. The actor's
payload lives at `payload` inside the envelope; the rest of the
fields are runner-built.

This split matters for two reasons. First, it keeps the actor
contract small: an actor backing's adapter is responsible for
producing one well-defined payload shape, not for synthesizing
runner state. Second, it keeps the envelope's invariants
unambiguous: a downstream state asking for `<state>.attempt` always
gets the runner's authoritative attempt counter, never something the
actor reported about itself.

## The universal result envelope

Every invocation, regardless of backing, produces a result with this
shape:

```
{
  state_id:           string,    # the state's name
  attempt:            integer,   # 1-indexed; see "Counter semantics" below
  actor_binding:      object,    # what the state bound to (see below)
  status:             string,    # one of: ok, error, timeout, cancelled
  outcome:            string,    # the typed outcome (see below)
  started_at:         timestamp, # ISO 8601
  ended_at:           timestamp, # ISO 8601
  duration_ms:        integer,
  inputs_read:        list,      # list of {artifact, version_id} entries
  artifacts_written:  list,      # list of {artifact, version_id} entries
  payload:            object,    # backing-specific (see per-backing sections)
  error:              object | null  # populated when status != ok
}
```

Field meanings:

- **`state_id`**: the name of the state that produced this result.
  Lets a downstream reference like `<state>.field` resolve
  unambiguously when results from multiple states are in flight (for
  example in resume scenarios).

- **`attempt`**: the 1-indexed sequence number of this invocation
  for this state. The first invocation of a state in a run has
  `attempt = 1`, the second has `attempt = 2`, and so on. The
  envelope's `attempt` is fixed at state entry and does not change
  during the invocation. This field is not the same thing as the
  `attempts.<state>` counter that transition guards reference; see
  "Counter semantics" below for the disambiguation.

- **`actor_binding`**: a record of what the state actually bound to
  at runtime. This is the runner's record of "which model and role,
  which agent, which group member, which shell command" was used.
  See "Actor binding record" below.

- **`status`**: one of `ok`, `error`, `timeout`, `cancelled`. This is
  not the same as the outcome. `status` is a coarse classification of
  whether the invocation completed normally; `outcome` is the typed
  verdict the state transitions on. A model invocation that returns a
  parseable verdict has `status = ok` and `outcome = <the verdict>`.
  A model invocation that returns malformed JSON against a verdict
  schema has `status = error` and `outcome = error`.

- **`outcome`**: the typed outcome the transition table dispatches on.
  The legal values depend on the actor backing and on whether a
  verdict schema is in use. See "Outcomes and verdicts" below.

- **`started_at` / `ended_at` / `duration_ms`**: the runner's record
  of the invocation's time bounds. `duration_ms = ended_at -
  started_at` exactly; both timestamps are recorded so wall-clock
  position in the run is recoverable.

- **`inputs_read`**: a list of `{artifact, version_id}` pairs, one
  per artifact the runner made available to the invocation. The list
  matches the state's declared `reads`, with the version_id resolved
  at invocation time. External inputs are not artifacts; they are
  recorded in the runner's run-level metadata, not in this list.

- **`artifacts_written`**: a list of `{artifact, version_id}` pairs,
  one per artifact a profile-registered result parser populated for
  this invocation. See "Artifact population by result parsers"
  below. If no parser ran (a state whose actor backing has no
  registered parser, or a state whose `writes` list is empty), this
  is the empty list.

- **`payload`**: the actor's contribution. Backing-specific shape;
  see per-backing sections below.

- **`error`**: populated when `status != ok`. Shape:
  ```
  {
    kind:    string,   # see "Error and exception shapes" below
    message: string,
    detail:  object,   # backing-specific, may be null
  }
  ```
  When `status == ok`, this field is `null`.

### Actor binding record

The `actor_binding` record names what the runner actually invoked.
Its shape depends on the actor backing:

- **Bare model**: `{ kind: "model", model: <id>, role: <id> | null,
  prompt_artifact_id: <id> }`. The `role` field is null when the
  state did not bind a role (for instance an LLM state whose
  invocation has no role declaration). `prompt_artifact_id` is the
  resolved prompt artifact from the prompt source (see the design
  document's "Prompt source vs prompt artifact" section).
- **Agent**: `{ kind: "agent", agent: <id>, model: <id>, role: <id>
  | null, prompt_artifact_id: <id> }`. The agent's underlying model
  is recorded so the binding stays interpretable when an agent is
  later renamed or its model swapped.
- **Shell**: `{ kind: "shell", commands: [string, ...] }`. The full
  list of commands is recorded so the result is auditable from the
  envelope alone.
- **Human**: `{ kind: "human", options: [string, ...],
  notification_id: <id> | null, prompt_artifact_id: <id> }`. The
  notification_id is the runner's reference to the notification
  that delivered the choice gate to the human.
- **Subworkflow** (v1): `{ kind: "subworkflow", workflow: <name>,
  run_id: <id> }`. Reserved for v1; envelopes for v0 do not
  produce this binding kind.

For multi-actor states (states invoking a group), the envelope
produced by the parent state has `actor_binding.kind = "group"`
and a `members` field listing the per-member bindings; see
"Aggregate results for multi-actor states" below.

### Outcomes and verdicts

The `outcome` field's legal values come from the actor backing's
invocation contract, plus any verdict schema in use.

- **LLM invocation without a verdict schema**: `complete`, `error`,
  `timeout`, `cancelled`. `complete` corresponds to `status = ok`.
- **LLM invocation with a verdict schema**: any value in the
  schema's verdict enum, plus `error`, `timeout`, `cancelled`. The
  schema-driven values correspond to `status = ok`.
- **Shell**: `pass`, `fail`, `error`, `timeout`, `cancelled`. `pass`
  and `fail` both have `status = ok`; the distinction is that
  `pass` indicates the command(s) ran to completion with the
  exit-code result the workflow author asked for, and `fail`
  indicates a non-zero exit (or non-zero exits, depending on
  `continue_on_fail`). `error` is for runner-level failures (the
  command could not be spawned, the binary was missing).
- **Human (choice gate)**: any of the option labels declared in
  `options`, plus `timeout`, `cancelled`. The chosen-option
  outcomes have `status = ok`.
- **Subworkflow** (v1): `complete`, `failed`, `error`, `timeout`,
  `cancelled`. Reserved.

The general rule: `status` answers "did the invocation complete in
a recognized way." `outcome` answers "what did it produce that the
transition table can route on."

When a verdict schema is in use, the verdict is also surfaced as a
field of the model payload (see "Model payload" below). The
envelope's `outcome` is authoritative for transitions; the
payload's `verdict` is the same value, included in the payload so
downstream guards and templates can reference it without having to
parse the envelope's outcome string.

### Error and exception shapes

When `status != ok`, the `error` object's `kind` field takes one of:

- `actor_failure`: the actor itself failed in a backing-specific
  way (LLM returned malformed structured output, shell command
  could not be executed, human cancelled the gate explicitly).
- `timeout`: the state's declared timeout was exceeded. This
  always pairs with `status = timeout` and `outcome = timeout`.
- `postcondition_failure`: a profile-registered postcondition
  rejected the result (for instance `require_diff` on a code-profile
  state that produced no diff). The postcondition's name and
  failure detail go in `error.detail`.
- `parser_failure`: a profile-registered result parser failed to
  populate a declared artifact from the actor's payload. The parser's
  name and failure detail go in `error.detail`.
- `runner_failure`: a runner-level failure not attributable to the
  actor (storage error, log write failure, adapter crash).
- `cancelled`: the workflow was cancelled. This always pairs with
  `status = cancelled` and `outcome = cancelled`.

The `error.message` field is human-readable. The `error.detail`
field is a structured object whose shape depends on `kind`.

## Per-backing payloads

This section defines the payload shape for each actor backing. The
payload always sits at `envelope.payload`. All fields are runner-
populated from the actor's response unless noted otherwise.

### Model payload

For a bare model invocation:

```
{
  output:        string,         # the assistant message text
  verdict:       string | null,  # the verdict enum value if a verdict
                                 # schema is in use; otherwise null
  fields:        object,         # any other top-level fields declared
                                 # by an output-schema or verdict-schema
                                 # JSON Schema attached to the state;
                                 # empty object if no schema in use
  tokens_in:     integer | null,
  tokens_out:    integer | null,
  cost_usd:      decimal | null, # null when cost is not reported by
                                 # the adapter (subscription billing)
  transcript_ref: string | null  # adapter-specific reference to the
                                 # full subprocess or API transcript;
                                 # null for adapters that do not
                                 # produce one
}
```

The `output` and `fields` fields are derived from the actor's raw
response by the model-backing adapter (which knows whether a JSON
Schema is in use and parses accordingly). `verdict` is extracted from
`fields` when a verdict schema is in use, and pulled to a top-level
field for ergonomic reasons since it drives transitions.

If a verdict schema is in use and the response cannot be parsed
against it, the envelope has `status = error`,
`outcome = error`, and `error.kind = actor_failure` with detail
naming the schema and the parse failure. The payload's `output` may
still contain the raw response text for diagnosis; `verdict` is
null and `fields` is empty in that case.

### Agent payload

An agent invocation is a model invocation against a runner-managed
conversation history. The payload extends the model payload with
agent-specific fields:

```
{
  output:           string,
  verdict:          string | null,
  fields:           object,
  tokens_in:        integer | null,
  tokens_out:       integer | null,
  cost_usd:         decimal | null,
  transcript_ref:   string | null,

  history_artifact: string,        # name of the messages artifact
                                   # that holds this agent's running
                                   # history
  history_version:  string,        # version_id of that artifact after
                                   # this invocation appended its
                                   # turn(s)
  compression_event: object | null # populated when this invocation
                                   # triggered a compression pass; see
                                   # the design's context-management
                                   # section
}
```

The `history_artifact` and `history_version` fields make the
agent's history inspectable from the envelope. They are not the
same as `artifacts_written`: the agent's history artifact is
managed by the runner's context-management machinery, not by a
profile result parser. A workflow author does not declare
`writes <agent>.history` on the state. The history is recorded in
the envelope so logs and downstream debugging tools can find it.

`compression_event`, when populated, has shape:
```
{
  triggered_by:    string,  # what caused the compression
  turns_before:    integer,
  turns_after:     integer,
  summary_artifact: string, # the messages artifact entry that holds
                            # the new summary
  summary_version: string
}
```

### Shell payload

For a shell invocation (single command or `runs` block):

```
{
  commands: [
    {
      command:       string,
      exit_code:     integer | null,  # null if skipped
      stdout_path:   string,          # filesystem path to captured stdout
      stderr_path:   string,          # filesystem path to captured stderr
      duration_ms:   integer | null,  # null if skipped
      skipped:       boolean          # true when continue_on_fail is
                                      # false and a prior command's
                                      # nonzero exit short-circuited
    },
    ...
  ],
  aggregate: {
    pass_count:    integer,           # commands that exited 0
    fail_count:    integer,           # commands that exited nonzero
    skipped_count: integer,           # commands that were skipped
    total_ms:      integer            # sum of duration_ms over commands
                                      # that ran (skipped excluded)
  }
}
```

For a single-command shell state (`command "..."`), the `commands`
list has one entry. For a `runs` block, the list has one entry per
line. The `aggregate` block is provided in both cases so downstream
references and result parsers do not need to special-case the count.

The `outcome` derivation for shell:
- `pass`: `aggregate.fail_count == 0` and `aggregate.skipped_count
  == 0`.
- `fail`: `aggregate.fail_count > 0` or (under `continue_on_fail =
  false`) some commands were skipped due to short-circuit. In the
  short-circuit case, `aggregate.fail_count >= 1` is guaranteed
  because the short-circuit was triggered by a fail.
- `error`: a runner-level failure (could not spawn, missing binary).
- `timeout`: state-level timeout was exceeded.
- `cancelled`: the workflow was cancelled.

`stdout_path` and `stderr_path` are filesystem paths because shell
output can be large and is wasteful to store inline in the
envelope. The runner's storage layout for these paths is a runner-
spec concern; the envelope only requires that the paths be
resolvable for the lifetime of the run.

### Human payload

For a choice-gate invocation:

```
{
  chosen:           string | null,  # one of the declared options;
                                    # null if status != ok
  notification_id:  string,         # the notification's identifier
                                    # in the backend (Telegram message
                                    # ID, etc.)
  prompt_artifact_id: string,       # resolved prompt artifact shown
                                    # to the human
  responded_at:     timestamp | null # null if status != ok
}
```

The `chosen` field is the option label the human selected. The
envelope's `outcome` mirrors `chosen` when `status = ok`. When the
human cancelled or the gate timed out, `chosen` is null.

### Subworkflow payload (v1, reserved)

```
{
  workflow:    string,
  run_id:      string,
  result:      object       # the inner workflow's terminal envelope,
                            # condensed to status + outcome + named
                            # output artifacts
}
```

Subworkflow invocations are deferred to v1. The payload shape is
sketched here so the envelope is forward-compatible; v0 envelopes
do not produce a subworkflow payload.

## Aggregate results for multi-actor states

A state that invokes a group runs multiple actors in parallel. The
state still produces one envelope (because transitions and the
runner's state machine work at the state level), but the envelope's
shape is extended to record per-member contributions.

For a multi-actor state:

```
{
  state_id:          string,
  attempt:           integer,
  actor_binding: {
    kind:            "group",
    group:           string,            # the group's declared name
    kind_of_members: "roles" | "agents",
    join:            "all" | "any" | "quorum N",
    model:           string | null,     # set for kind roles, null for kind agents
    members: [
      {
        member_kind:        "role" | "agent",
        member:             string,
        prompt_artifact_id: string | null
      },
      ...
    ]
  },
  status:            string,            # see "Aggregate status" below
  outcome:           string,            # see "Aggregate outcome" below
  started_at:        timestamp,
  ended_at:          timestamp,
  duration_ms:       integer,
  inputs_read:       list,
  artifacts_written: list,
  payload: {
    members: [
      {
        member:    string,              # role name or agent name
        envelope:  <full envelope>      # see below
      },
      ...
    ],
    aggregate: {
      success_count: integer,
      failure_count: integer,
      total_tokens_in:  integer | null,
      total_tokens_out: integer | null,
      total_cost_usd:   decimal | null
    }
  },
  error: object | null
}
```

Each per-member entry's `envelope` is a complete envelope of the
same shape as a single-actor invocation, with that member's
`actor_binding`, payload, status, and outcome. The member envelopes
are flat: no further nesting.

### Aggregate status

`status` for a multi-actor state is derived from member statuses
and the join policy:

- `join all`: aggregate `status = ok` if every member has `status =
  ok`. Any non-ok member sets aggregate `status` to that member's
  status, with `error.kind = actor_failure` and `error.detail`
  naming the failed member.
- `join any`: aggregate `status = ok` if at least one member has
  `status = ok`. If no member succeeded, aggregate status is the
  status of the first failed member in declaration order, with
  `error.detail` listing all member failures.
- `join quorum N`: aggregate `status = ok` if at least N members
  have `status = ok`. Otherwise as `join any`.

### Aggregate outcome

For a multi-actor state, `outcome` is `complete` when aggregate
`status = ok`, and matches the failure status otherwise (`error`,
`timeout`, `cancelled`). Verdict schemas on multi-actor states are
not supported in v0; if a workflow needs to synthesize a verdict
across multiple actors, it should use a downstream single-actor
state to do the synthesis (this is the canonical chair pattern in
Test 2).

### Per-member references

The design document's downstream references for multi-actor states
resolve against this envelope:

- `<state>.outputs`: the list of `payload.output` fields from each
  member envelope whose `status = ok`, in declaration order.
  Members with non-ok status are omitted from this list. Safe under
  any join policy.
- `<state>.<member>.<field>`: looks up the member envelope by name
  and resolves `<field>` against that envelope's payload (or, for
  envelope-level fields like `attempt`, against the envelope
  directly). Resolves to `null` if the named member's `status !=
  ok`. Safe only under `join all`.
- `<state>.<field>` (no member name): resolves against the
  aggregate envelope. For an aggregate state, `<state>.duration_ms`
  is the aggregate duration; `<state>.attempt` is the parent state's
  attempt counter.

### Writes from multi-actor states

A multi-actor state's `writes` declaration covers artifacts
populated by aggregate-aware result parsers (see below). The
canonical case is a multi-actor state writing a single `messages`
artifact whose entries are the per-member outputs, in declaration
order under `join all` or completion order under `join any` /
`quorum`. The parser sees the aggregate envelope and constructs the
artifact from the member envelopes' payloads.

## Counter semantics

Two distinct things share the word "attempt." Disambiguating them
is the job of this section.

**`envelope.attempt`** is a field of the result envelope. It records
the 1-indexed sequence number of one specific invocation. The first
invocation of a state has `envelope.attempt = 1`, the second has
`envelope.attempt = 2`, and so on. Once an envelope is built, its
`attempt` field is immutable. Downstream references to a past
state's envelope (`<state>.attempt`) read this field.

**`attempts.<state>`** is a runtime counter that transition guards
reference. It is not a field of any envelope. It lives in the
runner's run-level state and is updated as the run progresses.

The two are related but not the same. The relationship and the
update rule are:

1. `attempts.<state>` starts at 0 at the beginning of a run.
2. The runner increments `attempts.<state>` *on entry* to that
   state, before invoking the actor. The just-incremented value is
   the value that `envelope.attempt` will have for the invocation
   that is about to start.
3. Transition guards on outgoing edges from any state are evaluated
   *before* the runner crosses the edge to the target. At
   evaluation time, `attempts.<target>` has not yet been
   incremented for the about-to-start invocation. The guard sees
   the count of state entries to `<target>` that have already
   completed (or are currently executing, in the unusual case of
   a self-edge guard).

The acid tests use guards of the shape:

```
on continue when attempts.continue-gate < 6 => critique
```

evaluated on the outgoing edge from `continue-gate`. Under the rule
above, `attempts.continue-gate` at evaluation time equals the
sequence number of the invocation that just completed (the one
whose envelope.attempt is also that value). The guard succeeds
while that count is below the bound, fails when the count reaches
the bound. This is the natural "this is the Nth visit, do X"
reading of the guard.

Equivalently:

```
attempts.<state>  =  number of invocations of <state> that have
                     entered (and either completed or are currently
                     executing) so far in this run.
                  =  envelope.attempt of the most recent invocation
                     of <state>, when that invocation has finished
                     entering.
```

For the `retries.<state>` counter (introduced in the design
document for retry policy), the rule differs in one respect:
`retries.<state>` counts only re-entries caused by `error` or
`timeout` outcomes on the same state, and is reset to zero each
time the state is entered for a non-retry reason (a transition
from a different state, or the start of the run). It is updated on
entry, like `attempts.<state>`, and is similarly visible to guards.

Self-edges (a state transitioning to itself) and retries are the
only cases where a guard on an outgoing edge of state X references
`attempts.X` or `retries.X` for the about-to-start invocation. In
those cases the guard reads the count of completed entries; the
about-to-start entry is not yet counted. This is the same rule as
the cross-state case, applied consistently.

## Verdict and outcome mapping

The relationship between the envelope's `outcome` field and the
state's transition table is direct: the runner looks up the
transition entry whose `on <outcome>` matches the envelope's
`outcome`, evaluates any guard, and routes accordingly. The
envelope is the single source of truth.

When a verdict schema is in use, the schema's verdict enum
constrains the legal values of `outcome` for that state's actor.
Validation rule 10 (every verdict in the enum must have a
corresponding `on <verdict>` transition) is checked against the
schema at workflow load time; the runtime check is "the envelope's
`outcome` is one of the declared `on <outcome>` cases or
`error`/`timeout`/`cancelled`."

Verdicts are visible in two places: the envelope's top-level
`outcome` (authoritative for transitions) and the model payload's
`verdict` (convenient for guards and templates that want to use
the verdict as data without going through the outcome string).
Both fields hold the same value when the verdict is well-formed;
when the model's response is malformed, `outcome = error` and the
payload's `verdict = null`.

## Artifact population by result parsers

The design document's "Profile-registered result parsers"
subsection establishes that profiles convert actor results into
typed artifact values. This section pins down the contract.

### When parsers run

After the actor produces its payload but before the runner
finalizes the envelope, the runner identifies which parsers apply:

1. The state's actor backing identifies the candidate parser set.
   Each profile registers parsers under one or more actor backings.
2. The state's `writes` declaration identifies which artifacts the
   parser must populate. A parser is responsible for populating
   exactly the declared artifacts whose types it is registered to
   produce.
3. Each applicable parser runs against the actor's payload and the
   state's declared `writes`, producing one new version per declared
   artifact. The new version IDs are recorded in the envelope's
   `artifacts_written` field.

If a state declares writes but no registered parser covers the
required artifact type, this is a workflow load error (validation
rule 12 in the design document). Parsers are not optional once a
state declares the corresponding write.

### What parsers receive

A parser sees the actor's payload (the same payload that ends up at
`envelope.payload`), the state's `actor_binding` record, and the
state's declared `writes` list. Parsers do not see other states'
results; they do not see runner-internal state beyond the
invocation in question. This keeps parsers stateless and locally
auditable.

### What parsers produce

A parser produces, for each declared artifact it is responsible
for, one new version of that artifact with a value of the declared
type. The runner stores the new version, assigns a version ID, and
adds `{artifact, version_id}` to the envelope's `artifacts_written`
list.

If a parser cannot produce a value for a declared write (the
payload was malformed in a way the parser could not handle), the
envelope's `status` becomes `error`, `outcome` becomes `error`, and
`error.kind = parser_failure` with detail naming the parser and
the artifact it could not populate. Other parsers' contributions to
this invocation are rolled back (their new artifact versions are
discarded, not written).

### Worked example: code profile shell-result parser

The code profile registers a parser scoped to `actor shell` states.
When such a state declares it writes a `check-errors` json
artifact, the parser produces a new version whose value is a json
object summarizing per-command failures.

The parser receives the shell payload (the per-command list with
exit codes, stdout paths, stderr paths) and produces something
shaped roughly like:

```
{
  ruff:    { exit_code: 1, summary: <parsed ruff output> },
  mypy:    { exit_code: 0, summary: null },
  pytest:  { exit_code: 1, summary: <parsed pytest output> }
}
```

The exact JSON shape is a code-profile decision and is specified
alongside the code profile, not here. What this document specifies
is that the parser sees the shell payload, uses the per-command
fields, and produces a new version of the declared json artifact
that downstream states can read by name.

When the state's `writes` does not name `check-errors` (or
analogous), the parser does not run and produces no artifact
version. This is the correct behavior: not every shell state needs
structured error capture.

### Aggregate-aware parsers

A multi-actor state's `writes` is populated by aggregate-aware
parsers that see the full aggregate envelope (the parent envelope
with its `payload.members` list). The canonical example is a parser
that populates a `messages` artifact from a multi-actor state's
member outputs. Such parsers are registered against the relevant
actor backing in the same way as single-actor parsers; the parser's
implementation handles the aggregate shape.

## Logging implications

The runner's log records (see the design document's Logging
section) are derived from envelopes. Specifically:

- The state's `enter` log record is written before the actor is
  invoked. It includes `state_id`, `attempt`, and `actor_binding`.
- The state's `exit` log record is written after the envelope is
  finalized. It includes the envelope's `status`, `outcome`,
  `duration_ms`, `inputs_read`, and `artifacts_written`.
- Actor-specific log records (`actor_start`, `actor_end`) include
  payload-derived fields like model, tokens, cost (for LLM); per-
  command exit codes (for shell); chosen option (for human).

Resolved prompt artifact IDs and artifact version IDs appear in the
envelope and therefore in the log. The design document's logging
requirements are satisfied as long as the envelope structure here
is honored.

## Resumability implications

After a crash, the runner identifies the last completed state by
finding the most recent `exit` log record with a finalized
envelope. The runner can:

- Resume from the next transition, using the envelope's `outcome`
  to dispatch.
- Re-enter the last state, if no `exit` was recorded for it.
  Re-entry increments `attempts.<state>` (the next envelope's
  `attempt` will be one greater than the partial attempt's).

Partial envelopes (where the actor was invoked but the envelope was
not finalized) are not consumed for resume. The runner discards
partial envelope data and re-invokes the actor. Workspace artifacts
remain in whatever state the crash left them; the `git-workspace`
profile's checkpoint mechanism is what protects the workspace, not
the envelope.

## Open questions

The following are deferred to runner spec or to the grammar phase
once they become blocking.

1. **Storage format for `inputs_read` and `artifacts_written`.**
   The lists are conceptual; the wire format is a runner concern.
   Whether to store them in-band in the envelope or as separate
   index records pointing back to the envelope is open.

2. **Per-member envelope storage for multi-actor states.** Whether
   member envelopes are stored individually and the aggregate
   envelope holds references, or whether the aggregate envelope is
   self-contained, is a runner storage decision. Either is
   compatible with the contract here.

3. **Cost reporting under provider-managed sessions.** When an
   adapter does not surface tokens or cost (subscription-billed
   providers, relay adapters that talk to a desktop app), those
   fields are null. Whether the runner attempts to estimate them
   from message length is open. v0 records null and does not
   estimate.

4. **Compression event detail.** The agent payload's
   `compression_event` field is sketched here; the exact shape
   depends on the runner spec's choice of compression policy
   serialization.

5. **Subworkflow envelope condensation.** The subworkflow payload's
   `result` field is described as a "condensed" terminal envelope.
   What "condensed" means precisely is a v1 question; the full
   inner envelope is too large to carry in the parent's payload,
   but the parent state needs enough to dispatch on the inner
   workflow's outcome and to reference its named output artifacts.

6. **Error rollback granularity.** When a parser fails and the
   runner rolls back other parsers' artifact-version writes, the
   storage operation is straightforward in the inline-stored types
   (text, json) but more delicate in `git-workspace` and `file`
   artifacts that reference filesystem state. The runner spec
   needs to define the rollback contract for each artifact type.
