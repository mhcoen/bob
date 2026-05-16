<!-- bob-plan-format: 1 -->

# Strict-mode Plan

Exercises the magic line, mandatory stable task IDs, and the
canonical comment-form phase id. Every task carries a T-NNNNNN
prefix so strict-mode parsing accepts the document.

## Stage 1: Bootstrap
<!-- phase_id: phase_001 -->

The strict plan still allows phase prose between the heading-id
comment and the first task.

- [ ] T-000001: [BATCH] parent
  - [ ] T-000002: child a
    [RULEDOUT] earlier attempt
  - [ ] T-000003: child b
    @deps T-000002
- [x] T-000004: [USER] verified manually
- [!] T-000005: [AUTO:run_cli] mcloop --dry-run [feat: "wired"]

## Bugs

- [ ] T-000099: spurious crash on empty input
