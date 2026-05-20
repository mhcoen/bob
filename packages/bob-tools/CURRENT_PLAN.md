## Stage 13: Phase C Increment 4 - add_bug_task
<!-- phase_id: phase_013 -->

- [x] T-000175: Implement add_bug_task(plan, task, *, dedup_keys=()) -> tuple[Plan, str] per v4 Contract 2: returns one of appended, reopened, unchanged; creates the Bugs section if absent; root bug tasks only; force TODO and assign next global T-NNNNNN on append; dedup keys = explicit keys then fix annotation values then normalized text; TODO match returns unchanged; DONE or FAILED match reopens earliest in place preserving id, children, annotations, deps, ruled_out, position; task must pass Stage 10 field-stability. PlanValidationError only.
- [!] T-000176: Verify Stage 13 gate: absent section, append, unchanged-TODO, reopen-DONE, reopen-FAILED, fix-key dedup, text-key dedup, id assignment, children preserved, field-stability rejection; ruff, ruff format, mypy strict, full pytest all green.
