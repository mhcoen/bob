# Acid Test 2: Five-Member Council with Chair

## Goal

Probe the design's generality by expressing the canonical council
workflow: five advisors with distinct perspectives produce parallel
recommendations on a question, a separate set of five reviewers
produces anonymized peer review, and a chair synthesizes a verdict.

The design document calls out this workflow as the test that the
language is not secretly code-shaped. It exercises:

- Static role groups (`kind roles`) backed by a single model.
- A different role group for reviewers, distinct from advisors.
- Multi-actor states with `join all`.
- The `messages` artifact type as the natural shape for a
  collection of advisor outputs.
- The same model usable in multiple roles within a workflow (Opus
  as advisor, Opus as reviewer, Opus as chair).
- Verdict schemas on a chair-style synthesis state, with
  schema-driven transitions.
- Workflow-structural anonymization (no visibility primitives;
  v0 has none).
- A shell-backed actor used for a mechanical, non-LLM transform
  (the anonymization step), without invoking the code or workspace
  profiles.
- External inputs other than `topic`: a `question` of type `text`
  plus a `decision_id` for log correlation.
- A revision feedback path from the chair back to the advisors that
  actually carries information across the loop.

The sketch deliberately does not use:

- Persistent agents (each advisor and reviewer invocation is a
  fresh model call; there is no continuity to preserve across the
  parallel fan-out).
- Workspace or git artifacts.
- The code profile.

## Workflow sketch

### File: `council.orc`

```
spec 0.1

workflow council

  external_input question text
  external_input decision_id text

  max_total_steps 30

  # Single backend for the whole council. The same model plays
  # every advisor role, every reviewer role, and the chair role.
  # This is the design document's stated council pattern: roles
  # carry the perspective, the model is the same backend for all
  # of them.
  model opus

  # ---------------------------------------------------------------
  # Advisor roles. Each role has its own default prompt that
  # encodes the perspective. Prompts are not in scope for the
  # sketch; only the prompt source declarations matter.
  # ---------------------------------------------------------------

  role contrarian
    prompt file prompts/advisor-contrarian.md

  role first-principles
    prompt file prompts/advisor-first-principles.md

  role expansionist
    prompt file prompts/advisor-expansionist.md

  role outsider
    prompt file prompts/advisor-outsider.md

  role executor
    prompt file prompts/advisor-executor.md

  # ---------------------------------------------------------------
  # Reviewer roles. Distinct set from the advisor roles. Each
  # reviewer reviews one anonymized advisor output set against a
  # specific dimension. The reviewer roles describe a review
  # function, not an advisor perspective; they are intentionally
  # named differently from the advisor roles to make the two sets
  # non-overlapping.
  # ---------------------------------------------------------------

  role consistency-reviewer
    prompt file prompts/reviewer-consistency.md

  role evidence-reviewer
    prompt file prompts/reviewer-evidence.md

  role feasibility-reviewer
    prompt file prompts/reviewer-feasibility.md

  role coherence-reviewer
    prompt file prompts/reviewer-coherence.md

  role bias-reviewer
    prompt file prompts/reviewer-bias.md

  # ---------------------------------------------------------------
  # Chair role. Used at one state, with a verdict schema.
  # ---------------------------------------------------------------

  role chair
    prompt file prompts/chair.md

  # ---------------------------------------------------------------
  # Role groups. Both `kind roles`. The state that invokes a role
  # group declares the model to back all members.
  # ---------------------------------------------------------------

  group advisors
    kind roles
    members contrarian, first-principles, expansionist, outsider, executor

  group reviewers
    kind roles
    members consistency-reviewer, evidence-reviewer, feasibility-reviewer,
            coherence-reviewer, bias-reviewer

  # ---------------------------------------------------------------
  # Verdict schema for the chair. Declared as a workflow-level
  # schema artifact referenced by file path. The schema's verdict
  # enum drives the chair state's allowed transitions.
  # ---------------------------------------------------------------

  artifact council-verdict-schema schema
    source file schemas/council-verdict.schema.json

  # ---------------------------------------------------------------
  # Data artifacts.
  #
  # `advisor-outputs` is a `messages` artifact: each advisor's
  # response is one entry. The append-only semantics of `messages`
  # match the natural shape of "five contributions, ordered."
  #
  # `anonymized-advisor-outputs` is the same shape with role
  # identifiers stripped. Produced by the `anonymize` shell state.
  # Reviewers and the chair read this artifact, not the named
  # advisor outputs. This is workflow-structural anonymization
  # (see finding (F8)).
  #
  # `peer-reviews` is a `messages` artifact, one entry per
  # reviewer.
  #
  # `verdict` is a json artifact constrained by the verdict schema.
  #
  # `chair-feedback` is the channel by which a `revise` verdict
  # carries information back to the next round of advisors. It is
  # initialized empty so the first advisor pass is well-defined
  # (no chair has run yet). On `revise`, the chair writes a new
  # version of `chair-feedback` and the loop returns to `advise`,
  # which now reads the latest non-empty version. See finding (A14)
  # and clarification (F15).
  # ---------------------------------------------------------------

  artifact advisor-outputs messages
  artifact anonymized-advisor-outputs messages
  artifact peer-reviews messages
  artifact verdict json
  artifact chair-feedback text
    initial ""

  # ---------------------------------------------------------------
  # States.
  # ---------------------------------------------------------------

  # Parallel advisor invocation. Five role-bound calls to opus,
  # joined with `join all`. Each advisor sees the question, the
  # current chair feedback (empty on the first pass, populated on
  # later passes), and its own role's default prompt.
  state advise
    actor model opus
    group advisors
    reads question, chair-feedback
    writes advisor-outputs messages
    join all
    on complete => anonymize
    on error => stop
    on timeout => stop

  # Mechanical anonymization. Reads the named advisor outputs and
  # writes a copy with role identifiers stripped. Shell command
  # because there is no LLM judgment involved; this exercises the
  # shell actor backing without needing the workspace profile.
  # See findings (F8) and (F9).
  state anonymize
    actor shell
    command "scripts/anonymize-messages.sh"
    reads advisor-outputs
    writes anonymized-advisor-outputs messages
    on pass => peer-review
    on fail => stop
    on error => stop
    on timeout => stop

  # Parallel peer review. Five role-bound calls to opus, each
  # reading the anonymized advisor outputs. Reviewers do not see
  # advisor role identities.
  state peer-review
    actor model opus
    group reviewers
    reads question, anonymized-advisor-outputs
    writes peer-reviews messages
    join all
    on complete => synthesize
    on error => stop
    on timeout => stop

  # Chair synthesis. Single LLM call backed by a verdict schema.
  # The chair sees the named advisor outputs (not anonymized; the
  # chair's job is full synthesis with attribution) and the
  # anonymized peer reviews. On `revise`, the chair writes a new
  # version of `chair-feedback` so the next advisor pass has
  # something to react to.
  state synthesize
    actor model opus
    role chair
    reads question, advisor-outputs, peer-reviews
    schema council-verdict-schema
    writes verdict json
    writes chair-feedback text
    on approve => done
    on revise => advise          # round-trip: re-run advisors with chair feedback
    on reject => stop
    on error => stop
    on timeout => stop
```

