# Deferred Design Backlog

Append-only index of deferred design ideas; this is not the design docs themselves, and each entry is one idea.

## 2026-05-16 - Deterministic bugfile layer (schema incl. temporal/provenance fields)

Why it matters: BUGS.md keeps getting LLM-corrupted and needs the same deterministic-access treatment as planfile; timestamps/provenance are the forcing function.

Provenance pointer: parked DEFERRED stage at the end of `/Users/mhcoen/proj/bob-tools/PLAN.md`.

## 2026-05-16 - Executable specification for continuous rebuilding of Bob

Why it matters: continuously rebuilding Bob via Duplo/mcloop/vroom only converges if each rebuild is measured against a fixed external executable spec (behavioral/property tests encoding intent that no rebuild may weaken); without it, continuous rebuild risks being a non-converging random walk. Distilled structural-invariants/lessons document (mined from resolved-bug history) is part of that spec.

Provenance pointer: Claude conversation of 2026-05-16 on the bob ecosystem; to be expanded into a full design doc in `/Users/mhcoen/proj/bob/design/` when the user surfaces it from this backlog.

## 2026-05-16 - Unexplained `.duplo/` directory in duplo's own repo root

Why it matters: duplo has reportedly never been run on duplo, yet duplo's repo root contains a `.duplo/` working directory; combined with template-residue SPEC.md this caused duplo to accidentally self-target when run from its own root. The `.duplo/` presence is unexplained state that contradicts the assumption duplo was never self-run, and an unguarded wrong-cwd `duplo` invocation can self-build duplo from an empty spec.

Provenance pointer: Claude/Codex investigation of 2026-05-16 (duplo SPEC.md/ref deletion task); to be investigated when surfaced - determine why `.duplo/` exists in duplo root and whether duplo should guard against self-targeting from its own repo.

## 2026-05-16 - mcloop graceful recovery from transient API/infrastructure failures (distinct from genuine task failure)

Why it matters: mcloop's current batch/task retry (observed: 3 attempts, ~50s, then stop with "Remaining: N tasks") treats transient infrastructure errors and genuine task failures identically. During an Anthropic API incident on 2026-05-16, Stage-3.1 burned all 3 attempts on `API Error: 500 Internal server error` in under a minute and halted, forcing a manual mcloop restart once the incident cleared. Transient classes (HTTP 5xx, 429 rate-limit, connection reset/timeout, provider incident) are frequent, free to wait out, and should NOT consume the task's genuine-failure budget. Design: classify failures into transient-infrastructure vs. genuine-task; on transient, back off (exponential, capped, with a sane max total wait and a hard ceiling so it cannot hang forever) and retry without counting against the task failure limit; on genuine task failure, keep today's bounded-retry-then-stop behavior. The classification is the substance of the feature - a naive "always wait and retry" would mask real task failures as endless retries, so the transient-vs-genuine discriminator must be explicit and source-grounded. Also covers the NOTES.md DeepSeek/Sonnet fallback chain, which exhibited the same brittle all-providers-failed behavior under the same incident.

Provenance pointer: Claude conversation of 2026-05-16 (bob ecosystem); observed during the Anthropic API incident that failed bob-tools Stage-3.1. This is a deliberate mcloop design item to be scheduled; related but distinct from the already-filed mcloop/BUGS.md entries (claude-CLI argv-too-long fallback defect; CLAUDE.md reconcile retry-exhaustion) - those are deterministic bugs, this is a resilience design change.
