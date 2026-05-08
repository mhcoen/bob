# Probe: Kimi and DeepSeek as design-review actors

## What to test

Whether Kimi or DeepSeek can serve as design-review actors in
Orchestra workflows, alongside or instead of codex. Specifically:
the role tonight where I drafted prompts, you ran them past codex,
codex produced terse audit-friendly architectural opinions on
F1/F2/F2.5 splits, threshold rules, schema-vs-runtime layering.

## Available CLIs

**Kimi Code CLI** (open-source, Apache 2.0, MoonshotAI/kimi-cli on
GitHub). `pip install kimi-cli`. Default model is currently
"Kimi for Code" routing to K2.6 (or K2.5 if backend hasn't
propagated). 256K context window. Has thinking mode and non-
thinking mode. Subscription-gated for heavy use; per-API pricing
exists separately.

**DeepSeek** has multiple CLI options. The most relevant for our
purposes is the official API (deepseek-v4-pro or deepseek-v4-flash
model IDs) accessed via the OpenAI-compatible SDK or via
PierrunoYT/deepseek-cli (`pip install deepseek-cli`, `deepseek -q
"..."` for inline mode). 1M token context window. Three reasoning
effort modes: non-thinking, high, max.

## Why we care

Codex has been useful tonight but consultation latency is real.
Each round-trip is human-mediated (you paste prompts; you paste
back replies). The work could potentially benefit from:

- A second design-review actor with different model bias for
  Orchestra workflows where two reviewers strengthen the verdict
  (council, anonymous-reviewers).
- A cheaper actor for high-volume or exploratory consultations
  where codex's price-per-call discourages routine use.
- A locally-runnable option for offline or air-gapped contexts.

## Probe design

Three short tests for each model. Same input across all three
(codex baseline + Kimi + DeepSeek). Compare on terseness,
audit-friendliness, willingness to argue both sides, and
willingness to disagree with the framing.

### Test 1: Architectural decision (small)

Prompt: "Plan Ledger needs an event schema. Should
phase_started / phase_completed / phase_abandoned be three event
types, or one event type 'phase_status_changed' with a status
field? Argue both sides briefly."

Codex baseline already covered this kind of question tonight.
Compare reply shape.

### Test 2: Critique under uncertainty (medium)

Prompt: paste plan-ledger.md from /Users/mhcoen/proj/bob/design/.
Ask: "Three weakest assumptions in this design and how would each
fail in practice. Audit-friendly, terse."

This tests willingness to disagree with the framing and identify
gaps rather than just polishing the plan.

### Test 3: Multi-option weighing (large)

Prompt: "Plan Ledger's threshold rules can be expressed as code,
configuration, or both. Argue for each and recommend one. The
context is that thresholds will evolve as Bob's exploratory mode
matures."

This tests willingness to commit to a recommendation under
ambiguity, which is what we needed from codex on the F2.5a/b split.

## Commands to run

For Kimi:

```
pip install kimi-cli; \
kimi /login    # one-time, OAuth-style
```

After login, run prompts via:

```
echo "PROMPT TEXT HERE" | kimi --model kimi-for-code 2>&1 | tail -200
```

(kimi-cli's exact non-interactive flag set may differ; check
`kimi --help` after install. If no `-q`-style flag exists, the REPL
mode with stdin redirection works.)

For DeepSeek:

```
pip install deepseek-cli; \
export DEEPSEEK_API_KEY=...    # set once
```

Then:

```
deepseek -q "PROMPT TEXT HERE" -m deepseek-reasoner 2>&1 | tail -200
```

`deepseek-reasoner` is the thinking-mode model (formerly R1, now
covered by V4's thinking mode at the API level). `deepseek-chat`
is non-thinking.

(Note: DeepSeek's legacy model aliases retire July 24, 2026;
`deepseek-v4-pro` is the newer string.)

## Comparison criteria

For each test, record:

1. Reply length (terseness signal).
2. Whether it argued both sides (or just the side it preferred).
3. Whether it committed to a recommendation (for tests 1 and 3).
4. Whether it identified weaknesses in the framing (test 2).
5. Wall-clock latency.
6. Cost (tokens × per-million pricing).

## What we're looking for

A second-opinion actor that:

- Produces verdicts terse enough to act on without re-editing.
- Disagrees with the framing when warranted.
- Commits to recommendations rather than enumerating options.
- Costs less per call than codex if used routinely.

Codex remains the reference; this probe is to determine whether
adding Kimi or DeepSeek as a second actor is worth the wiring
work, or whether a single-actor codex setup is sufficient.

## Decision shape

After running the three tests on each model:

- If both Kimi and DeepSeek match codex on tests 1-3: add one
  (probably DeepSeek for cost; Kimi for context window) to
  Orchestra's actor registry. Use in council workflows.
- If only one matches: add that one.
- If neither matches: stay with codex; revisit when models update.

Cheaper does not justify lower-quality verdicts. The bar is "good
enough to ship a design decision without re-checking with codex."
