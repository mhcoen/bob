# Acid Test 1: Design Loop

## Goal

The smallest non-code probe of the Orchestra design. The workflow
expresses the author's manual cross-LLM design loop:

1. The user gives a topic.
2. A Claude-backed designer agent drafts a response.
3. A GPT-backed critic agent reviews the draft.
4. The Claude-backed designer agent (same agent, different role)
   reflects on the critique.
5. The user is asked, via a human choice gate, whether to continue
   iterating, accept the current state, or stop.
6. On `continue`, the loop returns to the critique step. On `accept`,
   the workflow ends successfully. On `stop`, the workflow ends as
   failed.

This sketch deliberately avoids shell actors, workspaces, verdict
schemas, role groups, and parallel join semantics. Those are exercised
in Tests 2 and 3. Test 1's job is to put pressure on:

- Persistent agents with conversation continuity.
- The same agent invoked in two different roles within one workflow
  run, with the per-agent (not per-role) history scoping the design
  document committed to.
- Prompt sources, exercising both `prompt file` and
  `prompt template ... with ...` in the same workflow.
- Distinct named artifacts as the data substrate, with no fallback
  to `<state>.output` for durable content.
- A human-backed actor used as a choice gate, including its outcome
  vocabulary.
- Workflow-level safety nets: `max_total_steps`, an explicit cycle
  exit guard tied to `attempts.<state>`.
- External inputs (a single `topic` of type `text`).

## Workflow sketch

### File: `design-loop.orc`

```
spec 0.1

workflow design-loop

  external_input topic text

  max_total_steps 40

  # Models. Short IDs only. Provider routing lives in env vars,
  # not in the workflow file.
  model opus
  model gpt

  # Roles. Each role has a default prompt source. State-level
  # prompts override the default for that invocation; they do not
  # mutate the role.
  role designer
    prompt file prompts/designer.md

  role critic
    prompt file prompts/critic.md

  role reflector
    prompt file prompts/reflector.md

  # Persistent agents. The Claude-backed agent is invoked in two
  # roles (designer, reflector) and keeps one conversation thread
  # across both. The GPT-backed agent has its own thread.
  agent claude-designer
    model opus
    adapter api_runner_managed
    context_policy default

  agent gpt-critic
    model gpt
    adapter api_runner_managed
    context_policy default

  # Named artifacts. Every nontrivial state writes one of these
  # explicitly. There is no implicit "state output" used for
  # durable data anywhere in this workflow.
  artifact draft text
  artifact critique text
  artifact reflection text

  # ---------------------------------------------------------------
  # States.
  # ---------------------------------------------------------------

  state draft
    actor agent claude-designer
    role designer
    prompt template prompts/designer-draft.md with topic
    reads topic
    writes draft text
    on complete => critique
    on error => stop
    on timeout => stop

  state critique
    actor agent gpt-critic
    role critic
    # Default critic prompt from the role; no override here.
    reads topic, draft
    writes critique text
    on complete => reflect
    on error => stop
    on timeout => stop

  state reflect
    actor agent claude-designer    # same agent as draft, different role
    role reflector
    prompt template prompts/reflector.md with topic, draft, critique
    reads topic, draft, critique
    writes reflection text
    on complete => continue?
    on error => stop
    on timeout => stop

  # Human choice gate. The user picks among continue / accept / stop.
  # See finding (F1) below for the introduced syntax.
  state continue?
    actor human
    prompt file prompts/continuation-question.md
    reads draft, critique, reflection
    options continue, accept, stop
    on continue when attempts.continue? < 5 => critique
    on continue => stop                # cycle exit guard, see (F4)
    on accept => done
    on stop => stop
    on timeout => stop
    on cancelled => stop
```

### Referenced but not created

Files referenced as prompt sources are not part of this sketch. They
would live at:

- `prompts/designer.md` (default for role `designer`)
- `prompts/critic.md` (default for role `critic`)
- `prompts/reflector.md` (default for role `reflector`)
- `prompts/designer-draft.md` (template, takes `topic`)
- `prompts/reflector.md` is also reused as a template taking
  `topic, draft, critique` (see finding (F2) on the file/template
  ambiguity this exposed)
- `prompts/continuation-question.md` (text shown to the human)

Per the project rules, the sketch is the workflow file. Prompt
content is not in scope.

## Primitives exercised

- **External input**: `topic` declared at the workflow level and
  consumed by the `draft` state's prompt template and by every
  downstream state's `reads`.
- **`max_total_steps`**: declared at the workflow level. Set to 40,
  which accommodates roughly 5 full continue iterations
  (critique + reflect + choice = 3 state visits per round) plus the
  initial draft and choice plus headroom. Reasoning recorded here
  rather than as a magic number.
