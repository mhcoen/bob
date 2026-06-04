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

**Chosen fix**: inject the three structured `CriterionDecl`s from the
`plan_author` binding into the judge template
(`duplo/workflows/templates/plan_author_judge.md`) so the judge evaluates and
echoes `criteria_compliance[].criterion_id` for exactly those three ids
(`task_granularity_5_to_15`, `batch_user_auto_discipline`,
`feat_fix_annotations_present`) and no others. The judge must be instructed to
emit one compliance entry per configured criterion, using those exact ids, and
must not invent ids from the `_PHASE_SYSTEM` prose rules. This aligns the prompt
with the contract `check_decision_consistency` enforces, keeps the
granularity/batch/annotation judge-level gate meaningful, and stops the
`missing_ids` failure. The three criterion ids and their descriptions are the
source of truth in `duplo/plan_author_role.py` (`PLAN_AUTHOR_CRITERIA`); thread
those into the judge prompt rather than hardcoding a second copy of the strings,
so the template and the binding cannot drift.

**Rejected alternatives** (for the record, do not implement): removing the three
criteria from the binding (discards the judge-level gate); deriving the check's
expected id set from the judge's own output (defeats the gate's purpose and
touches orchestra rather than duplo).

**Note**: surfaced via a hand-assembled project (`.orchestra/config.json` +
`.orchestra/workflows/` supplied manually rather than by `duplo init`). Verify
whether a normal `duplo init` path also exhibits it — if init emits the same
three-criteria binding and the same judge template, it does, and this is not
specific to the hand-assembly. After fixing, add a regression test that a
`plan_author` judge verdict reports exactly the three configured criterion ids
and that the loop survives an `iterate` verdict without raising `missing_ids`.

### Tasks

- [ ] In `duplo/workflows/templates/plan_author_judge.md`, inject the three configured criterion ids and their descriptions from `duplo/plan_author_role.py:PLAN_AUTHOR_CRITERIA` (`task_granularity_5_to_15`, `batch_user_auto_discipline`, `feat_fix_annotations_present`) into the judge prompt, and instruct the judge to emit `criteria_compliance` with exactly one entry per configured criterion using those exact ids and no others (do not invent ids from the `_PHASE_SYSTEM` prose rules), so `orchestra/executor/criteria.py:check_decision_consistency` no longer fails with `missing_ids`. Thread the ids/descriptions from the binding rather than hardcoding a second copy of the strings so the template and binding cannot drift.
- [ ] Add a regression test that a `plan_author` judge verdict reports exactly the three configured criterion ids, and that the loop survives an `iterate` verdict without raising `missing_ids`. Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy .` and keep them green.
