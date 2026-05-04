# Iteration and Implementation Workflows

Date: 2026-05-04
Status: Design draft for review

This document specifies three new workflow patterns for orchestra:

1. **Parallel Thinking** — N actors answer in parallel, no synthesizer.
2. **Iterate Until Acceptable** — Reviewer-Judge loop on a fixed proposal.
3. **Propose-Review-Judge-Implement** — Controller-driven loop with an
   implementer that mutates state inside the cycle.

The three are listed in order of increasing complexity. Each builds
mechanisms the next one uses.

## 1. Parallel Thinking

### Pattern

A framer reformulates the input. N panelists answer the framed
question in parallel. The workflow returns the N answers as
separate artifacts. No synthesizer.

### Why this is distinct from Council and Anonymous Reviewers

Both Council and Anonymous Reviewers end with a synthesizer state
that produces a single output. Parallel Thinking deliberately does
not synthesize: the consumer of the workflow gets the panelists'
answers and decides what to do with them. Useful when the goal is to
expose disagreement rather than resolve it.

### Roles

- **Framer** — reformulates the input into a question every panelist
  receives.
- **Panelists 1..N** — answer the framed question independently. N is
  fixed at 5 in the v0 implementation, matching the Council and
  Anonymous Reviewers shape.

### Stopping criteria

The workflow terminates when all panelists have produced output.
A failed panelist routes the workflow to `stop` rather than `done`,
matching the Council contract: a partial panel is not a meaningful
result.

### Output contract

The workflow's terminal state writes N artifacts:
`panelist_1_output`, `panelist_2_output`, ..., `panelist_N_output`,
each text. Consumers read these by name. There is no aggregated
output artifact.

### .orc shape

```
spec 0.1

workflow parallel_thinking

  external_input query text
  external_input history text

  max_total_steps 30

  model m_framer
  model m_panelist_1
  model m_panelist_2
  model m_panelist_3
  model m_panelist_4
  model m_panelist_5

  artifact framed_question text
  artifact panelist_1_output text
  artifact panelist_2_output text
  artifact panelist_3_output text
  artifact panelist_4_output text
  artifact panelist_5_output text
  artifact finish_marker text

  role framer
    prompt template "templates/parallel_thinking_framer.md" with query, history

  role panelist
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  state frame
    actor model m_framer
    role framer
    reads query, history
    writes framed_question text
    on complete fan_out [p1, p2, p3, p4, p5] join finish on error stop
    on error => stop
    on timeout => stop

  state p1
    actor model m_panelist_1
    role panelist
    reads framed_question
    writes panelist_1_output text
    on complete => done
    on error => stop
    on timeout => stop

  # p2 through p5 follow the same shape

  state finish
    actor transform finish_panel
    reads panelist_1_output, panelist_2_output, panelist_3_output, panelist_4_output, panelist_5_output
    writes finish_marker text
    on complete => done
    on error => stop
    on timeout => stop
```

The `finish` state is a transform-actor join, not a model
invocation. It uses a built-in `finish_panel` transform that reads
the N panelist outputs (pinning them in the must-reach analysis)
and writes a trivial `finish_marker` text artifact. The transform
performs no model work and produces no synthesis: its only purpose
is to provide a join target for the fan_out and a clean terminal
state.

The `finish_panel` transform is added to the built-in transform
registry as part of this workflow's implementation (alongside the
existing `anonymize_outputs` used by `ask_anonymous_reviewers`).
Making the join a transform rather than reusing `m_framer` with
the framer role avoids a real model call with a wrong prompt
contract.

### Open question

Whether N should be configurable per-run via external_input, or
fixed at 5 in the workflow file. The Council and Anonymous Reviewers
workflows fix it at 5; we recommend the same here for consistency.
A v1 extension can add configurable N once orchestra's grammar
admits dynamic fan-out width.

## 2. Iterate Until Acceptable

### Pattern

A proposer produces an initial draft. A reviewer-judge loop runs
until the judge accepts or the iteration cap is reached. The
proposal is fixed across iterations; only the review iterates.

The loop is the README's "iterate until acceptable" diagram, made
concrete.

### Roles

- **Proposer** — produces the initial artifact once. Not re-invoked.
- **Reviewer** — examines the proposal and current judge feedback,
  produces a critique.
- **Judge** — evaluates the reviewer's critique against the original
  proposal, produces a verdict.

### Distinct-actor constraint and where it is checked

