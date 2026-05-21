## Stage 21: Phase C Increment 12 - migrate duplo reauthor
<!-- phase_id: phase_021 -->

- [ ] T-000192: In duplo, migrate the reauthor path: parse and construct via bob_tools.planfile, substitute changed phases with replace_phase_validated, keep lineage and ledger policy in duplo, persist only via bob_tools.planfile save, output must pass assert_mcloop_canonical. Reauthor preserves unchanged phases and validates lineage as before.
- [ ] T-000193: Verify Stage 21 gate: reauthor preserves unchanged phases, substitutes changed, lineage validated, lifecycle events emitted, save only via planfile, canonical helper passes; duplo ruff and pytest green; bob-tools still green.