- **Models without roles or prompts**: `model opus`, `model gpt`
  declared as bare backends. Neither carries a role or a prompt.
- **Roles with default prompt sources**: each role declares
  `prompt file <path>`. The `designer` role's default is overridden
  at the `draft` state by a template; the `reflector` role's default
  is also overridden at the `reflect` state. The `critic` role uses
  its default at the `critique` state.
- **Persistent agents with adapter and context policy**:
  `claude-designer` and `gpt-critic` declared as agents on top of
  models, with `adapter api_runner_managed` and a default
  `context_policy`. The runner-managed history is invisible to the
  workflow file (per the discipline that agents' message artifacts
  are not surfaced in source).
- **Same agent in two roles with shared history**: `claude-designer`
  is the actor at both the `draft` state (role `designer`) and the
  `reflect` state (role `reflector`). The design document's
  per-agent (not per-role) history scoping means the reflector
  invocation sees the prior designer turn.
- **Prompt sources, three forms**:
  - `prompt file <path>` at three role declarations.
  - `prompt template <path> with <vars>` at the `draft` and `reflect`
    states.
  - The `critique` state has no state-level prompt; it uses the
    `critic` role's default. This exercises the third path: a state
    falling through to the role's default prompt source.
- **Named artifacts as the data substrate**: `draft`, `critique`,
  `reflection` are declared at the workflow level and written
  explicitly by their producing states. Every downstream state
  declares its `reads` against these artifacts. There is no use of
  `<state>.output` for durable data in this sketch.
- **Human-backed actor as a choice gate**: the `continue?` state has
  `actor human` and exits via the option labels `continue`,
  `accept`, `stop`, plus `timeout` and `cancelled`. See finding (F1).
- **Cycle exit guard tied to attempts**: the `continue?` state's
  transition list uses `on continue when attempts.continue? < 5 =>
  critique` followed by an unguarded `on continue => stop`. This is
  the explicit lint-recommended cycle guard from
  `orchestra-design.md` validation rule 11. It is redundant with
  `max_total_steps` but is the right shape for what the design doc
  asks workflow authors to write.

## What felt awkward

