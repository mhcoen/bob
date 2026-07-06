<!-- bob-plan-format: 1 -->

# Fenced-example parity fixture

A completed task carries a fenced output block containing a checkbox
line and a heading. The parser treats fenced content as verbatim, so
neither predicate may count the fenced checkbox as a dropped task.
Both predicates must ACCEPT (the fence-unaware count in mcloop's
precondition used to REJECT this file while bob-plan fmt blessed it,
wedging mcloop startup with a no-op remediation).

## Stage 1: Foundations
<!-- phase_id: phase_001 -->

- [x] T-000001: document the checkbox syntax
  Example of the syntax being documented:

  ```text
  - [ ] example checkbox inside fence
  ## Bugs
  ```

- [ ] T-000002: real next task
