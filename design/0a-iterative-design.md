# Spec: orchestra `iterative_design` pattern (roadmap item 0a)

## Status

Draft for iterative review. Author: Claude (Opus). Critic: Codex (or Kimi).
Critique file: `bob/design/0a-iterative-design-critique.md`.

## Purpose

A workflow pattern that runs an iterative cross-model design conversation
between two roles — **author** and **critic** — until either the critic
declares no serious issues remaining or a hard round cap is reached. Emits
the final design artifact plus the full transcript. Intended use: duplo's
design phase, mcloop's reauthor decision point, and any caller that wants
higher-quality design output than single-model invocation produces.

The pattern is *additive* to orchestra's existing pattern set (`single`,
`draft_then_adjudicate`, `propose_critique_synthesize`, council). It does
not modify or depend on them.

## Empirical claim being tested

Cross-model iterative design produces materially better designs than
single-model design, when "better" is measured by structural soundness,
edge-case coverage, and absence of contradictions — judged by a human
operator on real duplo design tasks against a hand-run baseline.

The pattern's value is empirical, not deductive. The mechanical test
verifies the machine does what it says; the empirical test verifies that
what it says is worth doing.

## Role model

Two roles, each bound at configuration time to a (model, prompt) pair:

- **author** — produces the initial design artifact and revises it across
  rounds in response to critic output. Stateful across rounds (sees full
  history).
- **critic** — reads the current artifact and the prior critique-and-
  revision history; emits a structured critique. Stateful across rounds.

Author and critic must be bound to **different models** (different
provider, or different model identifier within a provider). The pattern
refuses to start if both roles resolve to the same model. This is the
load-bearing assumption of the pattern; relaxing it silently would defeat
the purpose.

Models are referenced by symbolic name (`opus`, `codex`, `kimi`, etc.)
resolved through orchestra's existing model registry.

## State machine

States: `INIT`, `AUTHOR_DRAFTING`, `CRITIC_REVIEWING`, `CONVERGED`,
`CAPPED`, `ERROR`.

```
INIT
  └─→ AUTHOR_DRAFTING        (round 0: author produces initial draft)
        └─→ CRITIC_REVIEWING (round N, N ≥ 1: critic reads artifact + history)
              ├─→ CONVERGED        (critic emits ready=true, no severe issues)
              ├─→ CAPPED            (N == max_rounds, ready=false)
              └─→ AUTHOR_DRAFTING  (round N: author revises in response to critic_N)
                    └─→ CRITIC_REVIEWING (round N+1)
```

`ERROR` is reachable from any state on adapter failure, signal-parse
failure, or repeated structural malformation in role output.

A **round** is identified by an integer round number recorded in every
Turn. Round 0 contains exactly one Turn: the initial author draft. Round
N for N ≥ 1 contains exactly one critic review Turn and, if the loop
continues past that critic review (neither CONVERGED nor CAPPED), one
subsequent author revision Turn that responds to the critic review at
the same round number. The next critic review begins round N+1.

## Termination conditions

The pattern terminates in exactly one of:

1. **CONVERGED** — the critic's most recent output declares
   `ready: true` AND the `issues` list contains no entries of severity
   `structural` or `unrecoverable`. A critic output that declares
   `ready: true` while listing any `structural` or `unrecoverable` issue
   is an internal contradiction; the pattern treats this as malformed
   output (see Convergence signal contract below) and does not terminate
   CONVERGED.
2. **CAPPED** — `current_round == max_rounds` and the critic's most
   recent output declares `ready: false`. The author does *not* get a
   final revision after the capping critique. The artifact is whatever
   the author produced in the previous round.
3. **ERROR** — see below.

The caller receives the termination state explicitly in the result. A
`CAPPED` result is a real outcome, not a failure; the caller decides
whether to accept the artifact, escalate to a different pattern, or
escalate to human review.

## Convergence signal contract

The critic produces a structured output on every turn. Minimum required
fields:

```
{
  "ready": <bool>,
  "issues": [
    { "severity": "structural" | "behavioral" | "unrecoverable",
      "summary": <string ≤ 200 chars>,
      "detail":  <string> }
    ...
  ],
  "rationale": <string>
}
```

`ready: true` is valid only if `issues` is empty or contains only items
of severity `behavioral` (which the critic must justify-as-acceptable in
`rationale`). The pattern enforces this: a critic output that declares
`ready: true` while listing one or more `structural` or `unrecoverable`
issues is treated as malformed (internally contradictory) and routes
through the malformed-output retry path described below.

