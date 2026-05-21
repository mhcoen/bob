## Stage 22: Phase C Increment 13 - make plan_document.py callerless
<!-- phase_id: phase_022 -->

- [ ] T-000194: In duplo, remove the last production imports of duplo/plan_document.py by moving remaining callers (reauthor.py, reauthor_assemble.py) onto bob_tools.planfile. Do not delete the module yet. Run the behavior-preservation checks from the v4 ledger and prove every retained guarantee still holds against bob-tools paths.
- [ ] T-000195: Verify Stage 22 gate: rg plan_document across duplo and tests shows no production callers; behavior-preservation tests pass; duplo ruff and pytest green; bob-tools still green.
