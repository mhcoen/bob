# Model Invocation: Axes, Trade-offs, and Failure Handling

*Design rationale for how mcloop/duplo select, invoke, and fall over between
LLM coding models. Captures a design discussion held 2026-06-06. This is a
rationale document, not a spec: it records the conceptual frame, the measured
ground truth as of this date, and a set of candidate changes that are
**explicitly deferred**. The governing discipline is skeleton-first: a reliable,
robust running system before any optimization of model selection.*

---

## 1. Why this document exists

A build run surfaced confusion in how the fallover chain
(`opus → codex/gpt → kimi → deepseek`) treats model failures. Investigating it
revealed that several genuinely different concerns had been collapsed onto a
single ordered list. This document separates those concerns into independent
axes, records what is empirically known about how each model is currently
invoked, and states the failure-handling model that follows. It is also a
source for the eventual Bob paper, where the axes of model comparison and their
trade-offs are a point worth making carefully, because the common framing in the
field ("which model is best") is the wrong question.

## 2. The central claim: capability is not an ordering

The field habitually asks "what is the best model," implying a total order, or
retreats to "best model for a task category," implying a stable partial order.
Both are rejected here. Models cluster into coarse **tiers**, but within a tier
there is no reliable capability ordering: observed differences are dominated by
prompt, context-window size, time-of-day serving variability ("nerfing"), and
release churn — none stable enough to encode as routing policy. The most that
can be said is "a good model for a particular task at a particular time," and
that is not knowable in advance.

This is why claims of fine-grained per-model task-specialization (e.g. routing
across many models each "best" at something) are treated as unsupported: in
practice such differences are usually prompt effects, not stable model
properties. We have no data establishing problem-category-to-model strengths,
and we are not going to manufacture it speculatively. The coarse tiering below
is the only capability structure asserted.

**Tiers (capability, coarse):**
- **Top tier:** Opus, GPT. Unordered relative to each other.
- **Lower tier:** Kimi, DeepSeek. Treated as the same capability level; the
  author draws no reliable distinction between them on capability.

Preference *within* the top tier is driven by economics and availability, not
capability: a far more generous Opus subscription versus a small GPT
subscription means Opus is preferred for usage economy, with GPT reserved for
when Opus is unusable or for genuinely hard problems — an economic choice, not a
capability ranking.

## 3. The axes

Model invocation decisions live on several independent axes. The original
fallover chain failed because it projected all of them onto one ordered list.

1. **Availability** — is a model reachable and permitted *right now*? Splits
   into:
   - **(A) Unreachable** — network down, transport error, 5xx. Nothing was
     meaningfully attempted.
   - **(B) Not permitted** — quota/rate-limit/subscription cap exhausted. The
     model is capable and reachable but administratively unavailable for some
     window (hours, or a week).
   This is a fact about the world, not about the task.

2. **Cost** — subscription economy. Generous Opus subscription → prefer Opus;
   scarce GPT → reserve it. Drives both the preference within a tier and the
   choice of *starting* rung (see section 5).

3. **Speed** — wall-clock latency. Distinct from capability and from cost.
   Within the lower tier, Kimi and DeepSeek are the same capability level but
   Kimi is much faster; DeepSeek is very slow, and no faster-serving access
   path has been found through the Claude Code interface (examined and
   confirmed). Speed couples to the timeout/kill behavior (section 6): a slow
   model is more likely to be killed by the session wall-clock and have that
   misread as a failure.

4. **Effort / reasoning level** — how hard a model is told to think
   (low/medium/high/max/xhigh; Ultrathink as a prompt-level lever). Orthogonal
   in principle from *thinking-trace visibility* (see section 4). Moves cost,
   speed, and per-attempt capability simultaneously. Must be known and logged
   before any failure can be interpreted: a failure at minimum effort and a
   failure at maximum effort are different evidence.

5. **Capability tier** — the coarse, unordered-within-tier structure of
   section 2. Not a fine-grained axis; deliberately the weakest claim in the
   document.

## 4. Effort vs. thinking are different knobs

A correction worth recording, because it is a common confusion: *reasoning
effort* and *thinking-trace visibility* are distinct mechanisms, and disabling
one does not necessarily minimize the other.

- **Effort** (low/medium/high/max/xhigh) sets how much reasoning work the model
  does. On Claude models with adaptive reasoning, effort is the primary control
  over how much thinking happens.
- **Thinking trace** (`MAX_THINKING_TOKENS`, `DISABLE_INTERLEAVED_THINKING`) is
  the older fixed-budget extended-thinking mechanism — the emitted reasoning.