### Referenced but not created

Files referenced as prompt sources, schema sources, or shell commands
are not part of this sketch. They would live at:

- `prompts/advisor-contrarian.md`,
  `prompts/advisor-first-principles.md`,
  `prompts/advisor-expansionist.md`,
  `prompts/advisor-outsider.md`,
  `prompts/advisor-executor.md`
- `prompts/reviewer-consistency.md`,
  `prompts/reviewer-evidence.md`,
  `prompts/reviewer-feasibility.md`,
  `prompts/reviewer-coherence.md`,
  `prompts/reviewer-bias.md`
- `prompts/chair.md`
- `schemas/council-verdict.schema.json` (verdict enum:
  `approve`, `revise`, `reject`; plus required structured fields
  the chair must populate, including a `feedback` text field that
  the chair writes into `chair-feedback`)
- `scripts/anonymize-messages.sh` (reads `advisor-outputs`,
  strips role identifiers, writes `anonymized-advisor-outputs`)

## Primitives exercised

- **External inputs other than `topic`**: `question` and
  `decision_id`. The latter is for log correlation only and is not
  read by any state. This exposes the question of whether external
  inputs that are never read by any state are valid; the design
  document does not address it. Recorded as (A8).
- **Single model backing many roles**: `model opus` is the only model
  declared. It backs all five advisor roles, all five reviewer
  roles, and the chair. This is the role/model orthogonality
  the design document specifies.