Proposer and Reviewer must resolve to distinct actors at workflow
load time. Judge typically resolves to the same actor as Proposer.
The working principle of independent review is that the reviewer's
training data and blind spots differ from the proposer's; binding
both to the same actor collapses the architecture to single-model
self-critique.

The grammar cannot enforce this. Role identifiers in a `.orc` file
are bound to model and adapter values through the user's
orchestra config, which the runtime reads at workflow start. The
check happens at config-resolution time, not at parse time.

Actor identity for this check is the tuple `(adapter, model)`.
The role binding's `parameters` map is excluded from the identity.
Two roles bound to the same `adapter` and `model` are the same
actor regardless of differing parameters such as system prompts,
temperatures, or other adapter-specific knobs. The constraint is
about training-data independence, not prompt independence.

The check is implemented as a workflow-specific config validation
rule: `iterate_until_acceptable` rejects a config in which
`proposer` and `reviewer` resolve to the same actor identity. The
check runs after the merged orchestra config is loaded and before
the first state is invoked. Failure aborts the workflow with a
clear error naming the colliding roles.

### Stopping criteria

Schema-backed verdict from the judge:

```json
{
  "decision": "accept" | "iterate",
  "feedback": "..."
}
```

- `accept`: workflow terminates `done`. The proposal artifact is the
  final output.
- `iterate`: the reviewer runs again with the judge's feedback as
  additional input.

The `iterate` branch is bounded by `attempts.judge < N` where N is
the iteration cap. When the cap is reached, the workflow terminates
`done` with the proposal as the final output. This is "accept on
cap": the workflow does not refuse on iteration exhaustion.

The choice of accept-on-cap rather than refuse-on-cap is deliberate:
the proposal is by definition usable (the proposer produced it
without error), and the iteration loop is refining the *evaluation*,
not the proposal itself. Hitting the cap means the judge is not
converging, which is a workflow-author tuning issue, not a result
unfit for return.

### .orc shape

```
spec 0.1

workflow iterate_until_acceptable

  external_input query text
  external_input history text

  max_total_steps 60

  model m_proposer
  model m_reviewer
  model m_judge

  artifact proposal text
  artifact review_output text
  artifact judge_verdict json
    schema "schemas/iterate_judge_verdict.json"
    extract feedback => judge_feedback text
  artifact judge_feedback text initial ""

  role proposer
    prompt template "templates/iterate_proposer.md" with query, history

  role reviewer
    prompt template "templates/iterate_reviewer.md" with query, proposal, judge_feedback

  role judge
    prompt template "templates/iterate_judge.md" with query, proposal, review_output

  state propose
    actor model m_proposer
    role proposer
    reads query, history
    writes proposal text
    on complete => review
    on error => stop
    on timeout => stop

  state review
    actor model m_reviewer
    role reviewer
    reads query, proposal, judge_feedback
    writes review_output text
    on complete => judge
    on error => stop
    on timeout => stop

  state judge
    actor model m_judge
    role judge
    reads query, proposal, review_output
    writes judge_verdict json
    writes judge_feedback text
    on accept => done
    on iterate when attempts.judge < 6 => review
    on iterate => done
    on error => stop
    on timeout => stop
```

Notes:

- `judge_feedback` has `initial ""` so the first reviewer pass can
  read it before the judge has run.
- The `schema` qualifier on `judge_verdict` references a JSON
  Schema file. The schema-verdict runtime mechanism (separate
  commit; see `design/schema-verdict-runtime-support.md`) parses
  the model's output, validates against the schema, populates the
  artifact, and emits the schema's `decision` field as the
  transition outcome.
- The schema's `decision` enum is `["accept", "iterate"]`. The
  `on accept` and `on iterate` outcomes correspond to those enum
  values. This match is verified at workflow load time.
- The fallback `on iterate => done` (without guard) catches the
  cap-reached case: when `attempts.judge < 6` is false, this
  transition fires and accepts.

### Open questions

1. The iteration cap (6) is hardcoded. Whether to expose as
   external_input is a design call.
2. The accept-on-cap default. Some users may prefer refuse-on-cap.
   This could be a workflow variant rather than a configuration knob.

## 3. Propose-Review-Judge-Implement

### Pattern

The pattern automates the manual audit cycle we have been running:
Codex audits, Claude Desktop judges findings, Code implements fixes,
loop until the judge accepts.

The judge is the controller. It invokes the reviewer, consults the
proposer for what to do with the findings, invokes the implementer
to apply fixes when needed, and decides whether the work is
complete.

### Roles

