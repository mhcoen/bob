# Orchestra: Preliminary Design Document

## What this document is

This is a preliminary design for **Orchestra**, a project for specifying and
running multi-LLM systems. It describes the conceptual model, the language,
and the runtime requirements at a level sufficient to start prototyping.

This is not a finished spec. It is the result of a long design conversation
that produced a workflow meta-language for one specific application (mcloop,
an autonomous code task runner) and then generalized that language until it
could express arbitrary multi-LLM systems. The original code-specific spec
is preserved at `workflow-metalanguage.md` in this directory; this document
supersedes it conceptually but does not invalidate it. The code workflow is
re-expressed here as one profile of the general language.

The design is informed by:
- Practical experience driving mcloop and Duplo, which orchestrate Claude
  Code subprocesses across multiple LLMs.
- Manual multi-LLM workflows the author runs by hand (relaying between
  Claude Desktop, Claude Code, and ChatGPT).
- Established patterns from AWS Step Functions, XState, and Temporal,
  borrowing concepts (typed states, guarded transitions, durable
  execution, parallel join semantics) without copying their machinery.
- Critical reviews from multiple LLMs at successive stages of the design.

The goal is a small, regular, composable language for describing systems of
interacting LLMs. The system is meant to be readable by humans, generatable
by LLMs, and statically validatable so that errors surface at load time
rather than at runtime.


## Goals and non-goals

### Goals

1. Make it cheap to specify ad hoc multi-LLM architectures: councils,
   design loops, code workflows, research pipelines, and so on.
2. Provide a small, regular, orthogonal core whose primitives compose
   into rich behavior.
3. Treat the language as data: human-authored, LLM-authored, and
   machine-validated all on the same footing.
4. Cleanly separate the language from any one application domain. The
   coding-specific concerns of mcloop become one profile of the language,
   not the language itself.
5. Support persistent agent identity (a model with conversation
   continuity) without depending on provider-managed sessions.
6. Express the same primitives well enough to handle three concrete
   workloads as an acid test:
   - **mcloop's code implementation workflow** (write, check, review,
     fix, write tests, commit).
   - **The author's design loop** (Claude drafts, GPT critiques, Claude
     reflects, repeat under human approval).
   - **A five-member council with chair arbitration** (advisors with
     distinct perspectives, anonymized peer review by a separate set
     of reviewers, chairman synthesis).

### Non-goals (for v0)

1. Dynamic spawning of states or actors at runtime.
2. A general expression language inside the workflow file.
3. Recursive workflows or full higher-order workflow composition.
4. Distributed execution across machines.
5. Production-grade Temporal-style determinism.
6. Browser-tab automation or web UI scraping. UI automation adapters
   are not part of v0 core, but the adapter interface should not
   preclude them. A future macOS Accessibility adapter, browser
   automation adapter, or similar can be added without changing core
   semantics.
7. A grand unified ontology that subsumes every multi-agent framework
   ever built. The goal is a sharp tool for specific use cases, not a
   universal substrate.


## Conceptual model

Orchestra describes the execution of a **workflow**: a finite state machine
whose states perform work and whose transitions route control. Work means
**invoking an actor** to produce a **result** that may write to or read
from one or more **artifacts**. The runner persists enough state to resume
after a crash.

The language is built from a small number of orthogonal concepts. Each is
introduced below.

### External inputs

A workflow run takes external inputs. The most common is a `task` record
provided by the runner caller (e.g. mcloop iterating over PLAN.md tasks
passes one task per workflow run). External inputs are referenced in
guards and templates by name (`task.needs_tests`, `task.name`). A
workflow declares which external inputs it expects; the runner refuses
to start a run that lacks a required input.

### Actor

An **actor** is anything that does work in the workflow. Every state's
work is an actor invocation. Actors come in several backings:

- **Model**: an LLM invoked via a backend (Claude API, Codex CLI,
  Claude Code subprocess, OpenRouter, etc.). Stateless from the
  workflow's perspective: each invocation is fresh.
- **Shell**: a non-LLM actor that runs shell commands.
- **Tool**: a non-LLM actor that calls a registered tool (e.g. a
  vector DB query, a search API, a structured tool call). Tools are
  out of scope for v0 but the abstraction reserves the slot.
- **Human**: an actor whose response comes from a human via the
  notification backend (Telegram, etc.).
- **Workflow**: a subworkflow invoked as a single state. Deferred to v1.

An **agent** (see below) is not a separate actor backing but a
wrapping around a model-backed actor that adds persistent identity
and conversation continuity. Workflows that need conversational
continuity declare an agent and invoke it; the runner handles the
state accumulation behind the scenes.

The unification under "actor" is conceptual: it gives the workflow
syntax a single invocation primitive. The runner does not pretend all
actor backings behave alike. Each backing has its own **invocation
contract** that the runner enforces. See "Actor invocation contracts"
below.

### Actor invocation contracts

Every actor backing declares a contract along these dimensions. The
contract is part of the actor backing's definition; the runner enforces
the contract at invocation time.

- **Inputs**: what the actor accepts (prompt artifact, command string,
  question + options, subworkflow inputs).
- **Outputs**: what the actor produces (text, structured JSON, exit
  code + stdout/stderr, a chosen option, a subworkflow result).
- **Outcomes**: the typed verdicts the state can transition on
  (`complete`/`error`/`timeout`/`cancelled` for plain LLM; verdict enum
  for schema-backed LLM; `pass`/`fail`/`error`/`timeout`/`cancelled`
  for shell; etc.).
