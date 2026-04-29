# Acid Test 3: McLoop Code Implementation

## Goal

Re-express the original McLoop `implement` workflow (preserved at
`/Users/mhcoen/proj/mcloop/local/workflow-metalanguage.md`) in the
generalized Orchestra language. The discipline is that the general
core established by Tests 1 and 2 should still support this workload
once the versioned-workspace and code profiles are loaded.

The workflow:

1. A task is given as an external input.
2. Kimi writes the implementation against the workspace.
3. A shell-actor state runs lint, type-check, and existing tests in
   diagnostic mode (all three run regardless of intermediate failures).
4. On any check failure, Kimi fixes the mechanical errors and the
   workflow returns to `check`.
5. On all checks passing, Opus reviews the implementation against a
   verdict schema with values `approve` and `request_changes`.
6. On `request_changes`, Kimi fixes based on review feedback and the
   workflow returns to `check`.
7. On `approve` and `task.needs_tests` truthy, Kimi writes tests; the
   workflow returns to `check` afterward.
8. On `approve` and not needing tests, the workflow commits and ends.
9. After exhausted retries anywhere upstream, a human choice gate
   (`abandon`) presents three options: retry the whole task, skip
   the task, or halt the workflow.

The sketch exercises:

- The versioned-workspace profile (`git-workspace` artifact, mutation
  modes, runner checkpoints).
- The code profile (the `require_diff` postcondition).
- Shell-actor states that run multiple commands, both diagnostic mode
  (`continue_on_fail` true) and sequenced mode (the default).
- An LLM state with a verdict schema driving non-trivial transition
  guards (`on approve when task.needs_tests => write-tests`).
- The same retry/fix loop shape as the original metalanguage,
  expressed against the general core.
- The `abandon` choice gate, reusing the `options` syntax introduced
  in Test 1 (F1).
- Per-state retry policy on `error` and `timeout` outcomes.

The sketch does not use:

- Persistent agents (each LLM invocation is a fresh model call; the
  workspace is the source of truth, not a conversation history).
- Schema artifacts beyond the verdict schema for `review`.
- Role groups or multi-actor states.

## Workflow sketch

### File: `implement.orc`

