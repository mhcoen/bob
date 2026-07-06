<!-- bob-plan-format: 1 -->

# duplo Bug Backlog

ACCEPTED TRADEOFF (2026-07-06, on record after three verification-audit rounds; deliberate decision, not a work item): a task whose PROSE merely mentions a feat annotation (e.g. "Remove the stale [feat: exporter] annotation from docs") registers that feature as built in plan_sanity, so a genuine verify-without-build for the same feature passes the gate unflagged, and the same widening lets a prose mention satisfy scope coverage. This bias is chosen because the two failure directions are asymmetric: too-lenient leaves an unfulfillable verify task that fails LOUDLY in mcloop later (recoverable), while the stricter trailing-only parse it replaced silently DELETED legitimate verification work whenever a builder annotation carried a stray suffix like "?" or "(critical)" (commit 1e42746d; bias documented at plan_sanity.py's feat-collection block). Revisit only if prose mentions mask a real gap in practice.

## Bugs
