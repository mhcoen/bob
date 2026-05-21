## Stage 17: Phase C Increment 8 - mcloop and bob-tools R1/R2 cross-repo parity
<!-- phase_id: phase_017 -->

mcloop delegation is explicitly out of scope for Phase C and recorded as a tracked follow-on; the mandatory parity test below is the Path 1 mitigation for R1/R2 drift.

- [ ] T-000184: Add a required cross-repo parity test: a shared fixture corpus run through bob_tools.planfile assert_mcloop_canonical and through mcloop's real mcloop._planfile_precondition.enforce_canonical, asserting identical accept or reject on every fixture. The test must fail loudly if the two predicates disagree. Place the corpus and test in bob-tools; the test imports mcloop's precondition module by path.
- [ ] T-000185: Verify Stage 17 gate: parity test green across the corpus including canonical-pass, R1-drop, and R2-idless fixtures; ruff, ruff format, mypy strict, full pytest all green.
