## Stage 12: Phase C Increment 3 - assert_mcloop_canonical with semantic round-trip
<!-- phase_id: phase_012 -->

- [x] T-000173: Implement assert_mcloop_canonical(plan, *, source_path=None) per v4 Contract 5: run validate_plan constructed=True, render, parse, require SEMANTIC equality of parsed-vs-intended after normalizing only line_number, indent, source_path, trailing_lines (not byte fixed point), then enforce the R1/R2 equivalent without importing mcloop. Return the validated rendered text so the caller persists exactly what was checked. Raise PlanValidationError; let PlanSyntaxError from the re-parse propagate.
- [ ] T-000174: Verify Stage 12 gate: a plan that byte-fixed-points but is semantically different (the v3 leak class) is rejected; valid plan returns text; missing ids and R1-shape fixtures rejected; ruff, ruff format, mypy strict, full pytest all green.