Whether the two are independent in practice is **model-dependent**. For
architectures where the thinking tokens *are* the substrate of reasoning,
zeroing the thinking budget also reduces effort. For architectures where the
trace is a rendering of reasoning that happens regardless, suppressing the trace
saves latency/tokens without lowering effort. On Anthropic models, Opus 4.7+
always use adaptive reasoning (the fixed-budget mode and
`DISABLE_ADAPTIVE_THINKING` do not apply); on Opus/Sonnet 4.6 the fixed budget
can be re-enabled. For non-Anthropic models reached through the Claude Code
interface, whether a forwarded effort setting is honored by the third-party
endpoint is **unverified** and is a deferred tuning question.

Consequence for our Kimi tier: it runs with the thinking trace disabled
(`MAX_THINKING_TOKENS=0`, `DISABLE_INTERLEAVED_THINKING=1`). This suppresses the
trace; it does **not** necessarily set Kimi's effort to its floor. Earlier notes
that conflated the two were wrong.

## 5. Measured ground truth (2026-06-06)

Established by reading config and source and by controlled probe calls. The argv
mcloop builds carries **no** effort flag for any tier; effort is set entirely by
**config inheritance**, and it is high — contradicting an earlier assumption
that effort was unset/minimal.

| Tier | CLI | How invoked (argv) | Effort control | Sent in argv | Resolves to | Honored? |
|------|-----|--------------------|----------------|--------------|-------------|----------|
| opus | claude | `claude -p … --model opus` (+ stream-json, verbose, allowedTools) | `--effort` flag; `~/.claude/settings.json` `effortLevel`; `CLAUDE_EFFORT` env | none → inherits settings | **xhigh, thinking ON** (`effortLevel: xhigh`, `alwaysThinkingEnabled: true`) | `--effort` accepted; live honoring unverified |
| gpt-5.5 | codex | `codex exec --full-auto --model gpt-5.5` | `-c model_reasoning_effort`; `~/.codex/config.toml` | none → inherits config | **high** | **accepted + verified honored** (reasoning tokens emitted on probe) |
| kimi-k2.6 | claude (Moonshot endpoint via shell-fn env) | `claude -p … --model kimi-k2.6` + `DISABLE_INTERLEAVED_THINKING=1 MAX_THINKING_TOKENS=0` | `--effort` parses; thinking forced off | none; thinking-off | **thinking DISABLED by design**; effort = endpoint default | `--effort` parsed; honoring by Moonshot unverified |
| deepseek-v4-pro | claude (DeepSeek endpoint via shell-fn env) | `claude -p … --model deepseek-v4-pro` | `--effort` parses; no thinking override | none | provider default | unverified |