If the critic emits malformed JSON, omits required fields, or emits an
internally-contradictory `ready: true` (per the rule above), the pattern
issues one retry with a corrective prompt appended ("Your previous output
was malformed or internally contradictory. Re-emit using the schema; if
structural or unrecoverable issues remain, `ready` must be `false`.").
A second malformation transitions to `ERROR` with the malformed output
preserved in the transcript.

## Critic register lock

The critic prompt **must** include a register-locking clause:

> "Raise issues only at the following severities: structural (design will
> fail to meet its stated purpose), behavioral (design will produce
> incorrect or unexpected behavior under specified conditions),
> unrecoverable (design admits states from which no recovery is defined).
> Do **not** raise stylistic preferences, naming suggestions, scope
> expansions, alternative-design suggestions, or 'have you considered'
> commentary. If only such non-qualifying issues remain, emit
> `ready: true` and state in `rationale` that the design is structurally
> sound and any remaining concerns are below the severity threshold."

The author prompt must include a symmetric contract:

> "The design is considered done when the critic confirms no structural,
> behavioral, or unrecoverable issues remain. Stylistic and naming
> concerns are out of scope for this loop and will be handled at
> implementation time. Do not preemptively address such concerns."

Symmetry of the done-definition is essential. Without it, the loop fails
to converge — the author keeps polishing what the critic doesn't care
about, the critic keeps finding what the author considers out of scope.

## Hard round cap

`max_rounds` is a required configuration parameter. No default.
Recommended starting point for design tasks: 4. The pattern refuses to
start without an explicit value to force operator awareness of cost.

The cap counts critic reviews. `max_rounds = 4` permits one initial draft
+ four critic reviews + three author revisions = eight model invocations
maximum.

## State threading

Each role sees its full prior history on every turn. Concretely, the
author's context on round N includes: the seed input, the initial draft,
every critic critique (rounds 1..N, *including* critic_N which the
current revision is responding to), and every prior author revision
(rounds 1..N-1). The critic's context on round N includes: the seed
input, every prior version of the artifact, and its own prior critiques
(rounds 1..N-1).

This is expensive in tokens. The cost is intentional — both roles need
full history to reason about whether earlier issues were resolved or
merely re-introduced. No summarization or truncation in v1; revisit if
context overflow occurs in practice.

## Inputs

```python
run_iterative_design(
    seed_input: str,              # the design problem statement
    author_role: RoleBinding,     # (model_id, prompt_template, system_prompt)
    critic_role: RoleBinding,     # (model_id, prompt_template, system_prompt)
    max_rounds: int,              # hard cap; no default
    run_id: str | None = None,    # orchestra run_id; auto-generated if omitted
    transcript_dir: Path | None = None,  # default: orchestra's run dir
) -> IterativeDesignResult
```

## Outputs

```
IterativeDesignResult:
  termination: "CONVERGED" | "CAPPED" | "ERROR"
  rounds_completed: int
  final_artifact: str          # author's most recent output
  transcript: list[Turn]       # full ordered history
  run_id: str
  transcript_path: Path        # JSONL file with one Turn per line
  error: ErrorRecord | None    # populated iff termination == "ERROR"

Turn:
  round: int
  role: "author" | "critic"
  model_id: str
  prompt: str
  output: str                  # raw model output
  parsed: dict | None          # structured form for critic turns
  timestamp: str
  tokens_in: int
  tokens_out: int
```

The transcript is the primary artifact for empirical evaluation and for
downstream M1 FailureRecord lineage. It is written incrementally (one
Turn appended per role completion), not at end-of-run, so that crash
recovery preserves partial progress.

## Configuration binding

In `~/.orchestra/config.json` or project-local override:

```json
{
  "roles": {
    "design": {
      "pattern": "iterative_design",
      "author":  { "model": "opus",  "prompt": "design_author.md" },
      "critic":  { "model": "codex", "prompt": "design_critic.md" },
      "max_rounds": 4
    }
  }
}
```

A caller (duplo, mcloop) requests a role by name
(`orchestra.run_role("design", seed_input=...)`); orchestra resolves to
the bound pattern + parameters.

The `prompt` field is a filename resolved against orchestra's prompt-
template directory. Prompts are versioned alongside the orchestra package
so that prompt changes are part of orchestra's commit history.

## Error handling

`ERROR` termination on any of:

- Author or critic adapter raises (timeout, rate limit, network).
- Critic emits malformed structured output twice consecutively (one retry
  permitted).
- Same-model binding detected at start (refuse to start; configuration
  error, not runtime error).
- `max_rounds` ≤ 0 (refuse to start).

