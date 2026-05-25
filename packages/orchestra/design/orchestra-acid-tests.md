# Orchestra Acid Tests

## Purpose

This is the cover document for three workflow sketches written in the
proposed Orchestra language. The sketches are probes against the
conceptual model in `orchestra-design.md`. Their job is not to produce
production-ready workflows. Their job is to surface problems in the
abstractions before the grammar is pinned down and before the runner
is built.

The design document explicitly identifies these sketches as the gate
on further design work:

> The conceptual model has stabilized [...] Further design refinement
> without sketches and implementation will not reveal much. The acid
> tests will.

## The three tests

The tests are written in this order on purpose. The order runs from
smallest non-code probe to largest code-bearing probe, so that the
general core is exercised before any pull back toward the original
mcloop use case.

1. **Test 1: Design loop** (`orchestra-acid-test-1-design-loop.md`).
   The smallest non-code test. Two persistent agents, the same model
   used in different roles, distinct prompt sources (file and
   template), a human continuation choice gate, explicit named
   artifacts. Probes whether agents, roles, prompt sources, choice
   gates, and the ban on `<state>.output` for durable data hold up on
   a workflow with no shell, no workspace, no verdict schemas.

2. **Test 2: Five-member council with chair**
   (`orchestra-acid-test-2-council.md`). Probes generality. Two
   role groups (advisors and reviewers), one model bound across all
   roles, parallel invocation with `join all`, anonymization done
   workflow-structurally via a shell-actor state (visibility is v1,
   not v0), chair synthesis with a verdict schema. Stays out of the
   workspace and code profiles entirely; the only non-LLM actor is
   one shell call for the mechanical anonymization transform.

3. **Test 3: mcloop code implementation**
   (`orchestra-acid-test-3-mcloop.md`). Probes whether the general
   core still supports the original practical goal once the language
   has been generalized. Versioned-workspace and code profiles,
   `git-workspace` artifact, mutation modes, `require_diff`
   postcondition, multi-command shell-actor states (lint/typecheck/
   test sweep, sequenced commit), verdict schema with guarded
   transitions, retry policy on error/timeout, three cycles sharing
   a state, human abandon gate.

## Discipline (applied to all three)

The rules from `orchestra-design.md` and the project instructions
that govern these sketches:

- Every nontrivial state declares its artifact reads and writes
  explicitly. `<state>.output` is reserved for trivial ephemeral
  control data (verdict enum values, choice labels). Durable data
  flows through named artifacts.
- Model, role, prompt source, and artifact bindings are explicit at
  every state.
- New syntax is introduced only when the sketch becomes unreadable
  without it. When new syntax is introduced, it is flagged as a
  finding in the sketch's "what the sketch forced me to clarify"
  section.
- Surface form is held constant across the three sketches so that
  awkwardness is attributable to the abstraction rather than to a
  shifting surface form. The form follows the indicative conventions
  in `orchestra-design.md` ("Lexical and syntactic conventions"):
  `spec` and `workflow` headers, top-level `model` / `role` / `agent`
  / `group` / `artifact` / `state` / `prompt` declarations,
  indentation for grouping, `=>` for transitions, `#` comments,
  `state` keyword with `actor model <id>` / `actor agent <id>` /
  `actor shell` / `actor human` inside.

## Per-sketch structure

Every sketch sub-file has these sections, in this order:

1. **Goal.** What the sketch is meant to express and which design
   primitives it is supposed to exercise.
2. **Workflow sketch.** The actual workflow file content in fenced
   code blocks. Where a sketch has more than one file (e.g. the
   workflow plus a verdict schema referenced from it), each file is
   in its own fenced block with a header naming the path.
3. **Primitives exercised.** A checklist of the design-document
   primitives the sketch touches and how it touches them.
4. **What felt awkward.** Concrete frictions encountered while
   writing the sketch. Each item is specific enough to act on
   (either by changing the design, accepting a deferral, or pinning
   down a grammar decision).
5. **What the sketch forced me to clarify.** Decisions made during
   the sketch that the design document left open or ambiguous.
   Includes any new syntax introduced under the unreadability
   exception, with a brief justification.

## Status

- Test 1: written and revised once after review.
- Test 2: written and revised once after review.
- Test 3: written.

## Cross-test findings summary

The following findings recurred across multiple sketches or have the
strongest weight against the design document. This list is for
quick orientation; full context lives in each sub-file.

**The biggest finding from Test 3 (F16): profile-provided state-level
keywords.** The design document says profiles cannot add new top-level
keywords or new state types, and that the core grammar is closed.
Test 3 needed `runs` (a multi-line shell command block) on shell-actor
states; expressing it via one-state-per-command defeats the purpose of
having a shell actor. The recommended carve-out: profiles may register
state-level keywords scoped to actor backings the profile registers
(so `runs` is legal only inside `actor shell` states). This is the
sketch finding most likely to require an actual change to the design
document's profile rules.

**The loop-progress pattern (Test 1 A7, Test 2 A14).** Both Tests 1
and 2 needed the upstream state of a loop to write an artifact the
downstream loop target reads, or the loop did not progress. The fix
in both cases used the existing versioned-artifact mechanism with
no new primitive. Test 2 additionally needed the new `initial`
clause on artifact declarations (F15) for the first-pass case where
the loop target reads the artifact before any iteration has written
it.

**Multiple cycles sharing a state (Test 1 A1, Test 3 A16, Test 3 F20).**
The design document's `attempts.<state>` counter is per-state, not
per-loop. When multiple cycles share a state, the workflow author
must identify each loop's natural exit point and place the guard on
the originating state of that loop. The lint rule from validation
rule 11 helps but cannot tell the author which transition to guard.
A future affordance worth considering is named loops with explicit
bounds. Not blocking for v0.

**Retry policy syntax (Test 3 F19).** The original metalanguage's
`on error retry max N then <target>` shape is significantly more
readable than expressing retries as guarded transitions back to the
same state. Adopted under the unreadability exception. The design
document's `retries.<state>` counter is the right counter for this;
the syntax is just shorthand for guarded retries.

**External inputs and field references (Test 3 A15, F21).** The
original metalanguage treated `task` as a record with implicit
fields. The generalized design needs to decide whether external
inputs can carry schemas or typed record declarations so that field
references like `task.needs_tests` can be statically validated. Not
blocking for v0; flagged.

**Choice-gate syntax (Test 1 F1).** The `options` declaration on
`actor human` states is the single piece of new syntax most likely
to need adoption directly into the language. Reused identically in
Test 3.

**Schema and workspace artifact source qualifiers (Test 2 F11,
Test 3).** Test 2 introduced `source file <path>` for schema
artifacts; Test 3 introduced `source path workspace_path` for
git-workspace artifacts. Both are minimum-viable syntax for "this
artifact's content comes from outside the workflow." Worth
generalizing into one mechanism.