**Auth/provider mechanism (recorded so it is not re-litigated):** Kimi and
DeepSeek are reached by *redirecting* Claude Code at a third-party endpoint —
the shell functions set `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` (the
provider's own key), and a per-provider `CLAUDE_CONFIG_DIR`. These paths do
**not** consult `ANTHROPIC_API_KEY` at all; it is irrelevant to them. Only the
default Opus path hits Anthropic's endpoint and therefore cares whether
`ANTHROPIC_API_KEY` is present (API billing) or absent (subscription auth);
mcloop unsets it for that path. A billing error seen during probing was an
artifact of a bare probe shell that did not replicate the runtime environment,
not a runtime defect.

**Retry context (established by source read):** retries are *not* blind. All
three retry surfaces (single-task, batch, audit-fix) feed the prior failure
forward — the failing check output becomes `prior_errors`, ruled-out approaches
become an explicit do-not-repeat list, and the retry prompt is reframed as a
bug-investigation leading with the prior errors. The one limitation: the
forwarded context is a tail of the last ~50 lines of check/session output, not a
structured diff of *what the previous attempt changed*. Adequate for surfacing
*what broke*; weaker for conveying *what was done*.

## 6. Failure-handling model

The fallover chain is, properly understood, an **availability** mechanism. Its
defect was firing on capability failures and on inconclusive timeouts as if they
were availability events. The corrected model classifies every failure into one
of three classes and responds differently to each.

- **(A/B) Availability** — unreachable, or quota/rate-limit/cap. **Response:**
  substitute *any reachable, permitted* model to keep the skeleton running —
  cross-tier descent is acceptable here because it is triage, not a capability
  judgment, and a running lower-tier model beats a halted build. Within the
  lower tier, prefer Kimi over DeepSeek on **speed**. **Restore** to the
  cost-preferred model when it becomes available again. Work produced by a
  lower-tier substitute under availability-descent should be **marked as such**,
  so it is auditable and not silently indistinguishable from top-tier work.

- **(C) Capability failure** — the model *ran*, produced a checkable artifact,
  and that artifact failed checks. **Response, in order of cost:**
  1. **Same model, more effort** — the cheapest escalation; bump effort before
     changing models. (Note: top tier is already at xhigh/high by config, so
     this rung has less headroom there than at a lower starting rung.)
  2. **Same-tier lateral** — e.g. Opus ↔ GPT. A peer, not an upgrade (no
     ordering is claimed); spends scarcer quota for a genuinely different
     attempt with different failure modes. Debatable but not unreasonable.
  3. **Cost-economy climb** — if a *cheaper* rung was chosen as the starting
     point (see section 7), escalate up to a more expensive tier.
  **Never** descend a tier on a capability failure: a slower, same-or-weaker
  model is unlikely to fix what a stronger peer could not, and is slow on top of
  it. When the available top tier is exhausted, **stop and surface to a human** —
  do not burn lower tiers hoping for a capability win.

- **(Inconclusive) Timeout / kill** — a wall-clock or `-3`/`-2` kill, truncation,
  or any termination without a checkable artifact. **Response:** treat as
  *neither* availability nor capability evidence. Do **not** count toward
  capability escalation; do **not** trigger model substitution. Critically, a
  slow model (DeepSeek) inflates inconclusive kills, which is an argument for
  deprioritizing it on the speed axis, not for concluding it "failed."

**The classification predicate is the linchpin.** A model call's return is not
self-classifying, and mcloop often cannot tell A from B from C at the moment of
failure. The asserted rule: **a failure may advance capability handling (C) only
when there is positive evidence the model ran and produced a checkable artifact
that failed checks.** Absent that evidence — unreachable, rate-limited, killed
for time, truncated — it is availability (A/B) or inconclusive by default, never
"the model isn't capable enough." This makes capability-escalation require proof
of capability failure and routes everything else to the availability or
inconclusive path. Signals, in rough order of reliability: transport/connection
errors and 5xx → A; explicit quota/rate-limit response bodies → B; a clean
completion whose artifact fails *checks* → C; a wall-clock/`-3` kill →
inconclusive.

## 7. The cost/speed/effort "ladder" — reconciled

An apparent contradiction in the discussion ("there is no capability ladder to
climb" vs. "maybe we can get away with a weaker model on a good plan") resolves
as follows: there is no *capability* ladder, but there is a legitimate
**cost/speed** ladder conditioned on plan quality. If duplo has produced a
plan of small, atomic, design-free steps, a cheaper/faster model is a reasonable
*starting* rung, with escalation to a more expensive model only on a genuine
C-failure. The escalation is economic, not a capability ranking. Hypothesis to
be validated, not assumed: *the better the planning, the weaker the coding model
one can get away with.* Vibe-coding from a thin spec wants a top-tier model from
the first attempt; a fully decomposed plan may not.

## 8. Deferred — do not optimize yet

Recorded so future work knows these were conscious choices, not oversights. None
of the following is to be built until the skeleton is reliable and robust.

- **Dynamic / per-task model selection** and any problem-category-to-model
  routing. We lack the data; differences are largely prompt effects; the
  landscape shifts with time-of-day and releases. Explicit rabbit-hole, avoided.
- **Effort tuning** — *which* effort level to use per tier, whether to vary it,
  the exact escalation rungs. Knowing and logging the effort level is skeleton;
  choosing values is tuning.
- **Prompt-space exploration / prompt engineering** beyond the existing
  failure-context feedback. Not yet.
- **Cost-optimized starting rung** (section 7) — the plan-quality-conditioned
  start-cheap policy is a deferred optimization; the unblocking work is fixing
  the availability/capability conflation first.

## 9. Candidate changes implied by this analysis (status noted per item)

Recorded as candidates. Three coverage-gate fixes in the same family were
implemented this session (marked DONE); the rest remain deferred.

1. **Failure classification layer** — implement the A/B/C/inconclusive predicate
   of section 6 and demote the fallover chain to availability-only, so capability
   failures escalate (and stop), availability events substitute-and-restore, and
   timeouts do neither. *The core structural fix. DEFERRED — adjacent to the
   model-selection rabbit hole (section 8); wants its own SPEC and blast-radius
   assessment.* A build run on 2026-06-07 reproduced the symptom this would fix:
   a gate failure that no model could resolve drove a pointless
   opus→kimi→deepseek descent, exactly the capability/availability conflation
   described in section 6.
2. **Sub-session stall guard** — DONE (commit `5764f83d`). Detects non-progress
   within a single agent session keyed on **repeated identical tool-call
   signatures** (robust to noisy/trimmed/timestamped output), aborting at
   `STALL_REPEAT_THRESHOLD=4` consecutive identical signatures with a distinct
   `STALL_EXIT_CODE=-200` (out of the POSIX signal range so it cannot collide
   with a signal-killed child's passthrough returncode). Only parsed `tool_use`
   signatures feed the tracker, so interleaved text/partial/tool_result chatter
   does not reset the counter. Claude stream shape implemented; **codex is an
   explicit known gap** (`parse_tool_signatures` returns `[]` for non-claude
   backends) — a follow-up once codex's stream-json schema is verified. The
   optional "make permission denials non-retryable" idea remains unbuilt.
3. **Effort observability** — make the effort/thinking level explicit and
   **logged** per invocation rather than relying on silent config inheritance,
   so failures are interpretable. Specifying (the ability to set it) is distinct
   from tuning (choosing the value). *DEFERRED.*
4. **Richer retry context** — feed the prior attempt's **diff**, not only a
   ~50-line output tail, into the retry prompt. *DEFERRED.*
5. **Coverage-gate edge cases (same family) — two DONE, one DEFERRED:**
   - **Dotted-name collision** — DONE (commit `f6fbbb4e`). The import-graph
     module→file map used last-write-wins, so a package and a sibling module
     sharing a dotted name (`pkg/lint.py` and `pkg/lint/__init__.py` → `pkg.lint`)
     silently clobbered each other, making dependent-test discovery
     nondeterministic and able to drop a real dependent test. `_build_module_index`
     now fails closed with a verdict naming both paths; per-source failure reasons
     are now threaded into the gate output instead of one generic line.
   - **Untracked-file invisibility** — DONE (commit `9ac16b87`). The gate runs
     before the per-task commit, so a file the editor just created is still
     untracked; `changed_new_lines` diffed the committed baseline with
     `git diff <baseline> -- <src>`, which omits untracked files, yielding an
     empty changed-line set treated as a hard failure. It is now untracked-aware
     (mirrors `_changed_files`): an untracked file is modeled as a new-file diff
     (lines 1..N). Untracked files are made **visible, not exempt** — coverage is
     still required, and an untracked empty file still fails.
   - **Behavioral-line free-pass (gate-hardening)** — DEFERRED. `verify_change_covered`
     passes when `covered = changed & executed` is non-empty, i.e. when *any*
     changed line executed. For a new file, import/`def`/module-level lines
     trivially execute on import, so a file can pass while its function bodies
     go untested. This is a pre-existing property of the "any changed line
     executed" semantics, not introduced by the untracked fix, but the untracked
     fix makes it reachable for new files. Tightening the gate to require
     *behavioral*-line coverage is a consequential gate-policy change (it would
     newly fail an unknown number of currently-passing tasks across all projects)
     and needs its own SPEC, blast-radius assessment, and a behavioral-line
     classifier — the same deliberate treatment the stall guard received, not a
     rider on a bugfix. Pinned by `test_..._import_executed_passes_DOCUMENTED_LIMITATION`
     in `tests/test_coverage_verify.py`.
6. **Incidental, independent of the above (DEFERRED):**
   - codex `--full-auto` is deprecated (warns, steers to
     `--sandbox workspace-write`); `_build_command` still emits it.
   - Config dir-name discrepancy: mcloop config references a Kimi
     `CLAUDE_CONFIG_DIR` that may not match the working `~/.claude-kimi-fast`
     used by the shell function — verify the Kimi tier reads the intended dir.
   - **duplo acceptance emission** — nearly every task this session logged
     "Task has no declared acceptance; using legacy inference," so duplo is not
     emitting declared acceptance predicates for most leaf tasks and mcloop
     falls back to coverage inference. duplo's `acceptance.py` is meant to attach
     acceptance on every leaf implementation task. Separate from (and not fixed
     by) the gate fixes above. Fixing duplo's emission does not replace fixing
     mcloop's legacy fallback — both must work, since hand-authored and legacy
     plans will always exist.

---

*End of rationale. Parameters and policy values are intentionally absent; this
records structure and measured fact only.*
