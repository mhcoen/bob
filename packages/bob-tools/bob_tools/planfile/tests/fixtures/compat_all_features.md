# Compat Plan

Exercises every feature reachable without the strict-mode magic line:
USER, BATCH, and AUTO action tags; both annotation keys; nested
children; RULEDOUT siblings; @deps siblings; subsections; Stage and
Phase keyword headings; and a Bugs section.

## Stage 1: Core

Phase prose stays attached even with multiple paragraphs.

The second paragraph survives canonicalization.

- [ ] [BATCH] parent task
  - [ ] T-000002: child a [feat: "menu wired"]
    [RULEDOUT] tried polling instead
    [RULEDOUT] tried websocket subscription
  - [x] T-000003: child b done
    @deps T-000002
- [ ] T-000004: [USER] verify the menu manually
- [!] T-000005: [AUTO:run_cli] mcloop --dry-run [fix: "race condition"]

### Manual verification

A subsection with its own prose paragraph.

- [ ] check the smoke screen
- [ ] check the rollback path

## Phase 2: Polish

- [ ] [USER] [BATCH] verify the polished build
  - [ ] check colors
  - [ ] check spacing

## Bugs

- [ ] T-000099: crash on empty PLAN.md
- [x] T-000100: fixed memory leak in parser