- **Two role groups, both `kind roles`**: `advisors` and `reviewers`.
  Each state that invokes a role group declares the model
  (`actor model opus`). The two groups have non-overlapping member
  sets.
- **`join all`**: declared on both `advise` and `peer-review`.
  Required by validation rule 8.
- **Multi-actor state writing a single `messages` artifact**: the
  advisor invocations all contribute to `advisor-outputs`. The
  `messages` artifact type is append-only per the design, so each
  advisor's contribution is one entry. The exact mechanism for "a
  multi-actor state writes per-actor entries into one append-only
  messages artifact under join all" is not spelled out in the
  design document; recorded as (F10).
- **Schema-backed LLM state**: the `synthesize` state declares
  `schema council-verdict-schema` and has transitions for every
  verdict enum value (`approve`, `revise`, `reject`) plus the
  always-allowed `error` and `timeout`. Validation rule 10.
- **Schema as a first-class artifact**: `council-verdict-schema` is
  declared at the workflow level as an artifact of type `schema`
  with a `source file` reference. The design document mentions
  `schema` as an artifact type but does not show the declaration
  syntax for schemas backed by external files. Recorded as (F11).
- **Shell actor for mechanical transform**: the `anonymize` state
  uses `actor shell` with an inline `command` declaration. Outcomes
  are `pass`, `fail`, `error`, `timeout`, `cancelled` per the design.
  This exercises the shell backing without invoking the code or
  workspace profiles.
- **Loop back from chair to advisors carrying information**:
  `on revise => advise`, with the chair writing `chair-feedback`
  and `advise` reading it. Same shape as the loop-progress
  pattern from Test 1 (A7): an upstream state writes the artifact
  the downstream loop target needs, versioning resolves the rest.
  Recorded as (A14).
- **Artifact with an initial value**: `chair-feedback text` is
  declared with `initial ""`. This is new minimum-viable syntax;
  see clarification (F15).
- **Reads on every nontrivial state**: applied uniformly. The
  `synthesize` state reads `question, advisor-outputs, peer-reviews`
  but does not read `anonymized-advisor-outputs`, on the principle
  that the chair's job is attributed synthesis. The reviewers do
  the opposite. This separation is the entire mechanism by which
  anonymization is enforced in v0.

## What felt awkward

(A8) **External input declared but unused.** `decision_id` is meant
for log correlation; the runner records it in every log line but
no state reads it. The design document does not say whether this
is permitted. Two readings:
1. External inputs are workflow-level metadata; not all of them
   need to be read by states. The runner uses them for run-record
   identity and logging.
2. External inputs are dataflow inputs; if no state reads one, it
   is dead and the validator should reject the workflow.
The first reading matches how external inputs like `task` are used
in the design document's mcloop examples (some `task` fields are
referenced in guards, others may be referenced only in logs). I
went with reading 1. Recommendation for the grammar phase: external
inputs not referenced by any state or guard are permitted but
generate a lint warning unless explicitly marked
`for_logging` / `metadata` / equivalent. Not introducing syntax
for that here; just flagging.

