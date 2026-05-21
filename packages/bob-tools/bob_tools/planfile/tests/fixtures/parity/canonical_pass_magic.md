<!-- bob-plan-format: 1 -->

# Canonical-pass parity fixture

Strict mode, every task carries a T-NNNNNN id, no orphan checkboxes.
Both predicates must ACCEPT.

## Stage 1: Foundations
<!-- phase_id: phase_001 -->

- [ ] T-000001: first task
- [x] T-000002: completed task

## Stage 2: Polish
<!-- phase_id: phase_002 -->

- [ ] T-000003: second-phase task
  - [ ] T-000004: nested child
- [!] T-000005: previously failed task

## Bugs

- [ ] T-000099: a tracked bug