- **Proposer** — frames what should be reviewed. Consulted by the
  judge on each iteration to decide what to do with the reviewer's
  findings. May be re-invoked to re-frame.
- **Reviewer** — independent critic. Examines the current state of
  the work and produces findings.
- **Judge** — controller. Invokes the others, decides what to do
  with their outputs, terminates when the work is acceptable.
- **Implementer** — applies fixes. The only role that mutates the
  workspace.

### Distinct-actor constraint and where it is checked

Proposer, Reviewer, and Implementer must resolve to distinct actors
at workflow load time. Judge typically resolves to the same actor
as Proposer.

Same mechanism as Iterate Until Acceptable: the grammar admits any
binding, the check happens at config-resolution time, the actor
identity is the `(adapter, model)` tuple. The PRJI workflow's
config validation rule rejects a config in which any two of
`{proposer, reviewer, implementer}` resolve to the same actor.

### Mutation contract and where it is checked

The Implementer is the only role that mutates the workspace. The
other three roles are text-only.

The `agent` actor backing alone does not guarantee mutation: an
agent adapter is free to be read-only by configuration. The
mutation contract is a binding-time check on adapter
self-classification.

The adapter contract (`orchestra/adapters/base.py`) gains a
required `workspace_mutation` key in the dict returned by
`describe()`, with values `"mutating"` or `"text_only"`. The
shipped adapters declare:

- `claude_code_agent` => `"mutating"`
- `codex_agent` => `"mutating"`
- `claude_code_text` => `"text_only"`
- `codex_text` => `"text_only"`

Mock adapters used in tests declare values appropriate to their
behavior.

The PRJI workflow's config validation rule reads
`describe()["workspace_mutation"]` for each role's resolved
adapter and rejects:

- Implementer bound to a `"text_only"` adapter.
- Proposer, Reviewer, or Judge bound to a `"mutating"` adapter.

A new adapter is self-classifying via its `describe()` output and
requires no validator edit.

A future extension may model the workspace as a declared mutable
artifact with `mode readwrite` on the implementer state and `mode
readonly` on the others, using the existing versioned-workspace
profile machinery. v0 uses the self-classifying adapter check,
which is simpler and sufficient.

### Stopping criteria

Schema-backed verdict from the judge, four branches:

```json
{
  "decision": "accept" | "implement" | "rereview" | "reframe",
  "feedback": "...",
  "fix_instructions": "..."
}
```

- `accept`: workflow terminates `done`. Current workspace state is
  the final output.
- `implement`: invoke the implementer with `fix_instructions`, then
  loop back to the reviewer to verify the fix.
- `rereview`: invoke the reviewer again without an intervening
  implementation step. Used when the judge wants a deeper look at
  something the reviewer raised but did not fully analyze.
- `reframe`: invoke the proposer with the judge's feedback to
  re-frame what should be reviewed, then loop to the reviewer.

### Stopping criteria, expanded

Three counters bound the loop:

- `attempts.judge < 30` — total judge invocations. The judge's
  outcome is what drives every loop continuation, so this cap is
  the primary safety net. Exhaustion routes to `stop` (refuse-on-
  cap, not accept-on-cap: reaching this cap means the judge could
  not converge).
- `attempts.implement < 20` — total implementer invocations.
  Independent of judge cap; bounds runaway fix loops.
- `attempts.propose < 6` — total proposer invocations including the
  initial proposal. The reframe path reuses the `propose` state, so
  the counter is `attempts.propose`, not `attempts.reframe`. The
  initial proposal counts as attempt 1; up to 5 reframes are
  permitted.

The judge cap must guard *every* nonterminal branch out of the
judge state, because any of them re-invokes the judge after
the routed state runs. Guarding only one branch (rereview) leaves
the other branches free to re-invoke the judge past its cap. The
.orc shape below applies the judge cap on every nonterminal
branch.

### Why three caps and not one

A single cap can hide pathological loops. If only `attempts.judge`
is bounded, the implementer or proposer could be invoked many
times per judge invocation through some combination of branching.
Bounding each independently prevents any single role from being
over-invoked even if the judge's logic is buggy.

### .orc shape

