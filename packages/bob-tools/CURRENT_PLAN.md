## Stage 19: Phase C Increment 10 - migrate duplo bug and fix append
<!-- phase_id: phase_019 -->

- [ ] T-000188: In duplo, replace saver.append_to_bugs_section and the pipeline fix-mode fallback and investigator.investigation_to_fix_tasks with make_task plus add_bug_task. No raw PLAN.md or BUGS.md markdown write of bug content may remain. Tag escaping helpers become unnecessary because tags are typed fields; remove them on this path.
- [ ] T-000189: Verify Stage 19 gate: duplo fix and investigate paths cover append, skip duplicate, reopen DONE and FAILED; no raw plan-markdown write in saver; duplo ruff and pytest green; bob-tools still green.