- **Side effects**: whether the actor mutates artifacts, the
  workspace, external systems.
- **Timeout semantics**: how the actor responds to SIGTERM, what
  grace period it gets, whether timeout is meaningful (a human gate
  may legitimately wait for hours).
- **Blocking semantics**: whether the actor blocks indefinitely
  (humans, choice gates) or returns within bounded time (model calls,
  shell commands).
- **Structured result**: every actor backing produces a structured
  result with a canonical shape. The shape includes at minimum
  status, outcome, duration, and any backing-specific fields (for
  shell, this is per-command exit codes, stdout, and stderr; for
  LLM, this is the response text and token/cost metrics; for human,
  the chosen option). The result is the substrate from which a
  profile's result parsers populate declared artifacts. See
  "Profile-registered result parsers" under Profiles below.

The actor abstraction unifies invocation syntax. The contracts ensure
that the runner does not paper over real semantic differences between
backings.

### Model

A **model** is a backend for inference. Examples: `opus`, `kimi`,
`deepseek`, `gpt`. A model has no role, no prompt, and no persona.
It is an invocation method.

Models are declared in a model registry. The registry maps short IDs to
invocation commands and provider configuration. Provider routing (env
vars, base URLs, API keys) lives in the shell environment, not in the
registry, so the same model ID can be reached via different providers
by changing env vars.

### Role

A **role** is a function or perspective. Examples: `designer`, `critic`,
`arbiter`, `reviewer`, `contrarian`. A role has a default prompt source
that defines what playing that role looks like.

Roles and models are orthogonal. The same model can play different roles
in different states (Opus as designer in one state, Opus as arbiter in
another). The same role can be played by different models (Opus as
reviewer, Kimi as reviewer, GPT as reviewer).

A state may invoke a role with the role's default prompt, or override
the prompt at the state level. The state-level override is the same
mechanism as choosing any prompt source: it doesn't introduce a new
concept, it just substitutes a different prompt source for the role's
default.

### Prompt source vs prompt artifact

Two distinct concepts:

- A **prompt source** is a recipe for producing a prompt. Examples: "use
  this file," "apply this template to these values," "use the prompt
  artifact produced by state X." A prompt source is a static description.
- A **prompt artifact** is a resolved, concrete prompt produced from a
  prompt source at invocation time. The runner constructs the prompt
  artifact, uses it for the invocation, and logs its content as part
  of the run record.

Logs record the resolved prompt artifact (and its ID), not just the
source recipe. This matters for reproducibility, debugging, and
auditability: the source might be a template applied to values that
change per run, and the actual text used is what matters when
investigating a result.

Prompt sources include:

- A static file: `prompt file prompts/critic.md`. The path is
  resolved relative to the directory containing the workflow file
  (absolute paths are also accepted). The file's existence is
  validated at workflow load time.
- A reference to a prior state's prompt artifact: `prompt from tune-prompt.prompt`.
  Only states whose result is typed as a `prompt` artifact may be
  referenced this way. Validation that the referenced state exists is
  done at load time; the prompt artifact's content is not inspected
  until runtime since it does not exist yet at load time.
- A template applied to runtime values: `prompt template prompts/critic.md
  with task, draft.output`. Substitution is by named variable. The
  template file's existence is validated at load time; the substitution
  values are checked at runtime.

This makes meta-prompting (Min Choi / Lyra style) a first-class pattern:
an earlier state produces a prompt artifact, and a later state uses that
artifact as its prompt source. The earlier state's job is literally
"produce a prompt for some downstream invocation." Its result type is
`prompt`, not `text`.

### State

A **state** is a named invocation point in the workflow. It binds:

- An actor (what does the work).
- A role (what perspective the actor takes, when applicable).
- A prompt source (what prompt the actor receives, when applicable).
- Inputs (what artifacts and values are available).
- Outputs (where the result goes; see Artifacts).
- Transitions (how control routes after the state finishes).
- Timeout and retry policy.

States are the unit of execution. Workflows are made of states connected
by transitions.

### Workflow-level declarations

A workflow file declares, in addition to its states and the supporting
artifacts, models, roles, agents, and groups:

- **External inputs**: the named inputs the workflow expects from the
  runner caller (see "External inputs" above).
- **Profiles in use**: which profiles the workflow loads.
- **`max_total_steps`** (or equivalent `max_state_visits`): a hard
  ceiling on the number of state entries during a single run. The
  runner enforces this at runtime: when the budget is exhausted, the
  workflow transitions to `stop` with a recorded reason. This is the
  primary safety net against unbounded cycles. Every workflow must
  declare this; the validator rejects workflows without it.
- **Compression model**: the runner-level compression model setting
  (see "Context management" above). Surfaced here so workflow authors
  can see what model is producing summaries for their agents.

### Agent

An **agent** wraps a model-backed actor with persistent identity and
conversation continuity. An agent declares a model and a context policy
(see below) that determines how its conversation history accumulates.

A bare model invocation is stateless: each call is fresh. The model has
no memory of prior calls in this workflow.

An agent invocation is stateful: the runner maintains a conversation
log for the agent across all of its invocations within a workflow run.
Subsequent invocations of the same agent see that log as part of their
context automatically.

#### Agents and roles