```
spec 0.1

workflow implement

  uses profile versioned-workspace
  uses profile code

  external_input task json
  external_input workspace_path text

  max_total_steps 80

  # Models. Short IDs only, registry-managed; no provider-specific
  # routing in the workflow file.
  model kimi
  model opus

  # Roles. Each role has a default prompt source. The state-level
  # prompt override mechanism from Test 1 is not used here; every
  # state uses its role's default prompt.
  role implementer
    prompt file prompts/implement.md

  role check-fixer
    prompt file prompts/fix-check.md

  role reviewer
    prompt file prompts/review.md

  role review-fixer
    prompt file prompts/fix-review.md

  role test-writer
    prompt file prompts/write-tests.md

  # Verdict schema for the review state. Same artifact-based shape
  # as Test 2 (F11).
  artifact review-verdict-schema schema
    source file schemas/review.schema.json

  # ---------------------------------------------------------------
  # Workspace artifact. Provided by the versioned-workspace profile.
  # The workspace is the source of truth for code state; the
  # implementer, fixer, and test-writer roles all mutate it under
  # `mode readwrite`. The check state and reviewer state are
  # `mode readonly`.
  # ---------------------------------------------------------------

  artifact workspace git-workspace
    source path workspace_path

  # ---------------------------------------------------------------
  # Result artifacts. The check state writes a structured `errors`
  # artifact that the fix-check state reads. The reviewer writes
  # `review` (json validated against the verdict schema) including
  # a feedback field that the fix-review state reads. The verdict
  # field of `review` drives the state's transition outcome.
  # ---------------------------------------------------------------

  artifact check-errors json
    initial {}
  artifact review json

  # ---------------------------------------------------------------
  # States.
  # ---------------------------------------------------------------

  # Initial implementation. Kimi writes code into the workspace.
  # require_diff true (code profile postcondition) errors the state
  # if no workspace change was produced.
  state code
    actor model kimi
    role implementer
    mode readwrite
    reads task, workspace
    writes workspace
    require_diff true
    timeout 30m
    on complete => check
    on error retry max 2 then abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Lint, type-check, run existing tests. Diagnostic mode: all three
  # commands run regardless of intermediate failures. The shell
  # actor's stdout/stderr/exit-code capture is parsed into the
  # `check-errors` artifact for the fix-check state to consume.
  # See findings (F16) and (F17).
  state check
    actor shell
    mode readonly
    reads workspace
    writes check-errors
    runs
      "ruff check ."
      "mypy ."
      "pytest -q"
    continue_on_fail true
    timeout 15m
    on pass => review
    on fail => fix-check
    on error => abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Fix mechanical errors from check. Kimi reads the structured
  # check-errors artifact and the task. Returns to `check` so the
  # fixes are re-verified end to end.
  state fix-check
    actor model kimi
    role check-fixer
    mode readwrite
    reads task, check-errors, workspace
    writes workspace
    require_diff true
    timeout 30m
    on complete when attempts.fix-check < 5 => check
    on complete => abandon                       # cycle exit guard, see (F4)
    on error retry max 3 then abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Opus reviews the implementation. The verdict schema drives
  # transitions on `approve` and `request_changes`. The reviewer
  # is readonly: the review state must not modify the workspace.
  state review
    actor model opus
    role reviewer
    mode readonly
    reads task, workspace
    schema review-verdict-schema
    writes review
    timeout 15m
    on approve when task.needs_tests => write-tests
    on approve => commit
    on request_changes when attempts.review < 6 => fix-review
    on request_changes => abandon                # cycle exit guard
    on error retry max 2 then abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Fix based on the reviewer's design feedback. Kimi reads the
  # `review` artifact (which includes a feedback field, plus the
  # verdict). Returns to `check` so any code change is re-verified
  # mechanically before being re-reviewed.
  state fix-review
    actor model kimi
    role review-fixer
    mode readwrite
    reads task, review, workspace
    writes workspace
    require_diff true
    timeout 30m
    on complete => check
    on error retry max 2 then abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Write new tests. Reached only when the reviewer approved and the
  # task requires tests. Returns to `check` so the new tests run as
  # part of the check sweep.
  state write-tests
    actor model kimi
    role test-writer
    mode readwrite
    reads task, workspace
    writes workspace
    require_diff true
    timeout 30m
    on complete => check
    on error retry max 2 then abandon
    on timeout retry max 1 then abandon
    on cancelled => abandon

  # Commit the workspace changes. Sequenced shell commands, default
  # mode (continue_on_fail false): on the first nonzero exit, later
  # commands are skipped and the state outcome is `fail`.
  state commit
    actor shell
    mode readwrite
    reads task, workspace
    writes workspace
    runs
      "git add -A"
      "git diff --cached --check"
      "git commit -m \"{task.name}\""
    continue_on_fail false
    timeout 1m
    on pass => done
    on fail => abandon
    on error => abandon
    on timeout => abandon
    on cancelled => abandon

  # Reached when retries are exhausted somewhere upstream or a cycle
  # guard fired. Asks the user. Same shape as Test 1's choice gate.
  state abandon
    actor human
    prompt template prompts/abandon-question.md with task
    reads task
    options retry, skip, halt
    on retry => code
    on skip => done
    on halt => stop
    on timeout => stop
    on cancelled => stop
```

### Referenced but not created

Files referenced by the workflow but not part of the sketch:

- `prompts/implement.md` (default for role `implementer`)
- `prompts/fix-check.md` (default for role `check-fixer`)
- `prompts/review.md` (default for role `reviewer`)
- `prompts/fix-review.md` (default for role `review-fixer`)
- `prompts/write-tests.md` (default for role `test-writer`)
- `prompts/abandon-question.md` (template, takes `task`, shown to the
  human at the abandon gate)
- `schemas/review.schema.json` (verdict enum: `approve`,
  `request_changes`; plus a `feedback` text field consumed by
  fix-review)

External inputs supplied by the runner caller (McLoop):

- `task` (json): record with at least `name` (string, used in commit
  message) and `needs_tests` (boolean, used in guard).
- `workspace_path` (text): filesystem path to the git workspace the
  workflow operates on.

## Primitives exercised

- **Profile composition**: `uses profile versioned-workspace` followed
  by `uses profile code`. The first registers the `git-workspace`
  artifact type, the mutation modes, and the checkpoint mechanism;
  the second adds `require_diff` and code-specific defaults. The
  design document specifies that profile registrations are additive
  when they don't collide. This is the first sketch to exercise more
  than one profile. Recorded as (F18).
- **External input as a record**: `task` is declared as `json` and
  referenced as `task.name` in a template substitution and
  `task.needs_tests` in a guard. The design document shows this
  pattern in the original McLoop examples but does not specify
  whether `external_input task json` is the right shape vs a typed
  record declaration. Going with `json` for the sketch; see (A15).
