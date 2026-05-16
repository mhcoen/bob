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