The same agent can be invoked in different roles at different states.
The agent's history is **per-agent, not per-role**: an agent invoked
first as designer and then as arbiter sees the prior designer
conversation when invoked as arbiter. This matches what happens in a
single ongoing chat session with a human's underlying LLM (Claude, GPT)
where you pivot the same conversation from design to arbitration.

If you want role isolation, declare a separate agent for each isolated
role. Two agents backed by the same model are two distinct conversation
threads with no shared history.

v0 supports only agent-level history scoping. Per-role history scoping
within a single agent is deferred. The design recognizes this is a
real use case but does not address it in v0.

#### Persistent vs ephemeral

The author's manual design loop (Claude drafts, GPT critiques, Claude
reflects) is a multi-agent dialogue: the Claude side and the GPT side
both have ongoing conversation continuity across many turns. Orchestra
expresses this via two persistent agents and states that invoke them
in sequence.

Agents do not need provider-managed sessions to have continuity. The
runner can manage conversation history itself (see "Context management"
below). Provider-managed continuity (Claude Code's `--resume`, OpenAI's
`conversation_id`) is optional optimization, not a requirement.

### Artifact

An **artifact** is a typed, named piece of data that lives outside any
single state's result. Artifacts are how state outputs persist and how
states share data beyond the immediate predecessor relationship.

Core artifact types:

- `text`: free text.
- `json`: structured JSON.
- `messages`: an ordered list of conversation turns (the natural shape
  of an agent's history).
- `prompt`: a resolved prompt used in an invocation (distinct from a
  prompt source, which is the recipe for producing one).
- `schema`: a JSON Schema, used for validating structured outputs.
- `document`: a file or directory of files (typed; intended for things
  like PLAN.md, generated reports, drafts).
- `file`: a single file path with no semantic typing.
- `directory`: a directory path.
- `git-workspace`: a versioned working directory backed by git. The
  workspace is mutable; commits, checkpoints, branches, diffs, and
  rollback are available. Git is a versioning substrate, not a code
  artifact. A `git-workspace` artifact is appropriate any time the
  workflow operates on evolving textual or file-based state where
  history, branching, and rollback matter: source code, papers,
  manuscripts, design docs, plans, research notes, prompt libraries,
  generated reports, council transcripts, decision records. The
  `git-workspace` artifact type is provided by a versioned-workspace
  profile, not by the code profile. The code profile uses
  `git-workspace` and adds code-specific behavior (lint, type-check,
  test verification, `require_diff`); a writing or planning profile
  could use the same artifact type with different domain-specific
  postconditions.

#### Lifecycle and versioning

Artifacts are **versioned**. Every write to an artifact produces a new
version with a unique ID. The artifact name resolves to the latest
version when referenced by a downstream state. Logs record the artifact
version ID at every read and write so that runs are fully auditable
and a stale reference cannot accidentally resolve to a newer version
than was intended.

Specifically:

- A state that writes artifact `draft` produces a new version of `draft`.
- A later state that reads `draft.output` reads the latest version at
  the time of that read.
- The `messages` artifact type is append-only: each write adds turns
  rather than replacing the artifact. New turns produce new versions
  but old turns remain.
- A single state may write multiple distinct artifacts. The list of
  artifacts a state writes is part of the state's declaration.
  Nontrivial states should declare their writes explicitly
  (`writes draft text`, `writes review json`, `writes advisor_outputs
  messages`) rather than relying on implicit `<state>.output`
  references. Explicit writes keep dataflow visible and prevent
  artifacts from sliding back into being implicit state outputs
  with new branding.
- Parallel states writing to the same artifact are a v0 load error.
  Concurrent writes would make "latest version at time of read"
  surprising and ordering-dependent. v0 forbids the case rather
  than defining merge semantics.
- Artifact storage format depends on the type: `text` and `json` are
  stored inline in the runner's data store; `file`, `directory`, and
  `git-workspace` are references to filesystem locations.

The lifetime of an artifact is the workflow run unless declared
otherwise. Artifacts are not shared across runs in v0.

#### Loop-progress pattern

When a workflow contains a cycle, the state that issues the loop-back
transition must write some artifact that the loop target reads. If it
does not, the next iteration sees the same inputs as the previous one
and is semantically identical to it. Versioning of artifacts (each
write produces a new version, the name resolves to the latest) is the
mechanism that carries information across the loop.

Two cases follow from this:

1. The loop target's `reads` must include the artifact the loop-issuing
   state writes. This is enforced by the explicit-writes discipline
   above plus the requirement that nontrivial states declare their
   reads; the workflow author is expected to verify the dataflow at
   the loop-back point.
2. If the first iteration of the loop runs before any iteration of the
   loop-issuing state has executed, the artifact will not yet exist.
   In that case the artifact declaration must provide an initial
   value so the first read is well-defined.

Loop progress and loop termination are distinct. Progress (this
section) makes a loop iteration meaningful by ensuring its inputs
differ from the previous iteration's. Termination (validation rule
11, "Cycle and step bounds") is what causes the loop to exit. A
loop with progress but no termination mechanism runs until
`max_total_steps` is exhausted; a loop with termination but no
progress exits, but its iterations are wasted because each one sees
the same inputs. Both are required for a useful cycle.

The acid-test sketches surface this pattern in two of three workflows.
It is not a new primitive: it is a consequence of versioned artifacts
plus the discipline that durable data flows through named artifacts.

### Transition

A **transition** routes control from one state to another based on the
outcome of the source state. Outcomes are typed per actor type:

