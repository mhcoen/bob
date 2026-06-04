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
- [ ] \nAdd the required `category` argument to the `Feature(name=\"X\", description=\"d\")` constructor call at /tmp/claude-501/exp_e2e.py:72 (e.g. `category=\"c\"`), matching the valid `feats` construction earlier in the file.\n
- [ ] \nIn duplo/workflows/templates/plan_author_jud"}},"session_id":"96138470-c745-416a-9bda-70cf7f91d78b","parent_tool_use_id":null,"uuid":"857715c0-39f8-4c48-b508-bf058bf1eeaf"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"ge.md, inject the three configured criterion ids from the plan_author binding (`task_granularity_5_to_15`, `batch_user_auto_discip"}},"session_id":"96138470-c745-416a-9bda-70cf7f91d78b","parent_tool_use_id":null,"uuid":"9c3f6cf3-9c90-4549-a7f4-48f26f2d0d25"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"line`, `feat_fix_annotations_present`) and instruct the judge to emit `criteria_compliance[].criterion_id` for exactly those ids, so"}},"session_id":"96138470-c745-416a-9bda-70cf7f91d78b","parent_tool_use_id":null,"uuid":"9141c218-df95-4a50-a1da-78b73a88c2e5"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":" orchestra's check_decision_consistency stops failing with missing_ids.\n
- [ ] \nIn plan_author_adapter.py:236 CAPPED branch, decide convergence from the run's recorded final-round `valid"}},"session_id":"a0ce85b8-0581-4c1c-ad38-bc3fc9a3542b","parent_tool_use_id":null,"uuid":"5bdb9f12-faf8-47e8-87b0-de0cf9f8e6cd"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"ation_ok`/`validation_feedback` gate artifacts (returning the proposal the gate actually passed) instead of re-running `typed_plan_from_synthesiz"}},"session_id":"a0ce85b8-0581-4c1c-ad38-bc3fc9a3542b","parent_tool_use_id":null,"uuid":"91af4199-96e9-4b55-b783-d33fc7daa7d2"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"er_text` on `result.final_artifact`, since `_select_final_artifact` returns the latest `proposal` which may differ from the gate-validated body and"}},"session_id":"a0ce85b8-0581-4c1c-ad38-bc3fc9a3542b","parent_tool_use_id":null,"uuid":"f7e49c69-f425-4e55-992f-50cebcf251ad"}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":" causes a converged plan to be wrongly raised as `PlanAuthorCappedError`.\n
- [ ] \nIn the PlanAuthorCappedError handler (exp_dbg.py:59), read the transcript from the exception's structured `exc.transcript_path` attribute instead of string-parsing it out of `str(exc)`, and guard the read with `if exc.transcript_path.exists()` before `read_text()` to tolerate a missing/cleaned-up transcript.\n