(A9) **Cycle without per-state attempts guard.** The
`synthesize => advise` transition creates a cycle. Validation rule
11 recommends adding `attempts.<state> < N` guards on cycle exits.
I deliberately omitted this on `synthesize` because the chair has
genuine discretion: it may approve on the first pass, on the
fourth, or never. `max_total_steps 30` provides the safety net.
This is the case the design document's lint warning is meant to
allow with explicit author intent, but the design does not
describe how an author *expresses* intent ("I considered the
guard and chose not to add it"). The lint warning would fire
here. Possible future syntax: `# orchestra: cycle-guard-deferred
synthesize => advise` or a workflow-level
`acknowledged_unbounded_cycles` declaration. Not introducing it;
flagging.

(A10) **Schema reference at the chair state.** The state declares
`schema council-verdict-schema`. The design document specifies
that schema-backed LLM states have transitions for every verdict
in the schema's verdict enum, but does not show how the state
declares which schema to use. I treated `schema <artifact-name>`
as a state-level binding that references a workflow-level
schema artifact. This is the obvious shape but is not in the
design. Recorded as (F11) below.

(A11) **Anonymization is a state, not a property.** The reviewer
group reads `anonymized-advisor-outputs`, not `advisor-outputs`.
For this to mean what it says, a state must have produced the
anonymized version. The shell-actor `anonymize` state does this
mechanically. The fact that anonymization requires an explicit
state is a direct consequence of v0 not having visibility
primitives. Not a finding against v0 (the design document is
explicit that visibility is v1); it is a confirmation that the
v0 workflow-structural workaround is expressible. The friction
is that the workflow author has to remember to read the
anonymized artifact in the right place. A v1 visibility
primitive would let `advisor-outputs` carry a `visible-to` clause
and remove the explicit state. Recorded as (F8).

(A12) **One model declaration vs many.** The sketch declares
`model opus` once and uses it at three states. This is the
intended pattern. It does mean that swapping in a different
model for, say, the chair (so reviewers and advisors stay on
opus but the chair runs on a more capable model) requires
declaring a second model and changing the chair state's
`actor model` line. There is no facility for parameterizing the
backing model of a role group at the invocation site against an
external knob. Workflow files are static. This is consistent with
the design's static-group rule but worth noting that "swap the
chair model" requires editing the workflow source, not a runtime
flag. Calibration note, not a finding.

(A13) **`reads` on `advise`.** The state reads `question` and
`chair-feedback`. Each of the five advisor invocations sees both
artifacts and its role's default prompt; nothing else. This works.
But it raises a question for `peer-review`: each reviewer reads
`question, anonymized-advisor-outputs`, but is the entire
`anonymized-advisor-outputs` `messages` artifact passed to each
reviewer, or is each reviewer somehow given a slice? The design
document is silent. The natural reading is that the entire
artifact is passed to each invocation; reviewers reviewing the
council see all five anonymized outputs. That is what I assumed.
Recorded as (F12).

(A14) **Chair revision feedback as an explicit artifact.** The
original sketch had `on revise => advise` but the `advise` state
read only `question`, so the second advisor pass had no reason
to differ from the first. Same loop-progress bug as Test 1 (A7):
a state's loop-back target did not read the artifact the upstream
state would need to write to make the loop meaningful. Fix: the
chair writes `chair-feedback`; the advisor state reads it.
Versioning handles the rest, exactly as in Test 1.

The pattern is now identical across both tests: when a workflow
loops back to an earlier state, some artifact written by the
state issuing the loop-back must be read by the loop target.
This is not a primitive; it is a consequence of the existing
versioned-artifact rule combined with the discipline that durable
data flows through named artifacts. Worth calling out as a
recurring pattern in design-document prose, not as new syntax.

The first-pass case (no chair has run yet, so `chair-feedback`
does not exist) is handled by initializing the artifact to empty
text. See (F15) for the new minimum-viable `initial` syntax this
required.

## What the sketch forced me to clarify

(F8) **Anonymization is workflow-structural in v0.** The design
document explicitly defers visibility primitives to v1 and says
the council acid test "will use a workflow structure where
visibility is enforced by which artifacts each state's input
includes, not by per-actor visibility rules." This sketch
implements that: a separate state produces an
`anonymized-advisor-outputs` artifact; reviewers read the
anonymized version; the chair reads the named version. The
mechanism works but requires an explicit state. Confirmed and
applied.

(F9) **Shell actor invocation syntax.** The design document names
shell as an actor backing with outcomes `pass`, `fail`, `error`,
`timeout`, `cancelled` and says shell-backed actors run shell
commands, but does not show the state-level syntax. Under the
unreadability exception I used:

```
state anonymize
  actor shell
  command "scripts/anonymize-messages.sh"
  reads advisor-outputs
  writes anonymized-advisor-outputs messages
  on pass => peer-review
  on fail => stop
  ...
```

The `command` line is a string with the shell command. The
discipline of `reads` and `writes` continues to apply: the shell
script is responsible for actually reading the artifact backing
file and writing the output artifact backing file. The runner
provides the artifacts to the script via mechanism the design
document does not specify (environment variables, command-line
arguments, stdin, files at known paths). Recorded as a real gap
the runner spec must close. Minimum viable state-level syntax:
adopt or replace.

(F10) **Multi-actor state writing a single `messages` artifact.**
The design document says the `messages` artifact type is
append-only and that a multi-actor state declares a join policy.
It does not say what it means semantically for five parallel actors
under `join all` to all "write" to one `messages` artifact. The
natural semantics: each successful actor's output becomes one entry
in the artifact; entries are ordered by the join policy's
declaration order or by completion order. This sketch assumes
declaration order (the order the role group's members are listed)
because that is reproducible across runs. Recommendation for the
grammar/runner spec: under `join all` writing to a `messages`
artifact, entries appear in declaration order. Under
`join any` or `join quorum`, entries appear in completion order
because some actors may not contribute. Recorded.

(F11) **Schema artifact declaration and binding.** The design
document includes `schema` in its list of core artifact types but
does not show how a workflow declares one. Two questions:
(1) where does the schema's content come from? (2) how does a
state bind to it?

For (1), the obvious source is an external file containing JSON
Schema. The sketch uses:

```
artifact council-verdict-schema schema
  source file schemas/council-verdict.schema.json
```

The `source file` qualifier on an artifact declaration is new.
The design document allows for artifacts to be loaded from
external sources but does not specify syntax. Justified under the
unreadability exception: a schema artifact whose content is not
defined anywhere in the workflow is meaningless.

For (2), the state binding is `schema <artifact-name>`. This
references the workflow-level schema artifact. The runner
validates the actor's structured output against the schema at
runtime; the verdict enum extracted from the schema drives
which `on <verdict>` transitions the state must declare
(validation rule 10).

Both pieces of syntax are minimum viable. Adopt or replace.

(F12) **Per-actor input scope under role groups.** When a state
invokes a role group with `reads question,
anonymized-advisor-outputs`, the design document does not specify
whether each actor in the group sees the full set of artifacts in
`reads`, or some per-actor slice. The natural reading is "every
actor in the group sees every artifact in reads." This is what the
sketch assumes. The alternative (per-actor slicing) would require
syntax to express the slicing, which the design does not have.
Recorded; recommendation for the grammar/runner spec: per-actor
input scope under role groups is the full `reads` set, identical
across all members of the group.

(F13) **No per-actor results addressable in this sketch.** The
design document offers `<statename>.outputs` as the aggregate
list of successful results and `<statename>.<actor>.<field>` for
specific actor results under `join all`. The sketch does not use
either: instead, the multi-actor state writes a single named
artifact (`advisor-outputs` of type `messages`) and downstream
states read the artifact, not the per-state per-actor reference.
This is the design's stated discipline ("avoid `<state>.output`
for durable data; use named artifacts"). The per-actor result
reference syntax may still be useful for guard expressions or
control logic, but the data plane stays in named artifacts.
Confirmed and applied.

(F14) **Loop target is a multi-actor state.** The
`synthesize => advise` transition jumps back to the multi-actor
advisor state. On re-entry, the advisor group runs again,
producing a new version of `advisor-outputs`. The
`anonymize` and `peer-review` states then run again, producing
new versions of `anonymized-advisor-outputs` and `peer-reviews`.
The chair re-runs against the new versions. This is the same
versioned-artifact mechanism Test 1 used (artifacts written more
than once across iterations resolve to the latest version). The
design document's versioning rule covers it without modification.
Confirmed.

(F15) **Initial value on an artifact declaration.** The fix to
(A14) requires `chair-feedback` to exist on the first advisor
pass even though no chair has run yet. Two possible mechanisms:
(a) optional reads (a state's `reads` clause may name an
artifact whose latest version may be missing, and the runner
passes a typed null), or (b) initial values (the artifact
declaration provides a default value that exists at workflow
start and is treated as version 0).

Optional reads is a real new mechanism; it requires the runner
to distinguish "artifact exists with empty content" from
"artifact does not exist," and it requires every reading state's
prompt template or actor logic to handle the null case. Initial
values are simpler: the artifact behaves uniformly across all
reads, and the workflow author controls the initial content.

Under the unreadability exception I introduced:

```
artifact chair-feedback text
  initial ""
```

The `initial` clause sets the value of the artifact at workflow
start. For `text`, the literal is a string. For other types the
syntax would extend naturally (`initial []` for `messages`,
`initial {}` for `json`, etc.). Validation rule: an artifact with
no `initial` clause does not exist until first written; reading
it before first write is an error. An artifact with an `initial`
clause is treated as written-with-the-initial-value at workflow
start; the initial value is version 0 and any subsequent write
produces version 1, version 2, and so on.

This is the second loop-progress test (Test 2 (A14) is the same
shape as Test 1 (A7)) where the language needed something for
the first-pass case. Test 1 did not need it because every artifact
was written before being read in the natural state ordering. Test
2 needed it because the chair feedback flows backward across the
loop. Worth noting that this pattern will recur whenever a loop
target reads an artifact written by a state later in the
forward-graph. Adopt or replace.
