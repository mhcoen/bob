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
   has been generalized. Versioned-workspace profile, code profile,
   shell-backed actors for lint/typecheck/test, verdict schemas with
   `approve` / `request_changes`, `require_diff` postcondition,
   retry/fix loop, commit step. Should be expressible without
   reaching for primitives the first two tests did not need.

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
- Test 2: written.
- Test 3: not yet written.
