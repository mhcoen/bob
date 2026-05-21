# Independent adversarial validation — Kimi (K2.6)

## (A) PASS/FAIL table — one line per item with file:line evidence

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | 2(a) DFS/scope/leaf-before-parent equivalence | PASS | `mcloop/checklist.py:362-411` `_search_tasks` vs `bob_tools/planfile/operations.py:591-666` `_walk_actionable`; control flow isomorphic including leaf-before-parent return and `is_subtask` asymmetry |
| 2 | 2(a) `@deps` vacuous-match | PASS | `mcloop/PLAN.md` contains no `@deps` lines (`rg -n "@deps" PLAN.md` → empty); `_deps_satisfied` (`operations.py:515-525`) returns `all(()) == True` |
| 3 | 2(a) subsection-order divergence | PASS (accepted-doc) | `checklist.parse` flattens linearly; `next_tasks` (`operations.py:713-719`) walks `phase.tasks` before `sub.tasks`; divergence neutralized by one-time B1 canonicalization |
| 4 | 2(b) failed-sibling root-skip / subtask-block asymmetry | PASS | `checklist.py:375-382,402-407` vs `operations.py:622-626,657-665`; root FAILED → `continue`, subtask FAILED → `return` in both |
| 5 | 2(c) batch child selection | PASS | `checklist.py:695-719` `get_batch_children` vs `operations.py:550-575` `_get_batch_children`; barrier rules identical |
| 6 | 2(c) BATCH return-shape divergence | PASS (mcloop-adapt) | `checklist.find_next` returns leaf; `next_tasks` yields surfaced BATCH parent (`operations.py:636-649`); shim normalizes in B3 |
| 7 | 2(d) USER leading-anchored match | PASS | `checklist.is_user_task` (`checklist.py:633-642`) requires `text == "[USER]"` or `startswith("[USER] ")`; planfile `_extract_flag_tags` (`parser.py:849-865`) is leading-only after ID strip |
| 8 | 2(d) AUTO/BATCH substring vs leading divergence | PASS (accepted-doc) | `checklist.is_auto_task` uses `_AUTO_TAG_RE.search` (substring anywhere, `checklist.py:722-729`); `is_batch_task` uses `"[BATCH]" in task.text` (`checklist.py:686-692`); planfile uses leading-only. Three prose-mention `[BATCH]` tasks exist at `PLAN.md:341,359,439`; all are `[x]`. Freeze invariant grep returns no matches |
| 9 | 2(d) IDs break `checklist.is_user_task` | PASS (hard constraint) | `checklist.is_user_task` checks raw `task.text`; after `migrate()` prepends `T-000NNN:`, text `T-000001: [USER] …` fails both `== "[USER]"` and `startswith("[USER] ")` (`checklist.py:633-642`). `_collect_body` gate (`checklist.py:210-228`) also breaks. planfile strips ID first (`parser.py:668-671,685-686`) |
| 10 | 2(e) derived parent completion (no event) | PASS | `checklist._auto_check_parents` (`checklist.py:771-797`) silently checks parents; planfile `complete_task` returns derived Settlements with `ledger_event_required=False` (`operations.py:975-985`) |
| 11 | 2(e) `commit_landed` git-gating preserved | PASS | `ledger_emit.emit_task_lifecycle_events` (`ledger_emit.py:469-472`) still gates on `_git_head_sha`; planfile only emits descriptors, wire event built by caller |
| 12 | 2(e) AUTO/USER now settle | PASS (mcloop-adapt) | planfile `_direct_completion_kind` (`operations.py:904-918`) returns `"work_observed"` for AUTO/USER; current `main.py` emits NO ledger event for AUTO/USER (`main.py:1201-1224,1226-1251`). B3 shim must drop `work_observed` to preserve behavior |
| 13 | 2(e) commit-failure must not mark `[!]` | PASS (mcloop-adapt) | `main.py:1660-1673` calls `_ledger_settle(failure_kind="commit_failed")` but NOT `mark_failed`; B3 shim must not route commit failure through `fail_task` |
| 14 | 2(e) mutation needs IDs | PASS (hard constraint) | `complete_task`/`fail_task`/`reset_task` resolve by `task_id` via `_find_task_by_id` (`operations.py:107-122`); missing ID → `ValueError` (`operations.py:959-960,1008-1010,1041-1043`) |
| 15 | 2(f) `mark_failed` semantics | PASS | `fail_task` flips to FAILED unconditionally, `cascade=False` (`operations.py:1012`); matches `checklist.mark_failed` (`checklist.py:565-596`) |
| 16 | 2(f) `--retry` bulk clear | PASS (resolved) | `clear_failed` exported (`bob_tools/planfile/__init__.py:39,74`) and implemented (`operations.py:1058-1097`); B0.1 complete |
| 17 | 2(g) atomic/locking | PASS (mcloop-adapt, safer) | `fileio.save` uses `fcntl.flock LOCK_EX` + atomic `tempfile`+`fsync`+`os.replace` (`fileio.py:89-135`); shim wraps `update()` in bounded retry (`_planfile_compat.py:366-381`) |
| 18 | 2(g) whole-file canonical rewrite | PASS (hard constraint) | `fileio.save` writes `render_plan(plan)` canonical form; mcloop `checklist.check_off` changes one line. One-time B1 neutralization required |
| 19 | clear_failed exported from `bob_tools.planfile` | PASS | `__init__.py:39` imports `clear_failed`; `__all__` line 74 |
| 20 | validate_plan exported from `bob_tools.planfile` | PASS | `__init__.py:47` imports `validate_plan`; `__all__` line 87 |
| 21 | `_planfile_compat` unimported by runtime | PASS | `rg -n "_planfile_compat" mcloop --glob '!_planfile_compat.py'` → empty; `tests/test_planfile_compat.py:261-265` enforces additive-only import proof |
| 22 | B2 `resolve_phase_id` shim preserves no-ordinal emission | PASS | `ledger_emit.py:153-171` collapses `ordinal` source to `("none", None)` when `ordinal_index is None`; `main.py:903-906` calls `resolve_phase_id` without `ordinal_index` |
| 23 | B3 harness hermeticity | PASS | `test_stub_run.py:92-94` patches `_select_backend`→`"direct"` and `_build_command`; other integration tests patch `run_task` directly; `conftest.py:30-53` blocks real LLM subprocess calls as guardrail |
| 24 | §2(h) runner/checks/run_summary no coupling | PASS | `rg` across `mcloop/runner.py`, `mcloop/checks.py`, `mcloop/run_summary.py` finds no `checklist`/`plan_split` imports |
| 25 | §2(h) maintain.py CHECKBOX_RE-only | PASS | `maintain.py:13` imports only `CHECKBOX_RE`; no other checklist symbols |
| 26 | §2(h) deletion surface completeness | **FAIL** | `tests/test_args.py` imports `mcloop.checklist.Task` (`:13`) and `parse` (`:3958,4309,4340,5980,9893,9942,9988`) but is **omitted** from the design doc's §2(h) table |
| 27 | Stage preconditions / ordering | PASS | B0.2 shim depends on B0.1 `clear_failed` export; B1+B3 atomic due to hard constraints (IDs break checklist, mutation needs IDs); B5 deferred after B3; B6 quarantined after B5 |
| 28 | D1 conservative default (drop `work_observed`) | PASS | Current `main.py` emits NO ledger event for AUTO/USER success; B3 shim design preserves this |
| 29 | D2 conservative default (no ordinal attribution) | PASS | `ledger_emit.py:153-171` collapses `ordinal`→`("none", None)` when `ordinal_index` absent; matches today's emission profile |
| 30 | Freeze invariant: no `@deps` | PASS | `rg -n "@deps" PLAN.md` → empty |
| 31 | Freeze invariant: no prose-mention tags on incomplete tasks | PASS | `rg -n "^[[:space:]]*- \[[ !]\].*\[(BATCH|AUTO[^\]]*|USER)\]" PLAN.md` → empty |
| 32 | `mcloop/PLAN.md` 3-space indent | PASS | `rg -n "^   - \[" PLAN.md` matches subtasks; `rg -n "^  - \[" PLAN.md` → empty |

