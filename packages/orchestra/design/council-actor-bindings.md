# council_four actor bindings: distinct role bindings, not distinct model strings

## Background

The early `_validate_council_four` rule required the synthesizer to
resolve to a different `(adapter, model)` tuple from every one of the
four proposers. The motivation was an explicit concern flagged during
review: do not let a model judge its own output. A model that has
just produced text, asked under a similar prompt to evaluate that
text, is poorly placed to find its own blind spots.

That concern is real. It is the same concern that drives the
distinct-actor rule on `iterate_until_acceptable` (proposer vs
reviewer) and `propose_review_judge_implement` (proposer vs reviewer
vs implementer). In those workflows the rule fits cleanly: a single
actor produces output, then a separate actor critiques it.

## Why the rule does not fit `council_four`

Council synthesis is a different shape:

- The synthesizer reads four parallel proposals and writes a
  synthesis ACROSS them. It is not asked "is your proposal good?"
  It is asked "synthesize across these four into a coherent plan."
- The prompt is `council_synthesizer.md`, not any proposer template.
  The conversation context is fresh: four proposal artifacts as
  inputs, no prior turns from the synthesizer's perspective.
- The synthesizer does not "judge" any single proposal. The verdict
  it emits records agreements, disagreements, rejected options, and
  criteria compliance ACROSS the four — a structural summary of the
  fan-out, not a quality grade for one proposer.

The original concern was about a model evaluating its own output
under a similar prompt. Council synthesis is a model writing a
synthesis ACROSS four proposals under a synthesis-specific prompt.
The risk shape does not transfer.

## What the rule is now

`_validate_council_four` enforces:

- All six required roles are bound: framer + proposer_code +
  proposer_codex + proposer_kimi + proposer_deepseek + synthesizer.
- The four proposers resolve to pairwise distinct (adapter, model)
  tuples. Cross-model diversity at the proposer layer is the value
  of the council pattern; collapsing two proposers to the same model
  defeats the fan-out.

It does NOT enforce:

- Distinct model strings between synthesizer and any proposer.
  `proposer_code = (claude_code_text, opus)` and
  `synthesizer = (claude_code_text, opus)` is a valid configuration.
- Distinct model strings between framer and any other role.
  Framer's identity has been unconstrained from the start.

Distinct ROLE BINDINGS remain structural by definition: each role is
its own key in the bindings map with its own template. Two roles
sharing a model string still run as separate states with separate
prompts.

## Why this matters now

Opus is the default Anthropic model for non-trivial work; haiku and
sonnet are reserved for tasks simple enough to warrant them. Plan
synthesis is not such a task. The earlier rule pushed the
synthesizer off opus to satisfy the distinctness constraint, which
made the synthesis weaker than the proposers it was synthesizing
across. Loosening the rule lets duplo's default council run with
proposer_code on opus AND synthesizer on opus without contortion.

The `claude_code_text_kimi` and `claude_code_text_deepseek` adapters
preserve cross-model diversity at the proposer layer regardless of
where the Anthropic-routed roles land.