```
spec 0.1

workflow propose_review_judge_implement

  external_input task text
  external_input project_dir text
  external_input history text

  max_total_steps 200

  model m_proposer
  model m_reviewer
  model m_judge
  model m_implementer

  agent implementer_agent
    model m_implementer
    adapter claude_code_agent
    context_policy fresh

  artifact framing text
  artifact review_output text
  artifact judge_verdict json
    schema "schemas/prji_judge_verdict.json"
    extract feedback => judge_feedback text
    extract fix_instructions => fix_instructions text
  artifact judge_feedback text initial ""
  artifact fix_instructions text initial ""
  artifact implementer_output text initial ""

  role proposer
    prompt template "templates/prji_proposer.md" with task, history, judge_feedback

  role reviewer
    prompt template "templates/prji_reviewer.md" with task, framing, judge_feedback, implementer_output

  role judge
    prompt template "templates/prji_judge.md" with task, framing, review_output, implementer_output

  role implementer
    prompt template "templates/prji_implementer.md" with fix_instructions, project_dir

  state propose
    actor model m_proposer
    role proposer
    reads task, history, judge_feedback
    writes framing text
    on complete => review
    on error => stop
    on timeout => stop

  state review
    actor model m_reviewer
    role reviewer
    reads task, framing, judge_feedback, implementer_output
    writes review_output text
    on complete => judge
    on error => stop
    on timeout => stop

  state judge
    actor model m_judge
    role judge
    reads task, framing, review_output, implementer_output
    writes judge_verdict json
    writes judge_feedback text
    writes fix_instructions text
    on accept => done
    on implement when attempts.judge < 30 and attempts.implement < 20 => implement
    on implement => stop
    on rereview when attempts.judge < 30 => review
    on rereview => stop
    on reframe when attempts.judge < 30 and attempts.propose < 6 => propose
    on reframe => stop
    on error => stop
    on timeout => stop

  state implement
    actor agent implementer_agent
    role implementer
    reads fix_instructions, project_dir
    writes implementer_output text
    on complete => review
    on error => stop
    on timeout => stop
```

### Mapping to our auditing cycle

The pattern matches the audit cycle we have been running by hand:

| Workflow role | Our actor                  |
|---------------|----------------------------|
| Proposer      | Claude Desktop (the audit prompt author) |
| Reviewer      | Codex                      |
| Judge         | Claude Desktop (the triage author)       |
| Implementer   | Code (Claude Code via relay)             |

The judge invocations match the moments in this conversation where I
classified Codex findings, decided which were real, and either
accepted, asked for re-review, or sent fix instructions to Code.

### Open questions

1. The relay mechanism we have been using is Desktop ↔ Code. The
   workflow's invocation model is straight orchestra — every state's
   actor is invoked by the orchestra runtime, not via a manual
   relay step. This means the runtime needs adapters that can
   actually drive Codex (already exists: `codex_text`,
   `codex_agent`) and Claude Code (already exists:
   `claude_code_text`, `claude_code_agent`). The workflow as written
   above uses these. But the relay-mediated coordination we use
   manually has properties this workflow doesn't replicate: the
   ability for a human to interject at any point, the ability for
   one actor to ask the human a question, the ability to pause for
   human approval. Whether the workflow needs human-interaction
   states (the existing `actor human` backing covers this) is a
   design call. Recommendation: ship the fully automated version
   first, add human-interaction gates as a v1 extension if needed.
2. The implementer's output (`implementer_output`) is currently a
   text artifact, but real implementer output is a workspace
   mutation plus a summary. The orchestra `claude_code_agent`
   adapter already produces this shape. Whether the workflow
   should also commit the workspace state or leave that to the
   implementer is a contract question.
3. The reviewer reads `implementer_output` so it knows what was
   changed in the previous iteration. This is the mechanism that
   makes "iterate the review" meaningful: the second review is
   reviewing the post-fix state, not the original. Without this
   read, the reviewer would re-find the same issues every iteration.

## Cross-cutting concerns

### Schema artifact for verdicts

Two of the three workflows (Iterate, PRJI) use schema-backed json
verdicts. The schema-verdict runtime mechanism is a separate
commit specified in
`design/schema-verdict-runtime-support.md`; this document assumes
that mechanism is in place.

Under the new mechanism, schemas are declared as an artifact
qualifier (`artifact <name> json schema "<path>"`), not as a
state-level clause. Both verdict artifacts also declare `extract`
clauses that promote the `feedback` (and for PRJI also
`fix_instructions`) schema fields into separately declared text
artifacts. Extraction runs in the same transaction as the JSON
artifact write. The schemas needed:

- `iterate_judge_verdict.json`: two-branch decision
  (`accept`, `iterate`) with required `feedback`. The reviewer
  reads `judge_feedback` on every `iterate` branch, so leaving
  `feedback` optional would expose the reviewer to stale
  guidance from a prior iteration. The schema-verdict validator
  rejects this configuration.
