# R2-idless parity fixture

A phase-bearing plan whose tasks lack stable T-NNNNNN ids. R1 ALLOWs
(every source checkbox surfaces as a parsed task — the parser does
not run in strict mode without the magic line), but R2 REJECTs
because the canonical contract requires every task to carry a
stable id.

Both predicates must REJECT.

## Stage 1: Setup

- [ ] only task without an id
- [x] another id-less task

## Stage 2: More

- [ ] yet another id-less task
