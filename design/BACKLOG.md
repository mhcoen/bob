# Deferred Design Backlog

Append-only index of deferred design ideas; this is not the design docs themselves, and each entry is one idea.

## 2026-05-16 - Deterministic bugfile layer (schema incl. temporal/provenance fields)

Why it matters: BUGS.md keeps getting LLM-corrupted and needs the same deterministic-access treatment as planfile; timestamps/provenance are the forcing function.

Provenance pointer: parked DEFERRED stage at the end of `/Users/mhcoen/proj/bob-tools/PLAN.md`.

## 2026-05-16 - Executable specification for continuous rebuilding of Bob

Why it matters: continuously rebuilding Bob via Duplo/mcloop/vroom only converges if each rebuild is measured against a fixed external executable spec (behavioral/property tests encoding intent that no rebuild may weaken); without it, continuous rebuild risks being a non-converging random walk. Distilled structural-invariants/lessons document (mined from resolved-bug history) is part of that spec.

Provenance pointer: Claude conversation of 2026-05-16 on the bob ecosystem; to be expanded into a full design doc in `/Users/mhcoen/proj/bob/design/` when the user surfaces it from this backlog.
