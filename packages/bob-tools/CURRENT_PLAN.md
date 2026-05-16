## Stage 5: Operations

Operations are pure functions on typed Plan objects. Per design doc
sections 3.2 and 5: mutation operations return a tuple of Settlements
so derived parent completion is explicit.

- [x] [BATCH] Define the Settlement descriptor and migrate operation
   - [x] In `model.py`, define `Settlement` dataclass (frozen) with fields: `kind: Literal["commit_landed", "test_failed", "work_observed", "none"]`, `task_id: str | None`, `phase_id: str | None`, `summary: str`, `failure_kind: str | None`, `ledger_event_required: bool`. Per design doc section 5 target contract.
   - [x] Settlement kind policy by source operation:
   - [x] Direct success with a commit-producing task settles as `commit_landed` with `ledger_event_required=True`.
   - [x] Direct success without a commit (AUTO action tasks and successfully verified USER tasks) settles as `work_observed` with `ledger_event_required=True`. This commits to `work_observed` per Codex's pile-1 confirmation.
   - [x] Direct terminal task failure settles as `test_failed` with `ledger_event_required=True`.
   - [x] Derived parent completion (all children done, parent auto-checked by `complete_task`) settles as kind `none` with `ledger_event_required=False`. Per design doc section 5.
   - [x] In `operations.py`, implement `migrate(plan: Plan) -> Plan`. Returns a new Plan with task_id assigned to every task that had none, and a phase-id comment added for every phase whose `phase_id_source` is "none". ID assignment rule: preserve every existing T-NNNNNN unchanged; scan the plan for the maximum existing numeric ID; assign missing IDs sequentially starting at max+1 (or T-000001 if no existing IDs). This handles partially migrated plans, plans with non-contiguous existing IDs, and plans with no IDs at all. Phase-id assignment uses the same rule on phase_NNN values. Idempotent: a plan that already has IDs and phase-ids is returned unchanged.
   - [x] Tests: Settlement construction; the four kind values; `migrate` assigns missing IDs on a fully unmigrated plan; `migrate(migrate(plan))` equals `migrate(plan)`; `migrate` does not change tasks or phases that already have identifiers; partially-migrated input (some tasks have T-000003 and T-000007, others have none) correctly assigns T-000008, T-000009, ... to the unmigrated tasks without touching T-000003 or T-000007; the same rule for non-contiguous phase IDs.

- [ ] resolve_task_context
   - [x] Implement `resolve_task_context(plan: Plan, task_label_or_id: str) -> TaskContext` where TaskContext is a dataclass with fields `task_id: str | None`, `phase_id: str | None`, `phase_id_source: str`, `label: str`, `plan_phase_count: int`.
   - [ ] Accepts either a stable task ID or a positional label such as "1.3.2" (as mcloop's `task_label` function produces today via `checklist.py`). Tokenizes properly — does not do substring search. Per design doc section 7.2 caveat.
   - [ ] When the task's containing phase has `phase_id_source` equal to "none", fill in the ordinal-derived id (the n-th phase in document order) and set source to "ordinal". Per design doc section 2.4 and 7.1.
   - [ ] Tests: lookup by ID; lookup by label; ordinal fallback when no explicit phase_id; raises a clear error for an unknown task.

- [ ] [BATCH] Implement next_tasks preserving mcloop's find_next semantics
   - [ ] Implement `next_tasks(plan: Plan, *, limit: int = 1) -> list[Task]` per design doc section 6.
   - [ ] Priority: tasks in the Bugs section first (absolute), then first-incomplete-phase scope.
   - [ ] Actionability: status is TODO; every dep listed in the task's @deps is DONE; no failed ancestor; if children, return first actionable child before parent. Per `_search_tasks` in mcloop's checklist.
   - [ ] Failed sibling blocking: in the depth-first walk, a failed subtask blocks all later siblings under the same parent. Root-level failed tasks are skipped, not blocking. Per `_search_tasks` exactly.
   - [ ] BATCH parent surfacing: when the next actionable leaf is a child of a BATCH parent, return the parent as a single Task with its actionable children attached (caller iterates). Match the `get_batch_children` semantics in mcloop's checklist: consecutive unchecked children until a USER child or AUTO child stops collection.
   - [ ] Tests: each priority rule exercised in isolation; failed-sibling blocking; leaf-before-parent; BATCH returns parent unit; later phases invisible until current phase done; bug priority; @deps blocking exercised with at least one test where a task is unblocked only after its dep is completed.

- [ ] [BATCH] Mutation operations returning tuples of Settlements
   - [ ] Implement `complete_task(plan, task_id, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]`. Flips status to DONE. The settlement for the direct task uses the kind policy above. If the parent (and grandparent, transitively) becomes complete because all children are now DONE, add a derived `kind="none"` Settlement for each newly-completed ancestor. Order in the returned tuple: direct settlement first, then derived ancestors from innermost outward.
   - [ ] Implement `fail_task(plan, task_id, reason: str, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]`. Flips status to FAILED. The settlement kind is `test_failed` with the supplied reason and the outcome's failure_kind (default "max_retries_exceeded" if outcome is None). Failing a task does not auto-complete ancestors; the tuple has exactly one Settlement.
   - [ ] Implement `reset_task(plan, task_id) -> tuple[Plan, tuple[Settlement, ...]]`. Flips FAILED back to TODO (matches mcloop's `clear_failed_markers`). Settlement kind is `none`, `ledger_event_required=False`. Per design doc section 5: reset is an operator decision to retry existing work, not evidence about implementation.
   - [ ] Implement `add_task(plan, phase_id, text, *, deps=(), parent_id=None) -> Plan`. Appends to the named phase. If `parent_id` is given, nests under it. The new task gets the next sequential globally-unique stable ID. Per design doc section 11 question 1 (global default).
   - [ ] Implement `replace_phase(plan, phase_id, new_phase) -> Plan`. Wholesale phase replacement, used by Duplo on phase reauthor.
   - [ ] All operations are pure: input Plan is not mutated; a new Plan is constructed.
   - [ ] Tests for each operation: status transitions, settlement kinds, derived parent completion produces multiple Settlements in the right order, ID assignment in `add_task`, replacement preserves other phases. Specifically test: completing the last unchecked child of a chain of two BATCH parents returns three Settlements (direct + two derived).

- [ ] validate_plan and check_consistency
   - [ ] Implement `validate_plan(plan) -> None` raising `PlanValidationError(messages)` on: unknown bracket tags anywhere, malformed annotations, duplicate task IDs, references in @deps to non-existent task IDs. Per design doc section 4.2 Notes (unknown bracket tags are rejected by validation).
   - [ ] Implement `check_consistency(plan, events) -> None` raising `PlanInconsistencyError(messages)` per design doc section 5: flag contradictions between checkbox state and the most recent lifecycle event for each task; do NOT flag intentional ledger gaps such as derived parent completion or settlements where ledger_event_required is false.
   - [ ] Tests for each violation category.

- [ ] Verify Stage 5 leaves the repo green.
