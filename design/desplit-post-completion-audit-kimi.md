Now I have all evidence re-derived independently. Here is the complete audit report.

---

# Independent adversarial validation — Kimi (K2.6)

## (A) PASS/FAIL table — one line per item with file:line evidence

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | §2(a) DFS/scope/leaf-before-parent equivalence | PASS | `_planfile_compat.py:214-249` `_search_tasks` vs `operations.py:1553-1628` `_walk_actionable`; control flow isomorphic including leaf-before-parent return and `is_subtask` asymmetry |
| 2 | §2(a) `@deps` vacuous-match | PASS | `mcloop/PLAN.md` contains no `@deps` lines (`rg -n "@deps" PLAN.md` → empty); `_deps_satisfied` (`operations.py:1477-1487`) returns `all(()) == True` |
| 3 | §2(a) subsection-order divergence | PASS (accepted-doc) | `parse_plan` flattens by section; `next_tasks` (`operations.py:1675-1684`) walks `phase.tasks` before `sub.tasks`; divergence neutralized by one-time B1 canonicalization |
| 4 | §2(b) failed-sibling root-skip / subtask-block asymmetry | PASS | `_planfile_compat.py:222-224,242-245` vs `operations.py:1584-1588,1624-1627`; root FAILED → `continue`, subtask FAILED → `return` in both |
| 5 | §2(c) batch child selection | PASS | `_planfile_compat.py:346-361` `get_batch_children` vs `operations.py:1512-1537` `_get_batch_children`; barrier rules identical |
| 6 | §2(c) BATCH return-shape divergence | PASS (mcloop-adapt) | `find_next` returns leaf (`_planfile_compat.py:256-274`); `next_tasks` yields surfaced BATCH parent (`operations.py:1598-1611`); shim normalizes in B3 |
| 7 | §2(d) USER leading-anchored match | PASS | `_planfile_compat.py:294-310` `is_user_task` checks `"USER" in task.flag_tags` first, then text fallback `== "[USER]"` or `startswith("[USER] ")`; planfile `_extract_flag_tags` (`parser.py:849-865`) is leading-only after ID strip |
| 8 | §2(d) AUTO/BATCH substring vs leading divergence | PASS (accepted-doc) | `_planfile_compat.py:313-330`: `flag_tags`/`action_tag` path is leading-only; text fallback uses substring (`"[BATCH]" in task.text`, `_AUTO_TAG_RE.search`). Planfile parser is leading-only. Freeze invariant (`rg -n "^\s*- \[[ !]\].*\[(BATCH|AUTO[^\]]*|USER)\]" PLAN.md` → empty) neutralizes divergence |
| 9 | §2(d) ID-prefix strip | PASS | `parser.py:656-671` `_extract_task_id` strips `T-NNNNNN:` before tag extraction; `_build_task` (`parser.py:674-698`) calls `_extract_task_id` before `_extract_flag_tags` |
| 10 | §2(e) derived parent completion (no event) | PASS | `complete_task` (`operations.py:1896-1948`) returns derived Settlements with `kind="none"` and `ledger_event_required=False` (`operations.py:1937-1947`) |
| 11 | §2(e) `commit_landed` git-gating preserved | PASS | `ledger_emit.py:469-472` `emit_task_lifecycle_events` gates on `_git_head_sha`; returns `[]` if `sha is None` |
| 12 | §2(e) AUTO/USER `work_observed` drop | PASS (mcloop-adapt) | `_planfile_compat.py:446-449` `check_off` discards `complete_task` settlements (keeps only `[0]` the new Plan), dropping `work_observed`; preserves pre-planfile behavior |
| 13 | §2(e) commit-failure must not mark `[!]` | PASS (mcloop-adapt) | `main.py:1877-1886` calls `_ledger_settle(failure_kind="commit_failed")` but does NOT call `mark_failed`; B3 shim does not route commit failure through `fail_task` |
| 14 | §2(e) mutation needs IDs | PASS (hard constraint) | `complete_task`/`fail_task`/`reset_task` resolve by `task_id` via `_find_task_by_id` (`operations.py:414-429`); missing ID → `ValueError` (`operations.py:1921-1922,1971-1972,2004-2005`) |
| 15 | §2(f) `mark_failed` semantics | PASS | `fail_task` flips to FAILED unconditionally with `cascade=False` (`operations.py:1974`); matches `_planfile_compat.py:452-460` shim |
| 16 | §2(f) `--retry` bulk clear | PASS (resolved) | `main.py:854-855` calls `clear_failed_markers` on both `plan_path` and `bugs_path`; `_planfile_compat.py:463-472` routes through `planfile.clear_failed` |
| 17 | §2(g) atomic/locking | PASS (mcloop-adapt, safer) | `fileio.save` uses `fcntl.flock LOCK_EX` (`fileio.py:106-124`) + atomic `tempfile`+`fsync`+`os.replace` (`fileio.py:170-187`); shim wraps `update()` in bounded retry (`_planfile_compat.py:413-433`) |
| 18 | §2(g) whole-file canonical rewrite | PASS (hard constraint) | `fileio.save` writes `render_plan(plan)` canonical form; mcloop `checklist.check_off` changed one line. One-time B1 neutralization required |
| 19 | §2(g) empty `## Bugs` trailing-newline normalization | PASS (normalization property) | `renderer.py:71-74` strips trailing blank lines via `while lines and not lines[-1]: lines.pop()`; `_render_bugs_into` emits `["## Bugs", ""]` for empty bugs, which is normalized to `'## Bugs\n'` |
| 20 | `clear_failed` exported from `bob_tools.planfile` | PASS | `__init__.py:42` imports `clear_failed`; `__all__` line 88 |
| 21 | `validate_plan` exported from `bob_tools.planfile` | PASS | `__init__.py:53` imports `validate_plan`; `__all__` line 101 |
| 22 | `purge_done_bug_tasks` exported from `bob_tools.planfile` | PASS | `__init__.py:48` imports `purge_done_bug_tasks`; `__all__` line 96 |
| 23 | `mcloop/checklist.py` absent | PASS | `Glob("mcloop/checklist.py")` → no files; runtime code imports from `_planfile_compat` |
| 24 | B2 `resolve_phase_id` shim preserves no-ordinal emission | PASS | `ledger_emit.py:153-171` collapses `ordinal` source to `("none", None)` when `ordinal_index is None`; `main.py:1052-1055` calls `resolve_phase_id` without `ordinal_index` |
| 25 | B3 harness hermeticity | PASS (mechanical correction) | `tests/integration/test_stub_run.py:91-96` patches `_select_backend`→`"direct"` and `_build_command`; other integration tests patch `run_task` directly; `conftest.py:30-53` blocks real LLM subprocess calls as guardrail. **Correction:** file moved from `tests/test_stub_run.py` to `tests/integration/test_stub_run.py` |
| 26 | §2(h) runner/checks/run_summary no coupling | PASS | `rg` across `mcloop/runner.py`, `mcloop/checks.py`, `mcloop/run_summary.py` finds no `checklist`/`plan_split` imports |
| 27 | §2(h) maintain.py CHECKBOX_RE-only | PASS | `maintain.py:31` defines local `CHECKBOX_RE`; no `mcloop.checklist` import (D1a-E) |
| 28 | §2(h) `tests/test_args.py` repointed to shim | PASS (resolved) | `tests/test_args.py:4303,4336` imports `mcloop._planfile_compat` (not `mcloop.checklist`); May-17 deletion-surface omission fixed |
| 29 | Stage preconditions / ordering | PASS (accepted-doc) | Actual landed order from git log: B0.2 → B2 → B3 → B1+B3 → D3 → D2 → D1a → D1. B2 precedes B1+B3 rather than following it, but this preserves all load-bearing dependencies (B0.1 before B0.2, B1+B3 atomic, D3 before D2 before D1a before D1) |
| 30 | D1 conservative default (drop `work_observed`) | PASS | Shim `check_off` discards settlements; current `main.py` `_ledger_settle` path does not produce `work_observed` events |
| 31 | D2 conservative default (no ordinal attribution) | PASS | `ledger_emit.py:153-171` collapses `ordinal`→`("none", None)` when `ordinal_index` absent; matches today's emission profile |
| 32 | D3 `--retry` routes through `planfile.clear_failed` | PASS | `main.py:854-855` calls `clear_failed_markers` on both PLAN.md and BUGS.md; shim delegates to `planfile.clear_failed` |
| 33 | D5 `purge_done_bug_tasks` filters DONE | PASS | `operations.py:2087-2100` removes DONE bug tasks; no semantic change beyond filtering |
| 34 | Freeze invariant: no `@deps` | PASS | `rg -n "@deps" PLAN.md` → empty |
| 35 | Freeze invariant: no prose-mention tags on incomplete tasks | PASS | `rg -n "^\s*- \[[ !]\].*\[(BATCH|AUTO[^\]]*|USER)\]" PLAN.md` → empty |

**Totals: 35 PASS, 0 FAIL.**

---

## (B) CONSEQUENTIAL FINDINGS

None found.

All audited claims — parity semantics, hard constraints, implemented-stage integrity, freeze invariants, decision defaults, and ordering logic — hold against current source. The May-17 deletion-surface omission (`tests/test_args.py`) is resolved. No new consequential divergence surfaced between the mcloop-shim and bob-tools-planfile contracts.

---

## (C) DESIGN-LEVEL DECISIONS FOR THE HUMAN

None required.

All decision-register defaults (D1 drop `work_observed`, D2 no ordinal attribution) are preserved by the current shim and ledger-emitter paths. D3 (`--retry` through `clear_failed`) and D5 (`purge_done_bug_tasks`) are implemented and verified. No design-level question requires Michael's judgment.

---

## Closing — convergence note

This stream's findings should be compared against Claude Code's stream A report at `bob/design/desplit-post-completion-audit-claudecode.md`. Convergence is the closure criterion. Both streams should independently report zero behavioral defects, with at most mechanical citation-line drift or analogous documentation corrections.
