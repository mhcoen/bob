## Stage 23: Phase C Increment 14 - delete plan_document.py and global no-migrate gate
<!-- phase_id: phase_023 -->

- [ ] T-000196: Delete duplo/plan_document.py. Then run the single all-path end-to-end test: a full duplo run exercising initial generation, gap append, verification, contracts, bug append, and reauthor produces output that passes mcloop's real enforce_canonical with zero bob-plan migrate. Only after this test passes may the global claim duplo to mcloop runs with no migrate step be made.
- [ ] T-000197: Verify Stage 23 gate: plan_document.py deleted, no production imports, no behavior regressions, the all-path end-to-end no-migrate test green, no raw PLAN.md write sites remain in duplo except via bob_tools.planfile; duplo and bob-tools both green and pushed.