(A1) **Where the cycle exit guard belongs.** The natural author
intuition was to put the bound at the place control returns from
(the `continue?` state's transition), but a "max iterations of the
loop" feels conceptually attached to the loop body, not the gate.
The validator can detect either form, but a workflow author reading
the source has to do work to figure out which iteration counter
governs which loop. With one loop in this sketch the answer is
trivial. With two nested loops it would not be. The design document
lists `attempts.<state>` and `retries.<state>` as the available
counters; it does not give a way to label a loop and refer to its
counter by name. Recorded as friction to revisit if Test 3 (which
has at least one fix loop and possibly one check loop) makes it
worse.

(A2) **The `continue?` state name.** The indicative conventions don't
specify whether punctuation is allowed in state names. Trailing `?`
on a boolean-ish gate state is a common readability convention but
might collide with a future use of `?` as syntax. Replaceable with
`ask-continue` or `continuation-gate` without loss. Flagging because
the grammar phase needs to rule on identifier syntax. Did not feel
worth introducing a new finding number in syntax space; the issue is
naming policy, not syntax.

(A3) **Reusing the `reflector` role's default prompt as a template.**
I wanted the reflector's default prompt (in role declaration) to be
a static file, but the actual `reflect` state needs runtime
substitution of `topic`, `draft`, `critique`. So the role declares
`prompt file prompts/reflector.md` and the state overrides with
`prompt template prompts/reflector.md with topic, draft, critique`,
referencing the same file path. That is not wrong, but the
relationship between "this role has a default file prompt" and "this
state overrides with a template against the same file" is implicit:
there is nothing in the workflow source that says the file is
intended to be used as a template. The validator can check this only
at the state where the template is invoked. See clarification (C2)
below.

(A4) **`reads` on the `continue?` state.** The human is shown
`prompts/continuation-question.md` (a static file). The human does
not need `draft`, `critique`, `reflection` to make a decision in
some abstract sense, but the human prompt presumably embeds or
references them, and the runner can only inject them if they are
declared as inputs. So the state declares `reads draft, critique,
reflection`. This raises the question of whether `reads` describes
"what the prompt source needs" or "what the workflow author wants
the runner to consider when constructing the invocation." For
template states the relationship is direct (template variables map
to reads). For file-prompt states the relationship is by convention.
This is a real ambiguity in what `reads` means; recorded.

(A5) **Bare `actor model <id>` vs `actor agent <id>`.** Test 1 only
uses agent-backed actors. The indicative conventions mention `actor
model <id>` and `actor shell` but the sketch never needed a bare
model invocation, because every LLM call here benefits from
continuity. This made me notice that for many real workflows, bare
model invocations may be rarer than the design suggests; agents may
end up being the default and bare models the exception. Not a
finding against the design, but a calibration note for Test 2 where
the council uses bare models (the design specifies model-backed
role groups, not agent-backed ones, for advisors).

(A6) **Where `external_input` declarations belong.** I wrote
`external_input topic text` as a top-level declaration inside the
workflow body. The design document says workflow-level declarations
include external inputs, but doesn't pick a keyword. `input` would
collide with state-level `reads` informally, and `external_input`
is verbose. The grammar phase has to rule on this. Flagging.

## What the sketch forced me to clarify

(F1) **Choice-gate state syntax.** The design document defines `human`
as an actor backing whose outcomes include "the option labels
chosen, plus `timeout` and `cancelled`," but does not show the
syntax for declaring those option labels in a state. The sketch
needs this for the `continue?` state. Under the unreadability
exception, I introduced:

```
options continue, accept, stop
```

as a state-level declaration that lists the choice labels. The
labels then become the legal `on <label>` outcomes for that state's
transitions. Validation rule: a state with `actor human` must declare
`options`; the set of `on <label>` transitions must equal the
declared options (plus the always-allowed `timeout`, `cancelled`).
Minimum viable syntax. Recorded as the first place the design
document needs to either adopt this or specify something else.

(F2) **Prompt source for a role default vs prompt source for a state
override against the same file.** The design document says a role
"has a default prompt source that defines what playing that role
looks like" and that a state may override with any prompt source.
It does not say whether the role default and a state override may
reference the same path with different forms (file vs template).
The sketch needed this for `reflector`: the role's default prompt
is a static file, the state's prompt is a template against the
same file. I treated the two as independent prompt sources that
happen to share a path. The role's default is `prompt file
prompts/reflector.md`; the state's override is `prompt template
prompts/reflector.md with topic, draft, critique`. The runner
should validate each at its own declaration site. Clarified rather
than introduced as new syntax, because the design document already
allows both forms.

(F3) **Adapter and context-policy declarations on agents.** The
design document specifies adapters
(`api_runner_managed`, subprocess, subprocess-with-resume, relay)
and context policies but does not show the declaration site. The
sketch put them inside the agent block:

```
agent claude-designer
  model opus
  adapter api_runner_managed
  context_policy default
```

The keyword `default` for context_policy is a placeholder for "the
runner's documented default policy." A real workflow could spell
out retention, compression trigger, summary artifact, etc. Recorded
that the language probably wants both an inline policy and a named
policy reference, with a global default available by name. Not
introduced as new syntax beyond what the design document already
implies; flagging as something the grammar phase has to confirm.

(F4) **Two transitions for the same outcome with a guard.** Validation
rule 11 in the design document recommends adding a transition guard
on cycle exits using `attempts.<state>`. The sketch uses the
shape:

```
on continue when attempts.continue? < 5 => critique
on continue => stop
```

This relies on the design document's stated rule that "multiple
guarded transitions for the same outcome are evaluated in declaration
order; the first match wins. Unguarded transitions for an outcome are
fallthroughs." Confirmed in use. The friction is that the unguarded
fallthrough has to be written out separately, even though the intent
("if the bound is exceeded, stop") is conceptually one rule. A
single-line form like `on continue while attempts.continue? < 5
=> critique else stop` would read better but is not in the design.
Not introducing it; recording the friction.

(F5) **No agent message artifact named in the source.** Per your
instruction, the runner's per-agent `messages` artifact is not
declared in the workflow source. The design doc says these exist as
a runner-internal mechanism. Test 1 did not encounter a case where
a state needs to read another agent's history directly; the
intermediate named artifacts (`draft`, `critique`, `reflection`)
carried the data the downstream states needed. This was the
expected outcome for the simplest case. If Test 2 or Test 3 forces
a state to read another agent's full transcript, the question of
whether agents' message artifacts must be promotable to first-class
named artifacts will surface as a finding then. For Test 1, the
design's position holds.

(F6) **Reads declarations are mandatory on every nontrivial state.**
The design document says reads should be declared explicitly. The
sketch makes this true for every state including the `draft` state,
which reads only the external input `topic`. There was a temptation
to omit `reads topic` on `draft` since `topic` is the only thing
in scope, but the discipline rule covers this case too: if reads
are explicit, every state's input set is auditable from the source
without simulating the runner. Confirmed and applied.