- **Workspace artifact with a path source**: `artifact workspace
  git-workspace` with a `source path workspace_path` qualifier
  (analogous to the `source file <path>` from Test 2's schema
  declaration in F11). The runner binds the workspace artifact to
  the directory at `workspace_path` for the run.
- **Mutation modes**: `mode readwrite` on every code-writing state,
  `mode readonly` on `check` and `review`. The design document
  delegates these to the versioned-workspace profile. The cleanliness
  policy that enforces readonly is a runner concern, not a workflow
  concern.
- **`require_diff true` postcondition**: declared on every readwrite
  LLM state. Code profile postcondition. A readwrite state that
  produces no workspace diff exits as `error`, which then either
  retries or transitions to `abandon`.
- **Multi-command shell states**: `check` runs three commands in
  diagnostic mode; `commit` runs three commands sequenced. Both use
  a multi-line `runs` block. See (F16).
- **`continue_on_fail` semantics**: `true` on `check` (diagnostic
  sweep); `false` on `commit` (sequenced operations where later
  commands depend on earlier ones). Carried over from the original
  metalanguage.
- **Verdict schema with guarded transitions**: `review` declares
  `schema review-verdict-schema`; transitions are
  `approve when task.needs_tests => write-tests`,
  `approve => commit`, and `request_changes => fix-review` (with a
  cycle exit guard). The schema-driven exhaustiveness rule
  (validation rule 10) is satisfied: every verdict value has at
  least one transition.
- **Retry policy on `error` and `timeout`**: every LLM and shell
  state declares retry policies on its retryable outcomes
  (`on error retry max N then <target>`,
  `on timeout retry max N then <target>`). This is the original
  metalanguage's syntax; introduced under the unreadability
  exception. See (F19).
- **Three converging cycles back to `check`**: `fix-check`,
  `fix-review`, and `write-tests` all transition to `check` on
  completion. `check` itself transitions to either `review` (pass)
  or `fix-check` (fail). The graph has three distinct cycles
  sharing the `check` state. Cycle exit guards live on the states
  whose loops they bound: `fix-check` guards `attempts.fix-check`,
  `review` guards `attempts.review` for the request_changes loop.
  See (A16).
- **Human choice gate reused from Test 1**: `abandon` uses
  `actor human` and the `options retry, skip, halt` syntax from
  F1. Same shape as Test 1's `continue-gate`.

## What felt awkward

(A15) **`task` external input as `json`.** The original metalanguage
treated `task` as a record with named fields (`task.name`,
`task.needs_tests`); the field set was implicit, varying by what
workflows happened to reference. The sketch declares
`external_input task json`, which captures the shape but loses the
schema. Two consequences:
1. Field references like `task.needs_tests` cannot be statically
   validated against a declared schema; the validator can only
   check that the reference is syntactically a `task.<field>`
   form.
2. A typo in a guard (`task.needs_test` instead of
   `task.needs_tests`) silently evaluates to falsy.
The design document does not address this. Two possible fixes for
later:
- Add a `schema` qualifier on external inputs: `external_input
  task json schema schemas/task.schema.json`.
- Allow record-typed external inputs: `external_input task record
  { name text, needs_tests boolean }`.
The first reuses existing JSON Schema mechanism; the second is
inline. Either is post-v0. Recorded as a real gap, not introducing
syntax for it now.

(A16) **Multiple cycles sharing a state (`check`).** Three different
states (`fix-check`, `fix-review`, `write-tests`) transition back
to `check`, and `check` itself transitions back to `fix-check` on
fail. This means there are three distinct cycles in the state
graph, all of which include `check`. The cycle exit guards live
on the *originating* state of each cycle, not on `check`. So
`fix-check` has a guard on `attempts.fix-check`, `review` has a
guard on `attempts.review` (for the `request_changes => fix-review`
loop), and `write-tests` has no guard at all (the cycle through
`write-tests => check => review => write-tests` is bounded only
by `max_total_steps`).

This is the case A1 from Test 1 worried about made concrete. With
multiple loops sharing a state, the workflow author has to
identify each loop's natural exit point and put the guard there.
The validator's lint rule (validation rule 11) flags cycles
without guards, but it cannot tell the author *which transition*
should carry the guard for a given loop. The static graph has
three cycles; whether a given loop is bounded depends on which
attempts counter is incremented as the loop progresses.

