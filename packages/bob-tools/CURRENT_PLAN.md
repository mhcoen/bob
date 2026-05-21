## Stage 18: Phase C Increment 9 - migrate duplo fresh and initial phase generation, and council
<!-- phase_id: phase_018 -->

Stages 18-22 modify the duplo repo, not bob-tools. Each stage commits and pushes in /Users/mhcoen/proj/duplo with scoped commits. The bob-tools repo must remain green throughout.

- [ ] T-000186: In duplo, replace fresh and initial PLAN.md generation so the model returns structured task data, not PLAN.md markdown. Build a typed bob_tools.planfile Plan, validate with validate_plan constructed=True, persist only via bob_tools.planfile save. Migrate council.author_phase_plan to return structured data and delete council._validate_canonical_plan_markdown (replaced wholesale by constructed validation and assert_mcloop_canonical). Remove the model-instruction prompt text that tells the model to emit PLAN.md markdown. No markdown write_text of plan content may remain on this path.
- [ ] T-000187: Verify Stage 18 gate: a representative duplo fresh-generation run produces a PLAN.md equal to assert_mcloop_canonical output that passes mcloop's real enforce_canonical in an integration test; duplo tests that asserted old markdown behavior rewritten to assert typed behavior, not deleted; duplo ruff and pytest green; bob-tools still green.