- `prji_judge_verdict.json`: four-branch decision
  (`accept`, `implement`, `rereview`, `reframe`) with required
  `feedback` and required `fix_instructions`. Both fields are
  extracted into separate text artifacts read by downstream
  states under nonterminal branches, so both must be in the
  schema's `required` list per the same validator rule.

Both schema files live in `orchestra/workflows/schemas/` and are
committed alongside the workflow files.

### Fan-out attempts and the snapshot mechanism

Iterate and PRJI use `attempts.<state>` counters in transition
guards. These counters are part of the orchestra runtime state and
are correctly snapshotted under fan-out per the pass-6 fix. The
new workflows do not use fan-out (the only fan-out is in Parallel
Thinking, which does not use attempts counters in guards). So the
snapshot mechanism is not exercised by Iterate or PRJI.

### Verb wiring

Each workflow needs a verb in `api.py` so it is invocable as
`orchestra.<verb>`. Suggested verbs:

- `parallel_thinking` → `ask_parallel_thinking`
- `iterate_until_acceptable` → `iterate_until_acceptable`
- `propose_review_judge_implement` → `audit_and_fix` or `prji`

The verbs are the production interface; `orchestra run` is
restricted to mock/human/shell per pass-3 #5.

### Per-iteration context plumbing

Iterate and PRJI invoke the same role multiple times across a
loop. Each invocation is a fresh subprocess with no in-process
memory of prior invocations. The information continuity that the
manual audit workflow gets from long-lived chat sessions (where
the reviewer remembers what it said two iterations ago, the
judge recalls its prior verdicts, etc.) is not free here. It
must be assembled by the runtime and passed in via the prompt
template.

The current designs read only the most recent value of each
artifact. They do not provide access to prior values of the
role's own outputs across iterations. A reviewer in iteration 3
cannot see what it said in iterations 1 and 2 unless that
content is plumbed in by some explicit mechanism.

Three options for the implementation pass:

1. Accumulating artifacts. A new artifact qualifier (e.g.
   `mode append`) that turns `writes review_output text` into
   an append rather than an overwrite. Adds grammar surface and
   changes the artifact model.
2. Auxiliary history artifacts. For any artifact written under a
   loop, the runtime maintains a sibling `<artifact>_history`
   that concatenates prior values with separators. No grammar
   change, adds runtime bookkeeping. Templates read the history
   artifact alongside the current value.
3. Template-level expedient. Leave artifacts overwritable. Prompt
   templates carry the burden of summarizing prior iterations
   inside the text they emit, so that the next iteration's
   reader sees a self-summarized history written into the prior
   value. Cheapest to implement, costliest in template
   complexity, weakest correctness guarantees.

Logged here as a design concern. The choice between these is
deferred to the template-design pass. Not a workflow-correctness
issue (every state still terminates and routes correctly) but a
quality-parity gap against the manual audit workflow that the
PRJI pattern is intended to replace.

### Templates

Each workflow needs prompt templates under
`orchestra/workflows/templates/`. The templates are not specified in
this design doc; they will need their own design pass during
implementation.

Notable template requirements:

- The judge templates must produce valid JSON matching the
  corresponding schema. Schema-backed model states enforce this
  at parse time.
- The reviewer template in PRJI must read `implementer_output` and
  acknowledge the previous fix attempt. Without this, the second
  review iteration is blind to the fix.
- The proposer template in PRJI must read `judge_feedback` so the
  re-framing path actually uses the judge's reasoning.

## Implementation order

1. **Parallel Thinking** first. Smallest delta from existing
   workflows. Reuses Council's framer pattern. No iteration, no
   schema verdict.
2. **Iterate Until Acceptable** second. Introduces the
   schema-backed verdict and the bounded iteration loop.
   Establishes the patterns PRJI extends.
3. **PRJI** third. Builds on Iterate's verdict and loop machinery,
   adds the implementer integration and three-counter bounding.

Each is its own commit. Each ships with templates, a schema (where
applicable), a verb, and tests.

## Things deliberately out of scope

- Configurable N for Parallel Thinking. v0 fixes at 5.
- Human-interaction states in PRJI. v0 is fully automated.
- The auto-relay between Desktop and Code that our manual workflow
  uses. PRJI replaces that with direct adapter invocation; the
  manual relay remains available for cases where it is preferred.
- A `bug_verify` workflow. Already pending separately.
- The resume-vs-retry distinction logged in IDEAS.md. Independent.