On adapter failure, the pattern preserves the transcript up to the
failure point and surfaces the underlying error in `ErrorRecord`. No
automatic retry of the entire loop; the caller decides whether to
restart.

This shape is deliberately aligned with the M1 FailureRecord schema
(Wave 2 in the roadmap). When M1 lands,
`IterativeDesignResult.error` becomes a `FailureRecord` with
classification `iterative_design.<reason>`.

## Test plan

**Mechanical tests (orchestra integration test suite):**

1. *Round threading.* Mock author and critic that emit canned outputs
   over 3 critic rounds with `ready: false`, `ready: false`,
   `ready: true`. Assert: terminates `CONVERGED` at round 3; transcript
   contains all 6 turns in order — (a) initial author draft (round 0),
   (b) critic 1 (ready=false), (c) author revision 1, (d) critic 2
   (ready=false), (e) author revision 2, (f) critic 3 (ready=true). No
   author revision follows the converging critique.
2. *Cap enforcement.* Mock critic always emits `ready: false`. Assert:
   terminates `CAPPED` at exactly `max_rounds` critic turns, no author
   revision after the final critique.
3. *Same-model rejection.* Bind author and critic to the same model.
   Assert: refuses to start with explicit error.
4. *Malformed critic output, recoverable.* Mock critic emits malformed
   JSON on round 1, then valid `ready: true` on round 1 retry. Assert:
   terminates `CONVERGED`, transcript records the malformed turn and the
   retry.
5. *Malformed critic output, fatal.* Mock critic emits malformed JSON
   twice. Assert: terminates `ERROR`, transcript preserved, error record
   populated.
5b. *Internally-contradictory `ready: true`, recoverable.* Mock critic
    emits `ready: true` with a `structural` issue listed on round 1,
    then re-emits `ready: false` with the same issue on retry. Assert:
    pattern does not terminate `CONVERGED` on the contradictory output;
    proceeds to the round 1 author revision (in response to the
    now-valid critic_1); transcript records both the contradictory turn
    and the retry as round-1 critic turns, followed by the round-1
    author revision.
5c. *Internally-contradictory `ready: true`, fatal.* Mock critic emits
    `ready: true` with a `structural` issue listed, twice consecutively.
    Assert: terminates `ERROR`, transcript preserved, error record
    populated.
6. *Adapter failure.* Mock author adapter raises on round 2 revision.
   Assert: terminates `ERROR`, transcript preserved through round 1
   author turn.
7. *Transcript incrementality.* Crash the process mid-round (after a
   turn write, before the next turn). Assert: transcript file on disk
   contains all completed turns.

**Empirical test (one task, judged by operator):**

8. Pick one real duplo design task that the operator would otherwise
   hand-run through Opus↔Codex. Configure `design` role per the example
   above. Run the pattern. Compare the resulting `final_artifact` to the
   artifact the operator produces in a parallel hand-run (or to a recent
   prior hand-run on a comparable task). Judgment is binary: is the
   orchestra-hosted artifact comparable-or-better, or worse? A "worse"
   outcome triggers a postmortem before the pattern is wired into any
   caller.

The empirical test is the gate. Mechanical tests passing without the
empirical test passing is not sufficient evidence to ship.

## Out of scope for v1

- More than two roles (no triad, no committee).
- Branching critics (multiple critics in parallel).
- Streaming output to caller during the loop (return-only at termination).
- Cost budgets (token or dollar caps as termination conditions). The
  round cap is the only cap.
- Caching or memoization across runs.
- Resumption of an interrupted loop. A crashed loop is restarted from
  scratch; the transcript of the crashed run remains as historical
  evidence.

All of the above are reasonable extensions. v1 deliberately excludes them
to keep the contract small and the empirical test interpretable.

## Open questions

1. **Prompt versioning surface.** Are prompts version-controlled inside
   orchestra's package, or in a separate prompts repo? Inside orchestra
   is simpler; separate is cleaner for non-engineers to edit.
   Recommend: inside orchestra for v1.
2. **Role bindings beyond "design."** The roadmap mentions reauthor as
   another candidate caller. Should reauthor get its own role binding
   (separate prompts, possibly different `max_rounds`), or share the
   design binding? Recommend: separate role from day one. Shared bindings
   cause prompt drift.
3. **What does the critic see of orchestra's own structured output
   schema?** Does the critic prompt mention that orchestra will reject
   malformed JSON, or is that hidden from the critic's reasoning surface?
   Recommend: tell the critic explicitly; hiding it produces brittleness
   when models reason about format.
