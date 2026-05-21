## Stage 15: Phase C Increment 6 - add_phase_task
<!-- phase_id: phase_015 -->

- [x] T-000179: Implement add_phase_task(plan, phase_id, task, *, parent_id=None, subsection_title=None) -> tuple[Plan, str] per v4 Contract 6: append a Stage-10 field-stable task into an existing phase at root, under parent_id, or under a named subsection; share make_task validation and global-sequential id assignment; return new plan and the assigned T-NNNNNN; result must pass validate_plan constructed=True. PlanValidationError on unknown phase_id, parent_id, or subsection, invalid task, dup id, unknown deps.
- [ ] T-000180: Verify Stage 15 gate: root, parent, and subsection append; id assignment; plan-and-id return; validation; ruff, ruff format, mypy strict, full pytest all green.
