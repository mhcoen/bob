## Bugs

### plan_author judge verdict fails decision-consistency with `missing_ids`; no PLAN.md phase is ever generated

**Symptom**: `duplo` reaches phase generation; the `plan_author` loop runs
propose (opus) → review → judge (opus) to completion, then terminates
`ERROR: decision-consistency violation: missing_ids`. Zero phases written.
Reproduced on `/Users/mhcoen/proj/writer`, run `ed723f36e53b`.

**Root cause**: orchestra's `executor/criteria.py:check_decision_consistency`
requires the judge's `criteria_compliance[].criterion_id` set to exactly equal
the configured `CriterionDecl` ids. The `plan_author` binding
(`duplo/plan_author_role.py`) declares three criteria:
`task_granularity_5_to_15`, `batch_user_auto_discipline`,
`feat_fix_annotations_present`. But the judge prompt
(`duplo/workflows/templates/plan_author_judge.md`, fed the `_PHASE_SYSTEM`
directive via `query`) leads the judge to derive its own criteria from the
prose rules — in run `ed723f36e53b` it reported ten unrelated ids
(`phase_header_canonical_first_line`, `no_build_system_or_platform_preamble`,
`scaffold_scope_only`, …). Coverage check: the three configured ids are absent
from the judge's reported set → `missing_ids` (short-circuit precedence:
duplicates → missing → extras) → state exits via error outcome. The judge's
actual verdict was a correct `iterate` (it caught the proposer emitting a prose
paragraph before the canonical `## Phase phase_NNN:` header); the consistency
gate converts that normal iterate into a fatal ERROR, so the loop never gets to
feed feedback back to the proposer.

**Evidence**: transcript
`/Users/mhcoen/.orchestra/runs/ed723f36e53b/transcript.jsonl`, judge record:
`decision: "iterate"`, `criteria_compliance` contains ten ids, none matching the
three declared in the binding. The check's empty-config guard
(`if not configured: ok=True`) means a binding with zero criteria would pass
trivially — confirming the three declared criteria are exactly what arm a gate
the judge prompt does not cooperate with.

**Candidate fixes** (design decision required, do not pick silently):
1. Inject the three structured `CriterionDecl`s into the judge template so the
   judge evaluates and echoes exactly those ids. Aligns prompt with contract;
   keeps the criteria gate meaningful.
2. Remove the three criteria from the `plan_author` binding;
   `check_decision_consistency` no-ops on empty `configured`, so the gate passes
   and structural enforcement falls entirely to the `validate_plan_body`
   transform. Lighter, but discards the granularity/batch/annotation judge-level
   gate.
3. Derive the consistency check's expected id set from the same source the judge
   is shown, rather than from the binding.

**Note**: surfaced via a hand-assembled project (`.orchestra/config.json` +
`.orchestra/workflows/` supplied manually rather than by `duplo init`). Verify
whether a normal `duplo init` path also exhibits it — if init emits the same
three-criteria binding and the same judge template, it does, and this is not
specific to the hand-assembly.