**Totals: 31 PASS, 1 FAIL.**

---

## (B) CONSEQUENTIAL FINDINGS

### 1. Deletion-surface undercount: `tests/test_args.py` omitted from §2(h) table

**Finding:** The design doc's §2(h) importer inventory lists `test_plan_split.py`, `test_checklist.py`, `test_checklist_integration.py`, `test_subtask_ordering.py`, `test_output.py`, and `test_lifecycle.py`, but omits `tests/test_args.py`. That file imports `mcloop.checklist.Task` at `tests/test_args.py:13` and `parse` at `:3958,4309,4340,5980,9893,9942,9988`.

**Correction:** Add `tests/test_args.py` to the deletion-surface table with disposition **Migration site** (Stage B5/B3). The `Task` import is used for type signatures and mock construction; the `parse` imports drive argument-parsing test fixtures that construct task trees. All must be repointed to `_planfile_compat` or planfile types before `checklist` can be deleted.

### 2. None found beyond (1)

All other audited claims — parity semantics, hard constraints, implemented-stage integrity, freeze invariants, decision defaults, and ordering logic — hold against current source.

---

## (C) DESIGN-LEVEL DECISIONS FOR THE HUMAN

### D1 — AUTO/USER `work_observed` emission

**Decision:** Preserve current behavior (drop `work_observed` at the B3 settle hook). **Tradeoff:** Behavior-preserving cutover is the acceptance bar. Adopting `work_observed` now would change the ledger audit stream and could trigger `ledger_pause` threshold evaluation for tasks that today emit nothing. Defer to opt-in B6 with an explicitly updated baseline.

### D2 — Ordinal phase-id attribution

**Decision:** Preserve current behavior (collapse `ordinal`→`("none", None)` when `ordinal_index` is absent). **Tradeoff:** The ledger today emits `source="none"` for pre-migration plans. Enabling ordinal attribution would introduce `finding_observed` degraded events and change threshold exposure. Defer to opt-in B6.

### D4 — B1+B3 cutover go/no-go

**Decision:** GO for the B1+B3 cutover preconditions covered by this pre-flight, with the caveat that `tests/test_args.py` must be added to the B3 migration scope. **Tradeoff:** The B1 migration artifact is internally consistent, shim API-boundary parity checks pass, the recorded-replay harness is hermetic, and the two hard constraints (IDs break checklist; mutation needs IDs) make the atomic cutover unavoidable. The only unaddressed surface is the omitted `test_args.py` checklist imports, which is low-risk (type/mock fixtures) but must be included in the B3 checklist-to-shim repointing.

---

## (D) Overall GO/NO-GO for the B1+B3 cutover review

**GO**, contingent on adding `tests/test_args.py` to the B3 migration scope.

---

## Audit methodology confirmation

- **Source access:** YES (confirmed at start).
- **Git operations performed:** NONE.
- **Files written outside designated path:** NONE.
- **Only file written:** `/Users/mhcoen/proj/bob/design/desplit-independent-validation-kimi.md` (this file).
