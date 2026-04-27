# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Orchestra is a **design-stage** project. There is no code, build system, or test suite yet. The repository currently contains only design documents under `design/`. Do not invent build/test/lint commands; there are none to run.

The work in progress is a meta-language and runtime spec for describing systems of interacting LLMs (councils, design loops, code workflows, research pipelines). It is informed by the author's prior work on `mcloop` and `Duplo` (Claude Code subprocess orchestrators), and by manual multi-LLM workflows the author runs by hand.

## Documents and reading order

Read these in order when picking up the project:

1. `design/orchestra-design.md` — the authoritative preliminary design. Conceptual model, the four-way factoring (model / role / prompt source / state), agents, artifacts, profiles, validation rules, deferrals. ~1000 lines; treat its commitments as load-bearing.
2. `design/orchestra-acid-tests.md` — cover doc for the three sketch workflows that are the gate on further design work.
3. `design/orchestra-acid-test-1-design-loop.md` — the only sketch written so far. Tests 2 (council) and 3 (mcloop code workflow) are not yet written and are the next deliverables.

A historical `workflow-metalanguage.md` is referenced but not present in this directory; it is the code-specific predecessor that the general design supersedes.

## Where the project is in its workflow

The next concrete step (per `orchestra-design.md` "Status and next steps") is **writing acid-test workflows 2 and 3** in the proposed syntax. Findings from Test 1 may change how Tests 2 and 3 are approached. Grammar pinning, runner architecture, and implementation come *after* all three sketches exist. Do not skip ahead to grammar or implementation work unless the user asks.

## Discipline that governs sketch writing

These rules come from `orchestra-acid-tests.md` and apply to any new sketch you produce:

- Every nontrivial state declares its artifact `reads` and `writes` explicitly. `<state>.output` is reserved for trivial ephemeral control data (verdict enums, choice labels) — never for durable data.
- Bindings (model, role, prompt source, artifact) are explicit at every state.
- **Surface form is held constant across the three sketches.** Use the same indicative conventions as Test 1: `spec` / `workflow` headers, top-level `model` / `role` / `agent` / `group` / `artifact` / `state` / `prompt` declarations, indentation for grouping, `=>` for transitions, `#` comments, `state` keyword with `actor model <id>` / `actor agent <id>` / `actor shell` / `actor human` inside. Drift in surface form makes awkwardness uninterpretable.
- Introduce new syntax only when the sketch becomes unreadable without it. When you do, flag it as a finding (numbered `(F<n>)`) in the sketch's "What the sketch forced me to clarify" section, with justification.
- Each sketch sub-file follows the fixed structure: Goal → Workflow sketch → Primitives exercised → What felt awkward → What the sketch forced me to clarify.

## Design commitments that are easy to drift from

When discussing or writing about the design, these positions are deliberate — do not reframe them as open questions:

- **Roles and models are orthogonal.** The same model can play different roles; the same role can be played by different models. Do not collapse to "agent = model + prompt".
- **Prompt source vs prompt artifact** are distinct. A source is a recipe; an artifact is the resolved text logged at invocation time.
- **Per-agent (not per-role) history scoping** in v0. An agent invoked first as designer and then as arbiter sees the prior designer turns. Per-role isolation within one agent is deferred.
- **The core grammar is closed.** Profiles register artifact types, actor backings, postconditions, guards, parsers, validation rules, defaults — but cannot add top-level keywords, state types, or transition syntax.
- **`max_total_steps` is mandatory** at the workflow level; omitting it is a load error. Per-state cycle guards (`attempts.<state>`) are a separate, lint-recommended mechanism.
- **Parallel writes to the same artifact are a v0 load error**, not a merge problem to define semantics for.
- The v0 non-goals list in `orchestra-design.md` (dynamic spawning, expression language, recursion, distributed execution, browser automation, grand unified ontology) is a real boundary, not a wishlist.

## Acid tests as the unit of progress

The three acid tests are the *test cases* for the design itself, not for code. A sketch is "passing" when it expresses its workflow without special cases or extensions, with all bindings explicit and no `<state>.output` shortcuts for durable data. Frictions that emerge belong in "What felt awkward" (with `(A<n>)` numbering); decisions that close design ambiguities belong in "What the sketch forced me to clarify" (with `(F<n>)` numbering). Findings from earlier sketches feed forward into later ones.

## Author conventions

- Commit messages: never mention Claude, Claude Code, or Anthropic.
- Prose style across the design docs is plain, declarative, no marketing voice. Match it. Avoid bulleted lists where running prose works; avoid emoji; avoid "we'll" / "let's" framing.
