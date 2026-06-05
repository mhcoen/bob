<!-- bob-plan-format: 1 -->

## Bugs

- [ ] T-000001: In duplo's phase/roadmap generation, ensure that when a batch creates new executable modules, a covering test task that exercises those modules is emitted as a sibling task within the SAME batch (same parent), rather than deferring the covering test to a later phase. The batch must be self-contained so mcloop's coverage gate passes within it: created code and its exercising test are accepted together. [fix: "co-locate covering test task with module-creation tasks in the same batch"]
- [ ] T-000002: Add a regression test asserting that for a generated plan, every batch that creates new `.py` modules also contains (as a sibling task in the same batch/parent) a test task that targets those modules, so no module-creation batch is left with its covering test deferred to a later phase. [fix: "regression: module batch includes its covering test"]