- For LLM invocations without a verdict schema: `complete`, `error`,
  `timeout`, `cancelled`.
- For LLM invocations with a verdict schema: the verdict enum values,
  plus `error`, `timeout`, `cancelled`.
- For shell-backed actors: `pass`, `fail`, `error`, `timeout`,
  `cancelled`.
- For human-backed actors (choice gates): the option labels chosen,
  plus `timeout`, `cancelled`.

Transitions can be guarded:

```
on approve when task.needs_tests => write-tests
on approve => commit
```

Guards evaluate against runtime context (task fields, prior state
results, retry and attempt counters). Multiple guarded transitions for
the same outcome are evaluated in declaration order; the first match
wins. Unguarded transitions for an outcome are fallthroughs.

### Terminal targets

`done` ends the workflow successfully. `stop` ends it as failed. They
are reserved transition targets, not state types.


## The four-way factoring

The cleanest factoring of an LLM invocation is into four orthogonal
concerns:

1. **Backend** (model): how inference is performed.
2. **Function** (role): what perspective the model takes.
3. **Prompt source** (file, generated, template, artifact): how the
   actual prompt text is produced for this invocation.
4. **Invocation** (state): a specific execution point binding the above
   plus inputs, outputs, transitions, and policy.

Roles, models, prompt sources, and states are all separately reusable.
A workflow file declares each independently and combines them at states.

This is more granular than a typical "agent = model + prompt" framing.
The reason is that the same model can play different roles, and the
same role can have different prompts depending on context (a static
file today, a generated prompt tomorrow, a template applied to
runtime data the third day). Conflating these makes simple cases
slightly easier and complex cases significantly harder.


## Context management for agents

Agents accumulate conversation history. The runner maintains this
history as a `messages` artifact for each agent. When an agent is
invoked at a state, the runner constructs the request by appending the
state's input to the agent's existing history.

The context policy controls how the history is bounded as the
conversation grows. A policy specifies:

- **Retention**: which turns are kept verbatim (typically a recency
  window, possibly bounded by a token budget).
- **Compression trigger**: the condition under which older turns get
  compressed into a summary.
- **Compression actor**: the model used to produce summaries (configured
  at the runner level, not per agent).
- **Summary artifact**: where the compressed summary lives in the
  agent's history.

Compression is **incremental** (compress only the oldest unsummarized
turns when triggered, not the whole history) and **recursive**
(existing summaries can themselves be re-summarized when they grow
beyond their own threshold). This matches the hierarchical
summarization pattern used in production long-context agent systems.

The runner stores both raw and compressed history for the duration of
the run, so that compression failures or unexpected information loss
can be inspected and repaired.

The detailed boolean semantics of the trigger condition (positive vs
negative limits, OR vs XOR combinations of turn count and token count
thresholds) are an implementation concern for the runner spec, not
part of this conceptual design. They will be specified in the runner
spec alongside the other policy mechanics.

### Why runner-managed

Runner-managed context is provider-agnostic, inspectable, editable, and
portable. It works for any backend that accepts a messages array,
which is essentially every modern LLM API.

Different providers express messages differently: the Anthropic API
puts the system prompt in a separate top-level field with content as
either a string or an array of typed blocks; the OpenAI API puts the
system message inside the messages array as the first item with content
as a string. The runner translates between its internal canonical
message representation and each backend's wire format. Workflow authors
do not see the provider-specific differences.

Provider-managed sessions (Claude Code's `--resume`, OpenAI's
`conversation_id`) are optional adapters the runner may use when
available, but they are not the default and the language does not
depend on them.


## Persistent vs ephemeral actors

Most actor invocations are ephemeral: the runner spawns a subprocess or
makes an API call, gets a result, and the actor ceases to exist.

A few actor types are persistent: they exist before the workflow starts,
continue to exist after a state's invocation completes, and may be
invoked many times across the workflow run with conversation continuity
between invocations. Examples:

- A long-running Claude Desktop session reachable via the relay tool.
- A Claude Code session that has its own ongoing context.
- An OpenAI API conversation identified by `conversation_id`.

Persistent actors are declared as agents with the appropriate adapter.
The runner registers adapters per actor type:

- **Subprocess adapter**: spawns a fresh `claude -p` (or `codex exec`)
  per invocation. Stateless from the runner's perspective.
- **Subprocess-with-resume adapter**: spawns `claude -p --resume <id>`
  to inherit continuity from a prior subprocess invocation. Provider
  manages the session.
- **Relay adapter**: connects to an existing Claude Desktop or Claude
  Code instance via the existing mcloop relay. The relay has its own
  semantics (sender identity, mark_read, unread_only) which the
  adapter wraps; see the existing mcloop relay implementation for
  details. The adapter exposes a uniform send/receive interface to
  the runner.
- **API adapter with runner-managed history**: stateless API calls,
  with conversation history accumulated and maintained by the runner.
  The default for most agent declarations.
- **API adapter with provider conversation_id**: stateless API calls
  but using `conversation_id` (or equivalent) for provider-managed
  continuity. Optional optimization.

Workflows reference agents abstractly. The runner picks the adapter
based on the agent's declared adapter type.