The current sketch is provably bounded by `max_total_steps 80`
even if every per-state guard is wrong, but the per-state guards
are doing useful work for diagnosis (they distinguish "the
fix-check loop is unproductive" from "we hit the global budget").

A future language affordance worth considering: named loops with
explicit bounds. `loop fix-check-loop bound 5 from fix-check via
check` or similar. Not introducing this; recording the friction.

(A17) **Three "fix" roles for what feels like one role.** The
sketch has `check-fixer`, `review-fixer`, and `test-writer` as
distinct roles. They all happen to be backed by Kimi and they
all happen to mutate the workspace based on different inputs.
A more compact factoring would have one `kimi-implementer` agent
(persistent, with continuity) playing roles like `implement`,
`fix-mechanical`, `fix-design`, `add-tests` against the same
workspace.

I did not use a persistent agent here because the original
metalanguage didn't, and because the workspace is the source of
truth for state across iterations (not the agent's conversation
history). A persistent agent would add a parallel state channel
(the agent's history) on top of the workspace's state, with
unclear semantics about which channel is authoritative when the
two diverge. Not a finding against the design; a deliberate
choice. Recorded so it can be revisited if a code workflow
benefits from agent continuity.

(A18) **Reading and writing the same workspace artifact at every
state.** Every code-touching state has `reads workspace` and (if
readwrite) `writes workspace`. This is true and explicit but
verbose: of the eight states in the workflow, six have the same
`reads workspace, writes workspace` pair. The `mode readwrite`
declaration alone implies "writes workspace"; the `mode readonly`
declaration alone implies "reads workspace, does not write."

A future affordance: let `mode readwrite` and `mode readonly` on
states that the profile recognizes as workspace-aware imply the
reads and writes on the workspace artifact, without the author
having to repeat them. This is a profile-specific shorthand, not
a core language change. Recorded; not introducing now.

(A19) **`task` is `reads task` on every state.** Same shape as A18
but for the external input. Almost every state reads `task`. The
external input is workflow-scope; it is in scope for every state
by virtue of being declared at the workflow level. The design
document does not say whether external inputs need to be in
`reads` for a state to use them. The sketch declares them
explicitly, consistent with the discipline rule that reads be
auditable from the source. But this is verbose. Worth deciding
whether external inputs are implicitly in scope or must be
declared in reads like artifacts. Recorded.

## What the sketch forced me to clarify

(F16) **Multi-line shell command syntax.** Test 2 used a single
`command "..."` line for the anonymize state. Test 3's `check`
and `commit` states each run multiple commands. Two options:
1. Keep `command "..."` and have one shell-actor state per
   command, with transitions wiring them together.
2. Allow a multi-line `runs` block on a shell-actor state, where
   each line is one command and the state's outcome aggregates
   across them per the `continue_on_fail` policy.

Option 1 explodes the state graph for what is conceptually one
operation (running checks, doing a commit). Option 2 matches the
original metalanguage's `run "..."` lines and keeps the workflow
readable.

Under the unreadability exception I introduced:

```
state check
  actor shell
  ...
  runs
    "ruff check ."
    "mypy ."
    "pytest -q"
  continue_on_fail true
  ...
```

The `runs` keyword introduces a multi-line block of shell command
strings. The block is terminated by the next state-level keyword
(`continue_on_fail`, `timeout`, etc.). Under
`continue_on_fail true`, all commands run; the state outcome is
`pass` if all exit zero, `fail` otherwise. Under
`continue_on_fail false` (default), commands run sequenced; on the
first nonzero exit, remaining commands are skipped and the state
outcome is `fail`.

This is a profile concern: only the shell actor backing exposes
`runs`. Profile-provided syntax extension via a new state-level
keyword goes beyond what the design document says profiles can
do (the core grammar is closed). Either:
- The design's "profiles do not add new top-level keywords" rule
  needs amendment to permit profile-provided state-level
  keywords for actor-backing-specific options, or
- Multi-command shell states need to be expressed with one
  state per command (option 1 above), which is unworkable.

Recommendation for the grammar phase: profiles may register
state-level keyword extensions that are scoped to the actor
backings the profile registers. The keyword `runs` is registered
by the shell actor (or the versioned-workspace profile) and is
only legal inside `actor shell` states. The validator rejects
its use elsewhere.

This is the most significant finding from Test 3: the closed-core
rule for profiles needs a carve-out for actor-backing-specific
state-level options. The alternative (one state per command)
defeats the purpose of having a shell actor at all.

(F17) **Shell-actor structured output as an artifact.** The original
metalanguage's `command` state result has an `errors` field
("parsed error summary, best-effort, tool-specific"). The
fix-check state in the original referenced this as
`check.errors`. In the generalized language with the discipline
of named artifacts over `<state>.<field>` references, the check
state needs to write a `check-errors` json artifact that
fix-check reads.

The sketch does this explicitly:

```
state check
  ...
  writes check-errors
  ...

state fix-check
  ...
  reads task, check-errors, workspace
```

How the shell actor populates the `check-errors` artifact is not
specified. The original metalanguage had the runner do
best-effort parsing; the generalized language could either keep
that runner-side parsing or require the shell command to produce
a json file the runner reads as the artifact value.

The latter is cleaner: the workflow author writes a wrapper
script that runs the checks and emits structured output; the
runner reads the script's stdout as the artifact value. But
this makes the `runs` block insufficient: the runner needs to
know which command's output is the artifact, or the script
itself has to write the artifact to a known location.

Initialized `check-errors` to `{}` so the first read on workflow
start (which doesn't happen in this graph but might in a future
variation) is well-defined. Recorded as a gap; the runner spec
must close it.

(F18) **Profile composition.** First sketch to use two profiles.
Syntax:

```
uses profile versioned-workspace
uses profile code
```

The design document states profiles are additive when their
registrations don't collide. Versioned-workspace registers the
`git-workspace` artifact type and mutation modes. Code adds
`require_diff` postcondition. The two have non-overlapping
extension points; composition is automatic.

Question for the grammar phase: are profile declarations
ordered (does `uses profile code` after
`uses profile versioned-workspace` mean code is layered on top,
or are they unordered)? For non-conflicting profiles it does not
matter. For profiles with shared extension points (none of which
exist in v0) it might. Recorded as a minor open question.

(F19) **Retry policy syntax on `on error` and `on timeout`.** The
original metalanguage's `on error retry max 2 then abandon` is
one line. Expressing the same intent as guarded transitions back
to the same state would require:

```
on error when retries.code < 2 => code
on error => abandon
```

This is verbose, semantically slightly different (the second form
uses `retries.<state>` as if it were a regular guard, but
`retries` only counts on-error/on-timeout retries, not other
re-entries; the design document is careful about this
distinction), and obscures the author's intent. Under the
unreadability exception I kept the original syntax:

```
on error retry max 2 then abandon
on timeout retry max 1 then abandon
```

`retry max N then <target>` reads as: "on this outcome, retry the
state up to N times. If still failing after N retries, transition
to <target>." `retry max 0 then X` is equivalent to a plain
unguarded `on <outcome> => X`.

Validation rule recommendation: `retry max N then <target>` is
legal only on `error` and `timeout` outcomes (the retryable
outcomes per the design document). Domain verdicts are not
retried; they transition to a different state addressing the
verdict.

The retry counter (`retries.<state>`) is incremented per retry
attempt and reset to zero each time control returns to the state
from a different state. This is the original metalanguage's
semantics. Adopt or replace.

(F20) **Cycle exit guards on multiple cycles.** Test 3 has the
case Test 1 (A1) anticipated: multiple loops sharing a state.
The sketch attaches per-loop attempts guards to the originating
state of each loop:
- `fix-check` guards `attempts.fix-check < 5` (the
  fix-check => check => fix-check loop).
- `review` guards `attempts.review < 6` (the
  review => fix-review => check => review loop, on the
  request_changes outcome).

The `write-tests` loop is unguarded; it falls under
`max_total_steps`. This is a deliberate choice: writing tests
should happen at most once per approved review, and the natural
bound is the review loop's bound, not a separate write-tests
bound.

This is workable but requires the workflow author to reason
about every loop in the state graph and choose a bound for
each. The lint rule from validation rule 11 helps surface
unguarded cycles but does not say which transition should carry
the guard. As noted in (A16), named loops would be cleaner.
Recorded.

(F21) **External inputs as `json` records vs typed records.** See
(A15). The sketch uses `external_input task json` and references
`task.name` and `task.needs_tests`. The design document does not
specify how field references on a json external input are
validated. The sketch assumes:
- The reference syntax `task.<field>` is the same as it would
  be on a typed record.
- The validator does not statically check that the field exists
  or has a particular type, since the json type is unstructured.
- A field reference that resolves to undefined at runtime
  evaluates to falsy in a guard and to an error in a template.

Adopt or replace. The cleanest fix is to allow either a json
type with an associated schema (`external_input task json schema
<path>`) or a typed record literal in the declaration. Not
introducing either now; flagging.

