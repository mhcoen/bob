<!-- bob-plan-format: 1 -->

## Bugs

- [ ] T-000001: In `duplo/workflows/templates/plan_author_judge.md`, inject the three configured criterion ids and their descriptions from `duplo/plan_author_role.py:PLAN_AUTHOR_CRITERIA` (`task_granularity_5_to_15`, `batch_user_auto_discipline`, `feat_fix_annotations_present`) into the judge prompt, and instruct the judge to emit `criteria_compliance` with exactly one entry per configured criterion using those exact ids and no others (do not invent ids from the `_PHASE_SYSTEM` prose rules), so `orchestra/executor/criteria.py:check_decision_consistency` no longer fails with `missing_ids`. Thread the ids/descriptions from the binding rather than hardcoding a second copy of the strings so the template and binding cannot drift.
- [ ] T-000002: Add a regression test that a `plan_author` judge verdict reports exactly the three configured criterion ids, and that the loop survives an `iterate` verdict without raising `missing_ids`. Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy .` and keep them green.