Adapters are not interchangeable in subtle ways even when the runner
exposes them through a uniform interface. A relay-attached Claude
Desktop session has UI state, project context, and possibly memory
features that Orchestra's artifacts do not represent. A runner-managed
API conversation has only the messages the runner sent. A
subprocess-with-resume session has whatever continuity the provider's
resume mechanism preserves, which may differ from raw message-array
concatenation. These differences matter for some workflows. The
adapter abstraction makes the invocation interface uniform; it does
not make the underlying actor backings semantically equivalent.

For v0, the supported adapters are:
- Subprocess (with and without resume) for `claude -p` and `codex exec`.
- Relay for Claude Desktop / Claude Code (already in use by mcloop).
- API with runner-managed history for any backend that takes a
  messages array.

The macOS Accessibility-based bridge between Claude Desktop and ChatGPT
desktop is excluded from v0 because it requires elevated system
permissions the user is not willing to grant. The adapter interface
does not preclude such bridges; they can be added later without changes
to the core.


## Profiles

Orchestra has a **core language** that is domain-neutral. Domain-specific
concerns live in **profiles** layered on top of the core.

### What profiles can register

Profiles do not extend the core grammar. They register additional
capabilities through a fixed set of extension points:

- **Artifact types**: e.g. `git-workspace` for the versioned-workspace
  profile.
- **Actor backings**: new adapter types backing the generic actor
  abstraction.
- **Backing-scoped state-level keywords**: profiles may register
  state-level keywords whose use is **scoped to a specific actor
  backing** the profile registers (or co-registers). Example:
  `runs` is a multi-line shell-command block legal only inside
  `actor shell` states; `require_diff` is legal only inside states
  that write a `git-workspace` artifact under `mode readwrite`. The
  validator rejects use of a backing-scoped keyword outside its
  legal context. This is not a macro system or arbitrary syntax
  extension: each registered keyword has a fixed semantic
  registered with the actor backing it applies to, and is
  statically validated against the state's declared backing.
- **Postconditions**: predicates that must hold after a state runs
  (e.g. `require_diff` for the code profile, registered as the
  postcondition associated with the keyword of the same name).
- **Guard predicates**: new predicate forms usable in `when` clauses.
- **Result parsers**: parsers for converting actor output into typed
  result fields and into declared artifacts beyond the core's
  defaults. See "Profile-registered result parsers" below.
- **Validation rules**: load-time checks beyond the core rules.
- **Default policies**: domain-appropriate defaults for timeouts,
  retries, modes.

Profiles do **not** add new top-level keywords, new state types, or
new transition syntax. They do not introduce new state-level keywords
that are not scoped to a registered actor backing. The core grammar
is closed at the workflow, transition, and unscoped-state levels;
profile extension applies only inside the body of a state whose actor
backing the profile registers or co-registers.

### Profile-registered result parsers

Every actor invocation produces a structured result (see "Actor
invocation contracts" above). Profiles may register parsers that
convert the actor's structured result into typed artifact values,
populating artifacts the state declares as `writes`. Examples:

- The code profile registers a result parser that converts a shell
  actor's per-command stdout/stderr/exit-code result into a
  structured `check-errors` json artifact when the state declares
  it writes such an artifact.
- A council profile (illustrative) might register a parser that
  converts an LLM actor's structured response into entries in a
  `messages` artifact under specific role-group invocations.

This means a state's declared artifact writes are populated by the
runner-and-profile machinery, not by ad-hoc inspection of state
result fields by downstream states. The downstream state reads the
declared artifact; the actor's raw result fields are runner-internal
metadata.

The exact contract between actor backings and result parsers is
deferred to the runner spec. The conceptual requirement is that
every actor backing produce a structured result rich enough that a
profile parser can populate any declared artifact the state writes.
For the shell actor specifically, the structured result must include
at minimum per-command exit codes, stdout, stderr, aggregate
pass/fail, and total duration; profile parsers build on top of this.

### Concrete profiles

A layered set of profiles is anticipated. v0 ships the versioned
workspace profile and the code profile; others are sketched here as
illustration of the layering.

**Versioned workspace profile.** Domain-neutral. Registers:
- The `git-workspace` artifact type (mutable working tree, commits,
  checkpoints, branches, diffs, rollback).
- Workspace mutation modes (`readwrite`, `readonly`) and the
  cleanliness policy that enforces readonly.
- Runner checkpoint mechanism (refs under
  `refs/orchestra/checkpoints/`).

The versioned workspace profile is usable by any domain that benefits
from history, branching, and rollback over evolving textual or
file-based state. Profiles below depend on it.

**Code profile.** Depends on the versioned workspace profile. Adds:
- The `require_diff` postcondition (and its keyword) for mutating
  LLM states (errors the state if a `readwrite` invocation produces
  no diff). Scoped to states that write a `git-workspace` artifact
  under `mode readwrite`.
- The `runs` block keyword on `actor shell` states (a multi-line
  list of shell command strings), together with `continue_on_fail`
  for diagnostic vs sequenced semantics. Scoped to `actor shell`.
- A shell-result parser that populates a declared `check-errors`
  json artifact (or analogous artifact name) from per-command
  stdout/stderr/exit-code data.
- Shell-backed actor adapters via `claude -p` and `codex exec`
  invocation patterns oriented to code-specific subprocess output
  (transcripts, structured tool call traces).
- Default policies for code work (test-runner timeouts, lint
  expectations).

This is the profile mcloop uses.

**Writing/research profile (illustrative, not v0).** Would depend on
the versioned workspace profile. Would add postconditions like
`require_citation` or `require_outline_unchanged`, default patterns
for outline/draft/revise loops, and conventions for manuscript
branches.

**Planning profile (illustrative, not v0).** Would depend on the
versioned workspace profile. Would add patterns for plan artifacts,
task DAGs, alternatives, decision records, rollback from bad
decompositions.

**Council profile (illustrative, not v0).** Independent of the
versioned workspace profile. Would register a `messages` artifact
convention for advisor outputs, a standard "anonymized peer review"
pattern as a static role-group template, and a chairman synthesis
pattern with verdict schemas.

Workflows that don't use the versioned workspace profile don't see
`git-workspace` at all. A council workflow has no workspace, no diff
postconditions, and no shell-backed code-aware actors.

### Profile composition

Profiles are additive when their registrations don't collide. If two
profiles register the same artifact type name, actor backing name,
state-level keyword name, postcondition name, guard predicate name,
or validation rule name, the runner refuses to load the workflow with
a load error identifying the conflicting profiles. The author is
expected to use profiles that have been designed to coexist; v0 does
not provide an override mechanism for resolving profile conflicts.

A workflow can use multiple profiles simultaneously when they don't
conflict (e.g. a workflow that has a code-implementation phase
followed by a council-review phase).


## Static groups

A **group** is a named set of invocation participants that can be
invoked together at a single state. Groups exist for ergonomic
reasons: writing five role names every time you set up a council is
tedious and error-prone.

A group is one of two kinds, declared explicitly:

- **Role group**: members are role names. The state that invokes the
  group also declares a single model that backs all of them. Each role
  uses its own default prompt (or a state-level override). This is the
  natural form for a council where every member is the same backend
  but with a different perspective.

  ```
  group advisors
    kind roles
    members contrarian, first-principles, expansionist, outsider, executor
  ```

  Invoked at a state by binding a model:

  ```
  state advise
    actor model opus
    group advisors
    input question
    join all
    on complete => peer-review
    ...
  ```

- **Agent group**: members are agent names. Each agent already carries
  its own model and (where applicable) session identity. The state
  invoking the group does not declare a model; the agents bring their
  own. This is the natural form when the participants are heterogeneous
  (different models, different conversation histories).

  ```
  group panel
    kind agents
    members claude-thread, gpt-thread, kimi-thread
  ```

  Invoked at a state:

  ```
  state cross-model-review
    group panel
    input draft.output
    join any
    on complete => synthesize
    ...
  ```

A group declaration's `kind` is required. A group may not mix roles
and agents in v0.

Groups in v0 are **static**: members are declared, not generated.
Dynamic group sizing (e.g. "5 reviewers for strategic decisions, 3 for
routine ones") is deferred. The static abstraction is enough for the
council acid test.


## Joins for multi-actor states

A state that invokes multiple actors in parallel must declare a join
policy:

- `join all`: state succeeds only if every actor produces a non-error
  result. A single actor failure fails the state.
- `join any`: state succeeds if at least one actor produces a non-error
  result. Failed actor branches are recorded but do not propagate.
- `join quorum N`: state succeeds if at least N actors produce
  non-error results.

Multi-actor states without an explicit `join` are workflow load errors.

Aggregate references are available for downstream states:
- `<statename>.outputs`: list of `output` fields from all actors that
  completed successfully, in declaration order. Failed actors are
  omitted. Safe under any join policy.
- `<statename>.<actor>.<field>`: the named field of a specific actor's
  result. Resolves to `null` if that actor failed. Safe only under
  `join all`.


## Visibility (v1, sketch only)

For some council patterns, advisors should not see each other's
responses; reviewers should see anonymized advisor outputs; the chair
should see everything. This requires per-actor visibility on artifacts.

v0 does not implement visibility primitives. The acid test for the
council will use a workflow structure where visibility is enforced by
which artifacts each state's input includes, not by per-actor
visibility rules. This is sufficient for the canonical five-member
council.

v1 will add a single `visible-to` declaration on artifacts. The
absence of `visible-to` means the artifact is visible to any state
that references it (the v0 default). When `visible-to` is declared,
only listed roles, agents, or groups may reference the artifact in
their inputs. There is no separate `invisible-to`; visibility is
expressed positively only.

```
artifact advisor-outputs messages
  visible-to chair, reviewers
```


## Validation

Workflow files are statically validated at load time. The runner
rejects invalid files before any execution begins. Validation rules:

1. Spec version is present and supported.
2. Every workflow has at least one state.
3. The first declared state in each workflow is the start state.
4. State names within a workflow are unique and not reserved words.
5. Every transition target is a declared state, `done`, or `stop`.
6. Every actor invocation references a declared model, agent, role,
   or group as appropriate. A role group invocation must declare a
   model; an agent group invocation must not declare a model (the
   agents bring their own).
7. Every prompt source resolves at the level appropriate to its kind:
   file paths exist on disk (validated at load time); state references
   point to declared states whose result is typed as a `prompt`
   artifact (existence of the referenced state is load-time, content
   of the prompt artifact is necessarily run-time); template variables
   match declared inputs.
8. Multi-actor states declare a `join` policy.
9. Every state with retryable outcomes declares those transitions
   (`on error`, `on timeout` for LLM and shell actors; `on timeout`
   for choices).
10. Schema-backed LLM states have transitions for every verdict in
    the schema's verdict enum and no extras.
11. **Cycle and step bounds.** This rule covers loop *termination*
    only. Loop *progress* (whether each iteration sees inputs that
    differ from the previous iteration) is a separate concern, covered
    in the "Loop-progress pattern" subsection of the Artifact section.
    A loop can have progress without termination, termination without
    progress, both, or neither; the validator and the runner address
    them separately.

    Two complementary mechanisms ensure workflows terminate:
    - **Workflow-level step budget**: every workflow must declare
      `max_total_steps` (or `max_state_visits`) at the workflow
      level (see "Workflow-level declarations"). The runner enforces
      this at runtime: when the budget is exhausted, the workflow
      transitions to `stop` with a recorded reason. This is the
      primary safety net. Workflows that omit this declaration are a
      load error.
    - **Cycle guard recommendation (lint)**: the validator notes any
      directed cycle in the state graph that does not include a
      transition guarded by `attempts.<state>` or `retries.<state>`.
      This is reported as a warning, not a load error, because
      proving genuine boundedness statically is hard. Authors are
      expected to add a termination mechanism on at least one
      transition in each cycle. Termination mechanisms include:
      attempt or retry guards (`when attempts.<state> < N`, `when
      retries.<state> < N`); guards on workflow state that the loop
      itself can change (`when task.tests_written`, an explicit flag
      written by a state in the loop); human choice gates that can
      exit; verdict outcomes that route out of the loop. Artifact
      progress is *not* a termination mechanism; an iteration that
      sees fresh inputs but has no way to exit will still run until
      `max_total_steps` is exhausted. The lint warning surfaces
      cycles that have no termination mechanism on any transition.
12. Profile-specific validation rules registered by any profile in
    use also pass. Conflicting profile registrations (same artifact
    type name, actor backing name, state-level keyword name,
    postcondition name, guard predicate name, or validation rule
    name) are themselves load errors. Profile-registered
    backing-scoped state-level keywords are legal only inside
    states whose actor backing the profile registers or
    co-registers; uses outside that scope are load errors.
13. Every external input referenced in guards or templates is
    declared in the workflow's external input list.


## Logging

Every state entry, retry, and exit produces a structured log record.
Logs are JSON Lines, one record per line, written to a per-run log
file. Required fields per record:

- Timestamp.
- Workflow run ID.
- State name.
- Event type (enter, exit, retry, actor_start, actor_end,
  notification_sent, choice_received, checkpoint, error).
- Attempt number (matches `attempts.<state>`).
- For exits: outcome, duration.
- For LLM actors: model, role, resolved prompt artifact ID, tokens
  in, tokens out, cost (when reported by the adapter), transcript
  path.
- For shell actors: command, exit code, stdout path, stderr path.
- For choice exits: chosen option, notification message ID.
- For checkpoints: artifact references with version IDs, phase
  (pre or post).
- For artifact reads and writes: artifact name and version ID.

The log is sufficient to reconstruct what happened, replay decisions,
and resume after a crash. Recording resolved prompt artifact IDs and
artifact version IDs (rather than just source recipes and names)
makes the log fully auditable.


## Resumability

Workflow runs are resumable. After a crash or process restart, the
runner identifies the last committed state from the log and either:

- Resumes from the next transition, if the last state completed and
  recorded its result.
- Re-enters the last state, if it was interrupted mid-execution.
  Re-entry increments `attempts.<statename>`.

Re-entry is not idempotent. The runner does not attempt to recover
partial subprocess output or partial workspace state from before the
crash; it starts the state's invocation fresh. Workspace artifacts
remain in whatever state the crash left them in (preserved, not
rolled back) for diagnosis.


## Lexical and syntactic conventions

The full grammar is not specified in this document. Surface syntax
decisions (whether to use a single `state` keyword with `actor` inside
versus type-prefixed keywords like `invoke`, `choice`, `notify`,
`shell`; whether transitions use `=>` or `->` or `then`; whether
declarations use `:` or block indentation) are deferred to the grammar
phase. The conceptual model does not require any particular surface
form. The important constraint is that the AST be regular: every state
binds an actor, a role (when applicable), a prompt source (when
applicable), inputs, outputs, transitions, and policy. The surface form
can be ergonomic without changing the AST.

Indicative conventions used in examples in this document:

- Files begin with `spec <version>`.
- Workflows are introduced by `workflow <n>`.
- Top-level declarations: `model`, `role`, `agent`, `group`, `artifact`,
  `state`, `prompt`, `import` (for multi-file workflows; v1).
- States in this document's examples use a single `state` keyword with
  `actor model <id>` / `actor shell` / etc. inside. This is one
  defensible surface form, not a final grammar decision.
- Indentation is significant for grouping in the examples.
- Comments are `#` to end of line, except inside double-quoted strings.
- String literals are double-quoted with `\"`, `\\`, `\n` escapes.
- File path references (in `prompt file`, `schema`, etc.) are resolved
  relative to the directory containing the workflow file. Absolute
  paths are also accepted.
- Reserved words may not be used as user-defined names.

The exact grammar will be specified before implementation, informed
by the acid-test workflow sketches.


## Acid test workflows

The design's adequacy is tested against three concrete workflows. All
three must be expressible without special cases or extensions. The
next concrete step in this project is to write actual workflow files
for these three tests in the proposed syntax. The sketches will surface
problems in the abstractions faster than further prose will. Until the
sketches exist, every claim about composability in this document is
provisional.

**Discipline for the sketches.** Every nontrivial state must declare
its artifact writes explicitly. Do not lazily reference
`<state>.output` everywhere. The point of versioned artifacts as the
data substrate is to make dataflow visible; if the sketches use
implicit state outputs, artifacts have silently been demoted to
"named state outputs with better branding" and the abstraction is
failing. This applies to all three tests below.

### Test 1: mcloop code implementation

The workflow currently described in `workflow-metalanguage.md`'s
`implement` example. A task is given. Kimi writes the code. A shell
actor runs lint, type-check, and existing tests. Opus reviews via a
verdict schema (approve / request_changes). On approve and tests
required, Kimi writes new tests. On approve and no tests required,
shell-actor commits. On request_changes, Kimi fixes based on review
feedback and the loop repeats. Mechanical errors from the check loop
to a separate fix state.

This requires: `git-workspace` artifact type, shell actor support,
verdict schemas, guards on task fields, retry policy, choice gates for
abandonment. All in the code profile.

### Test 2: Author's design loop

Two persistent agents: a Claude agent (designer) and a GPT agent
(critic). The user gives a topic. The designer agent drafts a
response. The critic agent reviews the draft. The designer agent
reflects on the critique. A choice state asks the user whether to
continue iterating, accept the result, or stop. If continue, the loop
repeats from critique.

This requires: persistent agents with conversation continuity,
runner-managed message history, choice gates with timeout,
agent invocation across multiple states with the agent's history
preserved.

### Test 3: Five-member council with chair

Five role definitions for the advisors: contrarian, first-principles,
expansionist, outsider, executor. A static role group of these five
advisors, all backed by the same model (Opus, say). The user gives a
question. The advisor group is invoked in parallel with `join all`;
each advisor sees the question and produces a response. The advisor
outputs are anonymized into an artifact.

A separate set of five reviewer roles (distinct from the advisor
roles) forms the peer-review role group. The reviewers receive the
anonymized advisor artifact. Each reviewer reads all five anonymized
responses and produces a peer review. Reviewers may be backed by the
same model as the advisors or a different model; the council is
agnostic to this choice.

A chair state (Opus, in the role of arbiter) receives both the
advisor outputs and the peer reviews via verdict schema and produces
a council verdict.

This requires: roles separable from models, static role groups,
multi-actor states with `join all`, verdict schemas, the same model
usable in multiple roles within a workflow, and the ability to declare
two distinct sets of roles (advisors and reviewers) without conflating
them.


## What this document does not yet specify

The following are deferred to a follow-on grammar/spec document or to
v1. The first item is the most important: result schemas are the next
hard problem because everything else (guards, joins, verdicts, resume,
logging, downstream references) depends on result shape.

- **Canonical result schemas per actor type.** Every actor invocation
  needs a canonical result shape (status, outcome, output,
  artifacts_written, error, duration, metadata, plus backing-specific
  extensions). The acid test sketches will force this to be specified
  before the runner can be built. The shell actor's structured result
  in particular must include per-command exit codes, stdout, stderr,
  aggregate pass/fail, and total duration; the runner spec must define
  how profile result parsers consume this to populate declared
  artifacts.
- Exact concrete syntax (the surface form, punctuation, indentation
  rules in detail, reserved word list, EBNF or equivalent).
- The full set of guard predicates and their semantics.
- The exact API the runner exposes for resuming a run.
- Log storage policy. Resolved prompt artifacts and full transcripts
  may contain sensitive data (private task context, credentials
  accidentally included in input files, proprietary source). v0 logs
  to local files only, but a future version needs explicit policy:
  retention, permissions, redaction hooks, an opt-out for persisting
  resolved prompt content on specific workflows.
- The serialization format for artifacts other than text and JSON.
- Subworkflow call semantics.
- Map / foreach iteration.
- Visibility primitives.
- Cost-based branching syntax.
- Per-role history scoping within a single agent.
- The detailed boolean semantics of the context compression trigger.
- A standard library of profile-provided patterns (council protocols,
  research protocols, etc.).
- Tooling: visualization, linting, formatter, LSP integration.

The order in which these are addressed should be driven by the order
in which they become blocking for the acid test workflows.


## Status and next steps

This document is preliminary. The intended next steps are:

1. **Write the three acid-test workflows in the proposed syntax.** This
   is the immediate next step and the gate on further design work.
   The sketches will force decisions about groups, agents, roles,
   prompt sources, artifact writes, and state references that prose
   cannot resolve. If a workflow requires a feature not described
   here, decide whether to add it to the design or accept it as a v1
   deferral.
2. Pin down the concrete grammar in a follow-on document, informed by
   what the acid-test sketches required.
3. Specify the runner architecture: actor adapter interfaces,
   artifact storage, log format, resumability mechanism, context
   compression trigger semantics.
4. Build the smallest runner that executes one of the three acid test
   workflows on a real input. Iterate on the language and runtime
   together; let the implementation surface gaps and inconsistencies
   that document review missed.
5. Migrate mcloop to use Orchestra as its runtime, with the code
   profile providing the workspace-specific concerns. The current
   `workflow-metalanguage.md` becomes a historical artifact at that
   point.

The conceptual model has stabilized: actors with typed invocation
contracts, agents wrapping models with conversation continuity, roles
and models as orthogonal axes, prompt sources distinct from prompt
artifacts, versioned artifacts as the data substrate, profiles as
additive registrations against a closed core. Further design refinement
without sketches and implementation will not reveal much. The acid
tests will.
